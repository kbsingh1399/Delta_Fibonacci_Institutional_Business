"""engine/candle_rejection.py: Comprehensive candle rejection pattern engine
including Single-Bar Pinbar Kiss, Two-Bar Reversal Pairs, and Multi-Bar Cluster Dwell/Breakaways.
"""

from typing import Tuple, Dict, Any, List
import numpy as np


def detect_rejection_wick(
    open_p: float,
    high_p: float,
    low_p: float,
    close_p: float,
    min_wick_ratio: float = 0.35
) -> Tuple[bool, bool, float, float]:
    """Evaluates whether single candle exhibits a structural rejection wick (hammer/pinbar/star)."""
    rng = max(1e-6, high_p - low_p)
    body_top = max(open_p, close_p)
    body_bot = min(open_p, close_p)

    lower_wick = (body_bot - low_p) / rng
    upper_wick = (high_p - body_top) / rng

    is_bullish = (lower_wick >= min_wick_ratio) and ((close_p - low_p) / rng >= 0.35)
    is_bearish = (upper_wick >= min_wick_ratio) and ((high_p - close_p) / rng >= 0.35)

    return is_bullish, is_bearish, lower_wick, upper_wick


def detect_two_bar_reversal(
    o: np.ndarray, h: np.ndarray, l: np.ndarray, c: np.ndarray, atr: np.ndarray,
    idx: int, level: float, side_bias: int
) -> Dict[str, Any]:
    """Evaluates 2-bar rejection pair (tweezer / engulfing reversal off level)."""
    if idx < 1:
        return {"detected": False}

    tol = max(1e-5, 0.20 * atr[idx])

    if side_bias == -1:
        # Bearish: Bar 1 tested level, Bar 2 rejects with bearish close below midpoint of Bar 1
        bar1_tested = h[idx - 1] >= (level - tol)
        bar2_tested = h[idx] >= (level - tol) and c[idx] <= level
        bar2_bearish = c[idx] < o[idx]
        bar1_midpoint = (o[idx - 1] + c[idx - 1]) / 2.0
        closed_lower = c[idx] < bar1_midpoint

        if (bar1_tested or bar2_tested) and bar2_bearish and closed_lower and c[idx] <= level:
            struct_sl = max(h[idx - 1], h[idx]) + (0.20 * atr[idx])
            return {
                "detected": True,
                "pattern": "TWO_BAR_BEARISH",
                "side": -1,
                "structural_sl": struct_sl,
                "risk_dist": max(struct_sl - c[idx], 1.2 * atr[idx]),
            }

    elif side_bias == 1:
        # Bullish: Bar 1 tested level, Bar 2 bounces with bullish close above midpoint of Bar 1
        bar1_tested = l[idx - 1] <= (level + tol)
        bar2_tested = l[idx] <= (level + tol) and c[idx] >= level
        bar2_bullish = c[idx] > o[idx]
        bar1_midpoint = (o[idx - 1] + c[idx - 1]) / 2.0
        closed_higher = c[idx] > bar1_midpoint

        if (bar1_tested or bar2_tested) and bar2_bullish and closed_higher and c[idx] >= level:
            struct_sl = min(l[idx - 1], l[idx]) - (0.20 * atr[idx])
            return {
                "detected": True,
                "pattern": "TWO_BAR_BULLISH",
                "side": 1,
                "structural_sl": struct_sl,
                "risk_dist": max(c[idx] - struct_sl, 1.2 * atr[idx]),
            }

    return {"detected": False}


