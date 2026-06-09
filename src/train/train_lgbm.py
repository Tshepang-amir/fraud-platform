"""Train LightGBM champion model with full MLflow lineage logging.

Day 3 deliverable. Consumes temporal_split.py and feature_engineering.py.
Logs all required artefacts to local MLflow (mlruns/).

On Day 7 (Databricks), swap the tracking URI via:
  mlflow.set_tracking_uri(os.environ["MLFLOW_TRACKING_URI"])
No other code changes needed.
"""

from __future__ import annotations

import logging
import sys

import matplotlib.pyplot as plt
import mlflow
import mlflow.lightgbm
import numpy as np
import pandas as pd
from lightgbm import LGBMClassifier, early_stopping, log_evaluation
from mlflow.models import infer_signature
from sklearn.calibration import calibration_curve
from sklearn.metrics import brier_score_loss, roc_auc_score, roc_curve

from src.train.feature_engineering import compute_peer_stats, engineer_features
from src.train.temporal_split import log_split_stats, temporal_split

logger = logging.getLogger(__name__)

# ── Column policy ─────────────────────────────────────────────────────────────
TARGET_COL = "isFraud"

# TransactionID: excluded per Day 2 leakage scan (|corr|=0.014, non-predictive index).
# TransactionDT: temporal split key; raw seconds-offset encodes dataset position.
#   Hour-of-day / day-of-week derivatives are informative but deferred to
#   feature_set v2. Include in ENGINEERED_FEATURE_COLS when ready.
EXCLUDE_COLS: frozenset[str] = frozenset({"TransactionID", "isFraud", "TransactionDT"})

# ── MLflow lineage tags (Rule 4) ──────────────────────────────────────────────
MLFLOW_TAGS: dict[str, str] = {
    "developer": "tsapang_mashego",
    "feature_set": "v1_rolling_stats",
    "split_strategy": "temporal",
    "dataset_version": "0",  # 0 = local CSV; update to Delta version on Databricks
}

# ── Hyperparameters ───────────────────────────────────────────────────────────
LGBM_PARAMS: dict[str, object] = {
    "n_estimators": 2000,  # ceiling; early stopping finds the true best
    "learning_rate": 0.05,
    "num_leaves": 127,
    "max_depth": -1,
    "min_child_samples": 50,  # ≥50 prevents minority-class leaf overfitting (Day 2)
    "scale_pos_weight": 27,  # 1:27 class imbalance confirmed in EDA (Day 2 gate)
    "feature_fraction": 0.7,
    "bagging_fraction": 0.8,
    "bagging_freq": 5,
    "lambda_l1": 0.1,
    "lambda_l2": 0.1,
    "random_state": 42,
    "n_jobs": -1,
    "verbose": -1,
}

# ── Target metrics (from brief) ───────────────────────────────────────────────
TARGET_AUC = 0.88
TARGET_TPR_AT_001_FPR = 0.60
TARGET_BRIER = 0.04


def _tpr_at_fixed_fpr(
    y_true: np.ndarray,
    y_score: np.ndarray,
    fpr_target: float = 0.001,
) -> float:
    """Maximum TPR reachable without exceeding fpr_target.

    Fraud teams set a hard false-positive budget. We therefore choose the best
    recall at or below that FPR, rather than the ROC point merely closest to it.
    """
    fprs, tprs, _ = roc_curve(y_true, y_score)
    valid = fprs <= fpr_target
    if not np.any(valid):
        return 0.0
    return float(np.max(tprs[valid]))


