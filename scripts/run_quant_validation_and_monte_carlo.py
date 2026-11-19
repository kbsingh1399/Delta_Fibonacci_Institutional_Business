#!/usr/bin/env python3
"""scripts/run_quant_validation_and_monte_carlo.py:
Executes the Institutional Anti-Curve Fitting & Monte Carlo Validation Pipeline:
1. 70/30 In-Sample vs Out-of-Sample (Holdout) Temporal Validation.
2. 10,000 Numba-Accelerated Monte Carlo Bootstrap Simulations per candidate.
3. Deflated Sharpe Ratio (DSR) calculation adjusting for multiple-hypothesis testing.
4. Generates audit/INSTITUTIONAL_ROBUST_ALPHA_BASKET.csv and audit/MONTE_CARLO_RISK_AUDIT.md.
"""

import os
import sys
import json
import time
from typing import Dict, Any, List, Tuple
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
    simulate_with_signals,
    calculate_backtest_metrics
)
from engine.quant_robustness import (
    run_numba_monte_carlo,
    calculate_deflated_sharpe_ratio,
    split_train_test_data,
    verify_anti_curve_fit
)


def run_quant_anti_curve_fit_audit():
    # Detect champions source: prefer expanded, fallback to initial 23
    expanded_path = os.path.join(AUDIT_DIR, "expanded_multidim_champions.json")
    initial_path = os.path.join(AUDIT_DIR, "asset_multidim_champions.json")

    target_path = expanded_path if os.path.exists(expanded_path) else initial_path
    if not os.path.exists(target_path):
        print(f"❌ No champions JSON found at {target_path}", flush=True)
        return

    with open(target_path, "r") as f:
        champions_data = json.load(f)

    print("=" * 90, flush=True)
    print("🔬 QUANTITATIVE ANTI-CURVE FITTING & NUMBA MONTE CARLO AUDIT", flush=True)
    print(f"Source: {os.path.basename(target_path)} | Total Assets: {len(champions_data)}", flush=True)
    print("Methodology: 70% In-Sample / 30% Out-of-Sample + 10,000 Numba Monte Carlo Bootstrap", flush=True)
    print("=" * 90, flush=True)

    robust_results = []
    t0 = time.time()

    for sym, s_data in champions_data.items():
        timeframes = s_data.get("timeframes", {})
        is_crypto = any(k in sym.upper() for k in ["BTC", "ETH", "XLM", "XMR", "AVE", "ZEC", "YFI", "DSH"])
        anchor_mode = "utc" if is_crypto else "broker"

        for tf, champ in timeframes.items():
            pq_path = os.path.join(DATA_DIR, f"{sym}_{tf}.parquet")
            if not os.path.exists(pq_path):
                continue

            try:
                df = pd.read_parquet(pq_path)
            except Exception:
                continue

            if len(df) < 100:
                continue

            if not pd.api.types.is_datetime64_any_dtype(df["time"]):
                df["time"] = pd.to_datetime(df["time"], utc=True)
            df = df.sort_values("time").reset_index(drop=True)

            model_family = champ.get("model", "VWAP")
            param = champ.get("param", "")
            exit_mode = champ.get("exit_mode", "fixed_2.5r")

            # 1. Chronological 70% In-Sample / 30% Out-of-Sample Split
            train_df, test_df = split_train_test_data(df, train_ratio=0.70)

            # In-Sample Simulation
            is_trades, is_m = evaluate_dataset(train_df, model_family, param, exit_mode, anchor_mode, sym, tf)

            # Out-of-Sample (Holdout) Simulation
            oos_trades, oos_m = evaluate_dataset(test_df, model_family, param, exit_mode, anchor_mode, sym, tf)

            # Full Period Simulation for Monte Carlo
            full_trades, full_m = evaluate_dataset(df, model_family, param, exit_mode, anchor_mode, sym, tf)

            if len(full_trades) < 10:
                continue

            # 2. 10,000 Numba Monte Carlo Bootstrap Simulations
            r_mults = np.array([t["r_multiple"] for t in full_trades], dtype=float)
            mc = run_numba_monte_carlo(r_mults, n_sims=10000)

            # 3. Bailey & Lopez de Prado Deflated Sharpe Ratio (DSR)
            dsr = calculate_deflated_sharpe_ratio(r_mults, n_trials=40)

            # 4. Strict Anti-Curve Fitting Gate Evaluation
            gate = verify_anti_curve_fit(is_m, oos_m, mc, dsr)

            result_entry = {
                "symbol": sym,
                "tf": tf,
                "model": model_family,
                "param": param,
                "exit_mode": exit_mode,
                "verdict": gate["verdict"],
                "passes_all": gate["passes_all"],
                # Full Period
                "full_net_r": full_m["net_r"],
                "full_pf": full_m["profit_factor"],
                "full_wr": full_m["win_rate"],
                "full_trades": full_m["total_trades"],
                # In-Sample
                "is_net_r": is_m["net_r"],
                "is_pf": is_m["profit_factor"],
                "is_trades": is_m["total_trades"],
                # Out-of-Sample Holdout (Unseen)
                "oos_net_r": oos_m["net_r"],
                "oos_pf": oos_m["profit_factor"],
                "oos_trades": oos_m["total_trades"],
                "degradation_ratio": gate["degradation_ratio"],
                # Numba Monte Carlo 10,000
                "mc_median_r": mc["mc_median_r"],
                "mc_ci_5_r": mc["mc_ci_5_r"],
                "mc_p_profit": mc["mc_p_profit"],
                "mc_max_dd_95": mc["mc_max_dd_95"],
                "mc_max_dd_99": mc["mc_max_dd_99"],
                "mc_ruin_20r": mc["mc_risk_of_ruin_20r"],
                "deflated_sharpe": dsr,
            }
            robust_results.append(result_entry)

    elapsed = time.time() - t0
    print(f"\n✅ Completed Quant Audit in {elapsed:.2f}s across {len(robust_results)} setups", flush=True)

    if not robust_results:
        print("⚠️ No valid setups audited.", flush=True)
        return

    df_rob = pd.DataFrame(robust_results)

    # Save complete statistical CSV
    csv_path = os.path.join(AUDIT_DIR, "QUANT_ROBUSTNESS_COMPLETE.csv")
    df_rob.to_csv(csv_path, index=False)
    print(f"💾 Full Quant Audit CSV: {csv_path}", flush=True)

    # Filter strictly for INSTITUTIONAL_ALPHA and BORDERLINE_MONITOR
    passed_df = df_rob[df_rob["passes_all"] == True].sort_values(by=["oos_net_r", "full_net_r"], ascending=[False, False])
    robust_csv = os.path.join(AUDIT_DIR, "INSTITUTIONAL_ROBUST_ALPHA_BASKET.csv")
    passed_df.to_csv(robust_csv, index=False)
    print(f"🏆 Institutional Robust Alpha Basket ({len(passed_df)} Setups): {robust_csv}", flush=True)

    # Generate Detailed Markdown Report
    report_path = os.path.join(AUDIT_DIR, "MONTE_CARLO_RISK_AUDIT.md")
    generate_quant_markdown_report(df_rob, passed_df, report_path)
    print(f"📊 Detailed Monte Carlo Risk Audit Report: {report_path}", flush=True)