def detect_cluster_stall_breakaway(
    o: np.ndarray, h: np.ndarray, l: np.ndarray, c: np.ndarray, atr: np.ndarray,
    idx: int, level: float, side_bias: int
) -> Dict[str, Any]:
    """Evaluates 3-to-4 bar accumulation/distribution stall and breakaway off level."""
    if idx < 3:
        return {"detected": False}

    tol = max(1e-5, 0.20 * atr[idx])
    cluster_h = h[idx - 3:idx]
    cluster_l = l[idx - 3:idx]
    cluster_c = c[idx - 3:idx]

    if side_bias == -1:
        # Bearish Distribution Stall: Cluster tests level, bar idx breaks below cluster low
        touches = sum(1 for k in range(3) if cluster_h[k] >= (level - tol))
        if touches >= 2 and max(cluster_c) <= (level + 0.15 * atr[idx]):
            cluster_min_low = min(cluster_l)
            if c[idx] < cluster_min_low and c[idx] <= level:
                struct_sl = max(max(cluster_h), h[idx]) + (0.20 * atr[idx])
                return {
                    "detected": True,
                    "pattern": "CLUSTER_STALL_BEARISH",
                    "side": -1,
                    "structural_sl": struct_sl,
                    "risk_dist": max(struct_sl - c[idx], 1.2 * atr[idx]),
                }

    elif side_bias == 1:
        # Bullish Accumulation Stall: Cluster tests level, bar idx breaks above cluster high
        touches = sum(1 for k in range(3) if cluster_l[k] <= (level + tol))
        if touches >= 2 and min(cluster_c) >= (level - 0.15 * atr[idx]):
            cluster_max_high = max(cluster_h)
            if c[idx] > cluster_max_high and c[idx] >= level:
                struct_sl = min(min(cluster_l), l[idx]) - (0.20 * atr[idx])
                return {
                    "detected": True,
                    "pattern": "CLUSTER_STALL_BULLISH",
                    "side": 1,
                    "structural_sl": struct_sl,
                    "risk_dist": max(c[idx] - struct_sl, 1.2 * atr[idx]),
                }

    return {"detected": False}


def detect_three_bar_sweep(
    o: np.ndarray, h: np.ndarray, l: np.ndarray, c: np.ndarray, atr: np.ndarray,
    idx: int, level: float, side_bias: int
) -> Dict[str, Any]:
    """Evaluates 3-bar liquidity sweep & false-breakout reversal off level.
    Captures stop-hunt beyond level followed by aggressive reclaim/engulfing.
    """
    if idx < 2:
        return {"detected": False}

    tol = max(1e-5, 0.20 * atr[idx])

    if side_bias == -1:
        swept = max(h[idx - 2], h[idx - 1], h[idx]) >= (level - tol)
        peak_above = max(h[idx - 1], h[idx]) >= level
        reclaimed = c[idx] <= level and c[idx] < o[idx]
        engulfed = c[idx] < min(o[idx - 1], c[idx - 1]) or (c[idx] < (h[idx - 1] + l[idx - 1]) / 2.0)

        if swept and peak_above and reclaimed and engulfed:
            struct_sl = max(h[idx - 2], h[idx - 1], h[idx]) + (0.20 * atr[idx])
            return {
                "detected": True,
                "pattern": "THREE_BAR_SWEEP_BEARISH",
                "side": -1,
                "structural_sl": struct_sl,
                "risk_dist": max(struct_sl - c[idx], 1.2 * atr[idx]),
            }

    elif side_bias == 1:
        swept = min(l[idx - 2], l[idx - 1], l[idx]) <= (level + tol)
        dip_below = min(l[idx - 1], l[idx]) <= level
        reclaimed = c[idx] >= level and c[idx] > o[idx]
        engulfed = c[idx] > max(o[idx - 1], c[idx - 1]) or (c[idx] > (h[idx - 1] + l[idx - 1]) / 2.0)

        if swept and dip_below and reclaimed and engulfed:
            struct_sl = min(l[idx - 2], l[idx - 1], l[idx]) - (0.20 * atr[idx])
            return {
                "detected": True,
                "pattern": "THREE_BAR_SWEEP_BULLISH",
                "side": 1,
                "structural_sl": struct_sl,
                "risk_dist": max(c[idx] - struct_sl, 1.2 * atr[idx]),
            }

    return {"detected": False}


