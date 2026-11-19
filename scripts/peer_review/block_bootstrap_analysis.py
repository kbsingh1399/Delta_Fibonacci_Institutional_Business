#!/usr/bin/env python3
"""block_bootstrap_analysis.py: independent portfolio-level risk analysis.

1. Re-simulates the 25 committed champions (their anchor quirks) with trade
   timestamps; builds the TRUE time-ordered portfolio equity curve.
2. Reports portfolio Net R / PF / MaxDD on FULL and OOS (last-30% per symbol)
   periods -- the correct cross-asset computation (their reporting sums
   per-asset OOS R and never computes a portfolio DD).
3. IID (their engine, same seed) vs stationary block bootstrap (Politis-
   Romano, mean block 10/20) per champion and on the pooled portfolio series.
4. Cost sensitivity: per-trade cost in R units {0.01 (their assumption),
   0.05, 0.10, 0.20} applied to portfolio net R.
"""
import os
import sys
import json

import os as _os
_HERE = _os.path.dirname(_os.path.abspath(__file__))
REPO = _os.environ.get("PEER_REVIEW_REPO", _os.path.dirname(_os.path.dirname(_HERE)))
OUT_DIR = _os.path.join(REPO, "audit", "peer_review", "output")
_os.makedirs(OUT_DIR, exist_ok=True)
sys.path.insert(0, REPO)
sys.path.insert(0, OUT_DIR)

import numpy as np
import pandas as pd
import numba
from honest_oos_experiment import load, signals_for, run_exit, CRYPTO_QUANTBUG
from engine.quant_robustness import run_numba_monte_carlo


@numba.njit(fastmath=True)
def stationary_bootstrap_paths(r, n_sims, L, seed):
    np.random.seed(seed)
    n = len(r)
    net = np.zeros(n_sims)
    dds = np.zeros(n_sims)
    p = 1.0 / L
    for s in range(n_sims):
        cum = 0.0
        peak = 0.0
        mdd = 0.0
        idx = np.random.randint(0, n)
        for _ in range(n):
            if np.random.random() < p:
                idx = np.random.randint(0, n)
            else:
                idx += 1
                if idx >= n:
                    idx = 0
            cum += r[idx]
            if cum > peak:
                peak = cum
            dd = peak - cum
            if dd > mdd:
                mdd = dd
        net[s] = cum
        dds[s] = mdd
    return net, dds


def max_dd(series):
    cum = np.cumsum(series)
    peak = np.maximum.accumulate(np.concatenate([[0.0], cum]))[1:]
    return float(np.max(peak - cum)) if len(cum) else 0.0


