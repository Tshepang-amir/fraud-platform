"""Feature engineering — shared logic for training and serving.

Feature naming: all engineered columns are prefixed 'fe_' to distinguish
them from raw IEEE-CIS features (V1-V339, C1-C14, D1-D15).

LEAKAGE RULES enforced here:
  - All rolling windows use closed='left': the current transaction is NOT
    included in its own historical statistics.
  - Peer deviation uses TRAINING-ONLY statistics (PeerStats). Call
    compute_peer_stats(train_df) once on the training split, then pass the
    result to engineer_features() when processing val and test — never derive
    stats from the split being transformed.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Rolling window sizes in seconds (TransactionDT is seconds from reference date)
WINDOW_1H: int = 3_600
WINDOW_24H: int = 86_400
WINDOW_7D: int = 604_800

# All engineered feature column names — must mirror Feast FeatureView definitions
ENGINEERED_FEATURE_COLS: list[str] = [
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

# (median, std) statistics per ProductCD derived from the training set
PeerStats = dict[str, tuple[float, float]]


def compute_peer_stats(train_df: pd.DataFrame) -> PeerStats:
    """Compute per-ProductCD (median, std) from the TRAINING split only.

    Must be called on the training set. Pass the result to engineer_features()
    when processing validation and test splits to prevent target leakage.
    """
    stats: PeerStats = {}
    for prod, grp in train_df.groupby("ProductCD")["TransactionAmt"]:
        std = float(grp.std(ddof=0))
        stats[str(prod)] = (float(grp.median()), std if std > 0 else 1.0)
    return stats


def _to_dt_index(dt_series: pd.Series) -> pd.DatetimeIndex:
    """Convert integer seconds to DatetimeIndex for time-based rolling."""
    return pd.DatetimeIndex(pd.to_datetime(dt_series, unit="s"))


def _rolling_velocity(
    df_sorted: pd.DataFrame,
    window_secs: int,
    col_name: str,
) -> pd.Series:
    """Count of prior same-card transactions within window_secs (leakage-safe).

    closed='left' excludes the current transaction from its own count.
    """
    parts: list[pd.Series] = []

    for _, group in df_sorted.groupby("card1", sort=False):
        g = group.sort_values("TransactionDT")
        counts = (
            pd.Series(1.0, index=_to_dt_index(g["TransactionDT"]))
            .rolling(
                window=pd.Timedelta(seconds=window_secs),
                closed="left",
                min_periods=0,
            )
            .count()
        )
        counts.index = g.index
        parts.append(counts)

    return pd.concat(parts).reindex(df_sorted.index).rename(col_name)


def _rolling_amt_stats(df_sorted: pd.DataFrame) -> pd.DataFrame:
    """Past 24h TransactionAmt mean and std per card (leakage-safe).

    min_periods=1 for mean so a card's first transaction gets its own amount
    as the baseline (rather than NaN).
    """
    mean_parts: list[pd.Series] = []
    std_parts: list[pd.Series] = []

    for _, group in df_sorted.groupby("card1", sort=False):
        g = group.sort_values("TransactionDT")
        amt = pd.Series(
            g["TransactionAmt"].values,
            index=_to_dt_index(g["TransactionDT"]),
            dtype=float,
        )
        roll = amt.rolling(
            window=pd.Timedelta(seconds=WINDOW_24H),
            closed="left",
            min_periods=1,
        )
        m = roll.mean()
        s = roll.std(ddof=0).fillna(0.0)

        m.index = g.index
        s.index = g.index
        mean_parts.append(m)
        std_parts.append(s)

    return pd.DataFrame(
        {
            "fe_card_amt_mean_24h": pd.concat(mean_parts).reindex(df_sorted.index),
            "fe_card_amt_std_24h": pd.concat(std_parts).reindex(df_sorted.index),
        }
    )


def _time_since_last_txn(df_sorted: pd.DataFrame) -> pd.Series:
    """Seconds elapsed since the same card's previous transaction."""
    prev = df_sorted.groupby("card1", sort=False)["TransactionDT"].shift(1)
    elapsed = (df_sorted["TransactionDT"] - prev).fillna(0.0).clip(lower=0.0)
    elapsed.name = "fe_time_since_last_txn"
    return elapsed


def _card_entropy_series(g_sorted: pd.DataFrame) -> pd.Series:
    """Shannon entropy of past-7d ProductCD distribution per transaction, O(n).

    Uses a two-pointer sliding window so each transaction is added/removed once.
    Entropy is computed BEFORE adding the current transaction (closed='left').
    """
    dt_vals = g_sorted["TransactionDT"].values
    prod_vals = g_sorted["ProductCD"].values
    n = len(dt_vals)
    results = np.zeros(n, dtype=float)

    left = 0
    counts: dict[str, int] = {}

    for right in range(n):
        # Evict transactions that left the 7-day window
        while left < right and dt_vals[left] < dt_vals[right] - WINDOW_7D:
            out = prod_vals[left]
            counts[out] -= 1
            if counts[out] == 0:
                del counts[out]
            left += 1

        # Entropy of the window before adding the current transaction
        total = sum(counts.values())
        if total > 0:
            probs = np.array(list(counts.values()), dtype=float) / total
            results[right] = float(-(probs * np.log(probs + 1e-10)).sum())

        # Add current transaction into the window
        p = prod_vals[right]
        counts[p] = counts.get(p, 0) + 1

    return pd.Series(results, index=g_sorted.index, name="fe_card_entropy_product_7d")


