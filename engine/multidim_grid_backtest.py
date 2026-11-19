"""engine/multidim_grid_backtest.py: High-speed, zero-lookahead backtest engine
for Multi-Dimensional VWAP Standard Deviation Bands & Multi-Timeframe EMAs.
Features single-pass signal vectorization for sub-second parameter sweeps.
"""

from datetime import datetime, timezone
from typing import Dict, Any, List, Tuple, Optional
import numpy as np
import pandas as pd

from engine.vwap_bands import calculate_session_vwap_bands
from engine.candle_rejection import check_vwap_band_signal, check_ema_rejection_signal


def calculate_atr(h: np.ndarray, l: np.ndarray, c: np.ndarray, period: int = 14) -> np.ndarray:
    """Vectorized Average True Range."""
    n = len(c)
    atr = np.zeros(n, dtype=float)
    if n <= period:
        return atr

    tr = np.zeros(n, dtype=float)
    tr[0] = h[0] - l[0]
    for i in range(1, n):
        tr[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))

    atr[period - 1] = np.mean(tr[:period])
    for i in range(period, n):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
    return atr


def calculate_ema(c: np.ndarray, period: int) -> np.ndarray:
    """Vectorized Exponential Moving Average."""
    n = len(c)
    ema = np.zeros(n, dtype=float)
    if n < period:
        return ema

    ema[period - 1] = np.mean(c[:period])
    alpha = 2.0 / (period + 1.0)
    for i in range(period, n):
        ema[i] = alpha * c[i] + (1.0 - alpha) * ema[i - 1]
    return ema


