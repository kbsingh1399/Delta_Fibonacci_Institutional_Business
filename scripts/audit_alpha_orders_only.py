#!/usr/bin/env python3
"""
scripts/audit_alpha_orders_only.py
Exclusively audit orders and positions punched by the Alpha_ engine.
Separates pure Alpha_ stats from manual or external account trades.
"""

import sys
import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime, timezone, timedelta

def run_alpha_audit():
    if not mt5.initialize():
        print("ERROR: Failed to initialize MT5")
        sys.exit(1)

    print("================================================================================")
    print("           ISOLATED AUDIT: ALPHA_ ENGINE ORDERS & DEALS ONLY                    ")
    print(f"           Broker: BlueberryMarketsSVG-Live | Account: #5126976                 ")
    print(f"           Audit Timestamp: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("================================================================================")

    # 1. Load the Alpha live ledger
    ledger_path = "audit/mt5_multicriteria_live_ledger.csv"
    try:
        ledger_df = pd.read_csv(ledger_path)
        alpha_entry_tickets = set(ledger_df["ticket"].astype(int).tolist())
    except Exception as e:
        print(f"Warning: Could not read ledger: {e}")
        alpha_entry_tickets = set()

    print(f"Total Alpha_ entry tickets registered in ledger: {len(alpha_entry_tickets)}")

    # 2. Open Positions
    open_positions = mt5.positions_get()
    alpha_open = []
    other_open = []

    if open_positions:
        for p in open_positions:
            is_alpha = (
                p.ticket in alpha_entry_tickets
                or "Alpha_" in (p.comment or "")
                or (p.magic is not None and p.magic > 10000000)
            )
            item = {
                "ticket": p.ticket,
                "symbol": p.symbol,
                "type": "BUY" if p.type == 0 else "SELL",
                "volume": p.volume,
                "price_open": p.price_open,
                "price_current": p.price_current,
                "sl": p.sl,
                "tp": p.tp,
                "profit": p.profit,
                "comment": p.comment or "",
                "magic": p.magic,
                "time": datetime.fromtimestamp(p.time, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            }
            if is_alpha:
                alpha_open.append(item)
            else:
                other_open.append(item)

    print(f"\n[1] ACTIVE ALPHA_ OPEN POSITIONS: {len(alpha_open)}")
    print("--------------------------------------------------------------------------------")
    alpha_float_total = 0.0
    if alpha_open:
        for p in alpha_open:
            alpha_float_total += p["profit"]
            print(f"  #{p['ticket']} | {p['symbol']:<10} | {p['type']:<4} {p['volume']} | Open: {p['price_open']:<9} | SL: {p['sl']:<9} | TP: {p['tp']:<9} | Float: ${p['profit']:>6.2f} | Comment: {p['comment']}")
        print(f"  --> Net Alpha_ Floating PnL: ${alpha_float_total:.2f}")
    else:
        print("  None currently open.")

    print(f"\n[2] OTHER (MANUAL / SWING) OPEN POSITIONS: {len(other_open)}")
    print("--------------------------------------------------------------------------------")
    other_float_total = 0.0
    if other_open:
        for p in other_open:
            other_float_total += p["profit"]
            print(f"  #{p['ticket']} | {p['symbol']:<10} | {p['type']:<4} {p['volume']} | Open: {p['price_open']:<9} | Float: ${p['profit']:>6.2f} | Comment: {p['comment']}")
        print(f"  --> Net Other Floating PnL: ${other_float_total:.2f}")

    # 3. Closed Deals in History (Past 7 Days)
    from_t = datetime.now(timezone.utc) - timedelta(days=7)
    deals = mt5.history_deals_get(from_t, datetime.now(timezone.utc))

    alpha_closed = []
    other_closed = []

    if deals:
        for d in deals:
            # We only evaluate closing deals (entry == 1) to measure realized PnL
            if d.entry == 1:
                is_alpha = (
                    d.position_id in alpha_entry_tickets
                    or "Alpha_" in (d.comment or "")
                )
                item = {
                    "deal_ticket": d.ticket,
                    "position_id": d.position_id,
                    "symbol": d.symbol,
                    "type": "BUY" if d.type == 0 else "SELL",
                    "volume": d.volume,
                    "price": d.price,
                    "profit": d.profit,
                    "comment": d.comment or "",
                    "time": datetime.fromtimestamp(d.time, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                }
                if is_alpha:
                    alpha_closed.append(item)
                else:
                    other_closed.append(item)

    print(f"\n[3] ALPHA_ CLOSED DEALS PERFORMANCE SUMMARY")
    print("================================================================================")
    if alpha_closed:
        df_a = pd.DataFrame(alpha_closed)
        total_pnl = df_a["profit"].sum()
        wins = df_a[df_a["profit"] > 0]
        losses = df_a[df_a["profit"] < 0]
        bes = df_a[df_a["profit"] == 0]

        gross_win = wins["profit"].sum() if not wins.empty else 0.0
        gross_loss = abs(losses["profit"].sum()) if not losses.empty else 0.0
        pf = (gross_win / gross_loss) if gross_loss > 0 else (999.0 if gross_win > 0 else 0.0)
        win_rate = (len(wins) / len(df_a)) * 100.0

        print(f"  • Total Closed Alpha_ Trades : {len(df_a)}")
        print(f"  • Win / Loss / Breakeven     : {len(wins)} Wins | {len(losses)} Losses | {len(bes)} BE")
        print(f"  • Win Rate                   : {win_rate:.1f}%")
        print(f"  • Gross Wins                 : +${gross_win:.2f}")
        print(f"  • Gross Losses               : -${gross_loss:.2f}")
        print(f"  • Profit Factor (PF)         : {pf:.2f}")
        print(f"  • Net Realized Profit        : ${total_pnl:+.2f}")
        if not wins.empty:
            best_win = wins.loc[wins['profit'].idxmax()]
            print(f"  • Best Winner                : Pos #{best_win['position_id']} ({best_win['symbol']}) +${best_win['profit']:.2f}")
        if not losses.empty:
            worst_loss = losses.loc[losses['profit'].idxmin()]
            print(f"  • Worst Loss                 : Pos #{worst_loss['position_id']} ({worst_loss['symbol']}) ${worst_loss['profit']:.2f}")

        print("\n  FULL BREAKDOWN OF CLOSED ALPHA_ TRADES:")
        print("  ------------------------------------------------------------------------------")
        print(f"  {'Pos ID':<10} | {'Symbol':<10} | {'PnL ($)':<9} | {'Exit Time (UTC)':<19} | {'Comment'}")
        print("  ------------------------------------------------------------------------------")
        for idx, r in df_a.iterrows():
            print(f"  #{r['position_id']:<9} | {r['symbol']:<10} | ${r['profit']:>+7.2f} | {r['time']:<19} | {r['comment']}")
    else:
        print("  No closed Alpha_ trades found in history window.")

    print(f"\n[4] COMPARISON: ALPHA_ VS OTHER ACCOUNT ACTIVITY")
    print("================================================================================")
    df_o = pd.DataFrame(other_closed) if other_closed else pd.DataFrame()
    other_pnl = df_o["profit"].sum() if not df_o.empty else 0.0
    total_acc_realized = (df_a["profit"].sum() if alpha_closed else 0.0) + other_pnl

    acc = mt5.account_info()
    print(f"  • Alpha_ Net Realized PnL    : ${(df_a['profit'].sum() if alpha_closed else 0.0):+.2f}")
    print(f"  • Other / Manual Realized    : ${other_pnl:+.2f} ({len(df_o)} deals)")
    print(f"  • Total Account Realized     : ${total_acc_realized:+.2f}")
    print(f"  • Current Account Balance    : ${acc.balance:.2f}")
    print(f"  • Current Account Equity     : ${acc.equity:.2f}")
    print("================================================================================")

    mt5.shutdown()

if __name__ == "__main__":
    run_alpha_audit()
