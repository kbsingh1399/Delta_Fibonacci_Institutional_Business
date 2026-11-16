#!/usr/bin/env python3
r"""scratch/test_refined_winning_synergy.py: Test pure combinations of winning optimizations
1. Baseline
2. Opt A: Model 2 Wick 33% alone
3. Opt B: Drag Elimination alone (Drop FRA40, EUSTX50, NDX100, MC, EURNOK)
4. Opt A + B: Pure Synergy (Wick 33% + Drag Elimination)
5. Opt A + B + Concurrency 4
6. Opt A + B + Concurrency 3 (Cluster cap = 1)
"""

import os
import sys
from datetime import datetime, timezone
import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "FundedNext_Data")
sys.path.insert(0, BASE_DIR)

from engine.mt5_alpha_trader_monitor import OPTIMAL_ALPHA_BASKET, calculate_ema, calculate_atr, compute_adx

JAN_2026_TS = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp())
BASE_RECLAIM_UNIVERSE = ["US30", "DOGUSD", "ETHUSD", "USDCNH", "FRA40", "BABA", "ADAUSD", "NZDJPY", "NDX100", "NZDSGD"]


def run_single_asset_backtest(item, wick_threshold_m2=0.33, reclaim_universe=BASE_RECLAIM_UNIVERSE, cooldown_bars=12, friction_r=0.05):
    symbol = item["symbol"]
    cluster = item["cluster"]
    setup = item["setup"]
    exit_mode = item["exit_mode"]

    parquet_path = os.path.join(DATA_DIR, f"{symbol}.parquet")
    if not os.path.exists(parquet_path):
        return []

    df = pd.read_parquet(parquet_path)
    if len(df) < 300:
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

    rng = np.where(h - l <= 0, 1e-6, h - l)
    body_tops = np.maximum(o, c)
    body_bots = np.minimum(o, c)
    upper_wicks = (h - body_tops) / rng
    lower_wicks = (body_bots - l) / rng

    # Precompute rolling swing arrays
    roll_l20 = pd.Series(l).shift(1).rolling(20).min().to_numpy()
    roll_h20 = pd.Series(h).shift(1).rolling(20).max().to_numpy()

    bars_above_macro = np.zeros(n, dtype=int)
    bars_below_macro = np.zeros(n, dtype=int)
    for i in range(1, n):
        if c[i] > ema233[i]:
            bars_above_macro[i] = bars_above_macro[i - 1] + 1
            bars_below_macro[i] = 0
        elif c[i] < ema233[i]:
            bars_below_macro[i] = bars_below_macro[i - 1] + 1
            bars_above_macro[i] = 0

    trades = []
    in_trade = False
    side = 0
    ep = sl = rd = 0.0
    entry_ts = 0
    entry_bar = 0
    max_fav_r = 0.0
    last_exit_bar = -cooldown_bars - 1
    trade_model = ""
    active_exit_mode = exit_mode

    for i in range(250, n - 1):
        if in_trade:
            bh = h[i]; bl = l[i]
            cur_r = (ep - bl) / rd if side == -1 else (bh - ep) / rd
            if cur_r > max_fav_r: max_fav_r = cur_r
            exited = False; exit_r = 0.0

            if active_exit_mode.startswith("fixed_"):
                fixed_rr = float(active_exit_mode.split("_")[1])
                if side == 1:
                    if bl <= sl: exit_r = -1.0; exited = True
                    elif bh >= ep + fixed_rr * rd: exit_r = fixed_rr; exited = True
                else:
                    if bh >= sl: exit_r = -1.0; exited = True
                    elif bl <= ep - fixed_rr * rd: exit_r = fixed_rr; exited = True
            elif active_exit_mode == "step_trail":
                trail_sl = sl
                if max_fav_r >= 6.0: trail_sl = max(trail_sl, ema55[i]) if side == 1 else min(trail_sl, ema55[i])
                elif max_fav_r >= 4.0: trail_sl = max(trail_sl, ep + 2.0 * rd) if side == 1 else min(trail_sl, ep - 2.0 * rd)
                elif max_fav_r >= 2.0: trail_sl = max(trail_sl, ep + 0.5 * rd) if side == 1 else min(trail_sl, ep - 0.5 * rd)
                if side == 1 and bl <= trail_sl: exit_r = (trail_sl - ep) / rd; exited = True
                elif side == -1 and bh >= trail_sl: exit_r = (ep - trail_sl) / rd; exited = True

            if exited:
                net_r = exit_r - friction_r
                trades.append({
                    "symbol": symbol,
                    "cluster": cluster,
                    "model": trade_model,
                    "side": "LONG" if side == 1 else "SHORT",
                    "entry_ts": entry_ts,
                    "exit_ts": t[i],
                    "raw_r": exit_r,
                    "net_r": net_r,
                    "bars_held": i - entry_bar,
                })
                in_trade = False
                last_exit_bar = i
            continue

        if (i - last_exit_bar) < cooldown_bars:
            continue

        c_bar = c[i]; o_bar = o[i]; h_bar = h[i]; l_bar = l[i]; a_bar = atr[i]
        came_from_above = (o_bar >= ema89[i] * 0.998) or (c[i - 1] >= ema89[i - 1] * 0.998)
        came_from_below = (o_bar <= ema89[i] * 1.002) or (c[i - 1] <= ema89[i - 1] * 1.002)

        sig = 0
        current_model = "MODEL_1"
        shelf_type = "MID"

        if setup == "canonical":
            if ema89[i] > ema233[i] and came_from_above:
                if lower_wicks[i] >= 0.25 and (l_bar <= ema89[i] * 1.002 or l_bar <= ema55[i] * 1.002) and (c_bar >= ema89[i]):
                    sig = 1
            elif ema89[i] < ema233[i] and came_from_below:
                if upper_wicks[i] >= 0.25 and (h_bar >= ema89[i] * 0.998 or h_bar >= ema55[i] * 0.998) and (c_bar <= ema89[i]):
                    sig = -1

        elif setup == "turbo_tri_tier":
            if ema89[i] > ema233[i] and c_bar >= ema233[i] and came_from_above:
                if lower_wicks[i] >= 0.25:
                    if adx[i] >= 22 and (l_bar <= ema34[i] * 1.002 or l_bar <= ema55[i] * 1.002) and (c_bar >= ema55[i]):
                        sig = 1; shelf_type = "TURBO"
                    elif (l_bar <= ema89[i] * 1.002) and (c_bar >= ema89[i]):
                        sig = 1
            elif ema89[i] < ema233[i] and c_bar <= ema233[i] and came_from_below:
                if upper_wicks[i] >= 0.25:
                    if adx[i] >= 22 and (h_bar >= ema34[i] * 0.998 or h_bar >= ema55[i] * 0.998) and (c_bar <= ema55[i]):
                        sig = -1; shelf_type = "TURBO"
                    elif (h_bar >= ema89[i] * 0.998) and (c_bar <= ema89[i]):
                        sig = -1

        elif setup == "wyckoff_swing_sweep":
            if ema89[i] > ema233[i] and c_bar >= ema233[i]:
                if l_bar < roll_l20[i] and c_bar > roll_l20[i] and lower_wicks[i] >= 0.25:
                    sig = 1; shelf_type = "SWEEP"
            elif ema89[i] < ema233[i] and c_bar <= ema233[i]:
                if h_bar > roll_h20[i] and c_bar < roll_h20[i] and upper_wicks[i] >= 0.25:
                    sig = -1; shelf_type = "SWEEP"

        elif setup == "wyckoff_institutional_filtered":
            sep = abs(ema89[i] - ema233[i]) / a_bar
            if 0.25 <= sep <= 2.50 and adx[i] >= 20.0:
                if ema89[i] > ema233[i] and lower_wicks[i] >= 0.25 and came_from_above:
                    if (l_bar <= ema89[i] * 1.002 or l_bar <= ema55[i] * 1.002) and (c_bar >= ema89[i]):
                        sig = 1
                elif ema89[i] < ema233[i] and upper_wicks[i] >= 0.25 and came_from_below:
                    if (h_bar >= ema89[i] * 0.998 or h_bar >= ema55[i] * 0.998) and (c_bar <= ema89[i]):
                        sig = -1

        # --- MODEL 2 CASCADE ---
        if sig == 0 and symbol in reclaim_universe:
            if ema89[i] > ema233[i] and came_from_above and lower_wicks[i] >= wick_threshold_m2:
                if (l_bar <= ema89[i] * 1.002) and (c_bar >= ema89[i]):
                    recent_below = np.max(bars_below_macro[max(0, i - 35) : i])
                    if 3 <= recent_below <= 25:
                        sig = 1; current_model = "MODEL_2_RECLAIM"

            elif ema89[i] < ema233[i] and came_from_below and upper_wicks[i] >= wick_threshold_m2:
                if (h_bar >= ema89[i] * 0.998) and (c_bar <= ema89[i]):
                    recent_above = np.max(bars_above_macro[max(0, i - 35) : i])
                    if 3 <= recent_above <= 25:
                        sig = -1; current_model = "MODEL_2_RECLAIM"

        if sig != 0:
            next_b = i + 1
            if next_b >= n or t[next_b] < JAN_2026_TS:
                continue

            entry_p = o[next_b]
            if shelf_type in ["TURBO", "SWEEP"]:
                stop_p = (l_bar - 0.25 * a_bar) if sig == 1 else (h_bar + 0.25 * a_bar)
            else:
                stop_p = (l_bar - 0.30 * a_bar) if sig == 1 else (h_bar + 0.30 * a_bar)

            risk_d = abs(entry_p - stop_p)
            if risk_d <= 0 or (risk_d / entry_p) < 0.0005:
                continue

            in_trade = True
            side = sig
            ep = entry_p
            sl = stop_p
            rd = risk_d
            entry_ts = t[next_b]
            entry_bar = next_b
            trade_model = current_model
            active_exit_mode = "fixed_3.5" if current_model == "MODEL_2_RECLAIM" else exit_mode
            max_fav_r = 0.0

    return trades


