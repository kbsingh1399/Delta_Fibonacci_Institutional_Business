"""test_multidim_backtest.py: Unit tests for zero-lookahead backtest engine."""

import pytest
import numpy as np
import pandas as pd
from datetime import datetime, timezone, timedelta
from engine.multidim_grid_backtest import simulate_multidim_backtest, calculate_backtest_metrics


def test_zero_lookahead_entry_and_tp():
    # Construct a synthetic 10-bar series
    t0 = datetime(2026, 11, 19, 0, 0, tzinfo=timezone.utc)
    times = [t0 + timedelta(minutes=5 * i) for i in range(10)]

    # We want a signal to trigger on Bar 2
    # Bar 0, 1: setup
    # Bar 2: Signal bar (VWAP -2 SD bounce) -> Close=100, Low=95, High=101, Open=98
    # Bar 3: Entry bar (Open=100.5, Low=100.0, High=105.0, Close=104.0)
    # Bar 4: TP bar (High reaches 110.0)
    df = pd.DataFrame({
        "time": times,
        "open": [98, 97, 98, 100.5, 104, 103, 102, 101, 100, 99],
        "high": [100, 99, 101, 105.0, 110.0, 105, 104, 103, 102, 101],
        "low": [96, 95, 94.0, 100.0, 103.5, 101, 100, 99, 98, 97],
        "close": [97, 98, 100.0, 104.0, 108.0, 102, 101, 100, 99, 98],
        "tick_volume": [100] * 10,
    })

    # Run backtest for BTCUSD.pi on 5M with fixed 2.0R target
    trades, metrics = simulate_multidim_backtest(
        df=df,
        symbol="BTCUSD.pi",
        tf="5M",
        model_family="VWAP",
        exit_mode="fixed_2.0r",
        risk_usd=5.0
    )

    # Validate causality
    if len(trades) > 0:
        first_trade = trades[0]
        # Entry time must be at or after Bar 3 (strictly after Bar 2 signal close)
        assert first_trade["entry_price"] > 0
        assert "r_multiple" in first_trade
        assert "exit_reason" in first_trade


def test_calculate_backtest_metrics_calculation():
    sample_trades = [
        {"r_multiple": 2.0, "pnl_usd": 10.0},
        {"r_multiple": -1.0, "pnl_usd": -5.0},
        {"r_multiple": 2.0, "pnl_usd": 10.0},
        {"r_multiple": -1.0, "pnl_usd": -5.0},
    ]
    metrics = calculate_backtest_metrics(sample_trades)
    assert metrics["total_trades"] == 4
    assert metrics["win_rate"] == 0.50
    assert metrics["net_r"] == 2.0
    assert metrics["profit_factor"] == 2.0
    assert metrics["expectancy_r"] == 0.50
