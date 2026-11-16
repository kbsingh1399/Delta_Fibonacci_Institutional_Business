import os
import sys
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)
import MetaTrader5 as mt5
import pandas as pd
import numpy as np

from scratch.test_multi_timeframe import fetch_mt5_bars, backtest_single_tf

mt5.initialize()
test_symbols = ["XAUUSD", "BTCUSD", "ETHUSD", "DOGUSD", "US30", "USDJPY", "EURJPY", "USDCHF", "MSFT", "AMD"]

print("=" * 80)
print("DAILY (D1) TIMEFRAME AUDIT")
print("=" * 80)

results_d1 = []
for sym in test_symbols:
    df_d1 = fetch_mt5_bars(sym, mt5.TIMEFRAME_D1, 1500)
    t_d1 = backtest_single_tf(df_d1, exit_rr=3.0, cooldown_bars=6)
    r_d1 = sum(x['net_r'] for x in t_d1)
    wr_d1 = (sum(1 for x in t_d1 if x['net_r'] > 0) / max(1, len(t_d1))) * 100
    print(f"[{sym:<7}] D1: {len(t_d1):<3} trades | Win: {wr_d1:4.1f}% | Net R: {r_d1:+6.2f}R")
    results_d1.extend(t_d1)

tot_r = sum(x['net_r'] for x in results_d1)
wins = [x for x in results_d1 if x['net_r'] > 0]
losses = [x for x in results_d1 if x['net_r'] <= 0]
wr = (len(wins) / max(1, len(results_d1))) * 100
pf = sum(x['net_r'] for x in wins) / max(1e-6, abs(sum(x['net_r'] for x in losses)))
print("\n" + "=" * 80)
print(f"D1 Aggregate | Trades: {len(results_d1)} | Win Rate: {wr:4.1f}% | Net R: {tot_r:+7.2f}R | PF: {pf:4.2f}")
