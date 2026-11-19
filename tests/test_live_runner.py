"""tests/test_live_runner.py: Unit tests for the live multi-criteria execution runner.
Verifies basket loading, candle close detection logic, magic number isolation,
and minimum lot order structure without sending live orders.
"""

import pytest
import numpy as np
import pandas as pd
from engine.mt5_multicriteria_trader import (
    generate_setup_magic,
    calculate_position_lot_size,
    evaluate_live_setup_signal
)
from audit.EXPANDED_OPTIMAL_ALPHA_BASKET import ROBUST_ALPHA_BASKET


def test_robust_alpha_basket_integrity():
    assert len(ROBUST_ALPHA_BASKET) >= 15
    for sym, setups in ROBUST_ALPHA_BASKET.items():
        assert len(setups) >= 1
        for s in setups:
            assert s["tf"] in ["5M", "15M", "1H", "4H"]
            assert s["model"] in ["VWAP", "EMA"]
            assert s["oos_net_r"] > 0
            assert s["oos_pf"] >= 1.15
            assert s["mc_p_profit"] >= 0.90


def test_magic_number_isolation_across_timeframes():
    # Same symbol, different timeframe and setup -> must generate distinct magic numbers
    m1 = generate_setup_magic("USWTI.p", "5M", "EMA50_BOUNCE")
    m2 = generate_setup_magic("USWTI.p", "15M", "VWAP_M2_LONG")
    m3 = generate_setup_magic("SP500.p", "5M", "EMA50_BOUNCE")

    assert m1 != m2
    assert m1 != m3
    assert m2 != m3
    assert 20000000 <= m1 <= 89999999


def test_min_lot_order_sizing():
    # Min lot fallback verification
    lot = calculate_position_lot_size(
        risk_usd=5.0,
        risk_dist=2.5,
        tick_value=1.0,
        tick_size=0.1,
        min_lot=0.01,
        max_lot=10.0,
        lot_step=0.01
    )
    assert lot >= 0.01
    assert isinstance(lot, float)


def test_live_signal_evaluation_on_synthetic_data():
    # Generate 60 bars of synthetic data
    dates = pd.date_range("2026-11-19 12:00", periods=60, freq="5min", tz="UTC")
    closes = np.linspace(100, 110, 60)
    highs = closes + 0.5
    lows = closes - 0.5
    opens = closes - 0.1

    df = pd.DataFrame({
        "time": dates,
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": np.ones(60) * 100
    })

    sig = evaluate_live_setup_signal(df, "EURUSD.pi", "5M", "EMA", "EMA_50")
    assert isinstance(sig, dict)
    assert "side" in sig
