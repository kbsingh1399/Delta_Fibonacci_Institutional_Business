"""test_data_lake.py: Unit tests for MTF Parquet Data Lake validation."""

import os
import pytest
import pandas as pd
import numpy as np

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "Blueberry_Data")

CHAMPION_SYMBOLS = [
    "SP500.p", "NAS100.p", "XAUUSD.pi", "USWTI.p", "BTCUSD.pi", "EURUSD.pi"
]
TIMEFRAMES = ["5M", "15M", "1H", "4H"]


def test_data_lake_directory_exists():
    assert os.path.exists(DATA_DIR), f"Data directory missing: {DATA_DIR}"


@pytest.mark.parametrize("symbol", CHAMPION_SYMBOLS)
@pytest.mark.parametrize("tf", TIMEFRAMES)
def test_parquet_file_integrity(symbol, tf):
    parquet_path = os.path.join(DATA_DIR, f"{symbol}_{tf}.parquet")
    assert os.path.exists(parquet_path), f"Parquet file missing for {symbol}_{tf}: {parquet_path}"

    df = pd.read_parquet(parquet_path)
    assert len(df) >= 100, f"Insufficient rows in {symbol}_{tf}: {len(df)}"

    required_cols = {"time", "open", "high", "low", "close", "tick_volume"}
    assert required_cols.issubset(set(df.columns)), f"Missing required columns in {symbol}_{tf}"

    # Verify chronological ascending order
    times = pd.to_datetime(df["time"]).astype("int64")
    assert times.is_monotonic_increasing, f"Timestamps are not monotonically increasing in {symbol}_{tf}"

    # Verify price validity
    assert (df["high"] >= df["low"]).all(), f"High < Low found in {symbol}_{tf}"
    assert (df["high"] >= df["open"]).all(), f"High < Open found in {symbol}_{tf}"
    assert (df["high"] >= df["close"]).all(), f"High < Close found in {symbol}_{tf}"
    assert (df["low"] <= df["open"]).all(), f"Low > Open found in {symbol}_{tf}"
    assert (df["low"] <= df["close"]).all(), f"Low > Close found in {symbol}_{tf}"
    assert (df["low"] > 0).all(), f"Non-positive prices found in {symbol}_{tf}"
    assert (df["tick_volume"] >= 0).all(), f"Negative volume found in {symbol}_{tf}"
