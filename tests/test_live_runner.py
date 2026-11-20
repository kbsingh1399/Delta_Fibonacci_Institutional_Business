"""tests/test_live_runner.py: Unit tests for the live multi-criteria execution runner.
Verifies basket loading, candle close detection logic, magic number isolation,
and minimum lot order structure without sending live orders.
"""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import numpy as np
import pandas as pd
from engine import live_multicriteria_runner as live
from engine.mt5_multicriteria_trader import (
    generate_setup_magic,
    calculate_position_lot_size,
    evaluate_live_setup_signal
)
from engine.live_multicriteria_runner import (
    calculate_trailing_stop_update,
    is_in_rollover_window,
    is_spread_acceptable,
    classify_spread_execution_mode,
    calculate_adaptive_lot_size
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
            if "mc_p_profit" in s:
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


def test_trailing_stop_breakeven_lock_buy():
    # Buy at 100.0, risk_dist 2.0 (initial SL 98.0)
    # Below 1.0R (e.g. 101.5 = 0.75R) -> None
    res = calculate_trailing_stop_update(
        side=1, entry_price=100.0, current_sl=98.0,
        mfe_price=101.5, risk_dist=2.0, exit_mode="trail_0.5r", digits=2
    )
    assert res is None

    # At 1.0R (102.0) -> Breakeven +0.05R = 100.10
    res = calculate_trailing_stop_update(
        side=1, entry_price=100.0, current_sl=98.0,
        mfe_price=102.0, risk_dist=2.0, exit_mode="trail_0.5r", digits=2
    )
    assert res == 100.10


def test_trailing_stop_trail_step_buy():
    # Buy at 100.0, risk_dist 2.0, exit_mode trail_0.5r
    # MFE reaches 104.0 (2.0R) -> trail_level = 100 + (2.0 - 0.5)*2.0 = 103.0
    res = calculate_trailing_stop_update(
        side=1, entry_price=100.0, current_sl=100.10,
        mfe_price=104.0, risk_dist=2.0, exit_mode="trail_0.5r", digits=2
    )
    assert res == 103.0


def test_trailing_stop_breakeven_and_trail_sell():
    # Sell at 100.0, risk_dist 2.0 (initial SL 102.0)
    # At 1.0R (mfe=98.0) -> Breakeven -0.05R = 99.90
    res_be = calculate_trailing_stop_update(
        side=-1, entry_price=100.0, current_sl=102.0,
        mfe_price=98.0, risk_dist=2.0, exit_mode="trail_0.5r", digits=2
    )
    assert res_be == 99.90

    # At 2.0R (mfe=96.0) -> trail_level = 100 - (2.0 - 0.5)*2.0 = 97.0
    res_trail = calculate_trailing_stop_update(
        side=-1, entry_price=100.0, current_sl=99.90,
        mfe_price=96.0, risk_dist=2.0, exit_mode="trail_0.5r", digits=2
    )
    assert res_trail == 97.0


def test_trailing_stop_ignores_fixed_exit():
    res = calculate_trailing_stop_update(
        side=1, entry_price=100.0, current_sl=98.0,
        mfe_price=106.0, risk_dist=2.0, exit_mode="fixed_3.5r", digits=2
    )
    assert res is None


def test_rollover_window_detection():
    from datetime import datetime, timezone
    # 21:50 UTC -> outside
    dt1 = datetime(2026, 11, 20, 21, 50, tzinfo=timezone.utc)
    assert not is_in_rollover_window(dt1)

    # 22:00 UTC -> inside
    dt2 = datetime(2026, 11, 20, 22, 0, tzinfo=timezone.utc)
    assert is_in_rollover_window(dt2)

    # 22:15 UTC -> inside
    dt3 = datetime(2026, 11, 20, 22, 15, tzinfo=timezone.utc)
    assert is_in_rollover_window(dt3)

    # 22:20 UTC -> outside
    dt4 = datetime(2026, 11, 20, 22, 20, tzinfo=timezone.utc)
    assert not is_in_rollover_window(dt4)


def test_spread_acceptable_calibration():
    # Stop distance 0.40 (e.g. USWTI)
    risk_dist = 0.40

    # Normal market spread 0.046 -> 0.046 / 0.40 = 0.115R <= 0.12R -> Accept
    assert is_spread_acceptable(spread=0.046, risk_dist=risk_dist, max_ratio=0.12)

    # Blowout spread 0.060 -> 0.060 / 0.40 = 0.150R > 0.12R -> Reject
    assert not is_spread_acceptable(spread=0.060, risk_dist=risk_dist, max_ratio=0.12)

    # Zero or negative risk_dist -> Reject
    assert not is_spread_acceptable(spread=0.01, risk_dist=0.0, max_ratio=0.12)


def test_classify_spread_execution_mode():
    risk_dist = 0.40  # 40 cent stop (e.g. USWTI)

    # 1. MARKET Mode: spread <= 0.12R (0.048)
    assert classify_spread_execution_mode(spread=0.030, risk_dist=risk_dist) == "MARKET"
    assert classify_spread_execution_mode(spread=0.048, risk_dist=risk_dist) == "MARKET"

    # 2. LIMIT Mode: 0.12R < spread <= 0.30R (0.048 < spread <= 0.120)
    assert classify_spread_execution_mode(spread=0.050, risk_dist=risk_dist) == "LIMIT"
    assert classify_spread_execution_mode(spread=0.080, risk_dist=risk_dist) == "LIMIT"
    assert classify_spread_execution_mode(spread=0.120, risk_dist=risk_dist) == "LIMIT"

    # 3. REJECT Mode: spread > 0.30R (> 0.120) or risk_dist <= 0
    assert classify_spread_execution_mode(spread=0.130, risk_dist=risk_dist) == "REJECT"
    assert classify_spread_execution_mode(spread=0.250, risk_dist=risk_dist) == "REJECT"
    assert classify_spread_execution_mode(spread=0.010, risk_dist=0.0) == "REJECT"


def test_adaptive_lot_sizing_winning_asset_and_champion():
    class MockSymbolInfo:
        volume_min = 0.01
        volume_step = 0.01
        volume_max = 10.0

    class MockAccount:
        margin_free = 2200.0

    # Prime Champion setup EURUSD.pi
    info = MockSymbolInfo()
    account = MockAccount()

    # For EURUSD (Forex), mock margin calculation returns low margin (~$73)
    import MetaTrader5 as mt5
    original_calc = mt5.order_calc_margin
    try:
        mt5.order_calc_margin = lambda o_type, sym, vol, price: 73.0 if vol == 0.02 else 36.5
        lot, reason = calculate_adaptive_lot_size(
            symbol="EURUSD.pi",
            tf="15M",
            setup_name="VWAP_P1_LONG",
            info=info,
            account=account,
            order_type=0,
            entry_price=1.0500,
            risk_dist=0.0015
        )
        assert lot == 0.02
        assert "Slightly Scaled" in reason
    finally:
        mt5.order_calc_margin = original_calc


def test_adaptive_lot_sizing_high_margin_safety_cap():
    class MockSymbolInfo:
        volume_min = 0.01
        volume_step = 0.01
        volume_max = 10.0

    class MockAccount:
        margin_free = 2200.0

    info = MockSymbolInfo()
    account = MockAccount()

    import MetaTrader5 as mt5
    original_calc = mt5.order_calc_margin
    try:
        # Mock high margin (e.g. Gold or Indices > $150)
        mt5.order_calc_margin = lambda o_type, sym, vol, price: 700.0 if vol == 0.02 else 350.0
        lot, reason = calculate_adaptive_lot_size(
            symbol="XAUUSD.pi",
            tf="5M",
            setup_name="EMA200_PINBAR_KISS",
            info=info,
            account=account,
            order_type=0,
            entry_price=2650.0,
            risk_dist=2.5
        )
        # Must safely reject scaling and stay at 0.01 base!
        assert lot == 0.01
        assert "Margin Gate" in reason
    finally:
        mt5.order_calc_margin = original_calc


def test_adaptive_lot_sizing_unranked_setup():
    class MockSymbolInfo:
        volume_min = 0.01
        volume_step = 0.01
        volume_max = 10.0

    class MockAccount:
        margin_free = 2200.0

    info = MockSymbolInfo()
    account = MockAccount()

    lot, reason = calculate_adaptive_lot_size(
        symbol="USWTI.p",
        tf="5M",
        setup_name="SOME_UNRANKED_SETUP",
        info=info,
        account=account,
        order_type=0,
        entry_price=70.0,
        risk_dist=0.50
    )
    assert lot == 0.01
    assert "Standard Base Minimum" in reason



@pytest.mark.parametrize("risk,margin,should_send", [(5.0, 20.0, True), (7.0, 20.0, False), (5.0, None, False)])
def test_pending_placement_risk_and_ack(tmp_path, monkeypatch, risk, margin, should_send):
    import MetaTrader5 as mt5

    login = 12345
    candle_time = 1700000000
    broker = SimpleNamespace(**{name: getattr(mt5, name) for name in dir(mt5) if name.isupper()})
    for name in ("positions_get", "orders_get", "symbol_info_tick", "symbol_info", "account_info",
                 "order_calc_margin", "order_calc_profit", "order_send"):
        setattr(broker, name, Mock())
    broker.positions_get.return_value = ()
    broker.orders_get.return_value = ()
    broker.symbol_info_tick.return_value = SimpleNamespace(time=candle_time + 10, bid=99.8, ask=100.0)
    broker.symbol_info.return_value = SimpleNamespace(
        visible=True, digits=2, volume_min=0.01, volume_max=1.0, volume_step=0.01
    )
    broker.account_info.return_value = SimpleNamespace(
        login=login, currency="USD", margin_free=2000.0, margin_mode=mt5.ACCOUNT_MARGIN_MODE_RETAIL_HEDGING
    )
    broker.order_calc_margin.return_value = margin
    broker.order_calc_profit.return_value = -risk
    broker.order_send.return_value = SimpleNamespace(retcode=mt5.TRADE_RETCODE_PLACED, order=42)
    monkeypatch.setattr(live, "mt5", broker)
    monkeypatch.setattr(live, "LEDGER_FILE", tmp_path / "execution.csv")
    monkeypatch.setattr(live, "TRAIL_STATE_FILE", tmp_path / "trail.json")
    monkeypatch.setattr(live, "check_kaizen_quarantine", lambda *args: (False, ""))
    monkeypatch.setattr(live, "is_in_rollover_window", lambda: False)
    monkeypatch.setattr(live, "get_adaptive_sizing_multiplier", lambda *args: (1.0, "base"))
    monkeypatch.setattr(live, "evaluate_live_setup_signal", lambda *args: {
        "side": 1, "setup": "EMA20_BOUNCE", "risk_dist": 1.0
    })
    runner = live.MT5MultiCriteriaLiveRunner(account_login=login, max_stop_risk_usd=6.0)
    monkeypatch.setattr(runner, "fetch_rates_dataframe", lambda *args, **kwargs: pd.DataFrame({
        "time": pd.to_datetime([candle_time - 300, candle_time], unit="s", utc=True),
        "close": [100.0, 100.0],
    }))
    setup = {"tf": "5M", "model": "EMA", "param": "EMA_20", "exit_mode": "trail_0.5r"}
    runner.check_and_execute_setup("EURUSD.pi", setup)
    if should_send:
        broker.order_send.assert_called_once()
        request = broker.order_send.call_args.args[0]
        assert request["type_filling"] == mt5.ORDER_FILLING_RETURN
        row = runner.read_execution_ledger()[42]
        assert row["status"] == "PENDING_LIMIT"
        assert row["magic"] == request["magic"]
    else:
        broker.order_send.assert_not_called()
        assert runner.read_execution_ledger() == {}


def test_management_requires_exact_ledger_and_broker_identity():
    row = {"symbol": "EURUSD.pi", "magic": 23456789}
    ledger = {101: row}
    position = SimpleNamespace(
        ticket=202, identifier=101, comment="Alpha_VWAP_M2_LONG", symbol="EURUSD.pi", magic=23456789
    )
    assert live.MT5MultiCriteriaLiveRunner.owned_execution(position, ledger, position=True) is row
    assert live.MT5MultiCriteriaLiveRunner.owned_execution(position, {}, position=True) is None
    position.magic = 42001
    assert live.MT5MultiCriteriaLiveRunner.owned_execution(position, ledger, position=True) is None
