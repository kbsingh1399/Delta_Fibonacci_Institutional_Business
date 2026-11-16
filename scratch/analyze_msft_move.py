import os
import sys
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)
import MetaTrader5 as mt5
import pandas as pd
import numpy as np
from engine.mt5_alpha_trader_monitor import calculate_ema, calculate_atr, compute_adx

def check():
    mt5.initialize()
    rates = mt5.copy_rates_from_pos("MSFT", mt5.TIMEFRAME_H4, 0, 1200)
    df = pd.DataFrame(rates)
    c = df['close'].to_numpy(dtype=float)
    o = df['open'].to_numpy(dtype=float)
    h = df['high'].to_numpy(dtype=float)
    l = df['low'].to_numpy(dtype=float)
    t = df['time'].to_numpy(dtype=int)

    ema34 = calculate_ema(c, 34)
    ema55 = calculate_ema(c, 55)
    ema89 = calculate_ema(c, 89)
    ema233 = calculate_ema(c, 233)
    atr = calculate_atr(h, l, c, 14)
    adx = compute_adx(h, l, c, 14)

    for i in range(len(df) - 4, len(df)):
        dt = pd.to_datetime(t[i], unit='s')
        rng = max(1e-6, h[i] - l[i])
        body_top = max(o[i], c[i])
        body_bot = min(o[i], c[i])
        upper_wick = (h[i] - body_top) / rng
        lower_wick = (body_bot - l[i]) / rng

        print("=" * 60)
        print(f"Bar {i} | Time: {dt} (Close: {dt + pd.Timedelta(hours=4)})")
        print(f"O: {o[i]:.2f}, H: {h[i]:.2f}, L: {l[i]:.2f}, C: {c[i]:.2f}")
        print(f"EMA34: {ema34[i]:.2f}, EMA55: {ema55[i]:.2f}, EMA89: {ema89[i]:.2f}, EMA233: {ema233[i]:.2f}")
        print(f"ATR: {atr[i]:.2f}, ADX: {adx[i]:.2f}")
        print(f"Lower Wick %: {lower_wick*100:.1f}%, Upper Wick %: {upper_wick*100:.1f}%")
        
        # Check canonical
        c_bull = (ema89[i] > ema233[i]) and (lower_wick >= 0.25) and (
            ((l[i] <= ema89[i] * 1.002 or l[i] <= ema55[i] * 1.002) and c[i] >= ema89[i]) or
            ((l[i] <= ema233[i] * 1.002) and c[i] >= ema233[i])
        )
        print(f"Canonical Bull Match: {c_bull}")

        # Check turbo_tri_tier
        turbo_sig = 0
        if ema89[i] > ema233[i] and c[i] >= ema233[i]:
            if lower_wick >= 0.25:
                if adx[i] >= 22 and (l[i] <= ema34[i] * 1.002 or l[i] <= ema55[i] * 1.002) and (c[i] >= ema55[i]):
                    turbo_sig = 1
                    print("Turbo Match: TIER 1 (TURBO)")
                elif (l[i] <= ema89[i] * 1.002) and (c[i] >= ema89[i]):
                    turbo_sig = 1
                    print("Turbo Match: TIER 2 (MID)")
                elif (l[i] <= ema233[i] * 1.002) and (c[i] >= ema233[i]):
                    turbo_sig = 1
                    print("Turbo Match: TIER 3 (SLOW)")
        print(f"Turbo Tri-Tier Match: {turbo_sig != 0}")

if __name__ == "__main__":
    check()
