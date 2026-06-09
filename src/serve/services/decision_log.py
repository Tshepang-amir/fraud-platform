"""Postgres decision logging — champion decisions + challenger shadow decisions.

Two tables:
  decisions        — champion decisions returned to the caller
  shadow_decisions — challenger decisions (Rule 5: never returned to caller)

Both tables are created on first connect if they don't exist.
"""

from __future__ import annotations

import logging
from typing import Any

import psycopg2
import psycopg2.extras
import psycopg2.pool

logger = logging.getLogger(__name__)

_CREATE_DECISIONS = """
CREATE TABLE IF NOT EXISTS decisions (
    id              BIGSERIAL PRIMARY KEY,
    request_id      UUID         NOT NULL,
    transaction_id  TEXT         NOT NULL,
    card1           INTEGER      NOT NULL,
    fraud_score     DOUBLE PRECISION NOT NULL,
    decision        TEXT         NOT NULL,
    model_version   TEXT         NOT NULL,
    latency_ms      DOUBLE PRECISION NOT NULL,
    created_at      TIMESTAMPTZ  DEFAULT NOW()
);
"""

_CREATE_SHADOW = """
CREATE TABLE IF NOT EXISTS shadow_decisions (
    id              BIGSERIAL PRIMARY KEY,
    request_id      UUID         NOT NULL,
    transaction_id  TEXT         NOT NULL,
    card1           INTEGER      NOT NULL,
    fraud_score     DOUBLE PRECISION NOT NULL,
    model_version   TEXT         NOT NULL,
    created_at      TIMESTAMPTZ  DEFAULT NOW()
);
"""

_INSERT_DECISION = """
INSERT INTO decisions
    (request_id, transaction_id, card1, fraud_score, decision, model_version, latency_ms)
VALUES (%s, %s, %s, %s, %s, %s, %s);
"""

_INSERT_SHADOW = """
INSERT INTO shadow_decisions (request_id, transaction_id, card1, fraud_score, model_version)
VALUES (%s, %s, %s, %s, %s);
"""


class DecisionLogService:
    """Writes champion and shadow decisions to Postgres using a connection pool."""

    def __init__(self, dsn: str, min_conn: int = 2, max_conn: int = 10) -> None:
        self._pool: psycopg2.pool.ThreadedConnectionPool = psycopg2.pool.ThreadedConnectionPool(
            min_conn, max_conn, dsn
        )
        self._ensure_tables()
        logger.info("DecisionLogService ready (pool min=%d max=%d)", min_conn, max_conn)

    def _ensure_tables(self) -> None:
        conn = self._pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute(_CREATE_DECISIONS)
                cur.execute(_CREATE_SHADOW)
            conn.commit()
        finally:
            self._pool.putconn(conn)

    def log_decision(
        self,
        *,
        request_id: str,
        transaction_id: str,
        card1: int,
        fraud_score: float,
        decision: str,
        model_version: str,
        latency_ms: float,
    ) -> None:
        """Insert a champion decision.  Called synchronously on the request path."""
        conn = self._pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    _INSERT_DECISION,
                    (
                        request_id,
                        transaction_id,
                        card1,
                        fraud_score,
                        decision,
                        model_version,
                        latency_ms,
                    ),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            logger.exception("Failed to log champion decision request_id=%s", request_id)
        finally:
            self._pool.putconn(conn)

    def log_shadow(
        self,
        *,
        request_id: str,
        transaction_id: str,
        card1: int,
        fraud_score: float,
        model_version: str,
    ) -> None:
        """Insert a challenger shadow decision.  Called in BackgroundTask."""
        conn = self._pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    _INSERT_SHADOW,
                    (request_id, transaction_id, card1, fraud_score, model_version),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            logger.exception("Failed to log shadow decision request_id=%s", request_id)
        finally:
            self._pool.putconn(conn)

    def close(self) -> None:
        self._pool.closeall()
        logger.info("DecisionLogService: connection pool closed")

    def health_check(self) -> dict[str, Any]:
        """Return {'ok': True} or {'ok': False, 'error': str}."""
        try:
            conn = self._pool.getconn()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
            finally:
                self._pool.putconn(conn)
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
