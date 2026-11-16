import os
import sys
import MetaTrader5 as mt5
import pandas as pd
import numpy as np

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from engine.mt5_alpha_trader_monitor import (
    OPTIMAL_ALPHA_BASKET,
    calculate_ema,
    calculate_atr,
    compute_adx,
)

def fetch_mt5_bars(symbol, timeframe, n_bars=3000):
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, n_bars)
    if rates is None or len(rates) == 0:
        return None
    df = pd.DataFrame(rates)
    return df

def backtest_single_tf(df, setup_type="canonical", exit_rr=3.0, friction_r=0.05, cooldown_bars=12):
    if df is None or len(df) < 250:
        return []
    c = df['close'].to_numpy(dtype=float)
    o = df['open'].to_numpy(dtype=float)
    h = df['high'].to_numpy(dtype=float)
    l = df['low'].to_numpy(dtype=float)
    t = df['time'].to_numpy(dtype=int)
    n = len(c)

    ema34 = calculate_ema(c, 34)
    ema55 = calculate_ema(c, 55)
    ema89 = calculate_ema(c, 89)
    ema233 = calculate_ema(c, 233)
    atr = calculate_atr(h, l, c, 14)
    adx = compute_adx(h, l, c, 14)

    rng = np.where(h - l <= 0, 1e-6, h - l)
    body_tops = np.maximum(o, c)
    body_bots = np.minimum(o, c)
    upper_wicks = (h - body_tops) / rng
    lower_wicks = (body_bots - l) / rng

    trades = []
    in_trade = False
    side = 0
    ep = sl = rd = 0.0
    last_exit_bar = -cooldown_bars - 1

    for i in range(235, n - 1):
        if in_trade:
            bh, bl = h[i], l[i]
            exited = False
            exit_r = 0.0
            if side == 1:
                if bl <= sl:
                    exit_r = -1.0; exited = True
                elif bh >= ep + exit_rr * rd:
                    exit_r = exit_rr; exited = True
            elif side == -1:
                if bh >= sl:
                    exit_r = -1.0; exited = True
                elif bl <= ep - exit_rr * rd:
                    exit_r = exit_rr; exited = True
            if exited:
                trades.append({
                    "entry_ts": entry_ts,
                    "exit_ts": t[i],
                    "net_r": exit_r - friction_r,
                    "bars_held": i - entry_bar,
                    "side": side,
                })
                in_trade = False
                last_exit_bar = i
            continue

        if (i - last_exit_bar) < cooldown_bars:
            continue

        c_bar, o_bar, h_bar, l_bar, a_bar = c[i], o[i], h[i], l[i], atr[i]
        lower_wick, upper_wick = lower_wicks[i], upper_wicks[i]
        sig = 0

        # EMA 89 / 55 Mid Shelf Rejection (Color-Agnostic)
        if ema89[i] > ema233[i]:
            if lower_wick >= 0.25 and (l_bar <= ema89[i] * 1.002 or l_bar <= ema55[i] * 1.002) and (c_bar >= ema89[i]):
                sig = 1
        elif ema89[i] < ema233[i]:
            if upper_wick >= 0.25 and (h_bar >= ema89[i] * 0.998 or h_bar >= ema55[i] * 0.998) and (c_bar <= ema89[i]):
                sig = -1

        if sig != 0:
            next_bar = i + 1
            if next_bar >= n: break
            entry_price = o[next_bar]
            stop_price = (l_bar - 0.30 * a_bar) if sig == 1 else (h_bar + 0.30 * a_bar)
            risk_dist = abs(entry_price - stop_price)
            if risk_dist <= 0 or (risk_dist / entry_price) < 0.0005: continue
            in_trade = True
            side = sig
            ep = entry_price
            sl = stop_price
            rd = risk_dist
            entry_ts = t[next_bar]
            entry_bar = next_bar

    return trades

