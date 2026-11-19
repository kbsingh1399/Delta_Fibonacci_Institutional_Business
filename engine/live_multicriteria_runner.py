#!/usr/bin/env python3
"""engine/live_multicriteria_runner.py: Real-time autonomous multi-criteria MT5 live trader.
Subscribes to all verified Institutional Alpha setups from audit/EXPANDED_OPTIMAL_ALPHA_BASKET.py.
Monitors candle closes across 5M, 15M, 1H, 4H in real-time.
Punches trades at the minimum lot size as soon as rejection criteria confirm at candle close.
Features independent 8-digit magic numbers for concurrent position stacking without caps.
"""

import os
import sys
import time
import json
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
import numpy as np
import pandas as pd
import MetaTrader5 as mt5

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

AUDIT_DIR = os.path.join(BASE_DIR, "audit")
os.makedirs(AUDIT_DIR, exist_ok=True)

LEDGER_FILE = os.path.join(AUDIT_DIR, "mt5_multicriteria_live_ledger.csv")
HEALTH_FILE = os.path.join(AUDIT_DIR, "mt5_multicriteria_health.json")
POSITIONS_FILE = os.path.join(AUDIT_DIR, "mt5_multicriteria_positions.json")

from engine.mt5_multicriteria_trader import (
    generate_setup_magic,
    calculate_position_lot_size,
    evaluate_live_setup_signal
)
from audit.EXPANDED_OPTIMAL_ALPHA_BASKET import ROBUST_ALPHA_BASKET

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(os.path.join(AUDIT_DIR, "mt5_multicriteria_live.log"), encoding="utf-8")
    ]
)
logger = logging.getLogger("MultiCriteriaTrader")

TIMEFRAME_MAP = {
    "5M": mt5.TIMEFRAME_M5,
    "15M": mt5.TIMEFRAME_M15,
    "1H": mt5.TIMEFRAME_H1,
    "4H": mt5.TIMEFRAME_H4,
}

TIMEFRAME_SECONDS = {
    "5M": 300,
    "15M": 900,
    "1H": 3600,
    "4H": 14400,
}


