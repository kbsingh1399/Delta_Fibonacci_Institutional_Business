#!/usr/bin/env python3
"""health_check_alpha_basket.py: Comprehensive Diagnostic & Health Check
for the FundedNext MT5 Optimal Alpha Basket.
"""

import os
import sys
import json
import time
from datetime import datetime, timezone
import pandas as pd
import numpy as np
import MetaTrader5 as mt5

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUDIT_DIR = os.path.join(BASE_DIR, "audit")
HEALTH_STATUS_FILE = os.path.join(AUDIT_DIR, "mt5_health_status.json")

if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)
from engine.mt5_alpha_trader_monitor import OPTIMAL_ALPHA_BASKET as BASKET

def calculate_ema(arr: np.ndarray, span: int) -> np.ndarray:
    return pd.Series(arr).ewm(span=span, adjust=False).mean().to_numpy()

def calculate_atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> np.ndarray:
    n = len(closes)
    tr = np.zeros(n)
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
    return pd.Series(tr).rolling(period).mean().bfill().to_numpy()

def compute_adx(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
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
    for i in range(period + 1, n):
        atr_s[i] = (atr_s[i - 1] * (period - 1) + tr[i]) / period
        p_s[i] = (p_s[i - 1] * (period - 1) + plus_dm[i]) / period
        m_s[i] = (m_s[i - 1] * (period - 1) + minus_dm[i]) / period
    denom = np.where(atr_s > 0, atr_s, 1e-9)
    p_di = 100.0 * (p_s / denom)
    m_di = 100.0 * (m_s / denom)
    sum_di = p_di + m_di
    dx = 100.0 * np.divide(np.abs(p_di - m_di), np.where(sum_di > 0, sum_di, 1e-9))
    adx_arr = np.zeros(n, dtype=float)
    idx0 = 2 * period
    if n > idx0:
        adx_arr[idx0] = np.mean(dx[period : idx0 + 1])
        for i in range(idx0 + 1, n):
            adx_arr[i] = (adx_arr[i - 1] * (period - 1) + dx[i]) / period
    return adx_arr

def main():
    print("=" * 90)
    print("FUNDEDNEXT MT5 ALPHA BASKET COMPREHENSIVE QUANT HEALTH CHECK")
    print(f"Timestamp: {datetime.now(timezone.utc).isoformat()} UTC")
    print("=" * 90)

    # 1. Initialize MT5
    if not mt5.initialize():
        print(f"[FATAL] Could not initialize MT5: {mt5.last_error()}")
        sys.exit(1)

    acc = mt5.account_info()
    term = mt5.terminal_info()

    print(f"Terminal: {term.name} v{term.build} | Connected: {term.connected} | Ping: {term.ping_last / 1000:.1f}ms")
    print(f"Account:  Login #{acc.login} ({acc.company}) | Server: {acc.server}")
    print(f"Balance:  ${acc.balance:,.2f} | Equity: ${acc.equity:,.2f} | Free Margin: ${acc.margin_free:,.2f}")
    print(f"Trade Allowed: {term.trade_allowed} | TradeAPI: {not term.tradeapi_disabled}")
    print("-" * 90)

    # 2. Check Heartbeat
    if os.path.exists(HEALTH_STATUS_FILE):
        try:
            with open(HEALTH_STATUS_FILE, "r") as f:
                hb = json.load(f)
            hb_time = datetime.fromisoformat(hb["timestamp"])
            age_sec = (datetime.now(timezone.utc) - hb_time).total_seconds()
            print(f"Live Daemon Heartbeat: Last pulse {age_sec:.1f}s ago ({hb['timestamp']})")
            if age_sec < 120:
                print(">> DAEMON HEALTH: HEALTHY & ACTIVE (PULSING NORMALLY)")
            else:
                print(f">> DAEMON WARNING: Heartbeat stale ({age_sec:.1f}s > 120s)")
        except Exception as e:
            print(f">> Heartbeat check error: {e}")
    else:
        print(">> No heartbeat file found.")
    print("-" * 90)

    # 3. Analyze each asset in basket
    print(f"{'Symbol':<8} | {'Cluster':<14} | {'Status':<6} | {'Price':<10} | {'Spread':<6} | {'EMA89':<10} | {'EMA233':<10} | {'ADX':<5} | {'Bar Time (Close In)'}")
    print("-" * 90)

    for item in BASKET:
        sym = item["symbol"]
        cluster = item["cluster"]
        setup = item["setup"]

        mt5.symbol_select(sym, True)
        info = mt5.symbol_info(sym)
        tick = mt5.symbol_info_tick(sym)

        if not info or not tick:
            print(f"{sym:<8} | {cluster:<14} | NO DATA |")
            continue

        trade_status = "OPEN" if info.trade_mode == mt5.SYMBOL_TRADE_MODE_FULL else f"M_{info.trade_mode}"
        spread_pts = (tick.ask - tick.bid) / info.point if info.point > 0 else 0

        # Rates
        rates = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_H4, 0, 1200)
        if rates is not None and len(rates) >= 240:
            df = pd.DataFrame(rates)
            c = df["close"].to_numpy(dtype=float)
            h = df["high"].to_numpy(dtype=float)
            l = df["low"].to_numpy(dtype=float)
            t = df["time"].to_numpy(dtype=int)

            ema89 = calculate_ema(c, 89)
            ema233 = calculate_ema(c, 233)
            adx = compute_adx(h, l, c, 14)

            curr_bar_open = t[-1]
            next_close = curr_bar_open + 4 * 3600
            time_left_sec = max(0, next_close - tick.time)
            hrs_left = int(time_left_sec // 3600)
            mins_left = int((time_left_sec % 3600) // 60)
            close_in_str = f"{hrs_left}h {mins_left:02d}m"

            p_str = f"{tick.bid:.2f}" if tick.bid > 10 else f"{tick.bid:.4f}"
            e89_str = f"{ema89[-1]:.2f}" if ema89[-1] > 10 else f"{ema89[-1]:.4f}"
            e233_str = f"{ema233[-1]:.2f}" if ema233[-1] > 10 else f"{ema233[-1]:.4f}"
            adx_val = adx[-1]

            print(f"{sym:<8} | {cluster:<14} | {trade_status:<6} | {p_str:<10} | {spread_pts:<6.1f} | {e89_str:<10} | {e233_str:<10} | {adx_val:<5.1f} | in {close_in_str}")
        else:
            print(f"{sym:<8} | {cluster:<14} | {trade_status:<6} | {tick.bid:<10.3f} | {spread_pts:<6.1f} | Bars: {len(rates) if rates is not None else 0}")

    print("=" * 90)
    mt5.shutdown()

if __name__ == "__main__":
    main()
