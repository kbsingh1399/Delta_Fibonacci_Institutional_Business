#!/usr/bin/env python3
"""trailing_stop_optimizer.py - Walk-forward, regime-conditional exit
optimizer with a built-in deflation gate.

WHY THE GUARDRAILS (read before using)
A "Calmar maximizer" that searches exit parameters over the full sample and
reports the best in-sample result is a data-mining machine, not an
optimization: the repository's own history is the proof - the original grid
ran 11,613 trials, its top picks failed the 4.33 deflation floor, and the
whole-lake walk-forward (249 symbols, 14 Nov 2026) returned 0 PASS with the
median symbol's best-config profit decaying ~6x out-of-sample (+43R fit ->
+7.2R validate). Every new parameter search multiplies the number of trials
and therefore RAISES the bar a config must clear.

This module therefore:
  1. Fixes the ENTRY logic to the audited engine (entries are not a search
     dimension here; only exits and stop buffers are).
  2. Splits every trade by entry timestamp into FIT (<= 30 Sep 2026) and
     VALIDATE (>= 1 Oct 2026) with shared indicator warm-up - the same split
     as walk_forward_validator.py.
  3. Ranks configurations on the FIT window ONLY, then reports the selected
     configuration's VALIDATE statistics as the only performance numbers
     that matter.
  4. Computes the deflation threshold from the total trial count
     (symbols x 33 configs x 2 regimes + prior grid) and applies it to the
     validate t-stat. Default posture: NO PROMOTION unless the gate clears.

REGIME CONDITIONING
Each bar is tagged TREND / CHOP via Wilder ADX14 on the trading timeframe
(>= 25 -> TREND). Trades inherit the regime of their signal bar, and a
separate exit configuration is selected per regime - so trending markets can
run wider trails and choppy markets tighter targets, which is the point of
the request - but both selections face the same walk-forward gate.

GRID (33 configurations)
  exits: fixed RR 1.5 / 2.0 / 2.5 / 3.0 / 3.5 / 4.0, trail_55, trail_89,
         step_trail (engine ladder 2/0.5, 4/2, 6/EMA34),
         step_wide (3/1, 6/2, 9/EMA34),
         step_tight (2/0.5, 3/1.5, 5/3)
  stop ATR buffer: 0.25 / 0.30 / 0.35 x ATR14 (MID shelf; SLOW uses +0.05)

Usage:
    python3 trailing_stop_optimizer.py --data-dir ./data            # headline 7
    python3 trailing_stop_optimizer.py --data-dir ./data --symbols BTCUSD ETHUSD
    python3 trailing_stop_optimizer.py --data-dir ./data --json tso.json
    # whole lake: --all   (~1-2 minutes for the full 249-symbol data lake)

Dependencies: pandas, numpy.
"""

from __future__ import annotations

import argparse
import json
import math
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FIT_END = int(datetime(2026, 9, 30, 23, 59, 59, tzinfo=timezone.utc).timestamp())
VALIDATE_START = int(datetime(2026, 10, 1, 0, 0, 0, tzinfo=timezone.utc).timestamp())
SPREAD_COST_R = 0.04
WINNER_CAP_R = 15.0
ADX_PERIOD = 14
ADX_TREND_FLOOR = 25.0
MIN_FIT_TRADES = 5        # configs with fewer fit trades in a regime are not ranked
MIN_VALIDATE_TRADES = 5
PRIOR_GRID_TRIALS = 11_613   # the repository's existing search (master audit file)

DEFAULT_SYMBOLS = ["ARMBUSD", "MRVLBUSD", "HOODBUSD", "XAUTUSD", "PAXGUSD",
                   "BANKUSD", "GRAMUSD"]
DEFAULT_SPANS = (34, 89, 233)   # audited Fib_34_89 ribbon for entries

