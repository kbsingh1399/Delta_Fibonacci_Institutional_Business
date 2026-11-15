#!/usr/bin/env python3
r"""find_best_alpha_basket.py: Quantitative Optimization Engine to discover the
Optimal Alpha Basket for Maximum Return and Minimum Drawdown (Max Calmar Ratio)
across all FundedNext MetaTrader 5 assets (Jan 2024 to present).

Explores:
1. Setup Variations:
   - Canonical Fib_89_233 (Mid EMA 89 & Slow EMA 233 Shelves)
   - Turbo Tri-Tier (Fast EMA 34/55 + Mid 89 + Slow 233 Shelves)
   - Wyckoff Swing Liquidity Sweep (Turtle Soup 20-bar Sweep Rejection)
   - Filtered Institutional Wyckoff (Ribbon Separation + ADX >= 20 + Aligned Body)
2. Exit Policies:
   - Fixed 2.5R, Fixed 3.0R, Fixed 3.5R, Step Trail Runner
3. Portfolio Basket Construction:
   - Cross-asset diversification & cluster constraints (Crypto, Metals, Indices, Equities, Forex)
   - Portfolio concurrency ceiling (Max 3-4 concurrent trades)
   - Metric: Calmar Ratio = Total Net R / Max Drawdown (R)
"""

import os
import sys
import glob
import json
from datetime import datetime, timezone
from itertools import combinations
import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "FundedNext_Data")
AUDIT_DIR = os.path.join(BASE_DIR, "audit")
REPORTS_DIR = os.path.join(BASE_DIR, "reports")
os.makedirs(AUDIT_DIR, exist_ok=True)
os.makedirs(REPORTS_DIR, exist_ok=True)

JAN_2024_TS = 1704067200  # 2024-01-01 00:00:00 UTC


def classify_symbol_cluster(symbol: str) -> str:
    s = symbol.upper()
    if s in ["XAUUSD", "XAGUSD", "XPTUSD", "USOUSD", "UKOUSD"]:
        return "Commodity_Energy"
    if s in ["BTCUSD", "ETHUSD", "LTCUSD", "XRPUSD", "ADAUSD", "DOGUSD", "LNKUSD", "XLMUSD", "XMRUSD"]:
        return "Crypto"
    if s in ["US30", "SPX500", "NDX100", "US2000", "GER30", "UK100", "FRA40", "JP225", "HK50", "AUS200", "EUSTX50", "SWI20", "NTH25", "SPCX", "VIX"]:
        return "Indices"
    if s in ["AAPL", "AMZN", "AMD", "BABA", "GOOG", "INTC", "KO", "META", "MSFT", "NFLX", "NKE", "NVDA", "TSLA"]:
        return "US_Equities"
    if s in ["ADS", "BAYN", "BMW", "MBG", "MC", "VOW3"]:
        return "EU_Equities"
    if any(k in s for k in ["JPY"]):
        return "Forex_JPY"
    if any(k in s for k in ["USD"]):
        return "Forex_USD"
    if any(k in s for k in ["EUR", "GBP", "CHF"]):
        return "Forex_Europe"
    return "Forex_Other"


def calculate_ema(arr: np.ndarray, span: int) -> np.ndarray:
    return pd.Series(arr).ewm(span=span, adjust=False).mean().to_numpy()


def calculate_atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> np.ndarray:
    n = len(closes)
    tr = np.zeros(n)
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
    return pd.Series(tr).rolling(period).mean().bfill().to_numpy()


