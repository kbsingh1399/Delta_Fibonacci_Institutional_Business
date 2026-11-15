"""run_full_business_audit.py: Batch Optimization & Sector Calmar Leaderboard Generator.
Executes grid search across Delta Exchange India perpetual catalog:
- 4-EMA and 3-EMA Ribbon Stacks (including 21/55/89/233)
- Tiered Support (EMA 89 and EMA 233 Rejections)
- Both Independent Dual-Timeframe and MTF Confluence Models
- Automatic Sector Classification across 5 Uncorrelated Asset Classes
"""

import os
import sys
import glob
import pandas as pd
import numpy as np
import time

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.join(BASE_DIR, "engine") not in sys.path:
    sys.path.insert(0, os.path.join(BASE_DIR, "engine"))

from asset_universe_classifier import classify_asset, get_sector_display_name, ALL_SECTORS
from delta_master_calmar_optimizer import simulate_asset_calmar
from mtf_confluence_optimizer import simulate_mtf_confluence

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
AUDIT_DIR = os.path.join(BASE_DIR, "audit")
REPORTS_DIR = os.path.join(BASE_DIR, "reports")
os.makedirs(AUDIT_DIR, exist_ok=True)
os.makedirs(REPORTS_DIR, exist_ok=True)

RIBBONS = [
    ('Fib_89_233', 55, 89, 233),
    ('Fib_34_89', 34, 89, 233),
    ('Fib_21_55', 21, 55, 144),
    ('Classic_50_200', 50, 100, 200)
]

STRATEGIES = ['fixed_2.0', 'fixed_2.5', 'fixed_3.0', 'step_trail', 'trail_89', 'trail_55']

def main():
    csv_4h = glob.glob(os.path.join(DATA_DIR, "*_4h.csv"))
    csv_1h = glob.glob(os.path.join(DATA_DIR, "*_1h.csv"))
    
    print(f"Discovered {len(csv_4h)} 4H datasets and {len(csv_1h)} 1H datasets in data lake.")
    all_simulations = []
    start_time = time.time()
    
    # 1. Run Single-Timeframe Audits (4H and 1H)
    for tf_label, file_list in [('4H', csv_4h), ('1H', csv_1h)]:
        for fpath in file_list:
            symbol = os.path.basename(fpath).split('_')[0]
            try:
                df = pd.read_csv(fpath)
            except Exception:
                continue
            if len(df) < 250:
                continue
                
            sector = classify_asset(symbol)
            
            for mode in STRATEGIES:
                for rib_name, f_p, m_p, s_p in RIBBONS:
                    res = simulate_asset_calmar(
                        df, f_p, m_p, s_p,
                        exit_mode=mode,
                        allow_pyramiding=False,
                        min_days=45.0
                    )
                    if res and res['trades'] >= 10:
                        all_simulations.append({
                            'symbol': symbol,
                            'sector': sector,
                            'model_type': 'SINGLE_TF',
                            'timeframe': tf_label,
                            'strategy': mode,
                            'ribbon': rib_name,
                            'trades': res['trades'],
                            'win_rate': res['win_rate'],
                            'profit_factor': res['pf'],
                            'net_r': res['net_r'],
                            'annualized_net_r': res['annualized_net_r'],
                            'max_dd_r': res['max_dd_r'],
                            'calmar_ratio': res['calmar_ratio'],
                            'quality_ratio': res['quality_ratio'],
                            'avg_r': res['avg_r'],
                            'total_days': res['total_days']
                        })
                        
    # 2. Run Multi-Timeframe Confluence Audits (4H Macro Gate + 1H Rejection)
    print("Running Multi-Timeframe (MTF) Confluence simulations...")
    symbols_with_both = set([os.path.basename(f).split('_')[0] for f in csv_4h]).intersection(
        set([os.path.basename(f).split('_')[0] for f in csv_1h])
    )
    
    for sym in symbols_with_both:
        sector = classify_asset(sym)
        for mode in ['fixed_2.5', 'step_trail', 'trail_89']:
            res_mtf = simulate_mtf_confluence(
                sym, fast_p=34, mid_p=89, slow_p=233,
                exit_mode=mode, min_days=45.0
            )
            if res_mtf and res_mtf['trades'] >= 10:
                all_simulations.append({
                    'symbol': sym,
                    'sector': sector,
                    'model_type': 'MTF_CONFLUENCE',
                    'timeframe': '4H_1H',
                    'strategy': mode,
                    'ribbon': 'Fib_34_89_233',
                    'trades': res_mtf['trades'],
                    'win_rate': res_mtf['win_rate'],
                    'profit_factor': res_mtf['pf'],
                    'net_r': res_mtf['net_r'],
                    'annualized_net_r': res_mtf['annualized_net_r'],
                    'max_dd_r': res_mtf['max_dd_r'],
                    'calmar_ratio': res_mtf['calmar_ratio'],
                    'quality_ratio': res_mtf['quality_ratio'],
                    'avg_r': res_mtf['avg_r'],
                    'total_days': res_mtf['total_days']
                })
                
    elapsed = time.time() - start_time
    print(f"Completed {len(all_simulations)} backtest simulations across Delta Exchange in {elapsed:.1f}s.")
    
    df_all = pd.DataFrame(all_simulations)
    master_csv = os.path.join(AUDIT_DIR, "delta_full_master_audit.csv")
    df_all.to_csv(master_csv, index=False)
    print(f"Master audit database saved to {master_csv}")
    
    # Filter profitable assets with minimum sample days
    pos_df = df_all[(df_all['net_r'] > 0) & (df_all['total_days'] >= 45.0)].copy()
    
    # Primary Calmar Leaderboard: Top configuration per symbol
    idx_best = pos_df.groupby('symbol')['calmar_ratio'].idxmax()
    leaderboard = pos_df.loc[idx_best].sort_values('calmar_ratio', ascending=False).reset_index(drop=True)
    leaderboard_csv = os.path.join(AUDIT_DIR, "delta_calmar_leaderboard.csv")
    leaderboard.to_csv(leaderboard_csv, index=False)
    print(f"Calmar leaderboard saved to {leaderboard_csv}")
    
    # Sector-Specific Leaderboard: Top 5 per sector
    sector_leaders = []
    for sec in ALL_SECTORS:
        sec_df = leaderboard[leaderboard['sector'] == sec].sort_values('calmar_ratio', ascending=False).head(5)
        sector_leaders.append(sec_df)
    df_sectors = pd.concat(sector_leaders).reset_index(drop=True)
    sector_csv = os.path.join(AUDIT_DIR, "delta_sector_leaderboard.csv")
    df_sectors.to_csv(sector_csv, index=False)
    print(f"Sector-balanced leaderboard saved to {sector_csv}")
    
    print("\n" + "="*135)
    print("TOP PERFORMERS ACROSS ALL 5 INSTITUTIONAL SECTORS (RANKED BY CALMAR RATIO)")
    print("="*135)
    cols = ['symbol', 'sector', 'model_type', 'timeframe', 'strategy', 'ribbon', 'trades', 'win_rate', 'profit_factor', 'net_r', 'max_dd_r', 'calmar_ratio']
    print(df_sectors[cols].to_string(index=False))

if __name__ == '__main__':
    main()
