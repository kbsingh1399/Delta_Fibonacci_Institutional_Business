import os
import sys
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import pandas as pd
import numpy as np
from scratch.test_360_degree_breakthrough import ALPHA_BASKET, DATA_DIR, extract_trades_flexible, simulate_portfolio

drags = ['LNKUSD', 'XAGUSD', 'US2000', 'AUDJPY', 'NVDA', 'MBG', 'GOOG']
clean_basket = [x for x in ALPHA_BASKET if x['symbol'] not in drags]

data_map = {}
for item in clean_basket:
    p = os.path.join(DATA_DIR, f"{item['symbol']}.parquet")
    if os.path.exists(p):
        data_map[item['symbol']] = pd.read_parquet(p)

TS_2026 = 1767225600
trades_2026 = []
for item in clean_basket:
    s = item['symbol']
    if s not in data_map: continue
    t = extract_trades_flexible(data_map[s], s, item['cluster'], item['setup'], item['exit_mode'], rule_variant='ema89_only', allowed_direction='all')
    trades_2026.extend([x for x in t if x['entry_ts'] >= TS_2026])

res = simulate_portfolio(trades_2026, max_concurrent=3, max_per_cluster=1)
executed = res['executed_trades']

df_exec = pd.DataFrame(executed)
df_exec['entry_dt'] = pd.to_datetime(df_exec['entry_ts'], unit='s')
df_exec['exit_dt'] = pd.to_datetime(df_exec['exit_ts'], unit='s')
df_exec['date'] = df_exec['entry_dt'].dt.date

days_active = (df_exec['entry_dt'].max() - df_exec['entry_dt'].min()).days
total_trades = len(df_exec)
trades_per_day_calendar = total_trades / max(1, days_active)
trades_per_week = trades_per_day_calendar * 7

daily_counts = df_exec.groupby('date').size()

print('=== 2026 TRADE FREQUENCY & CONCURRENCY STATS ===')
print(f'Total Executed Trades (2026): {total_trades}')
print(f'Active Date Range: {df_exec["entry_dt"].min().strftime("%Y-%m-%d")} to {df_exec["entry_dt"].max().strftime("%Y-%m-%d")} ({days_active} days)')
print(f'Average Setups Executed Per Calendar Day: {trades_per_day_calendar:.2f}')
print(f'Average Setups Executed Per Week: {trades_per_week:.2f}')
print(f'Daily Distribution of Executed Trades:')
print(f'  Days with 0 Trades: {days_active - len(daily_counts)} days')
print(f'  Days with 1 Trade:  {(daily_counts == 1).sum()} days')
print(f'  Days with 2 Trades: {(daily_counts == 2).sum()} days')
print(f'  Days with 3 Trades: {(daily_counts == 3).sum()} days')
print(f'  Days with 4+ Trades: {(daily_counts >= 4).sum()} days')
print(f'  Max Trades Triggered in Single Day: {daily_counts.max()}')

print(f'\nHolding Time:')
print(f'  Average Bars Held: {df_exec["bars_held"].mean():.1f} bars ({df_exec["bars_held"].mean()*4:.1f} hours / {df_exec["bars_held"].mean()*4/24:.1f} days)')
print(f'  Median Bars Held:  {df_exec["bars_held"].median():.1f} bars ({df_exec["bars_held"].median()*4:.1f} hours)')
