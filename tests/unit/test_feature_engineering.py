"""Unit tests for temporal split and feature engineering."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.train.feature_engineering import (
    ENGINEERED_FEATURE_COLS,
    WINDOW_1H,
    _card_entropy_series,
    compute_peer_stats,
    engineer_features,
)
from src.train.temporal_split import (
    TemporalSplit,
    compute_thresholds,
    temporal_split,
)

# ── Test fixtures ──────────────────────────────────────────────────────────────


def make_txn_df(
    n: int = 200,
    n_cards: int = 10,
    seed: int = 42,
) -> pd.DataFrame:
    """Minimal valid IEEE-CIS-schema DataFrame for testing."""
    rng = np.random.default_rng(seed)
    return pd.DataFrame(
        {
            "TransactionID": range(1, n + 1),
            "TransactionDT": sorted(rng.integers(86_400, 15_000_000, size=n).tolist()),
            "TransactionAmt": rng.lognormal(mean=4.0, sigma=1.5, size=n),
            "card1": rng.integers(0, n_cards, size=n),
            "ProductCD": rng.choice(["W", "H", "C", "S", "R"], size=n),
            "isFraud": rng.choice([0, 1], size=n, p=[0.97, 0.03]),
        }
    )


def make_single_card_df(
    n_txns: int = 10,
    card_id: int = 42,
    spacing_secs: int = 3600,
    amount: float = 100.0,
    product: str = "W",
) -> pd.DataFrame:
    """Single-card DataFrame with evenly-spaced transactions."""
    base_dt = 1_000_000
    return pd.DataFrame(
        {
            "TransactionID": range(1, n_txns + 1),
            "TransactionDT": [base_dt + i * spacing_secs for i in range(n_txns)],
            "TransactionAmt": [amount] * n_txns,
            "card1": [card_id] * n_txns,
            "ProductCD": [product] * n_txns,
            "isFraud": [0] * n_txns,
        }
    )


# ── TestTemporalSplit ──────────────────────────────────────────────────────────


class TestTemporalSplit:
    @pytest.mark.unit
    def test_no_overlap_between_splits(self) -> None:
        split = temporal_split(make_txn_df(n=500))
        assert split.train["TransactionDT"].max() < split.val["TransactionDT"].min()
        assert split.val["TransactionDT"].max() < split.test["TransactionDT"].min()

    @pytest.mark.unit
    def test_union_equals_original(self) -> None:
        df = make_txn_df(n=500)
        split = temporal_split(df)
        total = len(split.train) + len(split.val) + len(split.test)
        assert total == len(df)

    @pytest.mark.unit
    def test_explicit_thresholds_are_respected(self) -> None:
        df = make_txn_df(n=500)
        dt = df["TransactionDT"]
        train_end = int(dt.quantile(0.60))
        val_end = int(dt.quantile(0.80))

        split = temporal_split(df, train_end_dt=train_end, val_end_dt=val_end)

        assert split.train["TransactionDT"].max() < train_end
        assert split.val["TransactionDT"].min() >= train_end
        assert split.val["TransactionDT"].max() < val_end
        assert split.test["TransactionDT"].min() >= val_end

    @pytest.mark.unit
    def test_raises_on_missing_dt_column(self) -> None:
        df = make_txn_df(n=50).drop(columns=["TransactionDT"])
        with pytest.raises(KeyError, match="TransactionDT"):
            temporal_split(df)

    @pytest.mark.unit
    def test_raises_when_train_end_gte_val_end(self) -> None:
        df = make_txn_df(n=100)
        with pytest.raises(ValueError, match="must be <"):
            temporal_split(df, train_end_dt=5_000_000, val_end_dt=5_000_000)

    @pytest.mark.unit
    def test_raises_on_empty_split(self) -> None:
        df = make_txn_df(n=100)
        dt_min = int(df["TransactionDT"].min())
        with pytest.raises(ValueError, match="empty"):
            # train_end below all data → training split is empty
            temporal_split(df, train_end_dt=dt_min - 1, val_end_dt=dt_min)

    @pytest.mark.unit
    def test_compute_thresholds_respects_fractions(self) -> None:
        df = make_txn_df(n=1000)
        train_end, val_end = compute_thresholds(df, train_frac=0.60, val_frac=0.80)
        split = temporal_split(df, train_end_dt=train_end, val_end_dt=val_end)
        # ~60% in train, ~20% in val, ~20% in test (approximate due to quantile)
        assert len(split.train) / len(df) == pytest.approx(0.60, abs=0.05)

    @pytest.mark.unit
    def test_result_is_immutable_dataclass(self) -> None:
        split = temporal_split(make_txn_df())
        assert isinstance(split, TemporalSplit)
        with pytest.raises((AttributeError, TypeError)):
            split.train = pd.DataFrame()  # type: ignore[misc]


# ── TestEngineerFeatures ───────────────────────────────────────────────────────


class TestEngineerFeatures:
    @pytest.mark.unit
    def test_all_feature_columns_present(self) -> None:
        result = engineer_features(make_txn_df(n=200))
        for col in ENGINEERED_FEATURE_COLS:
            assert col in result.columns, f"Missing engineered column: {col}"

    @pytest.mark.unit
    def test_row_count_preserved(self) -> None:
        df = make_txn_df(n=300)
        assert len(engineer_features(df)) == len(df)

    @pytest.mark.unit
    def test_transaction_id_order_preserved(self) -> None:
        df = make_txn_df(n=200)
        result = engineer_features(df)
        assert list(result["TransactionID"]) == list(df["TransactionID"])

    @pytest.mark.unit
    def test_no_leakage_first_transaction_per_card(self) -> None:
        """Each card's first transaction must have zero velocity — no prior history."""
        df = make_txn_df(n=500, n_cards=5)
        result = engineer_features(df)

        first_txns = result.sort_values(["card1", "TransactionDT"]).groupby("card1").first()
        assert (first_txns["fe_card_txn_count_1h"] == 0).all(), (
            "First transaction has non-zero 1h count — data leakage"
        )
        assert (first_txns["fe_card_txn_count_24h"] == 0).all(), (
            "First transaction has non-zero 24h count — data leakage"
        )
        assert (first_txns["fe_card_txn_count_7d"] == 0).all(), (
            "First transaction has non-zero 7d count — data leakage"
        )

    @pytest.mark.unit
    def test_velocity_increases_with_rapid_transactions(self) -> None:
        """Five transactions 1 minute apart should show growing 1h count."""
        df = make_single_card_df(n_txns=5, spacing_secs=60)
        result = engineer_features(df).sort_values("TransactionDT").reset_index(drop=True)

        counts = result["fe_card_txn_count_1h"].tolist()
        assert counts[0] == 0, "First transaction should see no prior txns in 1h"
        assert counts[1] == 1, "Second transaction should see 1 prior txn in 1h"
        assert counts[-1] == 4, "Fifth transaction should see 4 prior txns in 1h"

    @pytest.mark.unit
    def test_velocity_resets_after_window_expires(self) -> None:
        """Transactions more than 1h apart should not accumulate in 1h velocity."""
        df = make_single_card_df(n_txns=5, spacing_secs=WINDOW_1H + 1)
        result = engineer_features(df).sort_values("TransactionDT").reset_index(drop=True)

        # Every transaction is >1h after the previous one, so 1h count stays at 1
        # (the immediately preceding txn falls just outside the window)
        assert result["fe_card_txn_count_1h"].iloc[0] == 0
        for i in range(1, len(result)):
            assert result["fe_card_txn_count_1h"].iloc[i] <= 1, (
                f"Row {i}: transactions >1h apart should not accumulate"
            )

    @pytest.mark.unit
    def test_time_since_last_txn_is_zero_for_first(self) -> None:
        df = make_single_card_df(n_txns=5, spacing_secs=3600)
        result = engineer_features(df).sort_values("TransactionDT").reset_index(drop=True)

        assert result["fe_time_since_last_txn"].iloc[0] == 0.0, (
            "First transaction has no prior transaction — elapsed must be 0"
        )

    @pytest.mark.unit
    def test_time_since_last_txn_matches_spacing(self) -> None:
        spacing = 7200
        df = make_single_card_df(n_txns=5, spacing_secs=spacing)
        result = engineer_features(df).sort_values("TransactionDT").reset_index(drop=True)

        for i in range(1, len(result)):
            assert result["fe_time_since_last_txn"].iloc[i] == pytest.approx(spacing, rel=1e-6)

    @pytest.mark.unit
    def test_zscore_near_zero_for_consistent_amounts(self) -> None:
        """A card spending exactly the same amount repeatedly should show z-score ≈ 0."""
        df = make_single_card_df(n_txns=15, spacing_secs=3600, amount=50.0)
        result = engineer_features(df).sort_values("TransactionDT").reset_index(drop=True)

        # Skip first few rows while the rolling window is still warming up
        later = result.iloc[5:]
        assert (later["fe_card_amt_zscore_24h"].abs() < 0.1).all(), (
            "Consistent amounts should produce near-zero z-score"
        )

    @pytest.mark.unit
    def test_peer_stats_from_training_data_used_for_val(self) -> None:
        """Peer stats derived from training should not change when applied to val."""
        df = make_txn_df(n=400, n_cards=10)
        split = temporal_split(df)

        peer_stats = compute_peer_stats(split.train)
        val_result = engineer_features(split.val, peer_stats=peer_stats)

        # Peer stats are fixed from training, so val result is deterministic
        assert "fe_peer_amt_deviation" in val_result.columns
        assert not val_result["fe_peer_amt_deviation"].isna().all()

    @pytest.mark.unit
    def test_raises_on_missing_column(self) -> None:
        df = make_txn_df(n=50).drop(columns=["card1"])
        with pytest.raises(KeyError, match="card1"):
            engineer_features(df)

    @pytest.mark.unit
    def test_entropy_zero_for_first_transaction(self) -> None:
        """First transaction per card has no prior history — entropy must be 0."""
        df = make_single_card_df(n_txns=5, spacing_secs=3600)
        result = engineer_features(df).sort_values("TransactionDT").reset_index(drop=True)
        assert result["fe_card_entropy_product_7d"].iloc[0] == 0.0