def detect_double_tap_retest(
    o: np.ndarray, h: np.ndarray, l: np.ndarray, c: np.ndarray, atr: np.ndarray,
    idx: int, level: float, side_bias: int, min_gap: int = 3, max_gap: int = 15
) -> Dict[str, Any]:
    """Evaluates double-touch / re-test rejection off level.
    Captures initial touch, pullback confirmation, and secondary rejection hold.
    """
    if idx < max_gap:
        return {"detected": False}

    tol = max(1e-5, 0.20 * atr[idx])

    if side_bias == -1:
        curr_test = h[idx] >= (level - tol) and c[idx] <= level and c[idx] <= o[idx]
        if curr_test:
            for k in range(max(0, idx - max_gap), idx - min_gap):
                if h[k] >= (level - tol) and c[k] <= level:
                    valley = min(l[k + 1:idx])
                    if valley <= level - 0.40 * atr[idx]:
                        struct_sl = max(h[idx], h[k]) + (0.20 * atr[idx])
                        return {
                            "detected": True,
                            "pattern": "DOUBLE_TAP_BEARISH",
                            "side": -1,
                            "structural_sl": struct_sl,
                            "risk_dist": max(struct_sl - c[idx], 1.2 * atr[idx]),
                        }

    elif side_bias == 1:
        curr_test = l[idx] <= (level + tol) and c[idx] >= level and c[idx] >= o[idx]
        if curr_test:
            for k in range(max(0, idx - max_gap), idx - min_gap):
                if l[k] <= (level + tol) and c[k] >= level:
                    peak = max(h[k + 1:idx])
                    if peak >= level + 0.40 * atr[idx]:
                        struct_sl = min(l[idx], l[k]) - (0.20 * atr[idx])
                        return {
                            "detected": True,
                            "pattern": "DOUBLE_TAP_BULLISH",
                            "side": 1,
                            "structural_sl": struct_sl,
                            "risk_dist": max(c[idx] - struct_sl, 1.2 * atr[idx]),
                        }

    return {"detected": False}


def evaluate_level_rejection(
    o: np.ndarray, h: np.ndarray, l: np.ndarray, c: np.ndarray, atr: np.ndarray,
    idx: int, level: float, side_bias: int
) -> Dict[str, Any]:
    """Unified evaluator checking all 5 rejection variants at a target level:
    1. Single-Bar Pinbar Kiss
    2. Two-Bar Reversal Pair
    3. Three-Bar Liquidity Sweep & Reclaim
    4. Multi-Bar Cluster Dwell & Breakaway
    5. Double-Tap Re-Test Defense
    """
    tol = max(1e-5, 0.20 * atr[idx])
    open_p = o[idx]
    high_p = h[idx]
    low_p = l[idx]
    close_p = c[idx]

    # 1. Single-Bar Pinbar Check
    is_bull, is_bear, lower_ratio, upper_ratio = detect_rejection_wick(open_p, high_p, low_p, close_p)
    if side_bias == -1 and is_bear and (high_p >= level - tol) and (close_p <= level):
        struct_sl = high_p + (0.20 * atr[idx])
        return {
            "detected": True,
            "pattern": "PINBAR_KISS",
            "side": -1,
            "structural_sl": struct_sl,
            "risk_dist": max(struct_sl - close_p, 1.2 * atr[idx]),
        }
    elif side_bias == 1 and is_bull and (low_p <= level + tol) and (close_p >= level):
        struct_sl = low_p - (0.20 * atr[idx])
        return {
            "detected": True,
            "pattern": "PINBAR_KISS",
            "side": 1,
            "structural_sl": struct_sl,
            "risk_dist": max(close_p - struct_sl, 1.2 * atr[idx]),
        }

    # 2. Two-Bar Reversal Check
    res_2bar = detect_two_bar_reversal(o, h, l, c, atr, idx, level, side_bias)
    if res_2bar["detected"]:
        return res_2bar

    # 3. Three-Bar Liquidity Sweep Check
    res_sweep = detect_three_bar_sweep(o, h, l, c, atr, idx, level, side_bias)
    if res_sweep["detected"]:
        return res_sweep

    # 4. Cluster Stall Breakaway Check
    res_cluster = detect_cluster_stall_breakaway(o, h, l, c, atr, idx, level, side_bias)
    if res_cluster["detected"]:
        return res_cluster

    # 5. Double-Tap Re-Test Check
    res_double = detect_double_tap_retest(o, h, l, c, atr, idx, level, side_bias)
    if res_double["detected"]:
        return res_double

    return {"detected": False, "side": 0, "pattern": "NONE"}


