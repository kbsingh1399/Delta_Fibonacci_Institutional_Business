#!/usr/bin/env python3
r"""MT5_Backtest.py: Institutional 4H Fibonacci EMA 89 & 233 Wick Rejection Backtester
for all FundedNext MetaTrader 5 Assets (from January 2024 onwards).

Strategy Logic:
- Timeframe: H4
- EMAs: Fast (55), Mid (89), Slow (233)
- Volatility: 14-period Average True Range (ATR)
- Regime Definition:
    - Bullish: EMA 89 > EMA 233
    - Bearish: EMA 89 < EMA 233
- Rejection Wick:
    - Bullish: Lower wick >= 25% of candle range (min(O, C) - L) / (H - L) >= 0.25
        - Shelf 1 (Mid): Low <= max(EMA 55, EMA 89) * 1.002 and Close >= EMA 89
          Stop Loss: Low - 0.30 * ATR14
        - Shelf 2 (Slow): Low <= EMA 233 * 1.002 and Close >= EMA 233
          Stop Loss: min(EMA 233, Low) - 0.35 * ATR14
    - Bearish: Upper wick >= 25% of candle range (H - max(O, C)) / (H - L) >= 0.25
        - Shelf 1 (Mid): High >= min(EMA 55, EMA 89) * 0.998 and Close <= EMA 89
          Stop Loss: High + 0.30 * ATR14
        - Shelf 2 (Slow): High >= EMA 233 * 0.998 and Close <= EMA 233
          Stop Loss: max(EMA 233, High) + 0.35 * ATR14
- Entry: Open of the subsequent 4H candle (strictly causal, zero lookahead)
- Single Entry Invariant: 12-bar post-exit cooldown per asset
- Period: January 1, 2024 onwards (data pre-warmed from 2023-01-01)
"""

import os
import sys
import glob
import argparse
from datetime import datetime, timezone
import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "FundedNext_Data")
AUDIT_DIR = os.path.join(BASE_DIR, "audit")
REPORTS_DIR = os.path.join(BASE_DIR, "reports")
os.makedirs(AUDIT_DIR, exist_ok=True)
os.makedirs(REPORTS_DIR, exist_ok=True)

JAN_2024_TS = 1704067200  # 2024-01-01 00:00:00 UTC


def classify_mt5_symbol(symbol: str) -> str:
    """Categorizes FundedNext symbols into institutional asset classes."""
    s = symbol.upper()
    if s in ["XAUUSD", "XAGUSD", "XPTUSD", "USOUSD", "UKOUSD"]:
        return "Commodity/Metal"
    if s in ["BTCUSD", "ETHUSD", "LTCUSD", "XRPUSD", "ADAUSD", "DOGUSD", "LNKUSD", "XLMUSD", "XMRUSD"]:
        return "Crypto"
    if s in [
        "US30", "SPX500", "NDX100", "US2000", "GER30", "UK100", "FRA40",
        "JP225", "HK50", "AUS200", "EUSTX50", "SWI20", "NTH25", "SPCX", "VIX"
    ]:
        return "Equity Index"
    if s in ["AAPL", "AMZN", "AMD", "BABA", "GOOG", "INTC", "KO", "META", "MSFT", "NFLX", "NKE", "NVDA", "TSLA"]:
        return "US Equity"
    if s in ["ADS", "BAYN", "BMW", "MBG", "MC", "VOW3"]:
        return "EU Equity"
    if s in ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD", "AUDUSD", "NZDUSD"]:
        return "Forex Major"
    if any(ex in s for ex in ["HUF", "NOK", "SEK", "SGD", "HKD", "CNH", "DKK", "MXN", "ZAR"]):
        return "Forex Exotic"
    return "Forex Minor"


def calculate_ema(arr: np.ndarray, span: int) -> np.ndarray:
    return pd.Series(arr).ewm(span=span, adjust=False).mean().to_numpy()


def calculate_atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> np.ndarray:
    n = len(closes)
    tr = np.zeros(n)
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
    return pd.Series(tr).rolling(period).mean().bfill().to_numpy()


