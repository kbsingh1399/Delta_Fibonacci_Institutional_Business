import os
import sys
import pandas as pd
import numpy as np

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE_DIR = os.path.join(BASE_DIR, "engine")
if ENGINE_DIR not in sys.path:
    sys.path.insert(0, ENGINE_DIR)
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from universe_screener import load_snapshot, screen
from cluster_risk_governor import get_asset_cluster, CORRELATED_CLUSTERS
import institutional_wyckoff_engine as we
from run_inr_portfolio_backtest import get_asset_trades, FEE_SLIPPAGE_DRAG_R

def simulate_shared_portfolio(trade_list, name="SHARED", max_concurrency=3, risk_per_trade=50.0, start_cap=5000.0, max_same_dir_crypto=2):
    events = []
    for idx, t in enumerate(trade_list):
        t['id'] = idx
        en_ts = int(t['entry_ts'])
        ex_ts = int(t['exit_ts'])
        order_exit = 0 if en_ts < ex_ts else 2
        events.append((en_ts, 1, 'ENTRY', t))
        events.append((ex_ts, order_exit, 'EXIT', t))
        
    events.sort(key=lambda x: (x[0], x[1]))
    
    active = {}
    equity = start_cap
    peak = start_cap
    min_eq = start_cap
    max_dd = 0.0
    exec_trades = []
    engine_counts = {}
    
    for ts, ev_order, ev_type, trade in events:
        sym = trade['symbol']
        cluster = trade['cluster']
        eng = trade.get('engine', 'DEFAULT')
        
        if ev_type == 'EXIT':
            if sym in active and active[sym]['id'] == trade['id']:
                pnl = trade['net_r'] * risk_per_trade
                equity += pnl
                if equity > peak: peak = equity
                if equity < min_eq: min_eq = equity
                dd = peak - equity
                if dd > max_dd: max_dd = dd
                del active[sym]
                exec_trades.append(trade)
                engine_counts[eng] = engine_counts.get(eng, 0) + 1
        elif ev_type == 'ENTRY':
            if sym in active: continue
            if len(active) >= max_concurrency: continue
            conflict = False
            for op_sym, op_tr in active.items():
                if op_tr['cluster'] == cluster and cluster in CORRELATED_CLUSTERS:
                    conflict = True
                    break
            if conflict: continue
            crypto_same_dir = sum(1 for op_tr in active.values() if op_tr.get('cluster') in ['CRYPTO_ALTS_TOKENS', 'MEME_COINS'] and op_tr.get('side') == trade.get('side'))
            if trade.get('cluster') in ['CRYPTO_ALTS_TOKENS', 'MEME_COINS'] and crypto_same_dir >= max_same_dir_crypto:
                continue
            active[sym] = trade
            
    pnl_tot = equity - start_cap
    ret_pct = (pnl_tot / start_cap) * 100.0
    max_dd_pct = (max_dd / peak) * 100.0 if peak > 0 else 0.0
    wr = sum(1 for t in exec_trades if t['net_r'] > 0) / len(exec_trades) * 100.0 if exec_trades else 0.0
    years = 926 / 365.25
    cagr = ((equity / start_cap) ** (1 / years) - 1.0) * 100.0 if equity > 0 else -100.0
    calmar = cagr / max_dd_pct if max_dd_pct > 0 else 0.0
    
    print(f"\n=======================================================================")
    print(f"SCENARIO: {name}")
    print(f"=======================================================================")
    print(f"Initial Capital:         ₹{start_cap:,.2f} INR")
    print(f"Final Realized Equity:   ₹{equity:,.2f} INR")
    print(f"Total Net Profit:        ₹{pnl_tot:,.2f} INR (+{ret_pct:.2f}%)")
    print(f"Total Executed Trades:   {len(exec_trades)}")
    print(f"Engine Breakdown:        {engine_counts}")
    print(f"Win Rate:                {wr:.2f}%")
    print(f"Peak Drawdown:           ₹{max_dd:,.2f} INR ({max_dd_pct:.2f}%)")
    print(f"Minimum Capital Floor:   ₹{min_eq:,.2f} INR")
    print(f"Annualized Return (CAGR): {cagr:.2f}%")
    print(f"Calmar Ratio:            {calmar:.2f}")
    return {
        'name': name, 'equity': equity, 'ret_pct': ret_pct, 'trades': len(exec_trades), 
        'max_dd_pct': max_dd_pct, 'calmar': calmar, 'min_eq': min_eq,
        'counts': engine_counts, 'pnl': pnl_tot
    }

