"""GET /health, /ready, /metrics endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

router = APIRouter(tags=["ops"])


@router.get("/health")
async def health() -> JSONResponse:
    """Liveness probe — returns 200 if the process is running."""
    return JSONResponse({"status": "ok"})


@router.get("/ready")
async def ready(request: Request) -> Response:
    """Readiness probe — returns 200 only when the model is loaded.

    Container Apps / Kubernetes will not route traffic until this returns 200.
    """
    model_svc = request.app.state.model_service
    if not model_svc.ready:
        return JSONResponse(
            {
                "status": "not_ready",
                "model_ready": False,
                "reason": "model not loaded",
            },
            status_code=503,
        )
    return JSONResponse(
        {
            "status": "ready",
            "model_ready": True,
            "champion_run": model_svc.champion_run_id,
        }
    )


@router.get("/metrics")
async def metrics(request: Request) -> JSONResponse:
    """Lightweight metrics endpoint — useful for smoke-testing OTel setup."""
    model_svc = request.app.state.model_service
    decision_svc = request.app.state.decision_log
    return JSONResponse(
        {
            "model_ready": model_svc.ready,
            "champion_run_id": model_svc.champion_run_id,
            "challenger_run_id": model_svc.challenger_run_id,
            "db_health": decision_svc.health_check(),
        }
    )
