#!/usr/bin/env python3
"""institutional_wyckoff_engine.py: Core 4H Fib_89_233 Institutional Wyckoff
Absorption & Hybrid Chandelier Engine for Delta Exchange India (INR Denominated).

Consolidates:
  1. Causal 4H Indicator Suite (EMA 55/89/233, ATR 14, Wilder ADX 14, Volume SMA 20).
  2. Exact Delta Exchange India Fee + 18% GST + Liquidity-Tiered Slippage Model.
  3. Wyckoff Institutional Absorption Pullback Filter (aligned candle body,
     0.25-2.50 ATR ribbon separation band, <= 1.2x volume dry-up, <= 0.12R friction).
  4. Hybrid 50% Maker Limit TP (+2.5R) + 50% Chandelier (3.0*ATR) Runner &
     ADX-Adaptive Regime Exit Policies with gap-realistic stop execution.
  5. Chronological Event-Driven ₹5,000 INR Portfolio Simulator (Static 1R &
     Dynamic 1% Compounding) with Exact Integer Contract Sizing, Cluster &
     Directional Crypto Firewalls, Crash-Replay Anchor Gate, and Tie-Break Audit.
"""

from __future__ import annotations

import os
import sys
from typing import Dict, List, Optional, Tuple, Any

import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)
if os.path.join(BASE_DIR, "engine") not in sys.path:
    sys.path.insert(0, os.path.join(BASE_DIR, "engine"))

from universe_screener import load_snapshot, screen, INR_PER_USD, GST_ON_FEES
from delta_min_lot_sizer import calculate_delta_order_sizing
from cluster_risk_governor import validate_new_trade_entry, get_asset_cluster
from asset_universe_classifier import classify_asset

GST_MULT = 1.0 + GST_ON_FEES
DEFAULT_BASE_CAPITAL_INR = 5000.0
WARMUP_BARS_4H = 250


def load_deployable_specs(
    offline: bool = True,
    risk_cap_inr: float = 55.0,
    margin_cap_inr: float = 2500.0,
    volume_floor_usd: float = 100_000.0,
    friction_cap_r: float = 0.20,
) -> Dict[str, Dict[str, Any]]:
    """Loads product specifications + fee/slippage profiles for T1+T2+T3 eligible perpetuals."""
    try:
        products, tickers = load_snapshot(offline=offline)
    except Exception:
        products, tickers = load_snapshot(offline=True)

    rows = screen(
        products,
        tickers,
        risk_cap_inr=risk_cap_inr,
        margin_cap_inr=margin_cap_inr,
        volume_floor_usd=volume_floor_usd,
        friction_cap_r=friction_cap_r,
    )
    eligible_syms = {r["symbol"]: r for r in rows if r.get("eligible")}

    specs: Dict[str, Dict[str, Any]] = {}
    for p in products:
        sym = p.get("symbol")
        if sym in eligible_syms:
            r = eligible_syms[sym]
            t = tickers.get(sym, {})
            turnover_usd = float(t.get("turnover") or t.get("turnover_usd") or r.get("turnover_24h_usd") or 0.0)
            if turnover_usd >= 5_000_000.0:
                slip_rate = 0.0001  # 1 bps liquid major
            elif turnover_usd >= 100_000.0:
                slip_rate = 0.0002  # 2 bps mid-cap perpetual
            else:
                slip_rate = 0.0004  # 4 bps thin book
            specs[sym] = {
                "symbol": sym,
                "id": p.get("id"),
                "contract_value": float(p["contract_value"]),
                "tick_size": float(p.get("tick_size") or 0.0001),
                "taker_rate": float(p.get("taker_commission_rate") or 0.0005),
                "maker_rate": float(p.get("maker_commission_rate") or 0.0002),
                "slip_rate": slip_rate,
                "turnover_usd": turnover_usd,
                "mark_price_snapshot": float(r.get("mark_price") or 0.0),
                "sector": classify_asset(sym),
                "cluster": get_asset_cluster(sym),
            }
    return specs


