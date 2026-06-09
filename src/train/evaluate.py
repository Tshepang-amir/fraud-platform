"""Model evaluation suite: AUC, AUPRC, TPR@FPR, Brier, lift, bootstrapped CI.

Key metric: TPR at fixed 0.1% FPR — the business metric for fraud detection.
AUC is secondary. Banks care about a specific operating point, not aggregate
discriminability. See brief section "What TPR at fixed FPR means and why it beats AUC."

PSI thresholds (Rule 6) and governance policy are in governance/promotion_policy.md.
"""

from __future__ import annotations

import logging
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    roc_auc_score,
    roc_curve,
)

logger = logging.getLogger(__name__)

FPR_TARGET = 0.001  # 0.1% FPR — the business operating point


def tpr_at_fixed_fpr(
    y_true: np.ndarray,
    y_score: np.ndarray,
    fpr_target: float = FPR_TARGET,
) -> float:
    """Maximum TPR reachable without exceeding fpr_target.

    Uses max(TPR) where FPR ≤ target rather than the nearest ROC point.
    Fraud teams set a hard false-positive budget; we report the best recall
    achievable inside that budget.
    """
    fprs, tprs, _ = roc_curve(y_true, y_score)
    valid = fprs <= fpr_target
    if not np.any(valid):
        return 0.0
    return float(np.max(tprs[valid]))


def compute_metrics(
    y_true: np.ndarray,
    y_prob: np.ndarray,
    *,
    fpr_target: float = FPR_TARGET,
) -> dict[str, float]:
    """Compute the canonical metric suite for a binary fraud classifier."""
    return {
        "auc": float(roc_auc_score(y_true, y_prob)),
        "auprc": float(average_precision_score(y_true, y_prob)),
        "tpr_at_001_fpr": tpr_at_fixed_fpr(y_true, y_prob, fpr_target),
        "brier": float(brier_score_loss(y_true, y_prob)),
    }


def bootstrap_tpr_diff(
    y_true: np.ndarray,
    y_prob_champ: np.ndarray,
    y_prob_chal: np.ndarray,
    *,
    n_boot: int = 1000,
    fpr_target: float = FPR_TARGET,
    seed: int = 42,
) -> dict[str, float]:
    """Bootstrapped 95% CI on TPR difference: challenger - champion.

    Interpretation (matches governance/promotion_policy.md decision rules):
      CI lower bound > 0  → challenger significantly better  → PROMOTE_CHALLENGER
      CI upper bound < 0  → champion significantly better   → KEEP_CHAMPION
      CI spans 0          → difference unclear              → CONTINUE_SHADOW
    """
    rng = np.random.default_rng(seed)
    n = len(y_true)
    diffs = np.empty(n_boot)
    for i in range(n_boot):
        idx = rng.integers(0, n, size=n)
        tpr_c = tpr_at_fixed_fpr(y_true[idx], y_prob_champ[idx], fpr_target)
        tpr_ch = tpr_at_fixed_fpr(y_true[idx], y_prob_chal[idx], fpr_target)
        diffs[i] = tpr_ch - tpr_c
    ci_lo = float(np.percentile(diffs, 2.5))
    ci_hi = float(np.percentile(diffs, 97.5))
    return {
        "mean_diff": float(np.mean(diffs)),
        "ci_lo": ci_lo,
        "ci_hi": ci_hi,
        "n_boot": float(n_boot),
    }


def _lift_at_percentiles(
    y_true: np.ndarray,
    y_prob: np.ndarray,
    pct: np.ndarray,
) -> np.ndarray:
    order = np.argsort(y_prob)[::-1]
    y_sorted = y_true[order]
    base_rate = float(y_true.mean())
    n = len(y_sorted)
    lifts = np.empty(len(pct))
    for j, p in enumerate(pct):
        k = max(1, int(n * p / 100))
        lifts[j] = y_sorted[:k].mean() / base_rate if base_rate > 0 else 0.0
    return lifts


