#!/usr/bin/env python3
r"""scratch/quant_macro_reclaim_optimization.py: Comprehensive Quant Parameter Sweep & Optimization
Testing:
1. Dwell Duration: [12h (3b), 24h (6b), 48h (12b), 72h (18b), 96h (24b)]
2. EMA Pairs: [(55, 144), (55, 233), (89, 233), (89, 377), (144, 233)]
3. Setup Archetype: [RECLAIM_SPRING, TREND_PULLBACK, ALL_TOUCHES]
4. Wick Ratio: [0.20, 0.25, 0.33]
5. Stop Loss Geometry: [TIGHT_0.15, BASELINE_0.30, WIDE_0.50, STRUCTURAL_BASELINE]
6. Target RR: [2.0, 2.5, 3.0, 3.5, 4.0, 5.0]
7. ADX Gate: [None, ADX >= 20, ADX >= 25]

Computes: Trades, WinRate, Net R, MaxDD, Calmar, Profit Factor, Expectancy, and Student's t-stat / p-value.
"""

import os
import sys
import glob
import itertools
from datetime import datetime, timezone
import numpy as np
import pandas as pd
from scipy import stats

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "FundedNext_Data")
sys.path.insert(0, BASE_DIR)

from engine.mt5_alpha_trader_monitor import OPTIMAL_ALPHA_BASKET, calculate_ema, calculate_atr, compute_adx

JAN_2024_TS = 1704067200


def precalculate_asset_arrays(parquet_path):
    df = pd.read_parquet(parquet_path)
    if len(df) < 450:
        return None

    c = df["close"].to_numpy(dtype=float)
    o = df["open"].to_numpy(dtype=float)
    h = df["high"].to_numpy(dtype=float)
    l = df["low"].to_numpy(dtype=float)
    t = df["timestamp"].to_numpy(dtype=int)
    n = len(c)

    # Precalculate EMAs
    emas = {
        55: calculate_ema(c, 55),
        89: calculate_ema(c, 89),
        144: calculate_ema(c, 144),
        233: calculate_ema(c, 233),
        377: calculate_ema(c, 377),
    }
    atr = calculate_atr(h, l, c, 14)
    adx = compute_adx(h, l, c, 14)

    rng = np.where(h - l <= 0, 1e-6, h - l)
    body_tops = np.maximum(o, c)
    body_bots = np.minimum(o, c)
    upper_wicks = (h - body_tops) / rng
    lower_wicks = (body_bots - l) / rng

    return {
        "c": c, "o": o, "h": h, "l": l, "t": t, "n": n,
        "emas": emas, "atr": atr, "adx": adx,
        "upper_wicks": upper_wicks, "lower_wicks": lower_wicks,
    }


