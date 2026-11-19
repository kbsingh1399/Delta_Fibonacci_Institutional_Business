#!/usr/bin/env python3
"""honest_oos_experiment.py: independent peer-review backtest.

Protocol A (their pipeline, as committed): champions selected by FULL-sample
net_r (optimize_multidim_basket.py), then a 70/30 "OOS" split of the same
data (run_quant_validation_and_monte_carlo.py) -> selection leakage.

Protocol B (honest): identical grid (VWAP + EMA{20,50,100,200} x 9 exit
modes), identical per-cell gates (PF>=1.20, net_r>0, expectancy>0, sort
net_r, min trades 8/4H else 15), but selection runs on the TRAIN 70% only;
the selected combo is evaluated ONCE on the TEST 30%.

Also reproduces each committed champion's full-period trades (their anchor
quirks) and re-simulates them under the LIVE runner's exit mapping
(trail_* and fixed_2.0r -> fixed 2.5R TP) to quantify live/backtest parity.

Outputs: honest_results.json, committed_trades.json (peer_review dir).
"""
import os
import sys
import json
import time

import os as _os
_HERE = _os.path.dirname(_os.path.abspath(__file__))
REPO = _os.environ.get("PEER_REVIEW_REPO", _os.path.dirname(_os.path.dirname(_HERE)))
OUT_DIR = _os.path.join(REPO, "audit", "peer_review", "output")
_os.makedirs(OUT_DIR, exist_ok=True)
sys.path.insert(0, REPO)

import numpy as np
import pandas as pd
from engine.vwap_bands import calculate_session_vwap_bands
from engine.multidim_grid_backtest import (
    calculate_atr, calculate_ema, precompute_signals_vector, simulate_with_signals,
)
from engine.quant_robustness import (
    run_numba_monte_carlo, calculate_deflated_sharpe_ratio,
    split_train_test_data, verify_anti_curve_fit,
)

DATA_DIR = os.path.join(REPO, "Blueberry_Data")

CHAMPION_SYMBOLS = [
    "SP500.p", "JP225.p", "DJ30.p", "NAS100.p", "HK50.p",
    "XAUUSD.pi", "XAGUSD.pi", "USWTI.p", "BTCUSD.pi", "ETHUSD.pi",
    "XLMUSD.p", "XRPUSD.pi", "ADAUSD.p", "USDCAD.pi", "EURUSD.pi",
    "GBPUSD.pi", "USDCHF.pi", "GBPJPY.pi", "CADJPY.pi", "CHFJPY.pi",
    "USDJPY.pi", "NZDSGD.pi", "GBPNOK.pi",
]
TIMEFRAMES = ["5M", "15M", "1H", "4H"]
EMA_PERIODS = [20, 50, 100, 200]
EXIT_MODES = [
    "fixed_1.5r", "fixed_2.0r", "fixed_2.5r", "fixed_3.0r", "fixed_3.5r", "fixed_4.0r",
    "trail_0.5r", "trail_1.0r", "trail_1.5r",
]
CRYPTO_CORRECT = ["BTC", "ETH", "XLM", "XRP", "ADA"]          # optimizer list
CRYPTO_QUANTBUG = ["BTC", "ETH", "XLM", "XMR", "AVE", "ZEC", "YFI", "DSH"]  # quant-script list


def load(sym, tf):
    df = pd.read_parquet(os.path.join(DATA_DIR, f"{sym}_{tf}.parquet"))
    if not pd.api.types.is_datetime64_any_dtype(df["time"]):
        df["time"] = pd.to_datetime(df["time"], utc=True)
    return df.sort_values("time").reset_index(drop=True)


def signals_for(df, model, period, anchor):
    """Precompute signal arrays on a df slice, mirroring the optimizer script."""
    c = df["close"].to_numpy(float); h = df["high"].to_numpy(float)
    l = df["low"].to_numpy(float); o = df["open"].to_numpy(float)
    atr = calculate_atr(h, l, c, 14)
    if model == "VWAP":
        v = calculate_session_vwap_bands(df, anchor_mode=anchor)
        start = 20
        sig = precompute_signals_vector(
            o=o, h=h, l=l, c=c, atr=atr, model_family="VWAP",
            vwap_vals=v["vwap"].to_numpy(float), vwap_p1=v["vwap_p1"].to_numpy(float),
            vwap_p2=v["vwap_p2"].to_numpy(float), vwap_m1=v["vwap_m1"].to_numpy(float),
            vwap_m2=v["vwap_m2"].to_numpy(float), start_bar=start)
        param = "SD_BANDS"
    else:
        start = max(period + 10, 20)
        ema = calculate_ema(c, period)
        sig = precompute_signals_vector(
            o=o, h=h, l=l, c=c, atr=atr, model_family="EMA",
            ema_vals=ema, ema_period=period, start_bar=start)
        param = f"EMA_{period}"
    return o, h, l, c, atr, sig, param, start