def simulate_portfolio(all_trades, max_concurrent=3, max_per_cluster=1):
    if not all_trades:
        return None
    df_all = pd.DataFrame(all_trades).sort_values("entry_ts").reset_index(drop=True)
    active = []
    executed = []

    for _, row in df_all.iterrows():
        active = [p for p in active if p["exit_ts"] > row["entry_ts"]]
        if len(active) >= max_concurrent:
            continue
        clusters = [p["cluster"] for p in active]
        if clusters.count(row["cluster"]) >= max_per_cluster:
            continue

        active.append({"symbol": row["symbol"], "cluster": row["cluster"], "exit_ts": row["exit_ts"]})
        executed.append(row)

    if not executed:
        return None

    df = pd.DataFrame(executed).sort_values("exit_ts").reset_index(drop=True)
    df["cum_r"] = df["net_r"].cumsum()
    peak = df["cum_r"].cummax()
    dd = peak - df["cum_r"]
    max_dd = round(dd.max(), 2)
    net_r = round(df["net_r"].sum(), 2)
    wr = round((df["net_r"] > 0).mean() * 100, 1)
    calmar = round(net_r / max_dd, 2) if max_dd > 0 else 0.0
    
    gains = df[df["net_r"] > 0]["net_r"].sum()
    losses = abs(df[df["net_r"] < 0]["net_r"].sum())
    pf = round(gains / losses, 2) if losses > 0 else 99.0

    return {
        "trades": len(df),
        "net_r": net_r,
        "max_dd": max_dd,
        "wr": wr,
        "calmar": calmar,
        "pf": pf,
        "df": df
    }


