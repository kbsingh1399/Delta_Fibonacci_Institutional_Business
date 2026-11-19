#!/usr/bin/env python3
"""scripts/dump_blueberry_mtf.py: High-speed ingestion of Multi-Timeframe rates
(5M, 15M, 1H, 4H) for the 23 Champion assets from MT5 into Blueberry_Data/*.parquet.
"""

import os
import sys
import time
from datetime import datetime, timezone
import MetaTrader5 as mt5
import pandas as pd
import numpy as np

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

DATA_DIR = os.path.join(BASE_DIR, "Blueberry_Data")
os.makedirs(DATA_DIR, exist_ok=True)

try:
    from mt5_alpha_trader_live import OPTIMAL_ALPHA_BASKET
except ImportError:
    OPTIMAL_ALPHA_BASKET = [
        {"symbol": "SP500.p"}, {"symbol": "JP225.p"}, {"symbol": "DJ30.p"},
        {"symbol": "NAS100.p"}, {"symbol": "HK50.p"}, {"symbol": "XAUUSD.pi"},
        {"symbol": "XAGUSD.pi"}, {"symbol": "USWTI.p"}, {"symbol": "BTCUSD.pi"},
        {"symbol": "ETHUSD.pi"}, {"symbol": "XLMUSD.p"}, {"symbol": "XRPUSD.pi"},
        {"symbol": "ADAUSD.p"}, {"symbol": "USDCAD.pi"}, {"symbol": "EURUSD.pi"},
        {"symbol": "GBPUSD.pi"}, {"symbol": "USDCHF.pi"}, {"symbol": "GBPJPY.pi"},
        {"symbol": "CADJPY.pi"}, {"symbol": "CHFJPY.pi"}, {"symbol": "USDJPY.pi"},
        {"symbol": "NZDSGD.pi"}, {"symbol": "GBPNOK.pi"}
    ]

TIMEFRAME_MAP = {
    "5M": (mt5.TIMEFRAME_M5, 50000),
    "15M": (mt5.TIMEFRAME_M15, 50000),
    "1H": (mt5.TIMEFRAME_H1, 25000),
    "4H": (mt5.TIMEFRAME_H4, 15000),
}


def dump_multitimeframe_data():
    if not mt5.initialize():
        print(f"❌ Failed to initialize MT5: {mt5.last_error()}")
        sys.exit(1)

    acc = mt5.account_info()
    print("=" * 80)
    print("📥 BLUEBERRY MARKETS MULTI-TIMEFRAME INGESTION ENGINE")
    print(f"Account: {acc.login if acc else 'N/A'} | Server: {acc.server if acc else 'N/A'}")
    print(f"Target Directory: {DATA_DIR}")
    print(f"Target Assets: {len(OPTIMAL_ALPHA_BASKET)} Champion Assets")
    print(f"Target Timeframes: {list(TIMEFRAME_MAP.keys())}")
    print("=" * 80)

    symbols = [x["symbol"] for x in OPTIMAL_ALPHA_BASKET]
    t0 = time.time()
    total_saved = 0

    for idx, sym in enumerate(symbols, 1):
        if not mt5.symbol_select(sym, True):
            print(f"⚠️ Could not select symbol in MarketWatch: {sym}")

        sym_saved = 0
        for tf_str, (tf_const, bar_count) in TIMEFRAME_MAP.items():
            try:
                rates = mt5.copy_rates_from_pos(sym, tf_const, 0, bar_count)
                if rates is not None and len(rates) >= 50:
                    df = pd.DataFrame(rates)
                    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
                    df = df.sort_values("time").drop_duplicates(subset=["time"]).reset_index(drop=True)

                    out_path = os.path.join(DATA_DIR, f"{sym}_{tf_str}.parquet")
                    df.to_parquet(out_path, index=False)
                    sym_saved += 1
                    total_saved += 1
                else:
                    print(f"⚠️ Insufficient rates for {sym} {tf_str}: {len(rates) if rates is not None else 0}")
            except Exception as e:
                print(f"❌ Error fetching {sym} {tf_str}: {e}")

        print(f"[{idx:02d}/{len(symbols)}] Ingested {sym:<12} | {sym_saved}/4 Timeframes Saved")

    mt5.shutdown()
    elapsed = time.time() - t0
    print("=" * 80)
    print(f"✅ MULTI-TIMEFRAME INGESTION COMPLETE in {elapsed:.1f}s")
    print(f"Total Parquet Files Saved/Updated: {total_saved}")
    print("=" * 80)


if __name__ == "__main__":
    dump_multitimeframe_data()
