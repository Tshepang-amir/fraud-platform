"""FastAPI scoring service — main application with lifespan.

Endpoints:
  POST /score   → champion fraud decision (sync) + challenger shadow (background)
  GET  /health  → liveness probe
  GET  /ready   → readiness probe (503 until model is loaded)
  GET  /metrics → lightweight ops metrics

Environment variables (all required in production; defaults for local dev):
  MLFLOW_CHAMPION_RUN_ID         — run ID of the LightGBM champion
  MLFLOW_CHALLENGER_RUN_ID       — run ID of the CatBoost challenger (optional)
  MLFLOW_TRACKING_URI            — mlruns/ locally; Databricks URI in production
  FEAST_REPO_PATH                — path to feature_repo/ directory
  FEAST_POSTGRES_PASSWORD        — Feast online store password
  DECISION_LOG_DSN               — psycopg2 DSN for decision logging
  FRAUD_THRESHOLD_REVIEW         — score >= this → REVIEW  (default 0.50)
  FRAUD_THRESHOLD_DECLINE        — score >= this → DECLINE (default 0.90)
  OTEL_EXPORTER_OTLP_ENDPOINT   — OTLP endpoint; omit to disable remote tracing
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import mlflow
from fastapi import FastAPI

from src.serve.middleware.telemetry import configure_telemetry, instrument_app
from src.serve.routers.health import router as health_router
from src.serve.routers.score import router as score_router
from src.serve.services.decision_log import DecisionLogService
from src.serve.services.feature_service import FeatureService
from src.serve.services.model_service import ModelService

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── Defaults for local development ─────────────────────────────────────────────
_CHAMPION_RUN_ID: str = os.getenv("MLFLOW_CHAMPION_RUN_ID", "9c599d91d7c546df82ad252837990c29")
_CHALLENGER_RUN_ID: str = os.getenv("MLFLOW_CHALLENGER_RUN_ID", "cd2da7878fd44ad39dab091dde2984fb")
_FEAST_REPO_PATH: str = os.getenv("FEAST_REPO_PATH", "feature_repo")
_DECISION_LOG_DSN: str = os.getenv(
    "DECISION_LOG_DSN",
    "postgresql://postgres:local_dev_only@localhost:5433/fraud_platform",
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Load models and open DB connections at startup; clean up at shutdown."""
    logger.info("=== Fraud Scorer — startup ===")

    # ── Telemetry ──────────────────────────────────────────────────────────────
    configure_telemetry()

    # ── MLflow tracking URI ────────────────────────────────────────────────────
    tracking_uri = os.getenv("MLFLOW_TRACKING_URI", "mlruns")
    mlflow.set_tracking_uri(tracking_uri)
    logger.info("MLflow tracking URI: %s", tracking_uri)

    # ── Model service ──────────────────────────────────────────────────────────
    model_svc = ModelService()
    model_svc.load(
        champion_run_id=_CHAMPION_RUN_ID,
        challenger_run_id=_CHALLENGER_RUN_ID,
    )
    app.state.model_service = model_svc

    # ── Feature service ────────────────────────────────────────────────────────
    app.state.feature_service = FeatureService(repo_path=_FEAST_REPO_PATH)

    # ── Decision log ───────────────────────────────────────────────────────────
    app.state.decision_log = DecisionLogService(dsn=_DECISION_LOG_DSN)

    logger.info("=== Fraud Scorer — ready ===")
    yield

    # ── Shutdown ───────────────────────────────────────────────────────────────
    logger.info("=== Fraud Scorer — shutdown ===")
    app.state.decision_log.close()


# ── Application ────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Fraud Scorer",
    description="Real-time card fraud scoring — champion + shadow challenger.",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(health_router)
app.include_router(score_router)

instrument_app(app)
