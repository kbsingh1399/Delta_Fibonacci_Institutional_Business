import os
import glob
import pandas as pd
import numpy as np

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
AUDIT_DIR = os.path.join(BASE_DIR, "audit")
os.makedirs(AUDIT_DIR, exist_ok=True)

def calculate_ema_dict(closes, spans):
    res = {}
    s = pd.Series(closes)
    for sp in spans:
        res[sp] = s.ewm(span=sp, adjust=False).mean().to_numpy()
    return res

def calculate_atr(highs, lows, closes, period=14):
    n = len(closes)
    tr = np.zeros(n)
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
    atr = pd.Series(tr).rolling(period).mean().bfill().to_numpy()
    return atr

def simulate_asset_calmar(
    df,
    fast_p=34,
    mid_p=89,
    slow_p=233,
    macro_p=None,
    exit_mode="fixed_2.5",
    spread_cost_r=0.04,
    allow_pyramiding=False,
    min_days=45.0
):
    """Simulates trading strategy with Tiered EMA Support (EMA 89 + EMA 233 Rejections).
    
    Supports:
    - Tiered Rejections: Catches both standard EMA 89 bounces and deep discount EMA 233 sweeps.
    - Multiple Rejection Policy: Single position per asset default, with optional Pyramiding (max 2 tranches).
    - Statistical Rigor: Requires minimum history days to avoid annualization distortion.
    """
    opens = df['open'].to_numpy(dtype=float)
    highs = df['high'].to_numpy(dtype=float)
    lows = df['low'].to_numpy(dtype=float)
    closes = df['close'].to_numpy(dtype=float)
    timestamps = df['timestamp'].to_numpy(dtype=int)
    n = len(closes)
    
    if n < 250:
        return None
        
    rng = highs - lows
    rng = np.where(rng <= 0, 1e-6, rng)
    body_tops = np.maximum(opens, closes)
    body_bots = np.minimum(opens, closes)
    u_wicks = (highs - body_tops) / rng
    l_wicks = (body_bots - lows) / rng
    
    atrs = calculate_atr(highs, lows, closes, 14)
    all_spans = [21, 34, 50, 55, 89, 100, 144, 200, 233, 377]
    ema_dict = calculate_ema_dict(closes, all_spans)
    
    used_spans = [fast_p, mid_p, slow_p]
    if macro_p:
        used_spans.append(macro_p)
    start_idx = max(used_spans) + 10
    
    if start_idx >= n - 10:
        return None
        
    ef = ema_dict[fast_p]
    em = ema_dict[mid_p]
    es = ema_dict[slow_p]
    
    is_fixed = exit_mode.startswith("fixed_")
    fixed_rr = float(exit_mode.split("_")[1]) if is_fixed else 0.0
    
    # Position tracking (allows 1 position by default, or up to 2 if pyramiding)
    max_active_positions = 2 if allow_pyramiding else 1
    active_positions = []
    
    trades_r = []
    trade_exit_ts = []
    trade_entry_ts = []
    
    for i in range(start_idx, n - 1):
        bh = highs[i]
        bl = lows[i]
        bc = closes[i]
        
        # 1. Manage Active Positions
        still_active = []
        for pos in active_positions:
            entry_p = pos['entry_p']
            sl_p = pos['sl_p']
            r_dist = pos['r_dist']
            side = pos['side']
            max_fav_r = pos['max_fav_r']
            
            cur_r = (entry_p - bl) / r_dist if side == -1 else (bh - entry_p) / r_dist
            if cur_r > max_fav_r:
                pos['max_fav_r'] = cur_r
                max_fav_r = cur_r
                
            exited = False
            exit_r = 0.0
            
            if side == -1: # SHORT
                if is_fixed:
                    if bh >= sl_p:
                        exit_r = -1.0; exited = True
                    elif bl <= (entry_p - fixed_rr * r_dist):
                        exit_r = fixed_rr; exited = True
                elif exit_mode == "step_trail":
                    if max_fav_r >= 6.0:
                        sl_p = min(sl_p, ema_dict[34][i])
                    elif max_fav_r >= 4.0:
                        sl_p = min(sl_p, entry_p - 2.0 * r_dist)
                    elif max_fav_r >= 2.0:
                        sl_p = min(sl_p, entry_p - 0.5 * r_dist)
                    pos['sl_p'] = sl_p
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
            elif side == 1: # LONG
                if is_fixed:
                    if bl <= sl_p:
                        exit_r = -1.0; exited = True
                    elif bh >= (entry_p + fixed_rr * r_dist):
                        exit_r = fixed_rr; exited = True
                elif exit_mode == "step_trail":
                    if max_fav_r >= 6.0:
                        sl_p = max(sl_p, ema_dict[34][i])
                    elif max_fav_r >= 4.0:
                        sl_p = max(sl_p, entry_p + 2.0 * r_dist)
                    elif max_fav_r >= 2.0:
                        sl_p = max(sl_p, entry_p + 0.5 * r_dist)
                    pos['sl_p'] = sl_p
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
                trades_r.append(capped_r)
                trade_entry_ts.append(pos['entry_ts'])
                trade_exit_ts.append(timestamps[i])
            else:
                still_active.append(pos)
                
        active_positions = still_active
        
        # Check Capacity for New Entry
        if len(active_positions) >= max_active_positions:
            continue
            
        # Pyramiding requirement: Tranche 1 must be de-risked (MFE >= 1.5R)
        if len(active_positions) == 1 and allow_pyramiding:
            if active_positions[0]['max_fav_r'] < 1.5:
                continue
                
        # 2. Entry Signal Detection (Tiered EMA Rejections)
        c_c = closes[i]
        c_p = closes[i-1]
        h_i = highs[i]
        l_i = lows[i]
        a_i = atrs[i]
        
        sig = 0
        shelf_type = None
        
        # Macro Gate: Alignment of Mid vs Slow (e.g. 89 vs 233)
        # BEARISH REGIME: EMA 89 < EMA 233 (or fast < mid < slow)
        if (em[i] < es[i]):
            # Rejection must be on candle i
            if u_wicks[i] >= 0.25:
                # Shelf 1: Rejection at Fast/Mid (e.g., 55 or 89)
                if (h_i >= em[i] * 0.998 or h_i >= ef[i] * 0.998) and (c_c <= em[i]):
                    sig = -1
                    shelf_type = "MID"
                # Shelf 2: Deep Discount Rejection at Slow (e.g., 233)
                elif (h_i >= es[i] * 0.998) and (c_c <= es[i]):
                    sig = -1
                    shelf_type = "SLOW"
                    
        # BULLISH REGIME: EMA 89 > EMA 233 (or fast > mid > slow)
        elif (em[i] > es[i]):
            if l_wicks[i] >= 0.25:
                # Shelf 1: Rejection at Fast/Mid (e.g., 55 or 89)
                if (l_i <= em[i] * 1.002 or l_i <= ef[i] * 1.002) and (c_c >= em[i]):
                    sig = 1
                    shelf_type = "MID"
                # Shelf 2: Deep Discount Rejection at Slow (e.g., 233)
                elif (l_i <= es[i] * 1.002) and (c_c >= es[i]):
                    sig = 1
                    shelf_type = "SLOW"
                    
        if sig != 0:
            next_i = i + 1
            if next_i >= n:
                break
            ep = opens[next_i]
            
            # Stop loss calculation based on shelf type
            if shelf_type == "SLOW":
                # Deep discount stop: placed beyond the slow EMA (233) extreme with ATR buffer
                sp = (max(es[i], h_i) + 0.35 * a_i) if sig == -1 else (min(es[i], l_i) - 0.35 * a_i)
            else:
                sp = (h_i + 0.30 * a_i) if sig == -1 else (l_i - 0.30 * a_i)
                
            rd = abs(ep - sp)
            if rd <= 0:
                continue
                
            active_positions.append({
                'entry_p': ep,
                'sl_p': sp,
                'r_dist': rd,
                'side': sig,
                'entry_ts': timestamps[next_i],
                'shelf': shelf_type,
                'max_fav_r': 0.0
            })
            
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
    
    # Calculate duration in days & Annualized Net R
    total_days = max(1.0, (timestamps[-1] - timestamps[start_idx]) / 86400.0)
    
    # Minimum days filter: penalize or shrink if sample is too short
    if total_days < min_days or tot < 10:
        shrinkage = max(0.2, total_days / min_days)
    else:
        shrinkage = 1.0
        
    annualized_net_r = (net_r * (365.25 / total_days)) * shrinkage
    
    # Calmar Ratio in R units
    calmar_ratio = annualized_net_r / max(max_dd, 0.5)
    quality_ratio = net_r / max(max_dd, 0.5)
    
    return {
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
