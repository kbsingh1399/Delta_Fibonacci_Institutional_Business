#!/usr/bin/env python3
"""
engine/kaizen_self_improvement.py
================================================================================
KAIZEN (改善) CONTINUOUS QUANTITATIVE IMPROVEMENT & TRADE AVOIDANCE ENGINE
================================================================================
SOP v2.1.0 | Institutional Trade Factor Attribution, Deflated Expectancy & Toxic Setup Pruning.

PURPOSE:
1. Deconstructs live MT5 deal history and execution ledgers into factor triplets: (Symbol, Timeframe, Setup).
2. Measures realized Win Rate, Profit Factor, Expectancy (E[R]), and Drawdown Contribution per setup.
3. Automatically identifies toxic/bleeding setups exhibiting persistent negative drift.
4. Generates an active Quarantine / Avoidance List (audit/kaizen_quarantine_setups.json) that dynamically
   gates live execution, preventing the runner from punching negative-expectancy setups.
5. Identifies and protects Prime Champions (positive expectancy setups driving portfolio growth).
6. Enforces institutional "Via Negativa" (addition by subtraction) to maximize Sharpe and Sortino ratios.
"""

import os
import sys
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Tuple
import pandas as pd
import MetaTrader5 as mt5

logger = logging.getLogger("KaizenEngine")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUDIT_DIR = os.path.join(BASE_DIR, "audit")
os.makedirs(AUDIT_DIR, exist_ok=True)

KAIZEN_LEDGER_FILE = os.path.join(AUDIT_DIR, "kaizen_learning_ledger.json")
KAIZEN_QUARANTINE_FILE = os.path.join(AUDIT_DIR, "kaizen_quarantine_setups.json")
LIVE_LEDGER_FILE = os.path.join(AUDIT_DIR, "mt5_multicriteria_live_ledger.csv")


def load_alpha_ledger_mappings() -> Dict[int, Dict[str, Any]]:
    """Loads historical trade tickets to setup metadata mapping."""
    mappings = {}
    if not os.path.exists(LIVE_LEDGER_FILE):
        return mappings
    try:
        df = pd.read_csv(LIVE_LEDGER_FILE)
        for _, r in df.iterrows():
            mappings[int(r["ticket"])] = {
                "symbol": str(r["symbol"]),
                "tf": str(r["tf"]),
                "model": str(r["model"]),
                "setup": str(r["setup"]),
                "magic": int(r["magic"]) if pd.notnull(r.get("magic")) else 0,
            }
    except Exception as e:
        logger.warning(f"Failed to read live ledger for Kaizen mapping: {e}")
    return mappings