def _rolling_product_entropy(df_sorted: pd.DataFrame) -> pd.Series:
    """Shannon entropy of transaction-type distribution per card over past 7 days.

    High entropy = card used across many transaction types (typical behaviour).
    Low entropy = card concentrated in one type (potentially anomalous).
    """
    parts: list[pd.Series] = []
    for _, group in df_sorted.groupby("card1", sort=False):
        parts.append(_card_entropy_series(group.sort_values("TransactionDT")))
    return pd.concat(parts).reindex(df_sorted.index).rename("fe_card_entropy_product_7d")


def _peer_amt_deviation(df: pd.DataFrame, peer_stats: PeerStats) -> pd.Series:
    """Signed z-score of TransactionAmt relative to training-set peer-group stats.

    Uses ProductCD as the peer group. peer_stats must come from
    compute_peer_stats(train_df) to prevent leakage into val/test.
    """
    median_map = {k: v[0] for k, v in peer_stats.items()}
    std_map = {k: v[1] for k, v in peer_stats.items()}

    medians = df["ProductCD"].map(median_map).fillna(0.0)
    stds = df["ProductCD"].map(std_map).fillna(1.0).clip(lower=1.0)
    return ((df["TransactionAmt"] - medians) / stds).rename("fe_peer_amt_deviation")


def engineer_features(
    df: pd.DataFrame,
    *,
    peer_stats: PeerStats | None = None,
) -> pd.DataFrame:
    """Compute all engineered features and append them to the input DataFrame.

    This is the AUTHORITATIVE feature computation function used by:
      - Training pipeline:      train_lgbm.py, train_catboost.py
      - Feast materialisation:  feast_materialise.py (offline → online sync)
      - Serving:                feature_service.py fetches Feast online equivalents

    All rolling features use closed='left' — no transaction is included in its
    own historical statistics (leakage-safe by construction).

    Args:
        df: IEEE-CIS DataFrame. Required columns: TransactionID, TransactionDT,
            TransactionAmt, card1, ProductCD.
        peer_stats: Output of compute_peer_stats(train_df). When None, stats are
            derived from df itself — acceptable for exploration, but leaks into
            val/test if you pass the full dataset.

    Returns:
        df with ENGINEERED_FEATURE_COLS appended, original row order preserved,
        index reset to 0..n-1.

    Raises:
        KeyError: A required column is missing.
    """
    required = {"TransactionID", "TransactionDT", "TransactionAmt", "card1", "ProductCD"}
    missing = required - set(df.columns)
    if missing:
        raise KeyError(f"Missing required columns: {sorted(missing)}")

    logger.info("Engineering features for %d transactions", len(df))

    df_sorted = df.sort_values(["card1", "TransactionDT"]).copy()

    # ── Velocity ──────────────────────────────────────────────────────────────
    df_sorted["fe_card_txn_count_1h"] = _rolling_velocity(
        df_sorted, WINDOW_1H, "fe_card_txn_count_1h"
    )
    df_sorted["fe_card_txn_count_24h"] = _rolling_velocity(
        df_sorted, WINDOW_24H, "fe_card_txn_count_24h"
    )
    df_sorted["fe_card_txn_count_7d"] = _rolling_velocity(
        df_sorted, WINDOW_7D, "fe_card_txn_count_7d"
    )

    # ── Amount statistics ──────────────────────────────────────────────────────
    amt_stats = _rolling_amt_stats(df_sorted)
    df_sorted["fe_card_amt_mean_24h"] = amt_stats["fe_card_amt_mean_24h"]
    df_sorted["fe_card_amt_std_24h"] = amt_stats["fe_card_amt_std_24h"]

    # First transaction per card has no prior 24h history → mean is NaN.
    # Fill with the transaction's own amount: baseline = self, z-score = 0.
    df_sorted["fe_card_amt_mean_24h"] = df_sorted["fe_card_amt_mean_24h"].fillna(
        df_sorted["TransactionAmt"]
    )
    std_safe = df_sorted["fe_card_amt_std_24h"].clip(lower=1.0)
    df_sorted["fe_card_amt_zscore_24h"] = (
        (df_sorted["TransactionAmt"] - df_sorted["fe_card_amt_mean_24h"]) / std_safe
    ).fillna(0.0)

    # ── Recency ────────────────────────────────────────────────────────────────
    df_sorted["fe_time_since_last_txn"] = _time_since_last_txn(df_sorted)

    # ── Behavioural diversity ──────────────────────────────────────────────────
    df_sorted["fe_card_entropy_product_7d"] = _rolling_product_entropy(df_sorted)

    # ── Peer deviation ─────────────────────────────────────────────────────────
    if peer_stats is None:
        peer_stats = compute_peer_stats(df_sorted)
    df_sorted["fe_peer_amt_deviation"] = _peer_amt_deviation(df_sorted, peer_stats)

    result = df_sorted.sort_values("TransactionID").reset_index(drop=True)
    logger.info("Feature engineering complete: added %d features", len(ENGINEERED_FEATURE_COLS))
    return result