def calculate_backtest_metrics(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Calculates institutional backtest metrics from executed trade list."""
    if not trades:
        return {
            "total_trades": 0, "win_rate": 0.0, "net_r": 0.0,
            "profit_factor": 0.0, "max_dd_r": 0.0, "expectancy_r": 0.0,
            "avg_win_r": 0.0, "avg_loss_r": 0.0, "total_pnl_usd": 0.0
        }

    r_mults = [t["r_multiple"] for t in trades]
    pnls = [t.get("pnl_usd", t["r_multiple"] * 5.0) for t in trades]

    wins = [r for r in r_mults if r > 0.05]
    losses = [r for r in r_mults if r < -0.05]

    total = len(r_mults)
    win_rate = len(wins) / total if total > 0 else 0.0
    net_r = round(float(np.sum(r_mults)), 2)

    gross_win = float(np.sum(wins)) if wins else 0.0
    gross_loss = abs(float(np.sum(losses))) if losses else 0.0
    profit_factor = round(gross_win / gross_loss, 2) if gross_loss > 0 else (99.0 if gross_win > 0 else 0.0)

    # Max Drawdown in R
    cum_r = np.cumsum(r_mults)
    running_max = np.maximum.accumulate(cum_r)
    dd_series = running_max - cum_r
    max_dd = round(float(np.max(dd_series)), 2) if len(dd_series) > 0 else 0.0

    expectancy = round(net_r / total, 2) if total > 0 else 0.0
    avg_win = round(float(np.mean(wins)), 2) if wins else 0.0
    avg_loss = round(float(np.mean(losses)), 2) if losses else 0.0

    return {
        "total_trades": total,
        "win_rate": round(win_rate, 4),
        "net_r": net_r,
        "profit_factor": profit_factor,
        "max_dd_r": max_dd,
        "expectancy_r": expectancy,
        "avg_win_r": avg_win,
        "avg_loss_r": avg_loss,
        "total_pnl_usd": round(float(np.sum(pnls)), 2),
    }


def precompute_signals_vector(
    o: np.ndarray, h: np.ndarray, l: np.ndarray, c: np.ndarray, atr: np.ndarray,
    model_family: str,
    vwap_vals: Optional[np.ndarray] = None,
    vwap_p1: Optional[np.ndarray] = None,
    vwap_p2: Optional[np.ndarray] = None,
    vwap_m1: Optional[np.ndarray] = None,
    vwap_m2: Optional[np.ndarray] = None,
    ema_vals: Optional[np.ndarray] = None,
    ema_period: int = 50,
    start_bar: int = 20
) -> Tuple[np.ndarray, List[str]]:
    """Generates candle rejection signal vectors in a single pass across the price array."""
    n = len(c)
    sig_sides = np.zeros(n, dtype=np.int8)
    sig_setups = [""] * n

    for i in range(start_bar, n - 1):
        prev_idx = i - 1 if i > 0 else 0
        if model_family == "VWAP":
            res = check_vwap_band_signal(
                open_p=o[i], high_p=h[i], low_p=l[i], close_p=c[i],
                prev_close=c[prev_idx],
                vwap=vwap_vals[i], vwap_p1=vwap_p1[i], vwap_p2=vwap_p2[i],
                vwap_m1=vwap_m1[i], vwap_m2=vwap_m2[i],
                atr=atr[i]
            )
        else:
            res = check_ema_rejection_signal(
                open_p=o[i], high_p=h[i], low_p=l[i], close_p=c[i],
                ema_val=ema_vals[i], ema_prev=ema_vals[prev_idx],
                atr=atr[i], ema_period=ema_period
            )

        if res["side"] != 0:
            sig_sides[i] = res["side"]
            sig_setups[i] = res["setup"]

    return sig_sides, sig_setups


def simulate_with_signals(
    times: Any, o: np.ndarray, h: np.ndarray, l: np.ndarray, c: np.ndarray, atr: np.ndarray,
    sig_sides: np.ndarray, sig_setups: List[str],
    symbol: str, tf: str, model_family: str,
    exit_mode: str = "fixed_2.0r",
    risk_usd: float = 5.0,
    cooldown_bars: int = 6,
    start_bar: int = 20
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Ultra-fast trade simulation using precomputed signal arrays."""
    n = len(c)
    commission_usd = 0.05
    trades = []
    in_pos = False
    pos_side = 0
    pos_entry = 0.0
    pos_sl = 0.0
    pos_tp = 0.0
    pos_rd = 1.0
    pos_mfe = 0.0
    entry_idx = 0
    cooldown_until_idx = 0

    # Parse exit mode parameters
    is_trail = "trail" in exit_mode
    if is_trail:
        trail_step = 0.5 if "0.5" in exit_mode else (1.0 if "1.0" in exit_mode else 1.5)
        target_rr = 6.0 if "0.5" in exit_mode or "1.0" in exit_mode else 8.0
    else:
        trail_step = 0.0
        if "4.0" in exit_mode:
            target_rr = 4.0
        elif "3.5" in exit_mode:
            target_rr = 3.5
        elif "3.0" in exit_mode:
            target_rr = 3.0
        elif "2.5" in exit_mode:
            target_rr = 2.5
        elif "2.0" in exit_mode:
            target_rr = 2.0
        elif "1.5" in exit_mode:
            target_rr = 1.5
        else:
            target_rr = 2.5

    for i in range(start_bar, n - 1):
        if in_pos:
            bar_h = h[i]
            bar_l = l[i]
            bullish = c[i] >= o[i]

            # Intrabar Adverse Sequencing
            seq = [("LOW", bar_l), ("HIGH", bar_h)] if bullish else [("HIGH", bar_h), ("LOW", bar_l)]
            exited = False
            exit_p = 0.0
            exit_reason = ""

            for touch_type, touch_price in seq:
                if pos_side == 1:
                    if touch_type == "LOW" and touch_price <= pos_sl:
                        exited = True
                        exit_p = pos_sl
                        exit_reason = "SL_HIT" if pos_sl < pos_entry else "TRAIL_HIT"
                        break
                    elif touch_type == "HIGH":
                        curr_r = (touch_price - pos_entry) / pos_rd
                        if curr_r > pos_mfe:
                            pos_mfe = curr_r
                        if pos_tp > 0 and touch_price >= pos_tp:
                            exited = True
                            exit_p = pos_tp
                            exit_reason = "TP_HIT"
                            break
                else:
                    if touch_type == "HIGH" and touch_price >= pos_sl:
                        exited = True
                        exit_p = pos_sl
                        exit_reason = "SL_HIT" if pos_sl > pos_entry else "TRAIL_HIT"
                        break
                    elif touch_type == "LOW":
                        curr_r = (pos_entry - touch_price) / pos_rd
                        if curr_r > pos_mfe:
                            pos_mfe = curr_r
                        if pos_tp > 0 and touch_price <= pos_tp:
                            exited = True
                            exit_p = pos_tp
                            exit_reason = "TP_HIT"
                            break

            # Trailing stop ratchet
            if not exited and is_trail:
                if pos_mfe >= 1.0:
                    be_level = pos_entry + (0.05 * pos_rd if pos_side == 1 else -0.05 * pos_rd)
                    if (pos_side == 1 and be_level > pos_sl) or (pos_side == -1 and be_level < pos_sl):
                        pos_sl = be_level
                if pos_mfe >= 1.5:
                    trail_level = pos_entry + ((pos_mfe - trail_step) * pos_rd if pos_side == 1 else -(pos_mfe - trail_step) * pos_rd)
                    if (pos_side == 1 and trail_level > pos_sl) or (pos_side == -1 and trail_level < pos_sl):
                        pos_sl = trail_level

            if exited:
                in_pos = False
                cooldown_until_idx = i + cooldown_bars
                exit_r = (exit_p - pos_entry) / pos_rd if pos_side == 1 else (pos_entry - exit_p) / pos_rd
                pnl = (exit_r * risk_usd) - commission_usd

                t_entry = times[entry_idx]
                t_exit = times[i]
                trades.append({
                    "symbol": symbol,
                    "tf": tf,
                    "model": model_family,
                    "side": "BUY" if pos_side == 1 else "SELL",
                    "entry_time": t_entry.isoformat() if hasattr(t_entry, "isoformat") else str(t_entry),
                    "exit_time": t_exit.isoformat() if hasattr(t_exit, "isoformat") else str(t_exit),
                    "entry_price": round(pos_entry, 5),
                    "exit_price": round(exit_p, 5),
                    "risk_dist": round(pos_rd, 5),
                    "exit_reason": exit_reason,
                    "r_multiple": round(exit_r, 2),
                    "pnl_usd": round(pnl, 2),
                    "bars_held": i - entry_idx,
                })
                continue

        # Check signal on confirmed bar i
        if in_pos or i < cooldown_until_idx:
            continue

        sig_side = sig_sides[i]
        if sig_side != 0:
            next_bar = i + 1
            if next_bar >= n:
                break

            entry_p = o[next_bar]
            pos_entry = entry_p
            entry_idx = next_bar
            pos_side = int(sig_side)
            rd = max(1e-5, 1.5 * atr[i])
            pos_rd = rd

            pos_sl = (entry_p - rd) if pos_side == 1 else (entry_p + rd)
            pos_tp = (entry_p + target_rr * rd) if pos_side == 1 else (entry_p - target_rr * rd)
            pos_mfe = 0.0
            in_pos = True

    metrics = calculate_backtest_metrics(trades)
    return trades, metrics


def simulate_multidim_backtest(
    df: pd.DataFrame,
    symbol: str,
    tf: str,
    model_family: str = "VWAP",
    ema_period: int = 50,
    exit_mode: str = "fixed_2.0r",
    risk_usd: float = 5.0,
    cooldown_bars: int = 6,
    precomputed: Optional[Dict[str, Any]] = None
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Standard entry point backwards-compatible with unit tests."""
    if len(df) < max(ema_period + 20, 50):
        return [], calculate_backtest_metrics([])

    df_sorted = df.copy()
    if not pd.api.types.is_datetime64_any_dtype(df_sorted["time"]):
        df_sorted["time"] = pd.to_datetime(df_sorted["time"], utc=True)
    df_sorted = df_sorted.sort_values("time").reset_index(drop=True)

    c = df_sorted["close"].to_numpy(dtype=float)
    h = df_sorted["high"].to_numpy(dtype=float)
    l = df_sorted["low"].to_numpy(dtype=float)
    o = df_sorted["open"].to_numpy(dtype=float)
    atr = calculate_atr(h, l, c, 14)
    start_bar = max(ema_period + 10, 20)

    is_crypto = any(k in symbol.upper() for k in ["BTC", "ETH", "XLM", "XRP", "ADA"])
    anchor_mode = "utc" if is_crypto else "broker"

    if model_family == "VWAP":
        vwap_df = calculate_session_vwap_bands(df_sorted, anchor_mode=anchor_mode)
        sig_sides, sig_setups = precompute_signals_vector(
            o=o, h=h, l=l, c=c, atr=atr, model_family="VWAP",
            vwap_vals=vwap_df["vwap"].to_numpy(dtype=float),
            vwap_p1=vwap_df["vwap_p1"].to_numpy(dtype=float),
            vwap_p2=vwap_df["vwap_p2"].to_numpy(dtype=float),
            vwap_m1=vwap_df["vwap_m1"].to_numpy(dtype=float),
            vwap_m2=vwap_df["vwap_m2"].to_numpy(dtype=float),
            start_bar=start_bar
        )
    else:
        ema_vals = calculate_ema(c, ema_period)
        sig_sides, sig_setups = precompute_signals_vector(
            o=o, h=h, l=l, c=c, atr=atr, model_family="EMA",
            ema_vals=ema_vals, ema_period=ema_period,
            start_bar=start_bar
        )

    return simulate_with_signals(
        times=df_sorted["time"], o=o, h=h, l=l, c=c, atr=atr,
        sig_sides=sig_sides, sig_setups=sig_setups,
        symbol=symbol, tf=tf, model_family=model_family,
        exit_mode=exit_mode, risk_usd=risk_usd, cooldown_bars=cooldown_bars,
        start_bar=start_bar
    )
