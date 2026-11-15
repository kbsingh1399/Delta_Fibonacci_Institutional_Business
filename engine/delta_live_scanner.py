"""delta_live_scanner.py: Live Institutional Scanner for Delta Exchange India.
Scans the Curated 250-Asset Catalog for genuine Fibonacci Prime Rejections (EMA 89 and EMA 233).
Features:
- Single Entry Invariant: Tracks active positions in 'active_positions.json' to prevent duplicate alerts.
- Tiered Support Detection: Catches both standard EMA 89 wicks and deep discount EMA 233 sweeps.
- Multi-Sector Filtering: Classifies opportunities across Metals, TradFi, Crypto Majors, and Altcoins.
- Dual-Timeframe Execution: Scans 4H and 1H candles.
- Dynamic Lot Sizer: Computes exact integer contracts, 10X margin, and INR risk for ₹5,000 capital.
- Cluster Risk Governor: Validates sub-cluster correlations, max active trade counts, and portfolio heat.
"""

import os
import sys
import json
import time
import glob
import tempfile
import argparse
import urllib.request
import pandas as pd
import numpy as np

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.join(BASE_DIR, "engine") not in sys.path:
    sys.path.insert(0, os.path.join(BASE_DIR, "engine"))

from asset_universe_classifier import classify_asset, get_sector_display_name, ALL_SECTORS
from delta_min_lot_sizer import calculate_delta_order_sizing, fetch_delta_products
from cluster_risk_governor import validate_new_trade_entry, get_asset_cluster

ACTIVE_POS_FILE = os.path.join(BASE_DIR, "engine", "active_positions.json")
DELTA_API = "https://api.india.delta.exchange"
HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}

def calculate_ema(series, span):
    return series.ewm(span=span, adjust=False).mean()

def calculate_atr(df, period=14):
    high = df['high'].to_numpy(dtype=float)
    low = df['low'].to_numpy(dtype=float)
    close = df['close'].to_numpy(dtype=float)
    n = len(df)
    tr = np.zeros(n)
    tr[0] = high[0] - low[0]
    for i in range(1, n):
        tr[i] = max(high[i] - low[i], abs(high[i] - close[i-1]), abs(low[i] - close[i-1]))
    atr = pd.Series(tr).ewm(alpha=1.0/period, adjust=False).mean().bfill().to_numpy()
    return atr

def load_active_positions():
    if os.path.exists(ACTIVE_POS_FILE):
        for _ in range(5):
            try:
                with open(ACTIVE_POS_FILE, "r") as f:
                    return json.load(f)
            except Exception:
                time.sleep(0.05)
    return {}

def fetch_recent_candles(symbol, resolution="240", count=1200, use_mark=False):
    now = int(time.time())
    start = now - (count * 4 * 3600 if resolution == "240" else count * 3600)
    target_sym = f"MARK:{symbol}" if (use_mark and not symbol.startswith("MARK:")) else symbol
    url = f"{DELTA_API}/v2/chart/history?symbol={target_sym}&resolution={resolution}&from={start}&to={now}"
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        res = data.get('result', {})
        t = res.get('t', [])
        if not t or len(t) < 50:
            if not use_mark:
                return fetch_recent_candles(symbol, resolution=resolution, count=count, use_mark=True)
            return None
        df = pd.DataFrame({
            'timestamp': t,
            'open': res.get('o', []),
            'high': res.get('h', []),
            'low': res.get('l', []),
            'close': res.get('c', []),
            'volume': res.get('v', [])
        })
        df = df.sort_values('timestamp').reset_index(drop=True)
        return df
    except Exception:
        if not use_mark:
            return fetch_recent_candles(symbol, resolution=resolution, count=count, use_mark=True)
        return None

RIBBON_MAP = {
    'Fib_89_233': (55, 89, 233),
    'Fib_34_89': (34, 89, 233),
    'Fib_21_55': (21, 55, 144),
    'Classic_50_200': (50, 100, 200),
    'Fib_21_55_89': (21, 55, 89),
    'Fib_34_89_144': (34, 89, 144),
    'Fib_34_89_233': (34, 89, 233),
    'Fib_55_89_233': (55, 89, 233),
    'Trad_20_50_200': (20, 50, 200),
    'Trad_50_100_200': (50, 100, 200)
}