def _encode_categoricals(
    train: pd.DataFrame,
    val: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    """Convert object columns to category dtype; align val categories to train.

    Val values not seen in train become NaN, handled by LightGBM as missing.
    """
    cat_cols = train.select_dtypes(include="object").columns.tolist()
    train = train.copy()
    val = val.copy()
    for col in cat_cols:
        train[col] = train[col].astype("category")
        val[col] = pd.Categorical(val[col], categories=train[col].cat.categories)
    return train, val, cat_cols


def _plot_calibration(
    y_true: np.ndarray,
    y_prob: np.ndarray,
    out_path: str,
) -> None:
    fraction_pos, mean_pred = calibration_curve(y_true, y_prob, n_bins=20, strategy="quantile")
    fig, ax = plt.subplots(figsize=(6, 5))
    ax.plot(mean_pred, fraction_pos, "s-", label="LightGBM", color="steelblue")
    ax.plot([0, 1], [0, 1], "k--", label="Perfect calibration")
    ax.set_xlabel("Mean predicted probability")
    ax.set_ylabel("Fraction of positives")
    ax.set_title("Calibration Curve — LightGBM Champion")
    ax.legend()
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)


def _plot_feature_importance(
    model: LGBMClassifier,
    feature_names: list[str],
    out_path: str,
    top_n: int = 40,
) -> None:
    importances = model.feature_importances_
    indices = np.argsort(importances)[::-1][:top_n]
    names = [feature_names[i] for i in indices]
    vals = importances[indices]

    fig, ax = plt.subplots(figsize=(10, 8))
    ax.barh(range(top_n), vals[::-1], color="steelblue")
    ax.set_yticks(range(top_n))
    ax.set_yticklabels(names[::-1], fontsize=8)
    ax.set_xlabel("Importance (gain)")
    ax.set_title(f"Top {top_n} Features — LightGBM Champion")
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)


