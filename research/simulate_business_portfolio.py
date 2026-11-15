"""simulate_business_portfolio.py: Institutional Event-Driven Portfolio Simulator.
Simulates a $5,000 capital base across a multi-asset sector-balanced basket.
Features:
- Event-Driven Chronology: Zero equity lookahead (compounding uses realized equity at entry timestamp).
- Single Entry Invariant: Exactly 1 active position per asset.
- Concurrency Governor: Maximum 6 simultaneous positions across portfolio.
- Sector Diversification: Balances TradFi equities, commodities/metals, crypto majors, altcoins, and indices.
"""

import os
import sys
import glob
import pandas as pd
import numpy as np

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.join(BASE_DIR, "engine") not in sys.path:
    sys.path.insert(0, os.path.join(BASE_DIR, "engine"))

from asset_universe_classifier import classify_asset, get_sector_display_name, ALL_SECTORS
from delta_master_calmar_optimizer import calculate_atr, calculate_ema_dict
from mtf_confluence_optimizer import get_mtf_trades_for_portfolio

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
AUDIT_DIR = os.path.join(BASE_DIR, "audit")
REPORTS_DIR = os.path.join(BASE_DIR, "reports")
os.makedirs(AUDIT_DIR, exist_ok=True)
os.makedirs(REPORTS_DIR, exist_ok=True)

def get_asset_trades(symbol, tf, fast_p, mid_p, slow_p, exit_mode, spread_cost_r=0.04):
    """Extracts chronological trade list for an asset using Tiered EMA logic and Single Entry."""
    fpath = os.path.join(DATA_DIR, f"{symbol}_{tf.lower()}.csv")
    if not os.path.exists(fpath):
        return []
    try:
        df = pd.read_csv(fpath)
    except Exception:
        return []
    if len(df) < 250:
        return []
        
    opens = df['open'].to_numpy(dtype=float)
    highs = df['high'].to_numpy(dtype=float)
    lows = df['low'].to_numpy(dtype=float)
    closes = df['close'].to_numpy(dtype=float)
    timestamps = df['timestamp'].to_numpy(dtype=int)
    n = len(closes)
    
    rng = highs - lows
    rng = np.where(rng <= 0, 1e-6, rng)
    body_tops = np.maximum(opens, closes)
    body_bots = np.minimum(opens, closes)
    u_wicks = (highs - body_tops) / rng
    l_wicks = (body_bots - lows) / rng
    
    atrs = calculate_atr(highs, lows, closes, 14)
    all_spans = [21, 34, 50, 55, 89, 100, 144, 200, 233]
    ema_dict = calculate_ema_dict(closes, all_spans)
    
    start_idx = max(fast_p, mid_p, slow_p) + 10
    if start_idx >= n - 10:
        return []
        
    ef = ema_dict[fast_p]
    em = ema_dict[mid_p]
    es = ema_dict[slow_p]
    
    is_fixed = exit_mode.startswith("fixed_")
    fixed_rr = float(exit_mode.split("_")[1]) if is_fixed else 0.0
    
    trades = []
    in_trade = False
    trade_side = 0
    entry_p = 0.0
    sl_p = 0.0
    r_dist = 0.0
    entry_i = 0
    max_fav_r = 0.0
    
    for i in range(start_idx, n - 1):
        if in_trade:
            bh = highs[i]
            bl = lows[i]
            bc = closes[i]
            exited = False
            exit_r = 0.0
            
            cur_r = (entry_p - bl) / r_dist if trade_side == -1 else (bh - entry_p) / r_dist
            if cur_r > max_fav_r:
                max_fav_r = cur_r
                
            if trade_side == -1: # SHORT
                if is_fixed:
                    if bh >= sl_p:
                        exit_r = -1.0; exited = True
                    elif bl <= (entry_p - fixed_rr * r_dist):
                        exit_r = fixed_rr; exited = True
                elif exit_mode == "step_trail":
                    if max_fav_r >= 6.0:
                        sl_p = min(sl_p, ef[i])
                    elif max_fav_r >= 4.0:
                        sl_p = min(sl_p, entry_p - 2.0 * r_dist)
                    elif max_fav_r >= 2.0:
                        sl_p = min(sl_p, entry_p - 0.5 * r_dist)
                    if bh >= sl_p:
                        exit_r = (entry_p - sl_p) / r_dist; exited = True
                elif exit_mode == "trail_89":
                    if bc > em[i]:
                        exit_r = (entry_p - bc) / r_dist; exited = True
                    elif bh >= sl_p:
                        exit_r = -1.0; exited = True
                elif exit_mode == "trail_55":
                    if bc > ema_dict[55][i]:
                        exit_r = (entry_p - bc) / r_dist; exited = True
                    elif bh >= sl_p:
                        exit_r = -1.0; exited = True
            elif trade_side == 1: # LONG
                if is_fixed:
                    if bl <= sl_p:
                        exit_r = -1.0; exited = True
                    elif bh >= (entry_p + fixed_rr * r_dist):
                        exit_r = fixed_rr; exited = True
                elif exit_mode == "step_trail":
                    if max_fav_r >= 6.0:
                        sl_p = max(sl_p, ef[i])
                    elif max_fav_r >= 4.0:
                        sl_p = max(sl_p, entry_p + 2.0 * r_dist)
                    elif max_fav_r >= 2.0:
                        sl_p = max(sl_p, entry_p + 0.5 * r_dist)
                    if bl <= sl_p:
                        exit_r = (sl_p - entry_p) / r_dist; exited = True
                elif exit_mode == "trail_89":
                    if bc < em[i]:
                        exit_r = (bc - entry_p) / r_dist; exited = True
                    elif bl <= sl_p:
                        exit_r = -1.0; exited = True
                elif exit_mode == "trail_55":
                    if bc < ema_dict[55][i]:
                        exit_r = (bc - entry_p) / r_dist; exited = True
                    elif bl <= sl_p:
                        exit_r = -1.0; exited = True
                        
            if exited:
                capped_r = min(exit_r, 15.0) - spread_cost_r
                trades.append({
                    'symbol': symbol,
                    'timeframe': tf,
                    'sector': classify_asset(symbol),
                    'entry_ts': timestamps[entry_i],
                    'exit_ts': timestamps[i],
                    'r': capped_r,
                    'side': trade_side
                })
                in_trade = False
            continue
            
        # Entry Logic (Tiered Support: EMA 89 or EMA 233)
        c_c = closes[i]
        h_i = highs[i]
        l_i = lows[i]
        a_i = atrs[i]
        
        sig = 0
        shelf_type = None
        
        if (em[i] < es[i]): # Bearish
            if u_wicks[i] >= 0.25:
                if (h_i >= em[i] * 0.998 or h_i >= ef[i] * 0.998) and (c_c <= em[i]):
                    sig = -1; shelf_type = "MID"
                elif (h_i >= es[i] * 0.998) and (c_c <= es[i]):
                    sig = -1; shelf_type = "SLOW"
        elif (em[i] > es[i]): # Bullish
            if l_wicks[i] >= 0.25:
                if (l_i <= em[i] * 1.002 or l_i <= ef[i] * 1.002) and (c_c >= em[i]):
                    sig = 1; shelf_type = "MID"
                elif (l_i <= es[i] * 1.002) and (c_c >= es[i]):
                    sig = 1; shelf_type = "SLOW"
                    
        if sig != 0:
            next_i = i + 1
            if next_i >= n: break
            ep = opens[next_i]
            
            if shelf_type == "SLOW":
                sp = (max(es[i], h_i) + 0.35 * a_i) if sig == -1 else (min(es[i], l_i) - 0.35 * a_i)
            else:
                sp = (h_i + 0.30 * a_i) if sig == -1 else (l_i - 0.30 * a_i)
                
            rd = abs(ep - sp)
            if rd <= 0: continue
            
            in_trade = True
            trade_side = sig
            entry_p = ep
            sl_p = sp
            r_dist = rd
            entry_i = next_i
            max_fav_r = 0.0
            
    return trades

