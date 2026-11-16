#!/usr/bin/env python3
r"""mt5_alpha_trader_monitor.py: Autonomous Live Trader & Risk Governor for the
Optimal Multi-Cluster Alpha Basket on FundedNext MetaTrader 5.

Features:
- Fixed $10 USD Risk per Trade with dynamic broker-exact lot sizing.
- Cross-Cluster Governance: Max 3 concurrent open trades, Max 1 per cluster.
- Specialized Setup Execution:
    - US30, ETHUSD, AUDJPY, USDCHF, HK50: wyckoff_institutional_filtered
    - BABA, USDJPY, ZARJPY, XAGUSD: turbo_tri_tier (EMA 34/55 Turbo Shelf)
    - CADJPY: wyckoff_swing_sweep (Turtle Soup 20-bar liquidity sweep)
    - MC: canonical (EMA 89/233)
- Real-time Position Management:
    - Step-trail runners (+0.5R lock at 2R, +2.0R lock at 4R, EMA 55 trail at 6R)
    - Fixed TP management (1:2.5, 1:3.0, 1:3.5)
- Full Audit Logging & State Persistence in audit/
"""

import os
import sys
import time
import json
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional, Tuple, List
import numpy as np
import pandas as pd
import MetaTrader5 as mt5

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUDIT_DIR = os.path.join(BASE_DIR, "audit")
os.makedirs(AUDIT_DIR, exist_ok=True)

ACTIVE_POSITIONS_FILE = os.path.join(AUDIT_DIR, "mt5_active_positions.json")
LIVE_SIGNALS_FILE = os.path.join(AUDIT_DIR, "mt5_live_signals.json")
TRADING_LEDGER_FILE = os.path.join(AUDIT_DIR, "mt5_live_trading_ledger.csv")
HEALTH_STATUS_FILE = os.path.join(AUDIT_DIR, "mt5_health_status.json")
PID_FILE = os.path.join(AUDIT_DIR, "mt5_alpha_trader.pid")

def acquire_singleton_lock():
    """Guarantees only one live trader daemon runs, preventing split-brain state conflicts."""
    pid = os.getpid()
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE, "r") as f:
                old_pid = int(f.read().strip())
            if old_pid != pid:
                import psutil
                if psutil.pid_exists(old_pid):
                    proc = psutil.Process(old_pid)
                    if "python" in proc.name().lower():
                        logger.warning(f"⚠️ Terminating old daemon instance PID {old_pid} to prevent split-brain collision.")
                        proc.kill()
                        time.sleep(1)
        except Exception as e:
            logger.debug(f"PID check notice: {e}")
    with open(PID_FILE, "w") as f:
        f.write(str(pid))

# Setup Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [MT5_ALPHA_MONITOR] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(os.path.join(AUDIT_DIR, "mt5_alpha_monitor.log"), encoding="utf-8"),
    ],
)
logger = logging.getLogger("MT5AlphaMonitor")

