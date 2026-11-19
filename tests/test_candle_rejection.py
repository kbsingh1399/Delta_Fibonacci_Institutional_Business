"""test_candle_rejection.py: Unit tests for price action 'kiss & bounce' candle rejection signals."""

import pytest
import numpy as np
import pandas as pd
from engine.candle_rejection import (
    detect_rejection_wick,
    check_vwap_band_signal,
    check_ema_rejection_signal,
)


def test_rejection_wick_detection():
    # Synthetic Pinbar / Hammer (Bullish Rejection)
    # Open=102, High=103, Low=95, Close=102.5 -> Range = 8, Lower wick = (102 - 95) = 7 -> 7/8 = 87.5%
    is_bull, is_bear, lower_ratio, upper_ratio = detect_rejection_wick(
        open_p=102.0, high_p=103.0, low_p=95.0, close_p=102.5, min_wick_ratio=0.35
    )
    assert is_bull is True
    assert is_bear is False
    assert pytest.approx(lower_ratio, 0.01) == 0.875
    assert pytest.approx(upper_ratio, 0.01) == 0.0625

    # Synthetic Shooting Star (Bearish Rejection)
    # Open=98, High=105, Low=97.5, Close=97.8 -> Range = 7.5, Upper wick = (105 - 98) = 7 -> 7/7.5 = 93.3%
    is_bull, is_bear, lower_ratio, upper_ratio = detect_rejection_wick(
        open_p=98.0, high_p=105.0, low_p=97.5, close_p=97.8, min_wick_ratio=0.35
    )
    assert is_bull is False
    assert is_bear is True
    assert pytest.approx(upper_ratio, 0.01) == 0.933


def test_vwap_m2_long_bounce():
    # Price tests -2 SD band at 95.0 with low=94.5 and closes above at 97.0
    sig = check_vwap_band_signal(
        open_p=96.5, high_p=97.2, low_p=94.5, close_p=97.0,
        prev_close=96.0,
        vwap=100.0, vwap_p1=102.5, vwap_p2=105.0, vwap_m1=97.5, vwap_m2=95.0,
        atr=2.0
    )
    # Should trigger VWAP_M2_LONG (+1)
    assert sig["side"] == 1
    assert sig["setup"] == "VWAP_M2_LONG"


def test_vwap_0_short_rejection():
    # Price is below VWAP (100.0), rallies to high=100.2 (kiss), rejects with upper wick, closes at 99.0
    sig = check_vwap_band_signal(
        open_p=99.2, high_p=100.2, low_p=98.8, close_p=99.0,
        prev_close=98.5,
        vwap=100.0, vwap_p1=102.5, vwap_p2=105.0, vwap_m1=97.5, vwap_m2=95.0,
        atr=1.5
    )
    assert sig["side"] == -1
    assert sig["setup"] == "VWAP_0_SHORT"


def test_ema_kiss_and_bounce():
    # EMA = 50.0. Price tests low=49.9, closes at 50.8 with lower wick ratio > 35%
    sig_long = check_ema_rejection_signal(
        open_p=50.5, high_p=51.0, low_p=49.9, close_p=50.8,
        ema_val=50.0, ema_prev=49.9,
        atr=1.0, ema_period=50
    )
    assert sig_long["side"] == 1
    assert sig_long["setup"] == "EMA50_BOUNCE"

    # Bearish EMA rejection
    sig_short = check_ema_rejection_signal(
        open_p=49.5, high_p=50.1, low_p=49.0, close_p=49.2,
        ema_val=50.0, ema_prev=50.1,
        atr=1.0, ema_period=50
    )
    assert sig_short["side"] == -1
    assert sig_short["setup"] == "EMA50_REJECTION"