def simulate_portfolio_event_driven(
    curated_basket,
    initial_capital=5000.0,
    max_concurrent=6,
    max_per_sector=2
):
    """Simulates multi-asset portfolio with zero lookahead (strict event queue)."""
    ribbon_map = {
        'Fib_89_233': (55, 89, 233),
        'Fib_34_89': (34, 89, 233),
        'Fib_21_55': (21, 55, 144),
        'Classic_50_200': (50, 100, 200),
        'Fib_34_89_233': (34, 89, 233)
    }
    
    all_trades = []
    for row in curated_basket:
        f_p, m_p, s_p = ribbon_map[row['ribbon']]
        if row.get('timeframe') == '4H_1H' or row.get('model_type') == 'MTF_CONFLUENCE':
            t_list = get_mtf_trades_for_portfolio(row['symbol'], f_p, m_p, s_p, row['strategy'])
        else:
            t_list = get_asset_trades(row['symbol'], row['timeframe'], f_p, m_p, s_p, row['strategy'])
        all_trades.extend(t_list)
        
    if not all_trades:
        return {}
        
    results = {}
    for r_label, fixed_dollar, pct_risk in [
        ('Conservative Fixed $25 (0.5%)', 25.0, None),
        ('Institutional Standard $50 (1.0%)', 50.0, None),
        ('Dynamic Compounding 0.5%', None, 0.005),
        ('Aggressive Compounding 1.0%', None, 0.01)
    ]:
        equity = initial_capital
        peak_equity = equity
        max_dd_val = 0.0
        
        # Build timeline events: (timestamp, event_type, trade_data)
        # Event types: 0 = EXIT, 1 = ENTRY (so exits process before entries at same timestamp!)
        events = []
        for t in all_trades:
            events.append((t['entry_ts'], 1, t))
            events.append((t['exit_ts'], 0, t))
            
        events.sort(key=lambda x: (x[0], x[1]))
        
        active_positions = {}
        executed_trades = []
        equity_timeline = [(events[0][0], equity)]
        
        for ts, ev_type, trade in events:
            sym = trade['symbol']
            
            if ev_type == 0: # EXIT EVENT
                if sym in active_positions and active_positions[sym]['entry_ts'] == trade['entry_ts']:
                    pos_info = active_positions.pop(sym)
                    pnl = pos_info['dollar_risk'] * trade['r']
                    equity += pnl
                    if equity > peak_equity:
                        peak_equity = equity
                    dd = peak_equity - equity
                    if dd > max_dd_val:
                        max_dd_val = dd
                    equity_timeline.append((ts, equity))
                    
            elif ev_type == 1: # ENTRY EVENT
                # Concurrency limit check
                if len(active_positions) >= max_concurrent:
                    continue
                # Single active position per asset check
                if sym in active_positions:
                    continue
                # Sector balance check
                sector_count = sum(1 for p in active_positions.values() if p['sector'] == trade['sector'])
                if sector_count >= max_per_sector:
                    continue
                    
                # Calculate position risk dollar amount from current realized equity
                dollar_risk = fixed_dollar if fixed_dollar is not None else (equity * pct_risk)
                active_positions[sym] = {
                    'entry_ts': trade['entry_ts'],
                    'dollar_risk': dollar_risk,
                    'sector': trade['sector']
                }
                executed_trades.append({
                    'symbol': sym,
                    'sector': trade['sector'],
                    'r': trade['r'],
                    'entry_ts': trade['entry_ts'],
                    'exit_ts': trade['exit_ts']
                })
                
        df_exec = pd.DataFrame(executed_trades)
        if len(df_exec) == 0:
            continue
            
        wins = df_exec[df_exec['r'] > 0]
        total_days = max(1.0, (df_exec['exit_ts'].max() - df_exec['entry_ts'].min()) / 86400.0)
        
        profit = equity - initial_capital
        roi_pct = (profit / initial_capital) * 100.0
        max_dd_pct = (max_dd_val / peak_equity) * 100.0 if peak_equity > 0 else 0.0
        
        annualized_roi = ((max(0.01, equity) / initial_capital) ** (365.25 / total_days) - 1.0) * 100.0
        calmar = annualized_roi / max(max_dd_pct, 1.0)
        
        daily_usd = profit / total_days
        daily_inr = daily_usd * 87.0
        monthly_usd = daily_usd * 30.0
        monthly_inr = daily_inr * 30.0
        
        results[r_label] = {
            'trades': len(df_exec),
            'win_rate': round(len(wins) / len(df_exec) * 100.0, 1),
            'final_equity': round(equity, 2),
            'profit': round(profit, 2),
            'roi_pct': round(roi_pct, 1),
            'annualized_roi': round(annualized_roi, 1),
            'max_dd_pct': round(max_dd_pct, 1),
            'calmar_ratio': round(calmar, 2),
            'daily_usd': round(daily_usd, 2),
            'daily_inr': round(daily_inr, 0),
            'monthly_usd': round(monthly_usd, 2),
            'monthly_inr': round(monthly_inr, 0),
            'total_days': round(total_days, 1)
        }
        
    return results

