#!/usr/bin/env python3
r"""scratch/quant_ema_sweep_50_to_400.py: Comprehensive EMA Optimization Analysis (50 to 400)
Evaluates:
1. Single EMA Bounce / Rejection Efficiency across 16 EMA periods:
   [50, 55, 65, 75, 89, 100, 120, 144, 160, 200, 233, 277, 300, 350, 377, 400]
2. Asset-by-Asset Optimum Leaderboard: Which EMA period performs best for each of the 20 assets?
3. Cluster Patterns: Do Crypto, Equities, Forex, Indices, and Gold favor different EMAs?
4. Fast / Macro Dual-EMA Pairing Matrix: (Fast Shelf x Macro Baseline)
"""

import os
import sys
import numpy as np
import pandas as pd
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "FundedNext_Data")
sys.path.insert(0, BASE_DIR)

from engine.mt5_alpha_trader_monitor import OPTIMAL_ALPHA_BASKET, calculate_ema, calculate_atr

EMA_PERIODS = [50, 55, 65, 75, 89, 100, 120, 144, 160, 200, 233, 277, 300, 350, 377, 400]
JAN_2024_TS = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp())


def test_single_ema_rejection(symbol, ema_period, target_rr=3.0, friction_r=0.05, cooldown_bars=8):
    parquet_path = os.path.join(DATA_DIR, f"{symbol}.parquet")
    if not os.path.exists(parquet_path):
        return None

    df = pd.read_parquet(parquet_path)
    if len(df) < ema_period + 100:
        return None

    c = df["close"].to_numpy(dtype=float)
    o = df["open"].to_numpy(dtype=float)
    h = df["high"].to_numpy(dtype=float)
    l = df["low"].to_numpy(dtype=float)
    t = df["timestamp"].to_numpy(dtype=int)
    n = len(c)

    ema = calculate_ema(c, ema_period)
    atr = calculate_atr(h, l, c, 14)

    rng = np.where(h - l <= 0, 1e-6, h - l)
    body_tops = np.maximum(o, c)
    body_bots = np.minimum(o, c)
    upper_wicks = (h - body_tops) / rng
    lower_wicks = (body_bots - l) / rng

    trades = []
    in_trade = False
    side = 0
    ep = sl = rd = 0.0
    last_exit = -cooldown_bars - 1

    for i in range(ema_period + 20, n - 1):
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

        if (i - last_exit) < cooldown_bars:
            continue

        c_bar = c[i]; o_bar = o[i]; h_bar = h[i]; l_bar = l[i]; a_bar = atr[i]
        came_from_above = (o_bar >= ema[i] * 0.998) or (c[i - 1] >= ema[i] * 0.998)
        came_from_below = (o_bar <= ema[i] * 1.002) or (c[i - 1] <= ema[i] * 1.002)

        sig = 0
        # Long Rejection Shelf: Trend is up (EMA sloping up), price pulls back to EMA, forms lower wick, closes above
        if ema[i] > ema[i - 3] and came_from_above:
            if lower_wicks[i] >= 0.25 and l_bar <= ema[i] * 1.002 and c_bar >= ema[i]:
                sig = 1

        # Short Rejection Shelf: Trend is down (EMA sloping down), price rallies to EMA, forms upper wick, closes below
        elif ema[i] < ema[i - 3] and came_from_below:
            if upper_wicks[i] >= 0.25 and h_bar >= ema[i] * 0.998 and c_bar <= ema[i]:
                sig = -1

        if sig != 0:
            next_b = i + 1
            if next_b >= n or t[next_b] < JAN_2024_TS:
                continue

            entry_p = o[next_b]
            stop_p = (l_bar - 0.30 * a_bar) if sig == 1 else (h_bar + 0.30 * a_bar)
            risk_d = abs(entry_p - stop_p)
            if risk_d <= 0 or (risk_d / entry_p) < 0.0005:
                continue

            in_trade = True
            side = sig
            ep = entry_p
            sl = stop_p
            rd = risk_d

    if not trades:
        return {
            "period": ema_period,
            "trades": 0,
            "wr": 0.0,
            "net_r": 0.0,
            "exp_r": 0.0,
            "pf": 0.0
        }

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


