#!/usr/bin/env python3
"""optimize_blueberry_basket.py: Full-Universe Institutional Sweep across all 156 Blueberry Assets.
Tests:
- Timeframes: 1H & 4H
- Models: Model 1 (Pullback Rejection) & Model 2 (Dwell & Reclaim Spring)
- EMAs: 50, 55, 65, 89, 100, 160, 200, 233, 277, 300, 400
- Exit Modes: trail_0.5r, trail_1.0r, trail_1.5r, fixed_2.5r, fixed_3.5r, fixed_4.0r
- Anti-Chop Gates: Kaufman ER >= 0.28, ATR-banded crossings < 2
- Microstructure Guards: 1.5 ATR Stop, 12-bar post-exit cooldown, strictly causal (enter on bar open).

Uses multiprocessing across all CPU cores for ultra-fast execution.
"""

import os
import sys
import json
import time
from datetime import datetime, timezone
import multiprocessing as mp
from typing import Dict, Any, List, Optional
import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "Blueberry_Data")
AUDIT_DIR = os.path.join(BASE_DIR, "audit")
os.makedirs(AUDIT_DIR, exist_ok=True)

EMA_LIST = [50, 55, 65, 89, 100, 160, 200, 233, 277, 300, 400]
EXIT_MODES = ["trail_0.5r", "trail_1.0r", "trail_1.5r", "fixed_2.5r", "fixed_3.5r", "fixed_4.0r"]
MODELS = ["MODEL_1", "MODEL_2"]
TIMEFRAMES = ["1H", "4H"]

def get_cluster(sym: str) -> str:
    s = sym.upper()
    if any(k in s for k in ['BTC', 'ETH', 'SOL', 'DOG', 'ADA', 'XLM', 'LTC', 'XRP', 'BNB', 'DOT', 'AVX', 'LINK', 'MAT', 'UNI', 'BCH', 'ATOM', 'NEAR', 'FIL', 'APE', 'AXS', 'SAND', 'CHZ', 'MANA', 'CRV', 'AAVE', 'MKR', 'SNX', 'GRT', 'DYDX', 'COMP', '1INCH', 'BAT', 'ENJ', 'ZEC', 'DASH', 'EOS', 'XTZ', 'OMG', 'KSM', 'FTM', 'ICP', 'ALGO', 'TRX', 'VET', 'THETA', 'XMR', 'FLOW', 'GALA', 'QNT', 'LDO', 'ARB', 'OP', 'APT', 'SUI', 'TIA', 'SEI', 'INJ', 'RUNE', 'KAVA', 'MINA', 'STX', 'IMX', 'RNDR', 'PEPE', 'SHIB', 'FLOKI', 'BONK', 'WIF', 'JUP', 'PYTH', 'ORDI', 'STRK', 'BLUR', 'MEME', 'ZRO', 'NOT', 'TON', 'IO', 'ZK', 'TAO', 'RENDER', 'FET', 'AGIX', 'OCEAN', 'JASMY', 'NEO', 'IOTA', 'KNC', 'LRC', 'ZRX', 'YFI', 'SKL', 'ARW', 'BAR', 'ATM', 'AVE', 'ALG']):
        return 'Crypto'
    if any(k in s for k in ['XAU', 'XAG', 'XPT', 'XPD', 'WTI', 'BRENT', 'OIL', 'GAS', 'COPPER', 'ALUMINIUM', 'NICKEL', 'LEAD', 'ZINC']):
        return 'Commodity_Energy'
    if any(k in s for k in ['DJ30', 'SP500', 'NAS100', 'HK50', 'GER40', 'UK100', 'AU200', 'JP225', 'EU50', 'US2000', 'FRA40', 'SPA35', 'SWI20', 'NETH25', 'CHI50']):
        return 'Indices'
    if 'JPY' in s:
        return 'Forex_JPY'
    if any(k in s for k in ['EURUSD', 'GBPUSD', 'AUDUSD', 'NZDUSD', 'USDCAD', 'USDCHF']):
        return 'Forex_Majors'
    return 'Forex_Crosses_Exotics'

def calculate_ema(arr: np.ndarray, span: int) -> np.ndarray:
    return pd.Series(arr).ewm(span=span, adjust=False).mean().to_numpy()

def calculate_atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> np.ndarray:
    n = len(closes)
    tr = np.zeros(n)
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
    return pd.Series(tr).rolling(period).mean().bfill().to_numpy()

