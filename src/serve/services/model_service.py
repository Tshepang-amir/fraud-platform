"""Champion + challenger model loading and scoring.

Rule 5: The challenger NEVER makes live decisions.
It scores every request in BackgroundTasks but its output is never
returned to the caller and never used in any downstream system.
It writes only to the shadow_decisions table.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import mlflow
import mlflow.catboost
import mlflow.lightgbm
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Columns excluded from the feature vector (used for labels / split key only)
_EXCLUDE_COLS: frozenset[str] = frozenset(
    {"TransactionID", "isFraud", "TransactionDT", "transaction_id"}
)

_KNOWN_CATEGORICAL_COLS: frozenset[str] = frozenset(
    {
        "ProductCD",
        "card4",
        "card6",
        "P_emaildomain",
        "R_emaildomain",
        "M1",
        "M2",
        "M3",
        "M4",
        "M5",
        "M6",
        "M7",
        "M8",
        "M9",
        "id_12",
        "id_15",
        "id_16",
        "id_23",
        "id_27",
        "id_28",
        "id_29",
        "id_30",
        "id_31",
        "id_33",
        "id_34",
        "id_35",
        "id_36",
        "id_37",
        "id_38",
        "DeviceType",
        "DeviceInfo",
    }
)


def _tracking_uri_to_path() -> Path | None:
    """Return local filesystem path for file-backed MLflow tracking URIs."""
    tracking_uri = mlflow.get_tracking_uri()
    parsed = urlparse(tracking_uri)

    if parsed.scheme == "":
        return Path(tracking_uri)
    if parsed.scheme != "file":
        return None

    path = unquote(parsed.path)
    if os.name == "nt" and path.startswith("/") and len(path) > 2 and path[2] == ":":
        path = path[1:]
    return Path(path)


def _find_local_model_artifact(run_id: str, flavor: str) -> str | None:
    """Find a bundled MLflow model artifact by run_id + flavor.

    Docker runtime bundles `mlruns/1/models/.../artifacts`, but the file-store
    run metadata may not be present. In that case `runs:/...` cannot resolve;
    this scan locates the artifact directory from its MLmodel file instead.
    """
    tracking_path = _tracking_uri_to_path()
    if tracking_path is None or not tracking_path.exists():
        return None

    for mlmodel_path in tracking_path.rglob("MLmodel"):
        text = mlmodel_path.read_text(encoding="utf-8", errors="ignore")
        if f"run_id: {run_id}" in text and f"  {flavor}:" in text:
            return str(mlmodel_path.parent)
    return None


def _load_with_local_fallback(
    loader: Callable[[str], Any],
    model_uri: str,
    run_id: str,
    flavor: str,
) -> Any:
    """Load an MLflow model URI, falling back to bundled local artifacts."""
    try:
        return loader(model_uri)
    except Exception:
        fallback_uri = _find_local_model_artifact(run_id, flavor)
        if fallback_uri is None:
            raise
        logger.warning(
            "Could not load %s from %s; falling back to bundled artifact %s",
            flavor,
            model_uri,
            fallback_uri,
        )
        return loader(fallback_uri)


class ModelService:
    """Loads and caches champion + challenger models from MLflow at startup.

    Champion:   LightGBM — returns score to caller.
    Challenger: CatBoost — scores in BackgroundTask, writes to shadow_decisions only.
    """

    def __init__(self) -> None:
        self._champion: Any = None
        self._challenger: Any = None
        self._champion_run_id: str = ""
        self._challenger_run_id: str = ""
        self._feature_names: list[str] = []
        self._cat_cols: list[str] = []
        self._ready: bool = False

    # ── Public properties ──────────────────────────────────────────────────────

    @property
    def champion_run_id(self) -> str:
        return self._champion_run_id

    @property
    def challenger_run_id(self) -> str:
        return self._challenger_run_id

    @property
    def ready(self) -> bool:
        return self._ready

    # ── Startup ────────────────────────────────────────────────────────────────

    def load(self, champion_run_id: str, challenger_run_id: str = "") -> None:
        """Load models from MLflow artifact store.  Called once in app lifespan."""
        logger.info("Loading champion from MLflow run %s ...", champion_run_id)
        champion_uri = os.getenv(
            "MLFLOW_CHAMPION_MODEL_URI",
            f"runs:/{champion_run_id}/lgbm_champion",
        )
        self._champion = _load_with_local_fallback(
            mlflow.lightgbm.load_model,
            champion_uri,
            champion_run_id,
            "lightgbm",
        )
        self._champion_run_id = champion_run_id

        # Feature names in training order from the LightGBM booster
        self._feature_names = list(self._champion.booster_.feature_name())

        # Detect which features are categorical (trained with 'category' dtype)
        feature_types = getattr(self._champion.booster_, "feature_types", None)
        if feature_types:
            self._cat_cols = [
                name
                for name, ftype in zip(self._feature_names, feature_types, strict=False)
                if ftype == "categorical"
            ]
        elif getattr(self._champion.booster_, "pandas_categorical", None):
            self._cat_cols = [
                name for name in self._feature_names if name in _KNOWN_CATEGORICAL_COLS
            ]
        logger.info(
            "Champion loaded: %d features, %d categorical",
            len(self._feature_names),
            len(self._cat_cols),
        )

        if challenger_run_id:
            logger.info("Loading challenger from MLflow run %s ...", challenger_run_id)
            challenger_uri = os.getenv(
                "MLFLOW_CHALLENGER_MODEL_URI",
                f"runs:/{challenger_run_id}/catboost_challenger",
            )
            self._challenger = _load_with_local_fallback(
                mlflow.catboost.load_model,
                challenger_uri,
                challenger_run_id,
                "catboost",
            )
            self._challenger_run_id = challenger_run_id
            logger.info("Challenger loaded.")

        self._ready = True

    # ── Scoring ────────────────────────────────────────────────────────────────

    def score_champion(self, raw: dict[str, Any], feast: dict[str, Any]) -> float:
        """Return champion fraud probability in [0, 1]."""
        features = self._build_features(raw, feast, mode="lgbm")
        prob: float = float(self._champion.predict_proba(features)[0, 1])
        return prob

    def score_challenger(self, raw: dict[str, Any], feast: dict[str, Any]) -> float | None:
        """Return challenger fraud probability, or None if challenger not loaded."""
        if self._challenger is None:
            return None
        features = self._build_features(raw, feast, mode="catboost")
        prob: float = float(self._challenger.predict_proba(features)[0, 1])
        return prob

    # ── Internal ───────────────────────────────────────────────────────────────

    def _build_features(
        self, raw: dict[str, Any], feast: dict[str, Any], mode: str
    ) -> pd.DataFrame:
        """Merge raw request fields with Feast features into a single-row DataFrame.

        Fields in _EXCLUDE_COLS are dropped before building the vector.
        Missing columns are filled with NaN; columns are ordered to match training.
        """
        merged = {**raw, **feast}
        for col in _EXCLUDE_COLS:
            merged.pop(col, None)

        row = {
            col: np.nan if (value := merged.get(col, np.nan)) is None else value
            for col in self._feature_names
        }
        df = pd.DataFrame([row], columns=self._feature_names)

        if mode == "lgbm":
            for col in self._cat_cols:
                df[col] = df[col].astype("category")
        elif mode == "catboost":
            # CatBoost requires no NaN in categorical columns; sentinel string fills them
            for col in self._cat_cols:
                df[col] = df[col].astype(object).fillna("__NA__")

        return df
