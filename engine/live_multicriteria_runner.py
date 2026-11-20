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
TRAIL_STATE_FILE = os.path.join(AUDIT_DIR, "mt5_multicriteria_trailing_state.json")

from engine.mt5_multicriteria_trader import (
    generate_setup_magic,
    calculate_position_lot_size,
    evaluate_live_setup_signal
)
from engine.kaizen_self_improvement import check_kaizen_quarantine
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

# Maximum latency allowed after bar closure before considering signal stale (seconds)
MAX_LATENCY_SECONDS = {
    "5M": 25,
    "15M": 45,
    "1H": 90,
    "4H": 180,
}

MAX_SPREAD_RATIO = 0.12  # Peer-reviewed friction threshold for immediate Market Deals (12% of stop distance)
MAX_LIMIT_SPREAD_RATIO = 0.30  # Maximum spread threshold to accept as Resting Limit Order with Auto-Expiry (30% of stop distance)
LIMIT_ORDER_EXPIRATIONS = {
    "5M": 900,    # 15 minutes (3 bars)
    "15M": 1800,  # 30 minutes (2 bars)
    "1H": 3600,   # 60 minutes (1 bar)
    "4H": 7200,   # 120 minutes (0.5 bar)
}


def is_in_rollover_window(dt: Optional[datetime] = None) -> bool:
    """Checks if current UTC time falls within the daily broker rollover window (21:55-22:15 UTC).
    Brokers widen spreads significantly during this 20-minute gap.
    """
    if dt is None:
        dt = datetime.now(timezone.utc)
    minutes_of_day = dt.hour * 60 + dt.minute
    return 1315 <= minutes_of_day <= 1335


def is_spread_acceptable(spread: float, risk_dist: float, max_ratio: float = MAX_SPREAD_RATIO) -> bool:
    """Verifies that bid-ask spread is within allowable risk friction tolerance for immediate market fill."""
    if risk_dist <= 0:
        return False
    return spread <= (max_ratio * risk_dist)


def classify_spread_execution_mode(
    spread: float,
    risk_dist: float,
    max_market_ratio: float = MAX_SPREAD_RATIO,
    max_limit_ratio: float = MAX_LIMIT_SPREAD_RATIO
) -> str:
    """Classifies execution mode based on current bid-ask spread vs risk distance:
    - 'MARKET': spread <= max_market_ratio * risk_dist (Liquid, punch immediately at market)
    - 'LIMIT': max_market_ratio * risk_dist < spread <= max_limit_ratio * risk_dist (Mild spread elevation, place resting limit with auto-expiry)
    - 'REJECT': spread > max_limit_ratio * risk_dist (Extreme spread blowout / liquidity vacuum, discard trade)
    """
    if risk_dist <= 0:
        return "REJECT"
    if spread <= (max_market_ratio * risk_dist):
        return "MARKET"
    elif spread <= (max_limit_ratio * risk_dist):
        return "LIMIT"
    else:
        return "REJECT"


def calculate_trailing_stop_update(
    side: int,
    entry_price: float,
    current_sl: float,
    mfe_price: float,
    risk_dist: float,
    exit_mode: str,
    digits: int = 5
) -> Optional[float]:
    """Calculates updated trailing SL matching backtest ratchet mechanics exactly.
    - At MFE >= 1.0R: moves SL to Breakeven + 0.05R.
    - At MFE >= 1.5R: trails SL by trail_step behind MFE.
    Returns new SL rounded to broker digits if tightening by >= 0.05R, else None.
    """
    if risk_dist <= 0 or "trail" not in exit_mode:
        return None

    trail_step = 0.5 if "0.5" in exit_mode else (1.0 if "1.0" in exit_mode else 1.5)

    if side == 1:  # BUY
        pos_mfe = (mfe_price - entry_price) / risk_dist
        if pos_mfe < 1.0:
            return None

        target_sl = current_sl
        if pos_mfe >= 1.0:
            be_level = entry_price + (0.05 * risk_dist)
            if be_level > target_sl:
                target_sl = be_level

        if pos_mfe >= 1.5:
            trail_level = entry_price + ((pos_mfe - trail_step) * risk_dist)
            if trail_level > target_sl:
                target_sl = trail_level

        if target_sl > current_sl + (0.05 * risk_dist):
            return round(target_sl, digits)

    elif side == -1:  # SELL
        pos_mfe = (entry_price - mfe_price) / risk_dist
        if pos_mfe < 1.0:
            return None

        target_sl = current_sl
        if pos_mfe >= 1.0:
            be_level = entry_price - (0.05 * risk_dist)
            if target_sl == 0.0 or be_level < target_sl:
                target_sl = be_level

        if pos_mfe >= 1.5:
            trail_level = entry_price - ((pos_mfe - trail_step) * risk_dist)
            if target_sl == 0.0 or trail_level < target_sl:
                target_sl = trail_level

        if current_sl == 0.0 or target_sl < current_sl - (0.05 * risk_dist):
            return round(target_sl, digits)

    return None


