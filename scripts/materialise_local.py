"""Materialise the existing feature parquet into the local Postgres online store.

Requires:
    docker compose up postgres -d
    FEAST_POSTGRES_PASSWORD=local_dev_only (default)

Parquet must already exist at data/feast/card_transaction_stats.parquet.
This script skips the expensive CSV rebuild and goes straight to apply+materialise.

Usage:
    python scripts/materialise_local.py
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent
PARQUET_PATH = PROJECT_ROOT / "data" / "feast" / "card_transaction_stats.parquet"

if not PARQUET_PATH.exists():
    logger.error("Parquet not found at %s", PARQUET_PATH)
    logger.error("Run: python -m src.train.feast_materialise")
    sys.exit(1)

os.environ.setdefault("FEAST_POSTGRES_PASSWORD", "local_dev_only")

# feast_materialise.apply_and_materialise() handles apply + materialise
sys.path.insert(0, str(PROJECT_ROOT))
from src.train.feast_materialise import apply_and_materialise  # noqa: E402

size_mb = round(PARQUET_PATH.stat().st_size / 1e6, 1)
logger.info("Parquet found (%s MB) — skipping rebuild", size_mb)
logger.info("Running feast apply + materialise against local Postgres (localhost:5433)...")

apply_and_materialise()

logger.info("Done. Run the skew test:")
logger.info("  pytest tests/integration/test_feature_skew.py -v")
