"""Unit tests for the PaySim to IEEE-CIS demo seam mapper."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import numpy as np
import pandas as pd
import pytest

from src.ingest import paysim_to_ieee


def make_paysim_df() -> pd.DataFrame:
    """Small PaySim-shaped fixture with stable account IDs."""
    return pd.DataFrame(
        {
            "step": [1, 2, 3, 4, 5],
            "type": ["PAYMENT", "TRANSFER", "CASH_OUT", "DEBIT", "CASH_IN"],
            "amount": [100.0, 250.5, 75.25, 30.0, 999.99],
            "nameOrig": ["C100", "C100", "C200", "C300", "C100"],
            "oldbalanceOrg": [500.0, 400.0, 100.0, 50.0, 1_000.0],
            "newbalanceOrig": [400.0, 149.5, 24.75, 20.0, 1_999.99],
            "nameDest": ["M1", "C900", "C901", "M2", "C902"],
            "oldbalanceDest": [0.0, 100.0, 200.0, 0.0, 500.0],
            "newbalanceDest": [100.0, 350.5, 275.25, 30.0, 1_499.99],
            "isFraud": [0, 1, 0, 0, 1],
            "isFlaggedFraud": [0, 0, 0, 0, 1],
        }
    )


def make_writable_tmp_dir() -> Path:
    """Create a throwaway directory under ignored project data."""
    path = Path("data") / "test_tmp" / f"paysim-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    return path


@pytest.mark.unit
def test_map_paysim_to_ieee_core_schema_and_values() -> None:
    result = paysim_to_ieee.map_paysim_to_ieee(
        make_paysim_df(),
        seed=7,
        base_timestamp=10_000,
        step_seconds=60,
    )

    assert len(result) == 5
    assert result["TransactionID"].tolist() == [1, 2, 3, 4, 5]
    assert result["TransactionDT"].tolist() == [10_060, 10_120, 10_180, 10_240, 10_300]
    assert result["TransactionAmt"].tolist() == [100.0, 250.5, 75.25, 30.0, 999.99]
    assert result["ProductCD"].tolist() == ["C", "H", "R", "S", "W"]
    assert result["isFraud"].tolist() == [0, 1, 0, 0, 1]


@pytest.mark.unit
def test_map_paysim_to_ieee_has_expected_ieee_feature_shape() -> None:
    result = paysim_to_ieee.map_paysim_to_ieee(make_paysim_df(), seed=42)

    for col in ["card1", "card2", "card3", "card4", "card5", "card6"]:
        assert col in result.columns
    for col in ["addr1", "addr2", "P_emaildomain", "R_emaildomain"]:
        assert col in result.columns
    for i in range(1, paysim_to_ieee.N_C_FEATURES + 1):
        assert f"C{i}" in result.columns
    for i in range(1, paysim_to_ieee.N_D_FEATURES + 1):
        assert f"D{i}" in result.columns
    for i in range(1, paysim_to_ieee.N_V_FEATURES + 1):
        assert f"V{i}" in result.columns
    for i in range(1, 10):
        assert f"M{i}" in result.columns


@pytest.mark.unit
def test_map_paysim_to_ieee_is_deterministic_for_same_seed() -> None:
    df = make_paysim_df()

    first = paysim_to_ieee.map_paysim_to_ieee(df, seed=123)
    second = paysim_to_ieee.map_paysim_to_ieee(df, seed=123)

    pd.testing.assert_frame_equal(first, second)


@pytest.mark.unit
def test_same_paysim_origin_maps_to_same_card_identifier() -> None:
    result = paysim_to_ieee.map_paysim_to_ieee(make_paysim_df(), seed=42)

    same_origin = result.loc[[0, 1, 4], "card1"].tolist()
    assert len(set(same_origin)) == 1


@pytest.mark.unit
def test_unknown_transaction_type_defaults_to_product_w() -> None:
    df = make_paysim_df()
    df.loc[0, "type"] = "UNKNOWN"

    result = paysim_to_ieee.map_paysim_to_ieee(df)

    assert result.loc[0, "ProductCD"] == "W"


@pytest.mark.unit
def test_map_paysim_to_ieee_rejects_missing_required_columns() -> None:
    df = make_paysim_df().drop(columns=["amount"])

    with pytest.raises(ValueError, match="Missing required PaySim columns"):
        paysim_to_ieee.map_paysim_to_ieee(df)


@pytest.mark.unit
def test_deterministic_hash_stays_in_modulo_range() -> None:
    values = [paysim_to_ieee._deterministic_hash("C123456", modulo=17) for _ in range(3)]

    assert values == [values[0], values[0], values[0]]
    assert 0 <= values[0] < 17


@pytest.mark.unit
def test_synthetic_feature_generators_return_expected_columns() -> None:
    rng = np.random.default_rng(42)

    c_features = paysim_to_ieee._generate_synthetic_c_features(3, rng)
    d_features = paysim_to_ieee._generate_synthetic_d_features(
        pd.Series([10.0, 20.0, 30.0]),
        rng,
    )
    v_features = paysim_to_ieee._generate_synthetic_v_features(3, rng)

    assert c_features.shape == (3, paysim_to_ieee.N_C_FEATURES)
    assert d_features.shape == (3, paysim_to_ieee.N_D_FEATURES)
    assert v_features.shape == (3, paysim_to_ieee.N_V_FEATURES)
    assert c_features.columns[0] == "C1"
    assert d_features.columns[-1] == "D15"
    assert v_features.columns[-1] == "V339"


@pytest.mark.unit
def test_load_paysim_reads_csv() -> None:
    tmp_path = make_writable_tmp_dir()
    input_path = tmp_path / "paysim.csv"
    make_paysim_df().to_csv(input_path, index=False)

    result = paysim_to_ieee.load_paysim(input_path)

    assert len(result) == 5
    assert result["nameOrig"].tolist()[:2] == ["C100", "C100"]


@pytest.mark.unit
def test_load_paysim_raises_for_missing_file() -> None:
    tmp_path = make_writable_tmp_dir()
    with pytest.raises(FileNotFoundError, match="PaySim file not found"):
        paysim_to_ieee.load_paysim(tmp_path / "missing.csv")


@pytest.mark.unit
def test_convert_and_save_writes_parquet() -> None:
    tmp_path = make_writable_tmp_dir()
    input_path = tmp_path / "paysim.csv"
    output_path = tmp_path / "mapped" / "ieee.parquet"
    make_paysim_df().to_csv(input_path, index=False)

    result_path = paysim_to_ieee.convert_and_save(
        input_path,
        output_path,
        sample_frac=0.4,
        seed=42,
    )

    result = pd.read_parquet(result_path)
    assert result_path == output_path
    assert len(result) == 2
    assert "TransactionID" in result.columns