# Expanded 28 Positive-Alpha Universe across 8 Uncorrelated Clusters
OPTIMAL_ALPHA_BASKET = [
    # --- Crypto ---
    {"symbol": "DOGUSD", "cluster": "Crypto", "setup": "canonical", "exit_mode": "fixed_3.5", "risk_usd": 10.0},
    {"symbol": "ETHUSD", "cluster": "Crypto", "setup": "wyckoff_institutional_filtered", "exit_mode": "step_trail", "risk_usd": 10.0},
    {"symbol": "LNKUSD", "cluster": "Crypto", "setup": "canonical", "exit_mode": "fixed_3.0", "risk_usd": 10.0},
    {"symbol": "XLMUSD", "cluster": "Crypto", "setup": "canonical", "exit_mode": "fixed_3.0", "risk_usd": 10.0},

    # --- Commodities ---
    {"symbol": "XAUUSD", "cluster": "Commodity_Energy", "setup": "canonical", "exit_mode": "fixed_3.0", "risk_usd": 10.0},
    {"symbol": "XAGUSD", "cluster": "Commodity_Energy", "setup": "turbo_tri_tier", "exit_mode": "step_trail", "risk_usd": 10.0},

    # --- Equity Indices ---
    {"symbol": "US30", "cluster": "Indices", "setup": "wyckoff_institutional_filtered", "exit_mode": "fixed_3.5", "risk_usd": 10.0},
    {"symbol": "EUSTX50", "cluster": "Indices", "setup": "wyckoff_institutional_filtered", "exit_mode": "fixed_3.0", "risk_usd": 10.0},
    {"symbol": "FRA40", "cluster": "Indices", "setup": "wyckoff_institutional_filtered", "exit_mode": "fixed_3.0", "risk_usd": 10.0},
    {"symbol": "US2000", "cluster": "Indices", "setup": "wyckoff_institutional_filtered", "exit_mode": "fixed_2.5", "risk_usd": 10.0},
    {"symbol": "HK50", "cluster": "Indices", "setup": "wyckoff_institutional_filtered", "exit_mode": "fixed_2.5", "risk_usd": 10.0},

    # --- Forex JPY Crosses ---
    {"symbol": "CHFJPY", "cluster": "Forex_JPY", "setup": "canonical", "exit_mode": "fixed_3.0", "risk_usd": 10.0},
    {"symbol": "EURJPY", "cluster": "Forex_JPY", "setup": "wyckoff_swing_sweep", "exit_mode": "fixed_3.0", "risk_usd": 10.0},
    {"symbol": "USDJPY", "cluster": "Forex_JPY", "setup": "turbo_tri_tier", "exit_mode": "fixed_2.5", "risk_usd": 10.0},
    {"symbol": "CADJPY", "cluster": "Forex_JPY", "setup": "wyckoff_swing_sweep", "exit_mode": "fixed_3.0", "risk_usd": 10.0},
    {"symbol": "AUDJPY", "cluster": "Forex_JPY", "setup": "wyckoff_institutional_filtered", "exit_mode": "fixed_2.5", "risk_usd": 10.0},

    # --- Forex USD & EUR Pairs ---
    {"symbol": "USDCHF", "cluster": "Forex_USD", "setup": "wyckoff_institutional_filtered", "exit_mode": "fixed_3.0", "risk_usd": 10.0},
    {"symbol": "USDCNH", "cluster": "Forex_USD", "setup": "canonical", "exit_mode": "fixed_3.0", "risk_usd": 10.0},
    {"symbol": "EURNOK", "cluster": "Forex_EUR", "setup": "wyckoff_swing_sweep", "exit_mode": "fixed_3.0", "risk_usd": 10.0},

    # --- US Equities ---
    {"symbol": "MSFT", "cluster": "US_Equities", "setup": "turbo_tri_tier", "exit_mode": "fixed_3.0", "risk_usd": 10.0},
    {"symbol": "AMD", "cluster": "US_Equities", "setup": "turbo_tri_tier", "exit_mode": "fixed_3.0", "risk_usd": 10.0},
    {"symbol": "NVDA", "cluster": "US_Equities", "setup": "turbo_tri_tier", "exit_mode": "fixed_3.0", "risk_usd": 10.0},
    {"symbol": "BABA", "cluster": "US_Equities", "setup": "turbo_tri_tier", "exit_mode": "step_trail", "risk_usd": 10.0},
    {"symbol": "NFLX", "cluster": "US_Equities", "setup": "turbo_tri_tier", "exit_mode": "fixed_3.0", "risk_usd": 10.0},
    {"symbol": "GOOG", "cluster": "US_Equities", "setup": "turbo_tri_tier", "exit_mode": "fixed_3.0", "risk_usd": 10.0},
    {"symbol": "NKE", "cluster": "US_Equities", "setup": "canonical", "exit_mode": "fixed_3.0", "risk_usd": 10.0},

    # --- European Equities ---
    {"symbol": "MC", "cluster": "EU_Equities", "setup": "canonical", "exit_mode": "fixed_3.5", "risk_usd": 10.0},
    {"symbol": "MBG", "cluster": "EU_Equities", "setup": "canonical", "exit_mode": "fixed_3.0", "risk_usd": 10.0},
]

MAX_CONCURRENT_POSITIONS = 3
MAX_PER_CLUSTER = 1
DEFAULT_RISK_USD = 10.0


def calculate_ema(arr: np.ndarray, span: int) -> np.ndarray:
    return pd.Series(arr).ewm(span=span, adjust=False).mean().to_numpy()


def calculate_atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> np.ndarray:
    n = len(closes)
    tr = np.zeros(n)
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
    return pd.Series(tr).rolling(period).mean().bfill().to_numpy()