def evaluate_dataset(
    df: pd.DataFrame,
    model_family: str,
    param: str,
    exit_mode: str,
    anchor_mode: str,
    sym: str,
    tf: str
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    c = df["close"].to_numpy(dtype=float)
    h = df["high"].to_numpy(dtype=float)
    l = df["low"].to_numpy(dtype=float)
    o = df["open"].to_numpy(dtype=float)
    times = df["time"]

    atr = calculate_atr(h, l, c, 14)

    if model_family == "VWAP":
        vwap_df = calculate_session_vwap_bands(df, anchor_mode=anchor_mode)
        sig_sides, sig_setups, sig_rds = precompute_signals_vector(
            o=o, h=h, l=l, c=c, atr=atr, model_family="VWAP",
            vwap_vals=vwap_df["vwap"].to_numpy(dtype=float),
            vwap_p1=vwap_df["vwap_p1"].to_numpy(dtype=float),
            vwap_p2=vwap_df["vwap_p2"].to_numpy(dtype=float),
            vwap_m1=vwap_df["vwap_m1"].to_numpy(dtype=float),
            vwap_m2=vwap_df["vwap_m2"].to_numpy(dtype=float),
            start_bar=20
        )
    else:
        # Extract EMA period
        period = 50
        for p_cand in [20, 50, 100, 200]:
            if str(p_cand) in param:
                period = p_cand
                break
        ema_vals = calculate_ema(c, period)
        start_p = max(period + 10, 20)
        sig_sides, sig_setups, sig_rds = precompute_signals_vector(
            o=o, h=h, l=l, c=c, atr=atr, model_family="EMA",
            ema_vals=ema_vals, ema_period=period, start_bar=start_p
        )

    trades, m = simulate_with_signals(
        times=times, o=o, h=h, l=l, c=c, atr=atr,
        sig_sides=sig_sides, sig_setups=sig_setups,
        symbol=sym, tf=tf, model_family=model_family,
        exit_mode=exit_mode, risk_usd=5.0, start_bar=20, sig_rds=sig_rds
    )
    return trades, m


def generate_quant_markdown_report(df_all: pd.DataFrame, df_passed: pd.DataFrame, out_path: str):
    total_audited = len(df_all)
    passed_count = len(df_passed)
    pass_pct = round(passed_count / max(1, total_audited) * 100, 1)

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("# Institutional Quantitative Robustness & Monte Carlo Risk Audit\n\n")
        f.write(f"- **Generated**: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}\n")
        f.write(f"- **Anti-Curve Fitting Protocol**: Strict 70% In-Sample / 30% Out-of-Sample Temporal Split\n")
        f.write(f"- **Monte Carlo Engine**: 10,000 Numba-accelerated bootstrap resamples per setup\n")
        f.write(f"- **Selection Bias Defense**: Bailey & Lopez de Prado (2014) Deflated Sharpe Ratio\n")
        f.write(f"- **Total Candidate Setups Audited**: {total_audited}\n")
        f.write(f"- **Verified Institutional Champions (Passed All Gates)**: **{passed_count} ({pass_pct}%)**\n")
        f.write(f"- **Curve-Fit / Fragile Setups Purged**: **{total_audited - passed_count}**\n\n")

        f.write("## 1. Top Verified Institutional Champions (Zero Curve-Fit Basket)\n\n")
        f.write("| Symbol | TF | Model | Exit Rule | OOS Net R | OOS PF | Degradation | MC 95% DD | MC Ruin (20R) | DSR | Verdict |\n")
        f.write("| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |\n")

        for _, r in df_passed.head(35).iterrows():
            f.write(
                f"| `{r['symbol']}` | {r['tf']} | {r['model']} | `{r['exit_mode']}` | "
                f"**+{r['oos_net_r']:.1f}R** | **{r['oos_pf']:.2f}** | {r['degradation_ratio']:.2f} | "
                f"{r['mc_max_dd_95']:.1f}R | {r['mc_ruin_20r']*100:.2f}% | **{r['deflated_sharpe']:.2f}** | ✅ `{r['verdict']}` |\n"
            )

        f.write("\n## 2. Statistical Invariants & Quality Gate Hurdle\n\n")
        f.write("Every setup admitted to the live execution basket must satisfy all 4 institutional criteria:\n")
        f.write("1. **Out-of-Sample Profitability**: $Net\\ R_{OOS} > 0$ and $Profit\\ Factor_{OOS} \\ge 1.15$ on completely unseen chronological data.\n")
        f.write("2. **Degradation Hurdle**: $E[R]_{OOS} / E[R]_{IS} \\ge 0.35$ (strategy does not collapse after calibration).\n")
        f.write("3. **10,000 Monte Carlo Bootstrap**: $P(Profit) \\ge 90\\%$ and Risk of Ruin ($P(DD > 50R) \\le 10\\%$) across 10,000 randomized resamples.\n")
        f.write("4. **Deflated Sharpe Ratio**: $DSR \\ge 0.80$ under non-normal skewness/kurtosis, proving edge is not selection noise.\n")


if __name__ == "__main__":
    run_quant_anti_curve_fit_audit()
