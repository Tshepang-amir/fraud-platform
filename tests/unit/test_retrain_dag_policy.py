"""Tests for the retraining DAG promotion gate policy."""

from src.retrain.dags.retrain_fraud_scorer import (
    approval_variable_name,
    evaluate_promotion_decision,
)


def _evaluation(
    *,
    ci_lo: float,
    champion_auc: float = 0.9200,
    challenger_auc: float = 0.9210,
    champion_brier: float = 0.0349,
    challenger_brier: float = 0.0340,
) -> dict[str, object]:
    return {
        "champion_metrics": {"auc": champion_auc, "brier": champion_brier},
        "challenger_metrics": {"auc": challenger_auc, "brier": challenger_brier},
        "bootstrap_ci": {"ci_lo": ci_lo, "ci_hi": ci_lo + 0.01},
    }


def test_promote_requires_human_approval_after_metric_gates() -> None:
    decision = evaluate_promotion_decision(_evaluation(ci_lo=0.001))

    assert decision.action == "REQUEST_APPROVAL"
    assert decision.approval_required is True


def test_reject_when_tpr_lift_not_statistically_positive() -> None:
    decision = evaluate_promotion_decision(_evaluation(ci_lo=0.0))

    assert decision.action == "REJECT"
    assert decision.approval_required is False
    assert "TPR" in decision.reason


def test_reject_when_calibration_regresses() -> None:
    decision = evaluate_promotion_decision(_evaluation(ci_lo=0.001, challenger_brier=0.0400))

    assert decision.action == "REJECT"
    assert "calibration" in decision.reason


def test_approval_variable_name_is_safe_for_airflow() -> None:
    assert approval_variable_name("run/id:123") == "fraud_retrain_approval_run_id_123"

