"""Trigger the Airflow retraining DAG when PSI crosses the Rule 6 threshold."""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
from typing import Any

import httpx

from src.monitor.psi import PSI_RETRAIN_THRESHOLD, psi_status
from src.retrain.dags.retrain_fraud_scorer import DAG_ID

logger = logging.getLogger(__name__)

DEFAULT_PSI_PATH = Path("reports/psi_scores.json")


def load_psi_scores(path: Path = DEFAULT_PSI_PATH) -> dict[str, float]:
    """Load feature -> PSI score mapping from the drift report output."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"PSI report must be an object: {path}")
    return {str(feature): float(value) for feature, value in data.items()}


def features_requiring_retrain(psi_scores: dict[str, float]) -> dict[str, float]:
    """Return features whose PSI status is retrain."""
    return {
        feature: value for feature, value in psi_scores.items() if psi_status(value) == "retrain"
    }


def build_dag_run_conf(
    triggering_features: dict[str, float],
    *,
    psi_report_path: Path,
    source: str = "psi_monitor",
) -> dict[str, Any]:
    """Build the Airflow DAG run conf payload."""
    return {
        "source": source,
        "psi_report_path": str(psi_report_path),
        "psi_retrain_threshold": PSI_RETRAIN_THRESHOLD,
        "triggering_features": triggering_features,
    }


def trigger_airflow_dag(
    *,
    airflow_base_url: str,
    dag_id: str,
    conf: dict[str, Any],
    bearer_token: str | None = None,
    username: str | None = None,
    password: str | None = None,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    """Trigger an Airflow DAG run through the stable REST API."""
    url = f"{airflow_base_url.rstrip('/')}/api/v1/dags/{dag_id}/dagRuns"
    headers = {"Content-Type": "application/json"}
    auth: tuple[str, str] | None = None

    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"
    elif username and password:
        auth = (username, password)

    response = httpx.post(
        url,
        headers=headers,
        auth=auth,
        json={"conf": conf},
        timeout=timeout_seconds,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Airflow API response was not a JSON object")
    return payload


def check_and_trigger_retrain(
    *,
    psi_report_path: Path = DEFAULT_PSI_PATH,
    airflow_base_url: str | None = None,
    dag_id: str = DAG_ID,
    bearer_token: str | None = None,
    username: str | None = None,
    password: str | None = None,
) -> dict[str, Any]:
    """Check PSI scores and trigger Airflow only when Rule 6 requires it."""
    psi_scores = load_psi_scores(psi_report_path)
    triggering_features = features_requiring_retrain(psi_scores)
    if not triggering_features:
        return {
            "triggered": False,
            "reason": "no feature PSI exceeded retrain threshold",
            "triggering_features": {},
        }

    conf = build_dag_run_conf(triggering_features, psi_report_path=psi_report_path)
    if airflow_base_url is None:
        return {
            "triggered": False,
            "reason": "airflow_base_url not configured",
            "triggering_features": triggering_features,
            "dag_run_conf": conf,
        }

    dag_run = trigger_airflow_dag(
        airflow_base_url=airflow_base_url,
        dag_id=dag_id,
        conf=conf,
        bearer_token=bearer_token,
        username=username,
        password=password,
    )
    return {
        "triggered": True,
        "reason": "retrain threshold exceeded",
        "triggering_features": triggering_features,
        "dag_run": dag_run,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--psi-report", type=Path, default=DEFAULT_PSI_PATH)
    parser.add_argument("--airflow-base-url", default=os.getenv("AIRFLOW_BASE_URL"))
    parser.add_argument("--airflow-token", default=os.getenv("AIRFLOW_BEARER_TOKEN"))
    parser.add_argument("--airflow-username", default=os.getenv("AIRFLOW_USERNAME"))
    parser.add_argument("--airflow-password", default=os.getenv("AIRFLOW_PASSWORD"))
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    args = _parse_args()
    result = check_and_trigger_retrain(
        psi_report_path=args.psi_report,
        airflow_base_url=args.airflow_base_url,
        bearer_token=args.airflow_token,
        username=args.airflow_username,
        password=args.airflow_password,
    )
    logger.info("Retrain trigger result: %s", json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
