"""test_multicriteria_dispatch.py: Unit tests for Multi-Criteria concurrent execution dispatcher."""

import pytest
import os
import json
from engine.mt5_multicriteria_trader import (
    generate_setup_magic,
    calculate_position_lot_size,
    evaluate_live_setup_signal,
)


def test_unique_magic_number_generation():
    # Different timeframes and setups for the same symbol must produce distinct magic numbers
    m1 = generate_setup_magic("BTCUSD.pi", "5M", "VWAP_M2_LONG")
    m2 = generate_setup_magic("BTCUSD.pi", "15M", "EMA50_BOUNCE")
    m3 = generate_setup_magic("BTCUSD.pi", "1H", "EMA100_BOUNCE")
    m4 = generate_setup_magic("ETHUSD.pi", "5M", "VWAP_M2_LONG")

    assert len({m1, m2, m3, m4}) == 4, "Magic numbers must be distinct for independent multi-criteria tracking"
    for m in [m1, m2, m3, m4]:
        assert isinstance(m, int)
        assert 100000 <= m <= 99999999


def test_position_lot_size_calculation():
    # $5.00 target risk, risk_dist = 100 points, tick_value = 1.0, tick_size = 1.0
    lots = calculate_position_lot_size(
        risk_usd=5.0,
        risk_dist=100.0,
        tick_value=1.0,
        tick_size=1.0,
        min_lot=0.01,
        max_lot=10.0,
        lot_step=0.01
    )
    # 5.0 / (100 * 1.0) = 0.05 lots
    assert pytest.approx(lots, 0.001) == 0.05

    # Fallback to min_lot if risk distance is large
    lots_min = calculate_position_lot_size(
        risk_usd=5.0,
        risk_dist=2000.0,
        tick_value=1.0,
        tick_size=1.0,
        min_lot=0.01,
        max_lot=10.0,
        lot_step=0.01
    )
    assert lots_min == 0.01
