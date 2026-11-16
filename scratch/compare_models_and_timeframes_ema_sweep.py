#!/usr/bin/env python3
r"""scratch/compare_models_and_timeframes_ema_sweep.py: Dual-Model & Dual-Timeframe (H1 vs H4) EMA Sweep
Compares:
1. Model 1 (Pullback Rejections) vs Model 2 (Macro Dwell & Reclaim Spring)
2. H4 vs H1 Timeframes across the 20 Champion Assets
3. Evaluates key EMA periods: [50, 55, 75, 89, 100, 144, 200, 233, 300, 377]
4. Evaluates real friction impact on H1 vs H4 (Spread / ATR ratio)
"""

import os
import sys
import numpy as np
import pandas as pd
from datetime import datetime, timezone
import MetaTrader5 as mt5

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "FundedNext_Data")
sys.path.insert(0, BASE_DIR)

from engine.mt5_alpha_trader_monitor import OPTIMAL_ALPHA_BASKET, calculate_ema, calculate_atr

# Test common timeframe window: March 2025 to November 2026 (where both H1 and H4 have dense tick data)
START_TS = int(datetime(2025, 3, 15, tzinfo=timezone.utc).timestamp())
MACRO_EMAS = [100, 144, 200, 233, 300, 377]
FAST_EMAS = [50, 55, 75, 89, 100]


def get_data(symbol, timeframe_str):
    if timeframe_str == "H4":
        pq = os.path.join(DATA_DIR, f"{symbol}.parquet")
        if os.path.exists(pq):
            df = pd.read_parquet(pq)
            return df
        return None
    elif timeframe_str == "H1":
        # Pull live H1 from MT5
        if not mt5.initialize():
            return None
        rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H1, 0, 12000)
        if rates is None or len(rates) < 500:
            return None
        df = pd.DataFrame(rates)
        df.rename(columns={"time": "timestamp"}, inplace=True)
        return df
    return None


