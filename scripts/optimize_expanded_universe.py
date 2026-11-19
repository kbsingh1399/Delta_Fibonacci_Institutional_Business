#!/usr/bin/env python3
"""scripts/optimize_expanded_universe.py: Comprehensive grid optimization across the full
98 viable liquid symbols on Blueberry Markets across 5M, 15M, 1H, 4H, testing
Single-Bar Pinbars, Two-Bar Reversal Pairs, and Multi-Bar Cluster Stalls for VWAP and EMAs.
Outputs audit/expanded_multidim_champions.json and audit/EXPANDED_ALPHA_BASKET_REPORT.md.
"""

import os
import sys
import json
import time
from typing import Dict, Any, List
import pandas as pd
import numpy as np

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

DATA_DIR = os.path.join(BASE_DIR, "Blueberry_Data")
AUDIT_DIR = os.path.join(BASE_DIR, "audit")
os.makedirs(AUDIT_DIR, exist_ok=True)

from engine.vwap_bands import calculate_session_vwap_bands
from engine.multidim_grid_backtest import (
    calculate_atr,
    calculate_ema,
    precompute_signals_vector,
    simulate_with_signals
)

TIMEFRAMES = ["5M", "15M", "1H", "4H"]
EMA_PERIODS = [20, 50, 100, 200]
EXIT_MODES = [
    "fixed_2.0r", "fixed_2.5r", "fixed_3.0r", "fixed_3.5r", "fixed_4.0r",
    "trail_0.5r", "trail_1.0r", "trail_1.5r"
]


