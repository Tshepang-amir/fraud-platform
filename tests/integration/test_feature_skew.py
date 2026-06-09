"""THE critical test: offline feature values == online feature values. (Rule 2)

For a known card_id at a known timestamp, the feature value retrieved from
Feast offline store (training path) must exactly equal the feature value
retrieved from Feast online store (serving path) within 1e-6.

IF THIS TEST FAILS, STOP. DO NOT DEPLOY. FIX THE FEATURE DEFINITION.

Prerequisites (run before this test):
    docker-compose up postgres -d
    export FEAST_POSTGRES_PASSWORD=local_dev_only
    python -m src.train.feast_materialise

Run:
    pytest tests/integration/test_feature_skew.py -v
"""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import pytest

# Set local-dev password before Feast imports resolve the env var.
# In production, FEAST_POSTGRES_PASSWORD is injected from Key Vault.
os.environ.setdefault("FEAST_POSTGRES_PASSWORD", "local_dev_only")

from feast import FeatureStore

PROJECT_ROOT = Path(__file__).parent.parent.parent
FEATURE_REPO = PROJECT_ROOT / "feature_repo"
PARQUET_PATH = PROJECT_ROOT / "data" / "feast" / "card_transaction_stats.parquet"

FEATURE_COLS = [
    "card_transaction_stats:fe_card_txn_count_1h",
    "card_transaction_stats:fe_card_txn_count_24h",
    "card_transaction_stats:fe_card_txn_count_7d",
    "card_transaction_stats:fe_card_amt_mean_24h",
    "card_transaction_stats:fe_card_amt_std_24h",
    "card_transaction_stats:fe_card_amt_zscore_24h",
    "card_transaction_stats:fe_time_since_last_txn",
    "card_transaction_stats:fe_card_entropy_product_7d",
    "card_transaction_stats:fe_peer_amt_deviation",
]
FEATURE_SHORT = [f.split(":")[1] for f in FEATURE_COLS]

TOLERANCE = 1e-6
N_TEST_CARDS = 5


@pytest.fixture(scope="module")
def feast_store() -> FeatureStore:
    if not PARQUET_PATH.exists():
        pytest.skip(
            f"Parquet not found at {PARQUET_PATH}. Run: python -m src.train.feast_materialise"
        )
    return FeatureStore(repo_path=str(FEATURE_REPO))


@pytest.fixture(scope="module")
def test_cards(feast_store: FeatureStore) -> pd.DataFrame:
    """Return the latest row per card for N_TEST_CARDS cards.

    These are the rows that should exist in the online store after materialisation.
    Using cards with at least 10 transactions ensures non-trivial rolling features.
    """
    df = pd.read_parquet(PARQUET_PATH)
    counts = df["card1"].value_counts()
    eligible = counts[counts >= 10].index.tolist()
    if len(eligible) < N_TEST_CARDS:
        pytest.skip("Not enough cards with ≥10 transactions in the parquet.")

    sample_cards = eligible[:N_TEST_CARDS]
    latest_rows = (
        df[df["card1"].isin(sample_cards)]
        .sort_values("event_timestamp")
        .groupby("card1")
        .last()
        .reset_index()
    )
    return latest_rows[["card1", "event_timestamp", *FEATURE_SHORT]]


@pytest.mark.integration
class TestFeatureSkew:
    """Validate training/serving feature consistency via Feast."""

    def test_offline_equals_online_for_known_cards(
        self,
        feast_store: FeatureStore,
        test_cards: pd.DataFrame,
    ) -> None:
        """Offline retrieval must match online for the same entity + timestamp.

        This test is the gate before any deployment (Rule 2). If it fails,
        the feature definition or materialisation is broken — fix before shipping.
        """
        # ── Offline retrieval ─────────────────────────────────────────────────
        entity_df = test_cards[["card1", "event_timestamp"]].copy()
        entity_df["event_timestamp"] = pd.to_datetime(entity_df["event_timestamp"], utc=True)

        offline_df = (
            feast_store.get_historical_features(
                entity_df=entity_df,
                features=FEATURE_COLS,
            )
            .to_df()
            .sort_values("card1")
            .reset_index(drop=True)
        )

        # ── Online retrieval ──────────────────────────────────────────────────
        entity_rows = [{"card1": int(c)} for c in test_cards["card1"].tolist()]
        online_response = feast_store.get_online_features(
            entity_rows=entity_rows,
            features=FEATURE_COLS,
        ).to_dict()

        online_df = pd.DataFrame(online_response).sort_values("card1").reset_index(drop=True)

        # ── Compare ───────────────────────────────────────────────────────────
        assert set(offline_df["card1"].tolist()) == set(online_df["card1"].tolist()), (
            "Card IDs do not match between offline and online results."
        )

        failures: list[str] = []
        for feat in FEATURE_SHORT:
            offline_vals = offline_df[feat].values
            online_vals = online_df[feat].values
            for i, (off, on) in enumerate(zip(offline_vals, online_vals, strict=True)):
                if off is None or on is None:
                    failures.append(
                        f"card1={offline_df['card1'].iloc[i]} | {feat}: "
                        f"offline={off} online={on} — NULL value"
                    )
                elif abs(float(off) - float(on)) > TOLERANCE:
                    failures.append(
                        f"card1={offline_df['card1'].iloc[i]} | {feat}: "
                        f"offline={off:.8f} online={on:.8f} "
                        f"diff={abs(float(off) - float(on)):.2e}"
                    )

        assert not failures, (
            f"Training/serving skew detected in {len(failures)} feature(s):\n"
            + "\n".join(failures)
            + "\n\nDO NOT DEPLOY until this test passes."
        )

    def test_all_feature_columns_present_online(
        self,
        feast_store: FeatureStore,
        test_cards: pd.DataFrame,
    ) -> None:
        """Every expected feature column must be present in the online response."""
        entity_rows = [{"card1": int(c)} for c in test_cards["card1"].iloc[:1].tolist()]
        response = feast_store.get_online_features(
            entity_rows=entity_rows,
            features=FEATURE_COLS,
        ).to_dict()

        missing = [f for f in FEATURE_SHORT if f not in response]
        assert not missing, f"Missing features in online store: {missing}"

    def test_online_values_are_not_all_null(
        self,
        feast_store: FeatureStore,
        test_cards: pd.DataFrame,
    ) -> None:
        """Online store must contain actual feature values, not all NULLs."""
        entity_rows = [{"card1": int(c)} for c in test_cards["card1"].tolist()]
        response = feast_store.get_online_features(
            entity_rows=entity_rows,
            features=FEATURE_COLS,
        ).to_dict()

        all_null_features = [
            f for f in FEATURE_SHORT if all(v is None for v in response.get(f, [None]))
        ]
        assert not all_null_features, (
            f"All online values are NULL for: {all_null_features}. Was feast_materialise.py run?"
        )
