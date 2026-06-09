"""Challenger model training: CatBoost + MLflow logging. (Rule 4)

Same logging pattern as champion — all params, metrics, artifacts, and tags.
The challenger never makes live decisions (Rule 5: shadow mode only).

CatBoost handles categorical features natively via Pool(cat_features=...).
NaN values in categorical columns are replaced with the sentinel "__NA__"
so CatBoost sees a known category rather than a missing type.

On Day 7 (Databricks), swap the tracking URI via:
  mlflow.set_tracking_uri(os.environ["MLFLOW_TRACKING_URI"])
No other code changes needed.
"""

from __future__ import annotations

import logging
import sys

import matplotlib.pyplot as plt
import mlflow
import mlflow.catboost
import numpy as np
import pandas as pd
from catboost import CatBoostClassifier, Pool
from mlflow.models import infer_signature
from sklearn.calibration import calibration_curve
from sklearn.metrics import brier_score_loss, roc_auc_score, roc_curve

from src.train.evaluate import compare_models
from src.train.feature_engineering import compute_peer_stats, engineer_features
from src.train.temporal_split import log_split_stats, temporal_split

logger = logging.getLogger(__name__)

# ── Column policy ─────────────────────────────────────────────────────────────
TARGET_COL = "isFraud"
EXCLUDE_COLS: frozenset[str] = frozenset({"TransactionID", "isFraud", "TransactionDT"})

# ── MLflow lineage tags (Rule 4) ──────────────────────────────────────────────
MLFLOW_TAGS: dict[str, str] = {
    "developer": "tsapang_mashego",
    "feature_set": "v1_rolling_stats",
    "split_strategy": "temporal",
    "dataset_version": "0",
    "model_role": "challenger",
}

# ── Hyperparameters ───────────────────────────────────────────────────────────
CATBOOST_PARAMS: dict[str, object] = {
    "iterations": 2000,
    "learning_rate": 0.05,
    "depth": 8,
    "l2_leaf_reg": 3.0,
    "min_data_in_leaf": 50,
    "class_weights": [1, 27],  # mirrors scale_pos_weight=27 in LightGBM champion
    "eval_metric": "AUC",
    "od_type": "Iter",  # early stopping type
    "od_wait": 100,  # equivalent to stopping_rounds=100
    "random_seed": 42,
    "task_type": "CPU",
    "verbose": 0,
}

# ── Target metrics ────────────────────────────────────────────────────────────
TARGET_AUC = 0.88
TARGET_TPR_AT_001_FPR = 0.60
TARGET_BRIER = 0.04

# ── Champion run (Day 3) ──────────────────────────────────────────────────────
CHAMPION_RUN_ID = "9c599d91d7c546df82ad252837990c29"
CHAMPION_MODEL_URI = f"runs:/{CHAMPION_RUN_ID}/lgbm_champion"


def _tpr_at_fixed_fpr(
    y_true: np.ndarray,
    y_score: np.ndarray,
    fpr_target: float = 0.001,
) -> float:
    fprs, tprs, _ = roc_curve(y_true, y_score)
    valid = fprs <= fpr_target
    if not np.any(valid):
        return 0.0
    return float(np.max(tprs[valid]))


def _prepare_catboost_data(
    df: pd.DataFrame,
) -> tuple[pd.DataFrame, list[str]]:
    """Convert object columns for CatBoost: NaN → '__NA__' string sentinel.

    CatBoost requires categorical values to be strings with no NaN.
    Returns a copy of df with object columns cleaned, plus the list of cat column names.
    """
    df = df.copy()
    cat_cols = df.select_dtypes(include="object").columns.tolist()
    for col in cat_cols:
        df[col] = df[col].fillna("__NA__").astype(str)
    return df, cat_cols


def _plot_calibration(
    y_true: np.ndarray,
    y_prob: np.ndarray,
    out_path: str,
) -> None:
    fraction_pos, mean_pred = calibration_curve(y_true, y_prob, n_bins=20, strategy="quantile")
    fig, ax = plt.subplots(figsize=(6, 5))
    ax.plot(mean_pred, fraction_pos, "s-", label="CatBoost", color="darkorange")
    ax.plot([0, 1], [0, 1], "k--", label="Perfect calibration")
    ax.set_xlabel("Mean predicted probability")
    ax.set_ylabel("Fraction of positives")
    ax.set_title("Calibration Curve — CatBoost Challenger")
    ax.legend()
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)


