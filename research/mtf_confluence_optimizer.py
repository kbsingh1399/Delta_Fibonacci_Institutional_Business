"""mtf_confluence_optimizer.py: Multi-Timeframe (MTF) Quantitative Confluence Engine.
Aligns Higher Timeframe (4H) Macro Regime with Lower Timeframe (1H) Precision Fibonacci Rejection.
- Zero Lookahead Bias: 1H candles only use the latest closed 4H candle's macro state.
- Single Entry Invariant: Exactly 1 active position per asset.
- Deep Discount Capture: Rejections allowed at both 1H EMA 89 and 1H EMA 233.
"""

import os
import glob
import pandas as pd
import numpy as np

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
AUDIT_DIR = os.path.join(BASE_DIR, "audit")

def calculate_ema_series(series, span):
    return series.ewm(span=span, adjust=False).mean()

def calculate_atr(highs, lows, closes, period=14):
    n = len(closes)
    tr = np.zeros(n)
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
    atr = pd.Series(tr).rolling(period).mean().bfill().to_numpy()
    return atr

def simulate_mtf_confluence(
    symbol: str,
    fast_p=34,
    mid_p=89,
    slow_p=233,
    exit_mode="fixed_2.5",
    spread_cost_r=0.04,
    min_days=45.0
):
    """Executes MTF confluence: 4H Macro Gate + 1H Tiered Fibonacci Rejection."""
    fpath_4h = os.path.join(DATA_DIR, f"{symbol}_4h.csv")
    fpath_1h = os.path.join(DATA_DIR, f"{symbol}_1h.csv")
    
    if not os.path.exists(fpath_4h) or not os.path.exists(fpath_1h):
        return None
        
    try:
        df_4h = pd.read_csv(fpath_4h).sort_values('timestamp').reset_index(drop=True)
        df_1h = pd.read_csv(fpath_1h).sort_values('timestamp').reset_index(drop=True)
    except Exception:
        return None
        
    if len(df_4h) < 150 or len(df_1h) < 300:
        return None
        
    # 1. Compute 4H Macro Regime
    c_4h = df_4h['close']
    ema89_4h = calculate_ema_series(c_4h, 89).to_numpy()
    ema233_4h = calculate_ema_series(c_4h, 233).to_numpy()
    
    # 4H Regime: +1 = Bullish, -1 = Bearish, 0 = Neutral
    regime_4h = np.zeros(len(df_4h), dtype=int)
    for k in range(len(df_4h)):
        if (ema89_4h[k] > ema233_4h[k]) and (c_4h.iloc[k] > ema233_4h[k]):
            regime_4h[k] = 1
        elif (ema89_4h[k] < ema233_4h[k]) and (c_4h.iloc[k] < ema233_4h[k]):
            regime_4h[k] = -1
            
    df_4h['macro_regime'] = regime_4h
    
    # Align 4H macro regime with 1H candles strictly using past closed 4H candles
    # 4H candle at timestamp T covers [T, T + 4 hours). It closes at T + 4h (14400s).
    df_4h['close_ts'] = df_4h['timestamp'] + 14400
    df_macro = df_4h[['close_ts', 'macro_regime']].rename(columns={'close_ts': 'timestamp'})
    
    df_1h_merged = pd.merge_asof(
        df_1h,
        df_macro,
        on='timestamp',
        direction='backward'
    )
    df_1h_merged['macro_regime'] = df_1h_merged['macro_regime'].fillna(0).astype(int)
    
    # 2. Compute 1H Indicators
    opens = df_1h_merged['open'].to_numpy(dtype=float)
    highs = df_1h_merged['high'].to_numpy(dtype=float)
    lows = df_1h_merged['low'].to_numpy(dtype=float)
    closes = df_1h_merged['close'].to_numpy(dtype=float)
    timestamps = df_1h_merged['timestamp'].to_numpy(dtype=int)
    macro_regimes = df_1h_merged['macro_regime'].to_numpy(dtype=int)
    n = len(closes)
    
    rng = highs - lows
    rng = np.where(rng <= 0, 1e-6, rng)
    body_tops = np.maximum(opens, closes)
    body_bots = np.minimum(opens, closes)
    u_wicks = (highs - body_tops) / rng
    l_wicks = (body_bots - lows) / rng
    
    atrs = calculate_atr(highs, lows, closes, 14)
    c_series = pd.Series(closes)
    ef = calculate_ema_series(c_series, fast_p).to_numpy()
    em = calculate_ema_series(c_series, mid_p).to_numpy()
    es = calculate_ema_series(c_series, slow_p).to_numpy()
    
    start_idx = max(fast_p, mid_p, slow_p) + 20
    if start_idx >= n - 10:
        return None
        
    is_fixed = exit_mode.startswith("fixed_")
    fixed_rr = float(exit_mode.split("_")[1]) if is_fixed else 0.0
    
    # Enforce Single Active Position Invariant
    in_trade = False
    trade_side = 0
    entry_p = 0.0
    sl_p = 0.0
    r_dist = 0.0
    entry_i = 0
    max_fav_r = 0.0
    
    trades_r = []
    trade_entry_ts = []
    trade_exit_ts = []
    
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
                    if bc > ef[i]:
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
                    if bc < ef[i]:
                        exit_r = (bc - entry_p) / r_dist; exited = True
                    elif bl <= sl_p:
                        exit_r = -1.0; exited = True
                        
            if exited:
                capped_r = min(exit_r, 15.0) - spread_cost_r
                trades_r.append(capped_r)
                trade_entry_ts.append(timestamps[entry_i])
                trade_exit_ts.append(timestamps[i])
                in_trade = False
            continue # Single position active: no new entries while in trade
            
        # Entry Logic: Must align with 4H Macro Regime
        macro = macro_regimes[i]
        if macro == 0:
            continue
            
        c_c = closes[i]
        h_i = highs[i]
        l_i = lows[i]
        a_i = atrs[i]
        
        sig = 0
        shelf_type = None
        
        if macro == -1 and (em[i] < es[i]): # 4H Bearish + 1H Bearish Alignment
            if u_wicks[i] >= 0.25:
                # Rejection at 1H EMA 89
                if (h_i >= em[i] * 0.998 or h_i >= ef[i] * 0.998) and (c_c <= em[i]):
                    sig = -1
                    shelf_type = "MID"
                # Rejection at 1H EMA 233 (Deep Discount)
                elif (h_i >= es[i] * 0.998) and (c_c <= es[i]):
                    sig = -1
                    shelf_type = "SLOW"
                    
        elif macro == 1 and (em[i] > es[i]): # 4H Bullish + 1H Bullish Alignment
            if l_wicks[i] >= 0.25:
                # Rejection at 1H EMA 89
                if (l_i <= em[i] * 1.002 or l_i <= ef[i] * 1.002) and (c_c >= em[i]):
                    sig = 1
                    shelf_type = "MID"
                # Rejection at 1H EMA 233 (Deep Discount)
                elif (l_i <= es[i] * 1.002) and (c_c >= es[i]):
                    sig = 1
                    shelf_type = "SLOW"
                    
        if sig != 0:
            next_i = i + 1
            if next_i >= n:
                break
            ep = opens[next_i]
            
            if shelf_type == "SLOW":
                sp = (max(es[i], h_i) + 0.35 * a_i) if sig == -1 else (min(es[i], l_i) - 0.35 * a_i)
            else:
                sp = (h_i + 0.30 * a_i) if sig == -1 else (l_i - 0.30 * a_i)
                
            rd = abs(ep - sp)
            if rd <= 0:
                continue
                
            in_trade = True
            trade_side = sig
            entry_p = ep
            sl_p = sp
            r_dist = rd
            entry_i = next_i
            max_fav_r = 0.0
            
    if not trades_r:
        return None
        
    arr = np.array(trades_r)
    wins = np.sum(arr > 0)
    tot = len(arr)
    wr = (wins / tot) * 100
    gw = np.sum(arr[arr > 0])
    gl = abs(np.sum(arr[arr < 0]))
    pf = (gw / gl) if gl > 0 else 999.0
    net_r = np.sum(arr)
    avg_r = net_r / tot
    
    cum = np.cumsum(arr)
    peak = np.maximum.accumulate(cum)
    max_dd = np.max(peak - cum) if len(cum) > 0 else 0.0
    
    total_days = max(1.0, (timestamps[-1] - timestamps[start_idx]) / 86400.0)
    
    if total_days < min_days or tot < 10:
        shrinkage = max(0.2, total_days / min_days)
    else:
        shrinkage = 1.0
        
    annualized_net_r = (net_r * (365.25 / total_days)) * shrinkage
    calmar_ratio = annualized_net_r / max(max_dd, 0.5)
    quality_ratio = net_r / max(max_dd, 0.5)
    
    return {
        "symbol": symbol,
        "mode": "MTF_4H_1H",
        "trades": tot,
        "win_rate": round(float(wr), 1),
        "pf": round(float(pf), 2),
        "net_r": round(float(net_r), 2),
        "annualized_net_r": round(float(annualized_net_r), 2),
        "max_dd_r": round(float(max_dd), 2),
        "calmar_ratio": round(float(calmar_ratio), 2),
        "quality_ratio": round(float(quality_ratio), 2),
        "avg_r": round(float(avg_r), 3),
        "total_days": round(float(total_days), 1)
    }


