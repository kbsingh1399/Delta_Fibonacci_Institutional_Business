#!/usr/bin/env python3
"""adjudicate_gemini_claims.py: empirical adjudication of the cross-reviewer's
novel claims against the execution-verified record.

A. "Post-hoc trailing ratchet creates phantom-profit fills" (CRITICAL claim):
   builds a CORRECTED fill model -- gap-aware fills at the next-bar open,
   intrabar trail-crossing exits at end of bar, ratcheted stop tested on
   adverse touches that follow the favourable touch -- re-simulates the 25
   committed champions, and reports per-champion and portfolio deltas vs the
   engine as committed.
B. "Dead second clause in detect_rejection_wick": algebraic proof + incidence
   on real data (how many bullish-pinbar firings have a bearish close and a
   dominant upper wick, i.e. would be rejected by the intended close-position
   filter).
C. Committed-CSV reconciliation numbers (TF table, 8/25 negative at 0.10R,
   17/25 with ruin20 >= 50%, medians, zero-DSR counts).
D. DSR/MC input series (full-sample vs OOS-only) used by the validator.
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
from honest_oos_experiment import load, signals_for, CRYPTO_QUANTBUG
from engine.candle_rejection import detect_rejection_wick

# ---------------- A. corrected fill model ----------------
def simulate_corrected(times, o, h, l, c, sig_sides, sig_setups, symbol, tf,
                       model_family, exit_mode="fixed_2.0r", risk_usd=5.0,
                       cooldown_bars=6, start_bar=20, sig_rds=None):
    n = len(c)
    commission_usd = 0.05
    trades = []
    in_pos = False; pos_side = 0; pos_entry = 0.0; pos_sl = 0.0; pos_tp = 0.0
    pos_rd = 1.0; pos_mfe = 0.0; entry_idx = 0; cooldown_until_idx = 0

    is_trail = "trail" in exit_mode
    if is_trail:
        trail_step = 0.5 if "0.5" in exit_mode else (1.0 if "1.0" in exit_mode else 1.5)
        target_rr = 6.0 if ("0.5" in exit_mode or "1.0" in exit_mode) else 8.0
    else:
        trail_step = 0.0
        target_rr = float(exit_mode.replace("fixed_", "").replace("r", "")) if "fixed_" in exit_mode else 2.5

    def ratchet(side, mfe):
        """Returns the stop level the trail would hold given current MFE."""
        sl = pos_sl
        if is_trail and mfe >= 1.0:
            be = pos_entry + (0.05 * pos_rd if side == 1 else -0.05 * pos_rd)
            if (side == 1 and be > sl) or (side == -1 and be < sl):
                sl = be
        if is_trail and mfe >= 1.5:
            tl = pos_entry + ((mfe - trail_step) * pos_rd if side == 1 else -(mfe - trail_step) * pos_rd)
            if (side == 1 and tl > sl) or (side == -1 and tl < sl):
                sl = tl
        return sl

    for i in range(start_bar, n - 1):
        if in_pos:
            bar_h, bar_l, bar_o = h[i], l[i], o[i]
            bullish = c[i] >= o[i]
            exited = False; exit_p = 0.0; exit_reason = ""
            fav_done = False  # has the favourable extreme been processed this bar?

            # P2: gap-aware fill at the open
            if pos_side == 1:
                if bar_o <= pos_sl:
                    exited, exit_p, exit_reason = True, bar_o, "GAP_SL"
                elif pos_tp > 0 and bar_o >= pos_tp:
                    exited, exit_p, exit_reason = True, bar_o, "GAP_TP"
            else:
                if bar_o >= pos_sl:
                    exited, exit_p, exit_reason = True, bar_o, "GAP_SL"
                elif pos_tp > 0 and bar_o <= pos_tp:
                    exited, exit_p, exit_reason = True, bar_o, "GAP_TP"

            if not exited:
                seq = [("LOW", bar_l), ("HIGH", bar_h)] if bullish else [("HIGH", bar_h), ("LOW", bar_l)]
                for touch_type, touch_price in seq:
                    if pos_side == 1:
                        if touch_type == "LOW":
                            # adverse touch: if the favourable extreme already printed
                            # this bar, the ratcheted stop is active for the fall
                            eff_sl = ratchet(1, pos_mfe) if fav_done else pos_sl
                            if touch_price <= eff_sl:
                                exited, exit_p = True, eff_sl
                                exit_reason = "SL_HIT" if eff_sl < pos_entry else "TRAIL_HIT"
                                break
                        else:
                            curr_r = (touch_price - pos_entry) / pos_rd
                            if curr_r > pos_mfe:
                                pos_mfe = curr_r
                            fav_done = True
                            if pos_tp > 0 and touch_price >= pos_tp:
                                exited, exit_p, exit_reason = True, pos_tp, "TP_HIT"
                                break
                    else:
                        if touch_type == "HIGH":
                            eff_sl = ratchet(-1, pos_mfe) if fav_done else pos_sl
                            if touch_price >= eff_sl:
                                exited, exit_p = True, eff_sl
                                exit_reason = "SL_HIT" if eff_sl > pos_entry else "TRAIL_HIT"
                                break
                        else:
                            curr_r = (pos_entry - touch_price) / pos_rd
                            if curr_r > pos_mfe:
                                pos_mfe = curr_r
                            fav_done = True
                            if pos_tp > 0 and touch_price <= pos_tp:
                                exited, exit_p, exit_reason = True, pos_tp, "TP_HIT"
                                break

            # P1: intrabar trail-crossing -- close beyond the freshly ratcheted stop
            if not exited and is_trail:
                new_sl = ratchet(pos_side, pos_mfe)
                if (pos_side == 1 and c[i] <= new_sl) or (pos_side == -1 and c[i] >= new_sl):
                    exited, exit_p = True, new_sl
                    exit_reason = "SL_HIT" if ((pos_side == 1 and new_sl < pos_entry) or (pos_side == -1 and new_sl > pos_entry)) else "TRAIL_HIT"

            if not exited:
                pos_sl = ratchet(pos_side, pos_mfe) if is_trail else pos_sl

            if exited:
                in_pos = False
                cooldown_until_idx = i + cooldown_bars
                exit_r = (exit_p - pos_entry) / pos_rd if pos_side == 1 else (pos_entry - exit_p) / pos_rd
                pnl = (exit_r * risk_usd) - commission_usd
                trades.append({
                    "symbol": symbol, "tf": tf, "model": model_family,
                    "side": "BUY" if pos_side == 1 else "SELL",
                    "entry_time": str(times[entry_idx]), "exit_time": str(times[i]),
                    "entry_price": round(pos_entry, 5), "exit_price": round(exit_p, 5),
                    "risk_dist": round(pos_rd, 5), "exit_reason": exit_reason,
                    "r_multiple": round(exit_r, 2), "pnl_usd": round(pnl, 2),
                    "bars_held": i - entry_idx,
                })
                continue

        if in_pos or i < cooldown_until_idx:
            continue
        sig_side = sig_sides[i]
        if sig_side != 0:
            next_bar = i + 1
            if next_bar >= n:
                break
            pos_entry = o[next_bar]; entry_idx = next_bar; pos_side = int(sig_side)
            rd = max(1e-5, sig_rds[i]) if (sig_rds is not None and sig_rds[i] > 0) else max(1e-5, 1.5 * 1.0)
            pos_rd = rd
            pos_sl = (pos_entry - rd) if pos_side == 1 else (pos_entry + rd)
            pos_tp = (pos_entry + target_rr * rd) if pos_side == 1 else (pos_entry - target_rr * rd)
            pos_mfe = 0.0; in_pos = True

    total_r = round(sum(t["r_multiple"] for t in trades), 2)
    return trades, total_r


def main():
    committed = pd.read_csv(os.path.join(REPO, "audit", "QUANT_ROBUSTNESS_COMPLETE.csv"))
    champs = committed[committed["passes_all"] == True]
    stored = json.load(open(os.path.join(OUT_DIR, "committed_trades.json")))

    rows, tot_engine, tot_corr = [], 0.0, 0.0
    for _, r in champs.iterrows():
        sym, tf = r["symbol"], r["tf"]
        anchor = "utc" if any(k in sym.upper() for k in CRYPTO_QUANTBUG) else "broker"
        df = load(sym, tf)
        period = int(str(r["param"]).split("_")[1]) if r["model"] == "EMA" else None
        o, h, l, c, atr, sig, param, start = signals_for(df, r["model"], period, anchor)
        sides, setups, rds = sig
        tr_corr, tot = simulate_corrected(
            df["time"], o, h, l, c, sides, setups, sym, tf, r["model"],
            exit_mode=r["exit_mode"], start_bar=start, sig_rds=rds)
        key = f"{sym}_{tf}"
        eng = stored[key]
        eng_r = eng["reproduced_full_net_r"]
        rows.append({
            "cell": key, "exit": r["exit_mode"], "trades_engine": eng["reproduced_trades"],
            "trades_corrected": len(tr_corr), "net_r_engine": eng_r, "net_r_corrected": tot,
            "delta": round(tot - eng_r, 1),
        })
        tot_engine += eng_r; tot_corr += tot
        print(f"{key:<20} {r['exit_mode']:<12} engine {eng_r:>8.1f}  corrected {tot:>8.1f}  delta {tot-eng_r:>+7.1f}", flush=True)

    print(f"\nPORTFOLIO: engine {tot_engine:.1f}R | corrected {tot_corr:.1f}R | delta {tot_corr-tot_engine:+.1f}R "
          f"({(tot_corr-tot_engine)/tot_engine*100:+.1f}%)")
    json.dump({"rows": rows, "portfolio_engine": round(tot_engine, 1),
               "portfolio_corrected": round(tot_corr, 1)},
              open(os.path.join(OUT_DIR, "fill_model_adjudication.json"), "w"), indent=1)

    # ---------------- B. pinbar dead clause ----------------
    print("\n=== B. detect_rejection_wick dead-clause check ===")
    o_, h_, l_, c_ = 100.0, 136.0, 96.4, 101.0  # bearish close, 36% lower wick, ~60% upper wick
    isb, isbear, lw, uw = detect_rejection_wick(o_, h_, l_, c_)
    print(f"synthetic bearish-close candle (close 101, low 96.4, high 136): is_bullish={isb} "
          f"(lower_wick={lw:.2f}, upper_wick={uw:.2f}) -> dead-clause claim:", "CONFIRMED" if isb else "refuted")
    for sym_tf in ["USWTI.p_5M", "BTCUSD.pi_15M", "EURUSD.pi_15M", "XAUUSD.pi_1H"]:
        try:
            df = load(*sym_tf.split("_"))
        except Exception:
            continue
        oo = df["open"].to_numpy(float); hh = df["high"].to_numpy(float)
        ll = df["low"].to_numpy(float); cc = df["close"].to_numpy(float)
        n_bull = n_bad = 0
        for k in range(len(cc)):
            b, s, lw_, uw_ = detect_rejection_wick(oo[k], hh[k], ll[k], cc[k])
            if b:
                n_bull += 1
                if cc[k] < oo[k] and uw_ > lw_:
                    n_bad += 1
        print(f"{sym_tf:<18} bullish-pinbar firings: {n_bull:,} | bearish-close w/ dominant upper wick: "
              f"{n_bad:,} ({100*n_bad/max(1,n_bull):.1f}%)")

    # ---------------- C. committed-CSV reconciliation ----------------
    print("\n=== C. CSV reconciliation (Gemini table) ===")
    b = champs.copy()
    b["expectancy"] = b["full_net_r"] / b["full_trades"]
    g = b.groupby("tf").agg(setups=("symbol", "size"), full_r=("full_net_r", "sum"),
                            oos_r=("oos_net_r", "sum"), exp=("expectancy", "mean"),
                            dd95=("mc_max_dd_95", "mean"), dsr0=("deflated_sharpe", lambda s: int((s == 0).sum())))
    print(g.round(2).to_string())
    neg01 = b[b["full_net_r"] - 0.10 * b["full_trades"] < 0]
    print(f"\nchampions negative at 0.10R/trade: {len(neg01)}/25 (Gemini claims 8)")
    print(f"ruin20 >= 50%: {(b['mc_ruin_20r'] >= 0.5).sum()}/25 (Gemini claims 17)")
    print(f"median OOS PF: {b['oos_pf'].median():.2f} | median MC DD95: {b['mc_max_dd_95'].median():.2f}R")
    print(f"DD95 > 50R: {(b['mc_max_dd_95'] > 50).sum()}/25 (Gemini claims 8) | max {b['mc_max_dd_95'].max():.2f}R")
    print(f"degradation > 2x: {(b['degradation_ratio'] > 2).sum()}/25 (Gemini claims 10)")

    # ---------------- D. validator DSR/MC input ----------------
    print("\n=== D. validator DSR/MC input series ===")
    src = open(os.path.join(REPO, "scripts", "run_quant_validation_and_monte_carlo.py")).read()
    import re
    for m in re.finditer(r"(calculate_deflated_sharpe_ratio|run_numba_monte_carlo)\((.*?)\)", src, re.S):
        arg = " ".join(m.group(2).split())[:80]
        print(f"{m.group(1)}({arg})")


if __name__ == "__main__":
    main()