def simulate_series(c: np.ndarray, h: np.ndarray, l: np.ndarray, o: np.ndarray,
                    ema_fast: np.ndarray, atr: np.ndarray,
                    tf: str, primary_model: str, target_ema: int, exit_mode: str) -> Optional[Dict[str, Any]]:
    n = len(c)
    if n < (target_ema + 80):
        return None

    # Precalculate Kaufman Efficiency Ratio (ER >= 0.28)
    er_lb = 10
    direction = np.abs(c - np.roll(c, er_lb))
    diffs = np.abs(np.diff(c, prepend=c[0]))
    volatility = pd.Series(diffs).rolling(er_lb).sum().to_numpy()
    er = np.divide(direction, volatility, out=np.zeros_like(direction), where=volatility > 0)
    er[:er_lb] = 0.5

    # Precalculate ATR-Banded Crossings Filter (< 2 crossings in 12 bars)
    upper_band = ema_fast + 0.25 * atr
    lower_band = ema_fast - 0.25 * atr
    prev_c = np.roll(c, 1)
    prev_c[0] = c[0]
    is_cross = ((c > upper_band) & (prev_c <= lower_band)) | ((c < lower_band) & (prev_c >= upper_band))
    crossings = pd.Series(is_cross.astype(int)).rolling(12).sum().to_numpy()

    # Precalculate Wick Geometry
    rng = np.maximum(1e-6, h - l)
    body_top = np.maximum(o, c)
    body_bot = np.minimum(o, c)
    upper_wick = (h - body_top) / rng
    lower_wick = (body_bot - l) / rng

    # Precalculate Model Features
    if primary_model == "MODEL_1":
        slope_bars = 4 if tf == "1H" else 3
        prev_ema_slope = np.roll(ema_fast, slope_bars)
        norm_slope = np.divide(ema_fast - prev_ema_slope, slope_bars * atr, out=np.zeros_like(ema_fast), where=atr > 0)
        came_from_above = (o >= ema_fast * 0.998) | (prev_c >= np.roll(ema_fast, 1) * 0.998)
        came_from_below = (o <= ema_fast * 1.002) | (prev_c <= np.roll(ema_fast, 1) * 1.002)
        long_cond = (er >= 0.28) & (crossings < 2) & (norm_slope >= 0.02) & came_from_above & (lower_wick >= 0.25) & (l <= ema_fast * 1.002) & (c >= ema_fast + 0.05 * atr)
        short_cond = (er >= 0.28) & (crossings < 2) & (norm_slope <= -0.02) & came_from_below & (upper_wick >= 0.25) & (h >= ema_fast * 0.998) & (c <= ema_fast - 0.05 * atr)
    else:  # MODEL_2
        dwell_lb = 35 if tf == "4H" else 100
        min_dwell = 3 if tf == "4H" else 12
        max_dwell = 25 if tf == "4H" else 60
        min_clearance = 0.12 * atr
        ema_prev3 = np.roll(ema_fast, 3)
        recent_below = pd.Series((c < ema_fast).astype(int)).rolling(dwell_lb).sum().to_numpy()
        recent_above = pd.Series((c > ema_fast).astype(int)).rolling(dwell_lb).sum().to_numpy()
        long_cond = (er >= 0.28) & (crossings < 2) & (ema_fast >= ema_prev3 * 0.999) & (lower_wick >= 0.33) & (l <= ema_fast) & (c >= ema_fast + min_clearance) & (recent_below >= min_dwell) & (recent_below <= max_dwell)
        short_cond = (er >= 0.28) & (crossings < 2) & (ema_fast <= ema_prev3 * 1.001) & (upper_wick >= 0.33) & (h >= ema_fast) & (c <= ema_fast - min_clearance) & (recent_above >= min_dwell) & (recent_above <= max_dwell)

    # Determine target RR based on exit mode
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

    # Simulation State Machine
    in_pos = False
    pos_side = 0
    pos_entry = 0.0
    pos_sl = 0.0
    pos_tp = 0.0
    pos_rd = 1.0
    pos_mfe = 0.0
    cooldown_until = 0
    cooldown_bars = 12

    trades_r: List[float] = []

    start_idx = target_ema + 50
    for i in range(start_idx, n - 1):
        if in_pos:
            bar_h = h[i]
            bar_l = l[i]

            # Update MFE
            fav_dist = (bar_h - pos_entry) if pos_side == 1 else (pos_entry - bar_l)
            bar_mfe = fav_dist / pos_rd if pos_rd > 0 else 0.0
            if bar_mfe > pos_mfe:
                pos_mfe = bar_mfe

            # Dynamic Trailing SL Logic
            new_sl = pos_sl
            if exit_mode == "trail_1.5r" and pos_mfe >= 2.5:
                locked_r = pos_mfe - 1.5
                target_sl = (pos_entry + locked_r * pos_rd) if pos_side == 1 else (pos_entry - locked_r * pos_rd)
                new_sl = max(pos_sl, target_sl) if pos_side == 1 else min(pos_sl, target_sl)
            elif exit_mode == "trail_1.0r" and pos_mfe >= 2.0:
                locked_r = pos_mfe - 1.0
                target_sl = (pos_entry + locked_r * pos_rd) if pos_side == 1 else (pos_entry - locked_r * pos_rd)
                new_sl = max(pos_sl, target_sl) if pos_side == 1 else min(pos_sl, target_sl)
            elif exit_mode == "trail_0.5r" and pos_mfe >= 1.5:
                locked_r = pos_mfe - 0.5
                target_sl = (pos_entry + locked_r * pos_rd) if pos_side == 1 else (pos_entry - locked_r * pos_rd)
                new_sl = max(pos_sl, target_sl) if pos_side == 1 else min(pos_sl, target_sl)
            pos_sl = new_sl

            exited = False
            exit_r = 0.0
            if pos_side == 1 and bar_l <= pos_sl:
                exited = True
                exit_r = (pos_sl - pos_entry) / pos_rd
            elif pos_side == -1 and bar_h >= pos_sl:
                exited = True
                exit_r = (pos_entry - pos_sl) / pos_rd
            elif pos_tp > 0 and ((pos_side == 1 and bar_h >= pos_tp) or (pos_side == -1 and bar_l <= pos_tp)):
                exited = True
                exit_r = (pos_tp - pos_entry) / pos_rd if pos_side == 1 else (pos_entry - pos_tp) / pos_rd

            if exited:
                in_pos = False
                cooldown_until = i + cooldown_bars
                trades_r.append(exit_r)
                continue

        # Check Signal on bar i
        if not in_pos and i >= cooldown_until:
            sig = 0
            if long_cond[i]:
                sig = 1
            elif short_cond[i]:
                sig = -1

            if sig != 0:
                next_bar = i + 1
                entry_p = o[next_bar]
                a_bar = atr[i]
                rd = 1.5 * a_bar
                if rd <= 1e-6:
                    continue
                sl_p = (entry_p - rd) if sig == 1 else (entry_p + rd)
                tp_p = (entry_p + target_rr * rd) if sig == 1 else (entry_p - target_rr * rd)

                in_pos = True
                pos_side = sig
                pos_entry = entry_p
                pos_sl = sl_p
                pos_tp = tp_p
                pos_rd = rd
                pos_mfe = 0.0

    if len(trades_r) < 10:
        return None

    r_arr = np.array(trades_r)
    wins = r_arr[r_arr > 0]
    losses = r_arr[r_arr <= 0]
    win_count = len(wins)
    total_trades = len(r_arr)
    win_rate = (win_count / total_trades) * 100.0
    gross_win = float(np.sum(wins)) if win_count > 0 else 0.0
    gross_loss = float(np.abs(np.sum(losses))) if len(losses) > 0 else 1e-6
    profit_factor = gross_win / gross_loss if gross_loss > 0 else 1.0
    total_r = float(np.sum(r_arr))

    # Calculate Max Drawdown in R
    equity_curve = np.cumsum(r_arr)
    peak = np.maximum.accumulate(equity_curve)
    dd = peak - equity_curve
    max_dd = float(np.max(dd)) if len(dd) > 0 else 0.0

    # Score: balanced risk-adjusted alpha
    # Favor high total R, high PF, manageable DD, sufficient trade sample
    score = (total_r / (max_dd + 2.0)) * (profit_factor) * np.sqrt(total_trades)

    return {
        "tf": tf,
        "primary_model": primary_model,
        "ema": target_ema,
        "exit_mode": exit_mode,
        "trades": total_trades,
        "win_rate": round(win_rate, 1),
        "profit_factor": round(profit_factor, 2),
        "total_r": round(total_r, 2),
        "max_dd": round(max_dd, 2),
        "expectancy_r": round(total_r / total_trades, 3),
        "score": round(score, 2),
    }

