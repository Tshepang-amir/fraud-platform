"""Population Stability Index (PSI) calculation.

Rule 6 thresholds (fixed — not configurable at runtime):
  PSI < 0.10    → ok      (no action)
  PSI 0.10-0.20 → warn    (log warning)
  PSI > 0.20    → retrain (trigger retraining DAG)
"""

from __future__ import annotations

import numpy as np
import pandas as pd

PSI_WARN_THRESHOLD = 0.10
PSI_RETRAIN_THRESHOLD = 0.20

FEAST_FEATURES = [
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


def _psi_single(reference: np.ndarray, current: np.ndarray, bins: int = 10) -> float:
    breakpoints = np.percentile(reference, np.linspace(0, 100, bins + 1))
    breakpoints[0] = -np.inf
    breakpoints[-1] = np.inf

    ref_pct = np.histogram(reference, bins=breakpoints)[0] / len(reference)
    cur_pct = np.histogram(current, bins=breakpoints)[0] / len(current)

    # Replace zeros to avoid log(0) / division by zero
    ref_pct = np.where(ref_pct == 0, 1e-4, ref_pct)
    cur_pct = np.where(cur_pct == 0, 1e-4, cur_pct)

    return float(np.sum((cur_pct - ref_pct) * np.log(cur_pct / ref_pct)))


def compute_psi(
    reference: pd.DataFrame,
    current: pd.DataFrame,
    features: list[str],
    bins: int = 10,
) -> dict[str, float]:
    """Compute PSI for each feature column. Returns feature → PSI score mapping."""
    return {
        feat: _psi_single(
            reference[feat].dropna().to_numpy(),
            current[feat].dropna().to_numpy(),
            bins=bins,
        )
        for feat in features
    }


def psi_status(value: float) -> str:
    """Rule 6: classify PSI value into action tier."""
    if value >= PSI_RETRAIN_THRESHOLD:
        return "retrain"
    if value >= PSI_WARN_THRESHOLD:
        return "warn"
    return "ok"