def backtest_asset_model(df, fast_p, macro_p, model_type="MODEL_1", timeframe_str="H4", friction_r=0.05):
    c = df["close"].to_numpy(dtype=float)
    o = df["open"].to_numpy(dtype=float)
    h = df["high"].to_numpy(dtype=float)
    l = df["low"].to_numpy(dtype=float)
    t = df["timestamp"].to_numpy(dtype=int)
    n = len(c)

    if n < macro_p + 100:
        return []

    e_fast = calculate_ema(c, fast_p)
    e_macro = calculate_ema(c, macro_p)
    atr = calculate_atr(h, l, c, 14)

    rng = np.where(h - l <= 0, 1e-6, h - l)
    body_tops = np.maximum(o, c)
    body_bots = np.minimum(o, c)
    upper_wicks = (h - body_tops) / rng
    lower_wicks = (body_bots - l) / rng

    # Dwell trackers for Model 2
    # In H4: 12h-48h = 3-12 bars. In H1: 12h-48h = 12-48 bars.
    min_dwell = 3 if timeframe_str == "H4" else 12
    max_dwell = 25 if timeframe_str == "H4" else 60
    dwell_lookback = 35 if timeframe_str == "H4" else 100

    bars_above_macro = np.zeros(n, dtype=int)
    bars_below_macro = np.zeros(n, dtype=int)
    for i in range(1, n):
        if c[i] > e_macro[i]:
            bars_above_macro[i] = bars_above_macro[i - 1] + 1
            bars_below_macro[i] = 0
        elif c[i] < e_macro[i]:
            bars_below_macro[i] = bars_below_macro[i - 1] + 1
            bars_above_macro[i] = 0

    trades = []
    in_trade = False
    side = 0
    ep = sl = rd = 0.0
    cooldown = 12 if timeframe_str == "H4" else 36
    last_exit = -cooldown - 1
    target_rr = 3.5 if model_type == "MODEL_2" else 3.0

    for i in range(macro_p + 20, n - 1):
        if in_trade:
            bh = h[i]; bl = l[i]
            exited = False; exit_r = 0.0
            if side == 1:
                if bl <= sl: exit_r = -1.0; exited = True
                elif bh >= ep + target_rr * rd: exit_r = target_rr; exited = True
            else:
                if bh >= sl: exit_r = -1.0; exited = True
                elif bl <= ep - target_rr * rd: exit_r = target_rr; exited = True

            if exited:
                trades.append(exit_r - friction_r)
                in_trade = False
                last_exit = i
            continue

        if (i - last_exit) < cooldown:
            continue

        c_bar = c[i]; o_bar = o[i]; h_bar = h[i]; l_bar = l[i]; a_bar = atr[i]
        came_from_above = (o_bar >= e_fast[i] * 0.998) or (c[i - 1] >= e_fast[i] * 0.998)
        came_from_below = (o_bar <= e_fast[i] * 1.002) or (c[i - 1] <= e_fast[i] * 1.002)

        sig = 0
        if model_type == "MODEL_1":
            # Model 1: Dynamic Trend Pullback Shelf
            if e_fast[i] > e_macro[i] and came_from_above and lower_wicks[i] >= 0.25:
                if l_bar <= e_fast[i] * 1.002 and c_bar >= e_fast[i]:
                    sig = 1
            elif e_fast[i] < e_macro[i] and came_from_below and upper_wicks[i] >= 0.25:
                if h_bar >= e_fast[i] * 0.998 and c_bar <= e_fast[i]:
                    sig = -1

        elif model_type == "MODEL_2":
            # Model 2: Macro Dwell & Reclaim Spring (33% pinbar)
            if e_fast[i] > e_macro[i] and came_from_above and lower_wicks[i] >= 0.33:
                if l_bar <= e_fast[i] * 1.002 and c_bar >= e_fast[i]:
                    recent_below = np.max(bars_below_macro[max(0, i - dwell_lookback) : i])
                    if min_dwell <= recent_below <= max_dwell:
                        sig = 1
            elif e_fast[i] < e_macro[i] and came_from_below and upper_wicks[i] >= 0.33:
                if h_bar >= e_fast[i] * 0.998 and c_bar <= e_fast[i]:
                    recent_above = np.max(bars_above_macro[max(0, i - dwell_lookback) : i])
                    if min_dwell <= recent_above <= max_dwell:
                        sig = -1

        if sig != 0:
            next_b = i + 1
            if next_b >= n or t[next_b] < START_TS:
                continue

            entry_p = o[next_b]
            stop_p = (l_bar - 0.30 * a_bar) if sig == 1 else (h_bar + 0.30 * a_bar)
            risk_d = abs(entry_p - stop_p)
            if risk_d <= 0 or (risk_d / entry_p) < 0.0003:
                continue

            in_trade = True
            side = sig
            ep = entry_p
            sl = stop_p
            rd = risk_d

    return trades


