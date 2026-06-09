"""Temporal train/validation/test split — NO random splits. Ever. (Rule 1)

IEEE-CIS TransactionDT is in seconds from an undisclosed reference date.
The dataset spans ~183 days. Default thresholds approximate:
  - Training:   first 70% of the timeline  (~406k transactions)
  - Validation: next 15%                   (~88k transactions)
  - Test:       final 15% (holdout — used ONCE at the end)

Why temporal, not random: random splits allow the model to observe future
card behaviour during training. Rolling features computed from shuffled data
would encode information that doesn't exist at inference time. Temporal splits
replicate the actual production constraint: the model only knows the past.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TemporalSplit:
    """The three temporal partitions of a transaction dataset (immutable)."""

    train: pd.DataFrame
    val: pd.DataFrame
    test: pd.DataFrame


def compute_thresholds(
    df: pd.DataFrame,
    *,
    train_frac: float = 0.70,
    val_frac: float = 0.85,
    dt_col: str = "TransactionDT",
) -> tuple[int, int]:
    """Derive TransactionDT cutoffs from quantiles of the dataset.

    Returns (train_end_dt, val_end_dt) such that:
      - train: dt < train_end_dt
      - val:   train_end_dt ≤ dt < val_end_dt
      - test:  dt ≥ val_end_dt

    Args:
        df: Transaction DataFrame with dt_col.
        train_frac: Fraction of the timeline that belongs to training.
        val_frac: Fraction of the timeline that ends the validation window.
        dt_col: Timestamp column name.

    Returns:
        (train_end_dt, val_end_dt) as integers (TransactionDT units).
    """
    if not 0.0 < train_frac < val_frac < 1.0:
        raise ValueError(f"Require 0 < train_frac < val_frac < 1, got {train_frac=}, {val_frac=}")

    dt_sorted = df[dt_col].sort_values()
    n = len(dt_sorted)
    train_end = int(dt_sorted.iloc[int(n * train_frac)])
    val_end = int(dt_sorted.iloc[int(n * val_frac)])
    return train_end, val_end


def temporal_split(
    df: pd.DataFrame,
    *,
    train_end_dt: int | None = None,
    val_end_dt: int | None = None,
    dt_col: str = "TransactionDT",
) -> TemporalSplit:
    """Split transactions into train / val / test by time. Rule 1: no random splits.

    Args:
        df: Transaction DataFrame containing dt_col.
        train_end_dt: Exclusive upper bound for training (TransactionDT units).
            Defaults to the 70th-percentile value of dt_col.
        val_end_dt: Exclusive upper bound for validation.
            Defaults to the 85th-percentile value of dt_col.
        dt_col: Name of the timestamp column (seconds offset).

    Returns:
        TemporalSplit with .train, .val, .test DataFrames (index reset).

    Raises:
        KeyError: dt_col is absent from df.
        ValueError: A threshold produces an empty split, or train_end_dt >= val_end_dt.
    """
    if dt_col not in df.columns:
        raise KeyError(f"Column '{dt_col}' not found. Available: {list(df.columns)}")

    if train_end_dt is None or val_end_dt is None:
        computed_train, computed_val = compute_thresholds(df, dt_col=dt_col)
        train_end_dt = train_end_dt if train_end_dt is not None else computed_train
        val_end_dt = val_end_dt if val_end_dt is not None else computed_val

    if train_end_dt >= val_end_dt:
        raise ValueError(f"train_end_dt ({train_end_dt:,}) must be < val_end_dt ({val_end_dt:,})")

    dt = df[dt_col]
    train = df[dt < train_end_dt].reset_index(drop=True)
    val = df[(dt >= train_end_dt) & (dt < val_end_dt)].reset_index(drop=True)
    test = df[dt >= val_end_dt].reset_index(drop=True)

    for name, part in [("train", train), ("val", val), ("test", test)]:
        if len(part) == 0:
            raise ValueError(f"'{name}' split is empty with {train_end_dt=:,}, {val_end_dt=:,}")

    logger.info(
        "Temporal split: train=%d, val=%d, test=%d (dt thresholds: %d / %d)",
        len(train),
        len(val),
        len(test),
        train_end_dt,
        val_end_dt,
    )
    return TemporalSplit(train=train, val=val, test=test)


def log_split_stats(split: TemporalSplit) -> None:
    """Log a one-line summary of each partition for sanity checking."""
    total = len(split.train) + len(split.val) + len(split.test)
    for name, part in [("train", split.train), ("val", split.val), ("test", split.test)]:
        pct = len(part) / total * 100
        fraud_rate = f"{part['isFraud'].mean() * 100:.3f}%" if "isFraud" in part.columns else "n/a"
        dt_range = f"[{part['TransactionDT'].min():,} → {part['TransactionDT'].max():,}]"
        logger.info(
            "%5s: n=%7d (%4.1f%%)  fraud=%s  dt=%s",
            name,
            len(part),
            pct,
            fraud_rate,
            dt_range,
        )