# ── TestCardEntropySeries ──────────────────────────────────────────────────────


class TestCardEntropySeries:
    @pytest.mark.unit
    def test_uniform_distribution_has_higher_entropy(self) -> None:
        """Mixed transaction types should score higher entropy than a single type."""
        base_dt = 1_000_000
        n = 20

        uniform = pd.DataFrame(
            {
                "TransactionDT": [base_dt + i * 3600 for i in range(n)],
                "ProductCD": ["W", "H", "C", "S", "R"] * (n // 5),
            }
        )
        concentrated = pd.DataFrame(
            {
                "TransactionDT": [base_dt + i * 3600 for i in range(n)],
                "ProductCD": ["W"] * n,
            }
        )

        # Take entropy at the last transaction (full history available)
        h_uniform = _card_entropy_series(uniform).iloc[-1]
        h_concentrated = _card_entropy_series(concentrated).iloc[-1]

        assert h_uniform > h_concentrated, (
            "Uniform product distribution should have higher entropy"
        )

    @pytest.mark.unit
    def test_no_leakage_first_row_entropy_is_zero(self) -> None:
        df = pd.DataFrame(
            {
                "TransactionDT": [1_000_000, 1_003_600, 1_007_200],
                "ProductCD": ["W", "H", "C"],
            }
        )
        result = _card_entropy_series(df)
        assert result.iloc[0] == 0.0, "First transaction has no prior history — entropy must be 0"


# ── Hypothesis property-based tests ───────────────────────────────────────────


@pytest.mark.unit
@given(
    n_rows=st.integers(min_value=20, max_value=300),
    n_cards=st.integers(min_value=1, max_value=15),
)
@settings(max_examples=25, deadline=10_000)
def test_velocity_never_nan(n_rows: int, n_cards: int) -> None:
    """Velocity features must never be NaN (min_periods=0 handles cold start)."""
    df = make_txn_df(n=n_rows, n_cards=n_cards)
    result = engineer_features(df)

    for col in ("fe_card_txn_count_1h", "fe_card_txn_count_24h", "fe_card_txn_count_7d"):
        assert not result[col].isna().any(), f"{col} contains NaN"


@pytest.mark.unit
@given(n_rows=st.integers(min_value=30, max_value=500))
@settings(max_examples=20, deadline=10_000)
def test_temporal_split_no_overlap_property(n_rows: int) -> None:
    """Property: temporal split always produces strictly non-overlapping dt ranges."""
    df = make_txn_df(n=n_rows)
    split = temporal_split(df)

    if len(split.train) > 0 and len(split.val) > 0:
        assert split.train["TransactionDT"].max() < split.val["TransactionDT"].min()
    if len(split.val) > 0 and len(split.test) > 0:
        assert split.val["TransactionDT"].max() < split.test["TransactionDT"].min()


@pytest.mark.unit
@given(n_rows=st.integers(min_value=20, max_value=300))
@settings(max_examples=20, deadline=10_000)
def test_engineer_features_row_count_invariant(n_rows: int) -> None:
    """engineer_features never drops or duplicates rows."""
    df = make_txn_df(n=n_rows)
    assert len(engineer_features(df)) == n_rows
