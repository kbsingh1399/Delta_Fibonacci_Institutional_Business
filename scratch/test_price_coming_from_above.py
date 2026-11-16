import os
import sys
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import pandas as pd
import numpy as np
from scratch.test_360_degree_breakthrough import ALPHA_BASKET, DATA_DIR, calculate_ema, calculate_atr, compute_adx, simulate_portfolio

drags = ['LNKUSD', 'XAGUSD', 'US2000', 'AUDJPY', 'NVDA', 'MBG', 'GOOG']
clean_basket = [x for x in ALPHA_BASKET if x['symbol'] not in drags]

data_map = {}
for item in clean_basket:
    p = os.path.join(DATA_DIR, f"{item['symbol']}.parquet")
    if os.path.exists(p):
        data_map[item['symbol']] = pd.read_parquet(p)

def extract_trades_with_origin_check(df, symbol, cluster, setup_type, exit_mode, require_origin_above=True):
    if len(df) < 240: return []
    c = df["close"].to_numpy(dtype=float)
    o = df["open"].to_numpy(dtype=float)
    h = df["high"].to_numpy(dtype=float)
    l = df["low"].to_numpy(dtype=float)
    t = df["timestamp"].to_numpy(dtype=int)
    n = len(c)

    ema34 = calculate_ema(c, 34)
    ema55 = calculate_ema(c, 55)
    ema89 = calculate_ema(c, 89)
    ema233 = calculate_ema(c, 233)
    atr = calculate_atr(h, l, c, 14)
    adx = compute_adx(h, l, c, 14)

    roll_h20 = pd.Series(h).shift(1).rolling(20).max().to_numpy()
    roll_l20 = pd.Series(l).shift(1).rolling(20).min().to_numpy()

    rng = np.where(h - l <= 0, 1e-6, h - l)
    body_tops = np.maximum(o, c)
    body_bots = np.minimum(o, c)
    upper_wicks = (h - body_tops) / rng
    lower_wicks = (body_bots - l) / rng

    is_fixed = exit_mode.startswith("fixed_")
    fixed_rr = float(exit_mode.split("_")[1]) if is_fixed else 3.0

    trades = []
    in_trade = False
    side = 0
    ep = sl = rd = 0.0
    entry_ts = entry_bar = 0
    last_exit_bar = -13

    for i in range(235, n - 1):
        if in_trade:
            bh, bl = h[i], l[i]
            exited = False
            exit_r = 0.0
            if side == 1:
                if bl <= sl: exit_r = -1.0; exited = True
                elif bh >= ep + fixed_rr * rd: exit_r = fixed_rr; exited = True
            elif side == -1:
                if bh >= sl: exit_r = -1.0; exited = True
                elif bl <= ep - fixed_rr * rd: exit_r = fixed_rr; exited = True
            if exited:
                trades.append({
                    "symbol": symbol, "cluster": cluster, "entry_ts": entry_ts,
                    "exit_ts": t[i], "net_r": exit_r - 0.05, "side": side
                })
                in_trade = False
                last_exit_bar = i
            continue

        if (i - last_exit_bar) < 12: continue

        c_bar, o_bar, h_bar, l_bar, a_bar = c[i], o[i], h[i], l[i], atr[i]
        lower_wick, upper_wick = lower_wicks[i], upper_wicks[i]
        sig = 0

        # Check origin: did price come from ABOVE the EMA shelf for Longs?
        # That means either the bar opened above EMA, OR previous bar closed above EMA
        if require_origin_above:
            came_from_above = (o_bar >= ema89[i] * 0.998) or (c[i-1] >= ema89[i-1] * 0.998)
            came_from_below = (o_bar <= ema89[i] * 1.002) or (c[i-1] <= ema89[i-1] * 1.002)
        else:
            came_from_above = True
            came_from_below = True

        if setup_type == "canonical":
            if ema89[i] > ema233[i] and came_from_above:
                if lower_wick >= 0.25 and (l_bar <= ema89[i] * 1.002 or l_bar <= ema55[i] * 1.002) and (c_bar >= ema89[i]):
                    sig = 1
            elif ema89[i] < ema233[i] and came_from_below:
                if upper_wick >= 0.25 and (h_bar >= ema89[i] * 0.998 or h_bar >= ema55[i] * 0.998) and (c_bar <= ema89[i]):
                    sig = -1

        elif setup_type == "turbo_tri_tier":
            if ema89[i] > ema233[i] and c_bar >= ema233[i] and came_from_above:
                if lower_wick >= 0.25:
                    if adx[i] >= 22 and (l_bar <= ema34[i] * 1.002 or l_bar <= ema55[i] * 1.002) and (c_bar >= ema55[i]):
                        sig = 1
                    elif (l_bar <= ema89[i] * 1.002) and (c_bar >= ema89[i]):
                        sig = 1
            elif ema89[i] < ema233[i] and c_bar <= ema233[i] and came_from_below:
                if upper_wick >= 0.25:
                    if adx[i] >= 22 and (h_bar >= ema34[i] * 0.998 or h_bar >= ema55[i] * 0.998) and (c_bar <= ema55[i]):
                        sig = -1
                    elif (h_bar >= ema89[i] * 0.998) and (c_bar <= ema89[i]):
                        sig = -1

        elif setup_type == "wyckoff_swing_sweep":
            if ema89[i] > ema233[i] and c_bar >= ema233[i]:
                if l_bar < roll_l20[i] and c_bar > roll_l20[i] and lower_wick >= 0.25:
                    sig = 1
            elif ema89[i] < ema233[i] and c_bar <= ema233[i]:
                if h_bar > roll_h20[i] and c_bar < roll_h20[i] and upper_wick >= 0.25:
                    sig = -1

        elif setup_type == "wyckoff_institutional_filtered":
            sep = abs(ema89[i] - ema233[i]) / a_bar
            if 0.25 <= sep <= 2.50 and adx[i] >= 20.0:
                if ema89[i] > ema233[i] and lower_wick >= 0.25 and came_from_above:
                    if (l_bar <= ema89[i] * 1.002 or l_bar <= ema55[i] * 1.002) and (c_bar >= ema89[i]):
                        sig = 1
                elif ema89[i] < ema233[i] and upper_wick >= 0.25 and came_from_below:
                    if (h_bar >= ema89[i] * 0.998 or h_bar >= ema55[i] * 0.998) and (c_bar <= ema89[i]):
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

print("=" * 80)
print("TESTING PULLBACK ORIGIN INVARIANT (Price Coming From Above EMA For Longs)")
print("=" * 80)

for label, req_check in [("Without Origin Filter (Current)", False), ("With Origin Filter (Coming from Above/Below)", True)]:
    trades_all = []
    for item in clean_basket:
        s = item["symbol"]
        if s not in data_map: continue
        t = extract_trades_with_origin_check(data_map[s], s, item["cluster"], item["setup"], item["exit_mode"], require_origin_above=req_check)
        trades_all.extend(t)
    res = simulate_portfolio(trades_all, max_concurrent=3, max_per_cluster=1)
    print(f"{label:<45} | Trades: {res['trades']:<4} | Win%: {res['win_rate']:<5.1f}% | Net R: {res['net_r']:<+7.2f}R | PF: {res['pf']:<5.2f} | Max DD: -{res['max_dd_r']:<5.2f}R | Calmar: {res['calmar']:<5.2f}")
