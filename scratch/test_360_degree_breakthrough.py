#!/usr/bin/env python3
"""
360-Degree Breakthrough Quantitative Engine
Tests all directions, all timeframes/periods, all shelf variants, and exit targets
across all 28 Alpha Basket assets.
"""

import os
import sys
import glob
import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "FundedNext_Data")

ALPHA_BASKET = [
    {"symbol": "DOGUSD", "cluster": "Crypto", "setup": "canonical", "exit_mode": "fixed_3.5"},
    {"symbol": "ETHUSD", "cluster": "Crypto", "setup": "wyckoff_institutional_filtered", "exit_mode": "step_trail"},
    {"symbol": "LNKUSD", "cluster": "Crypto", "setup": "canonical", "exit_mode": "fixed_3.0"},
    {"symbol": "XLMUSD", "cluster": "Crypto", "setup": "canonical", "exit_mode": "fixed_3.0"},
    {"symbol": "XAUUSD", "cluster": "Commodity_Energy", "setup": "canonical", "exit_mode": "fixed_3.0"},
    {"symbol": "XAGUSD", "cluster": "Commodity_Energy", "setup": "turbo_tri_tier", "exit_mode": "step_trail"},
    {"symbol": "US30", "cluster": "Indices", "setup": "wyckoff_institutional_filtered", "exit_mode": "fixed_3.5"},
    {"symbol": "EUSTX50", "cluster": "Indices", "setup": "wyckoff_institutional_filtered", "exit_mode": "fixed_3.0"},
    {"symbol": "FRA40", "cluster": "Indices", "setup": "wyckoff_institutional_filtered", "exit_mode": "fixed_3.0"},
    {"symbol": "US2000", "cluster": "Indices", "setup": "wyckoff_institutional_filtered", "exit_mode": "fixed_2.5"},
    {"symbol": "HK50", "cluster": "Indices", "setup": "wyckoff_institutional_filtered", "exit_mode": "fixed_2.5"},
    {"symbol": "CHFJPY", "cluster": "Forex_JPY", "setup": "canonical", "exit_mode": "fixed_3.0"},
    {"symbol": "EURJPY", "cluster": "Forex_JPY", "setup": "wyckoff_swing_sweep", "exit_mode": "fixed_3.0"},
    {"symbol": "USDJPY", "cluster": "Forex_JPY", "setup": "turbo_tri_tier", "exit_mode": "fixed_2.5"},
    {"symbol": "CADJPY", "cluster": "Forex_JPY", "setup": "wyckoff_swing_sweep", "exit_mode": "fixed_3.0"},
    {"symbol": "AUDJPY", "cluster": "Forex_JPY", "setup": "wyckoff_institutional_filtered", "exit_mode": "fixed_2.5"},
    {"symbol": "USDCHF", "cluster": "Forex_USD", "setup": "wyckoff_institutional_filtered", "exit_mode": "fixed_3.0"},
    {"symbol": "USDCNH", "cluster": "Forex_USD", "setup": "canonical", "exit_mode": "fixed_3.0"},
    {"symbol": "EURNOK", "cluster": "Forex_EUR", "setup": "wyckoff_swing_sweep", "exit_mode": "fixed_3.0"},
    {"symbol": "MSFT", "cluster": "US_Equities", "setup": "turbo_tri_tier", "exit_mode": "fixed_3.0"},
    {"symbol": "AMD", "cluster": "US_Equities", "setup": "turbo_tri_tier", "exit_mode": "fixed_3.0"},
    {"symbol": "NVDA", "cluster": "US_Equities", "setup": "turbo_tri_tier", "exit_mode": "fixed_3.0"},
    {"symbol": "BABA", "cluster": "US_Equities", "setup": "turbo_tri_tier", "exit_mode": "step_trail"},
    {"symbol": "NFLX", "cluster": "US_Equities", "setup": "turbo_tri_tier", "exit_mode": "fixed_3.0"},
    {"symbol": "GOOG", "cluster": "US_Equities", "setup": "turbo_tri_tier", "exit_mode": "fixed_3.0"},
    {"symbol": "NKE", "cluster": "US_Equities", "setup": "canonical", "exit_mode": "fixed_3.0"},
    {"symbol": "MC", "cluster": "EU_Equities", "setup": "canonical", "exit_mode": "fixed_3.5"},
    {"symbol": "MBG", "cluster": "EU_Equities", "setup": "canonical", "exit_mode": "fixed_3.0"},
]