def analyze_single_symbol(symbol: str) -> Optional[Dict[str, Any]]:
    """Sweeps all parameter combinations for one symbol and returns the champion configuration."""
    cluster = get_cluster(symbol)
    best_res = None
    best_score = -999.0

    # Load 1H and 4H Data
    data_cache = {}
    for tf in TIMEFRAMES:
        pq = os.path.join(DATA_DIR, f"{symbol}_{tf}.parquet")
        if not os.path.exists(pq):
            continue
        try:
            df = pd.read_parquet(pq)
            if len(df) < 200:
                continue
            df["time_sec"] = pd.to_datetime(df["time"]).astype("int64") // 10**9
            df = df.sort_values("time_sec").reset_index(drop=True)
            c = df["close"].to_numpy(dtype=float)
            h = df["high"].to_numpy(dtype=float)
            l = df["low"].to_numpy(dtype=float)
            o = df["open"].to_numpy(dtype=float)
            atr = calculate_atr(h, l, c, 14)
            data_cache[tf] = (c, h, l, o, atr)
        except Exception:
            continue

    if not data_cache:
        return None

    # Sweep all parameters
    for tf, (c, h, l, o, atr) in data_cache.items():
        for ema_period in EMA_LIST:
            if len(c) < (ema_period + 80):
                continue
            ema_arr = calculate_ema(c, ema_period)
            for model in MODELS:
                for exit_m in EXIT_MODES:
                    res = simulate_series(c, h, l, o, ema_arr, atr, tf, model, ema_period, exit_m)
                    if res is not None:
                        # Statistical filters:
                        # Must have positive Total R and PF >= 1.05
                        if res["total_r"] > 0 and res["profit_factor"] >= 1.05:
                            if res["score"] > best_score:
                                best_score = res["score"]
                                best_res = res

    if best_res is not None:
        best_res["symbol"] = symbol
        best_res["cluster"] = cluster
        return best_res

    return None

