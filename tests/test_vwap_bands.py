"""test_vwap_bands.py: Unit tests for session-anchored VWAP standard deviation bands."""

import pytest
import numpy as np
import pandas as pd
from datetime import datetime, timezone, timedelta


def test_vwap_analytical_calculation():
    from engine.vwap_bands import calculate_session_vwap_bands

    # 3 bars on the same day
    t0 = datetime(2026, 11, 19, 10, 0, tzinfo=timezone.utc)
    times = [t0, t0 + timedelta(minutes=5), t0 + timedelta(minutes=10)]

    # Bar 1: P = 9, Vol = 100
    # Bar 2: P = 11, Vol = 100 -> CumVol=200, VWAP=10, Var=1, Std=1
    # Bar 3: P = 13, Vol = 200 -> CumVol=400, VWAP = (900 + 1100 + 2600)/400 = 4600/400 = 11.5
    #   Var = (100*(9-11.5)^2 + 100*(11-11.5)^2 + 200*(13-11.5)^2) / 400
    #       = (100*6.25 + 100*0.25 + 200*2.25) / 400 = (625 + 25 + 450) / 400 = 1100 / 400 = 2.75
    #   Std = sqrt(2.75) =~ 1.6583
    data = {
        "time": times,
        "high": [10.0, 12.0, 14.0],
        "low": [8.0, 10.0, 12.0],
        "close": [9.0, 11.0, 13.0],
        "open": [9.0, 11.0, 13.0],
        "tick_volume": [100, 100, 200],
    }
    df = pd.DataFrame(data)

    res = calculate_session_vwap_bands(df, anchor_mode="utc")
    assert "vwap" in res.columns
    assert "vwap_p1" in res.columns
    assert "vwap_p2" in res.columns
    assert "vwap_m1" in res.columns
    assert "vwap_m2" in res.columns

    # Bar 2 verification
    assert pytest.approx(res["vwap"].iloc[1], 1e-4) == 10.0
    assert pytest.approx(res["vwap_p1"].iloc[1], 1e-4) == 11.0
    assert pytest.approx(res["vwap_p2"].iloc[1], 1e-4) == 12.0
    assert pytest.approx(res["vwap_m1"].iloc[1], 1e-4) == 9.0
    assert pytest.approx(res["vwap_m2"].iloc[1], 1e-4) == 8.0

    # Bar 3 verification
    assert pytest.approx(res["vwap"].iloc[2], 1e-4) == 11.5
    expected_std = np.sqrt(2.75)
    assert pytest.approx(res["vwap_p1"].iloc[2], 1e-4) == 11.5 + expected_std
    assert pytest.approx(res["vwap_m1"].iloc[2], 1e-4) == 11.5 - expected_std


def test_session_reset():
    from engine.vwap_bands import calculate_session_vwap_bands

    # Day 1: 23:55 UTC
    # Day 2: 00:00 UTC (reset should occur)
    t1 = datetime(2026, 11, 19, 23, 55, tzinfo=timezone.utc)
    t2 = datetime(2026, 11, 20, 0, 0, tzinfo=timezone.utc)

    df = pd.DataFrame({
        "time": [t1, t2],
        "high": [100.0, 50.0],
        "low": [98.0, 48.0],
        "close": [99.0, 49.0],
        "open": [99.0, 49.0],
        "tick_volume": [1000, 500],
    })

    res = calculate_session_vwap_bands(df, anchor_mode="utc")
    # Day 2 bar 0 should reset: VWAP = (50+48+49)/3 = 49.0
    assert pytest.approx(res["vwap"].iloc[1], 1e-4) == 49.0
    assert (res["vwap_p2"] >= res["vwap_p1"]).all()
    assert (res["vwap_p1"] >= res["vwap"]).all()
    assert (res["vwap"] >= res["vwap_m1"]).all()
    assert (res["vwap_m1"] >= res["vwap_m2"]).all()