def backtest_mtf_4h_1h(df_4h, df_1h, exit_rr=3.0, friction_r=0.05, cooldown_bars=24):
    """Multi-timeframe: 4H Macro Trend Gate + 1H Precision Fibonacci Rejection."""
    if df_4h is None or df_1h is None or len(df_4h) < 250 or len(df_1h) < 1000:
        return []
    
    # 4H Macro indicators
    c_4h = df_4h['close'].to_numpy(dtype=float)
    t_4h = df_4h['time'].to_numpy(dtype=int)
    ema89_4h = calculate_ema(c_4h, 89)
    ema233_4h = calculate_ema(c_4h, 233)
    
    # 4H candle closes at time[k] + 14400
    df_macro = pd.DataFrame({
        'time': t_4h + 14400,
        'macro_bull': (ema89_4h > ema233_4h) & (c_4h > ema233_4h),
        'macro_bear': (ema89_4h < ema233_4h) & (c_4h < ema233_4h),
    })
    
    # Merge asof into 1H
    df_1h_merged = pd.merge_asof(df_1h.sort_values('time'), df_macro.sort_values('time'), on='time', direction='backward')
    df_1h_merged['macro_bull'] = df_1h_merged['macro_bull'].fillna(False).astype(bool)
    df_1h_merged['macro_bear'] = df_1h_merged['macro_bear'].fillna(False).astype(bool)
    
    c = df_1h_merged['close'].to_numpy(dtype=float)
    o = df_1h_merged['open'].to_numpy(dtype=float)
    h = df_1h_merged['high'].to_numpy(dtype=float)
    l = df_1h_merged['low'].to_numpy(dtype=float)
    t = df_1h_merged['time'].to_numpy(dtype=int)
    mb = df_1h_merged['macro_bull'].to_numpy()
    mr = df_1h_merged['macro_bear'].to_numpy()
    n = len(c)
    
    ema55 = calculate_ema(c, 55)
    ema89 = calculate_ema(c, 89)
    ema233 = calculate_ema(c, 233)
    atr = calculate_atr(h, l, c, 14)
    adx = compute_adx(h, l, c, 14)
    
    rng = np.where(h - l <= 0, 1e-6, h - l)
    body_tops = np.maximum(o, c)
    body_bots = np.minimum(o, c)
    upper_wicks = (h - body_tops) / rng
    lower_wicks = (body_bots - l) / rng
    
    trades = []
    in_trade = False
    side = 0
    ep = sl = rd = 0.0
    last_exit_bar = -cooldown_bars - 1
    
    for i in range(235, n - 1):
        if in_trade:
            bh, bl = h[i], l[i]
            exited = False
            exit_r = 0.0
            if side == 1:
                if bl <= sl: exit_r = -1.0; exited = True
                elif bh >= ep + exit_rr * rd: exit_r = exit_rr; exited = True
            elif side == -1:
                if bh >= sl: exit_r = -1.0; exited = True
                elif bl <= ep - exit_rr * rd: exit_r = exit_rr; exited = True
            if exited:
                trades.append({
                    "entry_ts": entry_ts,
                    "exit_ts": t[i],
                    "net_r": exit_r - friction_r,
                    "bars_held": i - entry_bar,
                    "side": side,
                })
                in_trade = False
                last_exit_bar = i
            continue
            
        if (i - last_exit_bar) < cooldown_bars:
            continue
            
        c_bar, o_bar, h_bar, l_bar, a_bar = c[i], o[i], h[i], l[i], atr[i]
        lower_wick, upper_wick = lower_wicks[i], upper_wicks[i]
        sig = 0
        
        # 1H Rejection strictly filtered by 4H Macro Regime
        if mb[i]:  # 4H is Macro Bullish
            if lower_wick >= 0.25 and (l_bar <= ema89[i] * 1.002 or l_bar <= ema55[i] * 1.002) and (c_bar >= ema89[i]):
                sig = 1
        elif mr[i]:  # 4H is Macro Bearish
            if upper_wick >= 0.25 and (h_bar >= ema89[i] * 0.998 or h_bar >= ema55[i] * 0.998) and (c_bar <= ema89[i]):
                sig = -1
                
        if sig != 0:
            next_bar = i + 1
            if next_bar >= n: break
            entry_price = o[next_bar]
            stop_price = (l_bar - 0.30 * a_bar) if sig == 1 else (h_bar + 0.30 * a_bar)
            risk_dist = abs(entry_price - stop_price)
            if risk_dist <= 0 or (risk_dist / entry_price) < 0.0005: continue
            in_trade = True
            side = sig
            ep = entry_price
            sl = stop_price
            rd = risk_dist
            entry_ts = t[next_bar]
            entry_bar = next_bar
            
    return trades