def calculate_ema(arr: np.ndarray, span: int) -> np.ndarray:
    return pd.Series(arr).ewm(span=span, adjust=False).mean().to_numpy()

def calculate_atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> np.ndarray:
    n = len(closes)
    tr = np.zeros(n)
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
    return pd.Series(tr).rolling(period).mean().bfill().to_numpy()

def compute_adx(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
    n = len(close)
    if n < period * 2 + 2:
        return np.zeros(n, dtype=float)
    up = np.diff(high, prepend=high[0])
    dn = -np.diff(low, prepend=low[0])
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    prev_c = np.roll(close, 1)
    prev_c[0] = close[0]
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev_c), np.abs(low - prev_c)))
    atr_s = np.zeros(n, dtype=float)
    p_s = np.zeros(n, dtype=float)
    m_s = np.zeros(n, dtype=float)
    atr_s[period] = np.mean(tr[1 : period + 1])
    p_s[period] = np.mean(plus_dm[1 : period + 1])
    m_s[period] = np.mean(minus_dm[1 : period + 1])
    alpha = 1.0 / period
    for i in range(period + 1, n):
        atr_s[i] = atr_s[i - 1] * (1.0 - alpha) + tr[i] * alpha
        p_s[i] = p_s[i - 1] * (1.0 - alpha) + plus_dm[i] * alpha
        m_s[i] = m_s[i - 1] * (1.0 - alpha) + minus_dm[i] * alpha
    p_di = 100.0 * p_s / (atr_s + 1e-12)
    m_di = 100.0 * m_s / (atr_s + 1e-12)
    dx = 100.0 * np.abs(p_di - m_di) / (p_di + m_di + 1e-12)
    adx = np.zeros(n, dtype=float)
    start = period * 2
    adx[start] = np.mean(dx[period : start + 1])
    for i in range(start + 1, n):
        adx[i] = adx[i - 1] * (1.0 - alpha) + dx[i] * alpha
    return adx