def main():
    sector_csv = os.path.join(AUDIT_DIR, "delta_sector_leaderboard.csv")
    if not os.path.exists(sector_csv):
        print(f"Error: {sector_csv} not found. Run run_full_business_audit.py first.")
        return
        
    df_sec = pd.read_csv(sector_csv)
    # Pick top 2 per sector for an all-weather 10-asset institutional basket
    basket_df = df_sec.groupby('sector').head(2).reset_index(drop=True)
    
    print("\n" + "="*120)
    print("INSTITUTIONAL SECTOR-BALANCED WEALTH BASKET (10 ASSETS, MAX 6 CONCURRENT, EVENT-DRIVEN)")
    print("="*120)
    cols = ['symbol', 'sector', 'timeframe', 'strategy', 'ribbon', 'win_rate', 'profit_factor', 'calmar_ratio']
    print(basket_df[cols].to_string(index=False))
    
    basket = basket_df.to_dict('records')
    results = simulate_portfolio_event_driven(basket, initial_capital=5000.0, max_concurrent=6, max_per_sector=2)
    
    print("\n" + "="*120)
    print("$5,000 BASE CAPITAL PERFORMANCE ACROSS SIZING TIERS (ZERO EQUITY LOOKAHEAD)")
    print("="*120)
    res_df = pd.DataFrame(results).T
    disp_cols = ['trades', 'win_rate', 'final_equity', 'profit', 'roi_pct', 'max_dd_pct', 'calmar_ratio', 'monthly_usd', 'monthly_inr']
    print(res_df[disp_cols].to_string())
    print("="*120 + "\n")

if __name__ == '__main__':
    main()