def main():
    committed = pd.read_csv(os.path.join(REPO, "audit", "QUANT_ROBUSTNESS_COMPLETE.csv"))
    champs = committed[committed["passes_all"] == True]

    trades = []
    per_champ = []
    for _, r in champs.iterrows():
        sym, tf = r["symbol"], r["tf"]
        anchor = "utc" if any(k in sym.upper() for k in CRYPTO_QUANTBUG) else "broker"
        df = load(sym, tf)
        period = int(str(r["param"]).split("_")[1]) if r["model"] == "EMA" else None
        arr = signals_for(df, r["model"], period, anchor)
        bt_tr, m = run_exit(df, arr, r["model"], r["exit_mode"], sym, tf, arr[7])
        split_ts = df["time"].iloc[int(len(df) * 0.70)]
        for t in bt_tr:
            trades.append({"symbol": sym, "tf": tf, "exit_time": pd.Timestamp(t["exit_time"]),
                           "r": t["r_multiple"], "oos": pd.Timestamp(t["exit_time"]) >= split_ts})
        rl = np.array([t["r_multiple"] for t in bt_tr], dtype=float)
        iid = run_numba_monte_carlo(rl, n_sims=10000)
        blk10 = stationary_bootstrap_paths(rl, 10000, 10, 42)
        blk20 = stationary_bootstrap_paths(rl, 10000, 20, 42)
        dd95 = lambda d: round(float(np.percentile(d, 95)), 1)
        per_champ.append({
            "symbol": sym, "tf": tf, "exit": r["exit_mode"], "trades": len(rl), "net_r": round(float(rl.sum()), 1),
            "iid_pp": iid["mc_p_profit"], "iid_dd95": iid["mc_max_dd_95"], "iid_dd99": iid["mc_max_dd_99"],
            "blk10_pp": round(float(np.mean(blk10[0] > 0)), 4), "blk10_dd95": dd95(blk10[1]),
            "blk10_dd99": round(float(np.percentile(blk10[1], 99)), 1),
            "blk20_dd95": round(float(np.percentile(blk20[1], 95)), 1), "blk20_dd99": round(float(np.percentile(blk20[1], 99)), 1),
        })
        print(f"done {sym} {tf}", flush=True)

    tdf = pd.DataFrame(trades).sort_values("exit_time").reset_index(drop=True)
    full_r = tdf["r"].to_numpy(float)
    oos_r = tdf[tdf["oos"]]["r"].to_numpy(float)
    is_r = tdf[~tdf["oos"]]["r"].to_numpy(float)

    def pf(x):
        w = x[x > 0.05].sum()
        l = abs(x[x < -0.05].sum())
        return round(float(w / l), 2) if l > 0 else 99.0

    # pooled portfolio bootstrap (all 25 champions' trades interleaved as one series)
    port_iid = run_numba_monte_carlo(full_r, n_sims=10000)
    pb10 = stationary_bootstrap_paths(full_r, 10000, 10, 42)
    pb20 = stationary_bootstrap_paths(full_r, 10000, 20, 42)

    results = {
        "portfolio_full": {
            "trades": int(len(full_r)), "net_r": round(float(full_r.sum()), 1),
            "win_rate": round(float((full_r > 0.05).mean()) * 100, 1), "profit_factor": pf(full_r),
            "max_dd_r": round(max_dd(full_r), 1),
        },
        "portfolio_oos_true": {
            "trades": int(len(oos_r)), "net_r": round(float(oos_r.sum()), 1),
            "win_rate": round(float((oos_r > 0.05).mean()) * 100, 1) if len(oos_r) else 0,
            "profit_factor": pf(oos_r) if len(oos_r) else 0,
            "max_dd_r": round(max_dd(oos_r), 1) if len(oos_r) else 0,
        },
        "portfolio_is_true": {
            "trades": int(len(is_r)), "net_r": round(float(is_r.sum()), 1),
            "profit_factor": pf(is_r) if len(is_r) else 0,
        },
        "per_champion_bootstrap": per_champ,
        "portfolio_bootstrap": {
            "iid_pp": port_iid["mc_p_profit"], "iid_dd95": port_iid["mc_max_dd_95"], "iid_dd99": port_iid["mc_max_dd_99"],
            "blk10_pp": round(float(np.mean(pb10[0] > 0)), 4), "blk10_dd95": round(float(np.percentile(pb10[1], 95)), 1),
            "blk20_pp": round(float(np.mean(pb20[0] > 0)), 4), "blk20_dd95": round(float(np.percentile(pb20[1], 95)), 1),
        },
        "cost_sensitivity_net_r": {
            f"cost_{c:.2f}R": round(float(full_r.sum() - c * len(full_r)), 1)
            for c in [0.01, 0.05, 0.10, 0.20]
        },
        "cost_sensitivity_oos_net_r": {
            f"cost_{c:.2f}R": round(float(oos_r.sum() - c * len(oos_r)), 1)
            for c in [0.01, 0.05, 0.10, 0.20]
        },
    }
    json.dump(results, open(os.path.join(OUT_DIR, "risk_analysis.json"), "w"), indent=1)
    print(json.dumps({k: v for k, v in results.items() if k != "per_champion_bootstrap"}, indent=2))


if __name__ == "__main__":
    main()