def get_mtf_trades_for_portfolio(symbol, fast_p=34, mid_p=89, slow_p=233, exit_mode="step_trail", spread_cost_r=0.04):
    """Returns chronological trade records for MTF Confluence for portfolio simulation."""
    fpath_4h = os.path.join(DATA_DIR, f"{symbol}_4h.csv")
    fpath_1h = os.path.join(DATA_DIR, f"{symbol}_1h.csv")
    if not os.path.exists(fpath_4h) or not os.path.exists(fpath_1h):
        return []
    try:
        df_4h = pd.read_csv(fpath_4h).sort_values('timestamp').reset_index(drop=True)
        df_1h = pd.read_csv(fpath_1h).sort_values('timestamp').reset_index(drop=True)
    except Exception:
        return []
    if len(df_4h) < 150 or len(df_1h) < 300:
        return []
        
    c_4h = df_4h['close']
    ema89_4h = calculate_ema_series(c_4h, 89).to_numpy()
    ema233_4h = calculate_ema_series(c_4h, 233).to_numpy()
    regime_4h = np.zeros(len(df_4h), dtype=int)
    for k in range(len(df_4h)):
        if (ema89_4h[k] > ema233_4h[k]) and (c_4h.iloc[k] > ema233_4h[k]):
            regime_4h[k] = 1
        elif (ema89_4h[k] < ema233_4h[k]) and (c_4h.iloc[k] < ema233_4h[k]):
            regime_4h[k] = -1
    df_4h['macro_regime'] = regime_4h
    df_4h['close_ts'] = df_4h['timestamp'] + 14400
    df_macro = df_4h[['close_ts', 'macro_regime']].rename(columns={'close_ts': 'timestamp'})
    df_1h_merged = pd.merge_asof(df_1h, df_macro, on='timestamp', direction='backward')
    df_1h_merged['macro_regime'] = df_1h_merged['macro_regime'].fillna(0).astype(int)
    
    opens = df_1h_merged['open'].to_numpy(dtype=float)
    highs = df_1h_merged['high'].to_numpy(dtype=float)
    lows = df_1h_merged['low'].to_numpy(dtype=float)
    closes = df_1h_merged['close'].to_numpy(dtype=float)
    timestamps = df_1h_merged['timestamp'].to_numpy(dtype=int)
    macro_regimes = df_1h_merged['macro_regime'].to_numpy(dtype=int)
    n = len(closes)
    
    rng = highs - lows
    rng = np.where(rng <= 0, 1e-6, rng)
    body_tops = np.maximum(opens, closes)
    body_bots = np.minimum(opens, closes)
    u_wicks = (highs - body_tops) / rng
    l_wicks = (body_bots - lows) / rng
    atrs = calculate_atr(highs, lows, closes, 14)
    c_series = pd.Series(closes)
    ef = calculate_ema_series(c_series, fast_p).to_numpy()
    em = calculate_ema_series(c_series, mid_p).to_numpy()
    es = calculate_ema_series(c_series, slow_p).to_numpy()
    start_idx = max(fast_p, mid_p, slow_p) + 20
    if start_idx >= n - 10:
        return []
        
    is_fixed = exit_mode.startswith("fixed_")
    fixed_rr = float(exit_mode.split("_")[1]) if is_fixed else 0.0
    trades = []
    in_trade = False
    trade_side = 0
    entry_p = 0.0; sl_p = 0.0; r_dist = 0.0; entry_i = 0; max_fav_r = 0.0
    
    for i in range(start_idx, n - 1):
        if in_trade:
            bh = highs[i]; bl = lows[i]; bc = closes[i]
            exited = False; exit_r = 0.0
            cur_r = (entry_p - bl) / r_dist if trade_side == -1 else (bh - entry_p) / r_dist
            if cur_r > max_fav_r: max_fav_r = cur_r
            if trade_side == -1:
                if is_fixed:
                    if bh >= sl_p: exit_r = -1.0; exited = True
                    elif bl <= (entry_p - fixed_rr * r_dist): exit_r = fixed_rr; exited = True
                elif exit_mode == "step_trail":
                    if max_fav_r >= 6.0: sl_p = min(sl_p, ef[i])
                    elif max_fav_r >= 4.0: sl_p = min(sl_p, entry_p - 2.0 * r_dist)
                    elif max_fav_r >= 2.0: sl_p = min(sl_p, entry_p - 0.5 * r_dist)
                    if bh >= sl_p: exit_r = (entry_p - sl_p) / r_dist; exited = True
                elif exit_mode == "trail_89":
                    if bc > em[i]: exit_r = (entry_p - bc) / r_dist; exited = True
                    elif bh >= sl_p: exit_r = -1.0; exited = True
            elif trade_side == 1:
                if is_fixed:
                    if bl <= sl_p: exit_r = -1.0; exited = True
                    elif bh >= (entry_p + fixed_rr * r_dist): exit_r = fixed_rr; exited = True
                elif exit_mode == "step_trail":
                    if max_fav_r >= 6.0: sl_p = max(sl_p, ef[i])
                    elif max_fav_r >= 4.0: sl_p = max(sl_p, entry_p + 2.0 * r_dist)
                    elif max_fav_r >= 2.0: sl_p = max(sl_p, entry_p + 0.5 * r_dist)
                    if bl <= sl_p: exit_r = (sl_p - entry_p) / r_dist; exited = True
                elif exit_mode == "trail_89":
                    if bc < em[i]: exit_r = (bc - entry_p) / r_dist; exited = True
                    elif bl <= sl_p: exit_r = -1.0; exited = True
            if exited:
                capped_r = min(exit_r, 15.0) - spread_cost_r
                trades.append({
                    'symbol': symbol,
                    'timeframe': '4H_1H',
                    'sector': 'TRADFI_EQUITIES',
                    'entry_ts': timestamps[entry_i],
                    'exit_ts': timestamps[i],
                    'r': capped_r,
                    'side': trade_side
                })
                in_trade = False
            continue
            
        macro = macro_regimes[i]
        if macro == 0: continue
        c_c = closes[i]; h_i = highs[i]; l_i = lows[i]; a_i = atrs[i]
        sig = 0; shelf_type = None
        if macro == -1 and (em[i] < es[i]):
            if u_wicks[i] >= 0.25:
                if (h_i >= em[i] * 0.998 or h_i >= ef[i] * 0.998) and (c_c <= em[i]):
                    sig = -1; shelf_type = "MID"
                elif (h_i >= es[i] * 0.998) and (c_c <= es[i]):
                    sig = -1; shelf_type = "SLOW"
        elif macro == 1 and (em[i] > es[i]):
            if l_wicks[i] >= 0.25:
                if (l_i <= em[i] * 1.002 or l_i <= ef[i] * 1.002) and (c_c >= em[i]):
                    sig = 1; shelf_type = "MID"
                elif (l_i <= es[i] * 1.002) and (c_c >= es[i]):
                    sig = 1; shelf_type = "SLOW"
        if sig != 0:
            next_i = i + 1
            if next_i >= n: break
            ep = opens[next_i]
            sp = (max(es[i], h_i) + 0.35 * a_i) if (shelf_type == "SLOW" and sig == -1) else ((min(es[i], l_i) - 0.35 * a_i) if (shelf_type == "SLOW" and sig == 1) else ((h_i + 0.30 * a_i) if sig == -1 else (l_i - 0.30 * a_i)))
            rd = abs(ep - sp)
            if rd <= 0: continue
            in_trade = True; trade_side = sig; entry_p = ep; sl_p = sp; r_dist = rd; entry_i = next_i; max_fav_r = 0.0
            
    return trades