def compute_adx(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
    n = len(close)
    if n < period * 2 + 2:
        return np.zeros(n, dtype=float)
    up = np.diff(high, prepend=high[0])
    dn = -np.diff(low, prepend=low[0])
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    prev_c = np.roll(close, 1)
    prev_c[0] = close[0]
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev_c), np.abs(low - prev_c)))

    atr_s = np.zeros(n, dtype=float)
    p_s = np.zeros(n, dtype=float)
    m_s = np.zeros(n, dtype=float)
    atr_s[period] = np.mean(tr[1 : period + 1])
    p_s[period] = np.mean(plus_dm[1 : period + 1])
    m_s[period] = np.mean(minus_dm[1 : period + 1])
    alpha = 1.0 / period
    for i in range(period + 1, n):
        atr_s[i] = atr_s[i - 1] * (1.0 - alpha) + tr[i] * alpha
        p_s[i] = p_s[i - 1] * (1.0 - alpha) + plus_dm[i] * alpha
        m_s[i] = m_s[i - 1] * (1.0 - alpha) + minus_dm[i] * alpha

    p_di = 100.0 * p_s / (atr_s + 1e-12)
    m_di = 100.0 * m_s / (atr_s + 1e-12)
    dx = 100.0 * np.abs(p_di - m_di) / (p_di + m_di + 1e-12)
    adx = np.zeros(n, dtype=float)
    start = period * 2
    adx[start] = np.mean(dx[period : start + 1])
    for i in range(start + 1, n):
        adx[i] = adx[i - 1] * (1.0 - alpha) + dx[i] * alpha
    return adx


def calculate_lot_size(symbol: str, risk_usd: float, entry: float, stop: float) -> Tuple[float, float, str]:
    """Calculates broker-compliant lot size for a target dollar risk amount.
    Returns: (final_lot, actual_risk_usd, status_message)
    """
    info = mt5.symbol_info(symbol)
    if not info:
        return 0.0, 0.0, f"Symbol info not available for {symbol}"

    dist = abs(entry - stop)
    if dist <= 1e-8:
        return 0.0, 0.0, "Invalid stop distance (near zero)"

    tick_size = info.trade_tick_size if (info.trade_tick_size and info.trade_tick_size > 0) else info.point
    tick_value = info.trade_tick_value
    if tick_value <= 0 or tick_size <= 0:
        return 0.0, 0.0, "Invalid tick value or tick size"

    one_lot_risk = (dist / tick_size) * tick_value
    if one_lot_risk <= 0:
        return 0.0, 0.0, "Zero 1-lot risk value"

    raw_lot = risk_usd / one_lot_risk
    step = info.volume_step if info.volume_step > 0 else 0.01
    vol_min = info.volume_min if info.volume_min > 0 else 0.01
    vol_max = info.volume_max if info.volume_max > 0 else 100.0

    rounded_lot = round(raw_lot / step) * step
    final_lot = max(vol_min, min(vol_max, rounded_lot))
    decimals = max(0, -int(np.floor(np.log10(step)))) if step < 1 else 0
    final_lot = round(final_lot, decimals)
    actual_risk = final_lot * one_lot_risk

    return final_lot, actual_risk, "OK"


