"""test_pattern_variants.py: Unit tests for Multi-Variant Candle Rejection Patterns:
1. Single-Bar Pinbar Kiss
2. Two-Bar Rejection / Engulfing Pair
3. Multi-Bar Cluster Dwell & Breakaway
"""

import pytest
import numpy as np
import pandas as pd
from engine.candle_rejection import (
    detect_rejection_wick,
    detect_two_bar_reversal,
    detect_cluster_stall_breakaway,
    evaluate_level_rejection
)


def test_two_bar_reversal_bearish():
    # Bar 1 (Bullish push into resistance at 100): Open=98, High=100.2, Low=97.8, Close=99.8
    # Bar 2 (Bearish rejection): Open=99.9, High=100.3, Low=98.0, Close=98.2 (closes well below midpoint of Bar 1)
    o = np.array([95, 96, 98.0, 99.9])
    h = np.array([96, 97, 100.2, 100.3])
    l = np.array([94, 95, 97.8, 98.0])
    c = np.array([95.5, 96.5, 99.8, 98.2])
    atr = np.array([1.5, 1.5, 1.5, 1.5])

    res = detect_two_bar_reversal(o, h, l, c, atr, idx=3, level=100.0, side_bias=-1)
    assert res["detected"] is True
    assert res["pattern"] == "TWO_BAR_BEARISH"
    assert res["side"] == -1
    # Structural stop is above highest high of the 2 bars (100.3)
    assert res["structural_sl"] > 100.3


def test_two_bar_reversal_bullish():
    # Bar 1 (Bearish flush into support at 50): Open=52, High=52.2, Low=49.8, Close=50.2
    # Bar 2 (Bullish bounce): Open=50.1, High=52.0, Low=49.7, Close=51.8 (closes above midpoint of Bar 1)
    o = np.array([54, 53, 52.0, 50.1])
    h = np.array([55, 54, 52.2, 52.0])
    l = np.array([53, 52, 49.8, 49.7])
    c = np.array([53.5, 52.5, 50.2, 51.8])
    atr = np.array([1.0, 1.0, 1.0, 1.0])

    res = detect_two_bar_reversal(o, h, l, c, atr, idx=3, level=50.0, side_bias=1)
    assert res["detected"] is True
    assert res["pattern"] == "TWO_BAR_BULLISH"
    assert res["side"] == 1
    # Structural stop is below lowest low of the 2 bars (49.7)
    assert res["structural_sl"] < 49.7


def test_cluster_stall_breakaway_bearish():
    # Price dwells at 100.0 for bars 1, 2, 3 and breaks down on bar 4
    # Level = 100.0
    o = np.array([96, 99.0, 99.5, 99.2, 98.8])
    h = np.array([97, 100.2, 100.1, 100.2, 99.0])
    l = np.array([95, 98.5, 98.6, 98.5, 97.0])  # Bar 4 breaks low of 98.5
    c = np.array([96.5, 99.4, 99.1, 98.9, 97.2])
    atr = np.array([1.5, 1.5, 1.5, 1.5, 1.5])

    res = detect_cluster_stall_breakaway(o, h, l, c, atr, idx=4, level=100.0, side_bias=-1)
    assert res["detected"] is True
    assert res["pattern"] == "CLUSTER_STALL_BEARISH"
    assert res["side"] == -1
    assert res["structural_sl"] > 100.2
