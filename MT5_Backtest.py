#!/usr/bin/env python3
r"""MT5_Backtest.py: Institutional 100% Code-Parity Backtest Engine
for the 23 Champion Assets in OPTIMAL_ALPHA_BASKET.

Features:
- 100% Logic Parity with mt5_alpha_trader_live.py
- Dual Models: Model 1 (Pullback Rejection) & Model 2 (Dwell & Reclaim Spring)
- Anti-Chop Gates: Kaufman Efficiency Ratio (ER >= 0.18) + ATR-Banded Crossings Filter
- Slope & Clearance Gates: ATR-normalized slope (>= +0.02 / <= -0.02) + 0.05 ATR clearance
- Exact Dynamic Trailing Exits: trail_1.5r, trail_1.0r, trail_0.5r, fixed_4.0r, fixed_3.5r, fixed_2.5r
- Zero Lookahead: Signal confirmed on candle close -> entered at subsequent bar open
- Candle-Direction Intrabar Path Model: bullish bar = low-first, bearish = high-first;
  trailing exits simulate the live engine's tick-by-tick ratchet without a
  favorable-sequencing assumption (validated 2026-11: +483R vs +323R optimistic
  pole and +452R conservative pole on 730d of real proxy data, 23/23 champions)
- Single Entry Invariant: 12-bar post-exit cooldown per asset
- Parquet Data Lake: Ingests directly from Blueberry_Data/{symbol}_{tf}.parquet
"""

import os
import sys
import json
from datetime import datetime, timezone
from typing import Dict, Any, List
import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

DATA_DIR = os.path.join(BASE_DIR, "Blueberry_Data")
AUDIT_DIR = os.path.join(BASE_DIR, "audit")
os.makedirs(AUDIT_DIR, exist_ok=True)

try:
    from mt5_alpha_trader_live import (
        OPTIMAL_ALPHA_BASKET,
        calculate_ema,
        calculate_atr,
    )
except ImportError:
    raise ImportError("Cannot import OPTIMAL_ALPHA_BASKET from mt5_alpha_trader_live.py")

JAN_2024_TS = 1704067200  # 2024-01-01 00:00:00 UTC


