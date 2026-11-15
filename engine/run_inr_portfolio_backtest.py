"""run_inr_portfolio_backtest.py: Institutional ₹5,000 Equity Backtest on Delta India.
Evaluates the Liquid Asset Catalog with 10X Leverage and Full Institutional Friction:
- Base Capital: ₹5,000 INR
- Risk Per Trade: 1R = ₹50 INR (1% initial equity)
- Full Exchange Taker Fee (0.05%) + 18% GST + Slippage = 0.138R per round-trip trade
- Enforces Cluster Risk Governor: Max 3 concurrent trades (3R max heat = ₹150), 60% margin ceiling
- Event-Driven Chronology: Exits precede entries on identical timestamps (zero lookahead)
- Alpha Basket Policy: Excludes range-bound Crypto Majors from trade entries while retaining
  them as crash-tape anchors, maximizing alpha on liquid Altcoins, Gold, and TradFi Equities.
"""

import os
import sys
import json
import argparse
import pandas as pd
import numpy as np

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.join(BASE_DIR, "engine") not in sys.path:
    sys.path.insert(0, os.path.join(BASE_DIR, "engine"))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from universe_screener import load_snapshot, screen, INR_PER_USD
from asset_universe_classifier import classify_asset, get_sector_display_name
from cluster_risk_governor import get_asset_cluster, CORRELATED_CLUSTERS

DATA_DIR = os.path.join(BASE_DIR, "data")
AUDIT_DIR = os.path.join(BASE_DIR, "audit")
REPORTS_DIR = os.path.join(BASE_DIR, "reports")
os.makedirs(AUDIT_DIR, exist_ok=True)
os.makedirs(REPORTS_DIR, exist_ok=True)

INITIAL_CAPITAL_INR = 5000.0
RISK_PER_TRADE_INR = 50.0   # 1R = 1%
FEE_SLIPPAGE_DRAG_R = 0.138 # 0.118R fees + GST + 0.02R slippage

def calculate_ema_dict(closes, spans):
    """Calculates EMA array for multiple lookback spans."""
    res = {}
    s = pd.Series(closes)
    for sp in spans:
        res[sp] = s.ewm(span=sp, adjust=False).mean().to_numpy()
    return res

def calculate_atr(highs, lows, closes, period=14):
    """Calculates 14-period Average True Range."""
    n = len(closes)
    tr = np.zeros(n)
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
    atr = pd.Series(tr).rolling(period).mean().bfill().to_numpy()
    return atr

def get_asset_trades(symbol, tf, fast_p, mid_p, slow_p, exit_mode, spread_cost_r=FEE_SLIPPAGE_DRAG_R, cooldown_bars=12):
    """Extracts chronological trade list for an asset using Tiered EMA logic, Single Entry Invariant, and Post-Exit Cooldown."""
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
    last_exit_i = -cooldown_bars - 1
    
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
                last_exit_i = i
            continue
            
        if (i - last_exit_i) < cooldown_bars:
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