def extract_trades_flexible(
    df: pd.DataFrame,
    symbol: str,
    cluster: str,
    setup_type: str,
    exit_mode: str,
    rule_variant: str = "current_live",  # 'baseline', 'current_live', 'ema89_only'
    allowed_direction: str = "all",     # 'all', 'long_only', 'short_only'
    friction_r: float = 0.05,
    cooldown_bars: int = 12,
) -> list:
    if len(df) < 240:
        return []

    c = df["close"].to_numpy(dtype=float)
    o = df["open"].to_numpy(dtype=float)
    h = df["high"].to_numpy(dtype=float)
    l = df["low"].to_numpy(dtype=float)
    t = df["timestamp"].to_numpy(dtype=int)
    n = len(c)

    ema34 = calculate_ema(c, 34)
    ema55 = calculate_ema(c, 55)
    ema89 = calculate_ema(c, 89)
    ema233 = calculate_ema(c, 233)
    atr = calculate_atr(h, l, c, 14)
    adx = compute_adx(h, l, c, 14)

    roll_h20 = pd.Series(h).shift(1).rolling(20).max().to_numpy()
    roll_l20 = pd.Series(l).shift(1).rolling(20).min().to_numpy()

    rng = np.where(h - l <= 0, 1e-6, h - l)
    body_tops = np.maximum(o, c)
    body_bots = np.minimum(o, c)
    upper_wicks = (h - body_tops) / rng
    lower_wicks = (body_bots - l) / rng

    is_fixed = exit_mode.startswith("fixed_")
    fixed_rr = float(exit_mode.split("_")[1]) if is_fixed else 3.0

    trades = []
    in_trade = False
    side = 0
    ep = 0.0
    sl = 0.0
    rd = 0.0
    entry_ts = 0
    entry_bar = 0
    max_fav_r = 0.0
    last_exit_bar = -cooldown_bars - 1

    start_idx = 235

    for i in range(start_idx, n - 1):
        if in_trade:
            bh = h[i]
            bl = l[i]
            cur_r = (ep - bl) / rd if side == -1 else (bh - ep) / rd
            if cur_r > max_fav_r:
                max_fav_r = cur_r

            exited = False
            exit_r = 0.0

            if side == 1:
                if is_fixed:
                    if bl <= sl:
                        exit_r = -1.0
                        exited = True
                    elif bh >= ep + fixed_rr * rd:
                        exit_r = fixed_rr
                        exited = True
                elif exit_mode == "step_trail":
                    trail_sl = sl
                    if max_fav_r >= 6.0:
                        trail_sl = max(trail_sl, ema55[i])
                    elif max_fav_r >= 4.0:
                        trail_sl = max(trail_sl, ep + 2.0 * rd)
                    elif max_fav_r >= 2.0:
                        trail_sl = max(trail_sl, ep + 0.5 * rd)
                    if bl <= trail_sl:
                        exit_r = (trail_sl - ep) / rd
                        exited = True
            elif side == -1:
                if is_fixed:
                    if bh >= sl:
                        exit_r = -1.0
                        exited = True
                    elif bl <= ep - fixed_rr * rd:
                        exit_r = fixed_rr
                        exited = True
                elif exit_mode == "step_trail":
                    trail_sl = sl
                    if max_fav_r >= 6.0:
                        trail_sl = min(trail_sl, ema55[i])
                    elif max_fav_r >= 4.0:
                        trail_sl = min(trail_sl, ep - 2.0 * rd)
                    elif max_fav_r >= 2.0:
                        trail_sl = min(trail_sl, ep - 0.5 * rd)
                    if bh >= trail_sl:
                        exit_r = (ep - trail_sl) / rd
                        exited = True

            if exited:
                net_r = exit_r - friction_r
                trades.append({
                    "symbol": symbol,
                    "cluster": cluster,
                    "setup": setup_type,
                    "shelf": trade_shelf,
                    "side": side,
                    "entry_ts": entry_ts,
                    "exit_ts": t[i],
                    "net_r": net_r,
                    "win": 1 if net_r > 0 else 0,
                    "bars_held": i - entry_bar,
                })
                in_trade = False
                last_exit_bar = i
            continue

        if (i - last_exit_bar) < cooldown_bars:
            continue

        c_bar = c[i]
        o_bar = o[i]
        h_bar = h[i]
        l_bar = l[i]
        a_bar = atr[i]
        lower_wick = lower_wicks[i]
        upper_wick = upper_wicks[i]

        sig = 0
        shelf_type = "MID"

        if rule_variant == "baseline":
            # Original find_best_alpha_basket logic
            if setup_type == "canonical":
                if ema89[i] > ema233[i]:
                    if lower_wick >= 0.25:
                        if (l_bar <= ema89[i] * 1.002 or l_bar <= ema55[i] * 1.002) and (c_bar >= ema89[i]):
                            sig = 1; shelf_type = "MID"
                        elif (l_bar <= ema233[i] * 1.002) and (c_bar >= ema233[i]):
                            sig = 1; shelf_type = "SLOW"
                elif ema89[i] < ema233[i]:
                    if upper_wick >= 0.25:
                        if (h_bar >= ema89[i] * 0.998 or h_bar >= ema55[i] * 0.998) and (c_bar <= ema89[i]):
                            sig = -1; shelf_type = "MID"
                        elif (h_bar >= ema233[i] * 0.998) and (c_bar <= ema233[i]):
                            sig = -1; shelf_type = "SLOW"
            elif setup_type == "turbo_tri_tier":
                if ema89[i] > ema233[i] and c_bar >= ema233[i]:
                    if lower_wick >= 0.25:
                        if adx[i] >= 22 and (l_bar <= ema34[i] * 1.002 or l_bar <= ema55[i] * 1.002) and (c_bar >= ema55[i]):
                            sig = 1; shelf_type = "TURBO"
                        elif (l_bar <= ema89[i] * 1.002) and (c_bar >= ema89[i]):
                            sig = 1; shelf_type = "MID"
                        elif (l_bar <= ema233[i] * 1.002) and (c_bar >= ema233[i]):
                            sig = 1; shelf_type = "SLOW"
                elif ema89[i] < ema233[i] and c_bar <= ema233[i]:
                    if upper_wick >= 0.25:
                        if adx[i] >= 22 and (h_bar >= ema34[i] * 0.998 or h_bar >= ema55[i] * 0.998) and (c_bar <= ema55[i]):
                            sig = -1; shelf_type = "TURBO"
                        elif (h_bar >= ema89[i] * 0.998) and (c_bar <= ema89[i]):
                            sig = -1; shelf_type = "MID"
                        elif (h_bar >= ema233[i] * 0.998) and (c_bar <= ema233[i]):
                            sig = -1; shelf_type = "SLOW"
            elif setup_type == "wyckoff_swing_sweep":
                if ema89[i] > ema233[i] and c_bar >= ema233[i]:
                    if l_bar < roll_l20[i] and c_bar > roll_l20[i] and lower_wick >= 0.25:
                        sig = 1; shelf_type = "SWEEP"
                elif ema89[i] < ema233[i] and c_bar <= ema233[i]:
                    if h_bar > roll_h20[i] and c_bar < roll_h20[i] and upper_wick >= 0.25:
                        sig = -1; shelf_type = "SWEEP"
            elif setup_type == "wyckoff_institutional_filtered":
                sep = abs(ema89[i] - ema233[i]) / a_bar
                if 0.25 <= sep <= 2.50 and adx[i] >= 20.0:
                    if ema89[i] > ema233[i] and c_bar > o_bar:
                        if lower_wick >= 0.25:
                            if (l_bar <= ema89[i] * 1.002 or l_bar <= ema55[i] * 1.002) and (c_bar >= ema89[i]):
                                sig = 1; shelf_type = "MID"
                            elif (l_bar <= ema233[i] * 1.002) and (c_bar >= ema233[i]):
                                sig = 1; shelf_type = "SLOW"
                    elif ema89[i] < ema233[i] and c_bar < o_bar:
                        if upper_wick >= 0.25:
                            if (h_bar >= ema89[i] * 0.998 or h_bar >= ema55[i] * 0.998) and (c_bar <= ema89[i]):
                                sig = -1; shelf_type = "MID"
                            elif (h_bar >= ema233[i] * 0.998) and (c_bar <= ema233[i]):
                                sig = -1; shelf_type = "SLOW"

        elif rule_variant == "current_live":
            # Current Live Engine logic (with Priority 1 Direct EMA233 Rejection & color-agnostic)
            if setup_type == "canonical":
                if c_bar >= ema233[i] and (l_bar <= ema233[i] * 1.002) and lower_wick >= 0.25:
                    sig = 1; shelf_type = "SLOW"
                elif c_bar <= ema233[i] and (h_bar >= ema233[i] * 0.998) and upper_wick >= 0.25:
                    sig = -1; shelf_type = "SLOW"
                elif ema89[i] > ema233[i]:
                    if lower_wick >= 0.25 and (l_bar <= ema89[i] * 1.002 or l_bar <= ema55[i] * 1.002) and (c_bar >= ema89[i]):
                        sig = 1; shelf_type = "MID"
                elif ema89[i] < ema233[i]:
                    if upper_wick >= 0.25 and (h_bar >= ema89[i] * 0.998 or h_bar >= ema55[i] * 0.998) and (c_bar <= ema89[i]):
                        sig = -1; shelf_type = "MID"
            elif setup_type == "turbo_tri_tier":
                if c_bar >= ema233[i] and (l_bar <= ema233[i] * 1.002) and lower_wick >= 0.25:
                    sig = 1; shelf_type = "SLOW"
                elif c_bar <= ema233[i] and (h_bar >= ema233[i] * 0.998) and upper_wick >= 0.25:
                    sig = -1; shelf_type = "SLOW"
                elif ema89[i] > ema233[i] and c_bar >= ema233[i]:
                    if lower_wick >= 0.25:
                        if adx[i] >= 22 and (l_bar <= ema34[i] * 1.002 or l_bar <= ema55[i] * 1.002) and (c_bar >= ema55[i]):
                            sig = 1; shelf_type = "TURBO"
                        elif (l_bar <= ema89[i] * 1.002) and (c_bar >= ema89[i]):
                            sig = 1; shelf_type = "MID"
                elif ema89[i] < ema233[i] and c_bar <= ema233[i]:
                    if upper_wick >= 0.25:
                        if adx[i] >= 22 and (h_bar >= ema34[i] * 0.998 or h_bar >= ema55[i] * 0.998) and (c_bar <= ema55[i]):
                            sig = -1; shelf_type = "TURBO"
                        elif (h_bar >= ema89[i] * 0.998) and (c_bar <= ema89[i]):
                            sig = -1; shelf_type = "MID"
            elif setup_type == "wyckoff_swing_sweep":
                if ema89[i] > ema233[i] and c_bar >= ema233[i]:
                    if l_bar < roll_l20[i] and c_bar > roll_l20[i] and lower_wick >= 0.25:
                        sig = 1; shelf_type = "SWEEP"
                elif ema89[i] < ema233[i] and c_bar <= ema233[i]:
                    if h_bar > roll_h20[i] and c_bar < roll_h20[i] and upper_wick >= 0.25:
                        sig = -1; shelf_type = "SWEEP"
            elif setup_type == "wyckoff_institutional_filtered":
                if c_bar >= ema233[i] and (l_bar <= ema233[i] * 1.002) and lower_wick >= 0.25:
                    sig = 1; shelf_type = "SLOW"
                elif c_bar <= ema233[i] and (h_bar >= ema233[i] * 0.998) and upper_wick >= 0.25:
                    sig = -1; shelf_type = "SLOW"
                else:
                    sep = abs(ema89[i] - ema233[i]) / a_bar
                    if 0.25 <= sep <= 2.50 and adx[i] >= 20.0:
                        if ema89[i] > ema233[i] and lower_wick >= 0.25:
                            if (l_bar <= ema89[i] * 1.002 or l_bar <= ema55[i] * 1.002) and (c_bar >= ema89[i]):
                                sig = 1; shelf_type = "MID"
                        elif ema89[i] < ema233[i] and upper_wick >= 0.25:
                            if (h_bar >= ema89[i] * 0.998 or h_bar >= ema55[i] * 0.998) and (c_bar <= ema89[i]):
                                sig = -1; shelf_type = "MID"

        elif rule_variant == "ema89_only":
            # EMA 89 / 55 Mid & Turbo Shelves ONLY (Zero EMA233 shelf drag)
            if setup_type == "canonical":
                if ema89[i] > ema233[i]:
                    if lower_wick >= 0.25 and (l_bar <= ema89[i] * 1.002 or l_bar <= ema55[i] * 1.002) and (c_bar >= ema89[i]):
                        sig = 1; shelf_type = "MID"
                elif ema89[i] < ema233[i]:
                    if upper_wick >= 0.25 and (h_bar >= ema89[i] * 0.998 or h_bar >= ema55[i] * 0.998) and (c_bar <= ema89[i]):
                        sig = -1; shelf_type = "MID"
            elif setup_type == "turbo_tri_tier":
                if ema89[i] > ema233[i] and c_bar >= ema233[i]:
                    if lower_wick >= 0.25:
                        if adx[i] >= 22 and (l_bar <= ema34[i] * 1.002 or l_bar <= ema55[i] * 1.002) and (c_bar >= ema55[i]):
                            sig = 1; shelf_type = "TURBO"
                        elif (l_bar <= ema89[i] * 1.002) and (c_bar >= ema89[i]):
                            sig = 1; shelf_type = "MID"
                elif ema89[i] < ema233[i] and c_bar <= ema233[i]:
                    if upper_wick >= 0.25:
                        if adx[i] >= 22 and (h_bar >= ema34[i] * 0.998 or h_bar >= ema55[i] * 0.998) and (c_bar <= ema55[i]):
                            sig = -1; shelf_type = "TURBO"
                        elif (h_bar >= ema89[i] * 0.998) and (c_bar <= ema89[i]):
                            sig = -1; shelf_type = "MID"
            elif setup_type == "wyckoff_swing_sweep":
                if ema89[i] > ema233[i] and c_bar >= ema233[i]:
                    if l_bar < roll_l20[i] and c_bar > roll_l20[i] and lower_wick >= 0.25:
                        sig = 1; shelf_type = "SWEEP"
                elif ema89[i] < ema233[i] and c_bar <= ema233[i]:
                    if h_bar > roll_h20[i] and c_bar < roll_h20[i] and upper_wick >= 0.25:
                        sig = -1; shelf_type = "SWEEP"
            elif setup_type == "wyckoff_institutional_filtered":
                sep = abs(ema89[i] - ema233[i]) / a_bar
                if 0.25 <= sep <= 2.50 and adx[i] >= 20.0:
                    if ema89[i] > ema233[i] and lower_wick >= 0.25:
                        if (l_bar <= ema89[i] * 1.002 or l_bar <= ema55[i] * 1.002) and (c_bar >= ema89[i]):
                            sig = 1; shelf_type = "MID"
                    elif ema89[i] < ema233[i] and upper_wick >= 0.25:
                        if (h_bar >= ema89[i] * 0.998 or h_bar >= ema55[i] * 0.998) and (c_bar <= ema89[i]):
                            sig = -1; shelf_type = "MID"

        # Apply Direction Filter
        if allowed_direction == "long_only" and sig == -1:
            sig = 0
        elif allowed_direction == "short_only" and sig == 1:
            sig = 0

        if sig != 0:
            next_bar = i + 1
            if next_bar >= n:
                break

            entry_price = o[next_bar]
            if shelf_type == "SLOW":
                stop_price = (min(ema233[i], l_bar) - 0.35 * a_bar) if sig == 1 else (max(ema233[i], h_bar) + 0.35 * a_bar)
            elif shelf_type in ["TURBO", "SWEEP"]:
                stop_price = (l_bar - 0.25 * a_bar) if sig == 1 else (h_bar + 0.25 * a_bar)
            else:
                stop_price = (l_bar - 0.30 * a_bar) if sig == 1 else (h_bar + 0.30 * a_bar)

            risk_dist = abs(entry_price - stop_price)
            if risk_dist <= 0 or (risk_dist / entry_price) < 0.0005:
                continue

            in_trade = True
            side = sig
            ep = entry_price
            sl = stop_price
            rd = risk_dist
            entry_ts = t[next_bar]
            entry_bar = next_bar
            trade_shelf = shelf_type
            max_fav_r = 0.0

    return trades

