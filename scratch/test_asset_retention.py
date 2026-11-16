#!/usr/bin/env python3
r"""scratch/test_asset_retention.py: Inspect exactly which dropped assets helped vs hurt
Dropped list was: FRA40, EUSTX50, NDX100, MC, EURNOK.
What if we drop only MC and EURNOK (the pure drag non-US assets)?
What if we test:
- 25 Assets + Wick 33% + Concur 4
- 23 Assets (drop MC, EURNOK) + Wick 33% + Concur 4
- 22 Assets (drop MC, EURNOK, NDX100) + Wick 33% + Concur 4
- 20 Assets (drop MC, EURNOK, NDX100, FRA40, EUSTX50) + Wick 33% + Concur 4
"""

import os
import sys
from datetime import datetime, timezone
import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "FundedNext_Data")
sys.path.insert(0, BASE_DIR)

from engine.mt5_alpha_trader_monitor import OPTIMAL_ALPHA_BASKET
from scratch.test_refined_winning_synergy import run_single_asset_backtest, simulate_portfolio, BASE_RECLAIM_UNIVERSE

def main():
    b_all = OPTIMAL_ALPHA_BASKET
    r_all = BASE_RECLAIM_UNIVERSE

    b_drop_mc_eurnok = [x for x in b_all if x["symbol"] not in ["MC", "EURNOK"]]
    r_drop_mc_eurnok = r_all

    b_drop_euro = [x for x in b_all if x["symbol"] not in ["MC", "EURNOK", "FRA40", "EUSTX50"]]
    r_drop_euro = [x for x in r_all if x != "FRA40"]

    b_clean_20 = [x for x in b_all if x["symbol"] not in ["FRA40", "EUSTX50", "NDX100", "MC", "EURNOK"]]
    r_clean_20 = [x for x in r_all if x not in ["FRA40", "NDX100"]]

    experiments = [
        ("A. 25 Assets | Wick 33% | Concur 3", b_all, r_all, 3),
        ("B. 25 Assets | Wick 33% | Concur 4", b_all, r_all, 4),
        ("C. Drop MC + EURNOK (23 Assets) | Wick 33% | Concur 3", b_drop_mc_eurnok, r_drop_mc_eurnok, 3),
        ("D. Drop MC + EURNOK (23 Assets) | Wick 33% | Concur 4", b_drop_mc_eurnok, r_drop_mc_eurnok, 4),
        ("E. Drop Euro Drag (21 Assets)   | Wick 33% | Concur 4", b_drop_euro, r_drop_euro, 4),
        ("F. Clean 20 Assets               | Wick 33% | Concur 4", b_clean_20, r_clean_20, 4),
        ("G. Clean 20 Assets               | Wick 33% | Concur 3", b_clean_20, r_clean_20, 3),
    ]

    print("=" * 105)
    print("ASSET RETENTION MATRIX (JAN 1, 2026 - NOV 16, 2026)")
    print("=" * 105)
    print(f"{'Experiment Name':<55} | {'Trades':<6} | {'WinRate':<7} | {'Net R':<8} | {'MaxDD':<7} | {'Calmar':<6} | {'PF':<5}")
    print("-" * 105)

    for name, basket, reclaim, concur in experiments:
        t_all = []
        for item in basket:
            t_list = run_single_asset_backtest(item, wick_threshold_m2=0.33, reclaim_universe=reclaim)
            t_all.extend(t_list)

        res = simulate_portfolio(t_all, max_concurrent=concur, max_per_cluster=1)
        if res:
            print(f"{name:<55} | {res['trades']:<6} | {res['wr']:<6.1f}% | {res['net_r']:<+8.2f} | {res['max_dd']:<7.2f} | {res['calmar']:<6.2f} | {res['pf']:<5.2f}")

    print("=" * 105)

if __name__ == "__main__":
    main()