def main():
    t0 = time.time()
    files = os.listdir(DATA_DIR)
    symbols = sorted(list(set(f.split('_')[0] for f in files if f.endswith('.parquet'))))
    total_syms = len(symbols)
    print("=" * 85)
    print(f"🚀 INSTITUTIONAL BLUEBERRY ASSET OPTIMIZER (156 ASSETS)")
    print(f"Target Universe: {total_syms} Symbols across 6 Uncorrelated Clusters")
    print(f"Combinations per asset: {len(TIMEFRAMES)} TFs x {len(MODELS)} Models x {len(EMA_LIST)} EMAs x {len(EXIT_MODES)} Exits = {len(TIMEFRAMES)*len(MODELS)*len(EMA_LIST)*len(EXIT_MODES)} Combinations")
    print(f"Total Parameter Tests: {total_syms * len(TIMEFRAMES)*len(MODELS)*len(EMA_LIST)*len(EXIT_MODES):,} simulations")
    print("=" * 85)

    workers = min(12, mp.cpu_count())
    print(f">> Launching parallel optimization pool with {workers} CPU workers...")
    with mp.Pool(processes=workers) as pool:
        results = pool.map(analyze_single_symbol, symbols)

    valid_champions = [r for r in results if r is not None]
    valid_champions.sort(key=lambda x: x["score"], reverse=True)

    elapsed = time.time() - t0
    print(f"✅ Optimization sweep complete in {elapsed:.1f}s!")
    print(f"Found {len(valid_champions)} positive-alpha champions with PF >= 1.05 and Total R > 0.")

    # Save all champions to JSON
    out_json = os.path.join(AUDIT_DIR, "blueberry_full_universe_champions.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(valid_champions, f, indent=2)
    print(f"💾 Full universe champions saved to: {out_json}")

    # Display Top Champions per Cluster
    by_cluster = {}
    for c in valid_champions:
        by_cluster.setdefault(c["cluster"], []).append(c)

    print("\n" + "=" * 85)
    print("🏆 TOP CHAMPIONS BY CLUSTER (SORTED BY RISK-ADJUSTED SCORE)")
    print("=" * 85)
    print(f"{'Symbol':<12} {'Cluster':<22} {'TF':<3} {'Model':<8} {'EMA':<4} {'Exit':<11} {'Trades':<7} {'Win%':<6} {'PF':<5} {'Total R':<8} {'Max DD':<7} {'Score':<7}")
    print("-" * 105)

    basket_candidates = []
    # Pick top 3-4 per cluster to form a pristine, diversified 20-22 asset alpha basket
    for cl_name, cl_list in sorted(by_cluster.items()):
        cl_list.sort(key=lambda x: x["score"], reverse=True)
        print(f"\n--- Cluster: {cl_name} ({len(cl_list)} qualifying assets) ---")
        for idx, item in enumerate(cl_list[:6]):
            print(f"{item['symbol']:<12} {item['cluster']:<22} {item['tf']:<3} {item['primary_model']:<8} {item['ema']:<4} {item['exit_mode']:<11} {item['trades']:<7} {item['win_rate']:<6.1f} {item['profit_factor']:<5.2f} {item['total_r']:<8.2f} {item['max_dd']:<7.2f} {item['score']:<7.1f}")
            if idx < 4:  # Take top 3-4 per cluster
                basket_candidates.append(item)

    # Save Chosen Elite Basket
    basket_file = os.path.join(AUDIT_DIR, "blueberry_optimal_alpha_basket.json")
    with open(basket_file, "w", encoding="utf-8") as f:
        json.dump(basket_candidates, f, indent=2)
    print(f"\n💾 Chosen Elite Alpha Basket saved to: {basket_file} ({len(basket_candidates)} assets)")

if __name__ == "__main__":
    main()
