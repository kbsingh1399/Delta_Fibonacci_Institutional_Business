#!/usr/bin/env python3
r"""scratch/rank_assets_for_dwell_model.py: Asset-Level Quantitative Ranking
for the Optimal Macro Dwell + Reclaim Spring / Trend Model.
Evaluates all assets in FundedNext_Data to find which assets produce
the highest Net R, Win Rate, Profit Factor, and Calmar Ratio.
"""

import os
import glob
import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "FundedNext_Data")
import sys
sys.path.insert(0, BASE_DIR)

from engine.mt5_alpha_trader_monitor import calculate_ema, calculate_atr

JAN_2024_TS = 1704067200


def evaluate_asset(file_path):
    symbol = os.path.basename(file_path).replace(".parquet", "")
    df = pd.read_parquet(file_path)
    if len(df) < 300:
        return None

    c = df["close"].to_numpy(dtype=float)
    o = df["open"].to_numpy(dtype=float)
    h = df["high"].to_numpy(dtype=float)
    l = df["low"].to_numpy(dtype=float)
    t = df["timestamp"].to_numpy(dtype=int)
    n = len(c)

    ema89 = calculate_ema(c, 89)
    ema233 = calculate_ema(c, 233)
    atr = calculate_atr(h, l, c, 14)

    rng = np.where(h - l <= 0, 1e-6, h - l)
    body_tops = np.maximum(o, c)
    body_bots = np.minimum(o, c)
    upper_wicks = (h - body_tops) / rng
    lower_wicks = (body_bots - l) / rng

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
    ep = sl = tp = rd = 0.0
    last_exit_bar = -13

    for i in range(250, n - 1):
        if in_trade:
            bh = h[i]; bl = l[i]
            exited = False; exit_r = 0.0
            if side == 1:
                if bl <= sl: exit_r = -1.0; exited = True
                elif bh >= tp: exit_r = 3.5; exited = True
            else:
                if bh >= sl: exit_r = -1.0; exited = True
                elif bl <= tp: exit_r = 3.5; exited = True
            if exited:
                trades.append(exit_r - 0.05)
                in_trade = False
                last_exit_bar = i
            continue

        if (i - last_exit_bar) < 12:
            continue

        c_bar = c[i]; o_bar = o[i]; h_bar = h[i]; l_bar = l[i]; a_bar = atr[i]
        came_from_above = (o_bar >= ema89[i] * 0.998) or (c[i - 1] >= ema89[i - 1] * 0.998)
        came_from_below = (o_bar <= ema89[i] * 1.002) or (c[i - 1] <= ema89[i - 1] * 1.002)

        sig = 0
        # Reclaim Spring setup: price dwelled on opposite side for 3-12 bars (12h-48h)
        if ema89[i] > ema233[i] and came_from_above and lower_wicks[i] >= 0.25:
            if (l_bar <= ema89[i] * 1.002) and (c_bar >= ema89[i]):
                recent_below = np.max(bars_below_macro[max(0, i - 35) : i])
                if 3 <= recent_below <= 20:
                    sig = 1
        elif ema89[i] < ema233[i] and came_from_below and upper_wicks[i] >= 0.25:
            if (h_bar >= ema89[i] * 0.998) and (c_bar <= ema89[i]):
                recent_above = np.max(bars_above_macro[max(0, i - 35) : i])
                if 3 <= recent_above <= 20:
                    sig = -1

        if sig != 0:
            next_b = i + 1
            if next_b >= n or t[next_b] < JAN_2024_TS:
                continue
            ep = o[next_b]
            sl = (l_bar - 0.30 * a_bar) if sig == 1 else (h_bar + 0.30 * a_bar)
            rd = abs(ep - sl)
            if rd <= 0 or (rd / ep) < 0.0005:
                continue
            tp = (ep + 3.5 * rd) if sig == 1 else (ep - 3.5 * rd)
            in_trade = True
            side = sig

    if len(trades) < 5:
        return None

    r_arr = np.array(trades)
    cnt = len(r_arr)
    wins = np.sum(r_arr > 0)
    wr = (wins / cnt) * 100
    net_r = np.sum(r_arr)
    gw = np.sum(r_arr[r_arr > 0])
    gl = abs(np.sum(r_arr[r_arr <= 0]))
    pf = (gw / gl) if gl > 0 else 99.0
    eq = np.cumsum(r_arr)
    peak = np.maximum.accumulate(eq)
    dd = peak - eq
    max_dd = np.max(dd) if len(dd) > 0 else 0.0
    calmar = (net_r / max_dd) if max_dd > 0 else 0.0

    return {
        "symbol": symbol,
        "trades": cnt,
        "win_rate": round(wr, 1),
        "net_r": round(net_r, 2),
        "pf": round(pf, 2),
        "max_dd": round(max_dd, 2),
        "calmar": round(calmar, 2),
        "expectancy": round(np.mean(r_arr), 3),
    }


def main():
    files = glob.glob(os.path.join(DATA_DIR, "*.parquet"))
    results = []
    for f in files:
        res = evaluate_asset(f)
        if res:
            results.append(res)

    df = pd.DataFrame(results).sort_values("net_r", ascending=False)
    print("=" * 85)
    print("TOP 15 BEST PERFORMING ASSETS FOR MACRO DWELL & RECLAIM SPRING (3.5R TARGET)")
    print("=" * 85)
    print(df.head(15).to_string(index=False))

    print("\n" + "=" * 85)
    print("BOTTOM 10 WORST PERFORMING ASSETS (AVOID / DISQUALIFIED)")
    print("=" * 85)
    print(df.tail(10).to_string(index=False))

if __name__ == "__main__":
    main()
