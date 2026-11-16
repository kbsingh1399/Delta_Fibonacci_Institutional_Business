#!/usr/bin/env python3
r"""scratch/simulate_233_dwell_and_rr.py: Refined Quantitative Simulation with 12-bar Cooldown
Testing:
1. Scenario A: RECLAIM SPRING (Price was on the OPPOSITE side of EMA 233 for >= 24h, then reclaims & bounces on EMA 89)
2. Scenario B: ESTABLISHED TREND (Price was on the TREND side of EMA 233 for >= 24h, then pulls back & bounces on EMA 89)
3. 12-bar cooldown enforced to eliminate repeated bar churn.
4. Target RR Ladder (2R, 2.5R, 3R, 3.5R, 4R, 5R, 6R, 8R, 10R).
5. "No SL" vs "Optimized SL" (Wick SL, Breakeven Trail, Catastrophic DD risk).
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

JAN_2024_TS = 1704067200


def analyze_asset_dwell_refined(parquet_path, dwell_bars_min=6, cooldown_bars=12, max_holding_bars=60):
    df = pd.read_parquet(parquet_path)
    if len(df) < 300:
        return []

    c = df["close"].to_numpy(dtype=float)
    o = df["open"].to_numpy(dtype=float)
    h = df["high"].to_numpy(dtype=float)
    l = df["low"].to_numpy(dtype=float)
    t = df["timestamp"].to_numpy(dtype=int)
    n = len(c)

    ema55 = calculate_ema(c, 55)
    ema89 = calculate_ema(c, 89)
    ema233 = calculate_ema(c, 233)
    atr = calculate_atr(h, l, c, 14)

    rng = np.where(h - l <= 0, 1e-6, h - l)
    body_tops = np.maximum(o, c)
    body_bots = np.minimum(o, c)
    upper_wicks = (h - body_tops) / rng
    lower_wicks = (body_bots - l) / rng

    # Dwell trackers
    bars_above_233 = np.zeros(n, dtype=int)
    bars_below_233 = np.zeros(n, dtype=int)
    for i in range(1, n):
        if c[i] > ema233[i]:
            bars_above_233[i] = bars_above_233[i - 1] + 1
            bars_below_233[i] = 0
        elif c[i] < ema233[i]:
            bars_below_233[i] = bars_below_233[i - 1] + 1
            bars_above_233[i] = 0

    events = []
    last_exit_bar = -cooldown_bars - 1

    for i in range(250, n - max_holding_bars - 1):
        if (i - last_exit_bar) < cooldown_bars:
            continue

        c_bar = c[i]
        o_bar = o[i]
        h_bar = h[i]
        l_bar = l[i]
        a_bar = atr[i]

        came_from_above = (o_bar >= ema89[i] * 0.998) or (c[i - 1] >= ema89[i - 1] * 0.998)
        came_from_below = (o_bar <= ema89[i] * 1.002) or (c[i - 1] <= ema89[i - 1] * 1.002)

        sig = 0
        scenario = ""

        # Bullish Regime: EMA 89 > EMA 233
        if ema89[i] > ema233[i] and came_from_above and lower_wicks[i] >= 0.25:
            if (l_bar <= ema89[i] * 1.002 or l_bar <= ema55[i] * 1.002) and (c_bar >= ema89[i]):
                recent_below_233 = np.max(bars_below_233[max(0, i - 30) : i])
                if recent_below_233 >= dwell_bars_min:
                    sig = 1
                    scenario = "RECLAIM_SPRING"
                elif bars_above_233[max(0, i - 1)] >= dwell_bars_min:
                    sig = 1
                    scenario = "ESTABLISHED_TREND"

        # Bearish Regime: EMA 89 < EMA 233
        elif ema89[i] < ema233[i] and came_from_below and upper_wicks[i] >= 0.25:
            if (h_bar >= ema89[i] * 0.998 or h_bar >= ema55[i] * 0.998) and (c_bar <= ema89[i]):
                recent_above_233 = np.max(bars_above_233[max(0, i - 30) : i])
                if recent_above_233 >= dwell_bars_min:
                    sig = -1
                    scenario = "RECLAIM_SPRING"
                elif bars_below_233[max(0, i - 1)] >= dwell_bars_min:
                    sig = -1
                    scenario = "ESTABLISHED_TREND"

        if sig == 0:
            continue

        entry_bar = i + 1
        if t[entry_bar] < JAN_2024_TS:
            continue

        entry_price = o[entry_bar]
        baseline_sl = (l_bar - 0.30 * a_bar) if sig == 1 else (h_bar + 0.30 * a_bar)
        risk_dist = abs(entry_price - baseline_sl)
        if risk_dist <= 0 or (risk_dist / entry_price) < 0.0005:
            continue

        fwd_highs = h[entry_bar : entry_bar + max_holding_bars]
        fwd_lows = l[entry_bar : entry_bar + max_holding_bars]
        fwd_closes = c[entry_bar : entry_bar + max_holding_bars]

        if sig == 1:
            excursions_fav = (fwd_highs - entry_price) / risk_dist
            excursions_adv = (entry_price - fwd_lows) / risk_dist
        else:
            excursions_fav = (entry_price - fwd_lows) / risk_dist
            excursions_adv = (fwd_highs - entry_price) / risk_dist

        mfe = np.max(excursions_fav)
        mae = np.max(excursions_adv)

        targets = [2.0, 2.5, 3.0, 3.5, 4.0, 5.0, 6.0, 8.0, 10.0]
        hits_with_sl = {}
        hits_no_sl = {}

        for tgt in targets:
            hit_tp = False
            for b in range(len(fwd_highs)):
                bh = fwd_highs[b]
                bl = fwd_lows[b]
                cur_fav = (bh - entry_price) / risk_dist if sig == 1 else (entry_price - bl) / risk_dist
                cur_adv = (entry_price - bl) / risk_dist if sig == 1 else (bh - entry_price) / risk_dist
                if cur_adv >= 1.0:
                    break
                if cur_fav >= tgt:
                    hit_tp = True
                    break
            hits_with_sl[tgt] = 1 if hit_tp else 0

            hit_tp_no_sl = False
            for b in range(len(fwd_highs)):
                bh = fwd_highs[b]
                bl = fwd_lows[b]
                cur_fav = (bh - entry_price) / risk_dist if sig == 1 else (entry_price - bl) / risk_dist
                if cur_fav >= tgt:
                    hit_tp_no_sl = True
                    break
            hits_no_sl[tgt] = 1 if hit_tp_no_sl else 0

        # Optimized Breakeven Step Trail:
        trail_sl = baseline_sl
        max_seen_r = 0.0
        trail_r = 0.0
        exit_bar_offset = 0

        for b in range(len(fwd_highs)):
            bh = fwd_highs[b]
            bl = fwd_lows[b]
            cur_r = (bh - entry_price) / risk_dist if sig == 1 else (entry_price - bl) / risk_dist
            if cur_r > max_seen_r:
                max_seen_r = cur_r

            if max_seen_r >= 4.5:
                trail_sl = (entry_price + 3.0 * risk_dist) if sig == 1 else (entry_price - 3.0 * risk_dist)
            elif max_seen_r >= 2.5:
                trail_sl = (entry_price + 1.5 * risk_dist) if sig == 1 else (entry_price - 1.5 * risk_dist)
            elif max_seen_r >= 1.2:
                trail_sl = (entry_price + 0.1 * risk_dist) if sig == 1 else (entry_price - 0.1 * risk_dist)

            if sig == 1 and bl <= trail_sl:
                trail_r = (trail_sl - entry_price) / risk_dist
                exit_bar_offset = b
                break
            elif sig == -1 and bh >= trail_sl:
                trail_r = (entry_price - trail_sl) / risk_dist
                exit_bar_offset = b
                break
        else:
            trail_r = (fwd_closes[-1] - entry_price) / risk_dist if sig == 1 else (entry_price - fwd_closes[-1]) / risk_dist
            exit_bar_offset = len(fwd_highs) - 1

        last_exit_bar = entry_bar + exit_bar_offset

        events.append({
            "scenario": scenario,
            "side": "LONG" if sig == 1 else "SHORT",
            "entry_time": datetime.fromtimestamp(t[entry_bar], tz=timezone.utc).strftime("%Y-%m-%d %H:%M"),
            "mfe": round(mfe, 2),
            "mae": round(mae, 2),
            "hits_with_sl": hits_with_sl,
            "hits_no_sl": hits_no_sl,
            "trail_r": round(trail_r, 2),
        })

    return events


def main():
    print("=" * 95)
    print("EMPIRICAL SIMULATION: EMA 233 DWELL (>= 24H) + EMA 89 BOUNCE (WITH 12-BAR COOLDOWN)")
    print("Universe: 21 Optimal Basket Assets | Period: Jan 2024 - Nov 2026")
    print("=" * 95)

    basket_symbols = [x["symbol"] for x in OPTIMAL_ALPHA_BASKET]
    all_events = []

    for sym in basket_symbols:
        p = os.path.join(DATA_DIR, f"{sym}.parquet")
        if os.path.exists(p):
            evs = analyze_asset_dwell_refined(p, dwell_bars_min=6, cooldown_bars=12, max_holding_bars=60)
            for e in evs:
                e["symbol"] = sym
            all_events.extend(evs)

    df = pd.DataFrame(all_events)
    print(f"\nTotal Discrete Setups (Post-Cooldown): {len(df)}")
    print(f"Scenario Breakdown: Established Trend: {len(df[df['scenario']=='ESTABLISHED_TREND'])}, Reclaim Spring: {len(df[df['scenario']=='RECLAIM_SPRING'])}")

    # 1. MFE & MAE Overall
    print("\n--- 1. EXCURSION DISTRIBUTIONS (MFE vs MAE) ---")
    print(f"Mean MFE (Max Profit Run)    : {df['mfe'].mean():.2f} R  | Median: {df['mfe'].median():.2f} R")
    print(f"Mean MAE (Adverse Drawdown)  : {df['mae'].mean():.2f} R  | Median: {df['mae'].median():.2f} R")
    print(f"75th Percentile MFE          : {df['mfe'].quantile(0.75):.2f} R")
    print(f"90th Percentile MFE          : {df['mfe'].quantile(0.90):.2f} R")
    print(f"Max Adverse Drawdown (No SL) : -{df['mae'].max():.2f} R (Catastrophic Ruin Risk!)")

    # 2. RR Ladder Comparison
    targets = [2.0, 2.5, 3.0, 3.5, 4.0, 5.0, 6.0, 8.0, 10.0]
    print("\n--- 2. TARGET RR LADDER: FIXED SL vs NO SL ---")
    print("Target | WinRate (SL) | Net Expectancy (SL) | WinRate (No SL) | Avg MAE on Loss | Worst Single Loss")
    print("-" * 95)

    for tgt in targets:
        hits_sl = [e["hits_with_sl"][tgt] for e in all_events]
        wr_sl = np.mean(hits_sl) * 100
        exp_sl = (wr_sl / 100.0 * tgt) - ((100.0 - wr_sl) / 100.0 * 1.0) - 0.05

        hits_nosl = [e["hits_no_sl"][tgt] for e in all_events]
        wr_nosl = np.mean(hits_nosl) * 100
        losses_nosl = [e for e in all_events if e["hits_no_sl"][tgt] == 0]
        avg_mae = np.mean([l["mae"] for l in losses_nosl]) if losses_nosl else 0.0
        worst_mae = np.max([l["mae"] for l in losses_nosl]) if losses_nosl else 0.0

        best_flag = " ⭐ [OPTIMAL PRO-FIRM SWEET SPOT]" if tgt in [2.5, 3.0, 3.5] and exp_sl > 0 else ""
        print(f" {tgt:4.1f}R |    {wr_sl:5.1f}%    |       {exp_sl:+6.3f}R       |     {wr_nosl:5.1f}%     |    -{avg_mae:4.1f}R      |     -{worst_mae:5.1f}R {best_flag}")

    # 3. Optimized Step Trail
    trail_rs = [e["trail_r"] - 0.05 for e in all_events]
    trail_wins = [r for r in trail_rs if r > 0]
    trail_runners = [r for r in trail_rs if r >= 3.0]
    print("\n--- 3. OPTIMIZED BREAKEVEN + STEP TRAILING STOP ---")
    print(f"Total Trades Evaluated : {len(trail_rs)}")
    print(f"Win Rate (> 0R)        : {len(trail_wins)/len(trail_rs)*100:.1f}%")
    print(f"Net Expectancy / Trade : {np.mean(trail_rs):+6.3f} R")
    print(f"Total Net Return       : {np.sum(trail_rs):+7.2f} R")
    print(f"Big Runners (>= +3.0R) : {len(trail_runners)} trades ({len(trail_runners)/len(trail_rs)*100:.1f}%) | Max Winner: {max(trail_rs):+.2f} R")

    # 4. Scenario Comparison
    print("\n--- 4. SCENARIO A (RECLAIM SPRING) vs SCENARIO B (ESTABLISHED TREND) ---")
    for sc in ["ESTABLISHED_TREND", "RECLAIM_SPRING"]:
        sub = df[df["scenario"] == sc]
        if len(sub) == 0:
            continue
        sub_events = [e for e in all_events if e["scenario"] == sc]
        sub_hits = [e["hits_with_sl"][3.0] for e in sub_events]
        sub_wr = np.mean(sub_hits) * 100
        sub_exp = (sub_wr / 100.0 * 3.0) - ((100.0 - sub_wr) / 100.0 * 1.0) - 0.05
        print(f"[{sc:17s}] Trades: {len(sub):4d} | Mean MFE: {sub['mfe'].mean():.2f}R | Win Rate (3.0R): {sub_wr:.1f}% | Expectancy: {sub_exp:+.3f}R")

if __name__ == "__main__":
    main()
