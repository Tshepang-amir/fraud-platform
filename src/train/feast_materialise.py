"""Offline → online feature materialisation for Feast.

Day 5 deliverable. Runs feature engineering on the training + validation
splits, saves the result to a parquet file (Feast offline source), then
materialises to the Postgres online store.

Usage (from fraud-platform/ project root):
    export FEAST_POSTGRES_PASSWORD=local_dev_only   # local dev only
    python -m src.train.feast_materialise

Production (Databricks / Day 7):
    Set FEAST_POSTGRES_PASSWORD from Key Vault; swap offline source to ADLS.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import pandas as pd
from feast import FeatureStore

from src.train.feature_engineering import compute_peer_stats, engineer_features
from src.train.temporal_split import temporal_split

logger = logging.getLogger(__name__)

# ── Paths ────────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent.parent.parent
FEATURE_REPO = PROJECT_ROOT / "feature_repo"
PARQUET_DIR = PROJECT_ROOT / "data" / "feast"
PARQUET_PATH = PARQUET_DIR / "card_transaction_stats.parquet"

# IEEE-CIS TransactionDT reference epoch: 2017-01-01 UTC
# TransactionDT is seconds elapsed since this date.
TXN_EPOCH = pd.Timestamp("2017-01-01", tz="UTC")

# Columns to keep in the parquet (entity key + features + timestamps)
FEATURE_COLS = [
    "fe_card_txn_count_1h",
    "fe_card_txn_count_24h",
    "fe_card_txn_count_7d",
    "fe_card_amt_mean_24h",
    "fe_card_amt_std_24h",
    "fe_card_amt_zscore_24h",
    "fe_time_since_last_txn",
    "fe_card_entropy_product_7d",
    "fe_peer_amt_deviation",
]


def _dt_to_timestamp(dt_series: pd.Series) -> pd.Series:
    """Convert TransactionDT (integer seconds) to UTC datetime."""
    return TXN_EPOCH + pd.to_timedelta(dt_series, unit="s")


def build_feature_parquet(
    transaction_path: str = "data/raw/train_transaction.csv",
    identity_path: str = "data/raw/train_identity.csv",
) -> Path:
    """Engineer features for train+val splits and write to parquet.

    Uses only train split for peer_stats to prevent leakage into val.
    Returns the path to the written parquet file.
    """
    logger.info("Loading %s", transaction_path)
    txn = pd.read_csv(transaction_path)
    logger.info("Loading %s", identity_path)
    identity = pd.read_csv(identity_path)
    df = txn.merge(identity, on="TransactionID", how="left")
    logger.info("Merged: %d rows x %d cols", *df.shape)

    split = temporal_split(df)

    peer_stats = compute_peer_stats(split.train)

    logger.info("Engineering features for train split (%d rows)...", len(split.train))
    train_fe = engineer_features(split.train, peer_stats=peer_stats)

    logger.info("Engineering features for val split (%d rows)...", len(split.val))
    val_fe = engineer_features(split.val, peer_stats=peer_stats)

    combined = pd.concat([train_fe, val_fe], ignore_index=True)

    combined["event_timestamp"] = _dt_to_timestamp(combined["TransactionDT"])
    combined["created_timestamp"] = combined["event_timestamp"]

    keep_cols = ["card1", "event_timestamp", "created_timestamp", *FEATURE_COLS]
    feast_df = combined[keep_cols].copy()

    PARQUET_DIR.mkdir(parents=True, exist_ok=True)
    feast_df.to_parquet(PARQUET_PATH, index=False)
    logger.info(
        "Wrote %d rows to %s",
        len(feast_df),
        PARQUET_PATH,
    )
    return PARQUET_PATH


def apply_and_materialise() -> None:
    """Run feast apply then materialise all feature views to Postgres.

    Requires FEAST_POSTGRES_PASSWORD to be set in the environment.
    For local dev: export FEAST_POSTGRES_PASSWORD=local_dev_only
    """
    os.environ.setdefault("FEAST_POSTGRES_PASSWORD", "local_dev_only")

    logger.info("Initialising FeatureStore from %s", FEATURE_REPO)
    store = FeatureStore(repo_path=str(FEATURE_REPO))

    logger.info("Running feast apply (programmatic)...")
    # Temporarily add feature_repo to sys.path so Feast's relative imports work
    # (entities.py uses `from entities import card`, not `from feature_repo.entities import card`)
    sys.path.insert(0, str(FEATURE_REPO))
    try:
        import importlib

        entities_mod = importlib.import_module("entities")
        features_mod = importlib.import_module("features")
        services_mod = importlib.import_module("feature_services")
        objects = [
            entities_mod.card,
            features_mod.card_stats_source,
            features_mod.card_transaction_stats,
            services_mod.fraud_scoring_service,
        ]
    finally:
        sys.path.pop(0)

    store.apply(objects)

    # Materialise the full time range of the parquet
    feast_df = pd.read_parquet(PARQUET_PATH, columns=["event_timestamp"])
    start_date = feast_df["event_timestamp"].min().to_pydatetime()
    end_date = feast_df["event_timestamp"].max().to_pydatetime()
    logger.info(
        "Materialising from %s to %s ...",
        start_date.strftime("%Y-%m-%d"),
        end_date.strftime("%Y-%m-%d"),
    )
    store.materialize(start_date=start_date, end_date=end_date)
    logger.info("Materialisation complete.")


def run(
    transaction_path: str = "data/raw/train_transaction.csv",
    identity_path: str = "data/raw/train_identity.csv",
) -> None:
    build_feature_parquet(transaction_path, identity_path)
    apply_and_materialise()
    print("\nFeast materialisation complete.")
    print(f"Parquet: {PARQUET_PATH}")
    print("Online store (Postgres) is populated and ready.")
    print("\nRun the skew test:")
    print("  pytest tests/integration/test_feature_skew.py -v")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    run()
    sys.exit(0)