def check_vwap_band_signal(
    open_p: float,
    high_p: float,
    low_p: float,
    close_p: float,
    prev_close: float,
    vwap: float,
    vwap_p1: float,
    vwap_p2: float,
    vwap_m1: float,
    vwap_m2: float,
    atr: float,
    min_wick_ratio: float = 0.35
) -> Dict[str, Any]:
    """Backwards-compatible VWAP Standard Deviation Band signal checker."""
    is_bull, is_bear, lower_ratio, upper_ratio = detect_rejection_wick(
        open_p, high_p, low_p, close_p, min_wick_ratio
    )
    tol = max(1e-5, 0.15 * atr)

    # 1. VWAP -2 SD Long Bounce
    if is_bull and (low_p <= vwap_m2 + tol) and (close_p >= vwap_m2):
        return {"side": 1, "setup": "VWAP_M2_LONG", "level": vwap_m2, "wick_ratio": lower_ratio}

    # 2. VWAP +2 SD Short Rejection
    if is_bear and (high_p >= vwap_p2 - tol) and (close_p <= vwap_p2):
        return {"side": -1, "setup": "VWAP_P2_SHORT", "level": vwap_p2, "wick_ratio": upper_ratio}

    # 3. VWAP 0 SD Short (Mean Rejection)
    if is_bear and (prev_close <= vwap + tol) and (close_p <= vwap) and (high_p >= vwap - tol):
        return {"side": -1, "setup": "VWAP_0_SHORT", "level": vwap, "wick_ratio": upper_ratio}

    # 4. VWAP -1 SD Short (Breakdown Continuation)
    if is_bear and (prev_close <= vwap_m1 + tol) and (close_p <= vwap_m1) and (high_p >= vwap_m1 - tol):
        return {"side": -1, "setup": "VWAP_M1_SHORT", "level": vwap_m1, "wick_ratio": upper_ratio}

    # 5. VWAP 0 SD Long (Mean Support Bounce)
    if is_bull and (prev_close >= vwap - tol) and (close_p >= vwap) and (low_p <= vwap + tol):
        return {"side": 1, "setup": "VWAP_0_LONG", "level": vwap, "wick_ratio": lower_ratio}

    # 6. VWAP +1 SD Long (Breakout Continuation)
    if is_bull and (prev_close >= vwap_p1 - tol) and (close_p >= vwap_p1) and (low_p <= vwap_p1 + tol):
        return {"side": 1, "setup": "VWAP_P1_LONG", "level": vwap_p1, "wick_ratio": lower_ratio}

    return {"side": 0, "setup": "NONE", "level": 0.0, "wick_ratio": 0.0}


def check_ema_rejection_signal(
    open_p: float,
    high_p: float,
    low_p: float,
    close_p: float,
    ema_val: float,
    ema_prev: float,
    atr: float,
    ema_period: int,
    min_wick_ratio: float = 0.35
) -> Dict[str, Any]:
    """Backwards-compatible EMA 'Kiss and Bounce' rejection checker."""
    is_bull, is_bear, lower_ratio, upper_ratio = detect_rejection_wick(
        open_p, high_p, low_p, close_p, min_wick_ratio
    )
    tol = max(1e-5, 0.15 * atr)

    # Bullish EMA Bounce
    if is_bull and (low_p <= ema_val + tol) and (close_p >= ema_val):
        if ema_val >= ema_prev * 0.9995:
            return {"side": 1, "setup": f"EMA{ema_period}_BOUNCE", "level": ema_val, "wick_ratio": lower_ratio}

    # Bearish EMA Rejection
    if is_bear and (high_p >= ema_val - tol) and (close_p <= ema_val):
        if ema_val <= ema_prev * 1.0005:
            return {"side": -1, "setup": f"EMA{ema_period}_REJECTION", "level": ema_val, "wick_ratio": upper_ratio}

    return {"side": 0, "setup": "NONE", "level": 0.0, "wick_ratio": 0.0}
