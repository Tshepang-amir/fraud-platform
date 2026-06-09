"""Integration smoke tests: /health, /ready, /score with a stubbed app.

Uses FastAPI's TestClient (in-process ASGI runner — no external server needed).
Model, feature store, and DB are mocked so these tests run without MLflow/Feast/Postgres.

Lifespan bypass: FastAPI stores the lifespan as app.router.lifespan_context.
Patching the module-level name has no effect because the app already holds a
reference to the original function.  We override the router attribute directly
so TestClient uses a no-op lifespan and we control app.state via fixture.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Generator
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_PROMETHEUS_DIR = Path("data") / "test_tmp" / "prometheus"
_PROMETHEUS_DIR.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("PROMETHEUS_MULTIPROC_DIR", str(_PROMETHEUS_DIR.resolve()))
os.environ.setdefault("prometheus_multiproc_dir", str(_PROMETHEUS_DIR.resolve()))

from src.serve.main import app

# ── Null lifespan — replaces the real startup during tests ────────────────────


@asynccontextmanager
async def _null_lifespan(application: FastAPI) -> AsyncIterator[None]:
    yield


# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture()
def _stub_services() -> None:
    """Inject stub model, feature, and decision-log services into app.state."""
    mock_model = MagicMock()
    mock_model.ready = True
    mock_model.champion_run_id = "test-champion-run-id"
    mock_model.challenger_run_id = "test-challenger-run-id"
    mock_model.score_champion.return_value = 0.15
    mock_model.score_challenger.return_value = 0.18

    mock_features = MagicMock()
    mock_features.get_features.return_value = {
        "fe_card_txn_count_1h": 2.0,
        "fe_card_txn_count_24h": 5.0,
        "fe_card_txn_count_7d": 20.0,
        "fe_card_amt_mean_24h": 120.0,
        "fe_card_amt_std_24h": 30.0,
        "fe_card_amt_zscore_24h": -0.1,
        "fe_time_since_last_txn": 3600.0,
        "fe_card_entropy_product_7d": 2.0,
        "fe_peer_amt_deviation": 0.05,
    }

    mock_decision = MagicMock()
    mock_decision.health_check.return_value = {"ok": True}

    app.state.model_service = mock_model
    app.state.feature_service = mock_features
    app.state.decision_log = mock_decision


@pytest.fixture()
def client(_stub_services: None) -> Generator[TestClient, None, None]:
    """TestClient with a no-op lifespan so real startup is bypassed."""
    original_lifespan = app.router.lifespan_context
    app.router.lifespan_context = _null_lifespan
    try:
        with TestClient(app, raise_server_exceptions=True) as c:
            yield c
    finally:
        app.router.lifespan_context = original_lifespan


# ── Tests ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
class TestAPISmokeTests:
    def test_health_endpoint(self, client: TestClient) -> None:
        """GET /health returns 200 with status ok."""
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_ready_endpoint(self, client: TestClient) -> None:
        """GET /ready returns 200 when model is loaded."""
        resp = client.get("/ready")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ready"

    def test_score_endpoint_approve(self, client: TestClient) -> None:
        """POST /score returns APPROVE for a low-risk transaction."""
        payload = {
            "transaction_id": "txn-smoke-001",
            "card1": 12345,
            "TransactionAmt": 99.50,
            "ProductCD": "W",
        }
        resp = client.post("/score", json=payload)
        assert resp.status_code == 200
        body = resp.json()
        assert body["transaction_id"] == "txn-smoke-001"
        assert body["decision"] == "APPROVE"
        assert 0.0 <= body["fraud_score"] <= 1.0
        assert "request_id" in body
        assert body["latency_ms"] >= 0

    def test_score_endpoint_decline(self, client: TestClient) -> None:
        """POST /score returns DECLINE when champion returns score >= 0.90."""
        app.state.model_service.score_champion.return_value = 0.95
        payload = {
            "transaction_id": "txn-smoke-002",
            "card1": 99999,
            "TransactionAmt": 9999.00,
        }
        resp = client.post("/score", json=payload)
        assert resp.status_code == 200
        assert resp.json()["decision"] == "DECLINE"

    def test_score_missing_required_field(self, client: TestClient) -> None:
        """POST /score with missing TransactionAmt returns 422."""
        resp = client.post("/score", json={"transaction_id": "x", "card1": 1})
        assert resp.status_code == 422

    def test_metrics_endpoint(self, client: TestClient) -> None:
        """GET /metrics returns model readiness and DB health."""
        resp = client.get("/metrics")
        assert resp.status_code == 200
        body = resp.json()
        assert body["model_ready"] is True
        assert body["db_health"]["ok"] is True