def run_sweep():
    print("=" * 105)
    print("DUAL-MODEL & DUAL-TIMEFRAME QUANT SWEEP (H4 vs H1 | MODEL 1 vs MODEL 2)")
    print(f"Common Comparative Time Horizon: March 2025 to November 2026")
    print(f"Assets: 20 Champion Universe Assets across 6 Clusters")
    print("=" * 105)

    # Pre-load datasets
    data_h4 = {}
    data_h1 = {}
    print("Loading H4 and H1 data for 20 assets...")
    for item in OPTIMAL_ALPHA_BASKET:
        sym = item["symbol"]
        d4 = get_data(sym, "H4")
        if d4 is not None: data_h4[sym] = d4
        d1 = get_data(sym, "H1")
        if d1 is not None: data_h1[sym] = d1

    print(f"Data Loaded: {len(data_h4)} H4 assets | {len(data_h1)} H1 assets")

    # 1. TIMEFRAME COMPARISON: H4 vs H1 across Key EMAs on Model 1
    print("\n" + "=" * 105)
    print("--- 1. TIMEFRAME COMPARISON (H4 vs H1) ON MODEL 1 (TREND PULLBACKS) ---")
    print("=" * 105)
    print(f"{'Pair':<16} | {'H4 Trades':<10} | {'H4 WR%':<8} | {'H4 Net R':<10} | {'H1 Trades':<10} | {'H1 WR%':<8} | {'H1 Net R':<10} | {'Winner':<8}")
    print("-" * 105)

    key_pairs = [(89, 233), (100, 200), (100, 233), (89, 300), (55, 233), (55, 300)]
    for f_p, m_p in key_pairs:
        # H4
        t4_all = []
        for sym, df in data_h4.items():
            t4_all.extend(backtest_asset_model(df, f_p, m_p, model_type="MODEL_1", timeframe_str="H4"))
        t4_arr = np.array(t4_all)
        r4 = round(t4_arr.sum(), 2) if len(t4_arr) > 0 else 0.0
        wr4 = round((t4_arr > 0).mean() * 100, 1) if len(t4_arr) > 0 else 0.0

        # H1
        t1_all = []
        for sym, df in data_h1.items():
            t1_all.extend(backtest_asset_model(df, f_p, m_p, model_type="MODEL_1", timeframe_str="H1"))
        t1_arr = np.array(t1_all)
        r1 = round(t1_arr.sum(), 2) if len(t1_arr) > 0 else 0.0
        wr1 = round((t1_arr > 0).mean() * 100, 1) if len(t1_arr) > 0 else 0.0

        winner = "H4 🏆" if r4 > r1 else "H1 🏆"
        print(f"EMA {f_p}/{m_p:<8} | {len(t4_arr):<10} | {wr4:<7.1f}% | {r4:<+10.2f} | {len(t1_arr):<10} | {wr1:<7.1f}% | {r1:<+10.2f} | {winner:<8}")

    # 2. MODEL 2 (MACRO DWELL & RECLAIM SPRING) SWEEP ACROSS MACRO EMAs
    print("\n" + "=" * 105)
    print("--- 2. MODEL 2 (MACRO DWELL & RECLAIM SPRING) SWEEP ACROSS MACRO EMAs (H4) ---")
    print("Testing which Macro EMA creates the highest expectancy when dwelled and reclaimed")
    print("=" * 105)
    print(f"{'Macro EMA':<12} | {'Trades':<8} | {'Win Rate':<9} | {'Net Return':<12} | {'Expectancy (R/tr)':<18} | {'PF':<6}")
    print("-" * 105)

    for m_p in MACRO_EMAS:
        t_all = []
        for sym, df in data_h4.items():
            t_all.extend(backtest_asset_model(df, 89, m_p, model_type="MODEL_2", timeframe_str="H4"))
        t_arr = np.array(t_all)
        if len(t_arr) == 0:
            continue
        tot_r = round(t_arr.sum(), 2)
        wr = round((t_arr > 0).mean() * 100, 1)
        exp = round(t_arr.mean(), 3)
        wins = t_arr[t_arr > 0].sum()
        losses = abs(t_arr[t_arr < 0].sum())
        pf = round(wins / losses, 2) if losses > 0 else 99.0
        print(f"EMA {m_p:<8} | {len(t_arr):<8} | {wr:<8.1f}% | {tot_r:<+12.2f} | {exp:<+18.3f} | {pf:<6.2f}")

    # 3. MODEL 2 TIMEFRAME TEST: H4 vs H1 on Macro Reclaims
    print("\n" + "=" * 105)
    print("--- 3. MODEL 2 TIMEFRAME TEST: H4 vs H1 (MACRO RECLAIM SPRING) ---")
    print("=" * 105)
    for m_p in [200, 233, 300]:
        t4 = []
        for sym, df in data_h4.items():
            t4.extend(backtest_asset_model(df, 89, m_p, model_type="MODEL_2", timeframe_str="H4"))
        t4_arr = np.array(t4)

        t1 = []
        for sym, df in data_h1.items():
            t1.extend(backtest_asset_model(df, 89, m_p, model_type="MODEL_2", timeframe_str="H1"))
        t1_arr = np.array(t1)

        r4 = round(t4_arr.sum(), 2) if len(t4_arr) > 0 else 0.0
        wr4 = round((t4_arr > 0).mean() * 100, 1) if len(t4_arr) > 0 else 0.0

        r1 = round(t1_arr.sum(), 2) if len(t1_arr) > 0 else 0.0
        wr1 = round((t1_arr > 0).mean() * 100, 1) if len(t1_arr) > 0 else 0.0

        print(f"Reclaim EMA {m_p} -> H4: {len(t4_arr)} tr | {wr4}% WR | {r4:+6.2f}R   vs   H1: {len(t1_arr)} tr | {wr1}% WR | {r1:+6.2f}R")

    print("=" * 105)


if __name__ == "__main__":
    run_sweep()