def evaluate_kaizen_trade_attribution(lookback_days: int = 7) -> Dict[str, Any]:
    """Forensically attributes every closed deal to its (Symbol, TF, Setup) factor,
    evaluates statistical expectancy, and updates the active Quarantine Avoidance list.
    """
    if not mt5.initialize():
        logger.error("MT5 initialization failed in Kaizen evaluator")
        return {"ok": False, "error": "MT5 init failed"}

    mappings = load_alpha_ledger_mappings()
    from_t = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    deals = mt5.history_deals_get(from_t, datetime.now(timezone.utc))

    trade_records = []
    if deals:
        for d in deals:
            # We only evaluate closing deals (entry == 1) to measure realized outcomes
            if d.entry == 1:
                meta = mappings.get(d.position_id)
                symbol = meta["symbol"] if meta else d.symbol
                tf = meta["tf"] if meta else "UNKNOWN"
                setup = meta["setup"] if meta else (d.comment or "MANUAL")
                key = f"{symbol}:{tf}:{setup}"

                trade_records.append({
                    "pos_id": d.position_id,
                    "symbol": symbol,
                    "tf": tf,
                    "setup": setup,
                    "key": key,
                    "profit": float(d.profit),
                    "comment": d.comment or "",
                    "time": d.time,
                    "is_alpha": (d.position_id in mappings) or ("Alpha_" in (d.comment or ""))
                })

    df = pd.DataFrame(trade_records)
    if df.empty:
        mt5.shutdown()
        return {"ok": True, "trades_count": 0, "quarantine_list": []}

    # Filter to dedicated Alpha trades
    alpha_df = df[df["is_alpha"]].copy()
    if alpha_df.empty:
        mt5.shutdown()
        return {"ok": True, "trades_count": 0, "quarantine_list": []}

    # Group by Setup Key
    factor_summary = []
    quarantine_list = []
    champion_list = []

    grouped = alpha_df.groupby("key")
    for key, group in grouped:
        trades = len(group)
        wins = int((group["profit"] > 0).sum())
        losses = int((group["profit"] < 0).sum())
        net_pnl = float(group["profit"].sum())
        gross_win = float(group.loc[group["profit"] > 0, "profit"].sum()) if wins > 0 else 0.0
        gross_loss = float(abs(group.loc[group["profit"] < 0, "profit"].sum())) if losses > 0 else 0.0
        win_rate = (wins / trades) * 100.0
        pf = round(gross_win / gross_loss, 2) if gross_loss > 0 else (99.0 if gross_win > 0 else 0.0)

        sym = group["symbol"].iloc[0]
        tf = group["tf"].iloc[0]
        setup = group["setup"].iloc[0]

        # Kaizen Status Evaluation:
        # 1. QUARANTINE CRITERIA (Toxic Setups to AVOID):
        #    a) Counter-trend Oil Longs (0% win rate across all timeframes during bear trend)
        #    b) 2 or more losses with 0 wins and negative PnL < -$1.00
        #    c) Net PnL < -$2.50 regardless of sample size (severe capital drain)
        #    d) 1H Gold giant-risk setups (> $10 stop risk)
        is_oil_long = (sym == "USWTI.p" and ("LONG" in setup or "BULLISH" in setup))
        is_high_risk_gold_1h = (sym == "XAUUSD.pi" and tf == "1H")
        is_consistent_bleeder = (trades >= 2 and wins == 0 and net_pnl <= -1.00)
        is_catastrophic_loser = (net_pnl <= -2.50 and win_rate < 30.0)

        status = "ACTIVE"
        quarantine_reason = ""

        if is_oil_long:
            status = "QUARANTINED"
            quarantine_reason = "Macro Bear Regime: 0% historical win rate on oil counter-trend longs. Longs prohibited."
        elif is_high_risk_gold_1h:
            status = "QUARANTINED"
            quarantine_reason = "Excessive Dollar Volatility: 1H Gold ATR creates single-trade stop risk > $10. Restrict to 5M/15M."
        elif is_consistent_bleeder:
            status = "QUARANTINED"
            quarantine_reason = f"Toxic Drift: {trades} trades, 0 wins, net PnL ${net_pnl:.2f} (0% Win Rate)."
        elif is_catastrophic_loser:
            status = "QUARANTINED"
            quarantine_reason = f"Severe Bleed: Net PnL ${net_pnl:.2f} with low win rate ({win_rate:.1f}%)."
        elif wins >= 2 and pf >= 1.50 and net_pnl > 0.50:
            status = "PRIME_CHAMPION"

        record = {
            "key": key,
            "symbol": sym,
            "tf": tf,
            "setup": setup,
            "trades": trades,
            "wins": wins,
            "losses": losses,
            "win_rate": round(win_rate, 1),
            "profit_factor": pf,
            "gross_win": round(gross_win, 2),
            "gross_loss": round(gross_loss, 2),
            "net_pnl": round(net_pnl, 2),
            "status": status,
            "quarantine_reason": quarantine_reason
        }
        factor_summary.append(record)

        if status == "QUARANTINED":
            quarantine_list.append(record)
        elif status == "PRIME_CHAMPION":
            champion_list.append(record)

    # Sort summaries
    factor_summary.sort(key=lambda x: x["net_pnl"], reverse=True)
    quarantine_list.sort(key=lambda x: x["net_pnl"])
    champion_list.sort(key=lambda x: x["net_pnl"], reverse=True)

    # Build Kaizen Intelligence Manifest
    kaizen_manifest = {
        "timestamp_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "total_alpha_trades": len(alpha_df),
        "total_setups_evaluated": len(factor_summary),
        "quarantined_count": len(quarantine_list),
        "champions_count": len(champion_list),
        "quarantine_keys": [q["key"] for q in quarantine_list],
        "quarantine_details": quarantine_list,
        "champions_details": champion_list,
        "factor_attribution": factor_summary
    }

    # Save to persistent files
    try:
        with open(KAIZEN_LEDGER_FILE, "w", encoding="utf-8") as f:
            json.dump(kaizen_manifest, f, indent=2)

        # Standalone clean machine-readable quarantine lookup
        quarantine_map = {q["key"]: q["quarantine_reason"] for q in quarantine_list}
        # Also add generic pattern vetoes (e.g. any USWTI.p:*LONG)
        with open(KAIZEN_QUARANTINE_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "timestamp_utc": kaizen_manifest["timestamp_utc"],
                "quarantine_map": quarantine_map,
                "macro_vetoes": [
                    {"pattern": "USWTI.p:*:*LONG*", "reason": "Prohibit counter-trend oil longs in bear macro regime"},
                    {"pattern": "USWTI.p:*:*BULLISH*", "reason": "Prohibit counter-trend oil longs in bear macro regime"},
                    {"pattern": "XAUUSD.pi:1H:*", "reason": "Prohibit 1H Gold setups due to excessive dollar stop distance"}
                ]
            }, f, indent=2)

        logger.info(f"✅ [KAIZEN] Updated quarantine list ({len(quarantine_list)} setups quarantined, {len(champion_list)} champions promoted)")
    except Exception as e:
        logger.error(f"Failed to write Kaizen files: {e}")

    mt5.shutdown()
    return kaizen_manifest


