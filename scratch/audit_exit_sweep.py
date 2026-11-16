import os
import sys
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)
import pandas as pd
import numpy as np

from scratch.test_360_degree_breakthrough import ALPHA_BASKET, DATA_DIR, extract_trades_flexible, simulate_portfolio

drags_to_drop = ["LNKUSD", "XAGUSD", "US2000", "AUDJPY", "NVDA", "MBG", "GOOG"]
clean_basket = [x for x in ALPHA_BASKET if x["symbol"] not in drags_to_drop]

data_map = {}
for item in clean_basket:
    sym = item["symbol"]
    p_path = os.path.join(DATA_DIR, f"{sym}.parquet")
    if os.path.exists(p_path):
        data_map[sym] = pd.read_parquet(p_path)

print("=" * 80)
print("EXIT TARGET SWEEP AUDIT (CLEAN 21 ASSETS, EMA89-ONLY, 2024-2026)")
print("=" * 80)

TS_2024 = 1704067200

for exit_mode in ["fixed_2.5", "fixed_3.0", "fixed_3.5", "step_trail", "optimal_assigned"]:
    all_trades = []
    for item in clean_basket:
        sym = item["symbol"]
        if sym not in data_map:
            continue
        mode = item["exit_mode"] if exit_mode == "optimal_assigned" else exit_mode
        trades = extract_trades_flexible(
            data_map[sym], sym, item["cluster"], item["setup"], mode,
            rule_variant="ema89_only", allowed_direction="all"
        )
        trades_all = [t for t in trades if t["entry_ts"] >= TS_2024]
        all_trades.extend(trades_all)
    res = simulate_portfolio(all_trades, max_concurrent=3, max_per_cluster=1)
    print(f"Exit: {exit_mode:<18} | Trades: {res['trades']:<4} | Win%: {res['win_rate']:<5.1f}% | Net R: {res['net_r']:<+7.2f}R | PF: {res['pf']:<5.2f} | Max DD: -{res['max_dd_r']:<5.2f}R | Calmar: {res['calmar']:<5.2f}")