def load_leaderboard_specs():
    leaderboard_file = os.path.join(BASE_DIR, "audit", "delta_calmar_leaderboard.csv")
    if not os.path.exists(leaderboard_file):
        return {}
    try:
        df = pd.read_csv(leaderboard_file)
        specs = {}
        for _, r in df.iterrows():
            sym = str(r['symbol']).strip()
            rib_str = str(r.get('ribbon', 'Fib_89_233')).strip()
            rib = RIBBON_MAP.get(rib_str, (55, 89, 233))
            tf = str(r.get('timeframe', '4H')).strip()
            if tf == '4H_1H':
                tf = '4H'
            strat = str(r.get('strategy', 'fixed_3.0')).strip()
            specs[sym] = {
                'symbol': sym,
                'timeframe': tf,
                'ribbon_name': rib_str,
                'ribbon': rib,
                'strategy': strat,
                'win_rate': float(r.get('win_rate', 0)),
                'net_r': float(r.get('net_r', 0)),
                'calmar_ratio': float(r.get('calmar_ratio', 0))
            }
        return specs
    except Exception:
        return {}

def scan_asset_setup(symbol, tf=None, equity_inr=5000.0, risk_pct=0.01, leverage=10.0, products_dict=None, live_mark_price=None, max_bar_age_sec=None, ribbon=None, strategy=None):
    """Scans an asset for Tiered Fibonacci Rejections enforcing Single Entry, Freshness, and Lot Sizing."""
    specs = load_leaderboard_specs()
    spec = specs.get(symbol, {})
    
    tf = tf or spec.get('timeframe', '4H')
    ribbon = ribbon or spec.get('ribbon', (55, 89, 233))
    strategy = strategy or spec.get('strategy', 'fixed_3.0')
    
    fast_p, mid_p, slow_p = ribbon
    res_code = "240" if tf == "4H" else "60"
    df = fetch_recent_candles(symbol, resolution=res_code, count=1200, use_mark=False)
    if df is None or len(df) < max(250, slow_p + 15):
        return None

    if live_mark_price is not None and live_mark_price > 0:
        df.loc[df.index[-1], 'close'] = live_mark_price
        if live_mark_price > df['high'].iloc[-1]:
            df.loc[df.index[-1], 'high'] = live_mark_price
        if live_mark_price < df['low'].iloc[-1]:
            df.loc[df.index[-1], 'low'] = live_mark_price

    closes = df['close']
    ef = calculate_ema(closes, fast_p).to_numpy()
    em = calculate_ema(closes, mid_p).to_numpy()
    es = calculate_ema(closes, slow_p).to_numpy()
    atr = calculate_atr(df, 14)
    
    i = len(df) - 1 # Forming candle
    prev = i - 1    # Confirmed closed candle
    
    bar_duration = 14400 if tf == "4H" else 3600
    candle_start_ts = int(df['timestamp'].iloc[prev])
    candle_close_ts = candle_start_ts + bar_duration

    # Freshness Gate: Reject stale setups from candles that closed > max_bar_age_sec ago
    if max_bar_age_sec is None:
        max_bar_age_sec = 900 if tf == "4H" else 300
    elapsed_since_close = int(time.time()) - candle_close_ts
    if elapsed_since_close > max_bar_age_sec:
        return None

    o = df['open'].iloc[prev]
    h = df['high'].iloc[prev]
    l = df['low'].iloc[prev]
    c = df['close'].iloc[prev]
    rng = h - l
    if rng <= 0:
        return None
        
    curr_fast = ef[prev]
    curr_mid = em[prev]
    curr_slow = es[prev]
    curr_atr = atr[prev]
    
    # Macro Trend / Regime Definition (Mid vs Slow EMA Gate)
    if curr_mid > curr_slow:
        regime = "BULLISH"
    elif curr_mid < curr_slow:
        regime = "BEARISH"
    else:
        return None
        
    # Check Active Positions file to enforce Single Entry Invariant
    active_pos = load_active_positions()
    if symbol in active_pos:
        last_entry_ts = active_pos[symbol].get('entry_ts', 0)
        if (df['timestamp'].iloc[prev] - last_entry_ts) < (12 * bar_duration):
            return None
            
    setup = None
    
    # Bullish setup check (Tiered Support: Mid EMA or Slow EMA)
    if regime == "BULLISH":
        lower_wick = min(o, c) - l
        wick_ratio = lower_wick / rng
        
        shelf = None
        if (l <= curr_mid * 1.002 or l <= curr_fast * 1.002) and (c >= curr_mid):
            shelf = f"EMA_{mid_p}"
        elif (l <= curr_slow * 1.002) and (c >= curr_slow):
            shelf = f"EMA_{slow_p}"
            
        if shelf and wick_ratio >= 0.25:
            # Drift gate: ensure live execution price is within 0.5% of confirmed candle close
            if live_mark_price is not None and live_mark_price > 0:
                drift_pct = abs(live_mark_price - c) / c
                if drift_pct > 0.005:
                    return None
                entry = float(live_mark_price)
            else:
                entry = float(c)

            stop = (min(curr_slow, l) - 0.35 * curr_atr) if shelf == f"EMA_{slow_p}" else (l - 0.30 * curr_atr)
            if entry <= stop:
                return None
            risk_dist = entry - stop
            if risk_dist > 0:
                is_fixed = strategy.startswith("fixed_")
                fixed_rr = float(strategy.split("_")[1]) if is_fixed else 3.0
                sizing = calculate_delta_order_sizing(
                    symbol, entry, stop, equity_inr=equity_inr, risk_pct=risk_pct, leverage=leverage, products_dict=products_dict
                )
                setup = {
                    'symbol': symbol,
                    'sector': classify_asset(symbol),
                    'cluster': get_asset_cluster(symbol),
                    'tf': tf,
                    'strategy': strategy,
                    'ribbon': ribbon,
                    'regime': regime,
                    'shelf': shelf,
                    'action': 'BUY / LONG',
                    'entry': round(entry, 4),
                    'stop': round(stop, 4),
                    'risk_distance': round(risk_dist, 4),
                    't1': round(entry + (1.5 * risk_dist), 4),
                    't2': round(entry + (fixed_rr * risk_dist), 4),
                    't3': round(entry + (5.0 * risk_dist), 4),
                    'rr': fixed_rr,
                    'contracts': sizing['contracts'],
                    'contract_value': sizing['contract_value'],
                    'underlying_units': sizing['underlying_units'],
                    'effective_leverage': sizing.get('effective_leverage', leverage),
                    'notional_inr': sizing['notional_inr'],
                    'margin_10x_inr': sizing['margin_10x_inr'],
                    'risk_inr': sizing['actual_risk_inr'],
                    'risk_pct': sizing['actual_risk_pct'],
                    'sizing_status': sizing['status'],
                    'wick_pct': round(wick_ratio * 100.0, 1),
                    'recent_pct_change': round((df['close'].iloc[prev] / df['close'].iloc[prev - 2] - 1.0) * 100.0, 2),
                    'entry_ts': int(df['timestamp'].iloc[prev])
                }
                
    # Bearish setup check (Tiered Resistance: Mid EMA or Slow EMA)
    elif regime == "BEARISH":
        upper_wick = h - max(o, c)
        wick_ratio = upper_wick / rng
        
        shelf = None
        if (h >= curr_mid * 0.998 or h >= curr_fast * 0.998) and (c <= curr_mid):
            shelf = f"EMA_{mid_p}"
        elif (h >= curr_slow * 0.998) and (c <= curr_slow):
            shelf = f"EMA_{slow_p}"
            
        if shelf and wick_ratio >= 0.25:
            # Drift gate: ensure live execution price is within 0.5% of confirmed candle close
            if live_mark_price is not None and live_mark_price > 0:
                drift_pct = abs(live_mark_price - c) / c
                if drift_pct > 0.005:
                    return None
                entry = float(live_mark_price)
            else:
                entry = float(c)

            stop = (max(curr_slow, h) + 0.35 * curr_atr) if shelf == f"EMA_{slow_p}" else (h + 0.30 * curr_atr)
            if entry >= stop:
                return None
            risk_dist = stop - entry
            if risk_dist > 0:
                is_fixed = strategy.startswith("fixed_")
                fixed_rr = float(strategy.split("_")[1]) if is_fixed else 3.0
                sizing = calculate_delta_order_sizing(
                    symbol, entry, stop, equity_inr=equity_inr, risk_pct=risk_pct, leverage=leverage, products_dict=products_dict
                )
                setup = {
                    'symbol': symbol,
                    'sector': classify_asset(symbol),
                    'cluster': get_asset_cluster(symbol),
                    'tf': tf,
                    'strategy': strategy,
                    'ribbon': ribbon,
                    'regime': regime,
                    'shelf': shelf,
                    'action': 'SELL / SHORT',
                    'entry': round(entry, 4),
                    'stop': round(stop, 4),
                    'risk_distance': round(risk_dist, 4),
                    't1': round(entry - (1.5 * risk_dist), 4),
                    't2': round(entry - (fixed_rr * risk_dist), 4),
                    't3': round(entry - (5.0 * risk_dist), 4),
                    'rr': fixed_rr,
                    'contracts': sizing['contracts'],
                    'contract_value': sizing['contract_value'],
                    'underlying_units': sizing['underlying_units'],
                    'effective_leverage': sizing.get('effective_leverage', leverage),
                    'notional_inr': sizing['notional_inr'],
                    'margin_10x_inr': sizing['margin_10x_inr'],
                    'risk_inr': sizing['actual_risk_inr'],
                    'risk_pct': sizing['actual_risk_pct'],
                    'sizing_status': sizing['status'],
                    'wick_pct': round(wick_ratio * 100.0, 1),
                    'recent_pct_change': round((df['close'].iloc[prev] / df['close'].iloc[prev - 2] - 1.0) * 100.0, 2),
                    'entry_ts': int(df['timestamp'].iloc[prev])
                }
                
    return setup