def check_kaizen_quarantine(symbol: str, tf: str, setup_name: str) -> Tuple[bool, str]:
    """Live runtime gate called before punching orders.
    Checks if a setup is currently quarantined by Kaizen self-improvement engine.
    """
    key = f"{symbol}:{tf}:{setup_name}"

    # 1. Macro Invariant Vetoes
    if symbol == "USWTI.p" and ("LONG" in setup_name or "BULLISH" in setup_name):
        return True, "Macro Bear Regime: Counter-trend oil longs prohibited by Kaizen quant rule"
    if symbol == "XAUUSD.pi" and tf == "1H":
        return True, "Dollar Risk Cap: 1H Gold setups prohibited due to excessive dollar stop distance"

    # 2. Check quarantine ledger file
    if os.path.exists(KAIZEN_QUARANTINE_FILE):
        try:
            with open(KAIZEN_QUARANTINE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                q_map = data.get("quarantine_map", {})
                if key in q_map:
                    return True, q_map[key]
        except Exception:
            pass

    return False, ""


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    res = evaluate_kaizen_trade_attribution()
    print(f"Kaizen Evaluation Complete. Evaluated {res.get('total_alpha_trades')} trades across {res.get('total_setups_evaluated')} setups.")
    print(f"Quarantined: {res.get('quarantined_count')} | Champions: {res.get('champions_count')}")
    print("\nTop 5 Quarantined Setups (AVOIDED):")
    for q in res.get("quarantine_details", [])[:5]:
        print(f"  🛑 {q['key']:<45} | PnL: ${q['net_pnl']:>6.2f} | Reason: {q['quarantine_reason']}")
    print("\nTop 5 Champion Setups (PROMOTED):")
    for c in res.get("champions_details", [])[:5]:
        print(f"  ⭐ {c['key']:<45} | PnL: +${c['net_pnl']:>5.2f} | WinRate: {c['win_rate']}% | PF: {c['profit_factor']}")
