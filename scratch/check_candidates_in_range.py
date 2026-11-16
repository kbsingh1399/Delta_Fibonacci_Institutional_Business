import os
import sys
import MetaTrader5 as mt5
import pandas as pd
import numpy as np

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from engine.mt5_alpha_trader_monitor import (
    OPTIMAL_ALPHA_BASKET,
    calculate_ema,
    calculate_atr,
    compute_adx,
)

mt5.initialize()

print("=" * 110)
print("REAL-TIME FIBONACCI EMA CANDIDATE PROXIMITY & FORMING BAR AUDIT (21 ELITE BASKET)")
print("=" * 110)

candidates = []

for item in OPTIMAL_ALPHA_BASKET:
    sym = item["symbol"]
    cluster = item["cluster"]
    setup = item["setup"]
    rates = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_H4, 0, 1200)
    if rates is None or len(rates) < 250:
        continue
    
    df = pd.DataFrame(rates)
    c = df['close'].to_numpy(dtype=float)
    o = df['open'].to_numpy(dtype=float)
    h = df['high'].to_numpy(dtype=float)
    l = df['low'].to_numpy(dtype=float)
    
    e55 = calculate_ema(c, 55)
    e89 = calculate_ema(c, 89)
    e233 = calculate_ema(c, 233)
    atr = calculate_atr(h, l, c, 14)
    adx = compute_adx(h, l, c, 14)
    
    # Check current forming bar (index -1)
    curr_c = c[-1]
    curr_o = o[-1]
    curr_h = h[-1]
    curr_l = l[-1]
    curr_atr = atr[-1]
    curr_adx = adx[-1]
    
    rng = max(1e-6, curr_h - curr_l)
    body_top = max(curr_o, curr_c)
    body_bot = min(curr_o, curr_c)
    upper_wick = (curr_h - body_top) / rng
    lower_wick = (body_bot - curr_l) / rng
    
    # Distance to EMA 89 in ATR units
    dist_89_atr = abs(curr_c - e89[-1]) / curr_atr
    dist_55_atr = abs(curr_c - e55[-1]) / curr_atr
    min_dist_atr = min(dist_89_atr, dist_55_atr)
    
    # Check if price touched EMA 89/55 during the forming bar
    touched_89_low = curr_l <= e89[-1] * 1.002
    touched_89_high = curr_h >= e89[-1] * 0.998
    
    # Regime
    regime = "BULL" if e89[-1] > e233[-1] else "BEAR"
    
    candidates.append({
        "symbol": sym,
        "cluster": cluster,
        "setup": setup,
        "price": curr_c,
        "ema89": e89[-1],
        "dist_atr": min_dist_atr,
        "regime": regime,
        "lower_wick": lower_wick * 100,
        "upper_wick": upper_wick * 100,
        "touched_low": touched_89_low,
        "touched_high": touched_89_high,
        "adx": curr_adx,
    })

# Sort by proximity to EMA89/55 shelf
candidates.sort(key=lambda x: x["dist_atr"])

print(f"{'Symbol':<8} | {'Cluster':<16} | {'Price':<10} | {'EMA 89':<10} | {'Dist(ATR)':<10} | {'Regime':<7} | {'L-Wick%':<8} | {'U-Wick%':<8} | {'Status'}")
print("-" * 110)

for c in candidates:
    if c['dist_atr'] <= 0.05:
        status = "🔥 TOUCHING SHELF"
    elif c['dist_atr'] <= 0.25:
        status = "⚡ IN RANGE (TESTING)"
    elif c['dist_atr'] <= 0.50:
        status = "👀 APPROACHING"
    else:
        status = "PULLBACK / EXTENDED"
        
    print(f"{c['symbol']:<8} | {c['cluster']:<16} | {c['price']:<10.4f} | {c['ema89']:<10.4f} | {c['dist_atr']:<10.2f} | {c['regime']:<7} | {c['lower_wick']:<7.1f}% | {c['upper_wick']:<7.1f}% | {status}")
