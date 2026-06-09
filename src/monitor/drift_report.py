"""Evidently drift report — reference vs simulated production data.

Usage:
    python -m src.monitor.drift_report

Outputs:
    reports/drift_report.html   — Evidently HTML report
    reports/psi_scores.json     — PSI per feature (Rule 6 thresholds applied)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from src.monitor.psi import FEAST_FEATURES, compute_psi, psi_status

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_FEAST_PARQUET = _REPO_ROOT / "data" / "feast" / "card_transaction_stats.parquet"
_REPORTS_DIR = _REPO_ROOT / "reports"


def load_reference_and_current() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Reference = first 80% of feast parquet; current = last 20% with simulated shift."""
    df = pd.read_parquet(_FEAST_PARQUET, columns=FEAST_FEATURES).dropna()

    split = int(len(df) * 0.8)
    reference = df.iloc[:split].reset_index(drop=True)
    current = df.iloc[split:].copy().reset_index(drop=True)

    # Simulate a realistic distribution shift on two high-signal features.
    # Represents increased transaction velocity + inflated amounts — typical fraud wave.
    rng = np.random.default_rng(42)
    current["fe_card_txn_count_24h"] = current["fe_card_txn_count_24h"] * rng.uniform(
        0.9, 1.4, size=len(current)
    )
    current["fe_card_amt_mean_24h"] = current["fe_card_amt_mean_24h"] * rng.uniform(
        0.85, 1.35, size=len(current)
    )

    return reference, current


def generate_report(output_dir: Path = _REPORTS_DIR) -> dict[str, float]:
    """Generate Evidently HTML report + PSI summary. Returns PSI per feature dict."""
    from evidently.legacy.metric_preset import DataDriftPreset
    from evidently.legacy.report import Report

    output_dir.mkdir(parents=True, exist_ok=True)
    reference, current = load_reference_and_current()

    report = Report(metrics=[DataDriftPreset()])
    report.run(reference_data=reference, current_data=current)

    html_path = output_dir / "drift_report.html"
    report.save_html(str(html_path))
    logger.info("Drift report saved to %s", html_path)

    psi_scores = compute_psi(reference, current, FEAST_FEATURES)

    logger.info("PSI Summary (Rule 6 — <0.10 ok | 0.10-0.20 warn | >0.20 retrain)")
    for feat, score in sorted(psi_scores.items(), key=lambda x: -x[1]):
        status = psi_status(score)
        logger.info("  %-38s %.4f  [%s]", feat, score, status)

    psi_path = output_dir / "psi_scores.json"
    psi_path.write_text(json.dumps(psi_scores, indent=2))
    logger.info("PSI scores saved to %s", psi_path)

    return psi_scores


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    generate_report()
