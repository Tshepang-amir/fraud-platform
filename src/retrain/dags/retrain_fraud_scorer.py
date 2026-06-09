"""Airflow DAG: retrain challenger and pause for human promotion approval.

Rule 7: production promotion is never automatic.  The DAG may train and evaluate a
challenger automatically, but it stops at a sensor until a human writes an explicit
approval variable in Airflow.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

DAG_ID = "retrain_fraud_scorer"
APPROVAL_VARIABLE_PREFIX = "fraud_retrain_approval"
MIN_TPR_CI_LOWER_BOUND = 0.0
MAX_BRIER_REGRESSION = 0.002
MAX_AUC_REGRESSION = 0.005


@dataclass(frozen=True)
class PromotionDecision:
    """Governance decision produced by the evaluation gate."""

    action: str
    reason: str
    approval_required: bool


def _metrics_pair(evaluation: dict[str, Any]) -> tuple[dict[str, float], dict[str, float]]:
    """Return champion/challenger metrics from either canonical or local labels."""
    champion = evaluation.get("champion_metrics") or evaluation.get("LightGBM_metrics")
    challenger = evaluation.get("challenger_metrics") or evaluation.get("CatBoost_metrics")
    if not isinstance(champion, dict) or not isinstance(challenger, dict):
        raise ValueError("evaluation must include champion_metrics and challenger_metrics")
    return champion, challenger


def evaluate_promotion_decision(evaluation: dict[str, Any]) -> PromotionDecision:
    """Apply the non-negotiable promotion gate.

    The challenger can only reach the human approval task when:
    - bootstrap CI lower bound for TPR@0.1%FPR difference is positive;
    - AUC has not materially regressed;
    - Brier score has not materially regressed.
    """
    champion, challenger = _metrics_pair(evaluation)
    ci = evaluation.get("bootstrap_ci")
    if not isinstance(ci, dict):
        raise ValueError("evaluation must include bootstrap_ci")

    ci_lo = float(ci.get("ci_lo", 0.0))
    champion_auc = float(champion["auc"])
    challenger_auc = float(challenger["auc"])
    champion_brier = float(champion["brier"])
    challenger_brier = float(challenger["brier"])

    if ci_lo <= MIN_TPR_CI_LOWER_BOUND:
        return PromotionDecision(
            action="REJECT",
            reason="challenger TPR lift is not statistically positive",
            approval_required=False,
        )
    if challenger_auc < champion_auc - MAX_AUC_REGRESSION:
        return PromotionDecision(
            action="REJECT",
            reason="challenger AUC regression exceeds policy tolerance",
            approval_required=False,
        )
    if challenger_brier > champion_brier + MAX_BRIER_REGRESSION:
        return PromotionDecision(
            action="REJECT",
            reason="challenger calibration regression exceeds policy tolerance",
            approval_required=False,
        )
    return PromotionDecision(
        action="REQUEST_APPROVAL",
        reason="challenger passed statistical and calibration gates",
        approval_required=True,
    )


def approval_variable_name(run_id: str) -> str:
    """Airflow Variable key a human must set to 'approved' for this run."""
    safe_run_id = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in run_id)
    return f"{APPROVAL_VARIABLE_PREFIX}_{safe_run_id}"


def build_airflow_dag() -> Any:
    """Build the real Airflow DAG when Airflow is installed."""
    try:
        from airflow import DAG
        from airflow.models import Variable
        from airflow.operators.empty import EmptyOperator
        from airflow.operators.python import BranchPythonOperator, PythonOperator
        from airflow.sensors.python import PythonSensor
    except ImportError:  # pragma: no cover - local dev often has no Airflow installed
        logger.info("Airflow not installed; %s DAG object not created", DAG_ID)
        return None

    default_args = {
        "owner": "fraud-platform",
        "depends_on_past": False,
        "retries": 1,
        "retry_delay": timedelta(minutes=5),
    }

    def prepare_training_data(**context: Any) -> dict[str, Any]:
        conf = context["dag_run"].conf or {}
        return {
            "gold_table": conf.get("gold_table", os.getenv("GOLD_TABLE", "gold.transactions")),
            "delta_version": conf.get("delta_version", "latest"),
            "triggering_features": conf.get("triggering_features", {}),
        }

    def train_challenger(**context: Any) -> dict[str, str]:
        conf = context["dag_run"].conf or {}
        run_id = conf.get("challenger_run_id", f"manual-airflow-{context['run_id']}")
        return {"challenger_run_id": run_id}

    def evaluate_challenger(**context: Any) -> dict[str, Any]:
        conf = context["dag_run"].conf or {}
        evaluation = conf.get("evaluation")
        if evaluation is None:
            evaluation = {
                "champion_metrics": {"auc": 0.9200, "brier": 0.0349},
                "challenger_metrics": {"auc": 0.9179, "brier": 0.0585},
                "bootstrap_ci": {"ci_lo": -0.0712, "ci_hi": -0.0067},
            }
        decision = evaluate_promotion_decision(evaluation)
        return {
            "evaluation": evaluation,
            "decision": decision.action,
            "reason": decision.reason,
            "approval_required": decision.approval_required,
        }

    def branch_on_evaluation(**context: Any) -> str:
        evaluation_result = context["ti"].xcom_pull(task_ids="evaluate_challenger")
        if evaluation_result["decision"] == "REQUEST_APPROVAL":
            return "request_human_approval"
        return "archive_challenger"

    def request_human_approval(**context: Any) -> dict[str, str]:
        training_result = context["ti"].xcom_pull(task_ids="train_challenger")
        run_id = training_result["challenger_run_id"]
        variable_name = approval_variable_name(run_id)
        logger.warning(
            "Human approval required. Set Airflow Variable %s=approved to promote %s.",
            variable_name,
            run_id,
        )
        return {"approval_variable": variable_name, "challenger_run_id": run_id}

    def approval_granted(**context: Any) -> bool:
        approval = context["ti"].xcom_pull(task_ids="request_human_approval")
        value = Variable.get(approval["approval_variable"], default_var="pending")
        return str(value).strip().lower() == "approved"

    def promote_to_production(**context: Any) -> dict[str, str]:
        approval = context["ti"].xcom_pull(task_ids="request_human_approval")
        run_id = approval["challenger_run_id"]
        logger.warning("Promoting challenger run %s after human approval", run_id)
        return {"promoted_run_id": run_id, "stage": "Production"}

    def archive_challenger(**context: Any) -> dict[str, str]:
        result = context["ti"].xcom_pull(task_ids="evaluate_challenger")
        return {"status": "archived", "reason": result["reason"]}

    with DAG(
        dag_id=DAG_ID,
        default_args=default_args,
        description="Retrain fraud model and pause for human production approval",
        schedule="@monthly",
        start_date=datetime(2026, 5, 1),
        catchup=False,
        tags=["fraud", "retrain", "governance"],
    ) as dag:
        prepare = PythonOperator(
            task_id="prepare_training_data",
            python_callable=prepare_training_data,
        )
        train = PythonOperator(task_id="train_challenger", python_callable=train_challenger)
        evaluate = PythonOperator(
            task_id="evaluate_challenger",
            python_callable=evaluate_challenger,
        )
        branch = BranchPythonOperator(
            task_id="branch_on_evaluation",
            python_callable=branch_on_evaluation,
        )
        approval_request = PythonOperator(
            task_id="request_human_approval",
            python_callable=request_human_approval,
        )
        wait_for_approval = PythonSensor(
            task_id="wait_for_human_approval",
            python_callable=approval_granted,
            mode="reschedule",
            poke_interval=300,
            timeout=7 * 24 * 60 * 60,
        )
        promote = PythonOperator(
            task_id="promote_to_production",
            python_callable=promote_to_production,
        )
        archive = PythonOperator(task_id="archive_challenger", python_callable=archive_challenger)
        done = EmptyOperator(task_id="done", trigger_rule="none_failed_min_one_success")

        prepare >> train >> evaluate >> branch
        branch >> approval_request >> wait_for_approval >> promote >> done
        branch >> archive >> done

    return dag


dag = build_airflow_dag()
