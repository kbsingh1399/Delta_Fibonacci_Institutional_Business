import os
import sys
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)
import pandas as pd
import numpy as np

from scratch.test_360_degree_breakthrough import ALPHA_BASKET, DATA_DIR, extract_trades_flexible, simulate_portfolio

TS_2024 = 1704067200
TS_2025 = 1735689600
TS_2026 = 1767225600

drags_to_drop = ["LNKUSD", "XAGUSD", "US2000", "AUDJPY", "NVDA", "MBG", "GOOG"]
clean_basket = [x for x in ALPHA_BASKET if x["symbol"] not in drags_to_drop]

data_map = {}
for item in clean_basket:
    sym = item["symbol"]
    p_path = os.path.join(DATA_DIR, f"{sym}.parquet")
    if os.path.exists(p_path):
        data_map[sym] = pd.read_parquet(p_path)

print("=" * 80)
print("CLEAN 21-ASSET BASKET ALL-TIME & DIRECTIONAL AUDIT")
print("=" * 80)

for year_label, start_ts, end_ts in [("2024 (H2)", TS_2024, TS_2025), ("2025 (Full Year)", TS_2025, TS_2026), ("2026 (YTD)", TS_2026, 9999999999), ("ALL-TIME (24-26)", TS_2024, 9999999999)]:
    for r_var, r_name in [("ema89_only", "EMA89-Only"), ("current_live", "Current Live")]:
        for d_var in ["all", "long_only", "short_only"]:
            all_trades = []
            for item in clean_basket:
                sym = item["symbol"]
                if sym not in data_map:
                    continue
                trades = extract_trades_flexible(
                    data_map[sym], sym, item["cluster"], item["setup"], item["exit_mode"],
                    rule_variant=r_var, allowed_direction=d_var
                )
                p_trades = [t for t in trades if start_ts <= t["entry_ts"] < end_ts]
                all_trades.extend(p_trades)
            res = simulate_portfolio(all_trades, max_concurrent=3, max_per_cluster=1)
            print(f"{year_label:<16} | {r_name:<11} | {d_var:<10} | Trades: {res['trades']:<4} | Win%: {res['win_rate']:<5.1f}% | Net R: {res['net_r']:<+7.2f}R | PF: {res['pf']:<5.2f} | Max DD: -{res['max_dd_r']:<5.2f}R | Calmar: {res['calmar']:<5.2f}")