def simulate_portfolio(trades_pool: list, max_concurrent: int = 3, max_per_cluster: int = 1) -> dict:
    if not trades_pool:
        return {"net_r": 0.0, "max_dd_r": 0.0, "calmar": 0.0, "trades": 0, "win_rate": 0.0, "pf": 0.0}

    sorted_trades = sorted(trades_pool, key=lambda x: x["entry_ts"])
    active_trades = []
    executed = []

    for t in sorted_trades:
        e_ts = t["entry_ts"]
        active_trades = [a for a in active_trades if a["exit_ts"] > e_ts]
        if len(active_trades) >= max_concurrent:
            continue
        cluster_count = sum(1 for a in active_trades if a["cluster"] == t["cluster"])
        if cluster_count >= max_per_cluster:
            continue
        active_trades.append(t)
        executed.append(t)

    if not executed:
        return {"net_r": 0.0, "max_dd_r": 0.0, "calmar": 0.0, "trades": 0, "win_rate": 0.0, "pf": 0.0}

    executed = sorted(executed, key=lambda x: x["exit_ts"])
    net_r_list = [t["net_r"] for t in executed]
    cum_r = np.cumsum(net_r_list)
    peak = np.maximum.accumulate(cum_r)
    dd = peak - cum_r
    max_dd = np.max(dd) if len(dd) > 0 else 0.0
    tot_net_r = cum_r[-1] if len(cum_r) > 0 else 0.0
    calmar = (tot_net_r / max_dd) if max_dd > 0 else 0.0
    wins = [r for r in net_r_list if r > 0]
    losses = [r for r in net_r_list if r <= 0]
    win_rate = (len(wins) / len(net_r_list)) * 100.0 if net_r_list else 0.0
    tot_wins = sum(wins)
    tot_losses = abs(sum(losses))
    pf = (tot_wins / tot_losses) if tot_losses > 0 else 999.0

    return {
        "net_r": round(tot_net_r, 2),
        "max_dd_r": round(max_dd, 2),
        "calmar": round(calmar, 2),
        "trades": len(executed),
        "win_rate": round(win_rate, 1),
        "pf": round(pf, 2),
        "executed_trades": executed,
    }

