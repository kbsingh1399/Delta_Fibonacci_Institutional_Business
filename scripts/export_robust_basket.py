#!/usr/bin/env python3
"""scripts/export_robust_basket.py: Exports the 25 verified institutional champions
to audit/EXPANDED_OPTIMAL_ALPHA_BASKET.py for live dispatcher execution.
"""

import os
import json
import pandas as pd

AUDIT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "audit")
csv_path = os.path.join(AUDIT_DIR, "INSTITUTIONAL_ROBUST_ALPHA_BASKET.csv")
out_path = os.path.join(AUDIT_DIR, "EXPANDED_OPTIMAL_ALPHA_BASKET.py")

if not os.path.exists(csv_path):
    print(f"Error: {csv_path} does not exist.")
    exit(1)

df_champs = pd.read_csv(csv_path)

basket = {}
for _, r in df_champs.iterrows():
    sym = r["symbol"]
    tf = r["tf"]
    if sym not in basket:
        basket[sym] = []
    basket[sym].append({
        "tf": tf,
        "model": r["model"],
        "param": r["param"],
        "exit_mode": r["exit_mode"],
        "oos_net_r": float(r["oos_net_r"]),
        "oos_pf": float(r["oos_pf"]),
        "mc_p_profit": float(r["mc_p_profit"]),
        "mc_max_dd_95": float(r["mc_max_dd_95"]),
        "deflated_sharpe": float(r["deflated_sharpe"]),
    })

content = '"""audit/EXPANDED_OPTIMAL_ALPHA_BASKET.py:\n'
content += 'Verified Institutional Multi-Dimensional Alpha Basket.\n'
content += 'All setups passed strict 70/30 Out-of-Sample Holdout and 10,000 Numba Monte Carlo Bootstrap.\n'
content += '"""\n\n'
content += 'ROBUST_ALPHA_BASKET = ' + json.dumps(basket, indent=4) + '\n'

with open(out_path, "w", encoding="utf-8") as f:
    f.write(content)

print(f"✅ Generated {out_path} with {len(basket)} unique assets and {len(df_champs)} setups.")
