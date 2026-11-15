#!/usr/bin/env python3
r"""dump_fundednext_data.py: Exports historical H4 OHLC data from FundedNext MT5 terminal
into Parquet files for institutional backtesting.

Target directory: C:\Users\SIGMA\Documents\Delta_Fibonacci_Institutional_Business\FundedNext_Data
"""

import os
import sys
from datetime import datetime, timezone
import MetaTrader5 as mt5
import pandas as pd

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "FundedNext_Data")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Start from 2023-01-01 to give full EMA 233 warmup before 2024-01-01
START_DATE = datetime(2023, 1, 1, tzinfo=timezone.utc)
END_DATE = datetime.now(timezone.utc)


def main():
    print("=" * 80)
    print("FUNDEDNEXT MT5 HISTORICAL DATA DUMPER (H4)")
    print(f"Output Directory: {OUTPUT_DIR}")
    print(f"Date Range: {START_DATE.strftime('%Y-%m-%d')} to {END_DATE.strftime('%Y-%m-%d')}")
    print("=" * 80)

    if not mt5.initialize():
        print(f"[ERROR] Failed to initialize MT5: {mt5.last_error()}")
        sys.exit(1)

    term = mt5.terminal_info()
    acc = mt5.account_info()
    print(f"[INFO] Connected to MT5: {term.name} ({term.path})")
    print(f"[INFO] Account: {acc.login} | Server: {acc.server} | Company: {acc.company}")

    symbols = mt5.symbols_get()
    if not symbols:
        print("[ERROR] No symbols retrieved from MT5.")
        mt5.shutdown()
        sys.exit(1)

    sym_names = sorted([s.name for s in symbols])
    print(f"[INFO] Discovered {len(sym_names)} symbols on {acc.server}")
    print("-" * 80)

    success_count = 0
    fail_count = 0
    total_bars = 0
    records = []

    for idx, sym in enumerate(sym_names, 1):
        if not mt5.symbol_select(sym, True):
            print(f"[{idx:02d}/{len(sym_names)}] [WARN] Failed to select symbol {sym}")

        rates = mt5.copy_rates_range(sym, mt5.TIMEFRAME_H4, START_DATE, END_DATE)
        if rates is None or len(rates) == 0:
            err = mt5.last_error()
            print(f"[{idx:02d}/{len(sym_names)}] [FAIL] {sym:<10} No data returned (Error: {err})")
            fail_count += 1
            continue

        df = pd.DataFrame(rates)
        # Ensure standard column naming and types
        # time is in unix epoch seconds
        df["timestamp"] = df["time"].astype("int64")
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)

        # Reorder columns cleanly: time, timestamp, open, high, low, close, tick_volume, spread, real_volume
        cols = ["time", "timestamp", "open", "high", "low", "close", "tick_volume", "spread", "real_volume"]
        available_cols = [c for c in cols if c in df.columns]
        df = df[available_cols]

        out_path = os.path.join(OUTPUT_DIR, f"{sym}.parquet")
        df.to_parquet(out_path, index=False, engine="pyarrow")

        n_bars = len(df)
        total_bars += n_bars
        earliest = df["time"].iloc[0].strftime("%Y-%m-%d")
        latest = df["time"].iloc[-1].strftime("%Y-%m-%d")
        file_size_kb = os.path.getsize(out_path) / 1024.0

        records.append({
            "symbol": sym,
            "bars": n_bars,
            "start": earliest,
            "end": latest,
            "size_kb": round(file_size_kb, 1),
        })

        print(
            f"[{idx:02d}/{len(sym_names)}] [OK] {sym:<10} | Bars: {n_bars:>5} | "
            f"Range: {earliest} to {latest} | Size: {file_size_kb:>5.1f} KB"
        )
        success_count += 1

    mt5.shutdown()

    print("=" * 80)
    print(f"[SUMMARY] Finished dumping MT5 data.")
    print(f"Successful: {success_count}/{len(sym_names)} symbols")
    print(f"Failed:     {fail_count}/{len(sym_names)} symbols")
    print(f"Total Bars: {total_bars:,}")
    print(f"Parquet Dir: {OUTPUT_DIR}")
    print("=" * 80)


if __name__ == "__main__":
    main()
