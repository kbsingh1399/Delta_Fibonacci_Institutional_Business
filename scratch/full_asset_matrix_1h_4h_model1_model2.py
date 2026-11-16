#!/usr/bin/env python3
r"""scratch/full_asset_matrix_1h_4h_model1_model2.py
Per-Asset Deep Dive:
For each asset:
1. 1H Timeframe:
   - Model 1 (Pullback Rejection Shelf): sweep 50 to 400 EMA -> Best EMA
   - Model 2 (Macro Dwell & Reclaim Spring): sweep 50 to 400 EMA -> Best EMA
2. 4H Timeframe:
   - Model 1 (Pullback Rejection Shelf): sweep 50 to 400 EMA -> Best EMA
   - Model 2 (Macro Dwell & Reclaim Spring): sweep 50 to 400 EMA -> Best EMA
3. Champion Verdict for that asset
4. Repeats sequentially for all 20 assets.
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

EMA_PERIODS = [50, 55, 65, 75, 89, 100, 120, 144, 160, 200, 233, 277, 300, 350, 377, 400]
START_TS = int(datetime(2025, 3, 15, tzinfo=timezone.utc).timestamp())


def load_data(symbol, tf_str):
    if tf_str == "4H":
        pq = os.path.join(DATA_DIR, f"{symbol}.parquet")
        if os.path.exists(pq):
            return pd.read_parquet(pq)
    # Pull from MT5
    if not mt5.initialize():
        return None
    tf = mt5.TIMEFRAME_H1 if tf_str == "1H" else mt5.TIMEFRAME_H4
    rates = mt5.copy_rates_from_pos(symbol, tf, 0, 12000)
    if rates is None or len(rates) < 300:
        return None
    df = pd.DataFrame(rates)
    df.rename(columns={"time": "timestamp"}, inplace=True)
    return df


def simulate_model_for_ema(df, ema_period, model_type="MODEL_1", tf_str="4H", friction_r=0.05):
    c = df["close"].to_numpy(dtype=float)
    o = df["open"].to_numpy(dtype=float)
    h = df["high"].to_numpy(dtype=float)
    l = df["low"].to_numpy(dtype=float)
    t = df["timestamp"].to_numpy(dtype=int)
    n = len(c)

    if n < ema_period + 50:
        return None

    ema = calculate_ema(c, ema_period)
    atr = calculate_atr(h, l, c, 14)

    rng = np.where(h - l <= 0, 1e-6, h - l)
    body_tops = np.maximum(o, c)
    body_bots = np.minimum(o, c)
    upper_wicks = (h - body_tops) / rng
    lower_wicks = (body_bots - l) / rng

    # Dwell trackers for Model 2
    min_dwell = 3 if tf_str == "4H" else 12
    max_dwell = 25 if tf_str == "4H" else 60
    dwell_lookback = 35 if tf_str == "4H" else 100

    bars_above = np.zeros(n, dtype=int)
    bars_below = np.zeros(n, dtype=int)
    for i in range(1, n):
        if c[i] > ema[i]:
            bars_above[i] = bars_above[i - 1] + 1
            bars_below[i] = 0
        elif c[i] < ema[i]:
            bars_below[i] = bars_below[i - 1] + 1
            bars_above[i] = 0

    cooldown = 12 if tf_str == "4H" else 36
    trades = []
    in_trade = False
    side = 0
    ep = sl = rd = 0.0
    last_exit = -cooldown - 1
    target_rr = 3.5 if model_type == "MODEL_2" else 3.0
    wick_thresh = 0.33 if model_type == "MODEL_2" else 0.25

    for i in range(ema_period + 10, n - 1):
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
        came_from_above = (o_bar >= ema[i] * 0.998) or (c[i - 1] >= ema[i] * 0.998)
        came_from_below = (o_bar <= ema[i] * 1.002) or (c[i - 1] <= ema[i] * 1.002)

        sig = 0
        if model_type == "MODEL_1":
            # Pullback Rejection: Trend matches EMA slope, wicks reject EMA, closes above/below
            if ema[i] > ema[i - 3] and came_from_above and lower_wicks[i] >= wick_thresh:
                if l_bar <= ema[i] * 1.002 and c_bar >= ema[i]:
                    sig = 1
            elif ema[i] < ema[i - 3] and came_from_below and upper_wicks[i] >= wick_thresh:
                if h_bar >= ema[i] * 0.998 and c_bar <= ema[i]:
                    sig = -1

        elif model_type == "MODEL_2":
            # Macro Dwell Reclaim Spring: Price dwelled on other side of EMA, then springs back & defends shelf
            if came_from_above and lower_wicks[i] >= wick_thresh and l_bar <= ema[i] * 1.002 and c_bar >= ema[i]:
                recent_under = np.max(bars_below[max(0, i - dwell_lookback) : i])
                if min_dwell <= recent_under <= max_dwell:
                    sig = 1
            elif came_from_below and upper_wicks[i] >= wick_thresh and h_bar >= ema[i] * 0.998 and c_bar <= ema[i]:
                recent_over = np.max(bars_above[max(0, i - dwell_lookback) : i])
                if min_dwell <= recent_over <= max_dwell:
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

    if not trades:
        return {"period": ema_period, "trades": 0, "wr": 0.0, "net_r": 0.0, "exp_r": 0.0, "pf": 0.0}

    tr_arr = np.array(trades)
    wins = tr_arr[tr_arr > 0]
    losses = np.abs(tr_arr[tr_arr < 0])
    pf = round(wins.sum() / losses.sum(), 2) if losses.sum() > 0 else 99.0

    return {
        "period": ema_period,
        "trades": len(trades),
        "wr": round((tr_arr > 0).mean() * 100, 1),
        "net_r": round(tr_arr.sum(), 2),
        "exp_r": round(tr_arr.mean(), 3),
        "pf": pf
    }


def find_best_ema_for_config(df, model_type, tf_str):
    best_p = None
    best_net_r = -999.0
    best_res = None
    all_res = {}

    for p in EMA_PERIODS:
        res = simulate_model_for_ema(df, p, model_type=model_type, tf_str=tf_str)
        if res and res["trades"] >= (8 if tf_str == "1H" else 4):
            all_res[p] = res
            if res["net_r"] > best_net_r:
                best_net_r = res["net_r"]
                best_p = p
                best_res = res

    if best_res is None and all_res:
        best_p = max(all_res.keys(), key=lambda k: all_res[k]["net_r"])
        best_res = all_res[best_p]

    if best_res is None:
        return {"period": 89, "trades": 0, "wr": 0.0, "net_r": 0.0, "exp_r": 0.0, "pf": 0.0}
    return best_res


def main():
    print("=" * 115)
    print("ASSET-BY-ASSET DEEP DIVE: 1H & 4H x MODEL 1 & MODEL 2 (EMA 50 TO 400)")
    print("Testing 16 EMA periods across each configuration for every single asset")
    print("=" * 115)

    asset_reports = []

    for item in OPTIMAL_ALPHA_BASKET:
        sym = item["symbol"]
        cluster = item["cluster"]

        df_1h = load_data(sym, "1H")
        df_4h = load_data(sym, "4H")

        if df_1h is None or df_4h is None:
            continue

        # 1. 1H Model 1
        res_1h_m1 = find_best_ema_for_config(df_1h, "MODEL_1", "1H")
        # 2. 1H Model 2
        res_1h_m2 = find_best_ema_for_config(df_1h, "MODEL_2", "1H")
        # 3. 4H Model 1
        res_4h_m1 = find_best_ema_for_config(df_4h, "MODEL_1", "4H")
        # 4. 4H Model 2
        res_4h_m2 = find_best_ema_for_config(df_4h, "MODEL_2", "4H")

        # Find overall winning configuration for this asset
        configs = [
            ("1H M1", res_1h_m1),
            ("1H M2", res_1h_m2),
            ("4H M1", res_4h_m1),
            ("4H M2", res_4h_m2)
        ]
        best_cfg_name, best_cfg_res = max(configs, key=lambda x: x[1]["net_r"])

        print(f"\n=========================================================================================")
        print(f"ASSET: {sym} ({cluster})")
        print(f"=========================================================================================")
        print(f"  [1H Timeframe]")
        print(f"    - Model 1 (Pullback Shelf) : Best EMA {res_1h_m1['period']:<3} | Trades: {res_1h_m1['trades']:<3} | WR: {res_1h_m1['wr']:<4.1f}% | Net R: {res_1h_m1['net_r']:<+7.2f} | Exp: {res_1h_m1['exp_r']:<+6.3f}R | PF: {res_1h_m1['pf']}")
        print(f"    - Model 2 (Reclaim Spring) : Best EMA {res_1h_m2['period']:<3} | Trades: {res_1h_m2['trades']:<3} | WR: {res_1h_m2['wr']:<4.1f}% | Net R: {res_1h_m2['net_r']:<+7.2f} | Exp: {res_1h_m2['exp_r']:<+6.3f}R | PF: {res_1h_m2['pf']}")
        print(f"  [4H Timeframe]")
        print(f"    - Model 1 (Pullback Shelf) : Best EMA {res_4h_m1['period']:<3} | Trades: {res_4h_m1['trades']:<3} | WR: {res_4h_m1['wr']:<4.1f}% | Net R: {res_4h_m1['net_r']:<+7.2f} | Exp: {res_4h_m1['exp_r']:<+6.3f}R | PF: {res_4h_m1['pf']}")
        print(f"    - Model 2 (Reclaim Spring) : Best EMA {res_4h_m2['period']:<3} | Trades: {res_4h_m2['trades']:<3} | WR: {res_4h_m2['wr']:<4.1f}% | Net R: {res_4h_m2['net_r']:<+7.2f} | Exp: {res_4h_m2['exp_r']:<+6.3f}R | PF: {res_4h_m2['pf']}")
        print(f"  >> CHAMPION PROFILE FOR {sym}: {best_cfg_name} on EMA {best_cfg_res['period']} ({best_cfg_res['net_r']:+7.2f} R, {best_cfg_res['wr']}% WR, PF {best_cfg_res['pf']})")

        asset_reports.append({
            "symbol": sym,
            "cluster": cluster,
            "1h_m1_ema": res_1h_m1["period"], "1h_m1_net": res_1h_m1["net_r"], "1h_m1_wr": res_1h_m1["wr"],
            "1h_m2_ema": res_1h_m2["period"], "1h_m2_net": res_1h_m2["net_r"], "1h_m2_wr": res_1h_m2["wr"],
            "4h_m1_ema": res_4h_m1["period"], "4h_m1_net": res_4h_m1["net_r"], "4h_m1_wr": res_4h_m1["wr"],
            "4h_m2_ema": res_4h_m2["period"], "4h_m2_net": res_4h_m2["net_r"], "4h_m2_wr": res_4h_m2["wr"],
            "champion_setup": f"{best_cfg_name} (EMA {best_cfg_res['period']})",
            "champion_net_r": best_cfg_res["net_r"],
            "champion_wr": best_cfg_res["wr"]
        })

    # Summary Table
    df_all = pd.DataFrame(asset_reports)
    print("\n" + "=" * 125)
    print("MASTER SUMMARY TABLE ACROSS ALL 20 ASSETS")
    print("=" * 125)
    print(f"{'Symbol':<8} | {'Cluster':<14} | {'1H M1 Best':<11} | {'1H M2 Best':<11} | {'4H M1 Best':<11} | {'4H M2 Best':<11} | {'Overall Champion':<22} | {'Net R':<8}")
    print("-" * 125)
    for _, r in df_all.iterrows():
        print(f"{r['symbol']:<8} | {r['cluster']:<14} | EMA {r['1h_m1_ema']:<3} {r['1h_m1_net']:>+5.1f} | EMA {r['1h_m2_ema']:<3} {r['1h_m2_net']:>+5.1f} | EMA {r['4h_m1_ema']:<3} {r['4h_m1_net']:>+5.1f} | EMA {r['4h_m2_ema']:<3} {r['4h_m2_net']:>+5.1f} | {r['champion_setup']:<22} | {r['champion_net_r']:>+7.2f}R")
    print("=" * 125)


if __name__ == "__main__":
    main()
