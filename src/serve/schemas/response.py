"""Pydantic v2 ScoreResponse schema for POST /score."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ScoreResponse(BaseModel):
    """Fraud scoring decision returned to the caller.

    Only the champion model decision is included here.
    The challenger scores in the background and writes to shadow_decisions only.
    """

    request_id: str = Field(..., description="UUID assigned to this scoring request")
    transaction_id: str = Field(..., description="Transaction ID from the request")
    decision: str = Field(..., description="APPROVE | REVIEW | DECLINE")
    fraud_score: float = Field(
        ..., ge=0.0, le=1.0, description="Champion fraud probability [0, 1]"
    )
    threshold_review: float = Field(
        ..., description="Lower decision threshold (score >= this → REVIEW)"
    )
    threshold_decline: float = Field(
        ..., description="Upper decision threshold (score >= this → DECLINE)"
    )
    model_version: str = Field(..., description="MLflow run ID of the champion model")
    latency_ms: float = Field(..., description="End-to-end request latency in milliseconds")
