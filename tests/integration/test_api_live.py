"""Live smoke tests against the deployed Container Apps staging endpoint.

Run via:
    STAGING_URL=https://<fqdn> pytest tests/integration/test_api_live.py -v

Called by the CD pipeline smoke-test job after deploy-staging completes.
"""

import os

import httpx
import pytest

BASE_URL = os.environ.get("STAGING_URL", "").rstrip("/")
VERIFY_TLS = os.environ.get("LIVE_SMOKE_VERIFY_TLS", "true").strip().lower() not in {
    "0",
    "false",
    "no",
}
TRUST_ENV = os.environ.get("LIVE_SMOKE_TRUST_ENV", "true").strip().lower() not in {
    "0",
    "false",
    "no",
}

_VALID_PAYLOAD = {
    "transaction_id": "live-smoke-001",
    "card1": 13926,
    "TransactionAmt": 49.99,
    "ProductCD": "W",
}


def _skip_if_no_url() -> None:
    if not BASE_URL:
        pytest.skip("STAGING_URL not set — skipping live tests")


@pytest.fixture(scope="module")
def client() -> httpx.Client:
    _skip_if_no_url()
    with httpx.Client(
        base_url=BASE_URL,
        timeout=30.0,
        verify=VERIFY_TLS,
        trust_env=TRUST_ENV,
    ) as c:
        yield c


def test_health(client: httpx.Client) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json().get("status") == "ok"


def test_ready(client: httpx.Client) -> None:
    r = client.get("/ready")
    assert r.status_code == 200
    body = r.json()
    assert body.get("model_ready") is True


def test_score_returns_valid_decision(client: httpx.Client) -> None:
    r = client.post("/score", json=_VALID_PAYLOAD)
    assert r.status_code == 200
    body = r.json()
    assert body["transaction_id"] == "live-smoke-001"
    assert body["decision"] in {"APPROVE", "REVIEW", "DECLINE"}
    assert 0.0 <= body["fraud_score"] <= 1.0
    assert body["threshold_review"] == pytest.approx(0.50, abs=1e-6)
    assert body["threshold_decline"] == pytest.approx(0.90, abs=1e-6)
    assert "latency_ms" in body
    assert body["latency_ms"] > 0


def test_score_missing_required_field(client: httpx.Client) -> None:
    bad = {k: v for k, v in _VALID_PAYLOAD.items() if k != "TransactionAmt"}
    r = client.post("/score", json=bad)
    assert r.status_code == 422


def test_score_extra_fields_accepted(client: httpx.Client) -> None:
    payload = {**_VALID_PAYLOAD, "transaction_id": "live-smoke-extra", "C1": 1, "D1": 0}
    r = client.post("/score", json=payload)
    assert r.status_code == 200


def test_metrics_endpoint(client: httpx.Client) -> None:
    r = client.get("/metrics")
    assert r.status_code == 200
    body = r.json()
    assert "model_ready" in body
