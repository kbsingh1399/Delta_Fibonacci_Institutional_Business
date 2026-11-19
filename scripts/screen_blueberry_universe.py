#!/usr/bin/env python3
"""scripts/screen_blueberry_universe.py: Comprehensive screening of all 151 symbols
on Blueberry Markets for liquidity, spread viability, and asset class categorization.
"""

import os
import sys
import json
import MetaTrader5 as mt5
import pandas as pd
import numpy as np

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUDIT_DIR = os.path.join(BASE_DIR, "audit")
os.makedirs(AUDIT_DIR, exist_ok=True)


def screen_all_symbols():
    if not mt5.initialize():
        print(f"❌ Failed to initialize MT5: {mt5.last_error()}")
        sys.exit(1)

    symbols = mt5.symbols_get()
    if not symbols:
        print("❌ No symbols retrieved from MT5 terminal.")
        mt5.shutdown()
        return

    records = []
    print(f"Screening {len(symbols)} symbols on Blueberry Markets...")

    for s in symbols:
        name = s.name
        # Select symbol in MarketWatch
        mt5.symbol_select(name, True)
        info = mt5.symbol_info(name)
        if not info or not info.visible:
            continue

        # Accurate categorization based on Blueberry Markets symbol conventions (.pi = Raw Prime FX/Metals, .p = CFDs)
        if name.endswith(".pi"):
            if any(k in name for k in ["BTC", "ETH", "XRP", "LTC", "DOT"]):
                cat = "CRYPTO"
            elif any(k in name for k in ["XAU", "XAG", "GAU", "XPT", "XPD"]):
                cat = "COMMODITIES"
            else:
                cat = "FOREX"
        elif name.endswith(".p"):
            if any(k in name for k in ["SP500", "NAS100", "DJ30", "JP225", "GER40", "UK100", "HK50", "AU200", "FR40", "STOXX50", "US2000", "CHINA50", "CHINAH", "NETH25", "SWISS20"]):
                cat = "INDICES"
            elif any(k in name for k in ["WTI", "BRENT", "GAS", "COPPER", "ALUMINIUM", "LEAD", "NICKEL", "ZINC"]):
                cat = "COMMODITIES"
            else:
                cat = "CRYPTO"

        # Fetch recent 50 1H bars to calculate ATR
        rates = mt5.copy_rates_from_pos(name, mt5.TIMEFRAME_H1, 0, 50)
        atr_1h = 0.0
        if rates is not None and len(rates) >= 20:
            df = pd.DataFrame(rates)
            tr = np.maximum(df["high"] - df["low"], np.abs(df["high"] - df["close"].shift(1)))
            atr_1h = float(tr.tail(14).mean())

        spread_points = info.spread
        point = info.point
        spread_val = spread_points * point
        spread_atr_ratio = (spread_val / atr_1h) if atr_1h > 0 else 999.0

        records.append({
            "symbol": name,
            "category": cat,
            "digits": info.digits,
            "point": point,
            "tick_size": info.trade_tick_size,
            "tick_value": info.trade_tick_value,
            "min_lot": info.volume_min,
            "max_lot": info.volume_max,
            "lot_step": info.volume_step,
            "spread_points": spread_points,
            "spread_value": spread_val,
            "atr_1h": atr_1h,
            "spread_atr_ratio": round(spread_atr_ratio, 4),
            "trade_mode": info.trade_mode,
        })

    mt5.shutdown()

    df_res = pd.DataFrame(records)
    out_csv = os.path.join(AUDIT_DIR, "symbol_universe_screening.csv")
    df_res.to_csv(out_csv, index=False)
    print(f"✅ Screened {len(df_res)} symbols. Saved to {out_csv}")

    # Print summary by category
    print("\nSummary by Category:")
    print(df_res["category"].value_counts())

    # Filter viable symbols: spread_atr_ratio <= 0.20 (spread is <= 20% of 1H ATR)
    viable = df_res[df_res["spread_atr_ratio"] <= 0.25]
    print(f"\nViable Liquid Symbols (spread <= 25% of 1H ATR): {len(viable)} / {len(df_res)}")
    print(viable["category"].value_counts())

    out_viable = os.path.join(AUDIT_DIR, "viable_liquid_symbols.json")
    with open(out_viable, "w") as f:
        json.dump(viable["symbol"].tolist(), f, indent=2)
    print(f"Saved viable symbols list to {out_viable}")


if __name__ == "__main__":
    screen_all_symbols()