def main():
    parser = argparse.ArgumentParser(description="Delta Exchange India Institutional Fibonacci Scanner")
    parser.add_argument("--tf", choices=["4H", "1H", "ALL"], default="4H", help="Timeframe to scan")
    parser.add_argument("--sector", choices=ALL_SECTORS + ["ALL"], default="ALL", help="Asset sector filter")
    parser.add_argument("--capital_inr", type=float, default=5000.0, help="Base trading capital in INR (e.g. 5000.0)")
    parser.add_argument("--risk", type=float, default=0.01, help="Risk per trade (fraction, e.g. 0.01 for 1 percent)")
    parser.add_argument("--leverage", type=float, default=10.0, help="Leverage multiplier (e.g. 10.0 for 10X)")
    parser.add_argument("--all_assets", action="store_true", help="Scan entire 242-asset universe instead of top 40")
    parser.add_argument("--alpha_basket", action="store_true", default=True, help="Scan the optimized Alpha Basket (exclude crypto major entry signals while tracking crash tape)")
    parser.add_argument("--include_majors", action="store_true", help="Include crypto majors in scan list")
    args = parser.parse_args()
    
    print("\nFetching live contract specs & liquidity filters from Delta Exchange India...")
    products = fetch_delta_products()
    active_positions = load_active_positions()
    
    turnover_map = {}
    try:
        from universe_screener import screen_deployable_universe
        df_deploy = screen_deployable_universe(min_24h_turnover_usd=100000.0)
        deployable_symbols = set(df_deploy['symbol'].tolist())
        for _, r in df_deploy.iterrows():
            t_inr = r['turnover_24h_usd'] * 85.0
            turnover_map[r['symbol']] = f"₹{t_inr/1e7:.2f} Cr" if t_inr >= 1e7 else f"₹{t_inr/1e5:.1f} Lakh"
    except Exception:
        deployable_symbols = set(products.keys())
        
    leaderboard_file = os.path.join(BASE_DIR, "audit", "delta_calmar_leaderboard.csv")
    if os.path.exists(leaderboard_file):
        df_lead = pd.read_csv(leaderboard_file)
        raw_symbols = df_lead['symbol'].drop_duplicates().tolist()
        symbols = [s for s in raw_symbols if s in deployable_symbols]
    else:
        symbols = [s for s in products.keys() if s in deployable_symbols]
        
    if args.sector != "ALL":
        symbols = [s for s in symbols if classify_asset(s) == args.sector]
    elif args.alpha_basket and not args.include_majors:
        symbols = [s for s in symbols if get_asset_cluster(s) != "CRYPTO_MAJORS"]
        
    timeframes = ["4H", "1H"] if args.tf == "ALL" else [args.tf]
    scan_list = symbols if args.all_assets else symbols[:40]
    
    print("\n" + "="*125)
    print(f"DELTA EXCHANGE INDIA LIVE SCANNER | BASE CAPITAL: ₹{args.capital_inr:,.0f} | 10X LEVERAGE | 1R TARGET: ₹{args.capital_inr*args.risk:,.0f}")
    print(f"Active Positions: {len(active_positions)} | Scanning: {len(scan_list)} symbols across {timeframes}")
    print("="*125)
    
    found_setups = []
    simulated_active = dict(active_positions)
    
    for sym in scan_list:
        for tf in timeframes:
            try:
                setup = scan_asset_setup(
                    sym, tf=tf, equity_inr=args.capital_inr, risk_pct=args.risk, leverage=args.leverage, products_dict=products
                )
                if setup:
                    governor_candidate = {
                        'symbol': setup['symbol'],
                        'actual_risk_inr': setup['risk_inr'],
                        'margin_10x_inr': setup['margin_10x_inr'],
                        'recent_pct_change': setup['recent_pct_change'],
                        'action': setup['action'],
                        'cluster': setup['cluster'],
                    }
                    is_gov_ok, gov_reason = validate_new_trade_entry(
                        governor_candidate,
                        simulated_active,
                        equity_inr=args.capital_inr
                    )
                    setup['turnover_inr'] = turnover_map.get(setup['symbol'], "N/A")
                    setup['governor_ok'] = is_gov_ok
                    setup['governor_msg'] = gov_reason
                    found_setups.append(setup)
                    
                    is_ready = is_gov_ok and setup['sizing_status'] != 'REJECTED_OVERSIZED'
                    if is_ready:
                        simulated_active[setup['symbol']] = {
                            'side': setup['action'],
                            'actual_risk_inr': setup['risk_inr'],
                            'margin_10x_inr': setup['margin_10x_inr'],
                            'cluster': setup['cluster'],
                        }
                    status_flag = "[READY]" if is_ready else "[BLOCKED]"
                    reason_suffix = "" if is_ready else f" -> {gov_reason if not is_gov_ok else setup['sizing_status']}"
                    print(f"  {status_flag} {setup['symbol']} ({setup['tf']}): {setup['action']} @ {setup['entry']} | Lots: {setup['contracts']} (10X Margin: ₹{setup['margin_10x_inr']:.1f}, Risk: ₹{setup['risk_inr']:.1f}, 24h Turn: {setup['turnover_inr']}){reason_suffix}")
            except Exception:
                continue
                
    print("\n" + "-"*125)
    if not found_setups:
        print("No active Fibonacci rejection setups currently forming on the selected assets.")
    else:
        df_res = pd.DataFrame(found_setups)
        display_cols = ['symbol', 'cluster', 'tf', 'action', 'shelf', 'entry', 'stop', 'contracts', '10x_margin_inr', 'risk_inr', 'risk_pct', 'turnover_inr', 'sizing_status', 'governor_ok']
        df_res['10x_margin_inr'] = df_res['margin_10x_inr'].apply(lambda x: f"₹{x:.1f}")
        df_res['risk_inr'] = df_res['risk_inr'].apply(lambda x: f"₹{x:.1f}")
        df_res['risk_pct'] = df_res['risk_pct'].apply(lambda x: f"{x:.2f}%")
        print(df_res[display_cols].to_string(index=False))
    print("="*125 + "\n")

if __name__ == '__main__':
    main()
