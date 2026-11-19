#!/usr/bin/env python3
"""scripts/dump_viable_universe_mtf.py: Ingests 5M, 15M, 1H, 4H data for all 98 viable liquid symbols
on Blueberry Markets into Blueberry_Data/*.parquet. Skips existing full files to minimize MT5 load.
"""

import os
import sys
import json
import time
import MetaTrader5 as mt5
import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "Blueberry_Data")
AUDIT_DIR = os.path.join(BASE_DIR, "audit")
os.makedirs(DATA_DIR, exist_ok=True)

TIMEFRAME_MAP = {
    "5M": (mt5.TIMEFRAME_M5, 50000),
    "15M": (mt5.TIMEFRAME_M15, 50000),
    "1H": (mt5.TIMEFRAME_H1, 25000),
    "4H": (mt5.TIMEFRAME_H4, 15000),
}


def dump_viable_universe():
    viable_path = os.path.join(AUDIT_DIR, "viable_liquid_symbols.json")
    if not os.path.exists(viable_path):
        print(f"❌ Viable symbols file missing: {viable_path}")
        sys.exit(1)

    with open(viable_path, "r") as f:
        symbols = json.load(f)

    if not mt5.initialize():
        print(f"❌ Failed to initialize MT5: {mt5.last_error()}")
        sys.exit(1)

    print("=" * 85, flush=True)
    print(f"📥 INGESTING MULTI-TIMEFRAME RATES FOR {len(symbols)} VIABLE LIQUID SYMBOLS", flush=True)
    print(f"Target Directory: {DATA_DIR}", flush=True)
    print(f"Timeframes: {list(TIMEFRAME_MAP.keys())}", flush=True)
    print("=" * 85, flush=True)

    t0 = time.time()
    total_saved = 0
    total_skipped = 0

    for idx, sym in enumerate(symbols, 1):
        mt5.symbol_select(sym, True)
        sym_saved = 0
        sym_skipped = 0

        for tf_str, (tf_const, bar_count) in TIMEFRAME_MAP.items():
            out_path = os.path.join(DATA_DIR, f"{sym}_{tf_str}.parquet")
            # Skip if file already exists with >= 30,000 bars (for 5M/15M) or >= 5,000 bars (for 1H/4H)
            min_needed = 30000 if tf_str in ["5M", "15M"] else 5000
            if os.path.exists(out_path):
                try:
                    existing_len = len(pd.read_parquet(out_path, columns=["time"]))
                    if existing_len >= min_needed:
                        sym_skipped += 1
                        total_skipped += 1
                        continue
                except Exception:
                    pass

            try:
                rates = mt5.copy_rates_from_pos(sym, tf_const, 0, bar_count)
                if rates is not None and len(rates) >= 50:
                    df = pd.DataFrame(rates)
                    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
                    df = df.sort_values("time").drop_duplicates(subset=["time"]).reset_index(drop=True)
                    df.to_parquet(out_path, index=False)
                    sym_saved += 1
                    total_saved += 1
            except Exception as e:
                print(f"❌ Error fetching {sym} {tf_str}: {e}", flush=True)

        print(f"[{idx:02d}/{len(symbols)}] {sym:<12} | Saved: {sym_saved} | Cached/Skipped: {sym_skipped}", flush=True)

    mt5.shutdown()
    elapsed = time.time() - t0
    print("=" * 85, flush=True)
    print(f"✅ INGESTION COMPLETE in {elapsed:.1f}s | Saved: {total_saved} | Cached: {total_skipped}", flush=True)
    print("=" * 85, flush=True)


if __name__ == "__main__":
    dump_viable_universe()