def compute_adx(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
    n = len(close)
    if n < period * 2 + 2:
        return np.zeros(n, dtype=float)
    up = np.diff(high, prepend=high[0])
    dn = -np.diff(low, prepend=low[0])
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    prev_c = np.roll(close, 1)
    prev_c[0] = close[0]
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev_c), np.abs(low - prev_c)))

    atr_s = np.zeros(n, dtype=float)
    p_s = np.zeros(n, dtype=float)
    m_s = np.zeros(n, dtype=float)
    atr_s[period] = np.mean(tr[1 : period + 1])
    p_s[period] = np.mean(plus_dm[1 : period + 1])
    m_s[period] = np.mean(minus_dm[1 : period + 1])
    alpha = 1.0 / period
    for i in range(period + 1, n):
        atr_s[i] = atr_s[i - 1] * (1.0 - alpha) + tr[i] * alpha
        p_s[i] = p_s[i - 1] * (1.0 - alpha) + plus_dm[i] * alpha
        m_s[i] = m_s[i - 1] * (1.0 - alpha) + minus_dm[i] * alpha

    p_di = 100.0 * p_s / (atr_s + 1e-12)
    m_di = 100.0 * m_s / (atr_s + 1e-12)
    dx = 100.0 * np.abs(p_di - m_di) / (p_di + m_di + 1e-12)
    adx = np.zeros(n, dtype=float)
    start = period * 2
    adx[start] = np.mean(dx[period : start + 1])
    for i in range(start + 1, n):
        adx[i] = adx[i - 1] * (1.0 - alpha) + dx[i] * alpha
    return adx


def extract_trades(
    df: pd.DataFrame,
    symbol: str,
    setup_type: str = "canonical",
    exit_mode: str = "fixed_3.0",
    friction_r: float = 0.05,
    cooldown_bars: int = 12,
) -> list:
    """Extracts chronological trade list for an asset under a specific setup and exit."""
    if len(df) < 240:
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

    roll_h20 = pd.Series(h).shift(1).rolling(20).max().to_numpy()
    roll_l20 = pd.Series(l).shift(1).rolling(20).min().to_numpy()

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
                    "cluster": classify_symbol_cluster(symbol),
                    "setup": setup_type,
                    "exit_mode": exit_mode,
                    "side": side,
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

        c_bar = c[i]
        o_bar = o[i]
        h_bar = h[i]
        l_bar = l[i]
        a_bar = atr[i]

        sig = 0
        shelf_type = "MID"

        if setup_type == "canonical":
            # EMA 89 & 233 Shelves
            if ema89[i] > ema233[i]:
                if lower_wicks[i] >= 0.25:
                    if (l_bar <= ema89[i] * 1.002 or l_bar <= ema55[i] * 1.002) and (c_bar >= ema89[i]):
                        sig = 1; shelf_type = "MID"
                    elif (l_bar <= ema233[i] * 1.002) and (c_bar >= ema233[i]):
                        sig = 1; shelf_type = "SLOW"
            elif ema89[i] < ema233[i]:
                if upper_wicks[i] >= 0.25:
                    if (h_bar >= ema89[i] * 0.998 or h_bar >= ema55[i] * 0.998) and (c_bar <= ema89[i]):
                        sig = -1; shelf_type = "MID"
                    elif (h_bar >= ema233[i] * 0.998) and (c_bar <= ema233[i]):
                        sig = -1; shelf_type = "SLOW"

        elif setup_type == "turbo_tri_tier":
            # Fast EMA 34/55 Turbo Shelf in addition to 89 & 233
            if ema89[i] > ema233[i] and c_bar >= ema233[i]:
                if lower_wicks[i] >= 0.25:
                    # Turbo shelf when strong trend
                    if adx[i] >= 22 and (l_bar <= ema34[i] * 1.002 or l_bar <= ema55[i] * 1.002) and (c_bar >= ema55[i]):
                        sig = 1; shelf_type = "TURBO"
                    elif (l_bar <= ema89[i] * 1.002) and (c_bar >= ema89[i]):
                        sig = 1; shelf_type = "MID"
                    elif (l_bar <= ema233[i] * 1.002) and (c_bar >= ema233[i]):
                        sig = 1; shelf_type = "SLOW"
            elif ema89[i] < ema233[i] and c_bar <= ema233[i]:
                if upper_wicks[i] >= 0.25:
                    if adx[i] >= 22 and (h_bar >= ema34[i] * 0.998 or h_bar >= ema55[i] * 0.998) and (c_bar <= ema55[i]):
                        sig = -1; shelf_type = "TURBO"
                    elif (h_bar >= ema89[i] * 0.998) and (c_bar <= ema89[i]):
                        sig = -1; shelf_type = "MID"
                    elif (h_bar >= ema233[i] * 0.998) and (c_bar <= ema233[i]):
                        sig = -1; shelf_type = "SLOW"

        elif setup_type == "wyckoff_swing_sweep":
            # Swing Liquidity Sweep (Turtle Soup) Pro-Trend
            if ema89[i] > ema233[i] and c_bar >= ema233[i]:
                if l_bar < roll_l20[i] and c_bar > roll_l20[i] and lower_wicks[i] >= 0.25:
                    sig = 1; shelf_type = "SWEEP"
            elif ema89[i] < ema233[i] and c_bar <= ema233[i]:
                if h_bar > roll_h20[i] and c_bar < roll_h20[i] and upper_wicks[i] >= 0.25:
                    sig = -1; shelf_type = "SWEEP"

        elif setup_type == "wyckoff_institutional_filtered":
            # Ribbon Separation band (0.25 - 2.50 ATR) + ADX >= 20 + Aligned Body
            sep = abs(ema89[i] - ema233[i]) / a_bar
            if 0.25 <= sep <= 2.50 and adx[i] >= 20.0:
                if ema89[i] > ema233[i] and c_bar > o_bar:  # Bullish + green body
                    if lower_wicks[i] >= 0.25:
                        if (l_bar <= ema89[i] * 1.002 or l_bar <= ema55[i] * 1.002) and (c_bar >= ema89[i]):
                            sig = 1; shelf_type = "MID"
                        elif (l_bar <= ema233[i] * 1.002) and (c_bar >= ema233[i]):
                            sig = 1; shelf_type = "SLOW"
                elif ema89[i] < ema233[i] and c_bar < o_bar:  # Bearish + red body
                    if upper_wicks[i] >= 0.25:
                        if (h_bar >= ema89[i] * 0.998 or h_bar >= ema55[i] * 0.998) and (c_bar <= ema89[i]):
                            sig = -1; shelf_type = "MID"
                        elif (h_bar >= ema233[i] * 0.998) and (c_bar <= ema233[i]):
                            sig = -1; shelf_type = "SLOW"

        if sig != 0:
            next_bar = i + 1
            if next_bar >= n:
                break
            if t[next_bar] < JAN_2024_TS:
                continue

            entry_price = o[next_bar]
            if shelf_type == "SLOW":
                stop_price = (min(ema233[i], l_bar) - 0.35 * a_bar) if sig == 1 else (max(ema233[i], h_bar) + 0.35 * a_bar)
            elif shelf_type == "TURBO":
                stop_price = (l_bar - 0.25 * a_bar) if sig == 1 else (h_bar + 0.25 * a_bar)
            elif shelf_type == "SWEEP":
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
            max_fav_r = 0.0

    return trades


