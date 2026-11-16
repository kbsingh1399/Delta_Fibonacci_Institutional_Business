#!/usr/bin/env python3
"""scan_all_mt5_assets.py: High-speed scanner across ALL MT5 tradeable assets
evaluating live tick proximity and setup status.
"""

import os
import sys
import time
import pandas as pd
import numpy as np
import MetaTrader5 as mt5

def calculate_ema(arr: np.ndarray, span: int) -> np.ndarray:
    return pd.Series(arr).ewm(span=span, adjust=False).mean().to_numpy()

def calculate_atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> np.ndarray:
    n = len(closes)
    tr = np.zeros(n)
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
    return pd.Series(tr).rolling(period).mean().bfill().to_numpy()

def main():
    if not mt5.initialize():
        print(f"Failed to initialize MT5: {mt5.last_error()}")
        sys.exit(1)

    all_symbols = [s.name for s in mt5.symbols_get() if s.trade_mode == mt5.SYMBOL_TRADE_MODE_FULL]
    print(f"Total active tradeable symbols on FundedNext MT5: {len(all_symbols)}")

    proximity_list = []
    rejection_signals = []

    t0 = time.time()
    for s in all_symbols:
        mt5.symbol_select(s, True)
        rates = mt5.copy_rates_from_pos(s, mt5.TIMEFRAME_H4, 0, 260)
        if rates is None or len(rates) < 240:
            continue
        tick = mt5.symbol_info_tick(s)
        if not tick or tick.bid <= 0:
            continue

        c = rates['close']
        h = rates['high']
        l = rates['low']
        o = rates['open']
        n = len(rates)
        prev = n - 2  # most recently completed 4H bar

        e89 = calculate_ema(c, 89)
        e233 = calculate_ema(c, 233)
        atr = calculate_atr(h, l, c, 14)

        curr_p = tick.bid
        d89_now = abs(curr_p - e89[-1]) / atr[-1]
        d233_now = abs(curr_p - e233[-1]) / atr[-1]
        min_d = min(d89_now, d233_now)
        shelf = 'EMA89' if d89_now < d233_now else 'EMA233'

        if min_d <= 0.75:
            proximity_list.append({
                "symbol": s,
                "shelf": shelf,
                "dist_atr": round(min_d, 2),
                "price": curr_p,
                "ema89": round(e89[-1], 4),
                "ema233": round(e233[-1], 4),
                "atr": round(atr[-1], 4),
            })

        # Check if the closed bar (prev) had a valid canonical rejection
        rng = max(1e-6, h[prev] - l[prev])
        up_wick = (h[prev] - max(o[prev], c[prev])) / rng
        low_wick = (min(o[prev], c[prev]) - l[prev]) / rng

        # Bullish
        if e89[prev] > e233[prev] and c[prev] >= e233[prev]:
            if low_wick >= 0.25 and (l[prev] <= e89[prev] * 1.002 or l[prev] <= e233[prev] * 1.002):
                rejection_signals.append({
                    "symbol": s, "side": "BUY", "shelf": "EMA89" if l[prev] <= e89[prev] * 1.002 else "EMA233",
                    "low_wick": round(low_wick * 100, 1), "close": c[prev], "time": rates['time'][prev]
                })
        # Bearish
        elif e89[prev] < e233[prev] and c[prev] <= e233[prev]:
            if up_wick >= 0.25 and (h[prev] >= e89[prev] * 0.998 or h[prev] >= e233[prev] * 0.998):
                rejection_signals.append({
                    "symbol": s, "side": "SELL", "shelf": "EMA89" if h[prev] >= e89[prev] * 0.998 else "EMA233",
                    "up_wick": round(up_wick * 100, 1), "close": c[prev], "time": rates['time'][prev]
                })

    elapsed = time.time() - t0
    print(f"Scanned all symbols in {elapsed:.2f}s ({elapsed/len(all_symbols)*1000:.1f}ms/sym)\n")

    print(f"=== CANDIDATE SIGNALS CONFIRMED ON LAST 4H BAR CLOSE: {len(rejection_signals)} ===")
    for sig in rejection_signals:
        print(f"  * {sig['side']} {sig['symbol']} off {sig['shelf']} (Wick: {sig.get('low_wick', sig.get('up_wick'))}%)")

    print(f"\n=== ASSETS CURRENTLY WITHIN 0.75 ATR OF EMA 89/233 SHELF: {len(proximity_list)} ===")
    proximity_list.sort(key=lambda x: x['dist_atr'])
    df_prox = pd.DataFrame(proximity_list)
    if not df_prox.empty:
        print(df_prox[['symbol', 'shelf', 'dist_atr', 'price', 'ema89', 'ema233']].to_string(index=False))

    mt5.shutdown()

if __name__ == "__main__":
    main()
