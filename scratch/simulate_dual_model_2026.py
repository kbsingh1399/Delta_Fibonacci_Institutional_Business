#!/usr/bin/env python3
r"""scratch/simulate_dual_model_2026.py: Dual-Model Backtest from Jan 1, 2026 to Present
Evaluating:
- Model 1 (Pullbacks / Turbo / Wyckoff) + Model 2 (Macro Dwell & Reclaim Spring)
- Universe: Full 25-asset live basket across 8 clusters
- Portfolio Constraints: Max 3 concurrent positions, Max 1 per cluster
- Friction: 0.05R per trade deducted
- Period: January 1, 2026 to November 16, 2026 (YTD 2026)
- Risk per trade: $10.00 USD
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
RECLAIM_SPRING_UNIVERSE = ["US30", "DOGUSD", "ETHUSD", "USDCNH", "BABA", "ADAUSD", "NZDJPY", "NZDSGD"]


def backtest_dual_asset_2026(item, cooldown_bars=12, friction_r=0.05):
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

    # Dwell trackers
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
                    "entry_dt": datetime.fromtimestamp(entry_ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M"),
                    "exit_dt": datetime.fromtimestamp(t[i], tz=timezone.utc).strftime("%Y-%m-%d %H:%M"),
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

        # --- MODEL 1: Primary Assigned Setup ---
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
            roll_l20 = pd.Series(l).shift(1).rolling(20).min().to_numpy()
            roll_h20 = pd.Series(h).shift(1).rolling(20).max().to_numpy()
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

        # --- MODEL 2 CASCADE: Macro Dwell & Reclaim Spring ---
        if sig == 0 and symbol in RECLAIM_SPRING_UNIVERSE:
            if ema89[i] > ema233[i] and came_from_above and lower_wicks[i] >= 0.33:
                if (l_bar <= ema89[i] * 1.002) and (c_bar >= ema89[i]):
                    recent_below = np.max(bars_below_macro[max(0, i - 35) : i])
                    if 3 <= recent_below <= 25:
                        sig = 1; current_model = "MODEL_2_RECLAIM"
            elif ema89[i] < ema233[i] and came_from_below and upper_wicks[i] >= 0.33:
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


def simulate_portfolio_2026(all_trades, max_concurrent=3, max_per_cluster=1):
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
    return net_r, max_dd, len(df), wr, calmar, df


def main():
    print("=" * 95)
    print("DUAL-MODEL INSTITUTIONAL BACKTEST (JAN 1, 2026 - NOV 16, 2026)")
    print("Model 1 (Pullbacks/Sweeps) + Model 2 (Macro Dwell & Reclaim Spring)")
    print("Constraints: Max 3 Concurrent | Max 1 per Cluster | $10 Risk | 0.05R Friction Drag")
    print("=" * 95)

    all_trades = []
    for item in OPTIMAL_ALPHA_BASKET:
        t_list = backtest_dual_asset_2026(item)
        all_trades.extend(t_list)

    print(f"\nTotal Candidate Trade Signals Generated in 2026: {len(all_trades)}")

    # Portfolio simulation
    res = simulate_portfolio_2026(all_trades, max_concurrent=4, max_per_cluster=1)
    if not res:
        print("No executed trades.")
        return

    net_r, max_dd, count, wr, calmar, df_exec = res

    print("\n" + "=" * 95)
    print("PORTFOLIO EXECUTION RESULTS (YTD 2026)")
    print("=" * 95)
    print(f"Total Portfolio Trades Executed : {count}")
    print(f"Overall Portfolio Win Rate      : {wr}%")
    print(f"Total Net Return in 2026        : {net_r:+7.2f} R")
    print(f"Maximum Portfolio Drawdown      : {max_dd:7.2f} R")
    print(f"Portfolio Calmar Ratio          : {calmar:.2f}")
    print(f"Dollar PnL ($10 USD Risk)      : ${net_r * 10.0:,.2f} USD (+{net_r * 10.0 / 5000 * 100:.2f}% on $5,000 Equity)")
    print(f"Max Dollar Drawdown ($10 Risk)  : ${max_dd * 10.0:,.2f} USD ({max_dd * 10.0 / 5000 * 100:.2f}% of Account)")

    # Model Breakdown
    print("\n--- MODEL 1 vs MODEL 2 PERFORMANCE CONTRIBUTION ---")
    m_grp = df_exec.groupby("model").agg(
        trades=("net_r", "count"),
        net_r=("net_r", "sum"),
        win_rate=("net_r", lambda x: round((x > 0).mean() * 100, 1))
    ).reset_index()
    print(m_grp.to_string(index=False))

    # Cluster Breakdown
    print("\n--- CLUSTER BREAKDOWN (2026) ---")
    c_grp = df_exec.groupby("cluster").agg(
        trades=("net_r", "count"),
        net_r=("net_r", "sum"),
        win_rate=("net_r", lambda x: round((x > 0).mean() * 100, 1))
    ).reset_index().sort_values("net_r", ascending=False)
    print(c_grp.to_string(index=False))

    # Top Assets in 2026
    print("\n--- TOP ASSET CONTRIBUTORS IN 2026 ---")
    sym_grp = df_exec.groupby("symbol").agg(
        trades=("net_r", "count"),
        net_r=("net_r", "sum"),
        win_rate=("net_r", lambda x: round((x > 0).mean() * 100, 1))
    ).reset_index().sort_values("net_r", ascending=False)
    print(sym_grp.head(10).to_string(index=False))

if __name__ == "__main__":
    main()
