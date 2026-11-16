#!/usr/bin/env python3
r"""scratch/run_live_basket_backtest.py: High-Precision Institutional Backtester
evaluating the exact OPTIMAL_ALPHA_BASKET (21 assets) with all CRO Invariants:
1. Pullback Origin Invariant: came_from_above (Long) & came_from_below (Short)
2. Mid Shelf (EMA 89/55) & Turbo Shelf (EMA 34/55 ADX>=22) - Zero Slow EMA 233 Traps
3. Rejection Wick >= 25%
4. 12-bar post-exit cooldown (48 hours)
5. Friction: 0.05R per trade (spread + commissions)
6. Portfolio Simulation: Max 3 concurrent positions, Max 1 per cluster
7. $10.00 USD target risk per trade (FundedNext $5,000 evaluation / scaling)
"""

import os
import sys
import glob
from datetime import datetime, timezone
import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "FundedNext_Data")
sys.path.insert(0, BASE_DIR)

from engine.mt5_alpha_trader_monitor import OPTIMAL_ALPHA_BASKET, calculate_ema, calculate_atr, compute_adx

JAN_2024_TS = 1704067200  # 2024-01-01 00:00:00 UTC


def backtest_single_asset(item, cooldown_bars=12, friction_r=0.05):
    symbol = item["symbol"]
    cluster = item["cluster"]
    setup = item["setup"]
    exit_mode = item["exit_mode"]

    parquet_path = os.path.join(DATA_DIR, f"{symbol}.parquet")
    if not os.path.exists(parquet_path):
        return None, []

    df = pd.read_parquet(parquet_path)
    if len(df) < 250:
        return None, []

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

    is_fixed = exit_mode.startswith("fixed_")
    fixed_rr = float(exit_mode.split("_")[1]) if is_fixed else 3.0

    trades = []
    in_trade = False
    side = 0
    ep = 0.0
    sl = 0.0
    rd = 0.0
    entry_ts = 0
    entry_bar = 0
    max_fav_r = 0.0
    last_exit_bar = -cooldown_bars - 1
    shelf_name = ""

    start_idx = 235

    for i in range(start_idx, n - 1):
        if in_trade:
            bh = h[i]
            bl = l[i]
            cur_r = (ep - bl) / rd if side == -1 else (bh - ep) / rd
            if cur_r > max_fav_r:
                max_fav_r = cur_r

            exited = False
            exit_r = 0.0

            if side == 1:  # LONG
                if is_fixed:
                    if bl <= sl:
                        exit_r = -1.0
                        exited = True
                    elif bh >= ep + fixed_rr * rd:
                        exit_r = fixed_rr
                        exited = True
                elif exit_mode == "step_trail":
                    trail_sl = sl
                    if max_fav_r >= 6.0:
                        trail_sl = max(trail_sl, ema55[i])
                    elif max_fav_r >= 4.0:
                        trail_sl = max(trail_sl, ep + 2.0 * rd)
                    elif max_fav_r >= 2.0:
                        trail_sl = max(trail_sl, ep + 0.5 * rd)
                    if bl <= trail_sl:
                        exit_r = (trail_sl - ep) / rd
                        exited = True
            elif side == -1:  # SHORT
                if is_fixed:
                    if bh >= sl:
                        exit_r = -1.0
                        exited = True
                    elif bl <= ep - fixed_rr * rd:
                        exit_r = fixed_rr
                        exited = True
                elif exit_mode == "step_trail":
                    trail_sl = sl
                    if max_fav_r >= 6.0:
                        trail_sl = min(trail_sl, ema55[i])
                    elif max_fav_r >= 4.0:
                        trail_sl = min(trail_sl, ep - 2.0 * rd)
                    elif max_fav_r >= 2.0:
                        trail_sl = min(trail_sl, ep - 0.5 * rd)
                    if bh >= trail_sl:
                        exit_r = (ep - trail_sl) / rd
                        exited = True

            if exited:
                net_r = exit_r - friction_r
                trades.append({
                    "symbol": symbol,
                    "cluster": cluster,
                    "setup": setup,
                    "shelf": shelf_name,
                    "exit_mode": exit_mode,
                    "side": "LONG" if side == 1 else "SHORT",
                    "entry_ts": entry_ts,
                    "exit_ts": t[i],
                    "entry_dt": datetime.fromtimestamp(entry_ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M"),
                    "exit_dt": datetime.fromtimestamp(t[i], tz=timezone.utc).strftime("%Y-%m-%d %H:%M"),
                    "entry_price": ep,
                    "stop_price": sl,
                    "risk_dist": rd,
                    "raw_r": exit_r,
                    "net_r": net_r,
                    "bars_held": i - entry_bar,
                })
                in_trade = False
                last_exit_bar = i
            continue

        # Cooldown check
        if (i - last_exit_bar) < cooldown_bars:
            continue

        c_bar = c[i]
        o_bar = o[i]
        h_bar = h[i]
        l_bar = l[i]
        a_bar = atr[i]

        sig = 0
        shelf_type = "MID"

        # Pullback Origin Invariant
        came_from_above = (o_bar >= ema89[i] * 0.998) or (c[i - 1] >= ema89[i - 1] * 0.998)
        came_from_below = (o_bar <= ema89[i] * 1.002) or (c[i - 1] <= ema89[i - 1] * 1.002)

        if setup == "canonical":
            if ema89[i] > ema233[i] and came_from_above:
                if lower_wicks[i] >= 0.25 and (l_bar <= ema89[i] * 1.002 or l_bar <= ema55[i] * 1.002) and (c_bar >= ema89[i]):
                    sig = 1; shelf_type = "MID"
            elif ema89[i] < ema233[i] and came_from_below:
                if upper_wicks[i] >= 0.25 and (h_bar >= ema89[i] * 0.998 or h_bar >= ema55[i] * 0.998) and (c_bar <= ema89[i]):
                    sig = -1; shelf_type = "MID"

        elif setup == "turbo_tri_tier":
            if ema89[i] > ema233[i] and c_bar >= ema233[i] and came_from_above:
                if lower_wicks[i] >= 0.25:
                    if adx[i] >= 22 and (l_bar <= ema34[i] * 1.002 or l_bar <= ema55[i] * 1.002) and (c_bar >= ema55[i]):
                        sig = 1; shelf_type = "TURBO"
                    elif (l_bar <= ema89[i] * 1.002) and (c_bar >= ema89[i]):
                        sig = 1; shelf_type = "MID"
            elif ema89[i] < ema233[i] and c_bar <= ema233[i] and came_from_below:
                if upper_wicks[i] >= 0.25:
                    if adx[i] >= 22 and (h_bar >= ema34[i] * 0.998 or h_bar >= ema55[i] * 0.998) and (c_bar <= ema55[i]):
                        sig = -1; shelf_type = "TURBO"
                    elif (h_bar >= ema89[i] * 0.998) and (c_bar <= ema89[i]):
                        sig = -1; shelf_type = "MID"

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
                        sig = 1; shelf_type = "MID"
                elif ema89[i] < ema233[i] and upper_wicks[i] >= 0.25 and came_from_below:
                    if (h_bar >= ema89[i] * 0.998 or h_bar >= ema55[i] * 0.998) and (c_bar <= ema89[i]):
                        sig = -1; shelf_type = "MID"

        if sig != 0:
            next_bar = i + 1
            if next_bar >= n:
                break
            if t[next_bar] < JAN_2024_TS:
                continue

            entry_price = o[next_bar]
            if shelf_type in ["TURBO", "SWEEP"]:
                stop_price = (l_bar - 0.25 * a_bar) if sig == 1 else (h_bar + 0.25 * a_bar)
            else:
                stop_price = (l_bar - 0.30 * a_bar) if sig == 1 else (h_bar + 0.30 * a_bar)

            risk_dist = abs(entry_price - stop_price)
            if risk_dist <= 0 or (risk_dist / entry_price) < 0.0005:
                continue

            in_trade = True
            side = sig
            ep = entry_price
            sl = stop_price
            rd = risk_dist
            entry_ts = t[next_bar]
            entry_bar = next_bar
            shelf_name = shelf_type
            max_fav_r = 0.0

    if not trades:
        return {
            "symbol": symbol, "cluster": cluster, "trades": 0, "net_r": 0.0, "win_rate": 0.0, "pf": 0.0, "max_dd": 0.0, "calmar": 0.0
        }, []

    df_t = pd.DataFrame(trades)
    wins = df_t[df_t["net_r"] > 0]
    losses = df_t[df_t["net_r"] <= 0]
    net_r = df_t["net_r"].sum()
    gross_win = wins["net_r"].sum()
    gross_loss = abs(losses["net_r"].sum())
    pf = round(gross_win / gross_loss, 2) if gross_loss > 0 else 99.0
    win_rate = round(len(wins) / len(df_t) * 100, 1)

    # Max Drawdown
    equity = df_t["net_r"].cumsum()
    peak = equity.cummax()
    dd = peak - equity
    max_dd = round(dd.max(), 2) if len(dd) > 0 else 0.0
    calmar = round(net_r / max_dd, 2) if max_dd > 0 else 0.0

    summary = {
        "symbol": symbol,
        "cluster": cluster,
        "setup": setup,
        "exit_mode": exit_mode,
        "trades": len(df_t),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": win_rate,
        "net_r": round(net_r, 2),
        "pf": pf,
        "max_dd": max_dd,
        "calmar": calmar,
    }
    return summary, trades


def simulate_portfolio(all_trades, max_concurrent=3, max_per_cluster=1):
    """Simulates realistic chronological execution with cluster & concurrency firewalls."""
    if not all_trades:
        return 0.0, 0.0, 0, 0.0, []

    df_all = pd.DataFrame(all_trades)
    df_all = df_all.sort_values("entry_ts").reset_index(drop=True)

    active_positions = []  # list of {cluster, exit_ts, net_r, symbol}
    executed_trades = []

    for idx, row in df_all.iterrows():
        # Free up expired positions
        active_positions = [p for p in active_positions if p["exit_ts"] > row["entry_ts"]]

        # Check concurrency ceiling
        if len(active_positions) >= max_concurrent:
            continue

        # Check cluster firewall
        current_clusters = [p["cluster"] for p in active_positions]
        if current_clusters.count(row["cluster"]) >= max_per_cluster:
            continue

        # Position allowed!
        active_positions.append({
            "symbol": row["symbol"],
            "cluster": row["cluster"],
            "exit_ts": row["exit_ts"],
            "net_r": row["net_r"],
        })
        executed_trades.append(row)

    if not executed_trades:
        return 0.0, 0.0, 0, 0.0, []

    df_exec = pd.DataFrame(executed_trades)
    # Sort by exit_ts to calculate realistic equity curve
    df_exec = df_exec.sort_values("exit_ts").reset_index(drop=True)
    df_exec["cum_r"] = df_exec["net_r"].cumsum()
    peak = df_exec["cum_r"].cummax()
    dd = peak - df_exec["cum_r"]
    max_dd = round(dd.max(), 2)
    total_net_r = round(df_exec["net_r"].sum(), 2)
    calmar = round(total_net_r / max_dd, 2) if max_dd > 0 else 0.0
    win_rate = round((df_exec["net_r"] > 0).mean() * 100, 1)

    return total_net_r, max_dd, len(df_exec), win_rate, df_exec


def main():
    print("=" * 100)
    print("FUNDEDNEXT 21-ASSET LIVE ALPHA BASKET BACKTEST (JAN 2024 - PRESENT)")
    print("Invariants: came_from_above / came_from_below | Mid/Turbo Shelves | 25% Wick | 12-bar Cooldown | 0.05R Friction")
    print("=" * 100)

    asset_summaries = []
    all_trades = []

    for item in OPTIMAL_ALPHA_BASKET:
        summary, trades = backtest_single_asset(item)
        if summary:
            asset_summaries.append(summary)
            all_trades.extend(trades)

    df_summary = pd.DataFrame(asset_summaries).sort_values("net_r", ascending=False)
    print("\n--- INDIVIDUAL ASSET PERFORMANCE (UNCONSTRAINED) ---")
    print(df_summary.to_string(index=False))

    # Long vs Short breakdown
    df_all_trades = pd.DataFrame(all_trades)
    longs = df_all_trades[df_all_trades["side"] == "LONG"]
    shorts = df_all_trades[df_all_trades["side"] == "SHORT"]

    print("\n--- LONG VS SHORT BREAKDOWN ---")
    print(f"LONG TRADES : {len(longs):4d} trades | Net R: {longs['net_r'].sum():+7.2f}R | Win Rate: {(longs['net_r']>0).mean()*100:.1f}%")
    print(f"SHORT TRADES: {len(shorts):4d} trades | Net R: {shorts['net_r'].sum():+7.2f}R | Win Rate: {(shorts['net_r']>0).mean()*100:.1f}%")

    # Cluster Breakdown
    print("\n--- CLUSTER PERFORMANCE SUMMARY ---")
    cluster_grp = df_all_trades.groupby("cluster").agg(
        trades=("net_r", "count"),
        net_r=("net_r", "sum"),
        win_rate=("net_r", lambda x: round((x > 0).mean() * 100, 1))
    ).reset_index().sort_values("net_r", ascending=False)
    print(cluster_grp.to_string(index=False))

    # Portfolio Simulation (Max 3 Concurrent, Max 1 per Cluster)
    p_net_r, p_max_dd, p_trades, p_wr, df_exec = simulate_portfolio(all_trades, max_concurrent=3, max_per_cluster=1)

    print("\n" + "=" * 100)
    print("PORTFOLIO SIMULATION RESULTS (FUNDEDNEXT CONSTRAINTS)")
    print("Rules: Max 3 Concurrent Positions | Max 1 Position per Cluster | $10 USD Risk per Trade")
    print("=" * 100)
    print(f"Total Portfolio Trades Taken : {p_trades}")
    print(f"Overall Portfolio Win Rate    : {p_wr}%")
    print(f"Total Portfolio Net Return   : {p_net_r:+7.2f} R")
    print(f"Maximum Portfolio Drawdown   : {p_max_dd:7.2f} R")
    print(f"Portfolio Calmar Ratio       : {round(p_net_r / p_max_dd, 2) if p_max_dd > 0 else 0.0}")
    print(f"Dollar PnL ($10 USD Risk)    : ${p_net_r * 10.0:,.2f} USD")
    print(f"Max Dollar Drawdown ($10 Risk): ${p_max_dd * 10.0:,.2f} USD ({p_max_dd * 10.0 / 5000 * 100:.2f}% of $5k Account)")
    
    # Yearly breakdown of executed trades
    if not df_exec.empty:
        df_exec["year"] = pd.to_datetime(df_exec["entry_ts"], unit="s").dt.year
        yearly = df_exec.groupby("year").agg(
            trades=("net_r", "count"),
            net_r=("net_r", "sum"),
            win_rate=("net_r", lambda x: round((x > 0).mean() * 100, 1))
        ).reset_index()
        print("\n--- YEAR-BY-YEAR PORTFOLIO RETURN ---")
        print(yearly.to_string(index=False))

if __name__ == "__main__":
    main()