def compute_wilder_adx(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
    """Computes causal Wilder ADX(period) without lookahead."""
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


def enrich_4h_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Computes Fib_89_233 (55, 89, 233) EMAs, ATR_14, Wilder ADX_14, and Volume SMA_20."""
    out = df.copy()
    if pd.api.types.is_numeric_dtype(out["timestamp"]):
        out["timestamp"] = pd.to_datetime(out["timestamp"], unit="s", utc=True)
    else:
        out["timestamp"] = pd.to_datetime(out["timestamp"], utc=True)
    out = out.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)

    c = out["close"].astype(float)
    h = out["high"].astype(float)
    l = out["low"].astype(float)
    v = out["volume"].astype(float)

    out["ema55"] = c.ewm(span=55, adjust=False).mean()
    out["ema89"] = c.ewm(span=89, adjust=False).mean()
    out["ema233"] = c.ewm(span=233, adjust=False).mean()

    prev_c = c.shift(1)
    tr = pd.concat([(h - l), (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    out["atr14"] = tr.rolling(14).mean()
    out["vol_sma20"] = v.rolling(20).mean()
    out["adx14"] = compute_wilder_adx(h.values, l.values, c.values, 14)
    out["ret_2bar_pct"] = (c / c.shift(2) - 1.0) * 100.0
    return out


def compute_leg_fee_inr(
    price: float,
    contracts: int,
    spec: Dict[str, Any],
    is_maker_limit: bool,
) -> float:
    """Computes single-leg Delta Exchange India fee + 18% GST + slippage in INR."""
    notional_inr = price * contracts * spec["contract_value"] * INR_PER_USD
    if is_maker_limit:
        rate = spec["maker_rate"] * GST_MULT
    else:
        rate = spec["taker_rate"] * GST_MULT + spec["slip_rate"]
    return notional_inr * rate


def estimate_roundtrip_cost_r(entry: float, sl: float, spec: Dict[str, Any]) -> float:
    """Computes trade-specific round-trip taker+slippage friction expressed in R multiples."""
    stop_pct = abs(entry - sl) / max(entry, 1e-12)
    if stop_pct <= 0:
        return 999.0
    rt_rate = 2.0 * (spec["taker_rate"] * GST_MULT + spec["slip_rate"])
    return rt_rate / stop_pct


def compute_signal_quality_score(
    cost_r: float,
    base_risk_inr: float,
    ema_sep_atr: float,
    vol_ratio: float,
) -> float:
    """Deterministic ex-ante setup quality score in [0, ~3.4] for simultaneous 4H signal ranking."""
    fee_eff = max(0.0, 1.0 - cost_r / 0.12)
    lot_util = min(1.0, base_risk_inr / 50.0)
    sep_health = max(0.0, 1.0 - abs(ema_sep_atr - 1.15) / 1.35)
    vol_quiet = max(0.0, 1.0 - vol_ratio / 1.50)
    return 1.2 * fee_eff + 0.9 * lot_util + 0.7 * sep_health + 0.6 * vol_quiet


def evaluate_bar_wyckoff_signal(
    row: pd.Series,
    spec: Dict[str, Any],
    anchor_2bar_pct: float = 0.0,
    min_wick_ratio: float = 0.25,
    min_sep_atr: float = 0.25,
    max_sep_atr: float = 2.50,
    max_vol_ratio: float = 1.20,
    max_cost_r: float = 0.12,
    sl_atr_buf: float = 0.30,
    require_aligned_body: bool = True,
) -> Optional[Dict[str, Any]]:
    """Evaluates a single CLOSED 4H bar for an Institutional Wyckoff Fib_89_233 setup.

    Returns a dictionary with full setup parameters if all institutional gates pass, else None.
    """
    atr = float(row["atr14"])
    c = float(row["close"])
    o = float(row["open"])
    h = float(row["high"])
    l = float(row["low"])
    v = float(row["volume"])
    e55 = float(row["ema55"])
    e89 = float(row["ema89"])
    e233 = float(row["ema233"])
    vsma = float(row["vol_sma20"])
    adx_val = float(row["adx14"])
    ret_2b = float(row["ret_2bar_pct"]) if pd.notna(row["ret_2bar_pct"]) else 0.0

    if not np.isfinite(atr) or atr <= 0 or c <= 0:
        return None
    rng = h - l
    if rng <= 1e-12:
        return None

    # 1. Pre-Entry Crash-Replay Gate (Asset & BTC Anchor)
    if ret_2b <= -6.0 or anchor_2bar_pct <= -6.0:
        return None

    # 2. Trend-Health Ribbon Separation Band (filters <0.25 ATR chop & >2.50 ATR exhaustion)
    ema_sep_atr = abs(e89 - e233) / atr
    if ema_sep_atr < min_sep_atr or ema_sep_atr > max_sep_atr:
        return None

    # 3. Wyckoff Pullback Volume Dry-Up Gate
    vol_ratio = v / vsma if (np.isfinite(vsma) and vsma > 0) else 1.0
    if vol_ratio > max_vol_ratio:
        return None

    # 4. Macro Trend + Dynamic Fib_89_233 Rejection Wick + Aligned Body
    sig = 0
    if e89 > e233 and c > e233:
        lower_wick = (c - l) / rng
        if l <= max(e55, e89) and c > e89 and lower_wick >= min_wick_ratio:
            if (not require_aligned_body) or (c > o):
                sig = 1
    elif e89 < e233 and c < e233:
        upper_wick = (h - c) / rng
        if h >= min(e55, e89) and c < e89 and upper_wick >= min_wick_ratio:
            if (not require_aligned_body) or (c < o):
                sig = -1

    if sig == 0:
        return None

    entry = c
    sl = (l - sl_atr_buf * atr) if sig == 1 else (h + sl_atr_buf * atr)
    r_dist = abs(entry - sl)
    if r_dist <= 0 or (r_dist / entry) < 0.0025:
        return None

    # 5. Universal Symbol-Specific Fee-Friction Gate
    cost_r = estimate_roundtrip_cost_r(entry, sl, spec)
    if cost_r > max_cost_r:
        return None

    # 6. Base ₹5,000 T1 Integer Lot Feasibility Check
    base_sz = calculate_delta_order_sizing(
        symbol=spec["symbol"],
        entry_price=entry,
        stop_price=sl,
        equity_inr=DEFAULT_BASE_CAPITAL_INR,
        risk_pct=0.01,
        leverage=10.0,
        products_dict={spec["symbol"]: spec},
    )
    if base_sz["status"] != "APPROVED_1R":
        return None

    qscore = compute_signal_quality_score(
        cost_r=cost_r,
        base_risk_inr=base_sz["actual_risk_inr"],
        ema_sep_atr=ema_sep_atr,
        vol_ratio=vol_ratio,
    )

    tp1_2_5r = entry + 2.5 * r_dist if sig == 1 else entry - 2.5 * r_dist
    tp_3_0r = entry + 3.0 * r_dist if sig == 1 else entry - 3.0 * r_dist

    return {
        "symbol": spec["symbol"],
        "timestamp": row["timestamp"],
        "side": sig,
        "side_str": "buy" if sig == 1 else "sell",
        "action": "BUY / LONG" if sig == 1 else "SELL / SHORT",
        "entry": entry,
        "sl": sl,
        "r_dist": r_dist,
        "tp1_2_5r": tp1_2_5r,
        "tp_3_0r": tp_3_0r,
        "trail_amount_3atr": 3.0 * atr,
        "atr14": atr,
        "adx14": adx_val,
        "ema_sep_atr": ema_sep_atr,
        "vol_ratio": vol_ratio,
        "cost_r": cost_r,
        "recent_pct_change": ret_2b,
        "cluster_anchor_pct_change": anchor_2bar_pct,
        "base_contracts": base_sz["contracts"],
        "base_risk_inr": base_sz["actual_risk_inr"],
        "base_margin_inr": base_sz["margin_10x_inr"],
        "quality_score": qscore,
    }


def simulate_trade_path_exact(
    df: pd.DataFrame,
    entry_idx: int,
    sig: int,
    entry: float,
    sl: float,
    contracts: int,
    spec: Dict[str, Any],
    exit_policy: str = "hybrid",
    tp1_r: float = 2.5,
    be_lock_r: float = 0.20,
    chand_mult: float = 3.0,
    fixed_tp_r: float = 3.0,
    adx_threshold: float = 25.0,
    cap_r: float = 15.0,
) -> Dict[str, Any]:
    """Simulates exact rupee PnL, fees, GST, and slippage across multi-lot or single-lot paths.

    * Strictly causal: trailing Chandelier stop on bar `j` uses `atr14[j-1]`.
    * Gap-realistic: if bar `j` opens past the active stop, exit executes at `open[j]`.
    """
    o = df["open"].values
    h = df["high"].values
    l = df["low"].values
    c = df["close"].values
    atr = df["atr14"].values
    ts = df["timestamp"].values
    adx_entry = float(df["adx14"].iloc[entry_idx])

    r_dist = abs(entry - sl)
    cv = spec["contract_value"]
    n = len(df)

    # Resolve effective policy for this trade
    eff_policy = exit_policy
    if exit_policy == "adx_adaptive":
        eff_policy = "hybrid" if adx_entry >= adx_threshold else "fixed_3.0"

    fee_entry_total = compute_leg_fee_inr(entry, contracts, spec, is_maker_limit=False)

    # Fixed resting Limit Take-Profit policy (+3.0R)
    if eff_policy == "fixed_3.0":
        tp_px = entry + fixed_tp_r * r_dist if sig == 1 else entry - fixed_tp_r * r_dist
        exit_px = float(c[-1])
        exit_idx = n - 1
        is_maker_exit = False
        exit_reason = "EOD_MARK"

        for j in range(entry_idx + 1, n):
            if sig == 1:
                if l[j] <= sl:
                    exit_px = float(min(o[j], sl))
                    exit_idx = j
                    is_maker_exit = False
                    exit_reason = "STOP_LOSS"
                    break
                if h[j] >= tp_px:
                    exit_px = tp_px
                    exit_idx = j
                    is_maker_exit = True
                    exit_reason = f"LIMIT_TP_{fixed_tp_r:.1f}R"
                    break
            else:
                if h[j] >= sl:
                    exit_px = float(max(o[j], sl))
                    exit_idx = j
                    is_maker_exit = False
                    exit_reason = "STOP_LOSS"
                    break
                if l[j] <= tp_px:
                    exit_px = tp_px
                    exit_idx = j
                    is_maker_exit = True
                    exit_reason = f"LIMIT_TP_{fixed_tp_r:.1f}R"
                    break

        gross_inr = sig * (exit_px - entry) * contracts * cv * INR_PER_USD
        fee_exit = compute_leg_fee_inr(exit_px, contracts, spec, is_maker_limit=is_maker_exit)
        total_fees = fee_entry_total + fee_exit
        net_inr = gross_inr - total_fees
        init_risk_inr = r_dist * contracts * cv * INR_PER_USD
        return {
            "exit_idx": exit_idx,
            "exit_time": df["timestamp"].iloc[exit_idx],
            "gross_pnl_inr": gross_inr,
            "fees_inr": total_fees,
            "net_pnl_inr": net_inr,
            "net_r": net_inr / max(init_risk_inr, 1e-9),
            "exit_reason": exit_reason,
            "effective_policy": eff_policy,
        }

    # Hybrid Scale-Out (+2.5R 50% Maker Limit TP + +0.20R BE Lock + 50% 3.0*ATR Chandelier Runner)
    lots_1 = contracts // 2
    lots_2 = contracts - lots_1
    tp1_px = entry + tp1_r * r_dist if sig == 1 else entry - tp1_r * r_dist
    cap_px = entry + cap_r * r_dist if sig == 1 else entry - cap_r * r_dist

    scaled = False
    cur_sl = sl
    peak = entry
    gross_1 = 0.0
    fee_exit_1 = 0.0
    rem_lots = contracts
    exit_px_2 = float(c[-1])
    exit_idx = n - 1
    is_maker_2 = False
    exit_reason = "EOD_MARK"

    for j in range(entry_idx + 1, n):
        prev_atr = float(atr[j - 1]) if np.isfinite(atr[j - 1]) and atr[j - 1] > 0 else float(atr[entry_idx])

        if sig == 1:
            if l[j] <= cur_sl:
                exit_px_2 = float(min(o[j], cur_sl))
                exit_idx = j
                is_maker_2 = False
                exit_reason = "CHANDELIER_RUNNER" if scaled else "STOP_LOSS"
                break

            if not scaled and h[j] >= tp1_px:
                scaled = True
                if lots_1 > 0:
                    gross_1 = (tp1_px - entry) * lots_1 * cv * INR_PER_USD
                    fee_exit_1 = compute_leg_fee_inr(tp1_px, lots_1, spec, is_maker_limit=True)
                    rem_lots = lots_2
                cur_sl = max(cur_sl, entry + be_lock_r * r_dist)

            if h[j] > peak:
                peak = float(h[j])
            if scaled:
                chand_sl = peak - chand_mult * prev_atr
                if chand_sl > cur_sl:
                    cur_sl = chand_sl
                if h[j] >= cap_px:
                    exit_px_2 = cap_px
                    exit_idx = j
                    is_maker_2 = True
                    exit_reason = f"RUNNER_CAP_{cap_r:.0f}R"
                    break
        else:
            if h[j] >= cur_sl:
                exit_px_2 = float(max(o[j], cur_sl))
                exit_idx = j
                is_maker_2 = False
                exit_reason = "CHANDELIER_RUNNER" if scaled else "STOP_LOSS"
                break

            if not scaled and l[j] <= tp1_px:
                scaled = True
                if lots_1 > 0:
                    gross_1 = (entry - tp1_px) * lots_1 * cv * INR_PER_USD
                    fee_exit_1 = compute_leg_fee_inr(tp1_px, lots_1, spec, is_maker_limit=True)
                    rem_lots = lots_2
                cur_sl = min(cur_sl, entry - be_lock_r * r_dist)

            if l[j] < peak:
                peak = float(l[j])
            if scaled:
                chand_sl = peak + chand_mult * prev_atr
                if chand_sl < cur_sl:
                    cur_sl = chand_sl
                if l[j] <= cap_px:
                    exit_px_2 = cap_px
                    exit_idx = j
                    is_maker_2 = True
                    exit_reason = f"RUNNER_CAP_{cap_r:.0f}R"
                    break

    gross_2 = sig * (exit_px_2 - entry) * rem_lots * cv * INR_PER_USD
    fee_exit_2 = compute_leg_fee_inr(exit_px_2, rem_lots, spec, is_maker_limit=is_maker_2)

    total_gross = gross_1 + gross_2
    total_fees = fee_entry_total + fee_exit_1 + fee_exit_2
    net_inr = total_gross - total_fees
    init_risk_inr = r_dist * contracts * cv * INR_PER_USD

    return {
        "exit_idx": exit_idx,
        "exit_time": df["timestamp"].iloc[exit_idx],
        "gross_pnl_inr": total_gross,
        "fees_inr": total_fees,
        "net_pnl_inr": net_inr,
        "net_r": net_inr / max(init_risk_inr, 1e-9),
        "exit_reason": exit_reason,
        "effective_policy": eff_policy,
    }


def scan_historical_candidates(
    data_dir: str,
    specs: Dict[str, Dict[str, Any]],
    min_sep_atr: float = 0.25,
    max_sep_atr: float = 2.50,
    max_vol_ratio: float = 1.20,
    max_cost_r: float = 0.12,
) -> Tuple[List[Dict[str, Any]], Dict[str, pd.DataFrame]]:
    """Loads all eligible 4H CSVs, computes indicators, and extracts causal Wyckoff candidates."""
    dfs: Dict[str, pd.DataFrame] = {}
    for sym in sorted(specs.keys()):
        fpath = os.path.join(data_dir, f"{sym}_4h.csv")
        if not os.path.exists(fpath):
            continue
        raw = pd.read_csv(fpath)
        if len(raw) < WARMUP_BARS_4H + 20:
            continue
        dfs[sym] = enrich_4h_dataframe(raw)

    # Market anchor (BTCUSD) 2-bar closed 4H % change map
    btc_2bar_map: Dict[pd.Timestamp, float] = {}
    if "BTCUSD" in dfs:
        bdf = dfs["BTCUSD"]
        for ts_val, pct_val in zip(bdf["timestamp"], bdf["ret_2bar_pct"]):
            if pd.notna(pct_val):
                btc_2bar_map[ts_val] = float(pct_val)

    candidates: List[Dict[str, Any]] = []
    for sym, df in dfs.items():
        spec = specs[sym]
        o_arr = df["open"].values
        h_arr = df["high"].values
        l_arr = df["low"].values
        c_arr = df["close"].values
        e55_arr = df["ema55"].values
        e89_arr = df["ema89"].values
        e233_arr = df["ema233"].values
        atr_arr = df["atr14"].values
        vsma_arr = df["vol_sma20"].values
        v_arr = df["volume"].values

        rng_arr = h_arr - l_arr
        valid = (atr_arr > 0) & (rng_arr > 1e-12)
        sep_arr = np.abs(e89_arr - e233_arr) / np.where(atr_arr > 0, atr_arr, 1.0)
        vol_ok = np.where(vsma_arr > 0, (v_arr / np.where(vsma_arr > 0, vsma_arr, 1.0)) <= max_vol_ratio, True)
        long_hit = (
            (e89_arr > e233_arr)
            & (c_arr > e233_arr)
            & (l_arr <= np.maximum(e55_arr, e89_arr))
            & (c_arr > e89_arr)
            & ((c_arr - l_arr) >= 0.25 * rng_arr)
            & (c_arr > o_arr)
        )
        short_hit = (
            (e89_arr < e233_arr)
            & (c_arr < e233_arr)
            & (h_arr >= np.minimum(e55_arr, e89_arr))
            & (c_arr < e89_arr)
            & ((h_arr - c_arr) >= 0.25 * rng_arr)
            & (c_arr < o_arr)
        )
        mask = valid & (sep_arr >= min_sep_atr) & (sep_arr <= max_sep_atr) & vol_ok & (long_hit | short_hit)
        hit_indices = np.flatnonzero(mask)
        for idx in hit_indices:
            if idx < WARMUP_BARS_4H or idx >= len(df) - 1:
                continue
            row = df.iloc[int(idx)]
            anchor_pct = btc_2bar_map.get(row["timestamp"], 0.0)
            cand = evaluate_bar_wyckoff_signal(
                row=row,
                spec=spec,
                anchor_2bar_pct=anchor_pct,
                min_sep_atr=min_sep_atr,
                max_sep_atr=max_sep_atr,
                max_vol_ratio=max_vol_ratio,
                max_cost_r=max_cost_r,
            )
            if cand is not None:
                cand["bar_idx"] = int(idx)
                candidates.append(cand)

    return candidates, dfs


def run_event_driven_inr_portfolio(
    candidates: List[Dict[str, Any]],
    dfs: Dict[str, pd.DataFrame],
    specs: Dict[str, Dict[str, Any]],
    start_ts: Optional[str] = None,
    end_ts: Optional[str] = None,
    starting_capital_inr: float = DEFAULT_BASE_CAPITAL_INR,
    compounding: bool = True,
    exit_policy: str = "hybrid",
    max_open_trades: int = 3,
    max_total_risk_r: float = 3.0,
    max_same_dir_crypto: int = 2,
    tie_break: str = "quality_score",
    rng_seed: int = 42,
) -> Dict[str, Any]:
    """Chronological event-driven INR portfolio simulator with exact integer lot sizing."""
    t0 = pd.Timestamp(start_ts, tz="UTC") if start_ts else None
    t1 = pd.Timestamp(end_ts, tz="UTC") if end_ts else None

    filtered = []
    for c in candidates:
        ts = c["timestamp"]
        if t0 is not None and ts < t0:
            continue
        if t1 is not None and ts > t1:
            continue
        filtered.append(c)

    by_ts: Dict[pd.Timestamp, List[Dict[str, Any]]] = {}
    for c in filtered:
        by_ts.setdefault(c["timestamp"], []).append(c)

    rng = np.random.default_rng(rng_seed)
    equity = starting_capital_inr
    peak_eq = starting_capital_inr
    max_dd_inr = 0.0
    max_dd_pct = 0.0

    active_positions: Dict[str, Dict[str, Any]] = {}
    pending_exits: List[Tuple[pd.Timestamp, str, Dict[str, Any]]] = []
    executed_trades: List[Dict[str, Any]] = []
    rejection_counts: Dict[str, int] = {
        "cluster_or_crypto_cap": 0,
        "concurrency_or_heat": 0,
        "margin_cap": 0,
        "lot_sizing": 0,
        "already_open": 0,
    }

    for cur_ts in sorted(by_ts.keys()):
        # 1. Settle all positions that exited on or before cur_ts
        still_open = []
        for ex_ts, sym, tr_rec in sorted(pending_exits, key=lambda x: x[0]):
            if ex_ts <= cur_ts:
                equity += tr_rec["net_pnl_inr"]
                tr_rec["equity_after_inr"] = round(equity, 2)
                peak_eq = max(peak_eq, equity)
                dd = peak_eq - equity
                max_dd_inr = max(max_dd_inr, dd)
                if peak_eq > 0:
                    max_dd_pct = max(max_dd_pct, dd / peak_eq * 100.0)
                active_positions.pop(sym, None)
            else:
                still_open.append((ex_ts, sym, tr_rec))
        pending_exits = still_open

        if equity <= 500.0:
            break

        # 2. Order simultaneous 4H signals according to tie-break rule
        batch = list(by_ts[cur_ts])
        if tie_break == "quality_score":
            batch.sort(key=lambda x: (-x["quality_score"], x["symbol"]))
        elif tie_break == "forward":
            batch.sort(key=lambda x: x["symbol"])
        elif tie_break == "reversed":
            batch.sort(key=lambda x: x["symbol"], reverse=True)
        elif tie_break == "random":
            rng.shuffle(batch)

        sizing_equity = max(starting_capital_inr, equity) if compounding else starting_capital_inr

        for cand in batch:
            sym = cand["symbol"]
            spec = specs[sym]

            # Dynamic integer contract sizing at current realized equity
            sz = calculate_delta_order_sizing(
                symbol=sym,
                entry_price=cand["entry"],
                stop_price=cand["sl"],
                equity_inr=sizing_equity,
                risk_pct=0.01,
                leverage=10.0,
                products_dict={sym: spec},
            )
            if sz["status"] != "APPROVED_1R":
                rejection_counts["lot_sizing"] += 1
                continue

            order_req = {
                "symbol": sym,
                "side": cand["side_str"],
                "actual_risk_inr": sz["actual_risk_inr"],
                "margin_10x_inr": sz["margin_10x_inr"],
                "notional_inr": sz["notional_inr"],
                "recent_pct_change": cand["recent_pct_change"],
                "cluster_anchor_pct_change": cand["cluster_anchor_pct_change"],
            }
            ok, reason = validate_new_trade_entry(
                candidate_order=order_req,
                active_positions=active_positions,
                equity_inr=sizing_equity,
                max_open_trades=max_open_trades,
                max_total_risk_r=max_total_risk_r,
                max_margin_ratio=0.60,
                max_same_dir_crypto=max_same_dir_crypto,
            )
            if not ok:
                if "already has an active" in reason:
                    rejection_counts["already_open"] += 1
                elif "Cluster conflict" in reason or "Directional crypto" in reason:
                    rejection_counts["cluster_or_crypto_cap"] += 1
                elif "margin" in reason.lower():
                    rejection_counts["margin_cap"] += 1
                else:
                    rejection_counts["concurrency_or_heat"] += 1
                continue

            # Simulate exact trade trajectory on 4H series (truncated to end_ts if specified)
            df_sym = dfs[sym]
            if t1 is not None:
                df_slice = df_sym[df_sym["timestamp"] <= t1].reset_index(drop=True)
            else:
                df_slice = df_sym

            sim = simulate_trade_path_exact(
                df=df_slice,
                entry_idx=cand["bar_idx"] if t1 is None else int(df_slice[df_slice["timestamp"] == cur_ts].index[0]),
                sig=cand["side"],
                entry=cand["entry"],
                sl=cand["sl"],
                contracts=sz["contracts"],
                spec=spec,
                exit_policy=exit_policy,
            )

            trade_record = {
                "symbol": sym,
                "sector": spec["sector"],
                "cluster": spec["cluster"],
                "side": "LONG" if cand["side"] == 1 else "SHORT",
                "entry_time": cur_ts,
                "exit_time": sim["exit_time"],
                "entry_price": round(cand["entry"], 6),
                "stop_price": round(cand["sl"], 6),
                "contracts": sz["contracts"],
                "sizing_equity_inr": round(sizing_equity, 2),
                "actual_risk_inr": round(sz["actual_risk_inr"], 2),
                "margin_10x_inr": round(sz["margin_10x_inr"], 2),
                "gross_pnl_inr": round(sim["gross_pnl_inr"], 2),
                "fees_gst_slip_inr": round(sim["fees_inr"], 2),
                "net_pnl_inr": round(sim["net_pnl_inr"], 2),
                "net_r": round(sim["net_r"], 4),
                "quality_score": round(cand["quality_score"], 4),
                "adx14": round(cand["adx14"], 2),
                "ema_sep_atr": round(cand["ema_sep_atr"], 3),
                "vol_ratio": round(cand["vol_ratio"], 3),
                "exit_policy": sim["effective_policy"],
                "exit_reason": sim["exit_reason"],
                "equity_after_inr": 0.0,
            }
            active_positions[sym] = {
                "side": cand["side_str"],
                "actual_risk_inr": sz["actual_risk_inr"],
                "margin_10x_inr": sz["margin_10x_inr"],
                "notional_inr": sz["notional_inr"],
            }
            pending_exits.append((sim["exit_time"], sym, trade_record))
            executed_trades.append(trade_record)

    # Settle remaining open trades at end of period
    for ex_ts, sym, tr_rec in sorted(pending_exits, key=lambda x: x[0]):
        equity += tr_rec["net_pnl_inr"]
        tr_rec["equity_after_inr"] = round(equity, 2)
        peak_eq = max(peak_eq, equity)
        dd = peak_eq - equity
        max_dd_inr = max(max_dd_inr, dd)
        if peak_eq > 0:
            max_dd_pct = max(max_dd_pct, dd / peak_eq * 100.0)

    wins = sum(1 for t in executed_trades if t["net_pnl_inr"] > 0)
    total_n = len(executed_trades)
    win_rate = (wins / total_n * 100.0) if total_n > 0 else 0.0
    gross_profit = sum(t["net_pnl_inr"] for t in executed_trades if t["net_pnl_inr"] > 0)
    gross_loss = abs(sum(t["net_pnl_inr"] for t in executed_trades if t["net_pnl_inr"] < 0))
    pf = (gross_profit / gross_loss) if gross_loss > 0 else 0.0
    avg_net_r = float(np.mean([t["net_r"] for t in executed_trades])) if total_n > 0 else 0.0
    total_fees_inr = sum(t["fees_gst_slip_inr"] for t in executed_trades)
    ret_pct = (equity - starting_capital_inr) / starting_capital_inr * 100.0

    # Annualized Calmar over active span
    if total_n >= 2:
        span_days = max(
            1.0,
            (executed_trades[-1]["exit_time"] - executed_trades[0]["entry_time"]).total_seconds() / 86400.0,
        )
        years = span_days / 365.25
        cagr_pct = ((max(equity, 1.0) / starting_capital_inr) ** (1.0 / max(years, 0.25)) - 1.0) * 100.0
        calmar = cagr_pct / max(max_dd_pct, 1.0)
    else:
        cagr_pct = 0.0
        calmar = 0.0

    return {
        "starting_capital_inr": starting_capital_inr,
        "final_equity_inr": round(equity, 2),
        "net_profit_inr": round(equity - starting_capital_inr, 2),
        "return_pct": round(ret_pct, 2),
        "cagr_pct": round(cagr_pct, 2),
        "max_dd_inr": round(max_dd_inr, 2),
        "max_dd_pct": round(max_dd_pct, 2),
        "calmar": round(calmar, 2),
        "trades": total_n,
        "win_rate_pct": round(win_rate, 2),
        "profit_factor": round(pf, 2),
        "avg_net_r": round(avg_net_r, 4),
        "total_fees_inr": round(total_fees_inr, 2),
        "rejections": rejection_counts,
        "executed_trades": executed_trades,
    }


def run_tiebreak_invariance_audit(
    candidates: List[Dict[str, Any]],
    dfs: Dict[str, pd.DataFrame],
    specs: Dict[str, Dict[str, Any]],
    compounding: bool = True,
    exit_policy: str = "hybrid",
    n_shuffles: int = 100,
) -> Dict[str, Any]:
    """Audits queue-order invariance across QualityScore, Forward, Reversed, and N random shuffles."""
    r_q = run_event_driven_inr_portfolio(
        candidates, dfs, specs, compounding=compounding, exit_policy=exit_policy, tie_break="quality_score"
    )
    r_fwd = run_event_driven_inr_portfolio(
        candidates, dfs, specs, compounding=compounding, exit_policy=exit_policy, tie_break="forward"
    )
    r_rev = run_event_driven_inr_portfolio(
        candidates, dfs, specs, compounding=compounding, exit_policy=exit_policy, tie_break="reversed"
    )
    mc_equities = []
    for seed in range(n_shuffles):
        r_mc = run_event_driven_inr_portfolio(
            candidates,
            dfs,
            specs,
            compounding=compounding,
            exit_policy=exit_policy,
            tie_break="random",
            rng_seed=1000 + seed,
        )
        mc_equities.append(r_mc["final_equity_inr"])

    arr = np.array(mc_equities, dtype=float)
    return {
        "quality_score_equity_inr": r_q["final_equity_inr"],
        "forward_alpha_equity_inr": r_fwd["final_equity_inr"],
        "reversed_alpha_equity_inr": r_rev["final_equity_inr"],
        "mc_p05_equity_inr": round(float(np.percentile(arr, 5)), 2),
        "mc_median_equity_inr": round(float(np.median(arr)), 2),
        "mc_p95_equity_inr": round(float(np.percentile(arr, 95)), 2),
        "mc_profitable_pct": round(float(np.mean(arr > DEFAULT_BASE_CAPITAL_INR) * 100.0), 1),
    }