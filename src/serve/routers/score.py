"""POST /score endpoint — champion scoring with shadow challenger.

Rule 5: the challenger scores in a BackgroundTask.  Its result is NEVER
returned to the caller and NEVER used in any live decision path.
It writes only to the shadow_decisions table for offline comparison.
"""

from __future__ import annotations

import logging
import os
import time
import uuid

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from src.serve.middleware.telemetry import record_score
from src.serve.schemas.request import TransactionRequest
from src.serve.schemas.response import ScoreResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["scoring"])

# Decision thresholds — configurable via env vars, with sensible defaults
_THRESHOLD_REVIEW: float = float(os.getenv("FRAUD_THRESHOLD_REVIEW", "0.50"))
_THRESHOLD_DECLINE: float = float(os.getenv("FRAUD_THRESHOLD_DECLINE", "0.90"))


def _make_decision(score: float) -> str:
    if score >= _THRESHOLD_DECLINE:
        return "DECLINE"
    if score >= _THRESHOLD_REVIEW:
        return "REVIEW"
    return "APPROVE"


def _log_champion_decision(
    request_id: str,
    transaction_id: str,
    card1: int,
    fraud_score: float,
    decision: str,
    model_version: str,
    latency_ms: float,
    decision_svc: object,
) -> None:
    """Background task: log the champion decision without delaying the response."""
    try:
        decision_svc.log_decision(  # type: ignore[attr-defined]
            request_id=request_id,
            transaction_id=transaction_id,
            card1=card1,
            fraud_score=fraud_score,
            decision=decision,
            model_version=model_version,
            latency_ms=latency_ms,
        )
    except Exception:
        logger.exception("champion decision logging failed request_id=%s", request_id)


def _score_and_log_challenger(
    request_id: str,
    transaction_id: str,
    card1: int,
    raw: dict,  # type: ignore[type-arg]
    feast: dict,  # type: ignore[type-arg]
    model_svc: object,
    decision_svc: object,
) -> None:
    """Background task: score with challenger and log to shadow_decisions."""
    try:
        score = model_svc.score_challenger(raw, feast)  # type: ignore[attr-defined]
        if score is None:
            return
        decision_svc.log_shadow(  # type: ignore[attr-defined]
            request_id=request_id,
            transaction_id=transaction_id,
            card1=card1,
            fraud_score=score,
            model_version=model_svc.challenger_run_id,  # type: ignore[attr-defined]
        )
        logger.debug("shadow_decision request_id=%s challenger_score=%.4f", request_id, score)
    except Exception:
        logger.exception("shadow scoring failed request_id=%s", request_id)


@router.post("/score", response_model=ScoreResponse)
async def score_transaction(
    payload: TransactionRequest,
    background_tasks: BackgroundTasks,
    request: Request,
) -> ScoreResponse:
    """Score a transaction with the champion model.

    The challenger scores in a background task (Rule 5).
    Both decisions are written to Postgres for offline comparison.
    """
    t0 = time.perf_counter()
    request_id = str(uuid.uuid4())

    model_svc = request.app.state.model_service
    feature_svc = request.app.state.feature_service
    decision_svc = request.app.state.decision_log

    if not model_svc.ready:
        raise HTTPException(status_code=503, detail="Model not loaded — service not ready")

    # ── 1. Fetch Feast features ────────────────────────────────────────────────
    feast_features = feature_svc.get_features(payload.card1)

    # ── 2. Score with champion ─────────────────────────────────────────────────
    raw_fields = payload.model_dump()
    champion_score = model_svc.score_champion(raw_fields, feast_features)
    decision = _make_decision(champion_score)
    latency_ms = (time.perf_counter() - t0) * 1_000

    # ── 3. Emit OTel metrics + log champion decision ──────────────────────────
    record_score(champion_score, decision, latency_ms)
    background_tasks.add_task(
        _log_champion_decision,
        request_id,
        payload.transaction_id,
        payload.card1,
        champion_score,
        decision,
        model_svc.champion_run_id,
        latency_ms,
        decision_svc,
    )

    logger.debug(
        "scored request_id=%s txn=%s card1=%d score=%.4f decision=%s latency=%.1fms",
        request_id,
        payload.transaction_id,
        payload.card1,
        champion_score,
        decision,
        latency_ms,
    )

    # ── 4. Schedule challenger (Rule 5 — background only) ─────────────────────
    background_tasks.add_task(
        _score_and_log_challenger,
        request_id,
        payload.transaction_id,
        payload.card1,
        raw_fields,
        feast_features,
        model_svc,
        decision_svc,
    )

    return ScoreResponse(
        request_id=request_id,
        transaction_id=payload.transaction_id,
        decision=decision,
        fraud_score=round(champion_score, 6),
        threshold_review=_THRESHOLD_REVIEW,
        threshold_decline=_THRESHOLD_DECLINE,
        model_version=model_svc.champion_run_id,
        latency_ms=round(latency_ms, 2),
    )
