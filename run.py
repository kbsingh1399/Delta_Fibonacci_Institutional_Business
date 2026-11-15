#!/usr/bin/env python3
"""run.py: Unified Master CLI Runner for Delta Fibonacci Institutional Business.

Target Venue: Delta Exchange India (api.india.delta.exchange) | FIU-IND Compliant
Base Capital: ₹5,000 INR | 10X Isolated Margin | 1R = ₹50 INR Risk

Usage:
    python run.py --verify          # Run full 6-point institutional readiness suite
    python run.py --backtest        # Run official portfolio backtest (Alpha Basket)
    python run.py --backtest --all  # Run baseline portfolio backtest (all liquid assets)
    python run.py --live            # Run live 4H market scanner
    python run.py --screen          # Run liquid asset universe screener
"""

import sys
import os
import argparse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENGINE_DIR = os.path.join(BASE_DIR, "engine")
if ENGINE_DIR not in sys.path:
    sys.path.insert(0, ENGINE_DIR)

def main():
    parser = argparse.ArgumentParser(
        description="Delta Fibonacci Institutional Business Master Runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  python run.py --verify
  python run.py --backtest
  python run.py --backtest --all
  python run.py --live --tf 4H
  python run.py --screen
        """
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--verify", action="store_true", help="Run 6-point institutional readiness verification suite")
    group.add_argument("--backtest", action="store_true", help="Run official ₹5,000 portfolio backtest")
    group.add_argument("--wyckoff", action="store_true", help="Run Institutional Wyckoff Absorption & Hybrid Chandelier Engine")
    group.add_argument("--live", action="store_true", help="Run live market scanner on Delta Exchange India")
    group.add_argument("--trade", action="store_true", help="Start institutional trade execution and live monitor")
    group.add_argument("--screen", action="store_true", help="Screen liquid tradable assets meeting turnover floor")

    parser.add_argument("--mode", choices=["demo", "paper", "live"], default="demo", help="Execution mode for --trade (default: demo)")
    parser.add_argument("--interval", type=int, default=60, help="Polling interval in seconds for --trade (default: 60)")
    parser.add_argument("--once", action="store_true", help="Run a single cycle and exit for --trade")
    parser.add_argument("--live-confirm", action="store_true", help="Mandatory flag to confirm real live trading")
    parser.add_argument("--capital", type=float, default=5000.0, help="Base trading capital in INR (default: 5000.0)")

    parser.add_argument("--all", action="store_true", help="Include all liquid assets (disables Alpha Basket filtering)")
    parser.add_argument("--taker-fees", action="store_true", help="Use legacy full taker fees (0.138R) instead of Maker Limit TP rebate model (0.050R)")
    parser.add_argument("--live-universe", action="store_true", help="Refresh universe snapshot from live Delta Exchange API instead of static offline cache")
    parser.add_argument("--tf", choices=["4H", "1H", "ALL"], default="4H", help="Timeframe for scanner (default: 4H)")
    parser.add_argument("--sector", default="ALL", help="Sector filter for scanner")

    args = parser.parse_args()

    if args.verify:
        from verify_live_readiness import main as verify_main
        verify_main()

    elif args.backtest:
        from run_inr_portfolio_backtest import run_portfolio_backtest
        run_portfolio_backtest(alpha_basket=not args.all, offline=not args.live_universe, maker_rebate=not args.taker_fees)

    elif args.wyckoff:
        import institutional_wyckoff_engine as we
        specs = we.load_deployable_specs(offline=not args.live_universe)
        candidates, dfs = we.scan_historical_candidates("data", specs)
        print("=" * 105)
        print("DELTA EXCHANGE INDIA — INSTITUTIONAL WYCKOFF ABSORPTION & HYBRID RUNNER ENGINE")
        print("4H Fib_89_233 | Aligned Body | Volume Dry-Up | 50% Maker Limit TP + 50% Chandelier Runner")
        print("=" * 105)
        res = we.run_event_driven_inr_portfolio(candidates, dfs, specs, compounding=True, exit_policy="hybrid")
        print(f"Base Initial Capital:       ₹{res['starting_capital_inr']:,.2f} INR")
        print(f"Final Realized Equity:      ₹{res['final_equity_inr']:,.2f} INR")
        print(f"Total Realized Profit:      ₹{res['net_profit_inr']:,.2f} INR (+{res['return_pct']}%)")
        print(f"Total Executed Trades:      {res['trades']} trades")
        print(f"Portfolio Win Rate:         {res['win_rate_pct']}%")
        print(f"Profit Factor:              {res['profit_factor']}")
        print(f"Average Net R:              {res['avg_net_r']:+.3f}R")
        print(f"Peak Drawdown:              ₹{res['max_dd_inr']:,.2f} INR ({res['max_dd_pct']}%)")
        print(f"Calmar Ratio:               {res['calmar']}")
        print("=" * 105)

    elif args.live:
        from delta_live_scanner import main as scanner_main
        sys.argv = [sys.argv[0], "--tf", args.tf, "--sector", args.sector]
        if args.all:
            sys.argv.append("--include_majors")
        scanner_main()

    elif args.trade:
        from delta_trader_monitor import DeltaTraderMonitor
        monitor = DeltaTraderMonitor(
            mode=args.mode,
            equity_inr=args.capital,
            risk_pct=0.01,
            leverage=10.0,
            tf=args.tf,
            poll_interval_sec=args.interval
        )
        if args.once:
            monitor.run_cycle()
        else:
            monitor.run_loop()

    elif args.screen:
        from universe_screener import main as screener_main
        sys.argv = [sys.argv[0]]
        screener_main()

if __name__ == "__main__":
    main()