class MT5MultiCriteriaLiveRunner:
    def __init__(self, risk_mode: str = "min_lot"):
        self.risk_mode = risk_mode
        self.basket = ROBUST_ALPHA_BASKET
        self.last_candle_times: Dict[str, int] = {}
        self.is_running = False
        self._init_ledger()

    def _init_ledger(self):
        if not os.path.exists(LEDGER_FILE):
            df_init = pd.DataFrame(columns=[
                "timestamp", "ticket", "symbol", "tf", "model", "setup",
                "action", "volume", "price", "sl", "tp", "magic", "exit_mode", "status"
            ])
            df_init.to_csv(LEDGER_FILE, index=False)

    def log_trade_to_ledger(self, entry: Dict[str, Any]):
        try:
            df = pd.DataFrame([entry])
            df.to_csv(LEDGER_FILE, mode="a", header=not os.path.exists(LEDGER_FILE), index=False)
        except Exception as e:
            logger.error(f"Error logging to ledger: {e}")

    def connect_mt5(self) -> bool:
        if not mt5.initialize():
            logger.error(f"MT5 initialize failed: {mt5.last_error()}")
            return False

        account = mt5.account_info()
        if not account:
            logger.error("Failed to retrieve MT5 account info")
            return False

        logger.info(
            f"✅ [MT5 CONNECTED] Login: {account.login} | Server: {account.server} | "
            f"Balance: ${account.balance:,.2f} | Equity: ${account.equity:,.2f}"
        )
        return True

    def fetch_rates_dataframe(self, symbol: str, tf_str: str, count: int = 100) -> Optional[pd.DataFrame]:
        tf_enum = TIMEFRAME_MAP.get(tf_str)
        if tf_enum is None:
            return None

        rates = mt5.copy_rates_from_pos(symbol, tf_enum, 0, count)
        if rates is None or len(rates) < 50:
            return None

        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        return df

    def get_filling_mode(self, info) -> int:
        if info.filling_mode & 2:
            return mt5.ORDER_FILLING_IOC
        elif info.filling_mode & 1:
            return mt5.ORDER_FILLING_FOK
        else:
            return mt5.ORDER_FILLING_RETURN

    def check_and_execute_setup(self, symbol: str, setup_cfg: Dict[str, Any]):
        tf = setup_cfg["tf"]
        model = setup_cfg["model"]
        param = setup_cfg["param"]
        exit_mode = setup_cfg["exit_mode"]

        key = f"{symbol}_{tf}"
        df = self.fetch_rates_dataframe(symbol, tf, count=80)
        if df is None:
            return

        # Latest completed closed bar is index -2 (bar -1 is currently forming)
        closed_bar_time = int(df["time"].iloc[-2].timestamp())
        last_seen = self.last_candle_times.get(key, 0)

        # Only evaluate on a FRESH confirmed candle close
        if closed_bar_time <= last_seen:
            return

        self.last_candle_times[key] = closed_bar_time

        # Evaluate signal on the confirmed closed candle
        sig = evaluate_live_setup_signal(df, symbol, tf, model, param)
        side = sig.get("side", 0)
        if side == 0:
            return

        setup_name = sig.get("setup", f"{model}_{tf}")
        magic = generate_setup_magic(symbol, tf, setup_name)

        # Check existing positions with this setup's magic number
        open_pos = mt5.positions_get(symbol=symbol)
        if open_pos:
            for p in open_pos:
                if p.magic == magic:
                    logger.info(f"⏭️ [ALREADY OPEN] {symbol} {tf} Magic: {magic} already active.")
                    return

        # Prepare order execution
        info = mt5.symbol_info(symbol)
        if not info or not info.visible:
            mt5.symbol_select(symbol, True)
            info = mt5.symbol_info(symbol)
            if not info:
                logger.warning(f"Failed to get symbol info for {symbol}")
                return

        tick = mt5.symbol_info_tick(symbol)
        if not tick:
            return

        action_str = "BUY" if side == 1 else "SELL"
        entry_price = tick.ask if side == 1 else tick.bid

        # Minimum lot size as per user requirement
        volume = float(info.volume_min)

        # Calculate SL and TP
        risk_dist = sig.get("risk_dist", 0.0)
        if risk_dist <= 0:
            # Fallback 1.5 ATR
            h = df["high"].to_numpy()
            l = df["low"].to_numpy()
            risk_dist = float(np.mean(h[-15:-1] - l[-15:-1])) * 1.5

        # Determine target RR from exit_mode
        target_rr = 2.5
        if "4.0" in exit_mode:
            target_rr = 4.0
        elif "3.5" in exit_mode:
            target_rr = 3.5
        elif "3.0" in exit_mode:
            target_rr = 3.0
        elif "2.0" in exit_mode or "trail" in exit_mode:
            target_rr = 2.5

        if side == 1:
            sl = entry_price - risk_dist
            tp = entry_price + (target_rr * risk_dist)
        else:
            sl = entry_price + risk_dist
            tp = entry_price - (target_rr * risk_dist)

        # Round prices to broker precision
        digits = info.digits
        entry_price = round(entry_price, digits)
        sl = round(sl, digits)
        tp = round(tp, digits)

        filling = self.get_filling_mode(info)

        req = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": volume,
            "type": mt5.ORDER_TYPE_BUY if side == 1 else mt5.ORDER_TYPE_SELL,
            "price": entry_price,
            "sl": sl,
            "tp": tp,
            "deviation": 25,
            "magic": magic,
            "comment": f"Alpha_{setup_name[:12]}",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": filling,
        }

        logger.info(
            f"⚡ [AUTO-PUNCH TRIGGER] {action_str} {symbol} | TF: {tf} | Setup: {setup_name} | "
            f"Lot: {volume} | Price: {entry_price} | SL: {sl} | TP: {tp} | Magic: {magic}"
        )

        res = mt5.order_send(req)
        if res and res.retcode == mt5.TRADE_RETCODE_DONE:
            ticket = res.order
            fill_p = res.price if res.price > 0 else entry_price
            logger.info(f"✅ [AUTO-PUNCH FILLED] Ticket #{ticket} {action_str} {symbol} @ {fill_p}")

            self.log_trade_to_ledger({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "ticket": ticket,
                "symbol": symbol,
                "tf": tf,
                "model": model,
                "setup": setup_name,
                "action": action_str,
                "volume": volume,
                "price": fill_p,
                "sl": sl,
                "tp": tp,
                "magic": magic,
                "exit_mode": exit_mode,
                "status": "FILLED"
            })
        else:
            err = res.comment if res else str(mt5.last_error())
            retcode = res.retcode if res else "NONE"
            logger.warning(f"❌ [ORDER REJECTED] {symbol} {action_str} retcode={retcode}: {err}")

    def update_health_telemetry(self):
        try:
            account = mt5.account_info()
            positions = mt5.positions_get()
            pos_list = []
            if positions:
                for p in positions:
                    pos_list.append({
                        "ticket": p.ticket,
                        "symbol": p.symbol,
                        "type": "BUY" if p.type == mt5.POSITION_TYPE_BUY else "SELL",
                        "volume": p.volume,
                        "price_open": p.price_open,
                        "sl": p.sl,
                        "tp": p.tp,
                        "profit": p.profit,
                        "magic": p.magic,
                        "comment": p.comment,
                    })

            health = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "status": "ONLINE",
                "login": account.login if account else None,
                "equity": account.equity if account else 0.0,
                "balance": account.balance if account else 0.0,
                "margin_free": account.margin_free if account else 0.0,
                "open_positions_count": len(pos_list),
                "active_symbols_tracked": len(self.basket),
                "total_setups_tracked": sum(len(v) for v in self.basket.values())
            }

            with open(HEALTH_FILE, "w", encoding="utf-8") as f:
                json.dump(health, f, indent=2)

            with open(POSITIONS_FILE, "w", encoding="utf-8") as f:
                json.dump(pos_list, f, indent=2)

        except Exception as e:
            logger.error(f"Error updating health telemetry: {e}")

    def run_cycle(self):
        """Executes a single monitoring cycle across all symbols and setups in the basket."""
        for sym, setups in self.basket.items():
            for setup_cfg in setups:
                try:
                    self.check_and_execute_setup(sym, setup_cfg)
                except Exception as e:
                    logger.error(f"Error checking {sym} {setup_cfg.get('tf')}: {e}")

        self.update_health_telemetry()

    def run_daemon(self, poll_interval_sec: float = 5.0):
        """Continuous live execution daemon."""
        if not self.connect_mt5():
            logger.error("Could not connect to MT5. Exiting.")
            return

        self.is_running = True
        logger.info(
            f"🚀 [MULTI-CRITERIA TRADER STARTED] Tracking {len(self.basket)} symbols and "
            f"{sum(len(v) for v in self.basket.values())} Institutional Alpha setups."
        )

        try:
            while self.is_running:
                self.run_cycle()
                time.sleep(poll_interval_sec)
        except KeyboardInterrupt:
            logger.info("Stopping MultiCriteria trader on keyboard interrupt...")
        finally:
            mt5.shutdown()
            logger.info("MT5 connection closed.")


if __name__ == "__main__":
    runner = MT5MultiCriteriaLiveRunner(risk_mode="min_lot")
    runner.run_daemon(poll_interval_sec=5.0)
