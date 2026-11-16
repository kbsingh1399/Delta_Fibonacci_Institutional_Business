#!/usr/bin/env python3
r"""scratch/comprehensive_asset_matrix_with_rr_trailing_v2.py
Comprehensive Quantitative Optimization Matrix:
For every asset in the 20-Champion Basket:
1. 1H vs 4H Timeframe
2. Model 1 (Pullback Rejection) vs Model 2 (Macro Dwell & Reclaim)
3. 16 EMA Periods (50 to 400): [50, 55, 65, 75, 89, 100, 120, 144, 160, 200, 233, 277, 300, 350, 377, 400]
4. 8 RR & Trailing SL Configurations:
   - fixed_2.5r
   - fixed_3.0r
   - fixed_3.5r (current production base)
   - fixed_4.0r
   - lock_0.5r (lock +0.5R at +1.5R profit, target 3.5R)
   - trail_0.5r (at +1.5R, trail 0.5R behind peak, target 6.0R)
   - trail_1.0r (at +2.0R, trail 1.0R behind peak, target 6.0R)
   - trail_1.5r (at +2.5R, trail 1.5R behind peak, target 8.0R)
"""

import os
import sys
import json
import numpy as np
import pandas as pd
from datetime import datetime, timezone
import MetaTrader5 as mt5

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "FundedNext_Data")
sys.path.insert(0, BASE_DIR)

from engine.mt5_alpha_trader_monitor import OPTIMAL_ALPHA_BASKET, calculate_ema, calculate_atr

EMA_PERIODS = [50, 55, 65, 75, 89, 100, 120, 144, 160, 200, 233, 277, 300, 350, 377, 400]
START_TS = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp())

EXIT_MODES = [
    "fixed_2.5r",
    "fixed_3.0r",
    "fixed_3.5r",
    "fixed_4.0r",
    "lock_0.5r",
    "trail_0.5r",
    "trail_1.0r",
    "trail_1.5r"
]


def load_data(symbol, tf_str):
    if tf_str == "4H":
        pq = os.path.join(DATA_DIR, f"{symbol}.parquet")
        if os.path.exists(pq):
            df = pd.read_parquet(pq)
            if "time" in df.columns and "timestamp" not in df.columns:
                df.rename(columns={"time": "timestamp"}, inplace=True)
            return df
    if not mt5.initialize():
        return None
    tf = mt5.TIMEFRAME_H1 if tf_str == "1H" else mt5.TIMEFRAME_H4
    rates = mt5.copy_rates_from_pos(symbol, tf, 0, 12000)
    if rates is None or len(rates) < 300:
        return None
    df = pd.DataFrame(rates)
    if "time" in df.columns and "timestamp" not in df.columns:
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

    # Vectorized rolling max for macro dwell to maximize speed
    recent_under_arr = pd.Series(bars_below).shift(1).rolling(dwell_lookback, min_periods=1).max().fillna(0).to_numpy()
    recent_over_arr = pd.Series(bars_above).shift(1).rolling(dwell_lookback, min_periods=1).max().fillna(0).to_numpy()

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
                recent_under = recent_under_arr[i]
                if min_dwell <= recent_under <= max_dwell:
                    sig = 1
            elif came_from_below and upper_wicks[i] >= wick_thresh and h_bar >= ema[i] * 0.998 and c_bar <= ema[i]:
                recent_over = recent_over_arr[i]
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

    target_r = 3.5
    if exit_mode == "fixed_2.5r": target_r = 2.5
    elif exit_mode == "fixed_3.0r": target_r = 3.0
    elif exit_mode == "fixed_3.5r": target_r = 3.5
    elif exit_mode == "fixed_4.0r": target_r = 4.0
    elif exit_mode == "lock_0.5r": target_r = 3.5
    elif exit_mode == "trail_0.5r": target_r = 6.0
    elif exit_mode == "trail_1.0r": target_r = 6.0
    elif exit_mode == "trail_1.5r": target_r = 8.0

    for sig in signals:
        b = sig["bar"]
        if (b - last_exit) < cooldown:
            continue

        side = sig["side"]
        ep = sig["ep"]
        sl = sig["sl"]
        rd = sig["rd"]

        active_sl = sl
        watermark = ep
        exit_r = 0.0

        tp_price = (ep + target_r * rd) if side == 1 else (ep - target_r * rd)

        for i in range(b, n):
            bh = h[i]; bl = l[i]

            # 1. Stop loss check (resting stop from prior bar)
            if (side == 1 and bl <= active_sl) or (side == -1 and bh >= active_sl):
                exit_r = ((active_sl - ep) / rd) if side == 1 else ((ep - active_sl) / rd)
                last_exit = i
                break

            # 2. Target check
            if (side == 1 and bh >= tp_price) or (side == -1 and bl <= tp_price):
                exit_r = target_r
                last_exit = i
                break

            # 3. Ratchet trailing stop for subsequent bars
            if side == 1:
                watermark = max(watermark, bh)
                peak_r = (watermark - ep) / rd
                if exit_mode == "lock_0.5r" and peak_r >= 1.5:
                    active_sl = max(active_sl, ep + 0.5 * rd)
                elif exit_mode == "trail_0.5r" and peak_r >= 1.5:
                    active_sl = max(active_sl, watermark - 0.5 * rd)
                elif exit_mode == "trail_1.0r" and peak_r >= 2.0:
                    active_sl = max(active_sl, watermark - 1.0 * rd)
                elif exit_mode == "trail_1.5r" and peak_r >= 2.5:
                    active_sl = max(active_sl, watermark - 1.5 * rd)
            else:
                watermark = min(watermark, bl)
                peak_r = (ep - watermark) / rd
                if exit_mode == "lock_0.5r" and peak_r >= 1.5:
                    active_sl = min(active_sl, ep - 0.5 * rd)
                elif exit_mode == "trail_0.5r" and peak_r >= 1.5:
                    active_sl = min(active_sl, watermark + 0.5 * rd)
                elif exit_mode == "trail_1.0r" and peak_r >= 2.0:
                    active_sl = min(active_sl, watermark + 1.0 * rd)
                elif exit_mode == "trail_1.5r" and peak_r >= 2.5:
                    active_sl = min(active_sl, watermark + 1.5 * rd)

        trades.append(exit_r - friction_r)

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