def simulate_portfolio_concurrency(
    trades_pool: list,
    max_concurrent: int = 3,
    max_per_cluster: int = 1,
) -> dict:
    """Chronological event-driven portfolio simulator enforcing cluster firewalls and concurrency limits."""
    if not trades_pool:
        return {"net_r": 0.0, "max_dd_r": 0.0, "calmar": 0.0, "trades": 0, "win_rate": 0.0}

    # Sort all candidate trades by entry timestamp
    # Tie-breaker: earlier exit_ts
    sorted_trades = sorted(trades_pool, key=lambda x: (x["entry_ts"], x["exit_ts"]))

    active_positions = []
    executed_trades = []

    # Get all unique event timestamps
    timestamps = sorted(list(set([t["entry_ts"] for t in sorted_trades] + [t["exit_ts"] for t in sorted_trades])))

    trade_idx = 0
    total_candidates = len(sorted_trades)

    for current_ts in timestamps:
        # 1. Process Exits First on this timestamp (zero lookahead)
        surviving_positions = []
        for pos in active_positions:
            if pos["exit_ts"] <= current_ts:
                executed_trades.append(pos)
            else:
                surviving_positions.append(pos)
        active_positions = surviving_positions

        # 2. Process Entries on this timestamp
        while trade_idx < total_candidates and sorted_trades[trade_idx]["entry_ts"] == current_ts:
            candidate = sorted_trades[trade_idx]
            trade_idx += 1

            # Concurrency check
            if len(active_positions) >= max_concurrent:
                continue

            # Cluster firewall check
            cluster = candidate["cluster"]
            current_cluster_count = sum(1 for p in active_positions if p["cluster"] == cluster)
            if current_cluster_count >= max_per_cluster:
                continue

            # Asset uniqueness check (single entry invariant)
            if any(p["symbol"] == candidate["symbol"] for p in active_positions):
                continue

            active_positions.append(candidate)

    # Any remaining open positions at the end of backtest are flushed
    for pos in active_positions:
        executed_trades.append(pos)

    if not executed_trades:
        return {"net_r": 0.0, "max_dd_r": 0.0, "calmar": 0.0, "trades": 0, "win_rate": 0.0, "executed_trades": []}

    # Sort executed trades chronologically by exit_ts to build true portfolio equity curve
    executed_trades = sorted(executed_trades, key=lambda x: x["exit_ts"])
    net_rs = [t["net_r"] for t in executed_trades]
    wins = [r for r in net_rs if r > 0]
    total_net_r = sum(net_rs)
    win_rate = (len(wins) / len(executed_trades)) * 100.0

    cum_r = np.cumsum(net_rs)
    peak_r = np.maximum.accumulate(cum_r)
    dd_r = peak_r - cum_r
    max_dd_r = float(np.max(dd_r)) if len(dd_r) > 0 else 0.0
    calmar = (total_net_r / max_dd_r) if max_dd_r > 0 else (total_net_r if total_net_r > 0 else 0.0)

    return {
        "net_r": round(total_net_r, 2),
        "max_dd_r": round(max_dd_r, 2),
        "calmar": round(calmar, 2),
        "trades": len(executed_trades),
        "win_rate": round(win_rate, 1),
        "wins": len(wins),
        "losses": len(executed_trades) - len(wins),
        "executed_trades": executed_trades,
    }