EXIT_GRID = [
    ("fixed_1.5", {"kind": "fixed", "rr": 1.5}),
    ("fixed_2.0", {"kind": "fixed", "rr": 2.0}),
    ("fixed_2.5", {"kind": "fixed", "rr": 2.5}),
    ("fixed_3.0", {"kind": "fixed", "rr": 3.0}),
    ("fixed_3.5", {"kind": "fixed", "rr": 3.5}),
    ("fixed_4.0", {"kind": "fixed", "rr": 4.0}),
    ("trail_55", {"kind": "trail_ema", "span": 55}),
    ("trail_89", {"kind": "trail_ema", "span": 89}),
    ("step_trail", {"kind": "step", "ladder": [(2.0, 0.5), (4.0, 2.0), (6.0, "ema34")]}),
    ("step_wide", {"kind": "step", "ladder": [(3.0, 1.0), (6.0, 2.0), (9.0, "ema34")]}),
    ("step_tight", {"kind": "step", "ladder": [(2.0, 0.5), (3.0, 1.5), (5.0, 3.0)]}),
]
STOP_MULTS = [0.25, 0.30, 0.35]


# ---------------------------------------------------------------------------
# Indicators
# ---------------------------------------------------------------------------

def ema(series: pd.Series, span: int) -> np.ndarray:
    return series.ewm(span=span, adjust=False).mean().to_numpy()


def atr14(highs, lows, closes) -> np.ndarray:
    tr = np.zeros(len(closes))
    tr[0] = highs[0] - lows[0]
    for i in range(1, len(closes)):
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]),
                    abs(lows[i] - closes[i - 1]))
    return pd.Series(tr).rolling(14).mean().bfill().to_numpy()


def adx14(highs, lows, closes, period: int = ADX_PERIOD) -> np.ndarray:
    """Wilder ADX. Returns ADX series (NaN-free via bfill)."""
    n = len(closes)
    plus_dm = np.zeros(n)
    minus_dm = np.zeros(n)
    tr = np.zeros(n)
    for i in range(1, n):
        up = highs[i] - highs[i - 1]
        dn = lows[i - 1] - lows[i]
        plus_dm[i] = up if (up > dn and up > 0) else 0.0
        minus_dm[i] = dn if (dn > up and dn > 0) else 0.0
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]),
                    abs(lows[i] - closes[i - 1]))
    # Wilder smoothing (modified moving average)
    def wilder(x: np.ndarray) -> np.ndarray:
        out = np.zeros(n)
        out[period] = x[1:period + 1].sum()
        for i in range(period + 1, n):
            out[i] = out[i - 1] - out[i - 1] / period + x[i]
        return out
    atr = wilder(tr)
    pdi = 100.0 * wilder(plus_dm) / np.where(atr <= 0, 1e-9, atr)
    mdi = 100.0 * wilder(minus_dm) / np.where(atr <= 0, 1e-9, atr)
    dx = 100.0 * np.abs(pdi - mdi) / np.where((pdi + mdi) <= 0, 1e-9, pdi + mdi)
    adx = pd.Series(dx).rolling(period).mean().bfill().to_numpy().copy()
    adx[:period] = adx[period] if n > period else 0.0
    return adx


# ---------------------------------------------------------------------------
# Parameterized simulator (audited entries; exits from the grid)
# ---------------------------------------------------------------------------

@dataclass
class OTrade:
    entry_ts: int
    exit_ts: int
    r: float
    side: int
    regime: str       # TREND / CHOP at the signal bar


