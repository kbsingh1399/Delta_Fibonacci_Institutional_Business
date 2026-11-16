import os
import sys
import pandas as pd
import numpy as np

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from engine.mt5_alpha_trader_monitor import OPTIMAL_ALPHA_BASKET

ALPHA_SYMBOLS = {item["symbol"]: item for item in OPTIMAL_ALPHA_BASKET}

def main():
    trades_path = os.path.join(BASE_DIR, "audit", "fundednext_h4_executed_trades.csv")
    if not os.path.exists(trades_path):
        print("Trades CSV not found.")
        return

    df = pd.read_csv(trades_path)
    df['entry_dt'] = pd.to_datetime(df['entry_time'])
    df['exit_dt'] = pd.to_datetime(df['exit_time'])

    # Filter for 2026 onwards and only the 28 Alpha Basket assets
    df_2026 = df[(df['entry_dt'] >= '2026-01-01') & (df['symbol'].isin(ALPHA_SYMBOLS.keys()))].copy()
    df_2026['cluster'] = df_2026['symbol'].map(lambda s: ALPHA_SYMBOLS[s]['cluster'])
    df_2026.sort_values('entry_dt', inplace=True)
    df_2026.reset_index(drop=True, inplace=True)

    print(f"Total Alpha Trades from Jan 2026 onwards: {len(df_2026)}")
    
    # 1. Asset-by-Asset Stats
    asset_rows = []
    for sym, cfg in ALPHA_SYMBOLS.items():
        sub = df_2026[df_2026['symbol'] == sym]
        n_trades = len(sub)
        if n_trades == 0:
            asset_rows.append({
                "symbol": sym, "cluster": cfg["cluster"], "setup": cfg["setup"],
                "trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
                "net_r": 0.0, "net_usd": 0.0, "profit_factor": 0.0
            })
            continue
        wins = len(sub[sub['net_r'] > 0])
        losses = len(sub[sub['net_r'] <= 0])
        wr = wins / n_trades * 100
        net_r = sub['net_r'].sum()
        net_usd = net_r * 10.0  # $10 risk per trade
        gross_win = sub[sub['net_r'] > 0]['net_r'].sum()
        gross_loss = abs(sub[sub['net_r'] <= 0]['net_r'].sum())
        pf = (gross_win / gross_loss) if gross_loss > 0 else 99.0

        asset_rows.append({
            "symbol": sym, "cluster": cfg["cluster"], "setup": cfg["setup"],
            "trades": n_trades, "wins": wins, "losses": losses, "win_rate": round(wr, 1),
            "net_r": round(net_r, 2), "net_usd": round(net_usd, 2), "profit_factor": round(pf, 2)
        })

    asset_df = pd.DataFrame(asset_rows)
    asset_df.sort_values('net_r', ascending=False, inplace=True)

    print("\n" + "=" * 95)
    print("2026 ALPHA BASKET INDIVIDUAL ASSET PERFORMANCE (Jan 2026 - Nov 2026)")
    print("=" * 95)
    print(f"{'Symbol':<9} | {'Cluster':<16} | {'Setup':<18} | {'Trades':<6} | {'Win%':<6} | {'Net R':<8} | {'Net USD ($10)':<14} | {'PF':<5}")
    print("-" * 95)
    for _, r in asset_df.iterrows():
        print(f"{r['symbol']:<9} | {r['cluster']:<16} | {r['setup']:<18} | {r['trades']:<6} | {r['win_rate']:<6.1f} | {r['net_r']:<8.2f} | ${r['net_usd']:<13.2f} | {r['profit_factor']:<5.2f}")

    # 2. Portfolio Chronological Simulation with Concurrency Governor
    # Rules: Max 3 concurrent positions total, Max 1 per cluster
    sim_trades = []
    active_positions = [] # (exit_dt, cluster, symbol)

    cum_r = 0.0
    peak_r = 0.0
    max_dd_r = 0.0
    r_curve = [0.0]

    for _, tr in df_2026.iterrows():
        ent_time = tr['entry_dt']
        cluster = tr['cluster']
        sym = tr['symbol']

        # Clear expired positions
        active_positions = [p for p in active_positions if p[0] > ent_time]

        # Check concurrency limits: max 3 total, max 1 per cluster
        if len(active_positions) >= 3:
            continue
        if any(p[1] == cluster for p in active_positions):
            continue

        # Accept trade
        active_positions.append((tr['exit_dt'], cluster, sym))
        sim_trades.append(tr)
        cum_r += tr['net_r']
        if cum_r > peak_r:
            peak_r = cum_r
        dd = peak_r - cum_r
        if dd > max_dd_r:
            max_dd_r = dd
        r_curve.append(cum_r)

    sim_df = pd.DataFrame(sim_trades)
    tot_trades = len(sim_df)
    tot_wins = len(sim_df[sim_df['net_r'] > 0])
    tot_losses = len(sim_df[sim_df['net_r'] <= 0])
    port_wr = (tot_wins / tot_trades * 100) if tot_trades > 0 else 0
    port_r = sim_df['net_r'].sum()
    port_usd = port_r * 10.0
    gross_w = sim_df[sim_df['net_r'] > 0]['net_r'].sum()
    gross_l = abs(sim_df[sim_df['net_r'] <= 0]['net_r'].sum())
    port_pf = gross_w / gross_l if gross_l > 0 else 99.0

    print("\n" + "=" * 95)
    print("2026 PORTFOLIO SIMULATION (MAX 3 CONCURRENT, MAX 1 PER CLUSTER, $10 RISK)")
    print("=" * 95)
    print(f"Total Trades Executed:    {tot_trades}")
    print(f"Wins / Losses:            {tot_wins} Wins / {tot_losses} Losses")
    print(f"Portfolio Win Rate:       {port_wr:.1f}%")
    print(f"Total Net Return (R):     +{port_r:.2f} R")
    print(f"Total Net Profit ($10):   +${port_usd:,.2f} USD")
    print(f"Portfolio Profit Factor:  {port_pf:.2f}")
    print(f"Max Portfolio Drawdown:   -{max_dd_r:.2f} R (-${max_dd_r * 10:,.2f} USD)")
    print(f"Calmar Ratio (2026):      {port_r / max_dd_r:.2f}")

    # 3. Monthly Breakdown for 2026
    sim_df['month'] = sim_df['entry_dt'].dt.to_period('M')
    monthly = sim_df.groupby('month').agg(
        trades=('net_r', 'count'),
        wins=('net_r', lambda x: (x > 0).sum()),
        net_r=('net_r', 'sum'),
    ).reset_index()
    monthly['win_rate'] = (monthly['wins'] / monthly['trades'] * 100).round(1)
    monthly['net_usd'] = (monthly['net_r'] * 10.0).round(2)

    print("\n" + "=" * 95)
    print("2026 MONTH-BY-MONTH BREAKDOWN")
    print("=" * 95)
    print(f"{'Month':<10} | {'Trades':<8} | {'Wins':<6} | {'Win Rate':<10} | {'Net Return (R)':<16} | {'Net Profit ($10 Risk)'}")
    print("-" * 95)
    for _, m in monthly.iterrows():
        print(f"{str(m['month']):<10} | {m['trades']:<8} | {m['wins']:<6} | {m['win_rate']:<10.1f}% | {m['net_r']:<+16.2f}R | +${m['net_usd']:,.2f}")

    # 4. Cluster Breakdown
    cluster_agg = sim_df.groupby('cluster').agg(
        trades=('net_r', 'count'),
        wins=('net_r', lambda x: (x > 0).sum()),
        net_r=('net_r', 'sum'),
    ).reset_index()
    cluster_agg['win_rate'] = (cluster_agg['wins'] / cluster_agg['trades'] * 100).round(1)
    cluster_agg['net_usd'] = (cluster_agg['net_r'] * 10.0).round(2)
    cluster_agg.sort_values('net_r', ascending=False, inplace=True)

    print("\n" + "=" * 95)
    print("2026 CLUSTER PERFORMANCE BREAKDOWN")
    print("=" * 95)
    print(f"{'Cluster':<18} | {'Trades':<8} | {'Win Rate':<10} | {'Net Return (R)':<16} | {'Net Profit ($10 Risk)'}")
    print("-" * 95)
    for _, c in cluster_agg.iterrows():
        print(f"{c['cluster']:<18} | {c['trades']:<8} | {c['win_rate']:<10.1f}% | {c['net_r']:<+16.2f}R | +${c['net_usd']:,.2f}")

if __name__ == "__main__":
    main()