class MT5MultiCriteriaLiveRunner:
    def __init__(self, risk_mode: str = "min_lot"):
        self.risk_mode = risk_mode
        self.basket = ROBUST_ALPHA_BASKET
        self.last_candle_times: Dict[str, int] = {}
        self.is_running = False
        self._init_ledger()
        self.trailing_state: Dict[str, Any] = self._load_trailing_state()

    def _load_trailing_state(self) -> Dict[str, Any]:
        if os.path.exists(TRAIL_STATE_FILE):
            try:
                with open(TRAIL_STATE_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_trailing_state(self):
        try:
            with open(TRAIL_STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(self.trailing_state, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving trailing state: {e}")

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

    def prime_candle_times(self):
        """Primes the last candle timestamps on startup so no historical stale bars are punched."""
        logger.info("⏳ Priming candle timestamps for all setups to prevent stale boot executions...")
        count = 0
        for sym, setups in self.basket.items():
            for s in setups:
                tf = s["tf"]
                key = f"{sym}_{tf}"
                if key in self.last_candle_times:
                    continue
                df = self.fetch_rates_dataframe(sym, tf, count=60)
                if df is not None and len(df) >= 2:
                    self.last_candle_times[key] = int(df["time"].iloc[-2].timestamp())
                    count += 1
        logger.info(f"✅ Primed {count} setups. Engine will only punch upon FRESH confirmed candle closures.")

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
        forming_bar_open_time = int(df["time"].iloc[-1].timestamp())
        last_seen = self.last_candle_times.get(key, 0)

        # Only evaluate on a FRESH confirmed candle close
        if closed_bar_time <= last_seen:
            return

        # Bar -2 closed the exact second Bar -1 opened.
        # Elapsed seconds since Bar -2 closed is: current_tick_time - forming_bar_open_time.
        tick = mt5.symbol_info_tick(symbol)
        if tick is None or tick.time == 0:
            return

        elapsed_since_close = tick.time - forming_bar_open_time
        max_latency = MAX_LATENCY_SECONDS.get(tf, 30)

        # If elapsed time exceeds max allowed latency, this bar closed in the past
        if elapsed_since_close > max_latency:
            self.last_candle_times[key] = closed_bar_time
            return

        self.last_candle_times[key] = closed_bar_time
        logger.info(f"🔔 [FRESH BAR CLOSURE] {symbol} {tf} | Elapsed since close: {elapsed_since_close}s | Evaluating setup...")

        # Evaluate signal on the confirmed closed candle
        sig = evaluate_live_setup_signal(df, symbol, tf, model, param)
        side = sig.get("side", 0)
        if side == 0:
            return

        setup_name = sig.get("setup", f"{model}_{tf}")

        # Kaizen Dynamic Quarantine Guard: Check if setup is currently quarantined due to negative expectancy
        is_quarantined, q_reason = check_kaizen_quarantine(symbol, tf, setup_name)
        if is_quarantined:
            logger.warning(f"🛑 [KAIZEN AVOIDANCE] {symbol} {tf} {setup_name} is QUARANTINED: {q_reason}. Skipping trade.")
            return

        magic = generate_setup_magic(symbol, tf, setup_name)

        # Check existing positions with this setup's magic number
        open_pos = mt5.positions_get(symbol=symbol)
        if open_pos:
            for p in open_pos:
                if p.magic == magic:
                    logger.info(f"⏭️ [ALREADY OPEN] {symbol} {tf} Magic: {magic} already active.")
                    return

        # Check existing pending limit orders with this setup's magic number
        open_orders = mt5.orders_get(symbol=symbol)
        if open_orders:
            for o in open_orders:
                if o.magic == magic:
                    logger.info(f"⏭️ [ALREADY PENDING] {symbol} {tf} Magic: {magic} pending order already active.")
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
        is_trail = "trail" in exit_mode
        if is_trail:
            target_rr = 6.0  # Allow trailing stop to manage exit
        elif "4.0" in exit_mode:
            target_rr = 4.0
        elif "3.5" in exit_mode:
            target_rr = 3.5
        elif "3.0" in exit_mode:
            target_rr = 3.0
        elif "2.5" in exit_mode:
            target_rr = 2.5
        elif "2.0" in exit_mode:
            target_rr = 2.0
        elif "1.5" in exit_mode:
            target_rr = 1.5
        else:
            target_rr = 2.5

        # 1. Rollover Guard (Pause new entries during daily 21:55-22:15 UTC bank rollover)
        if is_in_rollover_window():
            logger.info(f"⏳ [ROLLOVER GUARD] Pausing new entries during daily rollover window (21:55-22:15 UTC).")
            return

        # 2. Pre-Trade Two-Tier Spread Classification (Market vs. Resting Limit with Auto-Expiry)
        spread = tick.ask - tick.bid
        exec_mode = classify_spread_execution_mode(spread, risk_dist, MAX_SPREAD_RATIO, MAX_LIMIT_SPREAD_RATIO)

        if exec_mode == "REJECT":
            max_limit_spread = MAX_LIMIT_SPREAD_RATIO * risk_dist
            logger.warning(
                f"❌ [SPREAD FILTER] {symbol} spread={spread:.5f} > limit ceiling {max_limit_spread:.5f} ({MAX_LIMIT_SPREAD_RATIO:.2f}R). "
                f"Rejecting trade due to extreme liquidity blowout."
            )
            return

        digits = info.digits

        if exec_mode == "MARKET":
            order_action = mt5.TRADE_ACTION_DEAL
            order_type = mt5.ORDER_TYPE_BUY if side == 1 else mt5.ORDER_TYPE_SELL
            entry_price = tick.ask if side == 1 else tick.bid
            type_time = mt5.ORDER_TIME_GTC
            expiration_time = 0
            order_comment = f"Alpha_{setup_name[:12]}"
        else:  # LIMIT mode
            order_action = mt5.TRADE_ACTION_PENDING
            order_type = mt5.ORDER_TYPE_BUY_LIMIT if side == 1 else mt5.ORDER_TYPE_SELL_LIMIT
            candle_close = float(df["close"].iloc[-2]) if len(df) >= 2 else (tick.bid if side == 1 else tick.ask)

            if side == 1:
                # Buy Limit rests at candle close or bid, capped at current ask
                entry_price = min(candle_close, tick.bid)
                if entry_price >= tick.ask:
                    entry_price = tick.bid
            else:
                # Sell Limit rests at candle close or ask, capped at current bid
                entry_price = max(candle_close, tick.ask)
                if entry_price <= tick.bid:
                    entry_price = tick.ask

            expiry_sec = LIMIT_ORDER_EXPIRATIONS.get(tf, 900)
            type_time = mt5.ORDER_TIME_SPECIFIED
            expiration_time = int(time.time()) + expiry_sec
            order_comment = f"Alpha_{setup_name[:8]}_Lmt"

        # 3. Pre-Trade Margin & Solvency Filter
        account = mt5.account_info()
        if not account:
            return

        required_margin = mt5.order_calc_margin(order_type, symbol, volume, entry_price)
        if required_margin is not None:
            if required_margin > 0.50 * account.margin_free or (account.margin_free - required_margin) < 100.0:
                logger.warning(
                    f"❌ [MARGIN FILTER] {symbol} requires ${required_margin:.2f} margin, "
                    f"but free margin is ${account.margin_free:.2f}. Rejecting trade to preserve solvency."
                )
                return

        if side == 1:
            sl = entry_price - risk_dist
            tp = entry_price + (target_rr * risk_dist)
        else:
            sl = entry_price + risk_dist
            tp = entry_price - (target_rr * risk_dist)

        # Round prices to broker precision
        entry_price = round(entry_price, digits)
        sl = round(sl, digits)
        tp = round(tp, digits)

        filling = self.get_filling_mode(info)

        req = {
            "action": order_action,
            "symbol": symbol,
            "volume": volume,
            "type": order_type,
            "price": entry_price,
            "sl": sl,
            "tp": tp,
            "deviation": 25,
            "magic": magic,
            "comment": order_comment,
            "type_time": type_time,
            "type_filling": filling,
        }
        if type_time == mt5.ORDER_TIME_SPECIFIED:
            req["expiration"] = expiration_time

        if exec_mode == "MARKET":
            logger.info(
                f"⚡ [AUTO-PUNCH MARKET] {action_str} {symbol} | TF: {tf} | Setup: {setup_name} | "
                f"Lot: {volume} | Price: {entry_price} | SL: {sl} | TP: {tp} | Spread: {spread:.5f} ({spread/risk_dist:.2f}R) | Magic: {magic}"
            )
        else:
            logger.info(
                f"⏳ [AUTO-PUNCH LIMIT] {action_str}_LIMIT {symbol} | TF: {tf} | Setup: {setup_name} | "
                f"Lot: {volume} | LimitPrice: {entry_price} | SL: {sl} | TP: {tp} | Spread: {spread:.5f} ({spread/risk_dist:.2f}R > {MAX_SPREAD_RATIO:.2f}R) | "
                f"Expiry: {expiry_sec // 60}m | Magic: {magic}"
            )

        res = mt5.order_send(req)
        if res and res.retcode == mt5.TRADE_RETCODE_DONE:
            ticket = res.order
            fill_p = res.price if res.price > 0 else entry_price
            if exec_mode == "MARKET":
                logger.info(f"✅ [MARKET ORDER FILLED] Ticket #{ticket} {action_str} {symbol} @ {fill_p}")
            else:
                logger.info(f"✅ [LIMIT ORDER PLACED] Ticket #{ticket} {action_str}_LIMIT {symbol} @ {entry_price} (Expires in {expiry_sec // 60}m)")

            self.log_trade_to_ledger({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "ticket": ticket,
                "symbol": symbol,
                "tf": tf,
                "model": model,
                "setup": setup_name,
                "action": f"{action_str}_LIMIT" if exec_mode == "LIMIT" else action_str,
                "volume": volume,
                "price": entry_price,
                "sl": sl,
                "tp": tp,
                "magic": magic,
                "exit_mode": exit_mode,
                "status": "PENDING_LIMIT" if exec_mode == "LIMIT" else "FILLED"
            })
        else:
            err = res.comment if res else str(mt5.last_error())
            retcode = res.retcode if res else "NONE"
            logger.warning(f"❌ [ORDER REJECTED] {symbol} {action_str} retcode={retcode}: {err}")

    def update_health_telemetry(self):
        try:
            account = mt5.account_info()
            positions = mt5.positions_get()
            orders = mt5.orders_get()
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

            order_list = []
            if orders:
                for o in orders:
                    order_list.append({
                        "ticket": o.ticket,
                        "symbol": o.symbol,
                        "type": "BUY_LIMIT" if o.type == mt5.ORDER_TYPE_BUY_LIMIT else ("SELL_LIMIT" if o.type == mt5.ORDER_TYPE_SELL_LIMIT else str(o.type)),
                        "volume": o.volume_current,
                        "price_open": o.price_open,
                        "sl": o.sl,
                        "tp": o.tp,
                        "magic": o.magic,
                        "comment": o.comment,
                    })

            health = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "status": "ONLINE",
                "login": account.login if account else None,
                "equity": account.equity if account else 0.0,
                "balance": account.balance if account else 0.0,
                "margin_free": account.margin_free if account else 0.0,
                "open_positions_count": len(pos_list),
                "pending_orders_count": len(order_list),
                "active_symbols_tracked": len(self.basket),
                "total_setups_tracked": sum(len(v) for v in self.basket.values())
            }

            with open(HEALTH_FILE, "w", encoding="utf-8") as f:
                json.dump(health, f, indent=2)

            with open(POSITIONS_FILE, "w", encoding="utf-8") as f:
                json.dump(pos_list, f, indent=2)

            pending_file = os.path.join(AUDIT_DIR, "mt5_multicriteria_pending_orders.json")
            with open(pending_file, "w", encoding="utf-8") as f:
                json.dump(order_list, f, indent=2)

        except Exception as e:
            logger.error(f"Error updating health telemetry: {e}")

    def manage_trailing_stops(self):
        """Monitors open MT5 positions, tracks MFE, and ratchets trailing SL to breakeven (+0.05R)
        at +1.0R and trails behind MFE at >= 1.5R with 100% backtest fidelity."""
        try:
            positions = mt5.positions_get()
            if not positions:
                if self.trailing_state:
                    self.trailing_state.clear()
                    self._save_trailing_state()
                return

            open_tickets = {p.ticket for p in positions}
            # Clean up closed tickets
            for t in list(self.trailing_state.keys()):
                if int(t) not in open_tickets:
                    del self.trailing_state[t]

            # Index ledger for initial entry price, SL, and exit mode
            ledger_lookup = {}
            if os.path.exists(LEDGER_FILE):
                try:
                    df_led = pd.read_csv(LEDGER_FILE)
                    for _, row in df_led.iterrows():
                        ledger_lookup[int(row["ticket"])] = row
                except Exception:
                    pass

            for p in positions:
                ticket_str = str(p.ticket)
                side = 1 if p.type == mt5.POSITION_TYPE_BUY else -1
                symbol = p.symbol

                row = ledger_lookup.get(p.ticket)
                if row is not None:
                    exit_mode = str(row.get("exit_mode", ""))
                    initial_entry = float(row.get("price", p.price_open))
                    initial_sl = float(row.get("sl", p.sl))
                    risk_dist = abs(initial_entry - initial_sl)
                elif p.comment and p.comment.startswith("Alpha_"):
                    exit_mode = "trail_0.5r"
                    initial_entry = p.price_open
                    risk_dist = abs(p.price_open - p.sl) if p.sl > 0 else 0.0
                else:
                    continue

                if "trail" not in exit_mode or risk_dist <= 0:
                    continue

                info = mt5.symbol_info(symbol)
                if not info:
                    continue
                digits = info.digits
                tick = mt5.symbol_info_tick(symbol)
                if not tick:
                    continue

                state = self.trailing_state.get(ticket_str, {
                    "ticket": p.ticket,
                    "symbol": symbol,
                    "side": side,
                    "entry_price": initial_entry,
                    "risk_dist": risk_dist,
                    "exit_mode": exit_mode,
                    "mfe_price": initial_entry
                })

                if side == 1:
                    cur_price = max(tick.bid, p.price_current)
                    state["mfe_price"] = max(state.get("mfe_price", initial_entry), cur_price)
                else:
                    cur_price = min(tick.ask, p.price_current)
                    state["mfe_price"] = min(state.get("mfe_price", initial_entry), cur_price)

                self.trailing_state[ticket_str] = state

                new_sl = calculate_trailing_stop_update(
                    side=side,
                    entry_price=initial_entry,
                    current_sl=p.sl,
                    mfe_price=state["mfe_price"],
                    risk_dist=risk_dist,
                    exit_mode=exit_mode,
                    digits=digits
                )

                if new_sl is not None:
                    # Guard against invalid stop placement
                    if side == 1 and new_sl >= tick.bid:
                        continue
                    if side == -1 and new_sl <= tick.ask:
                        continue

                    mfe_r = (state["mfe_price"] - initial_entry) / risk_dist if side == 1 else (initial_entry - state["mfe_price"]) / risk_dist
                    logger.info(
                        f"🎯 [TRAILING RATCHET] Ticket #{p.ticket} {symbol} MFE={mfe_r:.2f}R | "
                        f"Updating SL: {p.sl} -> {new_sl} (TP: {p.tp})"
                    )

                    req = {
                        "action": mt5.TRADE_ACTION_SLTP,
                        "position": p.ticket,
                        "symbol": symbol,
                        "sl": new_sl,
                        "tp": p.tp,
                    }
                    res = mt5.order_send(req)
                    if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                        logger.info(f"✅ [TRAILING STOP SUCCESS] Ticket #{p.ticket} SL moved to {new_sl}")
                    else:
                        err = res.comment if res else str(mt5.last_error())
                        logger.warning(f"❌ [TRAILING STOP FAILED] Ticket #{p.ticket} retcode={getattr(res, 'retcode', 'NONE')}: {err}")

            self._save_trailing_state()
        except Exception as e:
            logger.error(f"Error in manage_trailing_stops: {e}")

    def run_cycle(self):
        """Executes a single monitoring cycle across all symbols and setups in the basket."""
        self.manage_trailing_stops()

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
        self.prime_candle_times()

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
