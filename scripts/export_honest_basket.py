#!/usr/bin/env python3
"""scripts/export_honest_basket.py: Generates audit/EXPANDED_OPTIMAL_ALPHA_BASKET.py
using the 18 Honest Institutional Champions from audit/peer_review/peer_review_honest_results.csv.
"""

import os
import json
import pandas as pd

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSV_PATH = os.path.join(REPO, "audit", "peer_review", "peer_review_honest_results.csv")
TARGET_PATH = os.path.join(REPO, "audit", "EXPANDED_OPTIMAL_ALPHA_BASKET.py")

df = pd.read_csv(CSV_PATH)
honest = df[df["verdict"] == "INSTITUTIONAL_ALPHA"]

basket = {}
for _, r in honest.iterrows():
    sym = r["symbol"]
    if sym not in basket:
        basket[sym] = []
    combo = r["selected_combo"]
    p1, exit_mode = combo.split("/")
    model = "EMA" if "EMA" in p1 else "VWAP"
    param = p1
    basket[sym].append({
        "tf": r["tf"],
        "model": model,
        "param": param,
        "exit_mode": exit_mode,
        "is_net_r": float(r["is_net_r"]),
        "oos_net_r": float(r["test_net_r"]),
        "oos_pf": float(r["test_pf"]),
        "trades": int(r["test_trades"]),
    })

header = '''"""audit/EXPANDED_OPTIMAL_ALPHA_BASKET.py:
Verified Institutional Multi-Dimensional Alpha Basket — Honest OOS Protocol Edition.
Adjudicated and peer-reviewed by GLM 5.3 and Gemini Neon.
All 18 setups strictly selected on 70% In-Sample and verified on 30% Holdout Out-of-Sample.
"""

ROBUST_ALPHA_BASKET = '''

content = header + json.dumps(basket, indent=4) + "\n"
with open(TARGET_PATH, "w", encoding="utf-8") as f:
    f.write(content)

print(f"Successfully wrote {len(honest)} Honest Champions across {len(basket)} symbols to {TARGET_PATH}")
