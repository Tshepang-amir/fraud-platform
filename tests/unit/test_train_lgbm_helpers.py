"""Fast unit tests for LightGBM training helpers."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.train import train_lgbm


@pytest.mark.unit
def test_tpr_at_fixed_fpr_returns_expected_roc_point() -> None:
    y_true = np.array([0, 0, 0, 1, 1, 1])
    y_score = np.array([0.01, 0.02, 0.03, 0.70, 0.80, 0.90])

    result = train_lgbm._tpr_at_fixed_fpr(y_true, y_score, fpr_target=0.0)

    assert result == pytest.approx(1.0)


@pytest.mark.unit
def test_encode_categoricals_aligns_validation_to_training_categories() -> None:
    train = pd.DataFrame(
        {
            "ProductCD": ["W", "H", "C"],
            "card4": ["visa", "mastercard", "visa"],
            "amount": [10.0, 20.0, 30.0],
        }
    )
    val = pd.DataFrame(
        {
            "ProductCD": ["W", "Z"],
            "card4": ["visa", "amex"],
            "amount": [40.0, 50.0],
        }
    )

    train_encoded, val_encoded, cat_cols = train_lgbm._encode_categoricals(train, val)

    assert cat_cols == ["ProductCD", "card4"]
    assert str(train_encoded["ProductCD"].dtype) == "category"
    assert str(val_encoded["ProductCD"].dtype) == "category"
    assert val_encoded["ProductCD"].cat.categories.tolist() == ["C", "H", "W"]
    assert pd.isna(val_encoded.loc[1, "ProductCD"])
    assert pd.isna(val_encoded.loc[1, "card4"])


@pytest.mark.unit
def test_lineage_tags_satisfy_rule_four() -> None:
    assert train_lgbm.MLFLOW_TAGS["developer"] == "tsapang_mashego"
    assert train_lgbm.MLFLOW_TAGS["feature_set"] == "v1_rolling_stats"
    assert train_lgbm.MLFLOW_TAGS["split_strategy"] == "temporal"
    assert train_lgbm.MLFLOW_TAGS["dataset_version"].isdigit()


@pytest.mark.unit
def test_training_excludes_split_keys_and_target() -> None:
    assert frozenset({"TransactionID", "isFraud", "TransactionDT"}) == train_lgbm.EXCLUDE_COLS


@pytest.mark.unit
def test_target_metric_constants_match_day_three_gate() -> None:
    assert pytest.approx(0.88) == train_lgbm.TARGET_AUC
    assert pytest.approx(0.60) == train_lgbm.TARGET_TPR_AT_001_FPR
    assert pytest.approx(0.04) == train_lgbm.TARGET_BRIER
