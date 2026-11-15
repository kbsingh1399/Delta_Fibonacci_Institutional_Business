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
    atr = pd.Series(tr).rolling(period).mean().bfill().to_numpy()
    return atr

def load_active_positions():
    if os.path.exists(ACTIVE_POS_FILE):
        try:
            with open(ACTIVE_POS_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_active_positions(pos_dict):
    try:
        dir_name = os.path.dirname(ACTIVE_POS_FILE)
        fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
        with open(fd, "w") as f:
            json.dump(pos_dict, f, indent=2)
        os.replace(tmp_path, ACTIVE_POS_FILE)
    except Exception:
        pass

def fetch_recent_candles(symbol, resolution="240", count=800):
    now = int(time.time())
    start = now - (count * 4 * 3600 if resolution == "240" else count * 3600)
    url = f"{DELTA_API}/v2/chart/history?symbol={symbol}&resolution={resolution}&from={start}&to={now}"
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        res = data.get('result', {})
        t = res.get('t', [])
        if not t or len(t) < 50:
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
        return None

def scan_asset_setup(symbol, tf="4H", equity_inr=5000.0, risk_pct=0.01, leverage=10.0, products_dict=None, live_mark_price=None):
    """Scans an asset for Tiered Fibonacci Rejections enforcing Single Entry and Lot Sizing."""
    res_code = "240" if tf == "4H" else "60"
    # QR-2026-1115 fix: 800 bars so EMA233 stabilises (300 bars leave a 7.6% seed
    # weight = up to 3.3% EMA error on volatile names vs the backtest depth).
    df = fetch_recent_candles(symbol, resolution=res_code, count=800)
    if df is None or len(df) < 250:
        return None
        
    closes = df['close']
    ema55 = calculate_ema(closes, 55).to_numpy()
    ema89 = calculate_ema(closes, 89).to_numpy()
    ema233 = calculate_ema(closes, 233).to_numpy()
    atr = calculate_atr(df, 14)
    
    i = len(df) - 1 # Forming candle
    prev = i - 1    # Confirmed closed candle
    
    o = df['open'].iloc[prev]
    h = df['high'].iloc[prev]
    l = df['low'].iloc[prev]
    c = df['close'].iloc[prev]
    rng = h - l
    if rng <= 0:
        return None
        
    curr_55 = ema55[prev]
    curr_89 = ema89[prev]
    curr_233 = ema233[prev]
    curr_atr = atr[prev]
    
    # 4H Macro Trend / Regime Definition (EMA 89 vs EMA 233 Macro Gate - matching all 11,613 backtests)
    if curr_89 > curr_233:
        regime = "BULLISH"
    elif curr_89 < curr_233:
        regime = "BEARISH"
    else:
        return None
        
    # Check Active Positions file to enforce Single Entry Invariant
    active_pos = load_active_positions()
    bar_duration = 14400 if tf == "4H" else 3600
    if symbol in active_pos:
        last_entry_ts = active_pos[symbol].get('entry_ts', 0)
        if (df['timestamp'].iloc[prev] - last_entry_ts) < (12 * bar_duration):
            return None
            
    setup = None
    
    # Bullish setup check (Tiered Support: EMA 89 or EMA 233)
    if regime == "BULLISH":
        lower_wick = min(o, c) - l
        wick_ratio = lower_wick / rng
        
        shelf = None
        if (l <= curr_89 * 1.002) and (c >= curr_89):
            shelf = "EMA_89"
        elif (l <= curr_233 * 1.002) and (c >= curr_233):
            shelf = "EMA_233"
            
        if shelf and wick_ratio >= 0.25:
            entry = float(live_mark_price) if live_mark_price is not None else float(df['close'].iloc[i])
            stop = (min(curr_233, l) - 0.35 * curr_atr) if shelf == "EMA_233" else (l - 0.30 * curr_atr)
            if entry <= stop:
                return None
            risk_dist = entry - stop
            if risk_dist > 0:
                sizing = calculate_delta_order_sizing(
                    symbol, entry, stop, equity_inr=equity_inr, risk_pct=risk_pct, leverage=leverage, products_dict=products_dict
                )
                setup = {
                    'symbol': symbol,
                    'sector': classify_asset(symbol),
                    'cluster': get_asset_cluster(symbol),
                    'tf': tf,
                    'regime': regime,
                    'shelf': shelf,
                    'action': 'BUY / LONG',
                    'entry': round(entry, 4),
                    'stop': round(stop, 4),
                    'risk_distance': round(risk_dist, 4),
                    't1': round(entry + (1.5 * risk_dist), 4),
                    't2': round(entry + (2.5 * risk_dist), 4),
                    't3': round(entry + (4.0 * risk_dist), 4),
                    'rr': 2.5,
                    'contracts': sizing['contracts'],
                    'contract_value': sizing['contract_value'],
                    'underlying_units': sizing['underlying_units'],
                    'notional_inr': sizing['notional_inr'],
                    'margin_10x_inr': sizing['margin_10x_inr'],
                    'risk_inr': sizing['actual_risk_inr'],
                    'risk_pct': sizing['actual_risk_pct'],
                    'sizing_status': sizing['status'],
                    'wick_pct': round(wick_ratio * 100.0, 1),
                    'recent_pct_change': round((df['close'].iloc[prev] / df['close'].iloc[prev - 2] - 1.0) * 100.0, 2),
                    'entry_ts': int(df['timestamp'].iloc[prev])
                }
                
    # Bearish setup check (Tiered Resistance: EMA 89 or EMA 233)
    elif regime == "BEARISH":
        upper_wick = h - max(o, c)
        wick_ratio = upper_wick / rng
        
        shelf = None
        if (h >= curr_89 * 0.998) and (c <= curr_89):
            shelf = "EMA_89"
        elif (h >= curr_233 * 0.998) and (c <= curr_233):
            shelf = "EMA_233"
            
        if shelf and wick_ratio >= 0.25:
            entry = float(live_mark_price) if live_mark_price is not None else float(df['close'].iloc[i])
            stop = (max(curr_233, h) + 0.35 * curr_atr) if shelf == "EMA_233" else (h + 0.30 * curr_atr)
            if entry >= stop:
                return None
            risk_dist = stop - entry
            if risk_dist > 0:
                sizing = calculate_delta_order_sizing(
                    symbol, entry, stop, equity_inr=equity_inr, risk_pct=risk_pct, leverage=leverage, products_dict=products_dict
                )
                setup = {
                    'symbol': symbol,
                    'sector': classify_asset(symbol),
                    'cluster': get_asset_cluster(symbol),
                    'tf': tf,
                    'regime': regime,
                    'shelf': shelf,
                    'action': 'SELL / SHORT',
                    'entry': round(entry, 4),
                    'stop': round(stop, 4),
                    'risk_distance': round(risk_dist, 4),
                    't1': round(entry - (1.5 * risk_dist), 4),
                    't2': round(entry - (2.5 * risk_dist), 4),
                    't3': round(entry - (4.0 * risk_dist), 4),
                    'rr': 2.5,
                    'contracts': sizing['contracts'],
                    'contract_value': sizing['contract_value'],
                    'underlying_units': sizing['underlying_units'],
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
