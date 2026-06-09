"""Great Expectations bronze validation suite for train_transaction.csv.

Validates raw IEEE-CIS data before it enters the bronze layer. Run this:
  - Locally before any feature engineering or model training
  - In the Databricks Bronze→Silver pipeline before Silver writes begin
  - In CI against a sampled fixture to catch schema regressions

Uses GE 1.x fluent API (great-expectations >= 1.0).
"""

from __future__ import annotations

import logging
import sys

import pandas as pd

logger = logging.getLogger(__name__)

# Expected value ranges derived from EDA (notebooks/01_eda.ipynb)
EXPECTED_ROW_COUNT_MIN = 500_000
EXPECTED_ROW_COUNT_MAX = 700_000
EXPECTED_FRAUD_RATE_MIN = 0.020  # 2.0%
EXPECTED_FRAUD_RATE_MAX = 0.050  # 5.0%
EXPECTED_DT_MIN = 0
EXPECTED_DT_MAX = 20_000_000  # ~231 days in seconds — generous upper bound
EXPECTED_AMT_MIN = 0.0
VALID_PRODUCT_CODES = ["W", "H", "C", "S", "R"]

SUITE_NAME = "bronze_transaction_suite"


def _build_suite(suite) -> None:  # type: ignore[no-untyped-def]
    """Define all expectations for the bronze transaction dataset."""
    import great_expectations.expectations as gxe

    # ── Table-level ─────────────────────────────────────────────────────────
    suite.add_expectation(
        gxe.ExpectTableRowCountToBeBetween(
            min_value=EXPECTED_ROW_COUNT_MIN,
            max_value=EXPECTED_ROW_COUNT_MAX,
        )
    )

    # ── Required columns exist ───────────────────────────────────────────────
    for col in [
        "TransactionID",
        "TransactionDT",
        "TransactionAmt",
        "ProductCD",
        "isFraud",
        "card1",
    ]:
        suite.add_expectation(gxe.ExpectColumnToExist(column=col))

    # ── TransactionID ────────────────────────────────────────────────────────
    suite.add_expectation(gxe.ExpectColumnValuesToNotBeNull(column="TransactionID"))
    suite.add_expectation(gxe.ExpectColumnValuesToBeUnique(column="TransactionID"))

    # ── TransactionDT (seconds offset — must be positive) ───────────────────
    suite.add_expectation(gxe.ExpectColumnValuesToNotBeNull(column="TransactionDT"))
    suite.add_expectation(
        gxe.ExpectColumnValuesToBeBetween(
            column="TransactionDT",
            min_value=EXPECTED_DT_MIN,
            max_value=EXPECTED_DT_MAX,
        )
    )

    # ── TransactionAmt (strictly positive) ──────────────────────────────────
    suite.add_expectation(gxe.ExpectColumnValuesToNotBeNull(column="TransactionAmt"))
    suite.add_expectation(
        gxe.ExpectColumnValuesToBeBetween(
            column="TransactionAmt",
            min_value=EXPECTED_AMT_MIN,
            strict_min=True,
        )
    )

    # ── isFraud (binary label, realistic fraud rate) ─────────────────────────
    suite.add_expectation(gxe.ExpectColumnValuesToNotBeNull(column="isFraud"))
    suite.add_expectation(gxe.ExpectColumnValuesToBeInSet(column="isFraud", value_set=[0, 1]))
    suite.add_expectation(
        gxe.ExpectColumnMeanToBeBetween(
            column="isFraud",
            min_value=EXPECTED_FRAUD_RATE_MIN,
            max_value=EXPECTED_FRAUD_RATE_MAX,
        )
    )

    # ── card1 (primary card identifier — must be present) ────────────────────
    suite.add_expectation(gxe.ExpectColumnValuesToNotBeNull(column="card1"))

    # ── ProductCD (known category set) ──────────────────────────────────────
    suite.add_expectation(
        gxe.ExpectColumnValuesToBeInSet(
            column="ProductCD",
            value_set=VALID_PRODUCT_CODES,
        )
    )

    # ── V-features: at least V1 must exist (structural check) ────────────────
    suite.add_expectation(gxe.ExpectColumnToExist(column="V1"))
    suite.add_expectation(gxe.ExpectColumnToExist(column="V339"))


def run_bronze_validation(
    data_path: str = "data/raw/train_transaction.csv",
    *,
    sample_n: int | None = None,
) -> bool:
    """Run the bronze validation suite against train_transaction.csv.

    Args:
        data_path: Path to the raw transaction CSV.
        sample_n: If set, validate against a random sample of this many rows.
                  Useful in CI to keep runtime under 60 seconds.

    Returns:
        True if all expectations pass, False otherwise.
    """
    import great_expectations as gx

    logger.info("Loading %s", data_path)
    df = pd.read_csv(data_path)

    if sample_n is not None:
        df = df.sample(n=min(sample_n, len(df)), random_state=42)
        logger.info("Sampled %d rows for validation", len(df))

    context = gx.get_context(mode="ephemeral")

    datasource = context.data_sources.add_pandas(name="bronze_source")
    asset = datasource.add_dataframe_asset(name="train_transaction")
    batch_def = asset.add_batch_definition_whole_dataframe(name="full_batch")

    suite = context.suites.add(gx.ExpectationSuite(name=SUITE_NAME))
    _build_suite(suite)

    batch = batch_def.get_batch(batch_parameters={"dataframe": df})
    result = batch.validate(suite)

    # ── Report ────────────────────────────────────────────────────────────────
    stats = result["statistics"]
    passed = stats["successful_expectations"]
    total = stats["evaluated_expectations"]

    print(f"\n{'=' * 60}")
    print(f"BRONZE VALIDATION SUITE: {SUITE_NAME}")
    print(f"Dataset: {data_path}  ({len(df):,} rows)")
    print(f"{'=' * 60}")
    print(f"Result : {'PASS' if result['success'] else 'FAIL'}")
    print(f"Score  : {passed} / {total} expectations passed")
    print()

    for r in result["results"]:
        ok = r["success"]
        exp_type = r["expectation_config"]["type"]
        col = r["expectation_config"]["kwargs"].get("column", "table")
        marker = "PASS" if ok else "FAIL"
        print(f"  {marker}  [{col:20s}]  {exp_type}")
        if not ok:
            print(f"       observed: {r.get('result', {})}")

    print(f"{'=' * 60}\n")
    return bool(result["success"])


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s | %(message)s")
    success = run_bronze_validation()
    sys.exit(0 if success else 1)