def train_lgbm_champion(
    transaction_path: str = "data/raw/train_transaction.csv",
    identity_path: str = "data/raw/train_identity.csv",
    *,
    experiment_name: str = "fraud-scorer",
    run_name: str = "lgbm-champion-v1",
    register_model: bool = False,
) -> str:
    """Train LightGBM champion model and log all artefacts to MLflow.

    Args:
        transaction_path: Path to train_transaction.csv.
        identity_path: Path to train_identity.csv.
        experiment_name: MLflow experiment name.
        run_name: Human-readable name for this run.
        register_model: Register model in MLflow Model Registry when True.

    Returns:
        MLflow run_id of the completed training run.
    """
    # ── Load and merge ────────────────────────────────────────────────────────
    logger.info("Loading %s", transaction_path)
    txn = pd.read_csv(transaction_path)
    logger.info("Loading %s", identity_path)
    identity = pd.read_csv(identity_path)
    df = txn.merge(identity, on="TransactionID", how="left")
    logger.info("Merged dataset: %d rows x %d columns", *df.shape)

    # ── Temporal split (Rule 1: NO random splits) ─────────────────────────────
    split = temporal_split(df)
    log_split_stats(split)

    # ── Feature engineering (peer_stats from train only — prevents leakage) ───
    peer_stats = compute_peer_stats(split.train)
    logger.info("Engineering features for train split...")
    train_fe = engineer_features(split.train, peer_stats=peer_stats)
    logger.info("Engineering features for val split...")
    val_fe = engineer_features(split.val, peer_stats=peer_stats)

    # ── Categorical encoding ───────────────────────────────────────────────────
    train_fe, val_fe, cat_cols = _encode_categoricals(train_fe, val_fe)
    logger.info("Categorical columns (%d): %s", len(cat_cols), cat_cols)

    # ── Build feature matrix ───────────────────────────────────────────────────
    feature_cols = [c for c in train_fe.columns if c not in EXCLUDE_COLS]
    x_train = train_fe[feature_cols]
    y_train = train_fe[TARGET_COL].astype(int)
    x_val = val_fe[feature_cols]
    y_val = val_fe[TARGET_COL].astype(int)
    logger.info(
        "Feature matrix — train: %s, val: %s, features: %d",
        x_train.shape,
        x_val.shape,
        len(feature_cols),
    )

    # ── MLflow experiment ──────────────────────────────────────────────────────
    mlflow.set_experiment(experiment_name)

    with mlflow.start_run(run_name=run_name) as run:
        # Lineage tags (Rule 4)
        mlflow.set_tags(MLFLOW_TAGS)

        mlflow.log_params(LGBM_PARAMS)
        mlflow.log_param("n_train", len(x_train))
        mlflow.log_param("n_val", len(x_val))
        mlflow.log_param("n_features", len(feature_cols))
        mlflow.log_param("fraud_rate_train", round(float(y_train.mean()), 4))
        mlflow.log_param("n_categorical_cols", len(cat_cols))

        # ── Train ──────────────────────────────────────────────────────────────
        logger.info(
            "Fitting LightGBM (up to %d rounds, early_stopping=100)...",
            LGBM_PARAMS["n_estimators"],
        )
        model = LGBMClassifier(**LGBM_PARAMS)  # type: ignore[arg-type]
        model.fit(
            x_train,
            y_train,
            eval_set=[(x_val, y_val)],
            eval_metric="auc",
            callbacks=[
                # first_metric_only=True: track only AUC, not binary_logloss.
                # Without this, logloss overfits at round 1 while AUC still improves,
                # causing early_stopping to fire prematurely (best_iteration_=1).
                early_stopping(stopping_rounds=100, first_metric_only=True, verbose=False),
                log_evaluation(period=100),
            ],
        )

        best_iter = int(model.best_iteration_)
        mlflow.log_param("n_estimators_best", best_iter)
        logger.info("Training complete. Best iteration: %d", best_iter)

        # ── Evaluate ───────────────────────────────────────────────────────────
        y_prob: np.ndarray = model.predict_proba(x_val)[:, 1]

        val_auc = float(roc_auc_score(y_val, y_prob))
        val_tpr = _tpr_at_fixed_fpr(y_val.values, y_prob)
        val_brier = float(brier_score_loss(y_val, y_prob))

        mlflow.log_metric("val_auc", val_auc)
        mlflow.log_metric("val_tpr_at_001_fpr", val_tpr)
        mlflow.log_metric("val_brier", val_brier)

        logger.info(
            "val_auc=%.4f  val_tpr@0.1%%FPR=%.4f  val_brier=%.4f",
            val_auc,
            val_tpr,
            val_brier,
        )

        # ── Calibration curve ──────────────────────────────────────────────────
        calib_path = "calibration_curve.png"
        _plot_calibration(y_val.values, y_prob, calib_path)
        mlflow.log_artifact(calib_path)

        # ── Feature importance ─────────────────────────────────────────────────
        fi_path = "feature_importance.png"
        _plot_feature_importance(model, feature_cols, fi_path)
        mlflow.log_artifact(fi_path)

        # ── Model signature + input_example ────────────────────────────────────
        # Keep category dtype so LightGBM's predict path sees the same types as
        # during training. Converting to object causes "categorical_feature do not
        # match" in LightGBM's _data_from_pandas validation.
        input_example = x_val.head(5)
        signature = infer_signature(input_example, y_prob[:5])

        registered_name = "fraud-scorer-champion" if register_model else None
        mlflow.lightgbm.log_model(
            lgb_model=model,
            name="lgbm_champion",
            signature=signature,
            input_example=input_example,
            registered_model_name=registered_name,
        )

        run_id: str = run.info.run_id

    # ── Console report ─────────────────────────────────────────────────────────
    targets_met = (
        val_auc >= TARGET_AUC and val_tpr >= TARGET_TPR_AT_001_FPR and val_brier <= TARGET_BRIER
    )
    print(f"\n{'=' * 60}")
    print("LGBM CHAMPION TRAINING COMPLETE")
    print(f"{'=' * 60}")
    print(f"MLflow run ID  : {run_id}")
    print(f"Best iteration : {best_iter}")
    print(f"val_auc        : {val_auc:.4f}  (target > {TARGET_AUC})")
    print(f"val_tpr@0.1FPR : {val_tpr:.4f}  (target > {TARGET_TPR_AT_001_FPR})")
    print(f"val_brier      : {val_brier:.4f}  (target < {TARGET_BRIER})")
    print(f"All targets met: {'YES' if targets_met else 'NO -- review metrics above'}")
    print(f"{'=' * 60}\n")

    return run_id


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    _run_id = train_lgbm_champion()
    sys.exit(0)