def run_exit(df, arrays, model, exit_mode, sym, tf, start):
    o, h, l, c, atr, (sides, setups, rds), param, _ = arrays
    trades, m = simulate_with_signals(
        times=df["time"], o=o, h=h, l=l, c=c, atr=atr,
        sig_sides=sides, sig_setups=setups, symbol=sym, tf=tf,
        model_family=model, exit_mode=exit_mode, risk_usd=5.0,
        start_bar=start, sig_rds=rds)
    return trades, m


def main():
    committed = pd.read_csv(os.path.join(REPO, "audit", "QUANT_ROBUSTNESS_COMPLETE.csv"))
    committed_champs = committed[committed["passes_all"] == True]
    comm_by_cell = {(r["symbol"], r["tf"]): r for _, r in committed_champs.iterrows()}

    honest_cells, committed_dump, parity_rows = [], {}, []
    t0 = time.time()

    for s_idx, sym in enumerate(CHAMPION_SYMBOLS, 1):
        crypto_correct = any(k in sym.upper() for k in CRYPTO_CORRECT)
        anchor = "utc" if crypto_correct else "broker"
        # committed champions were validated with the quant-script's buggy list
        anchor_repro = "utc" if any(k in sym.upper() for k in CRYPTO_QUANTBUG) else "broker"

        for tf in TIMEFRAMES:
            path = os.path.join(DATA_DIR, f"{sym}_{tf}.parquet")
            if not os.path.exists(path):
                continue
            df = load(sym, tf)
            if len(df) < 60:
                continue
            train_df, test_df = split_train_test_data(df, train_ratio=0.70)
            min_trades = 8 if tf == "4H" else 15

            # ---- Protocol B: grid selection on TRAIN only
            grid = []
            for model, period in [("VWAP", None)] + [("EMA", p) for p in EMA_PERIODS]:
                tr_arrays = signals_for(train_df, model, period, anchor)
                for em in EXIT_MODES:
                    _, m = run_exit(train_df, tr_arrays, model, em, sym, tf, tr_arrays[7])
                    if m["total_trades"] >= min_trades:
                        grid.append({"model": model, "period": period, "param": tr_arrays[6],
                                     "exit_mode": em, **m})
            gdf = pd.DataFrame(grid)
            sel = None
            if not gdf.empty:
                valid = gdf[(gdf["profit_factor"] >= 1.20) & (gdf["net_r"] > 0) & (gdf["expectancy_r"] > 0)]
                pool = valid if not valid.empty else gdf
                sel = pool.sort_values(by=["net_r", "profit_factor"], ascending=[False, False]).iloc[0]

            cell_out = {"symbol": sym, "tf": tf, "train_bars": len(train_df), "test_bars": len(test_df)}
            if sel is not None:
                sel_model = sel["model"]
                sel_period = int(str(sel["param"]).split("_")[1]) if sel_model == "EMA" else None
                te_arrays = signals_for(test_df, sel_model, sel_period, anchor)
                te_trades, te_m = run_exit(test_df, te_arrays, sel_model, sel["exit_mode"], sym, tf, te_arrays[7])
                fu_arrays = signals_for(df, sel_model, sel_period, anchor)
                fu_trades, fu_m = run_exit(df, fu_arrays, sel_model, sel["exit_mode"], sym, tf, fu_arrays[7])
                r_full = np.array([t["r_multiple"] for t in fu_trades], dtype=float)
                mc = run_numba_monte_carlo(r_full, n_sims=10000) if len(r_full) >= 5 else None
                dsr = calculate_deflated_sharpe_ratio(r_full, n_trials=40) if len(r_full) >= 10 else 0.0
                # IS metrics from the train grid row
                is_m = {"net_r": float(sel["net_r"]), "total_trades": int(sel["total_trades"])}
                gate = verify_anti_curve_fit(is_m, te_m, mc or {"mc_p_profit": 0, "mc_max_dd_95": 999}, dsr) if mc else {"verdict": "NO_TRADES", "passes_all": False}
                cell_out.update({
                    "honest_combo": f"{sel['param']}/{sel['exit_mode']}",
                    "honest_is_net_r": float(sel["net_r"]),
                    "honest_test_net_r": te_m["net_r"], "honest_test_pf": te_m["profit_factor"],
                    "honest_test_trades": te_m["total_trades"],
                    "honest_full_net_r": fu_m["net_r"], "honest_full_trades": fu_m["total_trades"],
                    "honest_verdict": gate["verdict"], "honest_passes": bool(gate["passes_all"]),
                    "honest_test_trades_list": [t["r_multiple"] for t in te_trades],
                    "honest_full_trades_list": r_full.tolist(),
                })
            honest_cells.append(cell_out)

            # ---- Reproduce committed champion + live-exit parity
            comm = comm_by_cell.get((sym, tf))
            if comm is not None:
                model = comm["model"]
                period = None
                if model == "EMA":
                    period = int(str(comm["param"]).split("_")[1])
                arr = signals_for(df, model, period, anchor_repro)
                bt_tr, bt_m = run_exit(df, arr, model, comm["exit_mode"], sym, tf, arr[7])
                live_exit = comm["exit_mode"]
                if "trail" in live_exit or "2.0" in live_exit:
                    live_exit = "fixed_2.5r"
                lv_tr, lv_m = run_exit(df, arr, model, live_exit, sym, tf, arr[7])
                committed_dump[f"{sym}_{tf}"] = {
                    "model": model, "param": comm["param"], "exit_mode": comm["exit_mode"],
                    "committed_full_net_r": float(comm["full_net_r"]),
                    "reproduced_full_net_r": bt_m["net_r"], "reproduced_trades": bt_m["total_trades"],
                    "committed_oos_net_r": float(comm["oos_net_r"]),
                    "live_exit": live_exit, "live_full_net_r": lv_m["net_r"],
                    "live_trades": lv_m["total_trades"], "live_pf": lv_m["profit_factor"],
                    "r_list": [t["r_multiple"] for t in bt_tr],
                    "r_list_live": [t["r_multiple"] for t in lv_tr],
                }
            print(f"[{s_idx:02d}/23] {sym:<12} {tf} done | {time.time()-t0:6.0f}s", flush=True)

    # ---- Portfolio comparison
    comm_oos_total = committed_champs["oos_net_r"].sum()
    h_pass = [c for c in honest_cells if c.get("honest_passes")]
    h_test_total = sum(c["honest_test_net_r"] for c in honest_cells if "honest_test_net_r" in c)
    h_pass_total = sum(c["honest_test_net_r"] for c in h_pass)
    summary = {
        "committed_portfolio": {
            "champions": int(len(committed_champs)), "oos_net_r_total": round(float(comm_oos_total), 1),
            "full_net_r_total": round(float(committed_champs["full_net_r"].sum()), 1),
        },
        "honest_portfolio": {
            "cells": len(honest_cells),
            "cells_with_trades": sum(1 for c in honest_cells if "honest_test_net_r" in c),
            "oos_positive_cells": sum(1 for c in honest_cells if c.get("honest_test_net_r", 0) > 0),
            "gates_passed": len(h_pass), "test_net_r_all_cells": round(float(h_test_total), 1),
            "test_net_r_passed_cells": round(float(h_pass_total), 1),
        },
        "parity": {
            "champions_reproduced_exact": sum(1 for d in committed_dump.values()
                                              if abs(d["reproduced_full_net_r"] - d["committed_full_net_r"]) < 0.51),
            "backtest_net_r_total": round(sum(d["reproduced_full_net_r"] for d in committed_dump.values()), 1),
            "live_exit_net_r_total": round(sum(d["live_full_net_r"] for d in committed_dump.values()), 1),
        },
    }
    json.dump({"summary": summary, "cells": honest_cells},
              open(os.path.join(OUT_DIR, "honest_results.json"), "w"), indent=1)
    json.dump(committed_dump, open(os.path.join(OUT_DIR, "committed_trades.json"), "w"))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