def plot_lift_comparison(
    y_true: np.ndarray,
    y_prob_champ: np.ndarray,
    y_prob_chal: np.ndarray,
    out_path: str,
    *,
    champ_name: str = "LightGBM",
    chal_name: str = "CatBoost",
) -> None:
    """Save lift chart comparing champion and challenger at each population decile."""
    pct = np.arange(1, 101, dtype=float)
    lifts_c = _lift_at_percentiles(y_true, y_prob_champ, pct)
    lifts_ch = _lift_at_percentiles(y_true, y_prob_chal, pct)

    fig, ax = plt.subplots(figsize=(8, 5))
    ax.plot(pct, lifts_c, label=champ_name, color="steelblue")
    ax.plot(pct, lifts_ch, label=chal_name, color="darkorange")
    ax.axhline(1.0, color="k", linestyle="--", alpha=0.5, label="Random baseline")
    ax.set_xlabel("% of population scored (high-risk first)")
    ax.set_ylabel("Lift over random")
    ax.set_title("Lift Chart — Champion vs Challenger")
    ax.legend()
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)


def compare_models(
    y_true: np.ndarray,
    y_prob_champ: np.ndarray,
    y_prob_chal: np.ndarray,
    *,
    champ_name: str = "LightGBM",
    chal_name: str = "CatBoost",
    lift_plot_path: str | None = None,
    n_boot: int = 1000,
) -> dict[str, Any]:
    """Full champion vs challenger comparison with bootstrapped CI.

    Returns dict with per-model metrics, bootstrap CI, and recommendation.

    Recommendation logic (matches governance/promotion_policy.md):
      PROMOTE_CHALLENGER  — challenger TPR strictly better at 95% confidence
      KEEP_CHAMPION       — champion TPR strictly better at 95% confidence
      CONTINUE_SHADOW     — difference not significant; collect more shadow data
    """
    champ_m = compute_metrics(y_true, y_prob_champ)
    chal_m = compute_metrics(y_true, y_prob_chal)
    ci = bootstrap_tpr_diff(y_true, y_prob_champ, y_prob_chal, n_boot=n_boot)

    if lift_plot_path is not None:
        plot_lift_comparison(
            y_true,
            y_prob_champ,
            y_prob_chal,
            lift_plot_path,
            champ_name=champ_name,
            chal_name=chal_name,
        )

    if ci["ci_lo"] > 0:
        recommendation = "PROMOTE_CHALLENGER"
    elif ci["ci_hi"] < 0:
        recommendation = "KEEP_CHAMPION"
    else:
        recommendation = "CONTINUE_SHADOW"

    width = 65
    print(f"\n{'=' * width}")
    print("MODEL COMPARISON: CHAMPION vs CHALLENGER")
    print(f"{'=' * width}")
    print(f"{'Metric':<28} {champ_name:<18} {chal_name:<14} Better")
    print(f"{'-' * width}")
    for key, label in [
        ("auc", "AUC"),
        ("auprc", "AUPRC"),
        ("tpr_at_001_fpr", "TPR @ 0.1% FPR"),
        ("brier", "Brier (lower=better)"),
    ]:
        higher_is_better = key != "brier"
        chal_wins = chal_m[key] > champ_m[key] if higher_is_better else chal_m[key] < champ_m[key]
        winner = chal_name if chal_wins else champ_name
        print(f"{label:<28} {champ_m[key]:<18.4f} {chal_m[key]:<14.4f} {winner}")
    print(f"{'-' * width}")
    print(
        f"Bootstrap TPR diff ({chal_name}-{champ_name}): "
        f"mean={ci['mean_diff']:+.4f}  "
        f"95%CI=[{ci['ci_lo']:+.4f}, {ci['ci_hi']:+.4f}]  "
        f"n_boot={int(ci['n_boot'])}"
    )
    print(f"Recommendation: {recommendation}")
    print(f"{'=' * width}\n")

    return {
        f"{champ_name}_metrics": champ_m,
        f"{chal_name}_metrics": chal_m,
        "bootstrap_ci": ci,
        "recommendation": recommendation,
    }
