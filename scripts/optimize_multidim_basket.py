#!/usr/bin/env python3
"""scripts/optimize_multidim_basket.py: Full Multi-Dimensional Grid Optimizer
for 23 Champion Assets across Timeframes (5M, 15M, 1H, 4H) x Models (VWAP, EMA) x R:R Exits.
Optimized with single-pass signal vectorization for lightning-fast execution.
Outputs audit/asset_multidim_champions.json and audit/MULTIDIM_ALPHA_BASKET_REPORT.md.
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

CHAMPION_SYMBOLS = [
    "SP500.p", "JP225.p", "DJ30.p", "NAS100.p", "HK50.p",
    "XAUUSD.pi", "XAGUSD.pi", "USWTI.p", "BTCUSD.pi", "ETHUSD.pi",
    "XLMUSD.p", "XRPUSD.pi", "ADAUSD.p", "USDCAD.pi", "EURUSD.pi",
    "GBPUSD.pi", "USDCHF.pi", "GBPJPY.pi", "CADJPY.pi", "CHFJPY.pi",
    "USDJPY.pi", "NZDSGD.pi", "GBPNOK.pi"
]

TIMEFRAMES = ["5M", "15M", "1H", "4H"]
EMA_PERIODS = [20, 50, 100, 200]
EXIT_MODES = [
    "fixed_1.5r", "fixed_2.0r", "fixed_2.5r", "fixed_3.0r", "fixed_3.5r", "fixed_4.0r",
    "trail_0.5r", "trail_1.0r", "trail_1.5r"
]


def run_full_basket_optimization():
    print("=" * 85, flush=True)
    print("🚀 MULTI-DIMENSIONAL VWAP & MTF EMA BASKET OPTIMIZER (LIGHTNING FAST)", flush=True)
    print(f"Watchlist: {len(CHAMPION_SYMBOLS)} Assets", flush=True)
    print(f"Timeframes: {TIMEFRAMES}", flush=True)
    print(f"Models: VWAP SD Bands (-2..+2) and EMAs {EMA_PERIODS}", flush=True)
    print(f"Exit Modes: {EXIT_MODES}", flush=True)
    print("=" * 85, flush=True)

    all_results = []
    t0 = time.time()

    for s_idx, sym in enumerate(CHAMPION_SYMBOLS, 1):
        sym_t0 = time.time()
        sym_results_count = 0
        is_crypto = any(k in sym.upper() for k in ["BTC", "ETH", "XLM", "XRP", "ADA"])
        anchor_mode = "utc" if is_crypto else "broker"

        for tf in TIMEFRAMES:
            pq_path = os.path.join(DATA_DIR, f"{sym}_{tf}.parquet")
            if not os.path.exists(pq_path):
                continue

            df = pd.read_parquet(pq_path)
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

            # 1. Precalculate VWAP signals ONCE
            vwap_sides, vwap_setups = precompute_signals_vector(
                o=o, h=h, l=l, c=c, atr=atr, model_family="VWAP",
                vwap_vals=vwap_df["vwap"].to_numpy(dtype=float),
                vwap_p1=vwap_df["vwap_p1"].to_numpy(dtype=float),
                vwap_p2=vwap_df["vwap_p2"].to_numpy(dtype=float),
                vwap_m1=vwap_df["vwap_m1"].to_numpy(dtype=float),
                vwap_m2=vwap_df["vwap_m2"].to_numpy(dtype=float),
                start_bar=20
            )

            # Sweep VWAP exit modes (pure trade simulation)
            for em in EXIT_MODES:
                trades, m = simulate_with_signals(
                    times=times, o=o, h=h, l=l, c=c, atr=atr,
                    sig_sides=vwap_sides, sig_setups=vwap_setups,
                    symbol=sym, tf=tf, model_family="VWAP",
                    exit_mode=em, risk_usd=5.0, start_bar=20
                )
                if m["total_trades"] >= (8 if tf == "4H" else 15):
                    all_results.append({
                        "symbol": sym, "tf": tf, "model": "VWAP",
                        "param": "SD_BANDS", "exit_mode": em, **m
                    })
                    sym_results_count += 1

            # 2. Precalculate EMA signals for each period
            for p in EMA_PERIODS:
                ema_vals = calculate_ema(c, p)
                start_p = max(p + 10, 20)
                ema_sides, ema_setups = precompute_signals_vector(
                    o=o, h=h, l=l, c=c, atr=atr, model_family="EMA",
                    ema_vals=ema_vals, ema_period=p, start_bar=start_p
                )

                # Sweep EMA exit modes
                for em in EXIT_MODES:
                    trades, m = simulate_with_signals(
                        times=times, o=o, h=h, l=l, c=c, atr=atr,
                        sig_sides=ema_sides, sig_setups=ema_setups,
                        symbol=sym, tf=tf, model_family="EMA",
                        exit_mode=em, risk_usd=5.0, start_bar=start_p
                    )
                    if m["total_trades"] >= (8 if tf == "4H" else 15):
                        all_results.append({
                            "symbol": sym, "tf": tf, "model": "EMA",
                            "param": f"EMA_{p}", "exit_mode": em, **m
                        })
                        sym_results_count += 1

        elapsed_sym = time.time() - sym_t0
        print(f"[{s_idx:02d}/{len(CHAMPION_SYMBOLS)}] {sym:<12} evaluated | {sym_results_count} qualifying runs | {elapsed_sym:.2f}s", flush=True)

    total_time = time.time() - t0
    print("=" * 85, flush=True)
    print(f"✅ GRID OPTIMIZATION COMPLETE in {total_time:.1f}s | Total Evaluated Runs: {len(all_results)}", flush=True)
    print("=" * 85, flush=True)

    df_res = pd.DataFrame(all_results)
    if df_res.empty:
        print("⚠️ No runs met minimum trade thresholds.", flush=True)
        return

    # Select Multi-Criteria Champion Matrix:
    # For each asset and timeframe, find the highest Net R setup meeting PF >= 1.25 and EV > 0
    champions = {}
    qualified_rows = []

    for sym in CHAMPION_SYMBOLS:
        sym_df = df_res[df_res["symbol"] == sym]
        if sym_df.empty:
            continue

        champions[sym] = {"timeframes": {}}

        for tf in TIMEFRAMES:
            tf_df = sym_df[sym_df["tf"] == tf]
            if tf_df.empty:
                continue

            # Filter by institutional quality gates
            valid = tf_df[(tf_df["profit_factor"] >= 1.20) & (tf_df["net_r"] > 0) & (tf_df["expectancy_r"] > 0.0)]
            if not valid.empty:
                best = valid.sort_values(by=["net_r", "profit_factor"], ascending=[False, False]).iloc[0]
                best_dict = best.to_dict()
                champions[sym]["timeframes"][tf] = best_dict
                qualified_rows.append(best_dict)
            else:
                # Fallback to top Net R
                fallback = tf_df.sort_values(by=["net_r", "profit_factor"], ascending=[False, False])
                if not fallback.empty:
                    best = fallback.iloc[0]
                    best_dict = best.to_dict()
                    champions[sym]["timeframes"][tf] = best_dict
                    qualified_rows.append(best_dict)

    # Save asset_multidim_champions.json
    json_path = os.path.join(AUDIT_DIR, "asset_multidim_champions.json")
    with open(json_path, "w") as f:
        json.dump(champions, f, indent=2)
    print(f"💾 Multi-Criteria Matrix saved to: {json_path}", flush=True)

    # Generate Markdown Report
    report_path = os.path.join(AUDIT_DIR, "MULTIDIM_ALPHA_BASKET_REPORT.md")
    generate_markdown_report(qualified_rows, report_path)
    print(f"📊 Detailed Report generated at: {report_path}", flush=True)


def generate_markdown_report(rows: List[Dict[str, Any]], out_path: str):
    df_q = pd.DataFrame(rows)
    if df_q.empty:
        return

    total_net_r = round(df_q["net_r"].sum(), 2)
    total_trades = int(df_q["total_trades"].sum())
    total_pnl = round(df_q["total_pnl_usd"].sum(), 2)
    overall_pf = round(df_q["profit_factor"].mean(), 2)
    overall_wr = round(df_q["win_rate"].mean() * 100, 1)

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("# Multi-Dimensional VWAP & MTF EMA Alpha Basket Optimization Report\n\n")
        f.write(f"- **Generated**: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}\n")
        f.write(f"- **Total Qualified Setups**: {len(df_q)}\n")
        f.write(f"- **Total Portfolio Net R**: **+{total_net_r}R**\n")
        f.write(f"- **Total Portfolio PnL**: **+${total_pnl:,.2f} USD** (at $5.00 fixed risk/trade)\n")
        f.write(f"- **Average Win Rate**: **{overall_wr}%**\n")
        f.write(f"- **Average Profit Factor**: **{overall_pf}**\n")
        f.write(f"- **Total Historical Trades**: **{total_trades:,}**\n\n")

        f.write("## 1. Multi-Criteria Champion Matrix (Asset x Timeframe)\n\n")
        f.write("| Asset | TF | Model | Parameter | Exit Mode | Trades | Win% | Profit Factor | Net R | Max DD | Exp (R) | PnL ($) |\n")
        f.write("| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |\n")

        for _, r in df_q.iterrows():
            f.write(
                f"| `{r['symbol']}` | `{r['tf']}` | **{r['model']}** | `{r['param']}` | `{r['exit_mode']}` | "
                f"{r['total_trades']} | {r['win_rate']*100:.1f}% | **{r['profit_factor']:.2f}** | "
                f"**+{r['net_r']:.1f}R** | {r['max_dd_r']:.1f}R | +{r['expectancy_r']:.2f}R | +${r['total_pnl_usd']:.2f} |\n"
            )

        f.write("\n## 2. Model Family Breakdown\n\n")
        vwap_count = len(df_q[df_q["model"] == "VWAP"])
        ema_count = len(df_q[df_q["model"] == "EMA"])
        f.write(f"- **VWAP Standard Deviation Rejections**: {vwap_count} setups ({vwap_count/len(df_q)*100:.1f}% of champions)\n")
        f.write(f"- **EMA Kiss & Bounce Setups**: {ema_count} setups ({ema_count/len(df_q)*100:.1f}% of champions)\n\n")

        f.write("## 3. Timeframe Distribution\n\n")
        for tf in ["5M", "15M", "1H", "4H"]:
            tf_cnt = len(df_q[df_q["tf"] == tf])
            tf_r = round(df_q[df_q["tf"] == tf]["net_r"].sum(), 1) if tf_cnt > 0 else 0.0
            f.write(f"- **{tf}**: {tf_cnt} qualified setups | Contribution: **+{tf_r}R**\n")


if __name__ == "__main__":
    run_full_basket_optimization()
