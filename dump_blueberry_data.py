#!/usr/bin/env python3
"""dump_blueberry_data.py: Ingests 1H and 4H OHLCV rates for all Blueberry Markets symbols
from 2023-01-01 to present into Blueberry_Data/*.parquet.
"""

import os
import sys
import time
from datetime import datetime, timezone
import MetaTrader5 as mt5
import pandas as pd
import numpy as np

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "Blueberry_Data")
os.makedirs(OUTPUT_DIR, exist_ok=True)

START_DATE = datetime(2023, 1, 1, tzinfo=timezone.utc)
NOW_DATE = datetime.now(timezone.utc)


def dump_all_blueberry_data():
    if not mt5.initialize():
        print(f"❌ Failed to initialize MT5: {mt5.last_error()}")
        sys.exit(1)

    acc = mt5.account_info()
    term = mt5.terminal_info()
    print("=" * 80)
    print(f"📥 BLUEBERRY MARKETS DATA DUMP ENGINE")
    print(f"Account: {acc.login if acc else 'N/A'} | Server: {acc.server if acc else 'N/A'}")
    print(f"Target Directory: {OUTPUT_DIR}")
    print(f"Time Range: {START_DATE.strftime('%Y-%m-%d')} to {NOW_DATE.strftime('%Y-%m-%d')}")
    print("=" * 80)

    symbols = mt5.symbols_get()
    if not symbols:
        print("❌ No symbols retrieved from MT5 terminal.")
        mt5.shutdown()
        return

    sym_names = sorted([s.name for s in symbols])
    total = len(sym_names)
    print(f"Found {total} tradeable symbols on Blueberry Markets.")

    saved_1h = 0
    saved_4h = 0
    t0 = time.time()

    for idx, sym in enumerate(sym_names, 1):
        # Ensure symbol is selected in Market Watch
        mt5.symbol_select(sym, True)

        # 1. Fetch 1H rates
        try:
            rates_1h = mt5.copy_rates_range(sym, mt5.TIMEFRAME_H1, START_DATE, NOW_DATE)
            if rates_1h is not None and len(rates_1h) >= 50:
                df_1h = pd.DataFrame(rates_1h)
                df_1h["time"] = pd.to_datetime(df_1h["time"], unit="s", utc=True)
                p1 = os.path.join(OUTPUT_DIR, f"{sym}_1H.parquet")
                df_1h.to_parquet(p1, index=False)
                saved_1h += 1
        except Exception as e:
            pass

        # 2. Fetch 4H rates
        try:
            rates_4h = mt5.copy_rates_range(sym, mt5.TIMEFRAME_H4, START_DATE, NOW_DATE)
            if rates_4h is not None and len(rates_4h) >= 20:
                df_4h = pd.DataFrame(rates_4h)
                df_4h["time"] = pd.to_datetime(df_4h["time"], unit="s", utc=True)
                p4 = os.path.join(OUTPUT_DIR, f"{sym}_4H.parquet")
                df_4h.to_parquet(p4, index=False)
                saved_4h += 1
        except Exception as e:
            pass

        if idx % 15 == 0 or idx == total:
            elapsed = time.time() - t0
            print(f"[{idx}/{total}] Processed {sym} | 1H: {saved_1h} files | 4H: {saved_4h} files | Elapsed: {elapsed:.1f}s")

    mt5.shutdown()
    print("=" * 80)
    print(f"✅ DATA DUMP COMPLETE in {time.time() - t0:.1f}s")
    print(f"Total 1H Parquet Files: {saved_1h}")
    print(f"Total 4H Parquet Files: {saved_4h}")
    print(f"Total Files in {OUTPUT_DIR}: {saved_1h + saved_4h}")
    print("=" * 80)


if __name__ == "__main__":
    dump_all_blueberry_data()
