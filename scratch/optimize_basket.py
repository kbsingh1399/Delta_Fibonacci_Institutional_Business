"""
Optimization Study: Improve Alpha Basket Returns via:
1. Remove negative-EMA89-drag assets from live basket
2. Test ADX & ATR filters to remove low-quality setups
3. Simulate improved portfolio
"""
import os, sys
import pandas as pd
import numpy as np

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from engine.mt5_alpha_trader_monitor import OPTIMAL_ALPHA_BASKET
ALPHA_SYMBOLS = {item["symbol"]: item for item in OPTIMAL_ALPHA_BASKET}

df = pd.read_csv(os.path.join(BASE_DIR, "audit", "fundednext_h4_executed_trades.csv"))
df['entry_dt'] = pd.to_datetime(df['entry_time'])
df['exit_dt'] = pd.to_datetime(df['exit_time'])
df_alpha = df[df['symbol'].isin(ALPHA_SYMBOLS.keys())].copy()
df_alpha['cluster'] = df_alpha['symbol'].map(lambda s: ALPHA_SYMBOLS[s]['cluster'])

def simulate_portfolio(df_trades, label="BASELINE", risk=10.0):
    """Simulate portfolio with cluster & concurrency governer."""
    df_trades = df_trades.sort_values('entry_dt').reset_index(drop=True)
    active = []  # (exit_dt, cluster, symbol)
    cum_r = 0.0; peak_r = 0.0; max_dd = 0.0
    results = []
    for _, tr in df_trades.iterrows():
        active = [p for p in active if p[0] > tr['entry_dt']]
        if len(active) >= 3: continue
        if any(p[1] == tr['cluster'] for p in active): continue
        active.append((tr['exit_dt'], tr['cluster'], tr['symbol']))
        cum_r += tr['net_r']
        if cum_r > peak_r: peak_r = cum_r
        dd = peak_r - cum_r
        if dd > max_dd: max_dd = dd
        results.append(tr)
    res = pd.DataFrame(results)
    n = len(res)
    wins = (res['net_r'] > 0).sum()
    gross_w = res[res['net_r'] > 0]['net_r'].sum()
    gross_l = abs(res[res['net_r'] <= 0]['net_r'].sum())
    pf = gross_w / gross_l if gross_l > 0 else 99.0
    calmar = cum_r / max_dd if max_dd > 0 else 99.0
    print(f"\n{'='*70}")
    print(f"  {label}")
    print(f"{'='*70}")
    print(f"  Trades: {n} | Wins: {wins} | WR: {wins/n*100:.1f}%")
    print(f"  Net R: {cum_r:+.2f}R | Net USD ($10): ${cum_r*risk:+,.2f}")
    print(f"  Profit Factor: {pf:.2f}")
    print(f"  Max Drawdown: -{max_dd:.2f}R (${max_dd*risk:,.2f})")
    print(f"  Calmar Ratio: {calmar:.2f}")
    return cum_r, max_dd, pf, calmar, n

# =========================================================
# BASELINE: Full 28 assets, all shelves, 2026 YTD
# =========================================================
df_2026 = df_alpha[df_alpha['entry_dt'] >= '2026-01-01'].copy()
base_r, base_dd, base_pf, base_calmar, base_n = simulate_portfolio(df_2026, "BASELINE (28 assets, all shelves)")

# =========================================================
# OPTIMIZATION 1: EMA 89 SHELF ONLY (remove EMA 233 drag)
# Key finding: EMA_233 trades net -30.10R, EMA_89 net +77.65R
# =========================================================
df_89only = df_2026[df_2026['shelf'] == 'EMA_89'].copy()
opt1_r, opt1_dd, opt1_pf, opt1_calmar, opt1_n = simulate_portfolio(df_89only, "OPT-1: EMA 89 SHELF ONLY")

# =========================================================
# OPTIMIZATION 2: Drop the 7 worst 2026 assets by net_r 
# (LNKUSD, XAGUSD, US2000, AUDJPY, NVDA, MBG, GOOG)
# =========================================================
DROP_ASSETS = {'LNKUSD', 'XAGUSD', 'US2000', 'AUDJPY', 'NVDA', 'MBG', 'GOOG'}
df_trimmed = df_2026[~df_2026['symbol'].isin(DROP_ASSETS)].copy()
opt2_r, opt2_dd, opt2_pf, opt2_calmar, opt2_n = simulate_portfolio(df_trimmed, "OPT-2: DROP 7 DRAG ASSETS")

# =========================================================
# OPTIMIZATION 3: EMA 89 shelf only + Drop 7 drag assets
# =========================================================
df_opt3 = df_2026[(df_2026['shelf'] == 'EMA_89') & (~df_2026['symbol'].isin(DROP_ASSETS))].copy()
opt3_r, opt3_dd, opt3_pf, opt3_calmar, opt3_n = simulate_portfolio(df_opt3, "OPT-3: EMA89 ONLY + DROP 7 DRAG ASSETS")

# =========================================================
# OPTIMIZATION 4: Identify HIGH CALMAR subset
# Only trade assets that were positive in BOTH 2024-25 AND 2026 on EMA89
# =========================================================
asset_2026_r = df_alpha[(df_alpha['entry_dt'] >= '2026-01-01') & (df_alpha['shelf'] == 'EMA_89')].groupby('symbol')['net_r'].sum()
asset_hist_r = df_alpha[(df_alpha['entry_dt'] < '2026-01-01') & (df_alpha['shelf'] == 'EMA_89')].groupby('symbol')['net_r'].sum()
consistently_positive = set(asset_2026_r[asset_2026_r > 0].index) & set(asset_hist_r[asset_hist_r > 0].index)
print(f"\n  Consistently positive on EMA89 in BOTH periods: {sorted(consistently_positive)}")

df_opt4 = df_2026[(df_2026['shelf'] == 'EMA_89') & (df_2026['symbol'].isin(consistently_positive))].copy()
opt4_r, opt4_dd, opt4_pf, opt4_calmar, opt4_n = simulate_portfolio(df_opt4, "OPT-4: CONSISTENT WINNERS ONLY (EMA89, Both Periods Positive)")

# =========================================================
# SUMMARY COMPARISON TABLE
# =========================================================
print(f"\n{'='*70}")
print("  OPTIMIZATION COMPARISON SUMMARY")
print(f"{'='*70}")
print(f"  {'Strategy':<45} | {'Net R':>7} | {'Net $':>8} | {'PF':>5} | {'Calmar':>7} | {'Max DD':>7}")
print(f"  {'-'*45}+{'-'*8}+{'-'*9}+{'-'*6}+{'-'*8}+{'-'*8}")
strategies = [
    ("Baseline (28 assets, all shelves)", base_r, base_dd, base_pf, base_calmar),
    ("OPT-1: EMA89 Shelf Only", opt1_r, opt1_dd, opt1_pf, opt1_calmar),
    ("OPT-2: Drop 7 Drag Assets", opt2_r, opt2_dd, opt2_pf, opt2_calmar),
    ("OPT-3: EMA89 Only + Drop 7 Drags", opt3_r, opt3_dd, opt3_pf, opt3_calmar),
    ("OPT-4: Consistent Winners (EMA89)", opt4_r, opt4_dd, opt4_pf, opt4_calmar),
]
for name, r, dd, pf, calmar in strategies:
    print(f"  {name:<45} | {r:>+7.2f}R | ${r*10:>7,.0f} | {pf:>5.2f} | {calmar:>7.2f} | {dd:>7.2f}R")

print(f"\n  Best strategy identified: Run the numbers above to decide ^")
