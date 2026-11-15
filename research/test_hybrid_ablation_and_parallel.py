import os
import sys
import json
import pandas as pd
import numpy as np

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE_DIR = os.path.join(BASE_DIR, "engine")
if ENGINE_DIR not in sys.path:
    sys.path.insert(0, ENGINE_DIR)
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import institutional_wyckoff_engine as we
from universe_screener import load_snapshot, screen, INR_PER_USD
from cluster_risk_governor import validate_new_trade_entry, get_asset_cluster, CORRELATED_CLUSTERS
from asset_universe_classifier import classify_asset

def run_investigation():
    specs = we.load_deployable_specs(offline=True)
    # Filter specs to Alpha Basket (exclude CRYPTO_MAJORS)
    alpha_specs = {k: v for k, v in specs.items() if v.get("cluster") != "CRYPTO_MAJORS"}
    print(f"Loaded {len(specs)} total liquid specs, {len(alpha_specs)} Alpha Basket specs.")
    
    # 1. Scan historical data once
    candidates_wyckoff, dfs = we.scan_historical_candidates(
        os.path.join(BASE_DIR, "data"), 
        alpha_specs,
        min_sep_atr=0.25,
        max_sep_atr=2.50,
        max_vol_ratio=1.20,
        max_cost_r=0.12
    )
    print(f"Wyckoff Filtered Candidates (Alpha Basket): {len(candidates_wyckoff)}")
    
    # Also scan candidates with Wyckoff filter relaxed (Filter OFF: no volume dry-up, no separation band cap)
    candidates_raw, _ = we.scan_historical_candidates(
        os.path.join(BASE_DIR, "data"),
        alpha_specs,
        min_sep_atr=0.0,
        max_sep_atr=999.0,
        max_vol_ratio=999.0,
        max_cost_r=0.25
    )
    print(f"Unfiltered (Baseline) Candidates: {len(candidates_raw)}")
    
    # Let's run backtests under both Static 1R (Rs 50) and Dynamic Compounding (1%)
    # Configuration matrix:
    # 1. Baseline Alpha Basket (Leaderboard exits / fixed RR / trail) - from run_inr_portfolio_backtest
    # 2. Raw signals + Split-Tranche Exits (Filter OFF, Split-Tranche ON)
    # 3. Wyckoff signals + Fixed 2.5R exits (Filter ON, Split-Tranche OFF)
    # 4. Hybrid (Wyckoff Filter ON + Split-Tranche ON)
    
    print("\n--- SIMULATION RESULTS (Alpha Basket Universe) ---")
    
    # Mode 2: Raw Signals + Split-Tranche Exits
    res_raw_split_static = we.run_event_driven_inr_portfolio(candidates_raw, dfs, alpha_specs, compounding=False, exit_policy="hybrid")
    res_raw_split_comp = we.run_event_driven_inr_portfolio(candidates_raw, dfs, alpha_specs, compounding=True, exit_policy="hybrid")
    
    # Mode 3: Wyckoff Filter + Fixed 2.5R Exit
    res_wyckoff_fixed_static = we.run_event_driven_inr_portfolio(candidates_wyckoff, dfs, alpha_specs, compounding=False, exit_policy="fixed_2.5")
    res_wyckoff_fixed_comp = we.run_event_driven_inr_portfolio(candidates_wyckoff, dfs, alpha_specs, compounding=True, exit_policy="fixed_2.5")
    
    # Mode 4: Wyckoff Filter + Split-Tranche Exit (Winning Hybrid)
    res_wyckoff_hybrid_static = we.run_event_driven_inr_portfolio(candidates_wyckoff, dfs, alpha_specs, compounding=False, exit_policy="hybrid")
    res_wyckoff_hybrid_comp = we.run_event_driven_inr_portfolio(candidates_wyckoff, dfs, alpha_specs, compounding=True, exit_policy="hybrid")
    
    # Print comparison
    data = [
        {"Model": "Raw Signals + Split-Tranche (Static)", "Trades": res_raw_split_static['trades'], "WR%": res_raw_split_static['win_rate_pct'], "PF": res_raw_split_static['profit_factor'], "Net PnL": res_raw_split_static['net_profit_inr'], "Ret%": res_raw_split_static['return_pct'], "MaxDD%": res_raw_split_static['max_dd_pct'], "Calmar": res_raw_split_static['calmar']},
        {"Model": "Raw Signals + Split-Tranche (Comp)", "Trades": res_raw_split_comp['trades'], "WR%": res_raw_split_comp['win_rate_pct'], "PF": res_raw_split_comp['profit_factor'], "Net PnL": res_raw_split_comp['net_profit_inr'], "Ret%": res_raw_split_comp['return_pct'], "MaxDD%": res_raw_split_comp['max_dd_pct'], "Calmar": res_raw_split_comp['calmar']},
        {"Model": "Wyckoff Filter + Fixed 2.5R (Static)", "Trades": res_wyckoff_fixed_static['trades'], "WR%": res_wyckoff_fixed_static['win_rate_pct'], "PF": res_wyckoff_fixed_static['profit_factor'], "Net PnL": res_wyckoff_fixed_static['net_profit_inr'], "Ret%": res_wyckoff_fixed_static['return_pct'], "MaxDD%": res_wyckoff_fixed_static['max_dd_pct'], "Calmar": res_wyckoff_fixed_static['calmar']},
        {"Model": "Wyckoff Filter + Fixed 2.5R (Comp)", "Trades": res_wyckoff_fixed_comp['trades'], "WR%": res_wyckoff_fixed_comp['win_rate_pct'], "PF": res_wyckoff_fixed_comp['profit_factor'], "Net PnL": res_wyckoff_fixed_comp['net_profit_inr'], "Ret%": res_wyckoff_fixed_comp['return_pct'], "MaxDD%": res_wyckoff_fixed_comp['max_dd_pct'], "Calmar": res_wyckoff_fixed_comp['calmar']},
        {"Model": "Wyckoff Hybrid (Static 1R=50)", "Trades": res_wyckoff_hybrid_static['trades'], "WR%": res_wyckoff_hybrid_static['win_rate_pct'], "PF": res_wyckoff_hybrid_static['profit_factor'], "Net PnL": res_wyckoff_hybrid_static['net_profit_inr'], "Ret%": res_wyckoff_hybrid_static['return_pct'], "MaxDD%": res_wyckoff_hybrid_static['max_dd_pct'], "Calmar": res_wyckoff_hybrid_static['calmar']},
        {"Model": "Wyckoff Hybrid (Dynamic 1% Comp)", "Trades": res_wyckoff_hybrid_comp['trades'], "WR%": res_wyckoff_hybrid_comp['win_rate_pct'], "PF": res_wyckoff_hybrid_comp['profit_factor'], "Net PnL": res_wyckoff_hybrid_comp['net_profit_inr'], "Ret%": res_wyckoff_hybrid_comp['return_pct'], "MaxDD%": res_wyckoff_hybrid_comp['max_dd_pct'], "Calmar": res_wyckoff_hybrid_comp['calmar']},
    ]
    df_res = pd.DataFrame(data)
    print(df_res.to_string(index=False))

if __name__ == "__main__":
    run_investigation()
