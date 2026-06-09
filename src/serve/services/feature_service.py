"""Feast online feature fetch service."""

from __future__ import annotations

import logging
import os
from typing import Any

import psycopg2
from feast import FeatureStore
from feast.infra.key_encoding_utils import serialize_entity_key
from feast.infra.online_stores.postgres_online_store.postgres import _table_id
from feast.protos.feast.types.EntityKey_pb2 import EntityKey as EntityKeyProto
from feast.protos.feast.types.Value_pb2 import Value as ValueProto
from feast.type_map import feast_value_type_to_python_type
from psycopg2 import Binary, sql

from src.train.feature_engineering import ENGINEERED_FEATURE_COLS

logger = logging.getLogger(__name__)

_FEAST_FEATURES = [f"card_transaction_stats:{col}" for col in ENGINEERED_FEATURE_COLS]


def _missing_features() -> dict[str, float]:
    return {col: float("nan") for col in ENGINEERED_FEATURE_COLS}


class FeatureService:
    """Fetches card-level rolling features from the Feast online (Postgres) store.

    One instance per application; created during lifespan startup.
    """

    def __init__(self, repo_path: str) -> None:
        self._store = FeatureStore(repo_path=repo_path)
        self._feature_view = self._store.get_feature_view("card_transaction_stats")
        self._table_name = _table_id(
            self._store.config.project,
            self._feature_view,
            self._store.config.registry.enable_online_feature_view_versioning,
        )
        self._online_feature_reads_enabled = True
        logger.info("FeatureStore initialised from %s", repo_path)

    def get_features(self, card1: int) -> dict[str, Any]:
        """Return the 9 rolling-window features for card1 from the online store.

        Returns a flat dict: {"fe_card_txn_count_1h": ..., ...}.
        Values are float | None (None if card has no history in the store).
        """
        if not self._online_feature_reads_enabled:
            return _missing_features()

        try:
            result = self._store.get_online_features(
                features=_FEAST_FEATURES,
                entity_rows=[{"card1": card1}],
            ).to_dict()

            # Strip the entity key; return only the 9 feature values
            return {
                k: float("nan") if v[0] is None else v[0]
                for k, v in result.items()
                if k != "card1"
            }
        except Exception:
            logger.exception("Feast online read failed; using psycopg2 fallback")
            try:
                return self._get_features_psycopg2(card1)
            except Exception:
                logger.exception(
                    "Feature fallback failed; disabling online feature reads "
                    "for this worker and returning missing feature defaults"
                )
                self._online_feature_reads_enabled = False
                return _missing_features()

    def _get_features_psycopg2(self, card1: int) -> dict[str, Any]:
        """Read Feast's Postgres online table directly with psycopg2.

        This keeps serving available if Feast's psycopg3 connection path fails
        in Container Apps while preserving the same serialized online-store data.
        """
        entity_key = EntityKeyProto(
            join_keys=["card1"],
            entity_values=[ValueProto(int64_val=card1)],
        )
        encoded_key = serialize_entity_key(
            entity_key,
            entity_key_serialization_version=self._store.config.entity_key_serialization_version,
        )

        query = sql.SQL(
            """
            SELECT feature_name, value
            FROM {}
            WHERE entity_key = %s AND feature_name = ANY(%s);
            """
        ).format(sql.Identifier(self._table_name))

        result: dict[str, Any] = _missing_features()
        conn = psycopg2.connect(
            host=os.environ["FEAST_ONLINE_STORE_HOST"],
            port=int(os.getenv("FEAST_ONLINE_STORE_PORT", "5432")),
            dbname="fraud_platform",
            user=os.environ["FEAST_ONLINE_STORE_USER"],
            password=os.environ["FEAST_POSTGRES_PASSWORD"],
            sslmode=os.getenv("FEAST_ONLINE_STORE_SSLMODE", "require"),
        )
        try:
            with conn.cursor() as cur:
                cur.execute(query, (Binary(encoded_key), ENGINEERED_FEATURE_COLS))
                for feature_name, value_bin in cur.fetchall():
                    value = ValueProto()
                    value.ParseFromString(bytes(value_bin))
                    result[feature_name] = feast_value_type_to_python_type(value)
        finally:
            conn.close()

        return result
