"""PaySim → IEEE-CIS schema mapper.

# ═══════════════════════════════════════════════════════════════════════════════
# ██████  DEMO SEAM — DECLARED AND DOCUMENTED  ██████
# ═══════════════════════════════════════════════════════════════════════════════
#
# This module reshapes PaySim synthetic mobile-money transactions to match the
# IEEE-CIS Fraud Detection dataset schema. It is used ONLY as the live event
# stream simulator for demo purposes.
#
# In a real deployment, this module would be replaced by an adapter that reads
# actual card transaction feeds (e.g., from a payment processor's API or a
# Kafka topic connected to the bank's core banking system).
#
# What this seam provides:
#   - Realistic transaction volume and velocity for streaming demo
#   - Schema-compatible events so downstream pipeline code is unchanged
#   - Fraud labels (isFraud) for immediate validation without label delay
#
# What this seam does NOT provide:
#   - Real card transaction features (V1-V339 identity/device fingerprints)
#   - Actual temporal patterns from production card usage
#   - Real-world feature distributions for drift detection validation
#
# The README states: "Live stream is simulated using PaySim events reshaped to
# IEEE schema. Real deployment would ingest actual card transaction feeds."
#
# ═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema mapping: PaySim columns → IEEE-CIS columns
# ---------------------------------------------------------------------------
# PaySim schema:
#   step, type, amount, nameOrig, oldbalanceOrg, newbalanceOrig,
#   nameDest, oldbalanceDest, newbalanceDest, isFraud, isFlaggedFraud
#
# IEEE-CIS schema (subset we map to):
#   TransactionID, TransactionDT, TransactionAmt, ProductCD, card1-card6,
#   addr1, addr2, P_emaildomain, R_emaildomain, isFraud,
#   C1-C14, D1-D15, M1-M9, V1-V339
#
# Unmappable IEEE-CIS features (device fingerprints, identity features) are
# filled with synthetic noise or NaN to maintain schema shape. This is
# acceptable because:
#   1. The model is trained on REAL IEEE-CIS data (not this mapped data)
#   2. This mapper is only used for the streaming DEMO pipeline
#   3. Feature importance will show these synthetic features contribute nothing

# PaySim transaction type → IEEE-CIS ProductCD mapping
PAYSIM_TYPE_TO_PRODUCT_CD: dict[str, str] = {
    "PAYMENT": "C",  # Card payment
    "TRANSFER": "H",  # Transfer (mapped to H-commerce)
    "CASH_OUT": "R",  # Cash withdrawal (mapped to R-recurring)
    "DEBIT": "S",  # Debit (mapped to S-subscription)
    "CASH_IN": "W",  # Cash-in (mapped to W-wire)
}

# Number of synthetic V-features to generate (IEEE-CIS has V1-V339)
N_V_FEATURES = 339

# Number of synthetic C-features (IEEE-CIS has C1-C14)
N_C_FEATURES = 14

# Number of synthetic D-features (IEEE-CIS has D1-D15)
N_D_FEATURES = 15


def _deterministic_hash(value: str, modulo: int) -> int:
    """Generate a deterministic integer hash for a string value.

    Used to map PaySim account names to IEEE-CIS card identifiers
    consistently — the same account always maps to the same card ID.

    Args:
        value: String to hash (e.g., PaySim account name).
        modulo: Upper bound for the output integer.

    Returns:
        Deterministic integer in [0, modulo).
    """
    digest = hashlib.sha256(value.encode()).hexdigest()
    return int(digest[:8], 16) % modulo


def _generate_synthetic_v_features(
    n_rows: int,
    rng: np.random.Generator,
) -> pd.DataFrame:
    """Generate synthetic V-features (V1-V339) as noise.

    These features represent device fingerprints and identity signals in the
    real IEEE-CIS dataset. Since PaySim has no equivalent data, we fill them
    with random noise. This is safe because:

    1. The champion model is trained on REAL IEEE-CIS V-features
    2. This synthetic data is only used in the streaming demo pipeline
    3. At inference time, real V-features would come from the payment processor

    Args:
        n_rows: Number of rows to generate.
        rng: NumPy random generator for reproducibility.

    Returns:
        DataFrame with columns V1 through V339.
    """
    # Mix of normal and uniform distributions to loosely approximate
    # the heterogeneous nature of the real V-features
    data: dict[str, np.ndarray[Any, np.dtype[np.floating[Any]]]] = {}
    for i in range(1, N_V_FEATURES + 1):
        if i % 3 == 0:
            # ~1/3 of features have NaN patterns (sparse, like real data)
            col = rng.standard_normal(n_rows)
            mask = rng.random(n_rows) < 0.4
            col[mask] = np.nan
            data[f"V{i}"] = col
        elif i % 2 == 0:
            data[f"V{i}"] = rng.uniform(0, 1, n_rows)
        else:
            data[f"V{i}"] = rng.standard_normal(n_rows)

    return pd.DataFrame(data)


def _generate_synthetic_c_features(
    n_rows: int,
    rng: np.random.Generator,
) -> pd.DataFrame:
    """Generate synthetic C-features (C1-C14) as counting features.

    In IEEE-CIS, C-features represent various counts (e.g., number of
    addresses associated with a card). We approximate with Poisson-distributed
    integers.

    Args:
        n_rows: Number of rows to generate.
        rng: NumPy random generator.

    Returns:
        DataFrame with columns C1 through C14.
    """
    return pd.DataFrame(
        {
            f"C{i}": rng.poisson(lam=2.0, size=n_rows).astype(float)
            for i in range(1, N_C_FEATURES + 1)
        }
    )


def _generate_synthetic_d_features(
    amounts: pd.Series,
    rng: np.random.Generator,
) -> pd.DataFrame:
    """Generate synthetic D-features (D1-D15) as time-delta features.

    In IEEE-CIS, D-features represent time deltas (e.g., days since last
    transaction). We derive D1 from the transaction amount as a rough proxy,
    and fill the rest with exponential noise.

    Args:
        amounts: Transaction amounts (used to derive D1 proxy).
        rng: NumPy random generator.

    Returns:
        DataFrame with columns D1 through D15.
    """
    n_rows = len(amounts)
    data: dict[str, np.ndarray[Any, np.dtype[np.floating[Any]]]] = {}
    # D1: loosely correlated with amount (higher amount → longer gap in days)
    data["D1"] = np.log1p(amounts.values) * rng.uniform(0.5, 2.0, n_rows)
    for i in range(2, N_D_FEATURES + 1):
        col = rng.exponential(scale=30.0, size=n_rows)
        # Some D-features are sparse in the real dataset
        if i > 10:
            mask = rng.random(n_rows) < 0.5
            col[mask] = np.nan
        data[f"D{i}"] = col

    return pd.DataFrame(data)


def map_paysim_to_ieee(
    paysim_df: pd.DataFrame,
    *,
    seed: int = 42,
    base_timestamp: int = 86400,
    step_seconds: int = 3600,
) -> pd.DataFrame:
    """Transform a PaySim DataFrame into IEEE-CIS-compatible schema.

    This is the core schema mapping function. It takes raw PaySim data and
    produces a DataFrame with the same column schema as the IEEE-CIS Fraud
    Detection dataset, suitable for feeding into the streaming pipeline.

    ┌──────────────────────────────────────────────────────────────────────┐
    │  DEMO SEAM: This function exists solely for the streaming demo.    │
    │  In production, raw card transactions would arrive pre-formatted   │
    │  from the payment processor with real card/device/identity data.   │
    └──────────────────────────────────────────────────────────────────────┘

    Args:
        paysim_df: Raw PaySim DataFrame with columns:
            step, type, amount, nameOrig, oldbalanceOrg, newbalanceOrig,
            nameDest, oldbalanceDest, newbalanceDest, isFraud, isFlaggedFraud
        seed: Random seed for reproducible synthetic feature generation.
        base_timestamp: Base value for TransactionDT (IEEE-CIS uses seconds
            from a reference point). Default 86400 (= 1 day offset).
        step_seconds: Number of seconds per PaySim step. PaySim uses 1 step
            = 1 hour of simulated time, so default is 3600.

    Returns:
        DataFrame with IEEE-CIS-compatible schema. All required columns
        present; synthetic features clearly marked in this docstring.

    Raises:
        ValueError: If required PaySim columns are missing.
    """
    required_cols = {
        "step",
        "type",
        "amount",
        "nameOrig",
        "nameDest",
        "isFraud",
        "oldbalanceOrg",
        "newbalanceOrig",
        "oldbalanceDest",
        "newbalanceDest",
    }
    missing = required_cols - set(paysim_df.columns)
    if missing:
        msg = f"Missing required PaySim columns: {sorted(missing)}"
        raise ValueError(msg)

    n_rows = len(paysim_df)
    rng = np.random.default_rng(seed)

    logger.info(
        "Mapping %d PaySim transactions to IEEE-CIS schema (demo seam)",
        n_rows,
    )

    # ── Core transaction fields ────────────────────────────────────────
    ieee_df = pd.DataFrame()

    # TransactionID: sequential, starting from 1
    ieee_df["TransactionID"] = range(1, n_rows + 1)

    # TransactionDT: seconds from reference point (derived from PaySim step)
    ieee_df["TransactionDT"] = base_timestamp + paysim_df["step"].values * step_seconds

    # TransactionAmt: direct mapping
    ieee_df["TransactionAmt"] = paysim_df["amount"].values

    # ProductCD: mapped from PaySim transaction type
    ieee_df["ProductCD"] = (
        paysim_df["type"]
        .map(PAYSIM_TYPE_TO_PRODUCT_CD)
        .fillna("W")  # Default to W for unknown types
    )

    # ── Card identifiers (card1-card6) ─────────────────────────────────
    # Derived deterministically from PaySim account names so the same
    # account always maps to the same card identifier.
    ieee_df["card1"] = paysim_df["nameOrig"].apply(
        lambda x: _deterministic_hash(str(x), modulo=20000)
    )
    ieee_df["card2"] = paysim_df["nameOrig"].apply(
        lambda x: _deterministic_hash(f"{x}_card2", modulo=600)
    )
    ieee_df["card3"] = paysim_df["nameOrig"].apply(
        lambda x: _deterministic_hash(f"{x}_card3", modulo=300)
    )
    ieee_df["card4"] = (
        paysim_df["type"]
        .map(
            {
                "PAYMENT": "visa",
                "TRANSFER": "mastercard",
                "CASH_OUT": "visa",
                "DEBIT": "discover",
                "CASH_IN": "mastercard",
            }
        )
        .fillna("visa")
    )
    ieee_df["card5"] = paysim_df["nameOrig"].apply(
        lambda x: _deterministic_hash(f"{x}_card5", modulo=400)
    )
    ieee_df["card6"] = rng.choice(["debit", "credit"], size=n_rows)

    # ── Address features ───────────────────────────────────────────────
    ieee_df["addr1"] = paysim_df["nameOrig"].apply(
        lambda x: _deterministic_hash(f"{x}_addr1", modulo=500)
    )
    ieee_df["addr2"] = paysim_df["nameOrig"].apply(
        lambda x: _deterministic_hash(f"{x}_addr2", modulo=100)
    )

    # ── Email domains ──────────────────────────────────────────────────
    email_domains = [
        "gmail.com",
        "yahoo.com",
        "hotmail.com",
        "outlook.com",
        "protonmail.com",
        "icloud.com",
        None,
    ]
    ieee_df["P_emaildomain"] = rng.choice(email_domains, size=n_rows)  # type: ignore[arg-type]
    ieee_df["R_emaildomain"] = rng.choice(email_domains, size=n_rows)  # type: ignore[arg-type]

    # ── Fraud label ────────────────────────────────────────────────────
    ieee_df["isFraud"] = paysim_df["isFraud"].values

    # ── M-features (match flags, boolean-like) ─────────────────────────
    for i in range(1, 10):
        ieee_df[f"M{i}"] = rng.choice(["T", "F", None], size=n_rows, p=[0.4, 0.4, 0.2])  # type: ignore[arg-type]

    # ── Synthetic C, D, V features ─────────────────────────────────────
    c_features = _generate_synthetic_c_features(n_rows, rng)
    d_features = _generate_synthetic_d_features(paysim_df["amount"], rng)
    v_features = _generate_synthetic_v_features(n_rows, rng)

    ieee_df = pd.concat([ieee_df, c_features, d_features, v_features], axis=1)

    logger.info(
        "Schema mapping complete: %d rows, %d columns (IEEE-CIS compatible)",
        len(ieee_df),
        len(ieee_df.columns),
    )

    return ieee_df


def load_paysim(filepath: str | Path) -> pd.DataFrame:
    """Load PaySim CSV dataset from disk.

    Args:
        filepath: Path to the PaySim CSV file (e.g., PS_20174392719_1491204016305_log.csv).

    Returns:
        Raw PaySim DataFrame.

    Raises:
        FileNotFoundError: If the file does not exist.
    """
    path = Path(filepath)
    if not path.exists():
        msg = f"PaySim file not found: {path}"
        raise FileNotFoundError(msg)

    logger.info("Loading PaySim dataset from %s", path)
    return pd.read_csv(path)


def convert_and_save(
    paysim_path: str | Path,
    output_path: str | Path,
    *,
    seed: int = 42,
    sample_frac: float | None = None,
) -> Path:
    """End-to-end conversion: load PaySim → map to IEEE-CIS → save as parquet.

    This is the entry point for the demo data preparation pipeline.
    The output parquet file is used by the Event Hubs producer to replay
    transactions into the streaming pipeline.

    Args:
        paysim_path: Path to raw PaySim CSV.
        output_path: Path for output parquet file.
        seed: Random seed for reproducibility.
        sample_frac: If set, sample this fraction of rows (useful for dev).

    Returns:
        Path to the saved parquet file.
    """
    paysim_df = load_paysim(paysim_path)

    if sample_frac is not None:
        n_before = len(paysim_df)
        paysim_df = paysim_df.sample(frac=sample_frac, random_state=seed)
        logger.info("Sampled %.1f%%: %d → %d rows", sample_frac * 100, n_before, len(paysim_df))

    ieee_df = map_paysim_to_ieee(paysim_df, seed=seed)

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    ieee_df.to_parquet(out, index=False, engine="pyarrow")

    logger.info("Saved IEEE-CIS-compatible dataset to %s", out)
    return out


if __name__ == "__main__":
    # Quick smoke test: convert a small sample
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")

    if len(sys.argv) < 2:
        print("Usage: python paysim_to_ieee.py <paysim_csv_path> [output_parquet_path]")
        sys.exit(1)

    paysim_file = sys.argv[1]
    output_file = sys.argv[2] if len(sys.argv) > 2 else "data/ieee_from_paysim.parquet"

    convert_and_save(
        paysim_path=paysim_file,
        output_path=output_file,
        sample_frac=0.01,  # 1% sample for quick validation
    )