def run_study():
    if not mt5.initialize():
        print("MT5 initialization failed!")
        return
    print("=" * 80)
    print("MULTI-TIMEFRAME QUANTITATIVE AUDIT (H4 vs H1 vs MTF 4H+1H)")
    print("=" * 80)

    # Let's test on 10 diverse liquid symbols from our basket
    test_symbols = ["XAUUSD", "BTCUSD", "ETHUSD", "DOGUSD", "US30", "USDJPY", "EURJPY", "USDCHF", "MSFT", "AMD"]

    results_h4 = []
    results_h1 = []
    results_mtf = []

    for sym in test_symbols:
        df_4h = fetch_mt5_bars(sym, mt5.TIMEFRAME_H4, 2500)
        df_1h = fetch_mt5_bars(sym, mt5.TIMEFRAME_H1, 5000)

        t_h4 = backtest_single_tf(df_4h, exit_rr=3.0)
        t_h1 = backtest_single_tf(df_1h, exit_rr=3.0, cooldown_bars=24)
        t_mtf = backtest_mtf_4h_1h(df_4h, df_1h, exit_rr=3.0, cooldown_bars=24)

        r_h4 = sum(x['net_r'] for x in t_h4)
        r_h1 = sum(x['net_r'] for x in t_h1)
        r_mtf = sum(x['net_r'] for x in t_mtf)

        wr_h4 = (sum(1 for x in t_h4 if x['net_r'] > 0) / max(1, len(t_h4))) * 100
        wr_h1 = (sum(1 for x in t_h1 if x['net_r'] > 0) / max(1, len(t_h1))) * 100
        wr_mtf = (sum(1 for x in t_mtf if x['net_r'] > 0) / max(1, len(t_mtf))) * 100

        print(f"[{sym:<7}]")
        print(f"  H4 Pure:       {len(t_h4):<4} trades | Win: {wr_h4:4.1f}% | Net R: {r_h4:+6.2f}R")
        print(f"  H1 Pure:       {len(t_h1):<4} trades | Win: {wr_h1:4.1f}% | Net R: {r_h1:+6.2f}R")
        print(f"  MTF (4H + 1H): {len(t_mtf):<4} trades | Win: {wr_mtf:4.1f}% | Net R: {r_mtf:+6.2f}R")

        results_h4.extend(t_h4)
        results_h1.extend(t_h1)
        results_mtf.extend(t_mtf)

    print("\n" + "=" * 80)
    print("PORTFOLIO AGGREGATE COMPARISON ACROSS TIMEFRAMES")
    print("=" * 80)
    for name, t_list in [("H4 Pure (Macro)", results_h4), ("H1 Pure (Intraday)", results_h1), ("MTF 4H Macro + 1H Precision", results_mtf)]:
        tot_r = sum(x['net_r'] for x in t_list)
        wins = [x for x in t_list if x['net_r'] > 0]
        losses = [x for x in t_list if x['net_r'] <= 0]
        wr = (len(wins) / max(1, len(t_list))) * 100
        pf = sum(x['net_r'] for x in wins) / max(1e-6, abs(sum(x['net_r'] for x in losses)))
        print(f"{name:<30} | Trades: {len(t_list):<5} | Win Rate: {wr:4.1f}% | Total Net R: {tot_r:+7.2f}R | PF: {pf:4.2f}")

if __name__ == "__main__":
    run_study()
