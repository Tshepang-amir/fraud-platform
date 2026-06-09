"""Feast FeatureView definitions for fraud platform.

The FileSource path is relative to the working directory (project root).
Always run feast commands and Python scripts from fraud-platform/.

Feature names mirror ENGINEERED_FEATURE_COLS in feature_engineering.py — any
rename there must be reflected here and in the Feast online store schema.
"""

import os
from datetime import timedelta

from entities import card
from feast import FeatureView, Field
from feast.infra.offline_stores.file_source import FileSource
from feast.types import Float64

# Resolved from repo_path so the parquet is found regardless of CWD
_REPO_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_REPO_DIR)
PARQUET_PATH = os.path.join(_PROJECT_ROOT, "data", "feast", "card_transaction_stats.parquet")

card_stats_source = FileSource(
    path=PARQUET_PATH,
    timestamp_field="event_timestamp",
    created_timestamp_column="created_timestamp",
)

card_transaction_stats = FeatureView(
    name="card_transaction_stats",
    entities=[card],
    ttl=timedelta(days=90),
    schema=[
        Field(name="fe_card_txn_count_1h", dtype=Float64),
        Field(name="fe_card_txn_count_24h", dtype=Float64),
        Field(name="fe_card_txn_count_7d", dtype=Float64),
        Field(name="fe_card_amt_mean_24h", dtype=Float64),
        Field(name="fe_card_amt_std_24h", dtype=Float64),
        Field(name="fe_card_amt_zscore_24h", dtype=Float64),
        Field(name="fe_time_since_last_txn", dtype=Float64),
        Field(name="fe_card_entropy_product_7d", dtype=Float64),
        Field(name="fe_peer_amt_deviation", dtype=Float64),
    ],
    source=card_stats_source,
    online=True,
    description="Per-card rolling transaction statistics — v1 feature set",
)
