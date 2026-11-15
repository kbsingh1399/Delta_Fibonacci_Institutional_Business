import os
import sys
import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.join(BASE_DIR, "engine") not in sys.path:
    sys.path.insert(0, os.path.join(BASE_DIR, "engine"))

from simulate_business_portfolio import simulate_portfolio_event_driven

def main():
    lead_file = os.path.join(BASE_DIR, "audit", "delta_calmar_leaderboard.csv")
    df_lead = pd.read_csv(lead_file)
    print(f"Total profitable assets available: {len(df_lead)}")
    print("=" * 125)
    print("BASKET SCALING STUDY: EXPANDING ASSET UNIVERSE ($5,000 BASE CAPITAL, MAX 6 CONCURRENT)")
    print("=" * 125)

    study_rows = []

    for top_n in [10, 20, 35, 50, 75, 100, 150, len(df_lead)]:
        basket = df_lead.head(top_n).to_dict('records')
        res = simulate_portfolio_event_driven(basket, initial_capital=5000.0, max_concurrent=6, max_per_sector=2)
        
        c = res.get('Institutional Standard $50 (1.0%)')
        if not c:
            continue
            
        daily_trades = c['trades'] / c['total_days']
        monthly_trades = daily_trades * 30.0
        
        study_rows.append({
            'Basket Size': f'Top {top_n}',
            'Total Trades': c['trades'],
            'Trades / Day': round(daily_trades, 2),
            'Trades / Month': round(monthly_trades, 1),
            'Win Rate %': c['win_rate'],
            'Net Profit ($)': c['profit'],
            'Total ROI %': c['roi_pct'],
            'Max DD %': c['max_dd_pct'],
            'Calmar Ratio': c['calmar_ratio'],
            'Avg Monthly ($)': c['monthly_usd'],
            'Avg Monthly (INR)': f"₹{c['monthly_inr']:,.0f}"
        })

    df_study = pd.DataFrame(study_rows)
    print(df_study.to_string(index=False))
    print("=" * 125)

if __name__ == '__main__':
    main()