def backtest_single_asset(
    filepath: str,
    exit_mode: str = "fixed_3.0",
    friction_r: float = 0.05,
    cooldown_bars: int = 12,
    min_wick_ratio: float = 0.25,
) -> dict:
    """Executes the H4 Fibonacci 89 & 233 Wick Rejection backtest on a single asset."""
    sym = os.path.basename(filepath).replace(".parquet", "")
    category = classify_mt5_symbol(sym)
    try:
        df = pd.read_parquet(filepath)
    except Exception as e:
        return {
            "symbol": sym,
            "category": category,
            "error": str(e),
            "trades": 0,
            "win_rate": 0.0,
            "wins": 0,
            "losses": 0,
            "raw_r": 0.0,
            "net_r": 0.0,
            "profit_factor": 0.0,
            "max_dd_r": 0.0,
            "expectancy": 0.0,
            "long_trades": 0,
            "short_trades": 0,
            "trades_list": [],
        }

    if len(df) < 240:
        return {
            "symbol": sym,
            "category": category,
            "error": f"Insufficient history ({len(df)} bars < 240)",
            "trades": 0,
            "win_rate": 0.0,
            "wins": 0,
            "losses": 0,
            "raw_r": 0.0,
            "net_r": 0.0,
            "profit_factor": 0.0,
            "max_dd_r": 0.0,
            "expectancy": 0.0,
            "long_trades": 0,
            "short_trades": 0,
            "trades_list": [],
        }

    closes = df["close"].to_numpy(dtype=float)
    opens = df["open"].to_numpy(dtype=float)
    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    timestamps = df["timestamp"].to_numpy(dtype=int)
    n = len(closes)

    ema55 = calculate_ema(closes, 55)
    ema89 = calculate_ema(closes, 89)
    ema233 = calculate_ema(closes, 233)
    atrs = calculate_atr(highs, lows, closes, 14)

    rng = np.where(highs - lows <= 0, 1e-6, highs - lows)
    body_tops = np.maximum(opens, closes)
    body_bots = np.minimum(opens, closes)
    upper_wicks = (highs - body_tops) / rng
    lower_wicks = (body_bots - lows) / rng

    is_fixed = exit_mode.startswith("fixed_")
    fixed_rr = float(exit_mode.split("_")[1]) if is_fixed else 3.0

    trades = []
    in_trade = False
    side = 0
    ep = 0.0
    sl = 0.0
    rd = 0.0
    entry_ts = 0
    entry_bar = 0
    shelf_name = ""
    max_fav_r = 0.0
    last_exit_bar = -cooldown_bars - 1

    start_idx = 235

    for i in range(start_idx, n - 1):
        if in_trade:
            bh = highs[i]
            bl = lows[i]
            cur_r = (ep - bl) / rd if side == -1 else (bh - ep) / rd
            if cur_r > max_fav_r:
                max_fav_r = cur_r

            exited = False
            exit_r = 0.0
            exit_reason = ""

            if side == 1:  # LONG
                if is_fixed:
                    # Conservative: if both hit in the same bar, assume SL triggered first
                    if bl <= sl:
                        exit_r = -1.0
                        exit_reason = "SL"
                        exited = True
                    elif bh >= ep + fixed_rr * rd:
                        exit_r = fixed_rr
                        exit_reason = "TP"
                        exited = True
                elif exit_mode == "step_trail":
                    trail_sl = sl
                    if max_fav_r >= 6.0:
                        trail_sl = max(trail_sl, ema55[i])
                    elif max_fav_r >= 4.0:
                        trail_sl = max(trail_sl, ep + 2.0 * rd)
                    elif max_fav_r >= 2.0:
                        trail_sl = max(trail_sl, ep + 0.5 * rd)
                    if bl <= trail_sl:
                        exit_r = (trail_sl - ep) / rd
                        exit_reason = "TRAIL_SL" if trail_sl > sl else "SL"
                        exited = True
            elif side == -1:  # SHORT
                if is_fixed:
                    # Conservative: if both hit in the same bar, assume SL triggered first
                    if bh >= sl:
                        exit_r = -1.0
                        exit_reason = "SL"
                        exited = True
                    elif bl <= ep - fixed_rr * rd:
                        exit_r = fixed_rr
                        exit_reason = "TP"
                        exited = True
                elif exit_mode == "step_trail":
                    trail_sl = sl
                    if max_fav_r >= 6.0:
                        trail_sl = min(trail_sl, ema55[i])
                    elif max_fav_r >= 4.0:
                        trail_sl = min(trail_sl, ep - 2.0 * rd)
                    elif max_fav_r >= 2.0:
                        trail_sl = min(trail_sl, ep - 0.5 * rd)
                    if bh >= trail_sl:
                        exit_r = (ep - trail_sl) / rd
                        exit_reason = "TRAIL_SL" if trail_sl < sl else "SL"
                        exited = True

            if exited:
                net_r = exit_r - friction_r
                trades.append({
                    "symbol": sym,
                    "side": "LONG" if side == 1 else "SHORT",
                    "shelf": shelf_name,
                    "entry_time": datetime.fromtimestamp(entry_ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M"),
                    "exit_time": datetime.fromtimestamp(timestamps[i], tz=timezone.utc).strftime("%Y-%m-%d %H:%M"),
                    "entry_price": ep,
                    "stop_price": sl,
                    "risk_dist": rd,
                    "raw_r": exit_r,
                    "net_r": net_r,
                    "exit_reason": exit_reason,
                    "bars_held": i - entry_bar,
                })
                in_trade = False
                last_exit_bar = i
            continue

        # Single Entry Invariant & Cooldown Gate
        if (i - last_exit_bar) < cooldown_bars:
            continue

        c_bar = closes[i]
        h_bar = highs[i]
        l_bar = lows[i]
        a_bar = atrs[i]

        sig = 0
        shelf_type = ""

        # Institutional Pullback Origin Invariant:
        # Bullish support bounce must originate from ABOVE the shelf
        # Bearish ceiling rejection must originate from BELOW the shelf
        o_bar = opens[i]
        c_prev = closes[i - 1]
        came_from_above = (o_bar >= ema89[i] * 0.998) or (c_prev >= ema89[i - 1] * 0.998)
        came_from_below = (o_bar <= ema89[i] * 1.002) or (c_prev <= ema89[i - 1] * 1.002)

        # Bullish Regime: EMA 89 > EMA 233
        if ema89[i] > ema233[i] and came_from_above:
            if lower_wicks[i] >= min_wick_ratio:
                # Shelf 1 (Mid): Sweeps EMA 89/55 and closes above EMA 89
                if (l_bar <= ema89[i] * 1.002 or l_bar <= ema55[i] * 1.002) and (c_bar >= ema89[i]):
                    sig = 1
                    shelf_type = "EMA_89"
                # Shelf 2 (Slow): Sweeps EMA 233 and closes above EMA 233
                elif (l_bar <= ema233[i] * 1.002) and (c_bar >= ema233[i]):
                    sig = 1
                    shelf_type = "EMA_233"

        # Bearish Regime: EMA 89 < EMA 233
        elif ema89[i] < ema233[i] and came_from_below:
            if upper_wicks[i] >= min_wick_ratio:
                # Shelf 1 (Mid): Sweeps EMA 89/55 and closes below EMA 89
                if (h_bar >= ema89[i] * 0.998 or h_bar >= ema55[i] * 0.998) and (c_bar <= ema89[i]):
                    sig = -1
                    shelf_type = "EMA_89"
                # Shelf 2 (Slow): Sweeps EMA 233 and closes below EMA 233
                elif (h_bar >= ema233[i] * 0.998) and (c_bar <= ema233[i]):
                    sig = -1
                    shelf_type = "EMA_233"

        if sig != 0:
            next_bar = i + 1
            if next_bar >= n:
                break

            # Date Filter: Trades strictly from Jan 1, 2024 onwards
            if timestamps[next_bar] < JAN_2024_TS:
                continue

            entry_price = opens[next_bar]
            if shelf_type == "EMA_233":
                stop_price = (min(ema233[i], l_bar) - 0.35 * a_bar) if sig == 1 else (max(ema233[i], h_bar) + 0.35 * a_bar)
            else:
                stop_price = (l_bar - 0.30 * a_bar) if sig == 1 else (h_bar + 0.30 * a_bar)

            risk_dist = abs(entry_price - stop_price)
            if risk_dist <= 0 or (risk_dist / entry_price) < 0.0005:
                continue

            in_trade = True
            side = sig
            ep = entry_price
            sl = stop_price
            rd = risk_dist
            entry_ts = timestamps[next_bar]
            entry_bar = next_bar
            shelf_name = shelf_type
            max_fav_r = 0.0

    # Compute Performance Metrics
    total_trades = len(trades)
    if total_trades == 0:
        return {
            "symbol": sym,
            "category": category,
            "trades": 0,
            "win_rate": 0.0,
            "wins": 0,
            "losses": 0,
            "raw_r": 0.0,
            "net_r": 0.0,
            "profit_factor": 0.0,
            "max_dd_r": 0.0,
            "expectancy": 0.0,
            "long_trades": 0,
            "short_trades": 0,
            "trades_list": [],
        }

    net_rs = [t["net_r"] for t in trades]
    raw_rs = [t["raw_r"] for t in trades]
    wins = [r for r in net_rs if r > 0]
    losses = [r for r in net_rs if r <= 0]
    win_count = len(wins)
    loss_count = len(losses)
    win_rate = (win_count / total_trades) * 100.0

    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (99.0 if gross_profit > 0 else 0.0)

    total_net_r = sum(net_rs)
    total_raw_r = sum(raw_rs)
    expectancy = total_net_r / total_trades

    # Drawdown in R
    cum_r = np.cumsum(net_rs)
    peak_r = np.maximum.accumulate(cum_r)
    dd_r = peak_r - cum_r
    max_dd_r = float(np.max(dd_r)) if len(dd_r) > 0 else 0.0

    long_trades = sum(1 for t in trades if t["side"] == "LONG")
    short_trades = sum(1 for t in trades if t["side"] == "SHORT")

    return {
        "symbol": sym,
        "category": category,
        "trades": total_trades,
        "win_rate": round(win_rate, 1),
        "wins": win_count,
        "losses": loss_count,
        "raw_r": round(total_raw_r, 2),
        "net_r": round(total_net_r, 2),
        "profit_factor": round(profit_factor, 2),
        "max_dd_r": round(max_dd_r, 2),
        "expectancy": round(expectancy, 3),
        "long_trades": long_trades,
        "short_trades": short_trades,
        "trades_list": trades,
    }


def main():
    parser = argparse.ArgumentParser(description="FundedNext MT5 H4/H1 Fibonacci Wick Rejection Backtester")
    parser.add_argument("--tf", type=str, default="4H", choices=["4H", "1H", "all"], help="Timeframe to backtest (default: 4H)")
    parser.add_argument("--exit-mode", type=str, default="fixed_3.0", choices=["fixed_2.5", "fixed_3.0", "fixed_3.5", "step_trail"], help="Exit strategy mode")
    parser.add_argument("--friction", type=float, default=0.05, help="Friction per trade in R (default: 0.05R for spread/commissions)")
    parser.add_argument("--sort-by", type=str, default="net_r", choices=["net_r", "profit_factor", "win_rate", "expectancy", "trades"], help="Metric to sort by")
    args = parser.parse_args()

    pattern = f"*_{args.tf}.parquet" if args.tf != "all" else "*.parquet"
    parquet_files = sorted(glob.glob(os.path.join(DATA_DIR, pattern)))
    if not parquet_files:
        print(f"[ERROR] No parquet files matching {pattern} found in {DATA_DIR}.")
        sys.exit(1)

    print("=" * 120)
    print("FUNDEDNEXT MT5 - 4H FIBONACCI EMA 89 & 233 WICK REJECTION BACKTEST")
    print(f"Data Source: {len(parquet_files)} assets in FundedNext_Data | Window: Jan 2024 to Present")
    print(f"Strategy: EMA 89/233 Regime + Wick Rejection >= 25% + Tiered Support (EMA 89/233) + 12-Bar Cooldown")
    print(f"Exit Policy: {args.exit_mode} | Friction Drag: {args.friction:.2f}R / trade | Sort Metric: {args.sort_by}")
    print("=" * 120)

    results = []
    all_executed_trades = []

    for fpath in parquet_files:
        res = backtest_single_asset(
            filepath=fpath,
            exit_mode=args.exit_mode,
            friction_r=args.friction,
            cooldown_bars=12,
            min_wick_ratio=0.25,
        )
        results.append(res)
        all_executed_trades.extend(res.get("trades_list", []))

    # Sort results
    sorted_results = sorted(results, key=lambda x: x.get(args.sort_by, -9999.0), reverse=True)

    # Print Table
    header = (
        f"{'Rank':<5} {'Symbol':<10} {'Asset Class':<16} {'Trades':<8} "
        f"{'Wins':<6} {'Losses':<8} {'Win Rate':<10} {'Net R':<11} "
        f"{'Profit Fac':<12} {'Max DD (R)':<12} {'Expectancy':<10}"
    )
    print("\n" + header)
    print("-" * 120)

    leaderboard_rows = []
    for rank, r in enumerate(sorted_results, 1):
        line = (
            f"{rank:<5} {r['symbol']:<10} {r['category']:<16} {r['trades']:<8} "
            f"{r['wins']:<6} {r['losses']:<8} {r['win_rate']:>5.1f}%    "
            f"{r['net_r']:>8.2f}R    {r['profit_factor']:>8.2f}    "
            f"{r['max_dd_r']:>8.2f}R    {r['expectancy']:>7.3f}R"
        )
        print(line)
        leaderboard_rows.append({
            "rank": rank,
            "symbol": r["symbol"],
            "asset_class": r["category"],
            "trades": r["trades"],
            "wins": r["wins"],
            "losses": r["losses"],
            "win_rate_pct": r["win_rate"],
            "raw_r": r["raw_r"],
            "net_r": r["net_r"],
            "profit_factor": r["profit_factor"],
            "max_dd_r": r["max_dd_r"],
            "expectancy_r": r["expectancy"],
            "long_trades": r["long_trades"],
            "short_trades": r["short_trades"],
        })

    # Save to CSV
    csv_path = os.path.join(AUDIT_DIR, "fundednext_h4_backtest_leaderboard.csv")
    pd.DataFrame(leaderboard_rows).to_csv(csv_path, index=False)

    if all_executed_trades:
        trades_csv_path = os.path.join(AUDIT_DIR, "fundednext_h4_executed_trades.csv")
        pd.DataFrame(all_executed_trades).to_csv(trades_csv_path, index=False)

    # Sector / Asset Class Summary
    df_board = pd.DataFrame(leaderboard_rows)
    print("\n" + "=" * 120)
    print("ASSET CLASS PERFORMANCE SUMMARY (JAN 2024 - PRESENT)")
    print("=" * 120)
    if not df_board.empty:
        sector_agg = df_board.groupby("asset_class").agg(
            assets=("symbol", "count"),
            total_trades=("trades", "sum"),
            avg_win_rate=("win_rate_pct", "mean"),
            total_net_r=("net_r", "sum"),
            avg_pf=("profit_factor", "mean"),
        ).reset_index().sort_values("total_net_r", ascending=False)

        sec_header = f"{'Asset Class':<20} {'Assets':<8} {'Total Trades':<14} {'Avg Win Rate':<14} {'Total Net R':<14} {'Avg PF':<10}"
        print(sec_header)
        print("-" * 120)
        for _, srow in sector_agg.iterrows():
            print(
                f"{srow['asset_class']:<20} {int(srow['assets']):<8} {int(srow['total_trades']):<14} "
                f"{srow['avg_win_rate']:>10.1f}%   {srow['total_net_r']:>10.2f}R    {srow['avg_pf']:>7.2f}"
            )

    # Global Universe Totals
    total_trades_all = sum(r["trades"] for r in leaderboard_rows)
    total_wins_all = sum(r["wins"] for r in leaderboard_rows)
    overall_wr = (total_wins_all / total_trades_all * 100.0) if total_trades_all > 0 else 0.0
    total_net_r_all = sum(r["net_r"] for r in leaderboard_rows)

    print("\n" + "=" * 120)
    print("OVERALL FUNDEDNEXT UNIVERSE TOTALS (ALL 96 ASSETS)")
    print(f"Total Assets Tested:   {len(sorted_results)}")
    print(f"Total Trades Taken:    {total_trades_all:,}")
    print(f"Overall Win Rate:      {overall_wr:.2f}% ({total_wins_all:,} Wins / {total_trades_all - total_wins_all:,} Losses)")
    print(f"Total Cumulative R:    {total_net_r_all:,.2f}R (with {args.friction:.2f}R friction accounted for)")
    print(f"Audit CSV Generated:   {csv_path}")
    print("=" * 120 + "\n")


if __name__ == "__main__":
    main()