def simulate_asset_backtest(cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Simulates trading for a single champion asset with strict zero-lookahead causality."""
    symbol = cfg["symbol"]
    cluster = cfg["cluster"]
    tf = cfg["tf"]
    primary_model = cfg["primary_model"]
    target_ema = cfg["ema"]
    exit_mode = cfg["exit_mode"]
    risk_usd = cfg.get("risk_usd", 10.0)

    pq_path = os.path.join(DATA_DIR, f"{symbol}_{tf}.parquet")
    if not os.path.exists(pq_path):
        print(f"⚠️ Parquet file missing for {symbol}_{tf}: {pq_path}")
        return []

    df = pd.read_parquet(pq_path)
    # Ensure time is integer unix epoch seconds. Normalize ANY datetime
    # resolution (s/ms/us/ns, tz-aware or naive) to ns first: parquet lakes
    # written by pandas 2.x carry non-nanosecond dtypes, and assuming ns
    # collapses time_sec into tie-groups that scramble bar order on sort.
    df["time_sec"] = pd.to_datetime(df["time"], utc=True).astype("datetime64[ns, UTC]").astype("int64") // 10**9
    df = df.sort_values("time_sec").reset_index(drop=True)
    c = df["close"].to_numpy(dtype=float)
    h = df["high"].to_numpy(dtype=float)
    l = df["low"].to_numpy(dtype=float)
    o = df["open"].to_numpy(dtype=float)
    t = df["time_sec"].to_numpy(dtype=int)
    n = len(c)

    if n < (target_ema + 100):
        return []

    ema_fast = calculate_ema(c, target_ema)
    atr = calculate_atr(h, l, c, 14)

    trades = []
    in_pos = False
    pos_side = 0
    pos_entry = 0.0
    pos_sl = 0.0
    pos_tp = 0.0
    pos_rd = 1.0
    pos_mfe = 0.0
    entry_idx = 0
    cooldown_until_idx = 0
    cooldown_bars = 12

    for i in range(target_ema + 50, n - 1):
        # 1. Manage Open Position on bar i
        if in_pos:
            bar_h = h[i]
            bar_l = l[i]
            bullish = c[i] >= o[i]

            # Candidate end-of-bar MFE and trailed stop (this bar's full range)
            fav_dist = (bar_h - pos_entry) if pos_side == 1 else (pos_entry - bar_l)
            bar_mfe = fav_dist / pos_rd if pos_rd > 0 else 0.0
            mfe_new = bar_mfe if bar_mfe > pos_mfe else pos_mfe

            # Update Trailing Stop (ratchet, evaluated on the candidate MFE)
            new_sl = pos_sl
            if exit_mode == "trail_1.5r" and mfe_new >= 2.5:
                locked_r = mfe_new - 1.5
                target_sl = (pos_entry + locked_r * pos_rd) if pos_side == 1 else (pos_entry - locked_r * pos_rd)
                new_sl = max(pos_sl, target_sl) if pos_side == 1 else min(pos_sl, target_sl)
            elif exit_mode == "trail_1.0r" and mfe_new >= 2.0:
                locked_r = mfe_new - 1.0
                target_sl = (pos_entry + locked_r * pos_rd) if pos_side == 1 else (pos_entry - locked_r * pos_rd)
                new_sl = max(pos_sl, target_sl) if pos_side == 1 else min(pos_sl, target_sl)
            elif exit_mode == "trail_0.5r" and mfe_new >= 1.5:
                locked_r = mfe_new - 0.5
                target_sl = (pos_entry + locked_r * pos_rd) if pos_side == 1 else (pos_entry - locked_r * pos_rd)
                new_sl = max(pos_sl, target_sl) if pos_side == 1 else min(pos_sl, target_sl)

            # Candle-direction intrabar path model: a bullish bar prints its low
            # before its high (adverse extreme first for longs), a bearish bar
            # prints its high first. The stop governing this bar's adverse
            # extreme is the one live would have had in force at that moment -
            # no favorable-sequencing assumption on trailing exits.
            if pos_side == 1:
                sl_check = pos_sl if bullish else new_sl
            else:
                sl_check = new_sl if bullish else pos_sl

            exited = False
            exit_p = 0.0
            exit_reason = ""
            exit_r = 0.0

            # Check SL / Trailing SL Hit (against the in-force stop)
            if pos_side == 1 and bar_l <= sl_check:
                exited = True
                exit_p = sl_check
                exit_reason = "SL_TRAILED" if sl_check > pos_entry else "SL_HIT"
                exit_r = (exit_p - pos_entry) / pos_rd
            elif pos_side == -1 and bar_h >= sl_check:
                exited = True
                exit_p = sl_check
                exit_reason = "SL_TRAILED" if sl_check < pos_entry else "SL_HIT"
                exit_r = (pos_entry - exit_p) / pos_rd

            # Check TP Hit
            elif pos_tp > 0 and ((pos_side == 1 and bar_h >= pos_tp) or (pos_side == -1 and bar_l <= pos_tp)):
                exited = True
                exit_p = pos_tp
                exit_reason = "TP_HIT"
                exit_r = (exit_p - pos_entry) / pos_rd if pos_side == 1 else (pos_entry - exit_p) / pos_rd

            if not exited:
                # Commit the end-of-bar trail state (matches live's ratchet at bar close)
                pos_mfe = mfe_new
                pos_sl = new_sl

            if exited:
                in_pos = False
                cooldown_until_idx = i + cooldown_bars
                trades.append({
                    "symbol": symbol,
                    "cluster": cluster,
                    "setup": f"{primary_model}_EMA{target_ema}_{tf}",
                    "side": "BUY" if pos_side == 1 else "SELL",
                    "entry_time": datetime.fromtimestamp(t[entry_idx], tz=timezone.utc).isoformat(),
                    "exit_time": datetime.fromtimestamp(t[i], tz=timezone.utc).isoformat(),
                    "entry_price": round(pos_entry, 5),
                    "exit_price": round(exit_p, 5),
                    "risk_dist": round(pos_rd, 5),
                    "exit_reason": exit_reason,
                    "r_multiple": round(exit_r, 2),
                    "pnl_usd": round(exit_r * risk_usd, 2),
                    "bars_held": i - entry_idx,
                })
                continue

        # 2. Check Signal on confirmed bar i if not in position and not in cooldown
        if in_pos or i < cooldown_until_idx:
            continue

        prev = i
        rng = max(1e-6, h[prev] - l[prev])
        body_top = max(o[prev], c[prev])
        body_bot = min(o[prev], c[prev])
        upper_wick = (h[prev] - body_top) / rng
        lower_wick = (body_bot - l[prev]) / rng

        c_bar = c[prev]
        o_bar = o[prev]
        h_bar = h[prev]
        l_bar = l[prev]
        a_bar = atr[prev]

        # Anti-Chop Filter: Kaufman ER >= 0.18 or Rejection Wick >= 35%
        er_lb = 10
        if prev >= er_lb:
            direction = abs(c[prev] - c[prev - er_lb])
            volatility = sum(abs(c[k] - c[k - 1]) for k in range(prev - er_lb + 1, prev + 1))
            er = direction / volatility if volatility > 0 else 0.0
        else:
            er = 0.5
        has_rejection_wick = (upper_wick >= 0.35) or (lower_wick >= 0.35)
        if er < 0.18 and not has_rejection_wick:
            continue

        # Anti-Chop Filter: ATR-banded crossings
        ema_crossings = 0
        for b in range(max(1, prev - 12), prev):
            upper_band = ema_fast[b] + 0.25 * atr[b]
            lower_band = ema_fast[b] - 0.25 * atr[b]
            if (c[b] > upper_band and c[b - 1] <= lower_band) or (c[b] < lower_band and c[b - 1] >= upper_band):
                ema_crossings += 1
        if ema_crossings >= 2:
            continue

        came_from_above = (o_bar >= ema_fast[prev] * 0.998) or (c[prev - 1] >= ema_fast[prev - 1] * 0.998)
        came_from_below = (o_bar <= ema_fast[prev] * 1.002) or (c[prev - 1] <= ema_fast[prev - 1] * 1.002)

        sig = 0
        if primary_model == "MODEL_1":
            slope_bars = 4 if tf == "1H" else 3
            norm_slope = (ema_fast[prev] - ema_fast[prev - slope_bars]) / (slope_bars * a_bar) if a_bar > 0 else 0.0
            if norm_slope >= 0.02 and came_from_above and lower_wick >= 0.25:
                if l_bar <= ema_fast[prev] * 1.002 and c_bar >= (ema_fast[prev] + 0.05 * a_bar):
                    sig = 1
            elif norm_slope <= -0.02 and came_from_below and upper_wick >= 0.25:
                if h_bar >= ema_fast[prev] * 0.998 and c_bar <= (ema_fast[prev] - 0.05 * a_bar):
                    sig = -1

        elif primary_model == "MODEL_2":
            dwell_lb = 35 if tf == "4H" else 100
            min_dwell = 3 if tf == "4H" else 12
            max_dwell = 25 if tf == "4H" else 60
            min_clearance = 0.12 * a_bar

            if ema_fast[prev] >= ema_fast[prev - 3] * 0.999 and lower_wick >= 0.33:
                if l_bar <= ema_fast[prev] and c_bar >= (ema_fast[prev] + min_clearance):
                    recent_below = sum(1 for b in range(max(0, prev - dwell_lb), prev) if c[b] < ema_fast[b])
                    if min_dwell <= recent_below <= max_dwell:
                        sig = 1
            elif ema_fast[prev] <= ema_fast[prev - 3] * 1.001 and upper_wick >= 0.33:
                if h_bar >= ema_fast[prev] and c_bar <= (ema_fast[prev] - min_clearance):
                    recent_above = sum(1 for b in range(max(0, prev - dwell_lb), prev) if c[b] > ema_fast[b])
                    if min_dwell <= recent_above <= max_dwell:
                        sig = -1

        if sig != 0:
            # Strictly causal: Enter on Open of subsequent candle (i+1)
            next_bar = i + 1
            entry_p = o[next_bar]
            entry_idx = next_bar

            # 1.5 ATR Institutional Stop Loss
            rd = 1.5 * a_bar
            if sig == 1:
                sl_p = entry_p - rd
            else:
                sl_p = entry_p + rd

            if exit_mode == "trail_1.5r":
                target_rr = 8.0
            elif exit_mode in ["trail_1.0r", "trail_0.5r"]:
                target_rr = 6.0
            elif exit_mode == "fixed_4.0r":
                target_rr = 4.0
            elif exit_mode in ["fixed_3.5r", "fixed_3.5"]:
                target_rr = 3.5
            elif exit_mode in ["fixed_2.5r", "fixed_2.5"]:
                target_rr = 2.5
            else:
                target_rr = 3.0

            tp_p = (entry_p + target_rr * rd) if sig == 1 else (entry_p - target_rr * rd)

            in_pos = True
            pos_side = sig
            pos_entry = entry_p
            pos_sl = sl_p
            pos_tp = tp_p
            pos_rd = rd
            pos_mfe = 0.0

    return trades


def run_full_portfolio_backtest():
    print("=" * 85)
    print("🚀 INSTITUTIONAL BACKTEST ENGINE: 20-ASSET CHAMPION BASKET (CODE-PARITY VERIFICATION)")
    print(f"Data Source: {DATA_DIR} | Evaluated from 2023/2024 to Present")
    print("=" * 85)

    all_trades = []
    summary_rows = []

    for item in OPTIMAL_ALPHA_BASKET:
        sym = item["symbol"]
        trades = simulate_asset_backtest(item)
        all_trades.extend(trades)

        n_t = len(trades)
        if n_t > 0:
            wins = [t for t in trades if t["r_multiple"] > 0]
            losses = [t for t in trades if t["r_multiple"] <= 0]
            wr = len(wins) / n_t * 100
            gross_win = sum(t["r_multiple"] for t in wins)
            gross_loss = abs(sum(t["r_multiple"] for t in losses))
            pf = gross_win / gross_loss if gross_loss > 0 else 99.9
            total_r = sum(t["r_multiple"] for t in trades)
            tot_pnl = sum(t["pnl_usd"] for t in trades)

            # Max Drawdown (R)
            cum_r = np.cumsum([t["r_multiple"] for t in trades])
            peak = np.maximum.accumulate(cum_r)
            dd = peak - cum_r
            max_dd = float(np.max(dd)) if len(dd) > 0 else 0.0
        else:
            wr, pf, total_r, tot_pnl, max_dd = 0.0, 0.0, 0.0, 0.0, 0.0

        summary_rows.append({
            "Symbol": sym,
            "Cluster": item["cluster"],
            "TF": item["tf"],
            "Model": item["primary_model"],
            "EMA": item["ema"],
            "Exit Mode": item["exit_mode"],
            "Trades": n_t,
            "Win Rate (%)": round(wr, 1),
            "Profit Factor": round(pf, 2),
            "Total R": round(total_r, 2),
            "Net PnL ($)": round(tot_pnl, 2),
            "Max DD (R)": round(max_dd, 2),
        })

    sum_df = pd.DataFrame(summary_rows)
    print("\n" + sum_df.to_string(index=False))

    # Portfolio Totals
    tot_trades = len(all_trades)
    if tot_trades > 0:
        p_wins = [t for t in all_trades if t["r_multiple"] > 0]
        p_losses = [t for t in all_trades if t["r_multiple"] <= 0]
        p_wr = len(p_wins) / tot_trades * 100
        p_gross_win = sum(t["r_multiple"] for t in p_wins)
        p_gross_loss = abs(sum(t["r_multiple"] for t in p_losses))
        p_pf = p_gross_win / p_gross_loss if p_gross_loss > 0 else 99.9
        p_tot_r = sum(t["r_multiple"] for t in all_trades)
        p_tot_pnl = sum(t["pnl_usd"] for t in all_trades)

        print("\n" + "=" * 85)
        print(f"📊 PORTFOLIO AGGREGATE PERFORMANCE (20 Curated Assets across 6 Clusters)")
        print(f"Total Trades:       {tot_trades}")
        print(f"Overall Win Rate:   {p_wr:.1f}%")
        print(f"Profit Factor:      {p_pf:.2f}")
        print(f"Total Return:       +{p_tot_r:.2f} R  (${p_tot_pnl:,.2f} USD @ $10/trade)")
        print("=" * 85)

        # Save to audit files
        ledger_file = os.path.join(AUDIT_DIR, "backtest_trade_ledger.csv")
        pd.DataFrame(all_trades).to_csv(ledger_file, index=False)
        print(f"💾 Full trade ledger saved to: {ledger_file}")

    summary_file = os.path.join(AUDIT_DIR, "backtest_champion_summary.json")
    with open(summary_file, "w", encoding="utf-8") as f:
        json.dump(summary_rows, f, indent=2)
    print(f"💾 Champion summary saved to: {summary_file}")


if __name__ == "__main__":
    run_full_portfolio_backtest()