def run_360_degree_study():
    # Load all data
    data_map = {}
    for item in ALPHA_BASKET:
        sym = item["symbol"]
        p_path = os.path.join(DATA_DIR, f"{sym}.parquet")
        if os.path.exists(p_path):
            data_map[sym] = pd.read_parquet(p_path)
        else:
            print(f"Warning: Missing data for {sym}")

    print("=" * 80)
    print("360-DEGREE QUANTITATIVE BREAKTHROUGH STUDY (2024 - 2026)")
    print(f"Loaded {len(data_map)} / {len(ALPHA_BASKET)} symbols.")
    print("=" * 80)

    # Time boundaries
    TS_2024 = 1704067200  # Jan 1, 2024
    TS_2025 = 1735689600  # Jan 1, 2025
    TS_2026 = 1767225600  # Jan 1, 2026

    # 1. TEST ALL DIRECTIONS (Both vs Longs Only vs Shorts Only) across Rule Variants
    print("\n>>> 1. ALL DIRECTIONS AUDIT (Jan 2026 - Present YTD):")
    print(f"{'Variant':<25} | {'Direction':<12} | {'Trades':<7} | {'Win%':<6} | {'Net R':<9} | {'Net $':<9} | {'PF':<5} | {'Calmar':<7} | {'Max DD'}")
    print("-" * 95)

    scenarios = [
        ("Baseline (Original)", "baseline", "all"),
        ("Baseline", "baseline", "long_only"),
        ("Baseline", "baseline", "short_only"),
        ("Current Live Engine", "current_live", "all"),
        ("Current Live Engine", "current_live", "long_only"),
        ("Current Live Engine", "current_live", "short_only"),
        ("EMA89-Only Shelf", "ema89_only", "all"),
        ("EMA89-Only Shelf", "ema89_only", "long_only"),
        ("EMA89-Only Shelf", "ema89_only", "short_only"),
    ]

    for label, r_var, d_var in scenarios:
        all_trades = []
        for item in ALPHA_BASKET:
            sym = item["symbol"]
            if sym not in data_map:
                continue
            df = data_map[sym]
            trades = extract_trades_flexible(
                df, sym, item["cluster"], item["setup"], item["exit_mode"],
                rule_variant=r_var, allowed_direction=d_var
            )
            # Filter for 2026
            trades_2026 = [t for t in trades if t["entry_ts"] >= TS_2026]
            all_trades.extend(trades_2026)

        res = simulate_portfolio(all_trades, max_concurrent=3, max_per_cluster=1)
        net_usd = res["net_r"] * 10.0
        print(f"{label:<25} | {d_var:<12} | {res['trades']:<7} | {res['win_rate']:<5.1f}% | {res['net_r']:<+8.2f}R | ${net_usd:<+8.2f} | {res['pf']:<5.2f} | {res['calmar']:<7.2f} | -{res['max_dd_r']:.2f}R")

    # 2. TEST ALL TIME PERIODS (2024, 2025, 2026, Full All-Time)
    print("\n>>> 2. ALL-TIME CONSISTENCY AUDIT (2024 vs 2025 vs 2026 vs Total):")
    time_windows = [
        ("2024 (H2)", TS_2024, TS_2025),
        ("2025 (Full Year)", TS_2025, TS_2026),
        ("2026 (YTD)", TS_2026, 9999999999),
        ("ALL-TIME (2024-2026)", TS_2024, 9999999999),
    ]

    for r_var, r_name in [("current_live", "Current Live Engine"), ("ema89_only", "EMA89-Only Shelf")]:
        print(f"\n--- Strategy: {r_name} ---")
        print(f"{'Time Period':<22} | {'Trades':<7} | {'Win%':<6} | {'Net R':<9} | {'Net $':<9} | {'PF':<5} | {'Calmar':<7} | {'Max DD'}")
        print("-" * 80)
        all_trades = []
        for item in ALPHA_BASKET:
            sym = item["symbol"]
            if sym not in data_map:
                continue
            df = data_map[sym]
            trades = extract_trades_flexible(
                df, sym, item["cluster"], item["setup"], item["exit_mode"],
                rule_variant=r_var, allowed_direction="all"
            )
            all_trades.extend(trades)

        for w_label, start_ts, end_ts in time_windows:
            period_trades = [t for t in all_trades if start_ts <= t["entry_ts"] < end_ts]
            res = simulate_portfolio(period_trades, max_concurrent=3, max_per_cluster=1)
            net_usd = res["net_r"] * 10.0
            print(f"{w_label:<22} | {res['trades']:<7} | {res['win_rate']:<5.1f}% | {res['net_r']:<+8.2f}R | ${net_usd:<+8.2f} | {res['pf']:<5.2f} | {res['calmar']:<7.2f} | -{res['max_dd_r']:.2f}R")

    # 3. TEST ASSET FILTERING (Dropping Drag Assets)
    print("\n>>> 3. BASKET OPTIMIZATION AUDIT (Dropping Persistent Underperformers):")
    drags_to_drop = ["LNKUSD", "XAGUSD", "US2000", "AUDJPY", "NVDA", "MBG", "GOOG"]
    cleaned_basket = [item for item in ALPHA_BASKET if item["symbol"] not in drags_to_drop]

    print(f"Original Basket: {len(ALPHA_BASKET)} assets | Cleaned Basket: {len(cleaned_basket)} assets (Dropped 7 drags)")
    for r_var, r_name in [("current_live", "Current Live Engine"), ("ema89_only", "EMA89-Only Shelf")]:
        # Full 2026
        trades_all = []
        trades_clean = []
        for item in ALPHA_BASKET:
            sym = item["symbol"]
            if sym not in data_map:
                continue
            trades = extract_trades_flexible(
                data_map[sym], sym, item["cluster"], item["setup"], item["exit_mode"],
                rule_variant=r_var, allowed_direction="all"
            )
            trades_2026 = [t for t in trades if t["entry_ts"] >= TS_2026]
            trades_all.extend(trades_2026)
            if sym not in drags_to_drop:
                trades_clean.extend(trades_2026)

        res_all = simulate_portfolio(trades_all, max_concurrent=3, max_per_cluster=1)
        res_clean = simulate_portfolio(trades_clean, max_concurrent=3, max_per_cluster=1)
        print(f"\n{r_name}:")
        print(f"  All 28 Assets:    Trades: {res_all['trades']} | Net R: {res_all['net_r']:+6.2f}R | Max DD: -{res_all['max_dd_r']:.2f}R | Calmar: {res_all['calmar']:.2f} | PF: {res_all['pf']:.2f}")
        print(f"  Clean 21 Assets:  Trades: {res_clean['trades']} | Net R: {res_clean['net_r']:+6.2f}R | Max DD: -{res_clean['max_dd_r']:.2f}R | Calmar: {res_clean['calmar']:.2f} | PF: {res_clean['pf']:.2f}")

    # 4. CLUSTER & DIRECTIONAL CONFLUENCE TEST (e.g. Long-Only Crypto & Equities)
    print("\n>>> 4. REGIME & ASSET CLASS DIRECTIONAL BREAKTHROUGH:")
    print("Testing if individual clusters perform vastly better with Directional Filters:")
    for cluster_name in ["Crypto", "Commodity_Energy", "Indices", "US_Equities", "EU_Equities", "Forex_JPY", "Forex_USD", "Forex_EUR"]:
        c_items = [x for x in ALPHA_BASKET if x["cluster"] == cluster_name]
        c_trades_all = []
        for item in c_items:
            sym = item["symbol"]
            if sym not in data_map:
                continue
            t = extract_trades_flexible(data_map[sym], sym, item["cluster"], item["setup"], item["exit_mode"], rule_variant="ema89_only", allowed_direction="all")
            c_trades_all.extend([x for x in t if x["entry_ts"] >= TS_2026])

        longs = [x for x in c_trades_all if x["side"] == 1]
        shorts = [x for x in c_trades_all if x["side"] == -1]
        long_r = sum(x["net_r"] for x in longs)
        short_r = sum(x["net_r"] for x in shorts)
        print(f"  Cluster {cluster_name:<18} | Longs: {len(longs):<3} trades ({long_r:+6.2f}R) | Shorts: {len(shorts):<3} trades ({short_r:+6.2f}R)")

if __name__ == "__main__":
    run_360_degree_study()