def evaluate_parameter_set(cached_data_list, shelf_ema, macro_ema, dwell_bars, setup_type, wick_ratio, sl_mode, tp_rr, adx_filter, cooldown_bars=12, friction_r=0.05):
    all_trade_rs = []
    
    for data in cached_data_list:
        c = data["c"]
        o = data["o"]
        h = data["h"]
        l = data["l"]
        t = data["t"]
        n = data["n"]
        e_shelf = data["emas"][shelf_ema]
        e_macro = data["emas"][macro_ema]
        atr = data["atr"]
        adx = data["adx"]
        uw = data["upper_wicks"]
        lw = data["lower_wicks"]

        # Track dwell under/above macro EMA
        bars_above_macro = np.zeros(n, dtype=int)
        bars_below_macro = np.zeros(n, dtype=int)
        for i in range(1, n):
            if c[i] > e_macro[i]:
                bars_above_macro[i] = bars_above_macro[i - 1] + 1
                bars_below_macro[i] = 0
            elif c[i] < e_macro[i]:
                bars_below_macro[i] = bars_below_macro[i - 1] + 1
                bars_above_macro[i] = 0

        in_trade = False
        side = 0
        ep = 0.0
        sl = 0.0
        tp = 0.0
        rd = 0.0
        last_exit_bar = -cooldown_bars - 1

        for i in range(380, n - 1):
            if in_trade:
                bh = h[i]
                bl = l[i]
                exited = False
                exit_r = 0.0

                if side == 1:
                    if bl <= sl:
                        exit_r = -1.0
                        exited = True
                    elif bh >= tp:
                        exit_r = tp_rr
                        exited = True
                else:
                    if bh >= sl:
                        exit_r = -1.0
                        exited = True
                    elif bl <= tp:
                        exit_r = tp_rr
                        exited = True

                if exited:
                    all_trade_rs.append(exit_r - friction_r)
                    in_trade = False
                    last_exit_bar = i
                continue

            if (i - last_exit_bar) < cooldown_bars:
                continue

            # ADX gate
            if adx_filter > 0 and adx[i] < adx_filter:
                continue

            c_bar = c[i]
            o_bar = o[i]
            h_bar = h[i]
            l_bar = l[i]
            a_bar = atr[i]

            came_from_above = (o_bar >= e_shelf[i] * 0.998) or (c[i - 1] >= e_shelf[i - 1] * 0.998)
            came_from_below = (o_bar <= e_shelf[i] * 1.002) or (c[i - 1] <= e_shelf[i - 1] * 1.002)

            sig = 0

            # LONG Regime
            if e_shelf[i] > e_macro[i] and came_from_above and lw[i] >= wick_ratio:
                if (l_bar <= e_shelf[i] * 1.002) and (c_bar >= e_shelf[i]):
                    recent_below = np.max(bars_below_macro[max(0, i - 35) : i])
                    is_reclaim = (recent_below >= dwell_bars)
                    is_trend = (bars_above_macro[max(0, i - 1)] >= dwell_bars)

                    if setup_type == "RECLAIM_SPRING" and is_reclaim:
                        sig = 1
                    elif setup_type == "TREND_PULLBACK" and is_trend:
                        sig = 1
                    elif setup_type == "ALL_TOUCHES" and (is_reclaim or is_trend):
                        sig = 1

            # SHORT Regime
            elif e_shelf[i] < e_macro[i] and came_from_below and uw[i] >= wick_ratio:
                if (h_bar >= e_shelf[i] * 0.998) and (c_bar <= e_shelf[i]):
                    recent_above = np.max(bars_above_macro[max(0, i - 35) : i])
                    is_reclaim = (recent_above >= dwell_bars)
                    is_trend = (bars_below_macro[max(0, i - 1)] >= dwell_bars)

                    if setup_type == "RECLAIM_SPRING" and is_reclaim:
                        sig = -1
                    elif setup_type == "TREND_PULLBACK" and is_trend:
                        sig = -1
                    elif setup_type == "ALL_TOUCHES" and (is_reclaim or is_trend):
                        sig = -1

            if sig != 0:
                next_bar = i + 1
                if next_bar >= n or t[next_bar] < JAN_2024_TS:
                    continue

                entry_p = o[next_bar]
                
                # SL calculations
                if sl_mode == "TIGHT_0.15":
                    stop_p = (l_bar - 0.15 * a_bar) if sig == 1 else (h_bar + 0.15 * a_bar)
                elif sl_mode == "BASELINE_0.30":
                    stop_p = (l_bar - 0.30 * a_bar) if sig == 1 else (h_bar + 0.30 * a_bar)
                elif sl_mode == "WIDE_0.50":
                    stop_p = (l_bar - 0.50 * a_bar) if sig == 1 else (h_bar + 0.50 * a_bar)
                elif sl_mode == "STRUCTURAL_BASELINE":
                    stop_p = (min(l_bar, e_macro[i]) - 0.25 * a_bar) if sig == 1 else (max(h_bar, e_macro[i]) + 0.25 * a_bar)

                risk_d = abs(entry_p - stop_p)
                if risk_d <= 0 or (risk_d / entry_p) < 0.0005:
                    continue

                target_p = (entry_p + tp_rr * risk_d) if sig == 1 else (entry_p - tp_rr * risk_d)

                in_trade = True
                side = sig
                ep = entry_p
                sl = stop_p
                tp = target_p
                rd = risk_d

    if len(all_trade_rs) < 25:
        return None

    r_arr = np.array(all_trade_rs)
    trades = len(r_arr)
    wins = np.sum(r_arr > 0)
    win_rate = (wins / trades) * 100.0
    net_r = np.sum(r_arr)
    gross_win = np.sum(r_arr[r_arr > 0])
    gross_loss = abs(np.sum(r_arr[r_arr <= 0]))
    pf = (gross_win / gross_loss) if gross_loss > 0 else 99.0

    # Max Drawdown in R
    eq = np.cumsum(r_arr)
    peak = np.maximum.accumulate(eq)
    dd = peak - eq
    max_dd = np.max(dd) if len(dd) > 0 else 0.0
    calmar = (net_r / max_dd) if max_dd > 0 else 0.0
    expectancy = np.mean(r_arr)

    # Student's t-test (H0: mean = 0 vs H1: mean > 0)
    se = np.std(r_arr, ddof=1) / np.sqrt(trades) if trades > 1 else 1.0
    t_stat = expectancy / se if se > 0 else 0.0
    p_val = stats.t.sf(t_stat, df=trades - 1) if trades > 1 else 1.0

    return {
        "shelf_ema": shelf_ema,
        "macro_ema": macro_ema,
        "dwell_bars": dwell_bars,
        "dwell_hours": dwell_bars * 4,
        "setup_type": setup_type,
        "wick_ratio": wick_ratio,
        "sl_mode": sl_mode,
        "tp_rr": tp_rr,
        "adx_filter": adx_filter,
        "trades": trades,
        "win_rate": round(win_rate, 1),
        "net_r": round(net_r, 2),
        "pf": round(pf, 2),
        "max_dd": round(max_dd, 2),
        "calmar": round(calmar, 2),
        "expectancy": round(expectancy, 4),
        "t_stat": round(t_stat, 2),
        "p_val": round(p_val, 4),
    }