def simulate(df: pd.DataFrame, spans: Tuple[int, int, int],
             exit_name: str, exit_cfg: dict, stop_mult: float) -> List[OTrade]:
    """Single-TF audited engine with parameterized exits and stop buffer.

    Entries are the audited rules: macro gate mid-vs-slow, shelves at the
    mid/fast EMA (0.2% tolerance) or the slow EMA, wick >= 25%, stop at the
    shelf extreme minus stop_mult*ATR (SLOW: min/max(EMA233, extreme) minus
    (stop_mult+0.05)*ATR), entry at next open. Single position; same-candle
    sweep re-entry allowed (faithful to the repository engine).
    """
    n = len(df)
    if n < 250:
        return []
    opens = df["open"].to_numpy(dtype=float)
    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    closes = df["close"].to_numpy(dtype=float)
    timestamps = df["timestamp"].to_numpy(dtype=np.int64)
    rng = highs - lows
    rng = np.where(rng <= 0, 1e-9, rng)
    u_w = (highs - np.maximum(opens, closes)) / rng
    l_w = (np.minimum(opens, closes) - lows) / rng
    atrs = atr14(highs, lows, closes)
    adx = adx14(highs, lows, closes)
    c = pd.Series(closes)
    emas = {s: ema(c, s) for s in {21, 34, 55, 89, 100, 144, 200, 233,
                                   spans[0], spans[1], spans[2]}}
    ef, em, es = emas[spans[0]], emas[spans[1]], emas[spans[2]]

    fast, mid, slow = spans
    start_idx = max(spans) + 10
    if start_idx >= n - 10:
        return []

    trades: List[OTrade] = []
    in_trade = False
    side = 0
    entry_p = sl_p = r_dist = 0.0
    max_fav = 0.0
    entry_ts = 0
    entry_regime = "CHOP"

    def exit_check(i: int, bh: float, bl: float, bc: float):
        """Returns (exited, exit_r) and updates sl_p for step exits."""
        nonlocal sl_p
        if side == -1:  # SHORT
            if exit_cfg["kind"] == "fixed":
                if bh >= sl_p:
                    return True, -1.0
                if bl <= entry_p - exit_cfg["rr"] * r_dist:
                    return True, exit_cfg["rr"]
            elif exit_cfg["kind"] == "trail_ema":
                if bc > emas[exit_cfg["span"]][i]:
                    return True, (entry_p - bc) / r_dist
                if bh >= sl_p:
                    return True, -1.0
            elif exit_cfg["kind"] == "step":
                for trigger, lock in exit_cfg["ladder"]:
                    if max_fav >= trigger:
                        new_sl = (emas[34][i] if lock == "ema34"
                                  else entry_p - lock * r_dist)
                        sl_p = min(sl_p, new_sl)
                if bh >= sl_p:
                    return True, (entry_p - sl_p) / r_dist
        else:           # LONG
            if exit_cfg["kind"] == "fixed":
                if bl <= sl_p:
                    return True, -1.0
                if bh >= entry_p + exit_cfg["rr"] * r_dist:
                    return True, exit_cfg["rr"]
            elif exit_cfg["kind"] == "trail_ema":
                if bc < emas[exit_cfg["span"]][i]:
                    return True, (bc - entry_p) / r_dist
                if bl <= sl_p:
                    return True, -1.0
            elif exit_cfg["kind"] == "step":
                for trigger, lock in exit_cfg["ladder"]:
                    if max_fav >= trigger:
                        new_sl = (emas[34][i] if lock == "ema34"
                                  else entry_p + lock * r_dist)
                        sl_p = max(sl_p, new_sl)
                if bl <= sl_p:
                    return True, (sl_p - entry_p) / r_dist
        return False, 0.0

    for i in range(start_idx, n - 1):
        if in_trade:
            bh, bl, bc = highs[i], lows[i], closes[i]
            cur = (entry_p - bl) / r_dist if side == -1 else (bh - entry_p) / r_dist
            if cur > max_fav:
                max_fav = cur
            exited, exit_r = exit_check(i, bh, bl, bc)
            if exited:
                trades.append(OTrade(entry_ts, int(timestamps[i]),
                                     min(exit_r, WINNER_CAP_R) - SPREAD_COST_R,
                                     side, entry_regime))
                in_trade = False
            else:
                continue
        else:
            cc, hi, lo, ai = closes[i], highs[i], lows[i], atrs[i]
            sig, shelf = 0, None
            if em[i] < es[i]:
                if u_w[i] >= 0.25:
                    if (hi >= em[i] * 0.998 or hi >= ef[i] * 0.998) and cc <= em[i]:
                        sig, shelf = -1, "MID"
                    elif hi >= es[i] * 0.998 and cc <= es[i]:
                        sig, shelf = -1, "SLOW"
            elif em[i] > es[i]:
                if l_w[i] >= 0.25:
                    if (lo <= em[i] * 1.002 or lo <= ef[i] * 1.002) and cc >= em[i]:
                        sig, shelf = 1, "MID"
                    elif lo <= es[i] * 1.002 and cc >= es[i]:
                        sig, shelf = 1, "SLOW"
            if sig == 0:
                continue
            next_i = i + 1
            if next_i >= n:
                break
            ep = opens[next_i]
            if shelf == "SLOW":
                sp = ((max(es[i], hi) + (stop_mult + 0.05) * ai) if sig == -1
                      else (min(es[i], lo) - (stop_mult + 0.05) * ai))
            else:
                sp = (hi + stop_mult * ai) if sig == -1 else (lo - stop_mult * ai)
            rd = abs(ep - sp)
            if rd <= 0:
                continue
            in_trade, side = True, sig
            entry_p, sl_p, r_dist = ep, sp, rd
            max_fav = 0.0
            entry_ts = int(timestamps[next_i])
            entry_regime = "TREND" if adx[i] >= ADX_TREND_FLOOR else "CHOP"
    return trades


