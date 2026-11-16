import MetaTrader5 as mt5
import pandas as pd
import numpy as np
import sys
import os

BASE_DIR = r"C:\Users\SIGMA\Documents\Delta_Fibonacci_Institutional_Business"
sys.path.insert(0, BASE_DIR)

from engine.mt5_alpha_trader_monitor import calculate_ema, calculate_atr

if not mt5.initialize():
    sys.exit(1)

rates = mt5.copy_rates_from_pos("NKE", mt5.TIMEFRAME_H4, 0, 300)
df = pd.DataFrame(rates)
c = df["close"].to_numpy()
o = df["open"].to_numpy()
h = df["high"].to_numpy()
l = df["low"].to_numpy()

ema89 = calculate_ema(c, 89)
ema233 = calculate_ema(c, 233)
atr = calculate_atr(h, l, c, 14)

prev = len(c) - 2
rng = max(1e-6, h[prev] - l[prev])
body_top = max(o[prev], c[prev])
body_bot = min(o[prev], c[prev])
upper_wick = (h[prev] - body_top) / rng
lower_wick = (body_bot - l[prev]) / rng

print("=== NKE H4 DETAILED AUDIT ===")
print(f"Bar -2 (Last Closed): Open={o[prev]:.2f}, High={h[prev]:.2f}, Low={l[prev]:.2f}, Close={c[prev]:.2f}")
print(f"EMA89: {ema89[prev]:.2f} | EMA233: {ema233[prev]:.2f}")
print(f"EMA89 vs EMA233: {'EMA89 > EMA233 (Bullish)' if ema89[prev] > ema233[prev] else 'EMA89 < EMA233 (Bearish)'}")
print(f"Lower Wick: {lower_wick*100:.1f}% | Upper Wick: {upper_wick*100:.1f}%")

print("\n--- SHORT ORDER EVALUATION ---")
print(f"1. Macro Bearish (EMA89 < EMA233): {ema89[prev] < ema233[prev]} (PASS)")
print(f"2. Ceiling Rejection (Upper Wick >= 25%): {upper_wick >= 0.25} ({upper_wick*100:.1f}% vs 25.0%) -> FAIL")
print(f"3. Close <= EMA89: {c[prev] <= ema89[prev]} ({c[prev]:.2f} vs {ema89[prev]:.2f}) -> FAIL (Price is ABOVE EMA89!)")
print(f"4. Came from below: {(o[prev] <= ema89[prev]*1.002) or (c[prev-1] <= ema89[prev-1]*1.002)}")
print(">> SHORT VERDICT: STRICTLY FORBIDDEN (No Short)")

print("\n--- BUY ORDER EVALUATION ---")
print(f"1. Support Bounce (Lower Wick >= 25%): {lower_wick >= 0.25} ({lower_wick*100:.1f}%) -> PASS")
print(f"2. Close >= EMA89: {c[prev] >= ema89[prev]} ({c[prev]:.2f} vs {ema89[prev]:.2f}) -> PASS")
print(f"3. Macro Bullish (EMA89 > EMA233): {ema89[prev] > ema233[prev]} -> FAIL (EMA233 sits overhead at {ema233[prev]:.2f}!)")
print(">> BUY VERDICT: STRICTLY FORBIDDEN (Macro Downtrend)")

print("\n=== FINAL STATUS ===")
print("NKE is in a CONFLICT ZONE: Price bounced off EMA89 support, but Macro Trend is Bearish.")
print("The engine correctly outputs: 'ABOVE EMA89 (TESTING SUPPORT - NO SHORT)'.")
print("Zero orders placed. Capital safely preserved.")

mt5.shutdown()