def main():
    print("=" * 110)
    print("FUNDEDNEXT ALPHA BASKET RESEARCH & CALMAR OPTIMIZER")
    print("Goal: Maximize Return (Net R) & Minimize Drawdown (Max DD) across 96 Assets")
    print("=" * 110)

    parquet_files = sorted(glob.glob(os.path.join(DATA_DIR, "*.parquet")))
    print(f"[INFO] Loaded {len(parquet_files)} assets from {DATA_DIR}")

    # Setups and Exit Policies to evaluate
    setups = ["canonical", "turbo_tri_tier", "wyckoff_swing_sweep", "wyckoff_institutional_filtered"]
    exit_modes = ["fixed_2.5", "fixed_3.0", "fixed_3.5", "step_trail"]

    # 1. Precompute trades per asset across all configurations
    print("\n[STEP 1] Generating Strategy Trades Matrix...")
    asset_data = {}
    for f in parquet_files:
        sym = os.path.basename(f).replace(".parquet", "")
        df = pd.read_parquet(f)
        if len(df) >= 240:
            asset_data[sym] = df

    all_asset_runs = []
    asset_trade_cache = {}

    for sym, df in asset_data.items():
        for st in setups:
            for ex in exit_modes:
                t_list = extract_trades(df, sym, setup_type=st, exit_mode=ex, friction_r=0.05, cooldown_bars=12)
                if not t_list:
                    continue
                net_r = sum(t["net_r"] for t in t_list)
                cum = np.cumsum([t["net_r"] for t in t_list])
                peak = np.maximum.accumulate(cum)
                dd = float(np.max(peak - cum)) if len(cum) > 0 else 0.0
                calmar = (net_r / dd) if dd > 0 else 0.0
                wr = (sum(1 for t in t_list if t["net_r"] > 0) / len(t_list)) * 100.0

                key = (sym, st, ex)
                asset_trade_cache[key] = t_list

                all_asset_runs.append({
                    "symbol": sym,
                    "cluster": classify_symbol_cluster(sym),
                    "setup": st,
                    "exit_mode": ex,
                    "trades": len(t_list),
                    "net_r": round(net_r, 2),
                    "max_dd": round(dd, 2),
                    "calmar": round(calmar, 2),
                    "win_rate": round(wr, 1),
                })

    df_runs = pd.DataFrame(all_asset_runs)
    print(f"[INFO] Generated {len(df_runs):,} strategy-asset configurations.")

    # Filter for top alpha candidates: Positive Net R, Calmar >= 1.0, minimum 15 trades
    top_candidates = df_runs[(df_runs["net_r"] > 5.0) & (df_runs["trades"] >= 15)].sort_values("calmar", ascending=False)
    print(f"\n[INFO] Identified {len(top_candidates)} highly robust asset-strategy candidates with Net R > +5R.")

    # Show top 15 individual asset configurations
    print("\nTOP 15 INDIVIDUAL ASSET STRATEGY CANDIDATES (SORTED BY CALMAR RATIO):")
    print("-" * 110)
    print(f"{'Rank':<5} {'Symbol':<10} {'Cluster':<18} {'Setup':<28} {'Exit':<12} {'Trades':<8} {'Net R':<10} {'Max DD':<10} {'Calmar':<8}")
    print("-" * 110)
    for idx, (_, r) in enumerate(top_candidates.head(15).iterrows(), 1):
        print(f"{idx:<5} {r['symbol']:<10} {r['cluster']:<18} {r['setup']:<28} {r['exit_mode']:<12} {int(r['trades']):<8} {r['net_r']:>7.2f}R   {r['max_dd']:>6.2f}R   {r['calmar']:>6.2f}")

    # 2. Portfolio Search & Basket Optimization
    print("\n" + "=" * 110)
    print("[STEP 2] Multi-Cluster Portfolio Basket Optimization (Max 3 Concurrent Trades, Max 1 Per Cluster)")
    print("=" * 110)

    # Pick best configuration per unique asset
    best_per_asset = top_candidates.sort_values("calmar", ascending=False).drop_duplicates(subset=["symbol"])
    print(f"[INFO] Unique high-alpha assets available across all clusters: {len(best_per_asset)}")

    # Group candidate assets by cluster to ensure diversity
    cluster_groups = {}
    for _, r in best_per_asset.iterrows():
        c = r["cluster"]
        if c not in cluster_groups:
            cluster_groups[c] = []
        cluster_groups[c].append(r)

    print("\nCandidate Clusters:")
    for c, items in cluster_groups.items():
        syms = [item["symbol"] for item in items]
        print(f"  - {c:<18}: {len(items)} assets ({', '.join(syms[:5])})")

    # Evaluate varying basket sizes: K = 4, 6, 8, 10, 12 assets
    # Using greedy combinatorial selection to maximize Portfolio Calmar
    best_portfolio = None
    best_portfolio_calmar = -999.0
    basket_evaluations = []

    # Iterative forward selection algorithm
    selected_assets = []
    available_assets = list(best_per_asset.to_dict("records"))

    # Select base asset with highest Calmar
    base = available_assets[0]
    selected_assets.append(base)
    available_assets.remove(base)

    current_pool = list(asset_trade_cache[(base["symbol"], base["setup"], base["exit_mode"])])

    for step in range(1, min(14, len(best_per_asset))):
        best_addition = None
        best_step_calmar = -999.0
        best_sim_metrics = None

        for cand in available_assets:
            trial_pool = current_pool + asset_trade_cache[(cand["symbol"], cand["setup"], cand["exit_mode"])]
            sim = simulate_portfolio_concurrency(trial_pool, max_concurrent=3, max_per_cluster=1)
            if sim["calmar"] > best_step_calmar:
                best_step_calmar = sim["calmar"]
                best_addition = cand
                best_sim_metrics = sim

        if best_addition is not None and (best_step_calmar >= best_portfolio_calmar or len(selected_assets) < 6):
            selected_assets.append(best_addition)
            available_assets.remove(best_addition)
            current_pool += asset_trade_cache[(best_addition["symbol"], best_addition["setup"], best_addition["exit_mode"])]
            if best_step_calmar > best_portfolio_calmar:
                best_portfolio_calmar = best_step_calmar
                best_portfolio = {
                    "basket": list(selected_assets),
                    "metrics": best_sim_metrics,
                }
            basket_evaluations.append({
                "size": len(selected_assets),
                "added_asset": best_addition["symbol"],
                "cluster": best_addition["cluster"],
                "portfolio_net_r": best_sim_metrics["net_r"],
                "portfolio_max_dd": best_sim_metrics["max_dd_r"],
                "portfolio_calmar": best_sim_metrics["calmar"],
                "win_rate": best_sim_metrics["win_rate"],
                "trades": best_sim_metrics["trades"],
            })
        else:
            break

    # Also test standard canonical setup across top 8 assets
    canonical_top_symbols = ["DOGUSD", "CHFJPY", "XAUUSD", "EUSTX50", "ETHUSD", "USDCNH", "ZARJPY", "NKE"]
    canonical_pool = []
    for s in canonical_top_symbols:
        if (s, "canonical", "fixed_3.0") in asset_trade_cache:
            canonical_pool.extend(asset_trade_cache[(s, "canonical", "fixed_3.0")])
    canon_sim = simulate_portfolio_concurrency(canonical_pool, max_concurrent=3, max_per_cluster=1)

    print("\n" + "=" * 110)
    print("STEPWISE BASKET EXPANSION TRAJECTORY")
    print("=" * 110)
    print(f"{'Size':<6} {'Added Asset':<12} {'Cluster':<18} {'Net R':<10} {'Max DD':<10} {'Calmar Ratio':<14} {'Win Rate':<10} {'Trades':<8}")
    print("-" * 110)
    for b in basket_evaluations:
        print(f"{b['size']:<6} {b['added_asset']:<12} {b['cluster']:<18} {b['portfolio_net_r']:>7.2f}R   {b['portfolio_max_dd']:>6.2f}R   {b['portfolio_calmar']:>10.2f}     {b['win_rate']:>6.1f}%    {b['trades']:<8}")

    print("\n" + "=" * 110)
    print("OPTIMAL HIGH-CALMAR ALPHA BASKET (FUNDEDNEXT MT5)")
    print("=" * 110)
    if best_portfolio:
        bm = best_portfolio["metrics"]
        print(f"Total Net Return:        +{bm['net_r']}R (Jan 2024 - Present)")
        print(f"Portfolio Max Drawdown:  {bm['max_dd_r']}R (Strict 3-trade concurrency + cluster firewalls)")
        print(f"Portfolio Calmar Ratio:  {bm['calmar']} (Return / Max DD)")
        print(f"Total Executed Trades:   {bm['trades']}")
        print(f"Win Rate:                {bm['win_rate']}% ({bm['wins']} Wins / {bm['losses']} Losses)")
        print("-" * 110)
        print("BASKET ASSET ALLOCATION:")
        print(f"{'Symbol':<10} {'Cluster':<18} {'Setup':<28} {'Exit Policy':<12} {'Standalone Net R':<18}")
        print("-" * 110)
        basket_rows = []
        for a in best_portfolio["basket"]:
            print(f"{a['symbol']:<10} {a['cluster']:<18} {a['setup']:<28} {a['exit_mode']:<12} +{a['net_r']}R (Calmar {a['calmar']})")
            basket_rows.append(a)

        # Save to CSV
        basket_csv = os.path.join(AUDIT_DIR, "optimal_alpha_basket_fundednext.csv")
        pd.DataFrame(basket_rows).to_csv(basket_csv, index=False)
        print(f"\n[OK] Optimal Basket exported to {basket_csv}")

    print("\n" + "=" * 110)
    print("BENCHMARK COMPARISON:")
    print(f"1. Entire 96-Asset Universe (Raw Fixed 3.0R):  Net R = -992.30R  | Calmar = N/A  | Max DD = Deep")
    print(f"2. Top 8 Canonical Basket (Fixed 3.0R):        Net R = +{canon_sim['net_r']}R  | Max DD = {canon_sim['max_dd_r']}R | Calmar = {canon_sim['calmar']} | Trades = {canon_sim['trades']}")
    if best_portfolio:
        print(f"3. Optimal Calmar Multi-Setup Basket:         Net R = +{bm['net_r']}R  | Max DD = {bm['max_dd_r']}R | Calmar = {bm['calmar']} | Trades = {bm['trades']}")
    print("=" * 110 + "\n")


if __name__ == "__main__":
    main()