# ---------------------------------------------------------------------------
# Walk-forward optimization
# ---------------------------------------------------------------------------

def _bucket_stats(trades: List[OTrade]) -> Optional[dict]:
    if not trades:
        return None
    arr = np.array([t.r for t in trades])
    tot = len(arr)
    net = float(np.sum(arr))
    cum = np.cumsum(arr)
    dd = float(np.max(np.maximum.accumulate(cum) - cum))
    var = float(np.var(arr, ddof=1)) if tot > 1 else 0.0
    se = math.sqrt(var / tot) if tot > 1 else float("nan")
    t = (net / tot) / se if se and se > 0 else float("nan")
    # quality ratio: Calmar's small-sample-stable cousin (their objective,
    # de-annualized - with < 1 year of data annualization is noise)
    qr = net / max(dd, 0.5)
    return {"trades": tot, "net_r": round(net, 2),
            "win_rate_pct": round(float(np.mean(arr > 0)) * 100, 1),
            "max_dd_r": round(dd, 2), "quality_ratio": round(qr, 2),
            "t_stat": round(t, 2) if t == t else None}


def optimize_symbol(symbol: str, data_dir: str, spans: Tuple[int, int, int],
                    fit_end: int, validate_start: int) -> List[dict]:
    """Grid-search exits per regime on FIT, report the selection's VALIDATE."""
    df = pd.read_csv(os.path.join(data_dir, f"{symbol}_4h.csv"))
    df = df.sort_values("timestamp").reset_index(drop=True)

    # one pass per grid config; bucket trades by (window, regime)
    grid_results = []
    for exit_name, exit_cfg in EXIT_GRID:
        for mult in STOP_MULTS:
            trades = simulate(df, spans, exit_name, exit_cfg, mult)
            if not trades:
                continue
            buckets = {}
            for window, lo, hi in (("fit", -1, fit_end), ("val", validate_start, 1 << 62)):
                for regime in ("TREND", "CHOP"):
                    sel = [t for t in trades
                           if lo <= t.entry_ts <= hi and t.regime == regime]
                    buckets[(window, regime)] = _bucket_stats(sel)
            grid_results.append({
                "exit": exit_name, "stop_mult": mult, "buckets": buckets})

    out = []
    for regime in ("TREND", "CHOP"):
        # rank on FIT only
        ranked = [g for g in grid_results
                  if (g["buckets"].get(("fit", regime)) or {}).get("trades", 0)
                  >= MIN_FIT_TRADES]
        if not ranked:
            out.append({"symbol": symbol, "regime": regime,
                        "verdict": "NO_FIT_SAMPLE",
                        "note": f"<{MIN_FIT_TRADES} fit trades in this regime"})
            continue
        best = max(ranked, key=lambda g: g["buckets"][("fit", regime)]["quality_ratio"])
        fit_s = best["buckets"][("fit", regime)]
        val_s = best["buckets"].get(("val", regime))
        out.append({
            "symbol": symbol, "regime": regime,
            "selected_exit": best["exit"], "stop_mult": best["stop_mult"],
            "fit": fit_s, "validate": val_s})
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data-dir", default="./data")
    ap.add_argument("--symbols", nargs="*", default=DEFAULT_SYMBOLS)
    ap.add_argument("--all", action="store_true",
                    help="run every symbol with a 4H file (long-running)")
    ap.add_argument("--spans", type=int, nargs=3, default=list(DEFAULT_SPANS),
                    help="entry ribbon fast mid slow (default 34 89 233)")
    ap.add_argument("--fit-end", type=int, default=FIT_END)
    ap.add_argument("--validate-start", type=int, default=VALIDATE_START)
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    symbols = args.symbols
    if args.all:
        symbols = sorted(os.path.basename(p)[:-7] for p in
                         glob_files(args.data_dir, "_4h.csv"))
    spans = tuple(args.spans)

    n_trials_this_run = len(symbols) * len(EXIT_GRID) * len(STOP_MULTS) * 2
    n_total = PRIOR_GRID_TRIALS + n_trials_this_run
    noise_t = math.sqrt(2.0 * math.log(n_total))

    print("Trailing-stop / RR optimizer - walk-forward, regime-conditional")
    print(f"entries fixed at ribbon {spans} | exits searched: {len(EXIT_GRID)} x "
          f"{len(STOP_MULTS)} stop buffers x 2 regimes = "
          f"{len(EXIT_GRID)*len(STOP_MULTS)*2} trials/symbol")
    print(f"trials this run: {n_trials_this_run:,} | total incl. prior grid "
          f"{n_total:,} -> deflation floor t >= {noise_t:.2f}")
    print(f"FIT <= {datetime.fromtimestamp(args.fit_end, tz=timezone.utc).date()} | "
          f"VALIDATE >= {datetime.fromtimestamp(args.validate_start, tz=timezone.utc).date()}"
          f"  (selection ranked on FIT; reported performance is VALIDATE)\n")

    rows: List[dict] = []
    for k, sym in enumerate(symbols, 1):
        path = os.path.join(args.data_dir, f"{sym}_4h.csv")
        if not os.path.exists(path):
            print(f"  {sym}: no 4H data, skipped")
            continue
        rows.extend(optimize_symbol(sym, args.data_dir, spans,
                                    args.fit_end, args.validate_start))
        print(f"  [{k}/{len(symbols)}] {sym} done", flush=True)

    print(f"\n{'symbol':12} {'regime':6} {'selected exit':13} {'mult':>5} | "
          f"{'FIT n':>5} {'netR':>7} {'QR':>5} | {'VAL n':>5} {'netR':>7} {'t':>5} | verdict")
    print("-" * 92)
    n_pass = 0
    for r in rows:
        if r.get("verdict") == "NO_FIT_SAMPLE":
            print(f"{r['symbol']:12} {r['regime']:6} {'-':13} {'-':>5} | "
                  f"{'-':>5} {'-':>7} {'-':>5} | {'-':>5} {'-':>7} {'-':>5} | "
                  f"NO_FIT_SAMPLE")
            continue
        v = r.get("validate")
        if v is None or v["trades"] < MIN_VALIDATE_TRADES:
            verdict = "INCONCLUSIVE"
        elif v["t_stat"] is not None and v["t_stat"] >= noise_t:
            verdict = "PASS"
            n_pass += 1
        else:
            verdict = "FAIL"
        r["verdict"] = verdict
        f = r["fit"]
        vj = v or {"trades": 0, "net_r": 0.0, "t_stat": None}
        print(f"{r['symbol']:12} {r['regime']:6} {r['selected_exit']:13} "
              f"{r['stop_mult']:>5.2f} | {f['trades']:>5} {f['net_r']:>7.2f} "
              f"{f['quality_ratio']:>5.2f} | {vj['trades']:>5} {vj['net_r']:>7.2f} "
              f"{str(vj['t_stat']):>5} | {verdict}")

    print(f"\n{len(rows)} symbol-regime cells | PASS: {n_pass} | floor {noise_t:.2f}.")
    print("PASS cells earn PAPER trading of that exit in that regime - nothing "
          "here earns live deployment on its own.")
    if n_pass == 0:
        print("No cell cleared the floor: the honest output of this search is "
              "'keep the audited exits', not a new parameter set.")

    if args.json:
        with open(args.json, "w") as f2:
            json.dump({"parameters": {"spans": list(spans), "fit_end": args.fit_end,
                                       "validate_start": args.validate_start,
                                       "n_trials_this_run": n_trials_this_run,
                                       "n_total_trials": n_total,
                                       "noise_t": round(noise_t, 2)},
                       "rows": rows}, f2, indent=1)
        print(f"results written to {args.json}")


def glob_files(data_dir: str, suffix: str) -> List[str]:
    return [os.path.join(data_dir, f) for f in os.listdir(data_dir)
            if f.endswith(suffix)]


if __name__ == "__main__":
    main()
