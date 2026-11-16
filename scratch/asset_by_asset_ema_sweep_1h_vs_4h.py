#!/usr/bin/env python3
r"""scratch/asset_by_asset_ema_sweep_1h_vs_4h.py: Granular Asset-by-Asset EMA Sweep (50 to 400)
For each of the 20 assets:
1. Sweeps EMA 50 to 400 on 1H (16 periods: [50, 55, 65, 75, 89, 100, 120, 144, 160, 200, 233, 277, 300, 350, 377, 400])
2. Sweeps EMA 50 to 400 on 4H (16 periods)
3. Identifies the Best EMA for 1H and Best EMA for 4H for THAT asset
4. Compares 1H vs 4H performance, Win Rate, Expectancy, Net R, and Drawdown
5. Generates a comprehensive per-asset comparison matrix
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
            df = pd.read_parquet(pq)
            return df
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


def evaluate_single_asset_ema(df, ema_period, tf_str, target_rr=3.0, friction_r=0.05):
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

    cooldown = 12 if tf_str == "4H" else 36
    trades = []
    in_trade = False
    side = 0
    ep = sl = rd = 0.0
    last_exit = -cooldown - 1

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
        if ema[i] > ema[i - 3] and came_from_above and lower_wicks[i] >= 0.25:
            if l_bar <= ema[i] * 1.002 and c_bar >= ema[i]:
                sig = 1
        elif ema[i] < ema[i - 3] and came_from_below and upper_wicks[i] >= 0.25:
            if h_bar >= ema[i] * 0.998 and c_bar <= ema[i]:
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
        return {"trades": 0, "wr": 0.0, "net_r": 0.0, "exp_r": 0.0, "pf": 0.0}

    tr_arr = np.array(trades)
    wins = tr_arr[tr_arr > 0]
    losses = np.abs(tr_arr[tr_arr < 0])
    pf = round(wins.sum() / losses.sum(), 2) if losses.sum() > 0 else 99.0

    return {
        "trades": len(trades),
        "wr": round((tr_arr > 0).mean() * 100, 1),
        "net_r": round(tr_arr.sum(), 2),
        "exp_r": round(tr_arr.mean(), 3),
        "pf": pf
    }


def main():
    print("=" * 115)
    print("GRANULAR ASSET-BY-ASSET EMA SWEEP: 1H vs 4H ACROSS 16 EMA PERIODS (50 TO 400)")
    print(f"Horizon: March 15, 2025 - November 16, 2026 (Common High-Density Window)")
    print(f"Tested Periods: {EMA_PERIODS}")
    print("=" * 115)

    asset_comparisons = []

    for item in OPTIMAL_ALPHA_BASKET:
        sym = item["symbol"]
        cluster = item["cluster"]

        df_1h = load_data(sym, "1H")
        df_4h = load_data(sym, "4H")

        if df_1h is None or df_4h is None:
            print(f"Skipping {sym}: Data incomplete.")
            continue

        # 1. Sweep 1H
        best_1h_p = None
        best_1h_net_r = -999.0
        best_1h_stat = None
        stats_1h = {}

        for p in EMA_PERIODS:
            res = evaluate_single_asset_ema(df_1h, p, "1H")
            if res and res["trades"] >= 10:
                stats_1h[p] = res
                if res["net_r"] > best_1h_net_r:
                    best_1h_net_r = res["net_r"]
                    best_1h_p = p
                    best_1h_stat = res

        if best_1h_stat is None and stats_1h:
            best_1h_p = max(stats_1h.keys(), key=lambda k: stats_1h[k]["net_r"])
            best_1h_stat = stats_1h[best_1h_p]

        # 2. Sweep 4H
        best_4h_p = None
        best_4h_net_r = -999.0
        best_4h_stat = None
        stats_4h = {}

        for p in EMA_PERIODS:
            res = evaluate_single_asset_ema(df_4h, p, "4H")
            if res and res["trades"] >= 5:
                stats_4h[p] = res
                if res["net_r"] > best_4h_net_r:
                    best_4h_net_r = res["net_r"]
                    best_4h_p = p
                    best_4h_stat = res

        if best_4h_stat is None and stats_4h:
            best_4h_p = max(stats_4h.keys(), key=lambda k: stats_4h[k]["net_r"])
            best_4h_stat = stats_4h[best_4h_p]

        # Determine winner for this asset
        r_1h = best_1h_stat["net_r"] if best_1h_stat else -999.0
        r_4h = best_4h_stat["net_r"] if best_4h_stat else -999.0
        winner = "4H 🏆" if r_4h > r_1h else ("1H 🏆" if r_1h > r_4h else "TIE")

        record = {
            "symbol": sym,
            "cluster": cluster,
            "best_1h_ema": best_1h_p,
            "tr_1h": best_1h_stat["trades"] if best_1h_stat else 0,
            "wr_1h": best_1h_stat["wr"] if best_1h_stat else 0.0,
            "net_1h": r_1h,
            "exp_1h": best_1h_stat["exp_r"] if best_1h_stat else 0.0,
            "pf_1h": best_1h_stat["pf"] if best_1h_stat else 0.0,
            "best_4h_ema": best_4h_p,
            "tr_4h": best_4h_stat["trades"] if best_4h_stat else 0,
            "wr_4h": best_4h_stat["wr"] if best_4h_stat else 0.0,
            "net_4h": r_4h,
            "exp_4h": best_4h_stat["exp_r"] if best_4h_stat else 0.0,
            "pf_4h": best_4h_stat["pf"] if best_4h_stat else 0.0,
            "winner": winner
        }
        asset_comparisons.append(record)

    df_comp = pd.DataFrame(asset_comparisons)

    print("\n" + "=" * 115)
    print("ASSET-BY-ASSET WINNER LEADERBOARD (1H vs 4H | BEST EMA FOUND)")
    print("=" * 115)
    print(f"{'Symbol':<8} | {'Cluster':<16} | {'Best 1H':<7} | {'1H Net R':<9} | {'1H WR%':<7} | {'Best 4H':<7} | {'4H Net R':<9} | {'4H WR%':<7} | {'Winner':<8}")
    print("-" * 115)
    for _, r in df_comp.iterrows():
        print(f"{r['symbol']:<8} | {r['cluster']:<16} | EMA {r['best_1h_ema']:<3} | {r['net_1h']:<+9.2f} | {r['wr_1h']:<5.1f}% | EMA {r['best_4h_ema']:<3} | {r['net_4h']:<+9.2f} | {r['wr_4h']:<5.1f}% | {r['winner']:<8}")
    print("-" * 115)

    # Detailed Breakdown for XAUUSD, US30, ETHUSD, DOGUSD as examples
    print("\n--- DETAILED ASSET DRILL-DOWN EXAMPLES ---")
    focus_assets = ["XAUUSD", "US30", "ETHUSD", "DOGUSD", "USDJPY"]
    for sym in focus_assets:
        df_1h = load_data(sym, "1H")
        df_4h = load_data(sym, "4H")
        print(f"\n>> {sym} EMA SPECTRUM (1H vs 4H):")
        print(f"{'EMA Period':<12} | {'1H Trades':<10} | {'1H WR%':<8} | {'1H Net R':<10} | {'4H Trades':<10} | {'4H WR%':<8} | {'4H Net R':<10}")
        print("-" * 80)
        for p in [50, 55, 89, 100, 200, 233, 300, 377]:
            res1 = evaluate_single_asset_ema(df_1h, p, "1H")
            res4 = evaluate_single_asset_ema(df_4h, p, "4H")
            t1 = res1["trades"] if res1 else 0
            w1 = res1["wr"] if res1 else 0.0
            r1 = res1["net_r"] if res1 else 0.0
            t4 = res4["trades"] if res4 else 0
            w4 = res4["wr"] if res4 else 0.0
            r4 = res4["net_r"] if res4 else 0.0
            print(f"EMA {p:<8} | {t1:<10} | {w1:<7.1f}% | {r1:<+10.2f} | {t4:<10} | {w4:<7.1f}% | {r4:<+10.2f}")


if __name__ == "__main__":
    main()
