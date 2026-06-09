"""Tests for PSI-triggered Airflow DAG runs."""

import json
from pathlib import Path
from uuid import uuid4

from src.monitor.trigger_retrain import (
    build_dag_run_conf,
    check_and_trigger_retrain,
    features_requiring_retrain,
    load_psi_scores,
)

_TEST_TMP = Path("data/test_tmp/trigger_retrain")


def _write_psi(scores: dict[str, object]) -> Path:
    _TEST_TMP.mkdir(parents=True, exist_ok=True)
    path = _TEST_TMP / f"psi_scores_{uuid4().hex}.json"
    path.write_text(json.dumps(scores), encoding="utf-8")
    return path


def test_features_requiring_retrain_filters_rule_6_threshold() -> None:
    scores = {"stable": 0.01, "warn": 0.15, "shifted": 0.21}

    assert features_requiring_retrain(scores) == {"shifted": 0.21}


def test_load_psi_scores_normalises_values_to_float() -> None:
    path = _write_psi({"feature_a": "0.25"})

    assert load_psi_scores(path) == {"feature_a": 0.25}


def test_check_and_trigger_returns_noop_without_retrain_features() -> None:
    path = _write_psi({"feature_a": 0.05})

    result = check_and_trigger_retrain(psi_report_path=path)

    assert result["triggered"] is False
    assert result["reason"] == "no feature PSI exceeded retrain threshold"


def test_check_and_trigger_builds_conf_when_airflow_is_not_configured() -> None:
    path = _write_psi({"feature_a": 0.25})

    result = check_and_trigger_retrain(psi_report_path=path)

    assert result["triggered"] is False
    assert result["reason"] == "airflow_base_url not configured"
    assert result["triggering_features"] == {"feature_a": 0.25}
    assert result["dag_run_conf"]["source"] == "psi_monitor"


def test_build_dag_run_conf_records_threshold_and_report_path() -> None:
    path = _TEST_TMP / "psi_scores.json"

    conf = build_dag_run_conf({"feature_a": 0.25}, psi_report_path=path)

    assert conf["psi_retrain_threshold"] == 0.20
    assert conf["psi_report_path"] == str(path)