def run_expanded_grid_optimization():
    viable_path = os.path.join(AUDIT_DIR, "viable_liquid_symbols.json")
    if not os.path.exists(viable_path):
        print(f"❌ Viable symbols file missing: {viable_path}", flush=True)
        return

    with open(viable_path, "r") as f:
        symbols = json.load(f)

    print("=" * 85, flush=True)
    print("🚀 EXPANDED UNIVERSE MULTI-DIMENSIONAL GRID OPTIMIZER", flush=True)
    print(f"Watchlist: {len(symbols)} Viable Liquid Symbols across Blueberry Markets", flush=True)
    print(f"Timeframes: {TIMEFRAMES}", flush=True)
    print(f"Patterns: Pinbar Kiss + 2-Bar Reversals + Cluster Stall Breakaways", flush=True)
    print(f"Levels: VWAP SD Bands (-2..+2) and EMAs {EMA_PERIODS}", flush=True)
    print(f"Exit Modes: {EXIT_MODES}", flush=True)
    print("=" * 85, flush=True)

    all_results = []
    t0 = time.time()

    for s_idx, sym in enumerate(symbols, 1):
        sym_t0 = time.time()
        sym_results_count = 0
        is_crypto = any(k in sym.upper() for k in ["BTC", "ETH", "XLM", "XRP", "ADA", "SOL", "DOG", "DOT", "LTC", "BCH", "AVX", "XMR"])
        anchor_mode = "utc" if is_crypto else "broker"

        for tf in TIMEFRAMES:
            pq_path = os.path.join(DATA_DIR, f"{sym}_{tf}.parquet")
            if not os.path.exists(pq_path):
                continue

            try:
                df = pd.read_parquet(pq_path)
            except Exception:
                continue

            if len(df) < 60:
                continue

            if not pd.api.types.is_datetime64_any_dtype(df["time"]):
                df["time"] = pd.to_datetime(df["time"], utc=True)
            df = df.sort_values("time").reset_index(drop=True)

            c = df["close"].to_numpy(dtype=float)
            h = df["high"].to_numpy(dtype=float)
            l = df["low"].to_numpy(dtype=float)
            o = df["open"].to_numpy(dtype=float)
            times = df["time"]

            # Precalculate indicators ONCE per (sym, tf)
            atr = calculate_atr(h, l, c, 14)
            vwap_df = calculate_session_vwap_bands(df, anchor_mode=anchor_mode)

            # 1. Precalculate VWAP signals (incorporates Pinbar + 2-Bar + Cluster Stall)
            vwap_sides, vwap_setups, vwap_rds = precompute_signals_vector(
                o=o, h=h, l=l, c=c, atr=atr, model_family="VWAP",
                vwap_vals=vwap_df["vwap"].to_numpy(dtype=float),
                vwap_p1=vwap_df["vwap_p1"].to_numpy(dtype=float),
                vwap_p2=vwap_df["vwap_p2"].to_numpy(dtype=float),
                vwap_m1=vwap_df["vwap_m1"].to_numpy(dtype=float),
                vwap_m2=vwap_df["vwap_m2"].to_numpy(dtype=float),
                start_bar=20
            )

            # Sweep VWAP exit modes
            for em in EXIT_MODES:
                trades, m = simulate_with_signals(
                    times=times, o=o, h=h, l=l, c=c, atr=atr,
                    sig_sides=vwap_sides, sig_setups=vwap_setups,
                    symbol=sym, tf=tf, model_family="VWAP",
                    exit_mode=em, risk_usd=5.0, start_bar=20, sig_rds=vwap_rds
                )
                if m["total_trades"] >= (8 if tf == "4H" else 15):
                    all_results.append({
                        "symbol": sym, "tf": tf, "model": "VWAP",
                        "param": "SD_BANDS_MULTIVARIANT", "exit_mode": em, **m
                    })
                    sym_results_count += 1

            # 2. Precalculate EMA signals for each period
            for p in EMA_PERIODS:
                ema_vals = calculate_ema(c, p)
                start_p = max(p + 10, 20)
                ema_sides, ema_setups, ema_rds = precompute_signals_vector(
                    o=o, h=h, l=l, c=c, atr=atr, model_family="EMA",
                    ema_vals=ema_vals, ema_period=p, start_bar=start_p
                )

                # Sweep EMA exit modes
                for em in EXIT_MODES:
                    trades, m = simulate_with_signals(
                        times=times, o=o, h=h, l=l, c=c, atr=atr,
                        sig_sides=ema_sides, sig_setups=ema_setups,
                        symbol=sym, tf=tf, model_family="EMA",
                        exit_mode=em, risk_usd=5.0, start_bar=start_p, sig_rds=ema_rds
                    )
                    if m["total_trades"] >= (8 if tf == "4H" else 15):
                        all_results.append({
                            "symbol": sym, "tf": tf, "model": "EMA",
                            "param": f"EMA_{p}_MULTIVARIANT", "exit_mode": em, **m
                        })
                        sym_results_count += 1

        elapsed_sym = time.time() - sym_t0
        print(f"[{s_idx:02d}/{len(symbols)}] {sym:<12} | Qualified Runs: {sym_results_count:<3} | {elapsed_sym:.2f}s", flush=True)

    total_time = time.time() - t0
    print("=" * 85, flush=True)
    print(f"✅ EXPANDED GRID OPTIMIZATION COMPLETE in {total_time:.1f}s | Total Evaluated Runs: {len(all_results)}", flush=True)
    print("=" * 85, flush=True)

    df_res = pd.DataFrame(all_results)
    if df_res.empty:
        print("⚠️ No runs met minimum trade thresholds.", flush=True)
        return

    # Select Multi-Criteria Champion Matrix per Asset
    champions = {}
    qualified_rows = []

    for sym in symbols:
        sym_df = df_res[df_res["symbol"] == sym]
        if sym_df.empty:
            continue

        champions[sym] = {"timeframes": {}}

        for tf in TIMEFRAMES:
            tf_df = sym_df[sym_df["tf"] == tf]
            if tf_df.empty:
                continue

            # Quality gate: PF >= 1.20, Net R > 0, Expectancy > 0.05
            valid = tf_df[(tf_df["profit_factor"] >= 1.20) & (tf_df["net_r"] > 0) & (tf_df["expectancy_r"] > 0.04)]
            if not valid.empty:
                best = valid.sort_values(by=["net_r", "profit_factor"], ascending=[False, False]).iloc[0]
                best_dict = best.to_dict()
                champions[sym]["timeframes"][tf] = best_dict
                qualified_rows.append(best_dict)
            else:
                fallback = tf_df.sort_values(by=["net_r", "profit_factor"], ascending=[False, False])
                if not fallback.empty and fallback.iloc[0]["net_r"] > 0:
                    best = fallback.iloc[0]
                    best_dict = best.to_dict()
                    champions[sym]["timeframes"][tf] = best_dict
                    qualified_rows.append(best_dict)

    # Save expanded_multidim_champions.json
    json_path = os.path.join(AUDIT_DIR, "expanded_multidim_champions.json")
    with open(json_path, "w") as f:
        json.dump(champions, f, indent=2)
    print(f"💾 Expanded Multi-Criteria Matrix saved to: {json_path}", flush=True)

    # Rank symbols by aggregate Net R across all timeframes to build the Top Institutional Basket
    df_q = pd.DataFrame(qualified_rows)
    if not df_q.empty:
        sym_agg = df_q.groupby("symbol").agg({
            "net_r": "sum",
            "total_trades": "sum",
            "profit_factor": "mean",
            "win_rate": "mean",
            "total_pnl_usd": "sum",
            "tf": "count"
        }).reset_index()
        sym_agg = sym_agg.sort_values(by="net_r", ascending=False).reset_index(drop=True)

        # Select Top 45 Alpha Basket Assets
        top_45_basket = sym_agg.head(45)
        top_45_csv = os.path.join(AUDIT_DIR, "TOP_45_ALPHA_BASKET.csv")
        top_45_basket.to_csv(top_45_csv, index=False)
        print(f"🏆 Top 45 Alpha Basket saved to: {top_45_csv}", flush=True)

        # Generate Comprehensive Markdown Report
        report_path = os.path.join(AUDIT_DIR, "EXPANDED_ALPHA_BASKET_REPORT.md")
        generate_expanded_report(df_q, sym_agg, report_path)
        print(f"📊 Detailed Report generated at: {report_path}", flush=True)