def main():
    specs = we.load_deployable_specs(offline=True)
    alpha_specs = {k: v for k, v in specs.items() if v.get("cluster") != "CRYPTO_MAJORS"}
    leaderboard_file = os.path.join(BASE_DIR, "audit", "delta_calmar_leaderboard.csv")
    df_lead = pd.read_csv(leaderboard_file)
    df_liquid = df_lead[df_lead['symbol'].isin(alpha_specs.keys())].copy()
    
    ribbon_map = {
        'Fib_21_55_89': (21, 55, 89),
        'Fib_34_89_144': (34, 89, 144),
        'Fib_34_89_233': (34, 89, 233),
        'Fib_55_89_233': (55, 89, 233),
        'Trad_20_50_200': (20, 50, 200),
        'Trad_50_100_200': (50, 100, 200)
    }
    
    # 1. Baseline standard taker drag (0.138R)
    base_trades_taker = []
    # 2. Baseline with Maker TP rebate drag (0.05R)
    base_trades_maker = []
    
    for _, row in df_liquid.iterrows():
        sym = row['symbol']
        ribbon = ribbon_map.get(row['ribbon'], (34, 89, 233))
        # Standard taker
        tr_t = get_asset_trades(sym, row['timeframe'], ribbon[0], ribbon[1], ribbon[2], row['strategy'], spread_cost_r=0.138)
        for t in tr_t:
            c = dict(t)
            c['symbol'] = sym
            c['engine'] = 'BASELINE_TAKER'
            c['cluster'] = get_asset_cluster(sym)
            c['net_r'] = t['r']
            base_trades_taker.append(c)
        # Maker upgrade
        tr_m = get_asset_trades(sym, row['timeframe'], ribbon[0], ribbon[1], ribbon[2], row['strategy'], spread_cost_r=0.050)
        for t in tr_m:
            c = dict(t)
            c['symbol'] = sym
            c['engine'] = 'BASELINE_MAKER'
            c['cluster'] = get_asset_cluster(sym)
            c['net_r'] = t['r']
            base_trades_maker.append(c)
            
    # 3. Wyckoff Trades
    candidates_wyckoff, dfs = we.scan_historical_candidates(
        os.path.join(BASE_DIR, "data"), alpha_specs,
        min_sep_atr=0.25, max_sep_atr=2.50, max_vol_ratio=1.20, max_cost_r=0.12
    )
    wyckoff_trades = []
    for c in candidates_wyckoff:
        sym = c['symbol']
        df = dfs[sym]
        spec = alpha_specs[sym]
        sim = we.simulate_trade_path_exact(
            df=df, entry_idx=c['bar_idx'], sig=c['side'],
            entry=c['entry'], sl=c['sl'], contracts=c['base_contracts'],
            spec=spec, exit_policy="hybrid"
        )
        if sim is not None and sim.get('exit_time') is not None:
            init_risk = abs(c['entry'] - c['sl']) * c['base_contracts'] * spec['contract_value'] * 85.0
            net_r = sim['net_pnl_inr'] / max(init_risk, 1e-6) if init_risk > 0 else 0.0
            wyckoff_trades.append({
                'symbol': sym, 'engine': 'WYCKOFF_HYBRID', 'cluster': spec['cluster'],
                'entry_ts': int(pd.Timestamp(c['timestamp']).timestamp()),
                'exit_ts': int(pd.Timestamp(sim['exit_time']).timestamp()),
                'net_r': net_r, 'side': c['side']
            })

    # Run comparisons
    print("\n" + "#"*75)
    print("RUNNING MULTI-SCENARIO TOURNAMENT")
    print("#"*75)
    
    # 1. Baseline Taker (Current)
    res1 = simulate_shared_portfolio(base_trades_taker, "1. Current Baseline Alpha Basket (Taker 0.138R)")
    # 2. Baseline with Maker TP Rebates (Arena upgrade 1)
    res2 = simulate_shared_portfolio(base_trades_maker, "2. Baseline Alpha Basket + Maker TP Rebate (0.05R Friction)")
    # 3. Wyckoff Alone
    res3 = simulate_shared_portfolio(wyckoff_trades, "3. Wyckoff Absorption Alone (High Selectivity 601 Trades)")
    # 4. Shared Parallel (Unpartitioned 3 slots FCFS)
    res4 = simulate_shared_portfolio(base_trades_taker + wyckoff_trades, "4. Shared Parallel (Unpartitioned 3 Slots FCFS)")
    # 5. Partitioned Parallel: ₹2,500 + 2 Slots for Baseline Maker | ₹2,500 + 1 Slot for Wyckoff
    res5_base = simulate_shared_portfolio(base_trades_maker, "5a. Partitioned Baseline Maker (₹2,500, 2 Slots, 1R=₹25)", max_concurrency=2, risk_per_trade=25.0, start_cap=2500.0)
    res5_wyck = simulate_shared_portfolio(wyckoff_trades, "5b. Partitioned Wyckoff (₹2,500, 1 Slot, 1R=₹25)", max_concurrency=1, risk_per_trade=25.0, start_cap=2500.0)
    
    tot_comb_eq = res5_base['equity'] + res5_wyck['equity']
    tot_comb_pnl = res5_base['pnl'] + res5_wyck['pnl']
    tot_ret_pct = (tot_comb_pnl / 5000.0) * 100.0
    print(f"\n=======================================================================")
    print(f"5c. COMBINED PARTITIONED DUAL-ENGINE ACCOUNT TOTAL (₹5,000 Starting)")
    print(f"=======================================================================")
    print(f"Combined Realized Equity: ₹{tot_comb_eq:,.2f} INR (+{tot_ret_pct:.2f}%)")
    print(f"Combined Profit:          ₹{tot_comb_pnl:,.2f} INR")
    print(f"Baseline Component:       ₹{res5_base['equity']:,.2f} (from ₹2,500)")
    print(f"Wyckoff Component:        ₹{res5_wyck['equity']:,.2f} (from ₹2,500)")
    print(f"=======================================================================")

if __name__ == "__main__":
    main()