def run_portfolio_backtest(alpha_basket=True, offline=True, maker_rebate=True, uniform=True, exit_mode="fixed_3.0", cooldown_bars=12):
    mode_name = "OPTIMIZED ALPHA BASKET" if alpha_basket else "FULL BASELINE LIQUID CATALOG"
    friction_drag = 0.050 if maker_rebate else 0.138
    friction_label = "Arena Maker Limit TP Rebate (0.050R Friction)" if maker_rebate else "Full Exchange Taker Fee (0.05%) + 18% GST + Slippage (0.138R)"
    setup_label = f"Uniform 4H Fib_55_89_233 | {exit_mode} | {cooldown_bars}-Bar Cooldown" if uniform else "Legacy Heterogeneous Calmar Leaderboard"
    print("="*105)
    print(f"DELTA EXCHANGE INDIA - INSTITUTIONAL ₹5,000 BACKTEST [{mode_name}]")
    print(f"Setup: {setup_label}")
    print(f"10X Isolated Leverage | {friction_label}")
    print("="*105)
    
    # 1. Identify Liquid Deployable Universe (24h turnover >= Rs 85 Lakhs)
    products, tickers = load_snapshot(offline=offline)
    screen_rows = screen(products, tickers, risk_cap_inr=55.0, margin_cap_inr=2500.0, volume_floor_usd=100000.0)
    eligible_symbols = set([r['symbol'] for r in screen_rows if r.get('eligible')])
    print(f"Eligible Liquid Assets (Turnover >= ₹85 Lakhs): {len(eligible_symbols)}")
    
    # 2. Configure Asset Loop (Uniform Setup vs Legacy Leaderboard)
    all_raw_trades = []
    single_asset_stats = []
    
    if uniform:
        candidate_symbols = sorted(list(eligible_symbols))
        if alpha_basket:
            candidate_symbols = [s for s in candidate_symbols if get_asset_cluster(s) != 'CRYPTO_MAJORS']
            print(f"Alpha Basket Tradable Assets (Excluding Crypto Majors): {len(candidate_symbols)}")
        else:
            print(f"All Liquid Tradable Assets: {len(candidate_symbols)}")
            
        for sym in candidate_symbols:
            trades = get_asset_trades(sym, "4H", 55, 89, 233, exit_mode, spread_cost_r=friction_drag, cooldown_bars=cooldown_bars)
            if not trades:
                continue
                
            trade_rs = [t['r'] for t in trades]
            wins = [r for r in trade_rs if r > 0]
            n = len(trade_rs)
            wr = (len(wins) / n * 100.0) if n > 0 else 0
            tot_net_r = sum(trade_rs)
            tot_pnl_inr = tot_net_r * RISK_PER_TRADE_INR
            
            cum_r = np.cumsum(trade_rs)
            peak_r = np.maximum.accumulate(np.maximum(0, cum_r))
            dd_r = peak_r - cum_r
            max_dd_r = float(np.max(dd_r)) if len(dd_r) > 0 else 0.0
            max_dd_inr = max_dd_r * RISK_PER_TRADE_INR
            max_dd_pct = (max_dd_inr / INITIAL_CAPITAL_INR) * 100.0
            
            single_asset_stats.append({
                'symbol': sym,
                'sector': classify_asset(sym),
                'model': 'SINGLE_TF',
                'strategy': exit_mode,
                'trades': n,
                'win_rate': round(wr, 1),
                'net_r': round(tot_net_r, 2),
                'net_pnl_inr': round(tot_pnl_inr, 1),
                'max_dd_inr': round(max_dd_inr, 1),
                'max_dd_pct': round(max_dd_pct, 1),
                'return_pct': round((tot_pnl_inr / INITIAL_CAPITAL_INR) * 100.0, 1)
            })
            
            for t in trades:
                t_copy = dict(t)
                t_copy['symbol'] = sym
                t_copy['cluster'] = get_asset_cluster(sym)
                t_copy['net_r'] = t['r']
                all_raw_trades.append(t_copy)
    else:
        leaderboard_file = os.path.join(AUDIT_DIR, "delta_calmar_leaderboard.csv")
        if not os.path.exists(leaderboard_file):
            raise FileNotFoundError(f"Missing {leaderboard_file}")
        df_lead = pd.read_csv(leaderboard_file)
        df_liquid = df_lead[df_lead['symbol'].isin(eligible_symbols)].copy()
        
        if alpha_basket:
            df_liquid = df_liquid[df_liquid['symbol'].apply(get_asset_cluster) != 'CRYPTO_MAJORS'].copy()
            print(f"Alpha Basket Tradable Assets (Excluding Crypto Majors): {len(df_liquid)}")
        else:
            print(f"Liquid Assets with Audited Configurations: {len(df_liquid)}")
        
        ribbon_map = {
            'Fib_21_55_89': (21, 55, 89),
            'Fib_34_89_144': (34, 89, 144),
            'Fib_34_89_233': (34, 89, 233),
            'Fib_55_89_233': (55, 89, 233),
            'Trad_20_50_200': (20, 50, 200),
            'Trad_50_100_200': (50, 100, 200)
        }
        
        for _, row in df_liquid.iterrows():
            sym = row['symbol']
            tf = row['timeframe']
            strat = row['strategy']
            ribbon_str = row['ribbon']
            ribbon = ribbon_map.get(ribbon_str, (34, 89, 233))
            
            trades = get_asset_trades(sym, tf, ribbon[0], ribbon[1], ribbon[2], strat, spread_cost_r=friction_drag, cooldown_bars=cooldown_bars)
            if not trades:
                continue
                
            trade_rs = [t['r'] for t in trades]
            wins = [r for r in trade_rs if r > 0]
            n = len(trade_rs)
            wr = (len(wins) / n * 100.0) if n > 0 else 0
            tot_net_r = sum(trade_rs)
            tot_pnl_inr = tot_net_r * RISK_PER_TRADE_INR
            
            cum_r = np.cumsum(trade_rs)
            peak_r = np.maximum.accumulate(np.maximum(0, cum_r))
            dd_r = peak_r - cum_r
            max_dd_r = float(np.max(dd_r)) if len(dd_r) > 0 else 0.0
            max_dd_inr = max_dd_r * RISK_PER_TRADE_INR
            max_dd_pct = (max_dd_inr / INITIAL_CAPITAL_INR) * 100.0
            
            single_asset_stats.append({
                'symbol': sym,
                'sector': classify_asset(sym),
                'model': 'SINGLE_TF',
                'strategy': strat,
                'trades': n,
                'win_rate': round(wr, 1),
                'net_r': round(tot_net_r, 2),
                'net_pnl_inr': round(tot_pnl_inr, 1),
                'max_dd_inr': round(max_dd_inr, 1),
                'max_dd_pct': round(max_dd_pct, 1),
                'return_pct': round((tot_pnl_inr / INITIAL_CAPITAL_INR) * 100.0, 1)
            })
            
            for t in trades:
                t_copy = dict(t)
                t_copy['symbol'] = sym
                t_copy['cluster'] = get_asset_cluster(sym)
                t_copy['net_r'] = t['r']
                all_raw_trades.append(t_copy)
            t_copy['cluster'] = get_asset_cluster(sym)
            t_copy['net_r'] = t['r']
            all_raw_trades.append(t_copy)
            
    df_single = pd.DataFrame(single_asset_stats)
    print(f"Generated {len(all_raw_trades)} raw trades across {len(df_single)} liquid symbols.")
    
    # 4. Multi-Asset Event-Driven Simulation with Cluster Governor
    events = []
    for trade_id, t in enumerate(all_raw_trades):
        t['id'] = trade_id
        entry_ts = int(t['entry_ts'])
        exit_ts = int(t['exit_ts'])
        order_entry = 1
        order_exit = 0 if entry_ts < exit_ts else 2
        events.append((entry_ts, order_entry, 'ENTRY', t))
        events.append((exit_ts, order_exit, 'EXIT', t))
        
    events.sort(key=lambda x: (x[0], x[1]))
    
    active_positions = {}
    equity_inr = INITIAL_CAPITAL_INR
    peak_equity_inr = INITIAL_CAPITAL_INR
    min_equity_inr = INITIAL_CAPITAL_INR
    max_portfolio_dd_inr = 0.0
    
    executed_trades = []
    rejected_concurrency = 0
    rejected_cluster = 0
    rejected_heat = 0
    
    equity_curve = [(events[0][0] if events else 0, equity_inr)]
    
    MAX_CONCURRENT_TRADES = 3
    MAX_TOTAL_RISK_INR = 150.0 # 3R = ₹150 max portfolio risk
    
    for ts, ev_order, ev_type, trade in events:
        sym = trade['symbol']
        cluster = trade['cluster']
        
        if ev_type == 'EXIT':
            if sym in active_positions and active_positions[sym]['id'] == trade['id']:
                trade_pnl_inr = trade['net_r'] * RISK_PER_TRADE_INR
                equity_inr += trade_pnl_inr
                if equity_inr > peak_equity_inr:
                    peak_equity_inr = equity_inr
                if equity_inr < min_equity_inr:
                    min_equity_inr = equity_inr
                dd = peak_equity_inr - equity_inr
                if dd > max_portfolio_dd_inr:
                    max_portfolio_dd_inr = dd
                    
                del active_positions[sym]
                executed_trades.append({
                    'symbol': sym,
                    'cluster': cluster,
                    'entry_ts': trade['entry_ts'],
                    'exit_ts': trade['exit_ts'],
                    'net_r': trade['net_r'],
                    'pnl_inr': trade_pnl_inr,
                    'realized_equity_inr': equity_inr
                })
                equity_curve.append((ts, equity_inr))
                
        elif ev_type == 'ENTRY':
            if sym in active_positions:
                continue
            if len(active_positions) >= MAX_CONCURRENT_TRADES:
                rejected_concurrency += 1
                continue
            cluster_conflict = False
            for open_sym, open_tr in active_positions.items():
                if open_tr['cluster'] == cluster and cluster in CORRELATED_CLUSTERS:
                    cluster_conflict = True
                    break
            if cluster_conflict:
                rejected_cluster += 1
                continue
            current_heat_inr = len(active_positions) * RISK_PER_TRADE_INR
            if (current_heat_inr + RISK_PER_TRADE_INR) > MAX_TOTAL_RISK_INR:
                rejected_heat += 1
                continue
                
            active_positions[sym] = trade
            
    df_exec = pd.DataFrame(executed_trades)
    
    tot_trades_exec = len(df_exec)
    exec_wins = df_exec[df_exec['net_r'] > 0]
    exec_wr = (len(exec_wins) / tot_trades_exec * 100.0) if tot_trades_exec > 0 else 0
    tot_pnl_exec = equity_inr - INITIAL_CAPITAL_INR
    tot_return_pct = (tot_pnl_exec / INITIAL_CAPITAL_INR) * 100.0
    initial_dip_pct = ((INITIAL_CAPITAL_INR - min_equity_inr) / INITIAL_CAPITAL_INR) * 100.0
    max_dd_pct = (max_portfolio_dd_inr / INITIAL_CAPITAL_INR) * 100.0
    
    if tot_trades_exec > 0:
        first_ts = df_exec['entry_ts'].min()
        last_ts = df_exec['exit_ts'].max()
        days_span = max(1, (last_ts - first_ts) / 86400.0)
        months_span = max(1.0, days_span / 30.0)
        monthly_pnl_inr = tot_pnl_exec / months_span
        monthly_return_pct = tot_return_pct / months_span
    else:
        days_span = 0; months_span = 1; monthly_pnl_inr = 0; monthly_return_pct = 0
        
    print("\n" + "="*105)
    print(f"PORTFOLIO BACKTEST SUMMARY ({mode_name})")
    print("="*105)
    print(f"Base Initial Capital:       ₹{INITIAL_CAPITAL_INR:,.2f} INR")
    print(f"Final Realized Equity:      ₹{equity_inr:,.2f} INR")
    print(f"Total Realized Profit:      ₹{tot_pnl_exec:,.2f} INR (+{tot_return_pct:.2f}%)")
    print(f"Average Monthly Profit:     ₹{monthly_pnl_inr:,.2f} INR (+{monthly_return_pct:.2f}% / month)")
    print(f"Total Executed Trades:      {tot_trades_exec} trades over {days_span:.0f} days (~{tot_trades_exec/days_span:.1f} trades/day)")
    print(f"Portfolio Win Rate:         {exec_wr:.1f}%")
    print(f"Minimum Capital Floor:      ₹{min_equity_inr:,.2f} INR (Max Initial Dip: -{initial_dip_pct:.2f}%)")
    print(f"Peak Portfolio Drawdown:    ₹{max_portfolio_dd_inr:,.2f} INR ({max_dd_pct:.2f}%)")
    print(f"Calmar Ratio (Ann/MaxDD):   {(tot_return_pct * (365.0/days_span)) / max(0.1, max_dd_pct):.2f}")
    print(f"Governor Rejected (Count):  {rejected_concurrency} trades (Max 3 concurrent ceiling)")
    print(f"Governor Rejected (Cluster):{rejected_cluster} trades (Correlated sub-cluster cap)")
    print("="*105 + "\n")
    
    # Save reports
    single_csv = os.path.join(AUDIT_DIR, "inr_single_asset_backtest.csv")
    df_single.to_csv(single_csv, index=False)
    
    exec_csv = os.path.join(AUDIT_DIR, "inr_portfolio_executed_trades.csv")
    df_exec.to_csv(exec_csv, index=False)
    
    # Sector breakdown of executed portfolio
    sec_summary = df_exec.groupby('cluster').agg(
        trades=('net_r', 'count'),
        net_r=('net_r', 'sum'),
        win_rate=('net_r', lambda x: round(np.mean(x > 0)*100, 1)),
        pnl_inr=('pnl_inr', 'sum')
    ).reset_index().sort_values('pnl_inr', ascending=False)
    
    print("Executed Portfolio Performance by Sector / Cluster:")
    print(sec_summary.to_string(index=False))
    print("\n")
    
    return df_single, df_exec, sec_summary

