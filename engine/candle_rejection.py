"""engine/candle_rejection.py: Candle wick rejection geometry and signal detection
for VWAP Standard Deviation Bands (-2, -1, 0, +1, +2) and Multi-Timeframe EMAs.
"""

from typing import Tuple, Dict, Any


def detect_rejection_wick(
    open_p: float,
    high_p: float,
    low_p: float,
    close_p: float,
    min_wick_ratio: float = 0.35
) -> Tuple[bool, bool, float, float]:
    """Evaluates whether candle exhibits a structural rejection wick (hammer/pinbar/star).

    Returns:
    - (is_bullish_wick, is_bearish_wick, lower_wick_ratio, upper_wick_ratio)
    """
    rng = max(1e-6, high_p - low_p)
    body_top = max(open_p, close_p)
    body_bot = min(open_p, close_p)

    lower_wick = (body_bot - low_p) / rng
    upper_wick = (high_p - body_top) / rng

    # Bullish rejection: lower wick is large, candle closes in top 65% of range
    is_bullish = (lower_wick >= min_wick_ratio) and ((close_p - low_p) / rng >= 0.35)

    # Bearish rejection: upper wick is large, candle closes in bottom 65% of range
    is_bearish = (upper_wick >= min_wick_ratio) and ((high_p - close_p) / rng >= 0.35)

    return is_bullish, is_bearish, lower_wick, upper_wick


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
    """Evaluates VWAP Standard Deviation Band rejection rules:
    - Below 0 SD & rejects 0 SD from below -> SHORT
    - Below -1 SD & rejects -1 SD from below -> SHORT
    - Above/Sweeping -2 SD & rejects -2 SD from above -> LONG
    - Above 0 SD & rejects 0 SD from above -> LONG
    - Above +1 SD & rejects +1 SD from above -> LONG
    - Below/Sweeping +2 SD & rejects +2 SD from below -> SHORT
    """
    is_bull, is_bear, lower_ratio, upper_ratio = detect_rejection_wick(
        open_p, high_p, low_p, close_p, min_wick_ratio
    )
    tol = max(1e-5, 0.15 * atr)

    # 1. VWAP -2 SD Long Bounce (Extreme Mean-Reversion / Liquidity Sweep)
    if is_bull and (low_p <= vwap_m2 + tol) and (close_p >= vwap_m2):
        return {
            "side": 1,
            "setup": "VWAP_M2_LONG",
            "level": vwap_m2,
            "wick_ratio": lower_ratio,
        }

    # 2. VWAP +2 SD Short Rejection (Extreme Mean-Reversion / Liquidity Sweep)
    if is_bear and (high_p >= vwap_p2 - tol) and (close_p <= vwap_p2):
        return {
            "side": -1,
            "setup": "VWAP_P2_SHORT",
            "level": vwap_p2,
            "wick_ratio": upper_ratio,
        }

    # 3. VWAP 0 SD Short (Mean Rejection downwards)
    if is_bear and (prev_close <= vwap + tol) and (close_p <= vwap) and (high_p >= vwap - tol):
        return {
            "side": -1,
            "setup": "VWAP_0_SHORT",
            "level": vwap,
            "wick_ratio": upper_ratio,
        }

    # 4. VWAP -1 SD Short (Breakdown Continuation downwards)
    if is_bear and (prev_close <= vwap_m1 + tol) and (close_p <= vwap_m1) and (high_p >= vwap_m1 - tol):
        return {
            "side": -1,
            "setup": "VWAP_M1_SHORT",
            "level": vwap_m1,
            "wick_ratio": upper_ratio,
        }

    # 5. VWAP 0 SD Long (Mean Support bounce upwards)
    if is_bull and (prev_close >= vwap - tol) and (close_p >= vwap) and (low_p <= vwap + tol):
        return {
            "side": 1,
            "setup": "VWAP_0_LONG",
            "level": vwap,
            "wick_ratio": lower_ratio,
        }

    # 6. VWAP +1 SD Long (Breakout Continuation upwards)
    if is_bull and (prev_close >= vwap_p1 - tol) and (close_p >= vwap_p1) and (low_p <= vwap_p1 + tol):
        return {
            "side": 1,
            "setup": "VWAP_P1_LONG",
            "level": vwap_p1,
            "wick_ratio": lower_ratio,
        }

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
    """Evaluates EMA 'Kiss and Bounce' rejection rules."""
    is_bull, is_bear, lower_ratio, upper_ratio = detect_rejection_wick(
        open_p, high_p, low_p, close_p, min_wick_ratio
    )
    tol = max(1e-5, 0.15 * atr)

    # Bullish EMA Bounce (Support test in uptrend or flat)
    if is_bull and (low_p <= ema_val + tol) and (close_p >= ema_val):
        if ema_val >= ema_prev * 0.9995:  # Non-declining slope
            return {
                "side": 1,
                "setup": f"EMA{ema_period}_BOUNCE",
                "level": ema_val,
                "wick_ratio": lower_ratio,
            }

    # Bearish EMA Rejection (Resistance test in downtrend or flat)
    if is_bear and (high_p >= ema_val - tol) and (close_p <= ema_val):
        if ema_val <= ema_prev * 1.0005:  # Non-ascending slope
            return {
                "side": -1,
                "setup": f"EMA{ema_period}_REJECTION",
                "level": ema_val,
                "wick_ratio": upper_ratio,
            }

    return {"side": 0, "setup": "NONE", "level": 0.0, "wick_ratio": 0.0}
