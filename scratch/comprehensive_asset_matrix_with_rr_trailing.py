#!/usr/bin/env python3
r"""scratch/comprehensive_asset_matrix_with_rr_trailing.py
Ultimate Asset-by-Asset Optimization:
For every asset in the basket:
1. 1H vs 4H Timeframe
2. Model 1 (Pullback Rejection) vs Model 2 (Macro Dwell & Reclaim)
3. Sweep EMA 50 to 400 (16 periods)
4. Optimize RR & Trailing SL:
   - Fixed 3.0R
   - Fixed 3.5R
   - Lock +0.5R at +1.5R (with target 3.5R)
   - Trail 1.0R behind peak (from +2.0R)
   - Tight 0.5R trail (from +1.5R)
   - Loose 1.5R trail (runner mode up to 6.0R)
5. Reports the champion EMA, Model, Timeframe, and RR/Trailing rule per asset!
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

EXIT_MODES = [
    "fixed_3.0",
    "fixed_3.5",
    "lock_0.5r_at_1.5r",
    "trail_1.0r",
    "tight_0.5r",
    "runner_trail_1.5r"
]


def load_data(symbol, tf_str):
    if tf_str == "4H":
        pq = os.path.join(DATA_DIR, f"{symbol}.parquet")
        if os.path.exists(pq):
            return pd.read_parquet(pq)
    if not mt5.initialize():
        return None
    tf = mt5.TIMEFRAME_H1 if tf_str == "1H" else mt5.TIMEFRAME_H4
    rates = mt5.copy_rates_from_pos(symbol, tf, 0, 12000)
    if rates is None or len(rates) < 300:
        return None
    df = pd.DataFrame(rates)
    df.rename(columns={"time": "timestamp"}, inplace=True)
    return df


def simulate_asset_signals(df, ema_period, model_type, tf_str):
    c = df["close"].to_numpy(dtype=float)
    o = df["open"].to_numpy(dtype=float)
    h = df["high"].to_numpy(dtype=float)
    l = df["low"].to_numpy(dtype=float)
    t = df["timestamp"].to_numpy(dtype=int)
    n = len(c)

    if n < ema_period + 50:
        return []

    ema = calculate_ema(c, ema_period)
    atr = calculate_atr(h, l, c, 14)

    rng = np.where(h - l <= 0, 1e-6, h - l)
    body_tops = np.maximum(o, c)
    body_bots = np.minimum(o, c)
    upper_wicks = (h - body_tops) / rng
    lower_wicks = (body_bots - l) / rng

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

    wick_thresh = 0.33 if model_type == "MODEL_2" else 0.25
    signals = []

    for i in range(ema_period + 10, n - 1):
        if t[i + 1] < START_TS:
            continue

        c_bar = c[i]; o_bar = o[i]; h_bar = h[i]; l_bar = l[i]; a_bar = atr[i]
        came_from_above = (o_bar >= ema[i] * 0.998) or (c[i - 1] >= ema[i] * 0.998)
        came_from_below = (o_bar <= ema[i] * 1.002) or (c[i - 1] <= ema[i] * 1.002)

        sig = 0
        if model_type == "MODEL_1":
            if ema[i] > ema[i - 3] and came_from_above and lower_wicks[i] >= wick_thresh:
                if l_bar <= ema[i] * 1.002 and c_bar >= ema[i]:
                    sig = 1
            elif ema[i] < ema[i - 3] and came_from_below and upper_wicks[i] >= wick_thresh:
                if h_bar >= ema[i] * 0.998 and c_bar <= ema[i]:
                    sig = -1

        elif model_type == "MODEL_2":
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
            if next_b >= n:
                continue
            entry_p = o[next_b]
            stop_p = (l_bar - 0.30 * a_bar) if sig == 1 else (h_bar + 0.30 * a_bar)
            risk_d = abs(entry_p - stop_p)
            if risk_d <= 0 or (risk_d / entry_p) < 0.0003:
                continue

            signals.append({
                "bar": next_b,
                "side": sig,
                "ep": entry_p,
                "sl": stop_p,
                "rd": risk_d
            })

    return signals, h, l, c, n


def simulate_trades_with_exit_mode(signals, h, l, c, n, exit_mode, tf_str, friction_r=0.05):
    cooldown = 12 if tf_str == "4H" else 36
    last_exit = -cooldown - 1
    trades = []

    for sig in signals:
        b = sig["bar"]
        if (b - last_exit) < cooldown:
            continue

        side = sig["side"]
        ep = sig["ep"]
        sl = sig["sl"]
        rd = sig["rd"]

        in_trade = True
        max_fav = 0.0
        exit_r = 0.0

        for i in range(b, n):
            bh = h[i]; bl = l[i]
            cur_fav = (bh - ep) / rd if side == 1 else (ep - bl) / rd
            if cur_fav > max_fav: max_fav = cur_fav

            # Exit rule evaluations
            trail_sl = sl
            if exit_mode == "fixed_3.0":
                if side == 1:
                    if bl <= sl: exit_r = -1.0; break
                    elif bh >= ep + 3.0 * rd: exit_r = 3.0; break
                else:
                    if bh >= sl: exit_r = -1.0; break
                    elif bl <= ep - 3.0 * rd: exit_r = 3.0; break

            elif exit_mode == "fixed_3.5":
                if side == 1:
                    if bl <= sl: exit_r = -1.0; break
                    elif bh >= ep + 3.5 * rd: exit_r = 3.5; break
                else:
                    if bh >= sl: exit_r = -1.0; break
                    elif bl <= ep - 3.5 * rd: exit_r = 3.5; break

            elif exit_mode == "lock_0.5r_at_1.5r":
                if max_fav >= 1.5:
                    trail_sl = (ep + 0.5 * rd) if side == 1 else (ep - 0.5 * rd)
                if side == 1:
                    if bl <= trail_sl: exit_r = -1.0 if trail_sl == sl else 0.5; break
                    elif bh >= ep + 3.5 * rd: exit_r = 3.5; break
                else:
                    if bh >= trail_sl: exit_r = -1.0 if trail_sl == sl else 0.5; break
                    elif bl <= ep - 3.5 * rd: exit_r = 3.5; break

            elif exit_mode == "trail_1.0r":
                # Once reaches 2.0R, trail 1.0R behind peak
                if max_fav >= 2.0:
                    locked_r = max_fav - 1.0
                    trail_sl = (ep + locked_r * rd) if side == 1 else (ep - locked_r * rd)
                if side == 1:
                    if bl <= trail_sl: exit_r = -1.0 if trail_sl == sl else (max_fav - 1.0); break
                    elif bh >= ep + 4.5 * rd: exit_r = 4.5; break
                else:
                    if bh >= trail_sl: exit_r = -1.0 if trail_sl == sl else (max_fav - 1.0); break
                    elif bl <= ep - 4.5 * rd: exit_r = 4.5; break

            elif exit_mode == "tight_0.5r":
                # Once reaches 1.5R, trail tightly 0.5R behind peak
                if max_fav >= 1.5:
                    locked_r = max_fav - 0.5
                    trail_sl = (ep + locked_r * rd) if side == 1 else (ep - locked_r * rd)
                if side == 1:
                    if bl <= trail_sl: exit_r = -1.0 if trail_sl == sl else (max_fav - 0.5); break
                    elif bh >= ep + 4.0 * rd: exit_r = 4.0; break
                else:
                    if bh >= trail_sl: exit_r = -1.0 if trail_sl == sl else (max_fav - 0.5); break
                    elif bl <= ep - 4.0 * rd: exit_r = 4.0; break

            elif exit_mode == "runner_trail_1.5r":
                # Once reaches 2.5R, trail 1.5R behind peak up to 6.0R
                if max_fav >= 2.5:
                    locked_r = max_fav - 1.5
                    trail_sl = (ep + locked_r * rd) if side == 1 else (ep - locked_r * rd)
                if side == 1:
                    if bl <= trail_sl: exit_r = -1.0 if trail_sl == sl else (max_fav - 1.5); break
                    elif bh >= ep + 6.0 * rd: exit_r = 6.0; break
                else:
                    if bh >= trail_sl: exit_r = -1.0 if trail_sl == sl else (max_fav - 1.5); break
                    elif bl <= ep - 6.0 * rd: exit_r = 6.0; break

        trades.append(exit_r - friction_r)
        last_exit = i

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


def optimize_asset_full(sym, cluster):
    df_1h = load_data(sym, "1H")
    df_4h = load_data(sym, "4H")

    if df_1h is None or df_4h is None:
        return None

    results = []

    for tf_str, df in [("1H", df_1h), ("4H", df_4h)]:
        for model in ["MODEL_1", "MODEL_2"]:
            for p in EMA_PERIODS:
                sig_data = simulate_asset_signals(df, p, model, tf_str)
                if not sig_data:
                    continue
                sigs, h_arr, l_arr, c_arr, n_bars = sig_data
                if len(sigs) < (8 if tf_str == "1H" else 4):
                    continue

                for emode in EXIT_MODES:
                    res = simulate_trades_with_exit_mode(sigs, h_arr, l_arr, c_arr, n_bars, emode, tf_str)
                    if res["trades"] >= (8 if tf_str == "1H" else 4):
                        results.append({
                            "symbol": sym,
                            "cluster": cluster,
                            "tf": tf_str,
                            "model": model,
                            "ema": p,
                            "exit_mode": emode,
                            "trades": res["trades"],
                            "wr": res["wr"],
                            "net_r": res["net_r"],
                            "exp_r": res["exp_r"],
                            "pf": res["pf"]
                        })

    if not results:
        return None

    df_res = pd.DataFrame(results)

    # 1. Best 1H Model 1
    sub_1h_m1 = df_res[(df_res["tf"] == "1H") & (df_res["model"] == "MODEL_1")]
    best_1h_m1 = sub_1h_m1.sort_values("net_r", ascending=False).iloc[0].to_dict() if len(sub_1h_m1) > 0 else None

    # 2. Best 1H Model 2
    sub_1h_m2 = df_res[(df_res["tf"] == "1H") & (df_res["model"] == "MODEL_2")]
    best_1h_m2 = sub_1h_m2.sort_values("net_r", ascending=False).iloc[0].to_dict() if len(sub_1h_m2) > 0 else None

    # 3. Best 4H Model 1
    sub_4h_m1 = df_res[(df_res["tf"] == "4H") & (df_res["model"] == "MODEL_1")]
    best_4h_m1 = sub_4h_m1.sort_values("net_r", ascending=False).iloc[0].to_dict() if len(sub_4h_m1) > 0 else None

    # 4. Best 4H Model 2
    sub_4h_m2 = df_res[(df_res["tf"] == "4H") & (df_res["model"] == "MODEL_2")]
    best_4h_m2 = sub_4h_m2.sort_values("net_r", ascending=False).iloc[0].to_dict() if len(sub_4h_m2) > 0 else None

    # Overall Champion
    best_overall = df_res.sort_values("net_r", ascending=False).iloc[0].to_dict()

    return {
        "symbol": sym,
        "cluster": cluster,
        "1h_m1": best_1h_m1,
        "1h_m2": best_1h_m2,
        "4h_m1": best_4h_m1,
        "4h_m2": best_4h_m2,
        "champion": best_overall
    }


def main():
    print("=" * 115)
    print("FULL ASSET MATRIX WITH RR & TRAILING SL OPTIMIZATION (1H & 4H | MODEL 1 & 2)")
    print("Testing 16 EMAs (50-400) x 6 Exit / Trailing SL Modes for each Asset")
    print("=" * 115)

    all_champions = []

    for item in OPTIMAL_ALPHA_BASKET:
        sym = item["symbol"]
        cluster = item["cluster"]

        res = optimize_asset_full(sym, cluster)
        if not res:
            continue

        champ = res["champion"]
        all_champions.append(champ)

        print(f"\n" + "-" * 90)
        print(f"ASSET: {sym:<8} | Cluster: {cluster}")
        print("-" * 90)

        # 1H Model 1
        m = res["1h_m1"]
        if m:
            print(f"  [1H Model 1] : Best EMA {m['ema']:<3} | Exit: {m['exit_mode']:<17} | Tr: {m['trades']:<3} | WR: {m['wr']:<4.1f}% | Net R: {m['net_r']:<+6.2f} | PF: {m['pf']}")
        else:
            print(f"  [1H Model 1] : No qualified trades")

        # 1H Model 2
        m = res["1h_m2"]
        if m:
            print(f"  [1H Model 2] : Best EMA {m['ema']:<3} | Exit: {m['exit_mode']:<17} | Tr: {m['trades']:<3} | WR: {m['wr']:<4.1f}% | Net R: {m['net_r']:<+6.2f} | PF: {m['pf']}")
        else:
            print(f"  [1H Model 2] : No qualified trades")

        # 4H Model 1
        m = res["4h_m1"]
        if m:
            print(f"  [4H Model 1] : Best EMA {m['ema']:<3} | Exit: {m['exit_mode']:<17} | Tr: {m['trades']:<3} | WR: {m['wr']:<4.1f}% | Net R: {m['net_r']:<+6.2f} | PF: {m['pf']}")
        else:
            print(f"  [4H Model 1] : No qualified trades")

        # 4H Model 2
        m = res["4h_m2"]
        if m:
            print(f"  [4H Model 2] : Best EMA {m['ema']:<3} | Exit: {m['exit_mode']:<17} | Tr: {m['trades']:<3} | WR: {m['wr']:<4.1f}% | Net R: {m['net_r']:<+6.2f} | PF: {m['pf']}")
        else:
            print(f"  [4H Model 2] : No qualified trades")

        print(f"  >> 🏆 OVERALL CHAMPION: {champ['tf']} {champ['model']} on EMA {champ['ema']} with {champ['exit_mode']}")
        print(f"     Trades: {champ['trades']} | Win Rate: {champ['wr']}% | Net Return: {champ['net_r']:+7.2f} R | Profit Factor: {champ['pf']}")

    # Print Master Summary Table
    print("\n" + "=" * 125)
    print("MASTER SUMMARY TABLE: GLOBAL OPTIMAL CONFIGURATION PER ASSET")
    print("=" * 125)
    df_champs = pd.DataFrame(all_champions)
    cols = ["symbol", "cluster", "tf", "model", "ema", "exit_mode", "trades", "wr", "net_r", "pf"]
    df_out = df_champs[cols].rename(columns={
        "symbol": "Symbol", "cluster": "Cluster", "tf": "TF", "model": "Model",
        "ema": "EMA", "exit_mode": "Exit Strategy", "trades": "Trades", "wr": "WR%",
        "net_r": "Net R", "pf": "PF"
    })
    print(df_out.to_string(index=False))
    print("=" * 125)


if __name__ == "__main__":
    main()
