"""generate_active_inr_universe.py: Generates the Complete Active Liquid Basket in INR.
Converts all Delta Exchange India metrics to Indian Rupees (₹) at the fixed 85.0 INR/USD rate.
Categorizes assets across the 5 institutional sectors and calculates exact lot sizes for ₹50 risk.
"""

import os
import sys
import json
import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.join(BASE_DIR, "engine") not in sys.path:
    sys.path.insert(0, os.path.join(BASE_DIR, "engine"))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from universe_screener import load_snapshot, screen, INR_PER_USD
from asset_universe_classifier import classify_asset, get_sector_display_name

def generate_report():
    products, tickers = load_snapshot()
    # Liquidity floor: $100k USD = Rs 85,00,000 (85 Lakhs)
    rows = screen(
        products, tickers,
        risk_cap_inr=55.0,
        margin_cap_inr=2500.0,
        volume_floor_usd=100000.0, # Rs 85 Lakhs
        friction_cap_r=0.20
    )
    
    elig = [r for r in rows if r.get('eligible')]
    
    sectors = {}
    for r in elig:
        sec = classify_asset(r['symbol'])
        sectors.setdefault(sec, []).append(r)
        
    print(f"\nTOTAL ACTIVE DEPLOYABLE ASSETS (LIQUID BASKET): {len(elig)}")
    print(f"LIQUIDITY FLOOR: ₹85,00,000 / day (₹85 Lakhs 24h Turnover)")
    print(f"BASE ACCOUNT: ₹5,000 INR | LEVERAGE: 10X ISOLATED | 1R RISK: ₹50 INR\n")
    
    out_lines = []
    
    for sec_code, items in sorted(sectors.items()):
        disp_name = get_sector_display_name(sec_code)
        header = f"### {disp_name} ({len(items)} Assets)"
        print(header)
        out_lines.append(header)
        
        table_hdr = "| Symbol | 24h Turnover (INR) | 1-Lot Notional | 10X Margin | 1-Lot Risk (SL) | Lots for ₹50 Risk (1R) | Actual ₹ Risk | Fee Drag |"
        sep = "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |"
        print(table_hdr)
        print(sep)
        out_lines.append(table_hdr)
        out_lines.append(sep)
        
        # Sort items by 24h turnover descending
        sorted_items = sorted(items, key=lambda x: -x['turnover_24h_usd'])
        
        for r in sorted_items:
            turnover_inr = r['turnover_24h_usd'] * INR_PER_USD
            if turnover_inr >= 1e7:
                turnover_str = f"₹{turnover_inr/1e7:,.2f} Cr"
            else:
                turnover_str = f"₹{turnover_inr/1e5:,.1f} Lakh"
                
            risk_1ct = r['risk_1ct_inr']
            target_lots = max(1, int(round(50.0 / risk_1ct))) if risk_1ct > 0 else 1
            actual_risk = target_lots * risk_1ct
            notional = target_lots * r['min_lot_notional_inr']
            margin = target_lots * r['min_lot_margin_inr']
            
            line = f"| `{r['symbol']}` | {turnover_str} | ₹{r['min_lot_notional_inr']:,.0f} | ₹{r['min_lot_margin_inr']:,.0f} | ₹{risk_1ct:.2f} | **{target_lots} lots** | ₹{actual_risk:.1f} | {r['fee_rt_r']:.3f}R |"
            print(line)
            out_lines.append(line)
            
        print("\n")
        out_lines.append("\n")
        
    # Write complete markdown catalog
    report_file = os.path.join(BASE_DIR, "reports", "active_liquid_basket_inr.md")
    os.makedirs(os.path.dirname(report_file), exist_ok=True)
    with open(report_file, "w", encoding="utf-8") as f:
        f.write("# Delta Exchange India - Active Deployable Basket (INR Denominated)\n\n")
        f.write(f"**Base Capital**: ₹5,000 INR | **Leverage**: 10X Isolated | **1R Risk Target**: ₹50 INR (1.0%)\n")
        f.write(f"**Platform FX Rate**: 85.0 INR/USD (Fixed) | **Liquidity Floor**: ₹85 Lakhs (24h Turnover)\n\n")
        f.write("\n".join(out_lines))
        
    print(f"Complete catalog written to: {report_file}")

if __name__ == "__main__":
    generate_report()