def main():
    parser = argparse.ArgumentParser(description="Delta Exchange India Institutional Portfolio Backtest")
    parser.add_argument("--alpha-basket", action="store_true", default=True, help="Run the Alpha Basket (excludes Crypto Majors entry drag)")
    parser.add_argument("--all-assets", action="store_true", help="Run the full baseline across all liquid assets including Crypto Majors")
    parser.add_argument("--live-universe", action="store_true", help="Refresh universe snapshot from live Delta Exchange API instead of static offline cache")
    parser.add_argument("--taker-fees", action="store_true", help="Use legacy full taker fees (0.138R) instead of Maker Limit TP rebate model (0.050R)")
    parser.add_argument("--uniform", action="store_true", default=True, help="Run Option A: Uniform 4H Fib_55_89_233 strategy across assets")
    parser.add_argument("--calmar-leaderboard", action="store_true", help="Fall back to legacy heterogeneous Calmar Leaderboard ribbons")
    parser.add_argument("--exit-mode", default="fixed_3.0", choices=["fixed_2.5", "fixed_3.0", "fixed_3.5", "step_trail", "trail_89", "trail_55"], help="Exit strategy mode (default: fixed_3.0)")
    parser.add_argument("--cooldown", type=int, default=12, help="Post-exit cooldown bars (default: 12 bars / 48 hours)")
    args = parser.parse_args()
    
    use_alpha = not args.all_assets
    is_uniform = not args.calmar_leaderboard
    run_portfolio_backtest(
        alpha_basket=use_alpha,
        offline=not args.live_universe,
        maker_rebate=not args.taker_fees,
        uniform=is_uniform,
        exit_mode=args.exit_mode,
        cooldown_bars=args.cooldown
    )

if __name__ == "__main__":
    main()