def main():
    print("=" * 100)
    print("QUANTITATIVE SYSTEM SWEEP: MULTI-DIMENSIONAL PARAMETER & REGIME OPTIMIZATION")
    print("Universe: Optimal Basket Assets | Period: Jan 2024 to Nov 2026")
    print("=" * 100)

    # Pre-cache all asset data
    print("Pre-caching asset arrays...")
    basket_symbols = [x["symbol"] for x in OPTIMAL_ALPHA_BASKET]
    cached_data_list = []
    for sym in basket_symbols:
        p = os.path.join(DATA_DIR, f"{sym}.parquet")
        if os.path.exists(p):
            d = precalculate_asset_arrays(p)
            if d:
                cached_data_list.append(d)
    print(f"Pre-cached {len(cached_data_list)} assets.")

    # Sweep dimensions:
    dwell_options = [3, 6, 12, 18, 24]  # 12h, 24h, 48h, 72h, 96h
    ema_pairs = [(55, 144), (55, 233), (89, 233), (89, 377)]
    setups = ["RECLAIM_SPRING", "TREND_PULLBACK"]
    wicks = [0.20, 0.25, 0.33]
    sl_modes = ["BASELINE_0.30", "STRUCTURAL_BASELINE", "TIGHT_0.15"]
    tp_targets = [2.5, 3.0, 3.5, 4.0]
    adx_filters = [0, 20]

    # Strategic Sweep:
    results = []
    
    # 1. Sweep Dwell Times (holding other baseline parameters constant)
    print("\n--- SWEEP 1: DWELL DURATION IMPACT (12h to 96h) ---")
    for d in dwell_options:
        for st in setups:
            res = evaluate_parameter_set(
                cached_data_list, shelf_ema=89, macro_ema=233,
                dwell_bars=d, setup_type=st, wick_ratio=0.25,
                sl_mode="BASELINE_0.30", tp_rr=3.0, adx_filter=0
            )
            if res:
                results.append(res)

    df_res = pd.DataFrame(results)
    if not df_res.empty:
        print(df_res[["setup_type", "dwell_hours", "trades", "win_rate", "net_r", "pf", "max_dd", "calmar", "expectancy", "t_stat", "p_val"]].to_string(index=False))

    # 2. Sweep EMA Pairs
    print("\n--- SWEEP 2: EMA SHELF & MACRO BASELINE PAIRS ---")
    ema_res = []
    for s_ema, m_ema in ema_pairs:
        for st in setups:
            res = evaluate_parameter_set(
                cached_data_list, shelf_ema=s_ema, macro_ema=m_ema,
                dwell_bars=6, setup_type=st, wick_ratio=0.25,
                sl_mode="BASELINE_0.30", tp_rr=3.0, adx_filter=0
            )
            if res:
                ema_res.append(res)
    df_ema = pd.DataFrame(ema_res)
    if not df_ema.empty:
        print(df_ema[["shelf_ema", "macro_ema", "setup_type", "trades", "win_rate", "net_r", "pf", "calmar", "expectancy", "t_stat"]].to_string(index=False))

    # 3. Sweep SL Geometry & Target RR
    print("\n--- SWEEP 3: STOP LOSS GEOMETRY & TARGET RR MATRIX ---")
    sl_tp_res = []
    for slm in sl_modes:
        for tp in tp_targets:
            res = evaluate_parameter_set(
                cached_data_list, shelf_ema=89, macro_ema=233,
                dwell_bars=6, setup_type="RECLAIM_SPRING", wick_ratio=0.25,
                sl_mode=slm, tp_rr=tp, adx_filter=0
            )
            if res:
                sl_tp_res.append(res)
    df_sltp = pd.DataFrame(sl_tp_res)
    if not df_sltp.empty:
        print(df_sltp[["sl_mode", "tp_rr", "trades", "win_rate", "net_r", "pf", "calmar", "expectancy", "t_stat"]].to_string(index=False))

    # 4. Sweep ADX & Wick Absorption Filter
    print("\n--- SWEEP 4: ADX REGIME & WICK ABSORPTION RATIO ---")
    filter_res = []
    for w in wicks:
        for adx_g in [0, 20, 25]:
            res = evaluate_parameter_set(
                cached_data_list, shelf_ema=89, macro_ema=233,
                dwell_bars=6, setup_type="RECLAIM_SPRING", wick_ratio=w,
                sl_mode="BASELINE_0.30", tp_rr=3.5, adx_filter=adx_g
            )
            if res:
                filter_res.append(res)
    df_filter = pd.DataFrame(filter_res)
    if not df_filter.empty:
        print(df_filter[["wick_ratio", "adx_filter", "trades", "win_rate", "net_r", "pf", "calmar", "expectancy", "t_stat", "p_val"]].to_string(index=False))

if __name__ == "__main__":
    main()