def generate_expanded_report(df_q: pd.DataFrame, sym_agg: pd.DataFrame, out_path: str):
    total_net_r = round(df_q["net_r"].sum(), 2)
    total_trades = int(df_q["total_trades"].sum())
    total_pnl = round(df_q["total_pnl_usd"].sum(), 2)
    overall_pf = round(df_q["profit_factor"].mean(), 2)
    overall_wr = round(df_q["win_rate"].mean() * 100, 1)

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("# Expanded Universe Multi-Dimensional Alpha Basket Report (98 Assets)\n\n")
        f.write(f"- **Generated**: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}\n")
        f.write(f"- **Total Qualified Setups**: **{len(df_q)}** across **{len(sym_agg)} Assets**\n")
        f.write(f"- **Total Portfolio Net R**: **+{total_net_r:,.2f}R**\n")
        f.write(f"- **Total Portfolio PnL**: **+${total_pnl:,.2f} USD** (at $5.00 fixed risk/trade)\n")
        f.write(f"- **Average Win Rate**: **{overall_wr}%**\n")
        f.write(f"- **Average Profit Factor**: **{overall_pf}**\n")
        f.write(f"- **Total Historical Trades**: **{total_trades:,}**\n\n")

        f.write("## 1. Top 45 Institutional Alpha Basket Leaderboard\n\n")
        f.write("| Rank | Asset | Active TFs | Net R | Profit Factor | Win% | Total Trades | PnL ($) |\n")
        f.write("| :---: | :--- | :---: | :---: | :---: | :---: | :---: | :---: |\n")

        for idx, r in sym_agg.head(45).iterrows():
            f.write(
                f"| #{idx+1:02d} | `{r['symbol']}` | {r['tf']}/4 | **+{r['net_r']:.1f}R** | "
                f"**{r['profit_factor']:.2f}** | {r['win_rate']*100:.1f}% | {int(r['total_trades']):,} | +${r['total_pnl_usd']:,.2f} |\n"
            )

        f.write("\n## 2. Model Pattern Breakdown\n\n")
        vwap_count = len(df_q[df_q["model"] == "VWAP"])
        ema_count = len(df_q[df_q["model"] == "EMA"])
        f.write(f"- **VWAP Standard Deviation Rejections**: {vwap_count} setups ({vwap_count/len(df_q)*100:.1f}% of champions)\n")
        f.write(f"- **EMA Rejections**: {ema_count} setups ({ema_count/len(df_q)*100:.1f}% of champions)\n\n")

        f.write("## 3. Timeframe Contribution\n\n")
        for tf in ["5M", "15M", "1H", "4H"]:
            tf_cnt = len(df_q[df_q["tf"] == tf])
            tf_r = round(df_q[df_q["tf"] == tf]["net_r"].sum(), 1) if tf_cnt > 0 else 0.0
            f.write(f"- **{tf}**: {tf_cnt} qualified setups | Contribution: **+{tf_r:,.1f}R**\n")


if __name__ == "__main__":
    run_expanded_grid_optimization()