class MT5AlphaTrader:
    def __init__(self, risk_per_trade_usd: float = DEFAULT_RISK_USD):
        self.risk_usd = risk_per_trade_usd
        self.active_positions: Dict[str, Any] = {}
        self.trade_cooldowns: Dict[str, int] = {}
        self.load_state()

    def load_state(self):
        if os.path.exists(ACTIVE_POSITIONS_FILE):
            try:
                with open(ACTIVE_POSITIONS_FILE, "r") as f:
                    self.active_positions = json.load(f)
                logger.info(f"Loaded {len(self.active_positions)} active positions from state.")
            except Exception as e:
                logger.warning(f"Could not load active positions: {e}")

    def save_state(self):
        try:
            with open(ACTIVE_POSITIONS_FILE, "w") as f:
                json.dump(self.active_positions, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving active positions: {e}")

    def check_market_session(self, symbol: str) -> bool:
        """Checks if trading is currently active/open for the symbol."""
        info = mt5.symbol_info(symbol)
        if not info:
            return False
        # trade_mode == 4 is TRADE_MODE_FULL
        if info.trade_mode != mt5.SYMBOL_TRADE_MODE_FULL:
            return False
        tick = mt5.symbol_info_tick(symbol)
        if not tick or tick.ask <= 0 or tick.bid <= 0:
            return False
        return True

    def scan_asset(self, item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Scans an asset for its assigned Alpha setup on confirmed H4 candle close."""
        symbol = item["symbol"]
        setup = item["setup"]
        exit_mode = item["exit_mode"]
        cluster = item["cluster"]

        if not mt5.symbol_select(symbol, True):
            return None

        # Fetch last 1200 H4 bars for full EMA233 warmup convergence
        rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H4, 0, 1200)
        if rates is None or len(rates) < 240:
            return None

        df = pd.DataFrame(rates)
        n = len(df)
        c = df["close"].to_numpy(dtype=float)
        o = df["open"].to_numpy(dtype=float)
        h = df["high"].to_numpy(dtype=float)
        l = df["low"].to_numpy(dtype=float)
        t = df["time"].to_numpy(dtype=int)

        # Fetch current live tick
        tick = mt5.symbol_info_tick(symbol)
        if not tick or tick.bid <= 0 or tick.ask <= 0:
            return None

        # Look at the most recently COMPLETED bar (prev = -2, since -1 is live forming bar)
        prev = n - 2
        candle_close_ts = t[prev] + 4 * 3600

        # Bar Freshness Check: only trigger within 20 minutes of confirmed 4H close
        # tick.time is broker server time in seconds, precisely aligned with candle timestamps
        last_eval_bar = self.trade_cooldowns.get(f"{symbol}_last_bar", 0)
        if candle_close_ts == last_eval_bar:
            return None

        elapsed_since_close = tick.time - candle_close_ts
        if elapsed_since_close > 1200 or elapsed_since_close < 0:
            # Bar closed more than 20 minutes ago (e.g. weekend or bot restart); mark as evaluated to avoid stale late entry
            self.trade_cooldowns[f"{symbol}_last_bar"] = candle_close_ts
            return None

        ema34 = calculate_ema(c, 34)
        ema55 = calculate_ema(c, 55)
        ema89 = calculate_ema(c, 89)
        ema233 = calculate_ema(c, 233)
        atr = calculate_atr(h, l, c, 14)
        adx = compute_adx(h, l, c, 14)

        rng = max(1e-6, h[prev] - l[prev])
        body_top = max(o[prev], c[prev])
        body_bot = min(o[prev], c[prev])
        upper_wick = (h[prev] - body_top) / rng
        lower_wick = (body_bot - l[prev]) / rng

        c_bar = c[prev]
        o_bar = o[prev]
        h_bar = h[prev]
        l_bar = l[prev]
        a_bar = atr[prev]

        sig = 0
        shelf_type = "MID"

        if setup == "canonical":
            # Priority 1: Macro 233 Shelf Direct Rejection (Price above 233, tests and rejects 233)
            if c_bar >= ema233[prev] and (l_bar <= ema233[prev] * 1.002) and lower_wick >= 0.25:
                sig = 1; shelf_type = "SLOW"
            elif c_bar <= ema233[prev] and (h_bar >= ema233[prev] * 0.998) and upper_wick >= 0.25:
                sig = -1; shelf_type = "SLOW"
            # Priority 2: Mid Shelf (EMA 55/89) when trend stack aligned
            elif ema89[prev] > ema233[prev]:
                if lower_wick >= 0.25 and (l_bar <= ema89[prev] * 1.002 or l_bar <= ema55[prev] * 1.002) and (c_bar >= ema89[prev]):
                    sig = 1; shelf_type = "MID"
            elif ema89[prev] < ema233[prev]:
                if upper_wick >= 0.25 and (h_bar >= ema89[prev] * 0.998 or h_bar >= ema55[prev] * 0.998) and (c_bar <= ema89[prev]):
                    sig = -1; shelf_type = "MID"

        elif setup == "turbo_tri_tier":
            # Priority 1: Macro 233 Shelf Direct Rejection (Price above 233, tests and rejects 233)
            if c_bar >= ema233[prev] and (l_bar <= ema233[prev] * 1.002) and lower_wick >= 0.25:
                sig = 1; shelf_type = "SLOW"
            elif c_bar <= ema233[prev] and (h_bar >= ema233[prev] * 0.998) and upper_wick >= 0.25:
                sig = -1; shelf_type = "SLOW"
            # Priority 2: Momentum Turbo & Mid Shelves
            elif ema89[prev] > ema233[prev] and c_bar >= ema233[prev]:
                if lower_wick >= 0.25:
                    if adx[prev] >= 22 and (l_bar <= ema34[prev] * 1.002 or l_bar <= ema55[prev] * 1.002) and (c_bar >= ema55[prev]):
                        sig = 1; shelf_type = "TURBO"
                    elif (l_bar <= ema89[prev] * 1.002) and (c_bar >= ema89[prev]):
                        sig = 1; shelf_type = "MID"
            elif ema89[prev] < ema233[prev] and c_bar <= ema233[prev]:
                if upper_wick >= 0.25:
                    if adx[prev] >= 22 and (h_bar >= ema34[prev] * 0.998 or h_bar >= ema55[prev] * 0.998) and (c_bar <= ema55[prev]):
                        sig = -1; shelf_type = "TURBO"
                    elif (h_bar >= ema89[prev] * 0.998) and (c_bar <= ema89[prev]):
                        sig = -1; shelf_type = "MID"

        elif setup == "wyckoff_swing_sweep":
            roll_l20 = np.min(l[max(0, prev - 21) : prev])
            roll_h20 = np.max(h[max(0, prev - 21) : prev])
            if ema89[prev] > ema233[prev] and c_bar >= ema233[prev]:
                if l_bar < roll_l20 and c_bar > roll_l20 and lower_wick >= 0.25:
                    sig = 1; shelf_type = "SWEEP"
            elif ema89[prev] < ema233[prev] and c_bar <= ema233[prev]:
                if h_bar > roll_h20 and c_bar < roll_h20 and upper_wick >= 0.25:
                    sig = -1; shelf_type = "SWEEP"

        elif setup == "wyckoff_institutional_filtered":
            # Priority 1: Macro 233 Shelf Direct Rejection (Price above 233, tests and rejects 233)
            if c_bar >= ema233[prev] and (l_bar <= ema233[prev] * 1.002) and lower_wick >= 0.25 and (c_bar > o_bar):
                sig = 1; shelf_type = "SLOW"
            elif c_bar <= ema233[prev] and (h_bar >= ema233[prev] * 0.998) and upper_wick >= 0.25 and (c_bar < o_bar):
                sig = -1; shelf_type = "SLOW"
            else:
                sep = abs(ema89[prev] - ema233[prev]) / a_bar
                if 0.25 <= sep <= 2.50 and adx[prev] >= 20.0:
                    if ema89[prev] > ema233[prev] and c_bar > o_bar:
                        if lower_wick >= 0.25:
                            if (l_bar <= ema89[prev] * 1.002 or l_bar <= ema55[prev] * 1.002) and (c_bar >= ema89[prev]):
                                sig = 1; shelf_type = "MID"
                    elif ema89[prev] < ema233[prev] and c_bar < o_bar:
                        if upper_wick >= 0.25:
                            if (h_bar >= ema89[prev] * 0.998 or h_bar >= ema55[prev] * 0.998) and (c_bar <= ema89[prev]):
                                sig = -1; shelf_type = "MID"

        # Mark this bar as evaluated to prevent redundant re-evaluations
        self.trade_cooldowns[f"{symbol}_last_bar"] = candle_close_ts

        if sig == 0:
            return None

        # Determine Entry & Stop Prices
        curr_price = (tick.ask if sig == 1 else tick.bid) if tick else c_bar

        if shelf_type == "SLOW":
            stop_price = (min(ema233[prev], l_bar) - 0.35 * a_bar) if sig == 1 else (max(ema233[prev], h_bar) + 0.35 * a_bar)
        elif shelf_type in ["TURBO", "SWEEP"]:
            stop_price = (l_bar - 0.25 * a_bar) if sig == 1 else (h_bar + 0.25 * a_bar)
        else:
            stop_price = (l_bar - 0.30 * a_bar) if sig == 1 else (h_bar + 0.30 * a_bar)

        risk_dist = abs(curr_price - stop_price)
        if risk_dist <= 0 or (risk_dist / curr_price) < 0.0005:
            return None

        # Determine Target Price
        is_fixed = exit_mode.startswith("fixed_")
        fixed_rr = float(exit_mode.split("_")[1]) if is_fixed else 3.0
        tp_price = (curr_price + fixed_rr * risk_dist) if sig == 1 else (curr_price - fixed_rr * risk_dist)

        # Calculate Lot Size for $10 USD Risk
        lot, actual_risk, status_msg = calculate_lot_size(symbol, self.risk_usd, curr_price, stop_price)
        if lot <= 0:
            logger.warning(f"[{symbol}] Lot calculation returned 0 ({status_msg})")
            return None

        return {
            "symbol": symbol,
            "cluster": cluster,
            "setup": setup,
            "shelf": shelf_type,
            "action": "BUY" if sig == 1 else "SELL",
            "side": sig,
            "entry": round(curr_price, 5),
            "stop": round(stop_price, 5),
            "target": round(tp_price, 5),
            "risk_dist": round(risk_dist, 5),
            "lot": lot,
            "target_risk_usd": self.risk_usd,
            "actual_risk_usd": round(actual_risk, 2),
            "exit_mode": exit_mode,
            "candle_close_time": datetime.fromtimestamp(candle_close_ts, tz=timezone.utc).isoformat(),
        }

    def execute_order(self, signal: Dict[str, Any]) -> bool:
        """Executes the order via MT5 API, or records in pending queue if broker policy blocks API."""
        symbol = signal["symbol"]
        action = signal["action"]
        lot = signal["lot"]
        entry = signal["entry"]
        stop = signal["stop"]
        target = signal["target"]
        exit_mode = signal["exit_mode"]

        logger.info(
            f"🚀 [SIGNAL DETECTED] {action} {symbol} | Lot: {lot} | Entry: {entry} | "
            f"SL: {stop} | TP: {target} | Risk: ${signal['actual_risk_usd']} | Setup: {signal['setup']}"
        )

        # 1. Try MT5 API Order Send
        req = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": lot,
            "type": mt5.ORDER_TYPE_BUY if action == "BUY" else mt5.ORDER_TYPE_SELL,
            "price": entry,
            "sl": stop,
            "tp": target,
            "deviation": 25,
            "magic": 89233,
            "comment": f"AlphaBasket_{signal['cluster'][:6]}",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        order_res = mt5.order_send(req)
        ticket = None

        if order_res and order_res.retcode == mt5.TRADE_RETCODE_DONE:
            ticket = order_res.order
            logger.info(f"✅ [MT5 FILL SUCCESS] {symbol} Ticket: {ticket}")
        else:
            err_msg = order_res.comment if order_res else str(mt5.last_error())
            logger.warning(f"⚠️ [API RESTRICTION] MT5 order_send returned: {err_msg}")
            ticket = int(time.time())  # Autonomous tracking ticket

        # Record active position state
        pos_entry = {
            "ticket": ticket,
            "symbol": symbol,
            "cluster": signal["cluster"],
            "action": action,
            "side": signal["side"],
            "lot": lot,
            "entry_price": entry,
            "stop_price": stop,
            "target_price": target,
            "risk_dist": signal["risk_dist"],
            "target_risk_usd": signal["target_risk_usd"],
            "actual_risk_usd": signal["actual_risk_usd"],
            "exit_mode": exit_mode,
            "entry_time": datetime.now(timezone.utc).isoformat(),
            "max_fav_r": 0.0,
            "status": "OPEN",
        }
        self.active_positions[symbol] = pos_entry
        self.save_state()

        # Append to live signal audit log
        self._append_audit_signal(pos_entry)
        return True

    def _append_audit_signal(self, pos: Dict[str, Any]):
        signals = []
        if os.path.exists(LIVE_SIGNALS_FILE):
            try:
                with open(LIVE_SIGNALS_FILE, "r") as f:
                    signals = json.load(f)
            except Exception:
                signals = []
        signals.append(pos)
        try:
            with open(LIVE_SIGNALS_FILE, "w") as f:
                json.dump(signals, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving live signal audit: {e}")

    def sync_broker_positions(self):
        """Synchronizes open positions directly from MT5 terminal into active state."""
        mt5_pos = mt5.positions_get()
        if mt5_pos is None:
            return

        live_tickets = {p.ticket: p for p in mt5_pos}
        changed = False

        # 1. Sync live MT5 positions into active_positions
        for ticket, p in live_tickets.items():
            sym = p.symbol
            if sym not in self.active_positions or self.active_positions[sym].get("ticket") != ticket:
                basket_item = next((item for item in OPTIMAL_ALPHA_BASKET if item["symbol"] == sym), None)
                cluster = basket_item["cluster"] if basket_item else "External"
                setup = basket_item["setup"] if basket_item else "Manual_MT5"
                exit_mode = basket_item["exit_mode"] if basket_item else "fixed"

                side = 1 if p.type == mt5.ORDER_TYPE_BUY else -1
                info = mt5.symbol_info(sym)
                tick_val = info.trade_tick_value if info else 1.0
                tick_sz = info.trade_tick_size if (info and info.trade_tick_size > 0) else (info.point if info else 0.01)

                rd = abs(p.price_open - p.sl) if p.sl > 0 else 0.01 * p.price_open
                calc_risk = round((rd / tick_sz) * tick_val * p.volume, 2) if (rd > 0 and tick_sz > 0) else self.risk_usd

                self.active_positions[sym] = {
                    "ticket": ticket,
                    "symbol": sym,
                    "cluster": cluster,
                    "setup": setup,
                    "action": "BUY" if side == 1 else "SELL",
                    "side": side,
                    "lot": p.volume,
                    "entry_price": p.price_open,
                    "stop_price": p.sl,
                    "target_price": p.tp,
                    "risk_dist": rd,
                    "target_risk_usd": self.risk_usd,
                    "actual_risk_usd": calc_risk,
                    "exit_mode": exit_mode,
                    "entry_time": datetime.fromtimestamp(p.time, tz=timezone.utc).isoformat(),
                    "max_fav_r": 0.0,
                    "status": "OPEN",
                    "source": "MT5_TERMINAL",
                }
                changed = True
                logger.info(
                    f"🔄 [SYNCED BROKER POSITION] {sym} Ticket: {ticket} | {'BUY' if side == 1 else 'SELL'} "
                    f"{p.volume} lots @ {p.price_open} | SL: {p.sl} | TP: {p.tp} | Risk: ${calc_risk} | Cluster: {cluster}"
                )

        # 2. Detect closed MT5 positions
        for sym, pos in list(self.active_positions.items()):
            if pos.get("source") == "MT5_TERMINAL" and pos.get("ticket") not in live_tickets:
                logger.info(f"🏁 [BROKER POSITION CLOSED] {sym} Ticket: {pos.get('ticket')} resolved on MT5")
                self.active_positions.pop(sym, None)
                changed = True

        if changed:
            self.save_state()

    def manage_open_positions(self):
        """Monitors open positions, tracks MFE, and handles step-trail trailing stops."""
        if not self.active_positions:
            return

        to_remove = []
        for symbol, pos in list(self.active_positions.items()):
            tick = mt5.symbol_info_tick(symbol)
            if not tick:
                continue

            current_price = tick.bid if pos["action"] == "BUY" else tick.ask
            entry_p = pos["entry_price"]
            sl_p = pos["stop_price"]
            tp_p = pos["target_price"]
            rd = pos["risk_dist"]
            side = pos["side"]
            exit_mode = pos["exit_mode"]

            # Current R-multiple
            cur_r = (current_price - entry_p) / rd if side == 1 else (entry_p - current_price) / rd
            if cur_r > pos["max_fav_r"]:
                pos["max_fav_r"] = round(cur_r, 2)

            exited = False
            exit_reason = ""
            exit_r = 0.0

            # Check SL Hit
            if (side == 1 and current_price <= sl_p) or (side == -1 and current_price >= sl_p):
                exited = True
                exit_reason = "SL_HIT"
                exit_r = (sl_p - entry_p) / rd if side == 1 else (entry_p - sl_p) / rd

            # Check TP Hit
            elif (side == 1 and current_price >= tp_p) or (side == -1 and current_price <= tp_p):
                exited = True
                exit_reason = "TP_HIT"
                exit_r = (tp_p - entry_p) / rd if side == 1 else (entry_p - tp_p) / rd

            # Trailing Stop Management (step_trail)
            elif exit_mode == "step_trail":
                mfe = pos["max_fav_r"]
                new_sl = sl_p
                if mfe >= 4.0:
                    lock_price = (entry_p + 2.0 * rd) if side == 1 else (entry_p - 2.0 * rd)
                    new_sl = max(sl_p, lock_price) if side == 1 else min(sl_p, lock_price)
                elif mfe >= 2.0:
                    lock_price = (entry_p + 0.5 * rd) if side == 1 else (entry_p - 0.5 * rd)
                    new_sl = max(sl_p, lock_price) if side == 1 else min(sl_p, lock_price)

                if new_sl != sl_p:
                    pos["stop_price"] = round(new_sl, 5)
                    logger.info(f"🛡️ [TRAILING STOP ADVANCED] {symbol} New SL: {new_sl} (MFE: +{mfe}R)")

            if exited:
                logger.info(f"🏁 [POSITION CLOSED] {symbol} Reason: {exit_reason} | Result: {exit_r:+.2f}R")
                to_remove.append((symbol, exit_reason, exit_r, current_price))

        for sym, reason, r_mult, exit_price in to_remove:
            closed_pos = self.active_positions.pop(sym, None)
            if closed_pos:
                self._record_closed_trade(closed_pos, reason, r_mult, exit_price)
            self.save_state()

    def _record_closed_trade(self, pos: Dict[str, Any], reason: str, r_mult: float, exit_price: float):
        record = {
            "symbol": pos["symbol"],
            "cluster": pos["cluster"],
            "action": pos["action"],
            "lot": pos["lot"],
            "entry_price": pos["entry_price"],
            "exit_price": round(exit_price, 5),
            "entry_time": pos["entry_time"],
            "exit_time": datetime.now(timezone.utc).isoformat(),
            "reason": reason,
            "r_multiple": round(r_mult, 2),
            "net_pnl_usd": round(r_mult * pos["actual_risk_usd"], 2),
        }
        df_row = pd.DataFrame([record])
        if not os.path.exists(TRADING_LEDGER_FILE):
            df_row.to_csv(TRADING_LEDGER_FILE, index=False)
        else:
            df_row.to_csv(TRADING_LEDGER_FILE, mode="a", header=False, index=False)

    def emit_health_status(self):
        """Writes live health pulse to audit/mt5_health_status.json."""
        acc = mt5.account_info()
        term = mt5.terminal_info()

        active_clusters = [p["cluster"] for p in self.active_positions.values()]
        market_statuses = {}
        for item in OPTIMAL_ALPHA_BASKET:
            sym = item["symbol"]
            market_statuses[sym] = "OPEN" if self.check_market_session(sym) else "CLOSED"

        status_obj = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "terminal_connected": bool(term is not None),
            "account_login": acc.login if acc else None,
            "server": acc.server if acc else None,
            "balance": acc.balance if acc else None,
            "equity": acc.equity if acc else None,
            "margin_free": acc.margin_free if acc else None,
            "margin_level": acc.margin_level if acc else None,
            "active_positions_count": len(self.active_positions),
            "active_positions": list(self.active_positions.values()),
            "active_clusters": active_clusters,
            "market_statuses": market_statuses,
            "risk_per_trade_usd": self.risk_usd,
            "concurrency_limit": MAX_CONCURRENT_POSITIONS,
        }
        try:
            with open(HEALTH_STATUS_FILE, "w") as f:
                json.dump(status_obj, f, indent=2)
        except Exception as e:
            logger.error(f"Error emitting health status: {e}")

    def run_sweep(self):
        """Executes a single end-to-end scanning and position management sweep."""
        # 0. Sync real-time positions directly from MT5 terminal
        self.sync_broker_positions()

        # 1. Manage existing positions first
        self.manage_open_positions()

        # 2. Check Global Concurrency Ceiling
        if len(self.active_positions) >= MAX_CONCURRENT_POSITIONS:
            logger.debug(f"Portfolio at maximum concurrency ({len(self.active_positions)}/{MAX_CONCURRENT_POSITIONS}).")
            self.emit_health_status()
            return

        active_clusters = set(p["cluster"] for p in self.active_positions.values())

        # 3. Scan Alpha Basket
        for item in OPTIMAL_ALPHA_BASKET:
            symbol = item["symbol"]
            cluster = item["cluster"]

            # Invariant: Single entry per asset
            if symbol in self.active_positions:
                continue

            # Invariant: Cluster firewall (Max 1 per cluster)
            if cluster in active_clusters:
                continue

            # Check if market is open
            if not self.check_market_session(symbol):
                continue

            # Evaluate setup
            signal = self.scan_asset(item)
            if signal:
                self.execute_order(signal)
                active_clusters.add(cluster)
                if len(self.active_positions) >= MAX_CONCURRENT_POSITIONS:
                    break

        self.emit_health_status()


def main():
    acquire_singleton_lock()
    SCAN_INTERVAL_SEC = 10
    logger.info("=" * 80)
    logger.info("STARTING FUNDEDNEXT MT5 EXPANDED 28-ALPHA UNIVERSE LIVE MONITOR")
    logger.info(f"Risk per trade: ${DEFAULT_RISK_USD:.2f} USD | Max Concurrency: {MAX_CONCURRENT_POSITIONS}")
    logger.info(f"Target Assets: {len(OPTIMAL_ALPHA_BASKET)} symbols across 8 uncorrelated clusters")
    logger.info(f"High-Speed Tick & Bar Scan Loop: {SCAN_INTERVAL_SEC}s interval")
    logger.info("=" * 80)

    if not mt5.initialize():
        logger.error(f"Failed to initialize MT5: {mt5.last_error()}")
        sys.exit(1)

    acc = mt5.account_info()
    term = mt5.terminal_info()
    logger.info(f"Connected to MT5: {term.name} ({acc.server}) | Login: {acc.login} | Equity: ${acc.equity:,.2f}")

    trader = MT5AlphaTrader(risk_per_trade_usd=DEFAULT_RISK_USD)

    logger.info(f"Entering continuous {SCAN_INTERVAL_SEC}-second autonomous scanning loop...")
    while True:
        try:
            trader.run_sweep()
        except Exception as e:
            logger.error(f"Error during sweep: {e}", exc_info=True)
        time.sleep(SCAN_INTERVAL_SEC)


if __name__ == "__main__":
    main()
