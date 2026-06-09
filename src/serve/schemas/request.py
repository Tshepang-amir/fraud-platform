"""Pydantic v2 TransactionRequest schema for POST /score.

Required fields: transaction_id, card1, TransactionAmt.
All other IEEE-CIS transaction fields are optional — LightGBM treats missing
values as NaN, which carries predictive signal for the V-features.
Extra fields are forwarded to the feature vector unchanged.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class TransactionRequest(BaseModel):
    """Incoming transaction payload for fraud scoring.

    Callers may send any subset of IEEE-CIS transaction columns as extra
    fields alongside the three required fields below.  The more fields
    supplied, the richer the feature vector and the more accurate the score.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    transaction_id: str = Field(..., description="Unique transaction identifier (for logging)")
    card1: int = Field(..., description="Primary card identifier — Feast online store entity key")
    TransactionAmt: float = Field(..., description="Transaction amount in USD")