def _plot_feature_importance(
    model: CatBoostClassifier,
    out_path: str,
    top_n: int = 40,
) -> None:
    importances = model.get_feature_importance()
    names = model.feature_names_
    indices = np.argsort(importances)[::-1][:top_n]
    top_names = [names[i] for i in indices]
    top_vals = importances[indices]

    fig, ax = plt.subplots(figsize=(10, 8))
    ax.barh(range(top_n), top_vals[::-1], color="darkorange")
    ax.set_yticks(range(top_n))
    ax.set_yticklabels(top_names[::-1], fontsize=8)
    ax.set_xlabel("Importance (PredictionValuesChange)")
    ax.set_title(f"Top {top_n} Features — CatBoost Challenger")
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)


def train_catboost_challenger(
    transaction_path: str = "data/raw/train_transaction.csv",
    identity_path: str = "data/raw/train_identity.csv",
    *,
    experiment_name: str = "fraud-scorer",
    run_name: str = "catboost-challenger-v1",
    champion_run_id: str = CHAMPION_RUN_ID,
    register_model: bool = False,
    lift_plot_path: str = "lift_chart_champ_vs_chal.png",
) -> str:
    """Train CatBoost challenger, compare with LightGBM champion, log to MLflow.

    Args:
        transaction_path: Path to train_transaction.csv.
        identity_path: Path to train_identity.csv.
        experiment_name: MLflow experiment name (same as champion).
        run_name: Human-readable name for this run.
        champion_run_id: MLflow run ID of the LightGBM champion.
        register_model: Register model in MLflow Model Registry when True.
        lift_plot_path: Path to save the lift chart comparison PNG.

    Returns:
        MLflow run_id of the completed CatBoost training run.
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

    # ── Feature engineering (peer_stats from train only) ─────────────────────
    peer_stats = compute_peer_stats(split.train)
    logger.info("Engineering features for train split...")
    train_fe = engineer_features(split.train, peer_stats=peer_stats)
    logger.info("Engineering features for val split...")
    val_fe = engineer_features(split.val, peer_stats=peer_stats)

    # ── CatBoost data preparation ─────────────────────────────────────────────
    train_cb, cat_cols = _prepare_catboost_data(train_fe)
    val_cb, _ = _prepare_catboost_data(val_fe)

    feature_cols = [c for c in train_cb.columns if c not in EXCLUDE_COLS]
    x_train = train_cb[feature_cols]
    y_train = train_cb[TARGET_COL].astype(int)
    x_val = val_cb[feature_cols]
    y_val = val_cb[TARGET_COL].astype(int)

    # CatBoost index positions of categorical columns (required by Pool)
    cat_indices = [feature_cols.index(c) for c in cat_cols if c in feature_cols]
    logger.info(
        "Feature matrix — train: %s, val: %s, features: %d, cat_features: %d",
        x_train.shape,
        x_val.shape,
        len(feature_cols),
        len(cat_indices),
    )

    train_pool = Pool(data=x_train, label=y_train, cat_features=cat_indices)
    val_pool = Pool(data=x_val, label=y_val, cat_features=cat_indices)

    # ── MLflow experiment ──────────────────────────────────────────────────────
    mlflow.set_experiment(experiment_name)

    with mlflow.start_run(run_name=run_name) as run:
        mlflow.set_tags(MLFLOW_TAGS)

        # Log flat params (CatBoost class_weights is a list — stringify it)
        loggable_params = {
            k: str(v) if isinstance(v, list) else v for k, v in CATBOOST_PARAMS.items()
        }
        mlflow.log_params(loggable_params)
        mlflow.log_param("n_train", len(x_train))
        mlflow.log_param("n_val", len(x_val))
        mlflow.log_param("n_features", len(feature_cols))
        mlflow.log_param("fraud_rate_train", round(float(y_train.mean()), 4))
        mlflow.log_param("n_categorical_cols", len(cat_indices))

        # ── Train ──────────────────────────────────────────────────────────────
        logger.info(
            "Fitting CatBoost (up to %d iterations, early stopping od_wait=%d)...",
            CATBOOST_PARAMS["iterations"],
            CATBOOST_PARAMS["od_wait"],
        )
        model = CatBoostClassifier(**CATBOOST_PARAMS)
        model.fit(train_pool, eval_set=val_pool, use_best_model=True)

        best_iter = int(model.get_best_iteration() or 0)
        mlflow.log_param("n_iterations_best", best_iter)
        logger.info("Training complete. Best iteration: %d", best_iter)

        # ── Evaluate ───────────────────────────────────────────────────────────
        y_prob_chal: np.ndarray = model.predict_proba(val_pool)[:, 1]

        val_auc = float(roc_auc_score(y_val, y_prob_chal))
        val_tpr = _tpr_at_fixed_fpr(y_val.values, y_prob_chal)
        val_brier = float(brier_score_loss(y_val, y_prob_chal))

        mlflow.log_metric("val_auc", val_auc)
        mlflow.log_metric("val_tpr_at_001_fpr", val_tpr)
        mlflow.log_metric("val_brier", val_brier)

        logger.info(
            "val_auc=%.4f  val_tpr@0.1%%FPR=%.4f  val_brier=%.4f",
            val_auc,
            val_tpr,
            val_brier,
        )

        # ── Load champion and run comparison ───────────────────────────────────
        logger.info("Loading LightGBM champion from run %s ...", champion_run_id)
        try:
            import mlflow.lightgbm as mlflow_lgbm

            champ_model = mlflow_lgbm.load_model(f"runs:/{champion_run_id}/lgbm_champion")  # type: ignore[no-untyped-call]

            # Build champion-format val: category dtype, categories from champion training
            # We need train_fe to derive categories — already computed above
            train_lgbm = train_fe.copy()
            val_lgbm = val_fe.copy()
            obj_cols = train_lgbm.select_dtypes(include="object").columns.tolist()
            for col in obj_cols:
                train_lgbm[col] = train_lgbm[col].astype("category")
                val_lgbm[col] = pd.Categorical(
                    val_lgbm[col], categories=train_lgbm[col].cat.categories
                )

            # Champion must see the same feature columns it was trained on
            champ_feature_cols = [c for c in train_lgbm.columns if c not in EXCLUDE_COLS]
            x_val_champ = val_lgbm[champ_feature_cols]
            y_prob_champ: np.ndarray = champ_model.predict_proba(x_val_champ)[:, 1]

            comparison = compare_models(
                y_val.values,
                y_prob_champ,
                y_prob_chal,
                champ_name="LightGBM",
                chal_name="CatBoost",
                lift_plot_path=lift_plot_path,
                n_boot=1000,
            )

            mlflow.log_metric("champ_val_auc", comparison["LightGBM_metrics"]["auc"])
            mlflow.log_metric(
                "champ_val_tpr_at_001_fpr",
                comparison["LightGBM_metrics"]["tpr_at_001_fpr"],
            )
            mlflow.log_metric("bootstrap_tpr_diff_mean", comparison["bootstrap_ci"]["mean_diff"])
            mlflow.log_metric("bootstrap_tpr_diff_ci_lo", comparison["bootstrap_ci"]["ci_lo"])
            mlflow.log_metric("bootstrap_tpr_diff_ci_hi", comparison["bootstrap_ci"]["ci_hi"])
            mlflow.log_param("recommendation", comparison["recommendation"])

            mlflow.log_artifact(lift_plot_path)

        except Exception:
            logger.exception("Champion comparison failed — logging CatBoost metrics only")
            comparison = {"recommendation": "COMPARISON_FAILED"}

        # ── Calibration curve ──────────────────────────────────────────────────
        calib_path = "catboost_calibration_curve.png"
        _plot_calibration(y_val.values, y_prob_chal, calib_path)
        mlflow.log_artifact(calib_path)

        # ── Feature importance ─────────────────────────────────────────────────
        fi_path = "catboost_feature_importance.png"
        _plot_feature_importance(model, fi_path)
        mlflow.log_artifact(fi_path)

        # ── Model signature + input_example ────────────────────────────────────
        input_example = x_val.head(5)
        signature = infer_signature(input_example, y_prob_chal[:5])

        registered_name = "fraud-scorer-challenger" if register_model else None
        mlflow.catboost.log_model(
            cb_model=model,
            name="catboost_challenger",
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
    print("CATBOOST CHALLENGER TRAINING COMPLETE")
    print(f"{'=' * 60}")
    print(f"MLflow run ID  : {run_id}")
    print(f"Best iteration : {best_iter}")
    print(f"val_auc        : {val_auc:.4f}  (target > {TARGET_AUC})")
    print(f"val_tpr@0.1FPR : {val_tpr:.4f}  (target > {TARGET_TPR_AT_001_FPR})")
    print(f"val_brier      : {val_brier:.4f}  (target < {TARGET_BRIER})")
    print(f"All targets met: {'YES' if targets_met else 'NO -- review metrics above'}")
    print(f"Recommendation : {comparison.get('recommendation', 'N/A')}")
    print(f"{'=' * 60}\n")

    return run_id


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    _run_id = train_catboost_challenger()
    sys.exit(0)
