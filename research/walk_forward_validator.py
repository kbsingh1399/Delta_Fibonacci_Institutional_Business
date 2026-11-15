#!/usr/bin/env python3
"""walk_forward_validator.py (v2) - Walk-forward validation with a
multiple-testing (deflation) screen, now with whole-lake mode.

v2 additions over the delivered v1 (QR-2026-1114 remediation item 3):
  * --all: validates the ENTIRE data lake, not just the seven headline
    configs. For every symbol it takes that symbol's best configuration from
    the master audit file (the deployment candidate a leaderboard-driven
    pipeline would pick), re-simulates it, and splits trades by entry
    timestamp into FIT (<= 30 Sep 2026) and VALIDATE (>= 1 Oct 2026).
  * Auto-computed noise floor: the deflation threshold is
    sqrt(2 ln N) with N = total grid trials (11,613 in the master file)
    plus any extra trials you have since run (--extra-trials, e.g. for a
    trailing-stop search). The audited N already covers all 249 symbols -
    the v2 "242-asset basket" is a subset of the already-searched lake, so
    the floor does NOT reset when the basket widens; it only rises.
  * Promotion registry: symbols that clear the screen out-of-sample are
    written to a registry file for PAPER trading only. An empty registry is
    the honest outcome and must be reported as such, never padded.

WHAT WAS ALREADY VERIFIED (v1, 14 Nov 2026)
  The engine re-implementations are bit-faithful to the repository (commit
  cb0fd35): the FULL column reproduces the audited headline rows exactly
  (ARMB 42/42.9%/3.23/+55.66R ... GRAM 33/63.6%/4.29/+41.07R), and the
  first walk-forward run returned 0 of 7 PASS (five FAIL, two INCONCLUSIVE).

VERDICTS
  PASS         validate t >= noise floor and >= 5 validate trades
  INCONCLUSIVE fewer than 5 validate trades (45-day window - the honest
               verdict for most symbols; do not read as pass)
  FAIL         >= 5 validate trades but t < noise floor

ENGINE FIDELITY NOTES (verified against the repository)
  - Single-TF: macro gate is mid-vs-slow EMA only (no Close-vs-EMA233 term -
    that condition exists only in the MTF regime gate); shelves tolerate
    0.2% around the EMAs; wick >= 25%; stops MID = extreme +/- 0.30*ATR14,
    SLOW = min/max(EMA233, extreme) +/- 0.35*ATR14; entry at next open;
    single position; same-candle sweep re-entry possible.
  - MTF: 4H regime (EMA89 vs EMA233 AND close vs EMA233, closed candles,
    backward asof-merge) AND 1H alignment; exit consumes the candle (no
    same-candle re-entry); start index = max span + 20.
  - Both: same-candle stop-and-target resolves as a LOSS; winners capped
    +15R; flat 0.04R cost; step_trail locks +0.5R/+2R/EMA34 at +2R/+4R/+6R
    MFE; trail_55 trails EMA55 (single-TF) / fast EMA (MTF).

Usage:
    python3 walk_forward_validator.py --data-dir ./data            # headline 7
    python3 walk_forward_validator.py --data-dir ./data --all      # whole lake
    python3 walk_forward_validator.py --data-dir ./data --all \
        --master audit/delta_full_master_audit.csv --extra-trials 0 \
        --registry promotion_registry.json --json wf_all.json

Data layout: {SYMBOL}_4h.csv / {SYMBOL}_1h.csv with columns
timestamp,datetime,open,high,low,close,volume.

Dependencies: pandas, numpy.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Policy constants (audit QR-2026-1114 / re-evaluation QR-2026-1114-R1)
# ---------------------------------------------------------------------------

FIT_END = int(datetime(2026, 9, 30, 23, 59, 59, tzinfo=timezone.utc).timestamp())
VALIDATE_START = int(datetime(2026, 10, 1, 0, 0, 0, tzinfo=timezone.utc).timestamp())
MIN_VALIDATE_TRADES = 5
SPREAD_COST_R = 0.04
WINNER_CAP_R = 15.0
EMA_SPANS = [21, 34, 50, 55, 89, 100, 144, 200, 233, 377]
RIBBON_SPANS = {
    "Fib_89_233": (55, 89, 233),
    "Fib_34_89": (34, 89, 233),
    "Fib_34_89_233": (34, 89, 233),     # MTF section label for 34/89/233
    "Fib_21_55": (21, 55, 144),
    "Classic_50_200": (50, 100, 200),
}

HEADLINE_CONFIGS = [
    # symbol, model, timeframe, exit_mode, (fast, mid, slow)
    ("ARMBUSD", "MTF", "4H_1H", "step_trail", (34, 89, 233)),
    ("MRVLBUSD", "MTF", "4H_1H", "step_trail", (34, 89, 233)),
    ("HOODBUSD", "SINGLE", "4H", "step_trail", (50, 100, 200)),
    ("XAUTUSD", "SINGLE", "4H", "fixed_3.0", (55, 89, 233)),
    ("PAXGUSD", "SINGLE", "4H", "fixed_2.5", (50, 100, 200)),
    ("BANKUSD", "SINGLE", "4H", "trail_55", (34, 89, 233)),
    ("GRAMUSD", "SINGLE", "1H", "step_trail", (55, 89, 233)),
]

MODEL_MAP = {"MTF_CONFLUENCE": "MTF", "SINGLE_TF": "SINGLE"}


# ---------------------------------------------------------------------------
# Shared indicator helpers (identical to the repository engines)
# ---------------------------------------------------------------------------

def ema(series: pd.Series, span: int) -> np.ndarray:
    return series.ewm(span=span, adjust=False).mean().to_numpy()


def atr14(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray) -> np.ndarray:
    tr = np.zeros(len(closes))
    tr[0] = highs[0] - lows[0]
    for i in range(1, len(closes)):
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]),
                    abs(lows[i] - closes[i - 1]))
    return pd.Series(tr).rolling(14).mean().bfill().to_numpy()


def wick_fractions(opens, highs, lows, closes):
    rng = highs - lows
    rng = np.where(rng <= 0, 1e-9, rng)
    body_top = np.maximum(opens, closes)
    body_bot = np.minimum(opens, closes)
    return (highs - body_top) / rng, (body_bot - lows) / rng


@dataclass
class Trade:
    entry_ts: int
    exit_ts: int
    r: float
    side: int
    shelf: str


def _stats(trades: List[Trade], start_ts: int, end_ts: int) -> Optional[dict]:
    if not trades:
        return None
    arr = np.array([t.r for t in trades])
    wins = int(np.sum(arr > 0))
    tot = len(arr)
    gw = float(np.sum(arr[arr > 0]))
    gl = abs(float(np.sum(arr[arr < 0])))
    pf = gw / gl if gl > 0 else 999.0
    net = float(np.sum(arr))
    avg = net / tot
    cum = np.cumsum(arr)
    peak = np.maximum.accumulate(cum)
    max_dd = float(np.max(peak - cum))
    var = float(np.var(arr, ddof=1)) if tot > 1 else 0.0
    se = math.sqrt(var / tot) if tot > 1 else float("nan")
    t = avg / se if se and se > 0 else float("nan")
    days = max(1.0, (end_ts - start_ts) / 86400.0)
    return {
        "trades": tot, "win_rate_pct": round(wins / tot * 100, 1),
        "profit_factor": round(pf, 2), "net_r": round(net, 2),
        "avg_r": round(avg, 3), "max_dd_r": round(max_dd, 2),
        "t_stat": round(t, 2) if t == t else None, "days": round(days, 1),
    }


# ---------------------------------------------------------------------------
# Single-timeframe engine (faithful to delta_master_calmar_optimizer.py)
# ---------------------------------------------------------------------------

def simulate_single_tf(df: pd.DataFrame, fast_p: int, mid_p: int, slow_p: int,
                       exit_mode: str,
                       spread_cost_r: float = SPREAD_COST_R) -> List[Trade]:
    n = len(df)
    if n < 250:
        return []
    opens = df["open"].to_numpy(dtype=float)
    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    closes = df["close"].to_numpy(dtype=float)
    timestamps = df["timestamp"].to_numpy(dtype=np.int64)
    u_w, l_w = wick_fractions(opens, highs, lows, closes)
    atrs = atr14(highs, lows, closes)
    c = pd.Series(closes)
    emas = {s: ema(c, s) for s in set(EMA_SPANS + [fast_p, mid_p, slow_p])}
    ef, em, es = emas[fast_p], emas[mid_p], emas[slow_p]

    is_fixed = exit_mode.startswith("fixed_")
    fixed_rr = float(exit_mode.split("_")[1]) if is_fixed else 0.0
    start_idx = max(fast_p, mid_p, slow_p) + 10
    if start_idx >= n - 10:
        return []

    trades: List[Trade] = []
    in_trade = False
    side = 0
    entry_p = sl_p = r_dist = 0.0
    max_fav = 0.0
    entry_ts = 0

    for i in range(start_idx, n - 1):
        if in_trade:
            bh, bl, bc = highs[i], lows[i], closes[i]
            cur = (entry_p - bl) / r_dist if side == -1 else (bh - entry_p) / r_dist
            if cur > max_fav:
                max_fav = cur
            exited, exit_r = False, 0.0

            if side == -1:  # SHORT
                if is_fixed:
                    if bh >= sl_p:
                        exited, exit_r = True, -1.0
                    elif bl <= entry_p - fixed_rr * r_dist:
                        exited, exit_r = True, fixed_rr
                elif exit_mode == "step_trail":
                    if max_fav >= 6.0:
                        sl_p = min(sl_p, emas[34][i])
                    elif max_fav >= 4.0:
                        sl_p = min(sl_p, entry_p - 2.0 * r_dist)
                    elif max_fav >= 2.0:
                        sl_p = min(sl_p, entry_p - 0.5 * r_dist)
                    if bh >= sl_p:
                        exited, exit_r = True, (entry_p - sl_p) / r_dist
                elif exit_mode == "trail_89":
                    if bc > em[i]:
                        exited, exit_r = True, (entry_p - bc) / r_dist
                    elif bh >= sl_p:
                        exited, exit_r = True, -1.0
                elif exit_mode == "trail_55":
                    if bc > emas[55][i]:
                        exited, exit_r = True, (entry_p - bc) / r_dist
                    elif bh >= sl_p:
                        exited, exit_r = True, -1.0
            else:           # LONG
                if is_fixed:
                    if bl <= sl_p:
                        exited, exit_r = True, -1.0
                    elif bh >= entry_p + fixed_rr * r_dist:
                        exited, exit_r = True, fixed_rr
                elif exit_mode == "step_trail":
                    if max_fav >= 6.0:
                        sl_p = max(sl_p, emas[34][i])
                    elif max_fav >= 4.0:
                        sl_p = max(sl_p, entry_p + 2.0 * r_dist)
                    elif max_fav >= 2.0:
                        sl_p = max(sl_p, entry_p + 0.5 * r_dist)
                    if bl <= sl_p:
                        exited, exit_r = True, (sl_p - entry_p) / r_dist
                elif exit_mode == "trail_89":
                    if bc < em[i]:
                        exited, exit_r = True, (bc - entry_p) / r_dist
                    elif bl <= sl_p:
                        exited, exit_r = True, -1.0
                elif exit_mode == "trail_55":
                    if bc < emas[55][i]:
                        exited, exit_r = True, (bc - entry_p) / r_dist
                    elif bl <= sl_p:
                        exited, exit_r = True, -1.0

            if exited:
                trades.append(Trade(timestamps[i], timestamps[i],
                                    min(exit_r, WINNER_CAP_R) - spread_cost_r,
                                    side, ""))
                trades[-1].entry_ts = entry_ts
                in_trade = False
                # single-TF engine falls through to entry detection on the
                # SAME candle (sweep re-entry) - faithful to the repo.
            else:
                continue

        # ---- entry detection on candle i ----
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
            sp = (max(es[i], hi) + 0.35 * ai) if sig == -1 else (min(es[i], lo) - 0.35 * ai)
        else:
            sp = (hi + 0.30 * ai) if sig == -1 else (lo - 0.30 * ai)
        rd = abs(ep - sp)
        if rd <= 0:
            continue
        in_trade, side = True, sig
        entry_p, sl_p, r_dist = ep, sp, rd
        max_fav = 0.0
        entry_ts = int(timestamps[next_i])
    return trades


# ---------------------------------------------------------------------------
# MTF confluence engine (faithful to mtf_confluence_optimizer.py)
# ---------------------------------------------------------------------------

def simulate_mtf(df_4h: pd.DataFrame, df_1h: pd.DataFrame, fast_p: int,
                 mid_p: int, slow_p: int, exit_mode: str,
                 spread_cost_r: float = SPREAD_COST_R) -> List[Trade]:
    if len(df_4h) < 150 or len(df_1h) < 300:
        return []
    df_4h = df_4h.sort_values("timestamp").reset_index(drop=True)
    df_1h = df_1h.sort_values("timestamp").reset_index(drop=True)

    c4 = df_4h["close"].astype(float)
    e89_4h, e233_4h = ema(c4, 89), ema(c4, 233)
    regime = np.zeros(len(df_4h), dtype=int)
    for k in range(len(df_4h)):
        if e89_4h[k] > e233_4h[k] and c4.iloc[k] > e233_4h[k]:
            regime[k] = 1
        elif e89_4h[k] < e233_4h[k] and c4.iloc[k] < e233_4h[k]:
            regime[k] = -1
    macro = pd.DataFrame({
        "timestamp": df_4h["timestamp"].to_numpy(dtype=np.int64) + 14400,
        "macro_regime": regime})
    merged = pd.merge_asof(df_1h, macro, on="timestamp", direction="backward")
    merged["macro_regime"] = merged["macro_regime"].fillna(0).astype(int)

    opens = merged["open"].to_numpy(dtype=float)
    highs = merged["high"].to_numpy(dtype=float)
    lows = merged["low"].to_numpy(dtype=float)
    closes = merged["close"].to_numpy(dtype=float)
    timestamps = merged["timestamp"].to_numpy(dtype=np.int64)
    macros = merged["macro_regime"].to_numpy(dtype=int)
    n = len(closes)
    u_w, l_w = wick_fractions(opens, highs, lows, closes)
    atrs = atr14(highs, lows, closes)
    c = pd.Series(closes)
    emas = {s: ema(c, s) for s in set(EMA_SPANS + [fast_p, mid_p, slow_p])}
    ef, em, es = emas[fast_p], emas[mid_p], emas[slow_p]

    is_fixed = exit_mode.startswith("fixed_")
    fixed_rr = float(exit_mode.split("_")[1]) if is_fixed else 0.0
    start_idx = max(fast_p, mid_p, slow_p) + 20
    if start_idx >= n - 10:
        return []

    trades: List[Trade] = []
    in_trade = False
    side = 0
    entry_p = sl_p = r_dist = 0.0
    max_fav = 0.0
    entry_ts = 0

    for i in range(start_idx, n - 1):
        if in_trade:
            bh, bl, bc = highs[i], lows[i], closes[i]
            cur = (entry_p - bl) / r_dist if side == -1 else (bh - entry_p) / r_dist
            if cur > max_fav:
                max_fav = cur
            exited, exit_r = False, 0.0
            if side == -1:  # SHORT
                if is_fixed:
                    if bh >= sl_p:
                        exited, exit_r = True, -1.0
                    elif bl <= entry_p - fixed_rr * r_dist:
                        exited, exit_r = True, fixed_rr
                elif exit_mode == "step_trail":
                    if max_fav >= 6.0:
                        sl_p = min(sl_p, ef[i])
                    elif max_fav >= 4.0:
                        sl_p = min(sl_p, entry_p - 2.0 * r_dist)
                    elif max_fav >= 2.0:
                        sl_p = min(sl_p, entry_p - 0.5 * r_dist)
                    if bh >= sl_p:
                        exited, exit_r = True, (entry_p - sl_p) / r_dist
                elif exit_mode == "trail_89":
                    if bc > em[i]:
                        exited, exit_r = True, (entry_p - bc) / r_dist
                    elif bh >= sl_p:
                        exited, exit_r = True, -1.0
                elif exit_mode == "trail_55":
                    if bc > ef[i]:
                        exited, exit_r = True, (entry_p - bc) / r_dist
                    elif bh >= sl_p:
                        exited, exit_r = True, -1.0
            else:           # LONG
                if is_fixed:
                    if bl <= sl_p:
                        exited, exit_r = True, -1.0
                    elif bh >= entry_p + fixed_rr * r_dist:
                        exited, exit_r = True, fixed_rr
                elif exit_mode == "step_trail":
                    if max_fav >= 6.0:
                        sl_p = max(sl_p, ef[i])
                    elif max_fav >= 4.0:
                        sl_p = max(sl_p, entry_p + 2.0 * r_dist)
                    elif max_fav >= 2.0:
                        sl_p = max(sl_p, entry_p + 0.5 * r_dist)
                    if bl <= sl_p:
                        exited, exit_r = True, (sl_p - entry_p) / r_dist
                elif exit_mode == "trail_89":
                    if bc < em[i]:
                        exited, exit_r = True, (bc - entry_p) / r_dist
                    elif bl <= sl_p:
                        exited, exit_r = True, -1.0
                elif exit_mode == "trail_55":
                    if bc < ef[i]:
                        exited, exit_r = True, (bc - entry_p) / r_dist
                    elif bl <= sl_p:
                        exited, exit_r = True, -1.0
            if exited:
                trades.append(Trade(entry_ts, int(timestamps[i]),
                                    min(exit_r, WINNER_CAP_R) - spread_cost_r,
                                    side, ""))
                in_trade = False
            continue          # MTF: candle consumed - no same-candle re-entry

        # ---- entry detection ----
        macro_i = macros[i]
        if macro_i == 0:
            continue
        cc, hi, lo, ai = closes[i], highs[i], lows[i], atrs[i]
        sig, shelf = 0, None
        if macro_i == -1 and em[i] < es[i]:
            if u_w[i] >= 0.25:
                if (hi >= em[i] * 0.998 or hi >= ef[i] * 0.998) and cc <= em[i]:
                    sig, shelf = -1, "MID"
                elif hi >= es[i] * 0.998 and cc <= es[i]:
                    sig, shelf = -1, "SLOW"
        elif macro_i == 1 and em[i] > es[i]:
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
            sp = (max(es[i], hi) + 0.35 * ai) if sig == -1 else (min(es[i], lo) - 0.35 * ai)
        else:
            sp = (hi + 0.30 * ai) if sig == -1 else (lo - 0.30 * ai)
        rd = abs(ep - sp)
        if rd <= 0:
            continue
        in_trade, side = True, sig
        entry_p, sl_p, r_dist = ep, sp, rd
        max_fav = 0.0
        entry_ts = int(timestamps[next_i])
    return trades


# ---------------------------------------------------------------------------
# Walk-forward validation
# ---------------------------------------------------------------------------

@dataclass
class WalkForwardResult:
    symbol: str
    model: str
    exit_mode: str
    fit: Optional[dict] = None
    validate: Optional[dict] = None
    full: Optional[dict] = None
    verdict: str = "INCONCLUSIVE"
    notes: List[str] = field(default_factory=list)


def load_df(data_dir: str, symbol: str, tf: str) -> Optional[pd.DataFrame]:
    path = os.path.join(data_dir, f"{symbol}_{tf}.csv")
    if not os.path.exists(path):
        return None
    return pd.read_csv(path).sort_values("timestamp").reset_index(drop=True)


def run_config(symbol: str, model: str, timeframe: str, exit_mode: str,
               ribbon: Tuple[int, int, int], data_dir: str,
               fit_end: int = FIT_END, validate_start: int = VALIDATE_START,
               noise_t: float = 4.33) -> WalkForwardResult:
    fast, mid, slow = ribbon
    res = WalkForwardResult(symbol=symbol, model=model, exit_mode=exit_mode)

    if model == "MTF":
        df4, df1 = load_df(data_dir, symbol, "4h"), load_df(data_dir, symbol, "1h")
        if df4 is None or df1 is None:
            res.notes.append("missing data files")
            return res
        trades = simulate_mtf(df4, df1, fast, mid, slow, exit_mode)
        span = (int(df1["timestamp"].iloc[0]), int(df1["timestamp"].iloc[-1]))
    else:
        df = load_df(data_dir, symbol, timeframe.lower())
        if df is None:
            res.notes.append("missing data file")
            return res
        trades = simulate_single_tf(df, fast, mid, slow, exit_mode)
        span = (int(df["timestamp"].iloc[0]), int(df["timestamp"].iloc[-1]))

    fit_trades = [t for t in trades if t.entry_ts <= fit_end]
    val_trades = [t for t in trades if t.entry_ts >= validate_start]
    res.full = _stats(trades, span[0], span[1])
    res.fit = _stats(fit_trades, span[0], max(fit_end, span[0]))
    res.validate = _stats(val_trades, validate_start, span[1])

    if res.validate is None or res.validate["trades"] < MIN_VALIDATE_TRADES:
        res.verdict = "INCONCLUSIVE"
        n = 0 if res.validate is None else res.validate["trades"]
        res.notes.append(
            f"only {n} validate trades (<{MIN_VALIDATE_TRADES}): 45-day window "
            f"cannot confirm or refute the edge")
    elif res.validate["t_stat"] is not None and res.validate["t_stat"] >= noise_t:
        res.verdict = "PASS"
    else:
        res.verdict = "FAIL"
    return res


# ---------------------------------------------------------------------------
# Whole-lake mode (v2)
# ---------------------------------------------------------------------------

def load_master(path: str) -> List[dict]:
    rows = []
    with open(path) as f:
        for r in csv.DictReader(f):
            rows.append(r)
    return rows


def best_config_per_symbol(rows: List[dict], min_trades: int = 10) -> Dict[str, dict]:
    """Deployment candidate per symbol: highest Calmar among configs with
    >= min_trades trades. This mirrors a leaderboard-driven pipeline and is
    deliberately the most generous selection rule - the walk-forward result
    then measures what that selection actually delivers out-of-sample."""
    best: Dict[str, dict] = {}
    for r in rows:
        if int(r["trades"]) < min_trades:
            continue
        sym = r["symbol"]
        if sym not in best or float(r["calmar_ratio"]) > float(best[sym]["calmar_ratio"]):
            best[sym] = r
    return best


def run_all_symbols(master_csv: str, data_dir: str, fit_end: int,
                    validate_start: int, noise_t: float,
                    progress_every: int = 25) -> List[WalkForwardResult]:
    rows = load_master(master_csv)
    n_grid = len(rows)
    best = best_config_per_symbol(rows)
    results: List[WalkForwardResult] = []
    for k, (sym, row) in enumerate(sorted(best.items()), 1):
        model = MODEL_MAP.get(row["model_type"])
        ribbon = RIBBON_SPANS.get(row["ribbon"])
        if model is None or ribbon is None:
            continue
        r = run_config(sym, model, row["timeframe"], row["strategy"], ribbon,
                       data_dir, fit_end, validate_start, noise_t)
        # fidelity note: full-sample stats must reproduce the master row
        if r.full and r.full["trades"] != int(row["trades"]):
            r.notes.append(
                f"re-sim trades {r.full['trades']} != master {row['trades']} "
                f"(data snapshot drift) - trust the re-simulation")
        results.append(r)
        if k % progress_every == 0:
            print(f"  ...{k}/{len(best)} symbols", flush=True)
    return results


def aggregate(results: List[WalkForwardResult]) -> dict:
    counts = {"PASS": 0, "FAIL": 0, "INCONCLUSIVE": 0}
    val_nets = []
    for r in results:
        counts[r.verdict] += 1
        if r.validate and r.validate["trades"] >= MIN_VALIDATE_TRADES:
            val_nets.append(r.validate["net_r"])
    med = float(np.median(val_nets)) if val_nets else None
    return {"symbols": len(results), "counts": counts,
            "validated_with_sample": len(val_nets),
            "median_validate_net_r": None if med is None else round(med, 2)}


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def print_results(results: List[WalkForwardResult], noise_t: float, n_grid: int,
                   fit_end: int, validate_start: int) -> None:
    print("Walk-forward validation - QR-2026-1114-R1")
    print(f"FIT: entries <= {datetime.fromtimestamp(fit_end, tz=timezone.utc).date()} | "
          f"VALIDATE: entries >= {datetime.fromtimestamp(validate_start, tz=timezone.utc).date()} | "
          f"noise floor t >= {noise_t:.2f} (sqrt(2 ln {n_grid:,}))\n")
    print(f"{'symbol':12} {'model':7} {'exit':10} | {'FIT':>3} {'netR':>7} {'t':>5} | "
          f"{'VAL':>3} {'netR':>7} {'t':>5} | {'full n':>6} {'full netR':>9} | verdict")
    print("-" * 103)
    for r in results:
        f, v, fu = (r.fit or {}), (r.validate or {}), (r.full or {})
        print(f"{r.symbol:12} {r.model:7} {r.exit_mode:10} | "
              f"{f.get('trades', 0):>3} {f.get('net_r', 0):>7.2f} "
              f"{str(f.get('t_stat', '-')):>5} | "
              f"{v.get('trades', 0):>3} {v.get('net_r', 0):>7.2f} "
              f"{str(v.get('t_stat', '-')):>5} | "
              f"{fu.get('trades', 0):>6} {fu.get('net_r', 0):>9.2f} | {r.verdict}")
    for r in results:
        for note in r.notes:
            print(f"  note {r.symbol}: {note}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data-dir", default="./data")
    ap.add_argument("--all", action="store_true",
                    help="validate every symbol in the master audit file "
                         "(best config per symbol) instead of the headline 7")
    ap.add_argument("--master",
                    default="./audit/delta_full_master_audit.csv",
                    help="master audit CSV (grid of trials; also sets N for "
                         "the deflation threshold)")
    ap.add_argument("--extra-trials", type=int, default=0,
                    help="additional trials run since the master file (e.g. a "
                         "trailing-stop search) - raises the noise floor")
    ap.add_argument("--fit-end", type=int, default=FIT_END)
    ap.add_argument("--validate-start", type=int, default=VALIDATE_START)
    ap.add_argument("--json", default=None)
    ap.add_argument("--registry", default=None,
                    help="write PASS/INCONCLUSIVE symbols to this JSON "
                         "registry for PAPER trading")
    args = ap.parse_args()

    n_grid = 0
    if os.path.exists(args.master):
        n_grid = len(load_master(args.master)) + args.extra_trials
    else:
        print(f"warning: master file not found at {args.master}; "
              f"cannot compute the grid-size noise floor")
    noise_t = math.sqrt(2.0 * math.log(n_grid)) if n_grid > 1 else 4.33

    if args.all:
        if not n_grid:
            raise SystemExit("--all requires the master audit CSV")
        results = run_all_symbols(args.master, args.data_dir, args.fit_end,
                                  args.validate_start, noise_t)
    else:
        results = [run_config(sym, model, tf, mode, ribbon, args.data_dir,
                              args.fit_end, args.validate_start, noise_t)
                   for sym, model, tf, mode, ribbon in HEADLINE_CONFIGS]

    print_results(results, noise_t, n_grid, args.fit_end, args.validate_start)
    agg = aggregate(results)
    print(f"\naggregate: {agg['counts']} | symbols with >=5 validate trades: "
          f"{agg['validated_with_sample']}"
          + (f" | median validate net {agg['median_validate_net_r']:+.2f}R"
             if agg['median_validate_net_r'] is not None else ""))

    if args.json:
        payload = {
            "parameters": {"fit_end": args.fit_end,
                           "validate_start": args.validate_start,
                           "n_grid_trials": n_grid, "noise_t": round(noise_t, 2),
                           "min_validate_trades": MIN_VALIDATE_TRADES,
                           "mode": "all" if args.all else "headline7"},
            "aggregate": agg,
            "results": [{"symbol": r.symbol, "model": r.model,
                         "exit_mode": r.exit_mode, "fit": r.fit,
                         "validate": r.validate, "full": r.full,
                         "verdict": r.verdict, "notes": r.notes}
                        for r in results]}
        with open(args.json, "w") as f:
            json.dump(payload, f, indent=1)
        print(f"results written to {args.json}")

    if args.registry:
        reg = {"generated": datetime.now(timezone.utc).isoformat(),
               "policy": "PASS/INCONCLUSIVE symbols are eligible for PAPER "
                         "trading only; live deployment requires an "
                         "accumulated forward sample clearing the same floor",
               "noise_t": round(noise_t, 2), "n_grid_trials": n_grid,
               "symbols": {}}
        for r in results:
            if r.verdict in ("PASS", "INCONCLUSIVE"):
                reg["symbols"][r.symbol] = {
                    "verdict": r.verdict, "model": r.model,
                    "exit_mode": r.exit_mode,
                    "validate": r.validate, "fit": r.fit}
        with open(args.registry, "w") as f:
            json.dump(reg, f, indent=1)
        n_pass = sum(1 for r in results if r.verdict == "PASS")
        print(f"registry written to {args.registry} "
              f"({len(reg['symbols'])} symbols, {n_pass} PASS)")


if __name__ == "__main__":
    main()