def main():
    print("=" * 105)
    print("INSTITUTIONAL QUANT SWEEP: EMA PERIODS 50 TO 400 ACROSS 20 BASKET ASSETS")
    print("Testing 16 EMAs: [50, 55, 65, 75, 89, 100, 120, 144, 160, 200, 233, 277, 300, 350, 377, 400]")
    print("Evaluation Period: Jan 1, 2024 to Nov 16, 2026 (Nearly 3 Years of H4 Data)")
    print("Geometry: Wick Rejection >= 25% | Stop: 0.30 ATR | Target: 3.0R | Friction: 0.05R")
    print("=" * 105)

    asset_best_map = {}
    period_aggregate_r = {p: 0.0 for p in EMA_PERIODS}
    period_aggregate_trades = {p: 0 for p in EMA_PERIODS}
    period_aggregate_wins = {p: 0 for p in EMA_PERIODS}

    results_table = []

    for item in OPTIMAL_ALPHA_BASKET:
        sym = item["symbol"]
        cluster = item["cluster"]

        best_p = None
        best_net_r = -999.0
        best_exp = -999.0
        best_wr = 0.0
        best_trades = 0
        best_pf = 0.0

        sym_period_stats = {}

        for p in EMA_PERIODS:
            res = test_single_ema_rejection(sym, p)
            if not res or res["trades"] == 0:
                continue

            period_aggregate_r[p] += res["net_r"]
            period_aggregate_trades[p] += res["trades"]
            period_aggregate_wins[p] += int(res["trades"] * (res["wr"] / 100.0))

            sym_period_stats[p] = res

            if res["net_r"] > best_net_r and res["trades"] >= 15:
                best_net_r = res["net_r"]
                best_exp = res["exp_r"]
                best_p = p
                best_wr = res["wr"]
                best_trades = res["trades"]
                best_pf = res["pf"]

        # If no EMA had >= 15 trades, take max
        if best_p is None and sym_period_stats:
            best_p = max(sym_period_stats.keys(), key=lambda k: sym_period_stats[k]["net_r"])
            best_net_r = sym_period_stats[best_p]["net_r"]
            best_exp = sym_period_stats[best_p]["exp_r"]
            best_wr = sym_period_stats[best_p]["wr"]
            best_trades = sym_period_stats[best_p]["trades"]
            best_pf = sym_period_stats[best_p]["pf"]

        asset_best_map[sym] = {
            "cluster": cluster,
            "best_ema": best_p,
            "net_r": best_net_r,
            "exp_r": best_exp,
            "wr": best_wr,
            "trades": best_trades,
            "pf": best_pf,
            "ema89_r": sym_period_stats.get(89, {}).get("net_r", 0.0),
            "ema200_r": sym_period_stats.get(200, {}).get("net_r", 0.0),
            "ema233_r": sym_period_stats.get(233, {}).get("net_r", 0.0),
        }

    # Print Aggregate Leaderboard across all assets
    print("\n--- 1. OVERALL EMA PERIOD PERFORMANCE (RANKED BY TOTAL NET R ACROSS ALL 20 ASSETS) ---")
    agg_list = []
    for p in EMA_PERIODS:
        t = period_aggregate_trades[p]
        w = period_aggregate_wins[p]
        r = period_aggregate_r[p]
        wr = round(w / t * 100.0, 1) if t > 0 else 0.0
        exp = round(r / t, 3) if t > 0 else 0.0
        agg_list.append({"EMA": p, "Trades": t, "WinRate%": wr, "Total Net R": round(r, 2), "Expectancy (R/tr)": exp})

    df_agg = pd.DataFrame(agg_list).sort_values("Total Net R", ascending=False).reset_index(drop=True)
    print(df_agg.to_string(index=False))

    # Print Asset by Asset Best EMA
    print("\n--- 2. ASSET-BY-ASSET OPTIMAL EMA LEADERBOARD ---")
    print(f"{'Symbol':<8} | {'Cluster':<16} | {'Best EMA':<8} | {'Trades':<6} | {'WinRate':<7} | {'Net R':<8} | {'Exp (R)':<7} | {'PF':<5} | {'EMA 89 R':<8} | {'EMA 200 R':<9} | {'EMA 233 R':<9}")
    print("-" * 115)
    for sym, d in asset_best_map.items():
        print(f"{sym:<8} | {d['cluster']:<16} | {d['best_ema']:<8} | {d['trades']:<6} | {d['wr']:<6.1f}% | {d['net_r']:<+8.2f} | {d['exp_r']:<+7.3f} | {d['pf']:<5.2f} | {d['ema89_r']:<+8.2f} | {d['ema200_r']:<+9.2f} | {d['ema233_r']:<+9.2f}")
    print("-" * 115)

    # Cluster Breakdown of Best EMAs
    print("\n--- 3. CLUSTER EMA PREFERENCES ---")
    df_assets = pd.DataFrame.from_dict(asset_best_map, orient="index").reset_index()
    for cluster, grp in df_assets.groupby("cluster"):
        emas = grp["best_ema"].tolist()
        print(f"Cluster: {cluster:<16} -> Preferred EMAs: {emas} (Dominant: {max(set(emas), key=emas.count)})")

    # Fast / Macro Pairing Grid
    print("\n--- 4. FAST SHELF x MACRO BASELINE PAIRING SWEEP ---")
    print("Testing combinations of Fast EMA (50, 55, 75, 89, 100) vs Macro EMA (144, 200, 233, 300, 377)")
    
    fast_emas = [50, 55, 75, 89, 100]
    macro_emas = [144, 200, 233, 300, 377]
    pair_results = []

    for f_ema in fast_emas:
        for m_ema in macro_emas:
            if f_ema >= m_ema:
                continue
            
            tot_trades = 0
            tot_net_r = 0.0
            tot_wins = 0

            for item in OPTIMAL_ALPHA_BASKET:
                sym = item["symbol"]
                pq = os.path.join(DATA_DIR, f"{sym}.parquet")
                if not os.path.exists(pq):
                    continue
                df = pd.read_parquet(pq)
                if len(df) < m_ema + 50:
                    continue
                c = df["close"].to_numpy(dtype=float)
                o = df["open"].to_numpy(dtype=float)
                h = df["high"].to_numpy(dtype=float)
                l = df["low"].to_numpy(dtype=float)
                t = df["timestamp"].to_numpy(dtype=int)
                n = len(c)

                e_fast = calculate_ema(c, f_ema)
                e_macro = calculate_ema(c, m_ema)
                atr = calculate_atr(h, l, c, 14)

                rng = np.where(h - l <= 0, 1e-6, h - l)
                body_tops = np.maximum(o, c)
                body_bots = np.minimum(o, c)
                upper_wicks = (h - body_tops) / rng
                lower_wicks = (body_bots - l) / rng

                in_t = False
                side = 0
                ep = sl = rd = 0.0
                last_ex = -12 - 1

                for i in range(m_ema + 10, n - 1):
                    if in_t:
                        bh = h[i]; bl = l[i]
                        ex = False; ex_r = 0.0
                        if side == 1:
                            if bl <= sl: ex_r = -1.0; ex = True
                            elif bh >= ep + 3.0 * rd: ex_r = 3.0; ex = True
                        else:
                            if bh >= sl: ex_r = -1.0; ex = True
                            elif bl <= ep - 3.0 * rd: ex_r = 3.0; ex = True
                        if ex:
                            tot_net_r += (ex_r - 0.05)
                            tot_trades += 1
                            if ex_r > 0: tot_wins += 1
                            in_t = False
                            last_ex = i
                        continue

                    if (i - last_ex) < 12:
                        continue

                    c_b = c[i]; o_b = o[i]; h_b = h[i]; l_b = l[i]; a_b = atr[i]
                    came_above = (o_b >= e_fast[i] * 0.998) or (c[i - 1] >= e_fast[i] * 0.998)
                    came_below = (o_b <= e_fast[i] * 1.002) or (c[i - 1] <= e_fast[i] * 1.002)

                    sig = 0
                    if e_fast[i] > e_macro[i] and came_above and lower_wicks[i] >= 0.25:
                        if l_b <= e_fast[i] * 1.002 and c_b >= e_fast[i]:
                            sig = 1
                    elif e_fast[i] < e_macro[i] and came_below and upper_wicks[i] >= 0.25:
                        if h_b >= e_fast[i] * 0.998 and c_b <= e_fast[i]:
                            sig = -1

                    if sig != 0:
                        nb = i + 1
                        if nb >= n or t[nb] < JAN_2024_TS:
                            continue
                        in_t = True
                        side = sig
                        ep = o[nb]
                        sl = (l_b - 0.30 * a_b) if sig == 1 else (h_b + 0.30 * a_b)
                        rd = abs(ep - sl)
                        if rd <= 0 or (rd / ep) < 0.0005:
                            in_t = False

            wr_p = round(tot_wins / tot_trades * 100.0, 1) if tot_trades > 0 else 0.0
            exp_p = round(tot_net_r / tot_trades, 3) if tot_trades > 0 else 0.0
            pair_results.append({
                "Fast EMA": f_ema,
                "Macro EMA": m_ema,
                "Pair": f"EMA {f_ema} / {m_ema}",
                "Trades": tot_trades,
                "WinRate%": wr_p,
                "Net R": round(tot_net_r, 2),
                "Exp (R/tr)": exp_p
            })

    df_pairs = pd.DataFrame(pair_results).sort_values("Net R", ascending=False).reset_index(drop=True)
    print(df_pairs.to_string(index=False))


if __name__ == "__main__":
    main()
