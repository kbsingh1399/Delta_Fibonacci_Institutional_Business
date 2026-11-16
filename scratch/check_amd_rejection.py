import MetaTrader5 as mt5
import pandas as pd
import numpy as np
import sys
import os

BASE_DIR = r"C:\Users\SIGMA\Documents\Delta_Fibonacci_Institutional_Business"
sys.path.insert(0, BASE_DIR)

from engine.mt5_alpha_trader_monitor import calculate_ema, calculate_atr, compute_adx

if not mt5.initialize():
    print("Init failed")
    sys.exit(1)

rates = mt5.copy_rates_from_pos("AMD", mt5.TIMEFRAME_H4, 0, 300)
df = pd.DataFrame(rates)
c = df["close"].to_numpy()
o = df["open"].to_numpy()
h = df["high"].to_numpy()
l = df["low"].to_numpy()

ema34 = calculate_ema(c, 34)
ema55 = calculate_ema(c, 55)
ema89 = calculate_ema(c, 89)
ema233 = calculate_ema(c, 233)
atr = calculate_atr(h, l, c, 14)
adx = compute_adx(h, l, c, 14)

prev = len(c) - 2 # last completed bar
rng = max(1e-6, h[prev] - l[prev])
body_top = max(o[prev], c[prev])
body_bot = min(o[prev], c[prev])
upper_wick = (h[prev] - body_top) / rng
lower_wick = (body_bot - l[prev]) / rng

c_bar = c[prev]
o_bar = o[prev]
h_bar = h[prev]
l_bar = l[prev]

came_from_above = (o_bar >= ema89[prev] * 0.998) or (c[prev - 1] >= ema89[prev - 1] * 0.998)
came_from_below = (o_bar <= ema89[prev] * 1.002) or (c[prev - 1] <= ema89[prev - 1] * 1.002)

print("=== LAST COMPLETED BAR (-2) EVALUATION ===")
print(f"Open: {o_bar:.2f}, High: {h_bar:.2f}, Low: {l_bar:.2f}, Close: {c_bar:.2f}")
print(f"EMA89: {ema89[prev]:.2f}, EMA233: {ema233[prev]:.2f}")
print(f"Lower Wick: {lower_wick*100:.1f}%, Upper Wick: {upper_wick*100:.1f}%")
print(f"Macro Bullish (EMA89 > EMA233): {ema89[prev] > ema233[prev]}")
print(f"Close >= EMA89: {c_bar >= ema89[prev]} ({c_bar:.2f} vs {ema89[prev]:.2f}) -> Status: {'PASS' if c_bar >= ema89[prev] else 'FAIL (Closed below shelf)'}")
print(f"Lower Wick >= 25%: {lower_wick >= 0.25} -> Status: {'PASS' if lower_wick >= 0.25 else 'FAIL'}")

print("\n=== CURRENT FORMING BAR (-1) (If it closes at current price) ===")
prev1 = len(c) - 1
rng1 = max(1e-6, h[prev1] - l[prev1])
body_top1 = max(o[prev1], c[prev1])
body_bot1 = min(o[prev1], c[prev1])
upper_wick1 = (h[prev1] - body_top1) / rng1
lower_wick1 = (body_bot1 - l[prev1]) / rng1
o_bar1 = o[prev1]
c_bar1 = c[prev1]
came_from_above1 = (o_bar1 >= ema89[prev1] * 0.998) or (c[prev1 - 1] >= ema89[prev1 - 1] * 0.998)
print(f"Open: {o_bar1:.2f}, High: {h[prev1]:.2f}, Low: {l[prev1]:.2f}, Close: {c_bar1:.2f}")
print(f"EMA89: {ema89[prev1]:.2f}")
print(f"came_from_above: {came_from_above1} (Open={o_bar1:.2f}, Prior Close={c[prev1-1]:.2f} vs EMA={ema89[prev1]:.2f}) -> Status: {'PASS' if came_from_above1 else 'FAIL (Approaching from below)'}")
print(f"Lower Wick: {lower_wick1*100:.1f}% (Needs >= 25%) -> Status: {'PASS' if lower_wick1 >= 0.25 else 'FAIL'}")
print(f"Upper Wick: {upper_wick1*100:.1f}% (Showing overhead ceiling rejection)")

mt5.shutdown()