def optimize_single_asset(sym, cluster):
    df_1h = load_data(sym, "1H")
    df_4h = load_data(sym, "4H")

    if df_1h is None or df_4h is None:
        return None

    all_combos = []

    for tf_str, df in [("1H", df_1h), ("4H", df_4h)]:
        for model in ["MODEL_1", "MODEL_2"]:
            for p in EMA_PERIODS:
                sig_data = simulate_asset_signals(df, p, model, tf_str)
                if not sig_data:
                    continue
                sigs, h_arr, l_arr, c_arr, n_bars = sig_data
                min_tr = 8 if tf_str == "1H" else 4
                if len(sigs) < min_tr:
                    continue

                for emode in EXIT_MODES:
                    res = simulate_trades_with_exit_mode(sigs, h_arr, l_arr, c_arr, n_bars, emode, tf_str)
                    if res["trades"] >= min_tr:
                        all_combos.append({
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

    if not all_combos:
        return None

    df_res = pd.DataFrame(all_combos)

    # 1. 1H Model 1 best
    s11 = df_res[(df_res["tf"] == "1H") & (df_res["model"] == "MODEL_1")]
    b_1h_m1 = s11.sort_values("net_r", ascending=False).iloc[0].to_dict() if len(s11) > 0 else None

    # 2. 1H Model 2 best
    s12 = df_res[(df_res["tf"] == "1H") & (df_res["model"] == "MODEL_2")]
    b_1h_m2 = s12.sort_values("net_r", ascending=False).iloc[0].to_dict() if len(s12) > 0 else None

    # 3. 4H Model 1 best
    s41 = df_res[(df_res["tf"] == "4H") & (df_res["model"] == "MODEL_1")]
    b_4h_m1 = s41.sort_values("net_r", ascending=False).iloc[0].to_dict() if len(s41) > 0 else None

    # 4. 4H Model 2 best
    s42 = df_res[(df_res["tf"] == "4H") & (df_res["model"] == "MODEL_2")]
    b_4h_m2 = s42.sort_values("net_r", ascending=False).iloc[0].to_dict() if len(s42) > 0 else None

    # Overall Champion for asset
    b_overall = df_res.sort_values("net_r", ascending=False).iloc[0].to_dict()

    return {
        "symbol": sym,
        "cluster": cluster,
        "1h_m1": b_1h_m1,
        "1h_m2": b_1h_m2,
        "4h_m1": b_4h_m1,
        "4h_m2": b_4h_m2,
        "champion": b_overall,
        "all_data": all_combos
    }


def main():
    print("=" * 115)
    print("QUANTITATIVE ASSET-BY-ASSET DEEP DIVE: EMA 50-400 x 1H & 4H x MODEL 1 & 2 x RR & TRAILING SL")
    print("Simulating 20 Assets across 16 EMAs x 4 Model-Timeframe Combinations x 8 Exit Strategies")
    print("=" * 115)

    all_asset_results = []
    champions = []

    for item in OPTIMAL_ALPHA_BASKET:
        sym = item["symbol"]
        cluster = item["cluster"]

        res = optimize_single_asset(sym, cluster)
        if not res:
            continue

        all_asset_results.append(res)
        champ = res["champion"]
        champions.append(champ)

        print(f"\n" + "=" * 90)
        print(f"ASSET: {sym:<8} | Cluster: {cluster}")
        print("=" * 90)

        # 1H Model 1
        m = res["1h_m1"]
        if m:
            print(f"  [1H Model 1] : Best EMA {m['ema']:<3} | Exit: {m['exit_mode']:<14} | Tr: {m['trades']:<3} | WR: {m['wr']:<4.1f}% | Net R: {m['net_r']:<+6.2f} | PF: {m['pf']}")
        else:
            print(f"  [1H Model 1] : No qualified trades")

        # 1H Model 2
        m = res["1h_m2"]
        if m:
            print(f"  [1H Model 2] : Best EMA {m['ema']:<3} | Exit: {m['exit_mode']:<14} | Tr: {m['trades']:<3} | WR: {m['wr']:<4.1f}% | Net R: {m['net_r']:<+6.2f} | PF: {m['pf']}")
        else:
            print(f"  [1H Model 2] : No qualified trades")

        # 4H Model 1
        m = res["4h_m1"]
        if m:
            print(f"  [4H Model 1] : Best EMA {m['ema']:<3} | Exit: {m['exit_mode']:<14} | Tr: {m['trades']:<3} | WR: {m['wr']:<4.1f}% | Net R: {m['net_r']:<+6.2f} | PF: {m['pf']}")
        else:
            print(f"  [4H Model 1] : No qualified trades")

        # 4H Model 2
        m = res["4h_m2"]
        if m:
            print(f"  [4H Model 2] : Best EMA {m['ema']:<3} | Exit: {m['exit_mode']:<14} | Tr: {m['trades']:<3} | WR: {m['wr']:<4.1f}% | Net R: {m['net_r']:<+6.2f} | PF: {m['pf']}")
        else:
            print(f"  [4H Model 2] : No qualified trades")

        print(f"  >> 🏆 ASSET CHAMPION: {champ['tf']} {champ['model']} | EMA {champ['ema']} | {champ['exit_mode']}")
        print(f"     Trades: {champ['trades']} | WR: {champ['wr']}% | Net: {champ['net_r']:+7.2f} R | PF: {champ['pf']}")

    # Master Table
    print("\n" + "=" * 125)
    print("MASTER SUMMARY TABLE: GLOBAL OPTIMAL CONFIGURATION PER ASSET")
    print("=" * 125)
    df_champs = pd.DataFrame(champions)
    cols = ["symbol", "cluster", "tf", "model", "ema", "exit_mode", "trades", "wr", "net_r", "pf"]
    df_out = df_champs[cols].rename(columns={
        "symbol": "Symbol", "cluster": "Cluster", "tf": "TF", "model": "Model",
        "ema": "EMA", "exit_mode": "Exit Strategy", "trades": "Trades", "wr": "WR%",
        "net_r": "Net R", "pf": "PF"
    })
    print(df_out.to_string(index=False))
    print("=" * 125)

    # Save complete JSON
    out_json = os.path.join(BASE_DIR, "scratch", "comprehensive_asset_matrix_results.json")
    # Strip df to serializable
    save_payload = []
    for r in all_asset_results:
        save_payload.append({
            "symbol": r["symbol"],
            "cluster": r["cluster"],
            "1h_m1": r["1h_m1"],
            "1h_m2": r["1h_m2"],
            "4h_m1": r["4h_m1"],
            "4h_m2": r["4h_m2"],
            "champion": r["champion"]
        })
    with open(out_json, "w") as f:
        json.dump(save_payload, f, indent=2)
    print(f"\n[DONE] Full results written to {out_json}")


if __name__ == "__main__":
    main()