def main():
    cleaned_basket = [x for x in OPTIMAL_ALPHA_BASKET if x["symbol"] not in ["FRA40", "EUSTX50", "NDX100", "MC", "EURNOK"]]
    cleaned_reclaim = [x for x in BASE_RECLAIM_UNIVERSE if x not in ["FRA40", "NDX100"]]

    experiments = [
        ("0. Baseline (25 Assets, Wick 25%, Concur 3)", OPTIMAL_ALPHA_BASKET, BASE_RECLAIM_UNIVERSE, 0.25, 3),
        ("1. Wick 33% Alone (25 Assets, Concur 3)", OPTIMAL_ALPHA_BASKET, BASE_RECLAIM_UNIVERSE, 0.33, 3),
        ("2. Cleaned Basket (20 Assets, Wick 25%, Concur 3)", cleaned_basket, cleaned_reclaim, 0.25, 3),
        ("3. PURE CHAMPION: Cleaned Basket + Wick 33% (Concur 3)", cleaned_basket, cleaned_reclaim, 0.33, 3),
        ("4. PURE CHAMPION: Cleaned Basket + Wick 33% (Concur 4)", cleaned_basket, cleaned_reclaim, 0.33, 4),
        ("5. PURE CHAMPION: Cleaned Basket + Wick 35% (Concur 3)", cleaned_basket, cleaned_reclaim, 0.35, 3),
    ]

    print("=" * 105)
    print("FOCUSED QUANT SYNERGY TEST (JAN 1, 2026 - NOV 16, 2026)")
    print("=" * 105)
    print(f"{'Experiment Name':<55} | {'Trades':<6} | {'WinRate':<7} | {'Net R':<8} | {'MaxDD':<7} | {'Calmar':<6} | {'PF':<5}")
    print("-" * 105)

    for name, basket, reclaim, wick, concur in experiments:
        t_all = []
        for item in basket:
            t_list = run_single_asset_backtest(item, wick_threshold_m2=wick, reclaim_universe=reclaim)
            t_all.extend(t_list)

        res = simulate_portfolio(t_all, max_concurrent=concur, max_per_cluster=1)
        if res:
            print(f"{name:<55} | {res['trades']:<6} | {res['wr']:<6.1f}% | {res['net_r']:<+8.2f} | {res['max_dd']:<7.2f} | {res['calmar']:<6.2f} | {res['pf']:<5.2f}")

    print("=" * 105)


if __name__ == "__main__":
    main()
