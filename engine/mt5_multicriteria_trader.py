"""engine/mt5_multicriteria_trader.py: Multi-Criteria concurrent execution dispatcher
for MT5 Blueberry Markets. Manages concurrent independent setups across 5M, 15M, 1H, 4H.
"""

import os
import sys
import json
import hashlib
from typing import Dict, Any, Optional
import numpy as np
import pandas as pd

from engine.vwap_bands import calculate_session_vwap_bands
from engine.candle_rejection import (
    check_vwap_band_signal,
    check_ema_rejection_signal,
    evaluate_level_rejection
)


def generate_setup_magic(symbol: str, tf: str, setup: str) -> int:
    """Generates a deterministic unique 8-digit MT5 Magic Number for a setup."""
    key = f"{symbol}_{tf}_{setup}".encode("utf-8")
    digest = hashlib.md5(key).hexdigest()
    num = int(digest[:8], 16)
    # Range 20000000 - 89999999
    return 20000000 + (num % 70000000)


def calculate_position_lot_size(
    risk_usd: float,
    risk_dist: float,
    tick_value: float,
    tick_size: float = 1.0,
    min_lot: float = 0.01,
    max_lot: float = 10.0,
    lot_step: float = 0.01
) -> float:
    """Calculates position volume strictly clamped to $5 target risk and broker limits."""
    if risk_dist <= 0 or tick_value <= 0 or tick_size <= 0:
        return min_lot

    points = risk_dist / tick_size
    risk_per_lot = points * tick_value
    if risk_per_lot <= 0:
        return min_lot

    raw_lots = risk_usd / risk_per_lot
    # Snap to broker lot step
    steps = round(raw_lots / lot_step)
    lots = steps * lot_step
    return float(np.clip(lots, min_lot, max_lot))


def evaluate_live_setup_signal(
    df: pd.DataFrame,
    symbol: str,
    tf: str,
    model: str,
    param: str
) -> Dict[str, Any]:
    """Evaluates live signal on the latest confirmed bar."""
    if len(df) < 50:
        return {"side": 0}

    # Use confirmed closed bar (index -2 if bar -1 is active unclosed)
    idx = len(df) - 2

    # Calculate ATR
    h = df["high"].to_numpy(dtype=float)
    l = df["low"].to_numpy(dtype=float)
    c = df["close"].to_numpy(dtype=float)
    o = df["open"].to_numpy(dtype=float)

    # Simplified 14-bar ATR on closed bars
    trs = [max(h[k] - l[k], abs(h[k] - c[k - 1]), abs(l[k] - c[k - 1])) for k in range(max(1, idx - 14), idx + 1)]
    atr = float(np.mean(trs)) if trs else 1.0

    is_crypto = any(k in symbol.upper() for k in ["BTC", "ETH", "XLM", "XRP", "ADA"])
    anchor = "utc" if is_crypto else "broker"

    if model == "VWAP":
        vwap_df = calculate_session_vwap_bands(df, anchor_mode=anchor)
        res = check_vwap_band_signal(
            open_p=o[idx], high_p=h[idx], low_p=l[idx], close_p=c[idx],
            prev_close=c[idx - 1],
            vwap=float(vwap_df["vwap"].iloc[idx]),
            vwap_p1=float(vwap_df["vwap_p1"].iloc[idx]),
            vwap_p2=float(vwap_df["vwap_p2"].iloc[idx]),
            vwap_m1=float(vwap_df["vwap_m1"].iloc[idx]),
            vwap_m2=float(vwap_df["vwap_m2"].iloc[idx]),
            atr=atr
        )
        if res.get("side", 0) == 0:
            vm2 = float(vwap_df["vwap_m2"].iloc[idx])
            vp2 = float(vwap_df["vwap_p2"].iloc[idx])
            v0 = float(vwap_df["vwap"].iloc[idx])
            tol = 0.20 * atr
            if l[idx] <= vm2 + tol:
                mv = evaluate_level_rejection(o, h, l, c, np.full(len(c), atr), idx, vm2, side_bias=1)
                if mv["detected"]:
                    return {"side": 1, "setup": f"VWAP_M2_{mv['pattern']}", "risk_dist": mv.get("risk_dist", 1.5 * atr)}
            elif h[idx] >= vp2 - tol:
                mv = evaluate_level_rejection(o, h, l, c, np.full(len(c), atr), idx, vp2, side_bias=-1)
                if mv["detected"]:
                    return {"side": -1, "setup": f"VWAP_P2_{mv['pattern']}", "risk_dist": mv.get("risk_dist", 1.5 * atr)}
            elif c[idx - 1] <= v0 + tol and h[idx] >= v0 - tol:
                mv = evaluate_level_rejection(o, h, l, c, np.full(len(c), atr), idx, v0, side_bias=-1)
                if mv["detected"]:
                    return {"side": -1, "setup": f"VWAP_0_{mv['pattern']}", "risk_dist": mv.get("risk_dist", 1.5 * atr)}
            elif c[idx - 1] >= v0 - tol and l[idx] <= v0 + tol:
                mv = evaluate_level_rejection(o, h, l, c, np.full(len(c), atr), idx, v0, side_bias=1)
                if mv["detected"]:
                    return {"side": 1, "setup": f"VWAP_0_{mv['pattern']}", "risk_dist": mv.get("risk_dist", 1.5 * atr)}
        return res
    elif model == "EMA":
        period = int(param.split("_")[1]) if "_" in param else 50
        alpha = 2.0 / (period + 1.0)
        ema_series = df["close"].ewm(alpha=alpha, adjust=False).mean()
        res = check_ema_rejection_signal(
            open_p=o[idx], high_p=h[idx], low_p=l[idx], close_p=c[idx],
            ema_val=float(ema_series.iloc[idx]),
            ema_prev=float(ema_series.iloc[idx - 1]),
            atr=atr,
            ema_period=period
        )
        if res.get("side", 0) == 0:
            eval_val = float(ema_series.iloc[idx])
            prev_val = float(ema_series.iloc[idx - 1])
            tol = 0.20 * atr
            if eval_val >= prev_val * 0.9995 and l[idx] <= eval_val + tol:
                mv = evaluate_level_rejection(o, h, l, c, np.full(len(c), atr), idx, eval_val, side_bias=1)
                if mv["detected"]:
                    return {"side": 1, "setup": f"EMA{period}_{mv['pattern']}", "risk_dist": mv.get("risk_dist", 1.5 * atr)}
            elif eval_val <= prev_val * 1.0005 and h[idx] >= eval_val - tol:
                mv = evaluate_level_rejection(o, h, l, c, np.full(len(c), atr), idx, eval_val, side_bias=-1)
                if mv["detected"]:
                    return {"side": -1, "setup": f"EMA{period}_{mv['pattern']}", "risk_dist": mv.get("risk_dist", 1.5 * atr)}
        return res

    return {"side": 0}
