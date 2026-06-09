"""Feast FeatureService definitions for fraud platform.

The fraud_scoring_service groups all features needed by the champion model
into a single retrieval call. The serving layer uses this service so that
adding a new feature view only requires updating this file, not the API code.
"""

from feast import FeatureService
from features import card_transaction_stats

fraud_scoring_service = FeatureService(
    name="fraud_scoring_service",
    features=[card_transaction_stats],
    description="All features required by the fraud scoring champion model v1",
)
