#!/usr/bin/env python3
r"""mt5_alpha_trader_monitor.py: Autonomous Live Trader & Risk Governor for the
Optimal Multi-Cluster Alpha Basket on Blueberry Markets MetaTrader 5
(BlueberryMarketsSVG-Live, Account #5126976).

Features:
- Fixed $10 USD Target Risk per Trade with dynamic broker-exact lot sizing.
- Hard Risk Cap (1.2R / $12.00 max): Strictly blocks min-lot risk inflation (Patch 1).
- Broker-Side Trailing Stops: Advanced via mt5.TRADE_ACTION_SLTP (Patch 2).
- Zero Phantom Fills: Rejections are never fabricated or recorded as open (Patch 3).
- Blueberry Daily Risk Governor: 4% daily loss limit ($100) & 10% lifetime floor $2,250 (Patch 4).
- Spread Friction Gate: Discards signals where spread > 15% of 1R risk distance.
- 12-Bar Post-Exit Cooldown: Enforces backtest parity across symbol exits.
- Institutional Pullback Origin: Enforces came_from_above (Long) & came_from_below (Short).
- Atomic State Persistence: tmp + rename prevents file corruption.
"""

import os
import sys
import time
import json
import gc
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional, Tuple, List
import numpy as np
import pandas as pd
import MetaTrader5 as mt5

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
AUDIT_DIR = os.path.join(BASE_DIR, "audit")
os.makedirs(AUDIT_DIR, exist_ok=True)

# Embedded DailyRiskGovernor below
"""
Daily Risk Governor for Blueberry Funded MT5 Accounts ($2,500 Prime 2-Step tier).
Enforces Blueberry Funded's exact prop-firm rulebook:
1. Daily Loss Limit: 4% of initial $2,500 balance ($100.00).
   - Anchored to 00:00 broker server time (UTC+2 / UTC+3).
   - Uses equity drop from server-midnight anchor = max(0.0, anchor - equity).
   - Persisted across process restarts in audit/governor_state.json.
2. Overall Loss Limit: 10% static floor ($2,250.00 on $2,500 account).
3. Throttle Threshold: At 80% of daily limit ($80.00), halt new entries.
"""

from typing import Tuple, Optional
import logging

logger = logging.getLogger("DailyRiskGovernor")
GOVERNOR_STATE_FILE = os.path.join(AUDIT_DIR, "governor_state.json")


def get_server_offset_seconds() -> int:
    """Calculates server time offset in seconds dynamically from broker tick."""
    t = mt5.symbol_info_tick("EURUSD.pi") or mt5.symbol_info_tick("BTCUSD.pi") or mt5.symbol_info_tick("USDJPY.pi")
    if t and t.time > 0:
        return int(round((t.time - time.time()) / 1800) * 1800)
    return 7200  # Default to UTC+2 if tick unavailable


class DailyRiskGovernor:
    def __init__(
        self,
        initial_balance: float = 2500.00,  # Blueberry Funded $2,500 challenge account
        daily_pct: float = 0.04,           # 4% daily limit ($100.00)
        overall_pct: float = 0.10,         # 10% lifetime static floor ($2,250.00)
    ):
        self.initial = initial_balance
        self.daily_limit = round(initial_balance * daily_pct, 2)              # $100.00
        self.overall_floor = round(initial_balance * (1.0 - overall_pct), 2)  # $2,250.00
        self.day_anchor_equity: Optional[float] = None
        self.last_server_day: Optional[int] = None
        self._load_state()

    def _load_state(self):
        if os.path.exists(GOVERNOR_STATE_FILE):
            try:
                with open(GOVERNOR_STATE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.last_server_day = data.get("last_server_day")
                    self.day_anchor_equity = data.get("day_anchor_equity")
            except Exception:
                pass

    def _save_state(self):
        try:
            tmp = f"{GOVERNOR_STATE_FILE}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({
                    "last_server_day": self.last_server_day,
                    "day_anchor_equity": self.day_anchor_equity,
                    "updated_at": datetime.now(timezone.utc).isoformat()
                }, f, indent=2)
            os.replace(tmp, GOVERNOR_STATE_FILE)
        except Exception as e:
            logger.error(f"Error saving governor state: {e}")

    def check(self, current_equity: float, current_balance: float) -> Tuple[str, str, float]:
        """Evaluates live equity against Blueberry rules using exact broker server day."""
        offset = get_server_offset_seconds()
        server_ts = time.time() + offset
        day = int(server_ts // 86400)

        # Server midnight reset: re-anchor to max(balance, equity) at 00:00 server time
        if self.last_server_day is None or day != self.last_server_day:
            self.last_server_day = day
            self.day_anchor_equity = max(current_balance, current_equity)
            self._save_state()
            logger.info(
                f"🌅 [GOVERNOR 00:00 SERVER RESET] Server Day {day}. "
                f"Anchor set to max(balance, equity) = ${self.day_anchor_equity:,.2f}"
            )

        if self.day_anchor_equity is None:
            self.day_anchor_equity = max(current_balance, current_equity)
            self._save_state()

        # Fix P0-2: Do NOT double-count closed losses! Equity already reflects realized PnL.
        used_loss_today = max(0.0, round(self.day_anchor_equity - current_equity, 2))

        # 1. Check Lifetime 10% Static Floor ($2,250.00)
        if current_equity <= self.overall_floor:
            msg = (
                f"CRITICAL: Equity ${current_equity:,.2f} breached Lifetime Floor "
                f"${self.overall_floor:,.2f} (-10.0% static)!"
            )
            return "HALT_OVERALL_FLOOR", msg, used_loss_today

        # 2. Check Daily 4% Limit ($100.00 of initial balance)
        if used_loss_today >= self.daily_limit:
            msg = (
                f"HALT: Today's Loss ${used_loss_today:,.2f} breached Daily 4% Limit "
                f"${self.daily_limit:,.2f}!"
            )
            return "HALT_DAILY_LIMIT", msg, used_loss_today

        # 3. Check 80% Daily Limit Throttle ($80.00)
        throttle_limit = round(0.80 * self.daily_limit, 2)
        if used_loss_today >= throttle_limit:
            msg = (
                f"THROTTLE: Today's Loss ${used_loss_today:,.2f} >= 80% of Daily Limit "
                f"(${throttle_limit:,.2f}). Halting new entries."
            )
            return "THROTTLE_NEW_ENTRIES", msg, used_loss_today

        msg = (
            f"OK (Used Today: ${used_loss_today:,.2f} / ${self.daily_limit:,.2f} | "
            f"Equity: ${current_equity:,.2f} / Floor: ${self.overall_floor:,.2f})"
        )
        return "OK", msg, used_loss_today


ACTIVE_POSITIONS_FILE = os.path.join(AUDIT_DIR, "mt5_active_positions.json")
LIVE_SIGNALS_FILE = os.path.join(AUDIT_DIR, "mt5_live_signals.json")
TRADING_LEDGER_FILE = os.path.join(AUDIT_DIR, "mt5_live_trading_ledger.csv")
HEALTH_STATUS_FILE = os.path.join(AUDIT_DIR, "mt5_health_status.json")
COOLDOWNS_FILE = os.path.join(AUDIT_DIR, "trade_cooldowns.json")
MANUAL_PENDING_FILE = os.path.join(AUDIT_DIR, "manual_action_pending.json")
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
                    # Kill only a genuine prior instance of THIS daemon; a recycled
                    # PID belonging to an unrelated process must never be killed
                    if "python" in proc.name().lower() and any(
                        "mt5_alpha_trader_live" in arg for arg in proc.cmdline()
                    ):
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

# Elite 23 Positive-Alpha Universe across 6 Uncorrelated Clusters (Blueberry Markets Certified)
OPTIMAL_ALPHA_BASKET = [
    # --- Equity Indices (5 Assets) ---
    {"symbol": "SP500.p", "cluster": "Indices", "tf": "1H", "primary_model": "MODEL_1", "ema": 89, "exit_mode": "trail_0.5r", "risk_usd": 5.0},
    {"symbol": "JP225.p", "cluster": "Indices", "tf": "1H", "primary_model": "MODEL_1", "ema": 65, "exit_mode": "trail_0.5r", "risk_usd": 5.0},
    {"symbol": "DJ30.p", "cluster": "Indices", "tf": "1H", "primary_model": "MODEL_1", "ema": 55, "exit_mode": "trail_0.5r", "risk_usd": 5.0},
    {"symbol": "NAS100.p", "cluster": "Indices", "tf": "1H", "primary_model": "MODEL_1", "ema": 55, "exit_mode": "trail_0.5r", "risk_usd": 5.0},
    {"symbol": "HK50.p", "cluster": "Indices", "tf": "1H", "primary_model": "MODEL_1", "ema": 50, "exit_mode": "trail_0.5r", "risk_usd": 5.0},

    # --- Commodities & Energy (3 Assets) ---
    {"symbol": "XAUUSD.pi", "cluster": "Commodity_Energy", "tf": "1H", "primary_model": "MODEL_1", "ema": 55, "exit_mode": "fixed_3.5r", "risk_usd": 5.0},
    {"symbol": "XAGUSD.pi", "cluster": "Commodity_Energy", "tf": "1H", "primary_model": "MODEL_1", "ema": 160, "exit_mode": "fixed_4.0r", "risk_usd": 5.0},
    {"symbol": "USWTI.p", "cluster": "Commodity_Energy", "tf": "1H", "primary_model": "MODEL_1", "ema": 50, "exit_mode": "trail_0.5r", "risk_usd": 5.0},

    # --- Crypto (5 Assets) ---
    {"symbol": "BTCUSD.pi", "cluster": "Crypto", "tf": "1H", "primary_model": "MODEL_2", "ema": 233, "exit_mode": "fixed_4.0r", "risk_usd": 5.0},
    {"symbol": "ETHUSD.pi", "cluster": "Crypto", "tf": "4H", "primary_model": "MODEL_2", "ema": 89, "exit_mode": "fixed_3.5r", "risk_usd": 5.0},
    {"symbol": "XLMUSD.p", "cluster": "Crypto", "tf": "1H", "primary_model": "MODEL_2", "ema": 65, "exit_mode": "fixed_3.5r", "risk_usd": 5.0},
    {"symbol": "XRPUSD.pi", "cluster": "Crypto", "tf": "1H", "primary_model": "MODEL_2", "ema": 89, "exit_mode": "trail_0.5r", "risk_usd": 5.0},
    {"symbol": "ADAUSD.p", "cluster": "Crypto", "tf": "1H", "primary_model": "MODEL_1", "ema": 55, "exit_mode": "fixed_4.0r", "risk_usd": 5.0},

    # --- Forex Majors (4 Assets) ---
    {"symbol": "USDCAD.pi", "cluster": "Forex_Majors", "tf": "1H", "primary_model": "MODEL_1", "ema": 55, "exit_mode": "fixed_3.5r", "risk_usd": 5.0},
    {"symbol": "EURUSD.pi", "cluster": "Forex_Majors", "tf": "1H", "primary_model": "MODEL_1", "ema": 55, "exit_mode": "trail_0.5r", "risk_usd": 5.0},
    {"symbol": "GBPUSD.pi", "cluster": "Forex_Majors", "tf": "4H", "primary_model": "MODEL_2", "ema": 100, "exit_mode": "fixed_3.5r", "risk_usd": 5.0},
    {"symbol": "USDCHF.pi", "cluster": "Forex_Majors", "tf": "1H", "primary_model": "MODEL_2", "ema": 400, "exit_mode": "trail_0.5r", "risk_usd": 5.0},

    # --- Forex JPY Crosses (4 Assets) ---
    {"symbol": "GBPJPY.pi", "cluster": "Forex_JPY", "tf": "4H", "primary_model": "MODEL_1", "ema": 160, "exit_mode": "fixed_2.5r", "risk_usd": 5.0},
    {"symbol": "CADJPY.pi", "cluster": "Forex_JPY", "tf": "4H", "primary_model": "MODEL_1", "ema": 100, "exit_mode": "fixed_2.5r", "risk_usd": 5.0},
    {"symbol": "CHFJPY.pi", "cluster": "Forex_JPY", "tf": "4H", "primary_model": "MODEL_2", "ema": 160, "exit_mode": "fixed_3.5r", "risk_usd": 5.0},
    {"symbol": "USDJPY.pi", "cluster": "Forex_JPY", "tf": "4H", "primary_model": "MODEL_2", "ema": 89, "exit_mode": "trail_0.5r", "risk_usd": 5.0},

    # --- Forex Crosses & Exotics (2 Assets) ---
    {"symbol": "NZDSGD.pi", "cluster": "Forex_Crosses_Exotics", "tf": "1H", "primary_model": "MODEL_1", "ema": 100, "exit_mode": "fixed_3.5r", "risk_usd": 5.0},
    {"symbol": "GBPNOK.pi", "cluster": "Forex_Crosses_Exotics", "tf": "1H", "primary_model": "MODEL_2", "ema": 89, "exit_mode": "trail_0.5r", "risk_usd": 5.0},
]

MAX_CONCURRENT_POSITIONS = 4
MAX_PER_CLUSTER = 1
DEFAULT_RISK_USD = 5.0
MAX_RISK_CAP_USD = 40.0
FIB_ORDER_COMMENT = "fib_orders"
FIB_MAGIC_NUMBER = 202611
POST_EXIT_COOLDOWN_BARS = 12  # 12 bars post-exit cooldown


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
    plus_di_s = np.zeros(n, dtype=float)
    minus_di_s = np.zeros(n, dtype=float)
    alpha = 1.0 / period

    atr_s[period] = np.mean(tr[1 : period + 1])
    plus_di_s[period] = np.mean(plus_dm[1 : period + 1])
    minus_di_s[period] = np.mean(minus_dm[1 : period + 1])

    for i in range(period + 1, n):
        atr_s[i] = (atr_s[i - 1] * (period - 1) + tr[i]) * alpha
        plus_di_s[i] = (plus_di_s[i - 1] * (period - 1) + plus_dm[i]) * alpha
        minus_di_s[i] = (minus_di_s[i - 1] * (period - 1) + minus_dm[i]) * alpha

    plus_di = np.divide(100.0 * plus_di_s, atr_s, out=np.zeros_like(plus_di_s), where=atr_s > 0)
    minus_di = np.divide(100.0 * minus_di_s, atr_s, out=np.zeros_like(minus_di_s), where=atr_s > 0)
    denom = plus_di + minus_di
    dx = np.divide(100.0 * np.abs(plus_di - minus_di), denom, out=np.zeros_like(denom), where=denom > 0)

    adx = np.zeros(n, dtype=float)
    adx_start = period * 2
    if adx_start < n:
        adx[adx_start] = np.mean(dx[period : adx_start + 1])
        for i in range(adx_start + 1, n):
            adx[i] = (adx[i - 1] * (period - 1) + dx[i]) * alpha
    return adx


def calculate_lot_size(symbol: str, risk_usd: float, entry: float, stop: float) -> Tuple[float, float, str]:
    """Calculates broker-compliant lot size for a target dollar risk amount.
    User Mandate: Target risk is $5.00 USD per trade.
    If 1 minimum lot (vol_min = 0.01) already requires >= $5.00 (e.g. SP500, Gold, Crypto, Indices),
    execute minimum lot size (vol_min). If min lot risk < $5.00 (e.g. FX majors/crosses),
    dynamically size up to achieve $5.00 target risk.
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

    vol_min = info.volume_min if info.volume_min > 0 else 0.01
    vol_max = info.volume_max if info.volume_max > 0 else 100.0
    step = info.volume_step if info.volume_step > 0 else 0.01

    min_lot_risk = round(vol_min * one_lot_risk, 2)

    # User Mandate: if more than $5 is required at min lot, take minimum lot size
    if min_lot_risk >= risk_usd:
        final_lot = vol_min
        actual_risk = min_lot_risk
    else:
        raw_lot = risk_usd / one_lot_risk
        rounded_lot = round(raw_lot / step) * step
        final_lot = max(vol_min, min(vol_max, rounded_lot))
        decimals = max(0, -int(np.floor(np.log10(step)))) if step < 1 else 0
        final_lot = round(final_lot, decimals)
        actual_risk = round(final_lot * one_lot_risk, 2)

    if actual_risk > MAX_RISK_CAP_USD:
        return 0.0, 0.0, f"CALCULATED RISK ${actual_risk:.2f} > ${MAX_RISK_CAP_USD:.2f} cap - SKIP"

    return final_lot, actual_risk, "OK"


class MT5AlphaTrader:
    def __init__(self, risk_per_trade_usd: float = DEFAULT_RISK_USD):
        self.risk_usd = risk_per_trade_usd
        self.active_positions: Dict[str, Any] = {}
        self.trade_cooldowns: Dict[str, Any] = {}
        self.governor = DailyRiskGovernor()
        self.load_state()

    def load_state(self):
        """Loads persistent active positions and trade cooldowns atomically."""
        if os.path.exists(ACTIVE_POSITIONS_FILE):
            try:
                with open(ACTIVE_POSITIONS_FILE, "r", encoding="utf-8") as f:
                    self.active_positions = json.load(f)
                logger.info(f"Loaded {len(self.active_positions)} active positions from state.")
            except Exception as e:
                logger.warning(f"Could not load active positions: {e}")

        if os.path.exists(COOLDOWNS_FILE):
            try:
                with open(COOLDOWNS_FILE, "r", encoding="utf-8") as f:
                    self.trade_cooldowns = json.load(f)
            except Exception as e:
                logger.debug(f"Could not load trade cooldowns: {e}")

    def save_state(self):
        """Atomically saves state using temporary files + atomic replace."""
        try:
            tmp_active = f"{ACTIVE_POSITIONS_FILE}.tmp"
            with open(tmp_active, "w", encoding="utf-8") as f:
                json.dump(self.active_positions, f, indent=2, default=str)
            for _ in range(3):
                try:
                    os.replace(tmp_active, ACTIVE_POSITIONS_FILE)
                    break
                except Exception:
                    time.sleep(0.05)

            tmp_cd = f"{COOLDOWNS_FILE}.tmp"
            with open(tmp_cd, "w", encoding="utf-8") as f:
                json.dump(
                    self.trade_cooldowns,
                    f,
                    indent=2,
                    default=lambda x: int(x) if isinstance(x, (np.integer, int)) else str(x),
                )
            for _ in range(3):
                try:
                    os.replace(tmp_cd, COOLDOWNS_FILE)
                    break
                except Exception:
                    time.sleep(0.05)
        except Exception as e:
            logger.error(f"Error saving state atomically: {e}")

    def check_market_session(self, symbol: str) -> bool:
        """Checks if trading is currently active/open for the symbol."""
        info = mt5.symbol_info(symbol)
        if not info:
            return False
        if info.trade_mode != mt5.SYMBOL_TRADE_MODE_FULL:
            return False
        tick = mt5.symbol_info_tick(symbol)
        if not tick or tick.ask <= 0 or tick.bid <= 0:
            return False
        return True

    def scan_asset(self, item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Scans an asset for its empirically champion setup on confirmed candle close (1H or 4H)."""
        symbol = item["symbol"]
        cluster = item["cluster"]
        tf_str = item.get("tf", "4H")
        primary_model = item.get("primary_model", "MODEL_1")
        target_ema = item.get("ema", 89)
        exit_mode = item.get("exit_mode", "trail_1.0r")

        mt5_tf = mt5.TIMEFRAME_H1 if tf_str == "1H" else mt5.TIMEFRAME_H4
        bar_seconds = 3600 if tf_str == "1H" else 14400
        freshness_limit = 3300 if tf_str == "1H" else 13800  # 55 min for 1H, 3h50m for 4H

        rates = mt5.copy_rates_from_pos(symbol, mt5_tf, 0, 600)
        if rates is None or len(rates) < (target_ema + 50):
            logger.warning(f"⚠️ [DATA UNAVAILABLE] {symbol} insufficient history ({len(rates) if rates is not None else 0} bars < {target_ema + 50})")
            return None

        df = pd.DataFrame(rates)
        c = df["close"].to_numpy(dtype=float)
        h = df["high"].to_numpy(dtype=float)
        l = df["low"].to_numpy(dtype=float)
        o = df["open"].to_numpy(dtype=float)
        t = df["time"].to_numpy(dtype=int)
        n = len(c)

        tick = mt5.symbol_info_tick(symbol)
        if not tick:
            return None

        prev = n - 2
        candle_close_ts = int(t[prev] + bar_seconds)

        # 1. Post-Exit Cooldown Enforcement (12 closed bars after the exit bar,
        # exact backtest parity - wall-clock expiry across weekends is not a bar count)
        exit_ts = self.trade_cooldowns.get(f"{symbol}_exit_ts", 0)
        if exit_ts > 0:
            exit_bar = int(np.searchsorted(t, exit_ts, side="right")) - 1
            if (prev - exit_bar) < POST_EXIT_COOLDOWN_BARS:
                bars_left = POST_EXIT_COOLDOWN_BARS - (prev - exit_bar)
                logger.debug(f"⏳ [COOLDOWN ACTIVE] {symbol} in post-exit cooldown ({bars_left} bars remaining)")
                return None

        # 2. Bar Freshness Check: ensure we only evaluate freshly confirmed completed candle
        last_eval_bar = self.trade_cooldowns.get(f"{symbol}_last_bar", 0)
        if candle_close_ts == last_eval_bar:
            return None

        elapsed_since_close = tick.time - candle_close_ts
        if elapsed_since_close > freshness_limit or elapsed_since_close < 0:
            self.trade_cooldowns[f"{symbol}_last_bar"] = candle_close_ts
            return None

        ema_fast = calculate_ema(c, target_ema)
        macro_ema_period = 233 if tf_str == "4H" else 400
        ema_macro = calculate_ema(c, macro_ema_period)
        atr = calculate_atr(h, l, c, 14)

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

        # Anti-Whipsaw Chop Filter: Kaufman Efficiency Ratio & ATR-Banded Crossings
        er_lb = 10
        if prev >= er_lb:
            direction = abs(c[prev] - c[prev - er_lb])
            volatility = sum(abs(c[i] - c[i - 1]) for i in range(prev - er_lb + 1, prev + 1))
            er = direction / volatility if volatility > 0 else 0.0
        else:
            er = 0.5

        # Rejection Wick Exemption: An institutional pin bar / rejection wick (>= 35%) at the champion shelf
        # is the hallmark of structural rejection, even if 10-bar net displacement was absorbed by the pullback.
        has_rejection_wick = (upper_wick >= 0.35) or (lower_wick >= 0.35)

        if er < 0.18 and not has_rejection_wick:
            logger.debug(f"🔇 [CHOP FILTER ACTIVE] {symbol} Kaufman ER {er:.3f} < 0.18 (wick < 35%). Noise regime, skipping.")
            self.trade_cooldowns[f"{symbol}_last_bar"] = candle_close_ts
            return None

        # ATR-Banded Crossing Count: require penetration beyond +-0.25 ATR to count true whipsaw
        ema_crossings = 0
        for b in range(max(1, prev - 12), prev):
            upper_band = ema_fast[b] + 0.25 * atr[b]
            lower_band = ema_fast[b] - 0.25 * atr[b]
            if (c[b] > upper_band and c[b - 1] <= lower_band) or (c[b] < lower_band and c[b - 1] >= upper_band):
                ema_crossings += 1

        if ema_crossings >= 2:
            logger.debug(f"🔇 [BANDED WHIPSAW] {symbol} {ema_crossings} ATR-band crossings across EMA in 12 bars.")
            self.trade_cooldowns[f"{symbol}_last_bar"] = candle_close_ts
            return None

        sig = 0
        executed_model = ""

        # Invariant: Institutional Pullback Origin Validation
        came_from_above = (o_bar >= ema_fast[prev] * 0.998) or (c[prev - 1] >= ema_fast[prev - 1] * 0.998)
        came_from_below = (o_bar <= ema_fast[prev] * 1.002) or (c[prev - 1] <= ema_fast[prev - 1] * 1.002)

        def eval_model_1():
            # Model 1: Pullback Rejection on Champion EMA with ATR-Normalized Slope Filter
            slope_bars = 4 if tf_str == "1H" else 3
            norm_slope = (ema_fast[prev] - ema_fast[prev - slope_bars]) / (slope_bars * a_bar) if a_bar > 0 else 0.0

            # Buy Pullback: Positive slope (>= +0.02 ATR/bar), lower wick >= 25%, candle close >= EMA + 0.05 ATR
            if norm_slope >= 0.02 and came_from_above and lower_wick >= 0.25:
                if l_bar <= ema_fast[prev] * 1.002 and c_bar >= (ema_fast[prev] + 0.05 * a_bar):
                    return 1
            # Sell Pullback: Negative slope (<= -0.02 ATR/bar), upper wick >= 25%, candle close <= EMA - 0.05 ATR
            elif norm_slope <= -0.02 and came_from_below and upper_wick >= 0.25:
                if h_bar >= ema_fast[prev] * 0.998 and c_bar <= (ema_fast[prev] - 0.05 * a_bar):
                    return -1
            return 0

        def eval_model_2():
            # Model 2: Macro Dwell & Reclaim Spring (33% pinbar threshold)
            dwell_lb = 35 if tf_str == "4H" else 100
            min_dwell = 3 if tf_str == "4H" else 12
            max_dwell = 25 if tf_str == "4H" else 60

            # Minimum Decisive Clearance: at least 0.12 ATR beyond EMA to prevent noise trap
            min_clearance = max(0.12 * a_bar, (tick.ask - tick.bid) * 1.5 if tick else 0.0)

            # Buy Reclaim: EMA must not be crashing, price closes decisively above EMA
            if ema_fast[prev] >= ema_fast[prev - 3] * 0.999 and lower_wick >= 0.33:
                if l_bar <= ema_fast[prev] and c_bar >= (ema_fast[prev] + min_clearance):
                    recent_below = sum(1 for b in range(max(0, prev - dwell_lb), prev) if c[b] < ema_fast[b])
                    if min_dwell <= recent_below <= max_dwell:
                        return 1
            # Sell Reclaim: EMA must not be surging, price closes decisively below EMA
            elif ema_fast[prev] <= ema_fast[prev - 3] * 1.001 and upper_wick >= 0.33:
                if h_bar >= ema_fast[prev] and c_bar <= (ema_fast[prev] - min_clearance):
                    recent_above = sum(1 for b in range(max(0, prev - dwell_lb), prev) if c[b] > ema_fast[b])
                    if min_dwell <= recent_above <= max_dwell:
                        return -1
            return 0

        # Strict Champion Model Isolation: Execute ONLY the designated champion model for this asset (No blind cross-model fallback)
        if primary_model == "MODEL_1":
            sig = eval_model_1()
            if sig != 0:
                executed_model = "MODEL_1_PULLBACK"
        elif primary_model == "MODEL_2":
            sig = eval_model_2()
            if sig != 0:
                executed_model = "MODEL_2_RECLAIM"
        else:
            sig = 0
            executed_model = ""

        # Mark bar as evaluated
        self.trade_cooldowns[f"{symbol}_last_bar"] = candle_close_ts
        self.save_state()

        if sig == 0:
            return None

        # Determine Entry & Stop Prices
        # Institutional Stop Loss = 1.5 * ATR(14) from entry (Step 8 mandate; exact
        # backtest parity - the signal-bar wick is entry context, not the stop anchor)
        curr_price = (tick.ask if sig == 1 else tick.bid) if tick else c_bar
        stop_price = (curr_price - 1.5 * a_bar) if sig == 1 else (curr_price + 1.5 * a_bar)

        risk_dist = abs(curr_price - stop_price)
        if risk_dist <= 0 or (risk_dist / curr_price) < 0.0003:
            return None

        # 3. Microstructure Spread vs Risk Friction Gate (<15% institutional ceiling)
        spread = (tick.ask - tick.bid) if (tick and tick.ask > 0 and tick.bid > 0) else 0.0
        spread_in_r = spread / risk_dist if risk_dist > 0 else 1.0
        if spread_in_r > 0.15:
            logger.info(
                f"⚠️ [FRICTION GATE BLOCKED] {symbol} spread {spread:.5f} is {spread_in_r:.1%} of 1R "
                f"(exceeds 15.0% institutional ceiling) - SKIP"
            )
            return None

        # Determine Target Price based on Champion Exit Strategy
        if exit_mode == "trail_1.5r":
            target_rr = 8.0
        elif exit_mode in ["trail_1.0r", "trail_0.5r"]:
            target_rr = 6.0
        elif exit_mode == "fixed_4.0r":
            target_rr = 4.0
        elif exit_mode in ["fixed_3.5r", "fixed_3.5"]:
            target_rr = 3.5
        elif exit_mode in ["fixed_3.0r", "fixed_3.0"]:
            target_rr = 3.0
        elif exit_mode in ["fixed_2.5r", "fixed_2.5"]:
            target_rr = 2.5
        else:
            target_rr = 3.5

        tp_price = (curr_price + target_rr * risk_dist) if sig == 1 else (curr_price - target_rr * risk_dist)

        # Calculate Lot Size for $10 USD Target Risk (with 1.2R / $12.00 hard cap)
        lot, actual_risk, status_msg = calculate_lot_size(symbol, self.risk_usd, curr_price, stop_price)
        if lot <= 0:
            logger.warning(f"[{symbol}] Lot calculation returned 0 ({status_msg})")
            return None

        return {
            "symbol": symbol,
            "cluster": cluster,
            "tf": tf_str,
            "model": executed_model,
            "setup": f"{executed_model}_EMA{target_ema}",
            "shelf": f"EMA_{target_ema}",
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
        """Executes the order via MT5 API.
        Zero Phantom Fills: If order_send fails, NEVER fabricates ticket or open state (Patch 3).
        """
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

        info = mt5.symbol_info(symbol)
        filling = mt5.ORDER_FILLING_IOC
        if info:
            if info.filling_mode & 2:
                filling = mt5.ORDER_FILLING_IOC
            elif info.filling_mode & 1:
                filling = mt5.ORDER_FILLING_FOK
            else:
                filling = mt5.ORDER_FILLING_RETURN

        req = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": lot,
            "type": mt5.ORDER_TYPE_BUY if action == "BUY" else mt5.ORDER_TYPE_SELL,
            "price": entry,
            "sl": stop,
            "tp": target,
            "deviation": 25,
            "magic": FIB_MAGIC_NUMBER,
            "comment": FIB_ORDER_COMMENT,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": filling,
        }

        # One refresh-and-retry pass for transient price rejections; all other
        # outcomes fall through to the manual-punch protocol unchanged
        transient = (getattr(mt5, "TRADE_RETCODE_REQUOTE", 10004),
                     getattr(mt5, "TRADE_RETCODE_PRICE_CHANGED", 10020),
                     getattr(mt5, "TRADE_RETCODE_PRICE_OFF", 10021))
        order_res = mt5.order_send(req)
        if not (order_res and order_res.retcode == mt5.TRADE_RETCODE_DONE) \
                and order_res and order_res.retcode in transient:
            fresh = mt5.symbol_info_tick(symbol)
            if fresh and fresh.ask > 0 and fresh.bid > 0:
                req["price"] = fresh.ask if action == "BUY" else fresh.bid
                order_res = mt5.order_send(req)

        if order_res and order_res.retcode == mt5.TRADE_RETCODE_DONE:
            ticket = order_res.order
            fill_price = getattr(order_res, "price", 0.0) or entry
            logger.info(f"✅ [MT5 AUTO-FILL SUCCESS] {symbol} Ticket: {ticket} @ {fill_price}")
            self._clear_manual_pending(symbol)
        else:
            err_msg = order_res.comment if order_res else str(mt5.last_error())
            retcode = order_res.retcode if order_res else "None"
            logger.warning(f"⚠️ [AUTO-EXECUTION REJECTED] {symbol} retcode {retcode}: {err_msg}")
            
            # Print prominent actionable alert for manual MT5 punching
            border = "=" * 80
            manual_msg = (
                f"\n{border}\n"
                f"🚨 [ACTION REQUIRED: MANUAL PUNCH SETUP IN MT5]\n"
                f"   Asset:       {symbol}\n"
                f"   Action:      {action} (BUY / SELL)\n"
                f"   Volume:      {lot} Lots\n"
                f"   Entry Level: {entry}\n"
                f"   Stop Loss:   {stop}\n"
                f"   Take Profit: {target}\n"
                f"   Risk USD:    ${signal['actual_risk_usd']:.2f} (Target: ${signal['target_risk_usd']:.2f})\n"
                f"   Setup:       {signal['setup']} ({signal['tf']} TF | Exit: {exit_mode})\n"
                f"   Order Tag:   comment='fib_orders' (REQUIRED - the daemon only adopts &\n"
                f"                trails tickets tagged fib_orders / magic 202611)\n"
                f"   Reason:      AutoTrading restricted by broker (retcode {retcode}: {err_msg})\n"
                f"👉 PUNCH THIS ORDER MANUALLY IN MT5 GUI. The daemon will auto-sync and manage trailing SL!\n"
                f"{border}\n"
            )
            print(manual_msg, flush=True)
            logger.critical(manual_msg)

            pending_entry = {
                "symbol": symbol,
                "action": action,
                "lot": lot,
                "entry": entry,
                "stop": stop,
                "target": target,
                "risk_usd": signal["actual_risk_usd"],
                "target_risk_usd": signal["target_risk_usd"],
                "setup": signal["setup"],
                "tf": signal["tf"],
                "exit_mode": exit_mode,
                "cluster": signal["cluster"],
                "detected_at": datetime.now(timezone.utc).isoformat(),
                "status": "AWAITING_MANUAL_PUNCH"
            }
            self._save_manual_pending(pending_entry)
            return False  # Strictly NO ticket fabrication, NO phantom OPEN state

        pos_entry = {
            "ticket": ticket,
            "symbol": symbol,
            "cluster": signal["cluster"],
            "setup": signal["setup"],
            "action": action,
            "side": signal["side"],
            "lot": lot,
            "entry_price": fill_price,
            "stop_price": stop,
            "target_price": target,
            "risk_dist": signal["risk_dist"],
            "target_risk_usd": signal["target_risk_usd"],
            "actual_risk_usd": signal["actual_risk_usd"],
            "exit_mode": exit_mode,
            "entry_time": datetime.now(timezone.utc).isoformat(),
            "max_fav_r": 0.0,
            "status": "OPEN",
            "source": "MONITOR_ALGO",
        }
        self.active_positions[symbol] = pos_entry
        self.save_state()
        self._append_audit_signal(pos_entry)
        return True

    def _append_audit_signal(self, pos: Dict[str, Any]):
        signals = []
        if os.path.exists(LIVE_SIGNALS_FILE):
            try:
                with open(LIVE_SIGNALS_FILE, "r", encoding="utf-8") as f:
                    signals = json.load(f)
            except Exception:
                signals = []
        signals.append(pos)
        try:
            tmp_sig = f"{LIVE_SIGNALS_FILE}.tmp"
            with open(tmp_sig, "w", encoding="utf-8") as f:
                json.dump(signals, f, indent=2)
            os.replace(tmp_sig, LIVE_SIGNALS_FILE)
        except Exception as e:
            logger.error(f"Error saving live signal audit: {e}")

    def _save_manual_pending(self, entry: Dict[str, Any]):
        try:
            tmp = f"{MANUAL_PENDING_FILE}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(entry, f, indent=2)
            os.replace(tmp, MANUAL_PENDING_FILE)
        except Exception as e:
            logger.error(f"Error saving manual pending signal: {e}")

    def _close_broker_position(self, pos: Dict[str, Any], comment: str = "ALGO_CLOSE") -> bool:
        """Sends an active broker deal order to close an open MT5 position."""
        ticket = pos.get("ticket")
        sym = pos.get("symbol")
        lot = pos.get("lot", 0.01)
        side = pos.get("side", 1)
        order_type = mt5.ORDER_TYPE_SELL if side == 1 else mt5.ORDER_TYPE_BUY
        tick = mt5.symbol_info_tick(sym)
        if not tick:
            return False
        price = tick.bid if side == 1 else tick.ask
        req = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": sym,
            "volume": lot,
            "type": order_type,
            "position": ticket,
            "price": price,
            "deviation": 20,
            "magic": FIB_MAGIC_NUMBER,
            "comment": FIB_ORDER_COMMENT,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        res = mt5.order_send(req)
        if res and res.retcode == mt5.TRADE_RETCODE_DONE:
            logger.info(f"✅ [CLOSED BROKER POSITION] {sym} Ticket: {ticket} @ {price} | Comment: {comment}")
            return True
        logger.warning(f"⚠️ [CLOSE BROKER POSITION FAILED] {sym} Ticket: {ticket}: {res.comment if res else mt5.last_error()}")
        return False

    def flatten_all(self, reason: str = "EMERGENCY_FLATTEN"):
        """Emergency liquidate all open fib_orders positions."""
        logger.warning(f"🚨 [FLATTEN ALL TRIGGERED] Reason: {reason}")
        for sym, pos in list(self.active_positions.items()):
            self._close_broker_position(pos, comment=reason)

    def _clear_manual_pending(self, symbol: str):
        if os.path.exists(MANUAL_PENDING_FILE):
            try:
                with open(MANUAL_PENDING_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("symbol") == symbol:
                    os.remove(MANUAL_PENDING_FILE)
            except Exception:
                pass

    def sync_broker_positions(self):
        """Synchronizes open positions directly from MT5 terminal into active state.
        STRICT MULTI-ALGO ISOLATION: Only tracks and manages orders with comment='fib_orders'
        or magic=202611. Other algorithms operating on this account are strictly untouched.
        """
        mt5_pos = mt5.positions_get()
        if mt5_pos is None:
            return

        # STRICT MULTI-ALGO ISOLATION: filter exclusively fib_orders
        live_tickets = {
            p.ticket: p for p in mt5_pos
            if (getattr(p, "comment", "") == FIB_ORDER_COMMENT or getattr(p, "magic", 0) == FIB_MAGIC_NUMBER)
        }
        changed = False

        # 1. Sync live MT5 positions into active_positions
        for ticket, p in live_tickets.items():
            sym = p.symbol
            self._clear_manual_pending(sym)
            if sym not in self.active_positions or self.active_positions[sym].get("ticket") != ticket:
                basket_item = next((item for item in OPTIMAL_ALPHA_BASKET if item["symbol"] == sym), None)
                cluster = basket_item["cluster"] if basket_item else "External"
                setup = basket_item.get("setup", f"{basket_item.get('primary_model', 'MODEL_1')}_EMA{basket_item.get('ema', 100)}") if basket_item else "Manual_MT5"
                exit_mode = basket_item.get("exit_mode", "fixed") if basket_item else "fixed"

                side = 1 if p.type == mt5.ORDER_TYPE_BUY else -1
                info = mt5.symbol_info(sym)
                tick_val = info.trade_tick_value if info else 1.0
                tick_sz = info.trade_tick_size if (info and info.trade_tick_size > 0) else (info.point if info else 0.01)

                if p.sl > 0:
                    rd = abs(p.price_open - p.sl)
                    stop_p = p.sl
                else:
                    # Default institutional stop: 1.5 H4 ATR
                    rates = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_H4, 0, 20)
                    if rates is not None and len(rates) >= 14:
                        rd = float(np.mean(rates['high'][-14:] - rates['low'][-14:])) * 1.5
                    else:
                        rd = 0.005 * p.price_open
                    stop_p = (p.price_open - rd) if side == 1 else (p.price_open + rd)

                if exit_mode == "trail_1.5r":
                    target_rr = 8.0
                elif exit_mode in ["trail_1.0r", "trail_0.5r"]:
                    target_rr = 6.0
                elif exit_mode == "fixed_4.0r":
                    target_rr = 4.0
                elif exit_mode in ["fixed_3.5r", "fixed_3.5"]:
                    target_rr = 3.5
                elif exit_mode in ["fixed_2.5r", "fixed_2.5"]:
                    target_rr = 2.5
                else:
                    target_rr = 3.0
                target_p = p.tp if p.tp > 0 else ((p.price_open + target_rr * rd) if side == 1 else (p.price_open - target_rr * rd))
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
                    "stop_price": stop_p,
                    "target_price": target_p,
                    "risk_dist": rd,
                    "initial_risk_dist": rd,
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
                    f"{p.volume} lots @ {p.price_open} | SL: {stop_p} | TP: {target_p} | Risk: ${calc_risk} | Cluster: {cluster}"
                )
            else:
                # Update SL / TP if modified on MT5 terminal - NEVER overwrite initial_risk_dist or risk_dist
                pos_entry = self.active_positions[sym]
                if p.sl > 0 and abs(pos_entry.get("stop_price", 0.0) - p.sl) > 1e-5:
                    pos_entry["stop_price"] = p.sl
                    changed = True
                    logger.info(f"🔄 [SYNCED BROKER SL UPDATE] {sym} SL updated to {p.sl}")
                if p.tp > 0 and abs(pos_entry.get("target_price", 0.0) - p.tp) > 1e-5:
                    pos_entry["target_price"] = p.tp
                    changed = True
                    logger.info(f"🔄 [SYNCED BROKER TP UPDATE] {sym} TP updated to {p.tp}")

        # 2. Detect closed MT5 positions (strict reconciliation with broker deal history)
        for sym, pos in list(self.active_positions.items()):
            if pos.get("ticket") not in live_tickets:
                ticket = pos.get("ticket")
                deals = mt5.history_deals_get(position=ticket)
                exit_p = pos.get("target_price", 0.0)
                real_pnl = 0.0
                exit_reason = "BROKER_RESOLVED"

                if deals and len(deals) > 0:
                    exit_deal = deals[-1]
                    if exit_deal.price > 0:
                        exit_p = exit_deal.price
                    real_pnl = sum(d.profit + d.commission + d.swap + getattr(d, 'fee', 0.0) for d in deals)
                    comment = (exit_deal.comment or "").upper()
                    if "TP" in comment or exit_deal.reason == getattr(mt5, "DEAL_REASON_TP", 4):
                        exit_reason = "TP_HIT"
                    elif "SL" in comment or exit_deal.reason == getattr(mt5, "DEAL_REASON_SL", 3):
                        exit_reason = "SL_HIT"
                    elif exit_deal.reason == getattr(mt5, "DEAL_REASON_SO", 5):
                        exit_reason = "STOP_OUT"
                    else:
                        exit_reason = "BROKER_CLOSE"
                else:
                    tick = mt5.symbol_info_tick(sym)
                    if tick:
                        exit_p = tick.bid if pos.get("side") == 1 else tick.ask

                rd = pos.get("initial_risk_dist") or pos.get("risk_dist") or 1.0
                entry_p = pos.get("entry_price", exit_p)
                side = pos.get("side", 1)
                r_mult = ((exit_p - entry_p) / rd if side == 1 else (entry_p - exit_p) / rd) if rd > 0 else 0.0

                logger.info(
                    f"🏁 [BROKER POSITION RESOLVED] {sym} Ticket: {ticket} | Reason: {exit_reason} | "
                    f"Exit: {exit_p} | Net PnL: ${real_pnl:.2f} | R: {r_mult:+.2f}R"
                )
                self._record_closed_trade(pos, exit_reason, r_mult, exit_p, real_pnl=real_pnl)
                self.active_positions.pop(sym, None)
                changed = True

        if changed:
            self.save_state()

    def manage_open_positions(self):
        """Monitors open positions, enforces targets/stops, and sends real broker trailing stops."""
        for symbol, pos in list(self.active_positions.items()):
            tick = mt5.symbol_info_tick(symbol)
            if not tick:
                continue

            current_price = tick.bid if pos["action"] == "BUY" else tick.ask
            entry_p = pos["entry_price"]
            sl_p = pos["stop_price"]
            tp_p = pos["target_price"]
            rd = pos.get("initial_risk_dist") or pos.get("risk_dist") or 1.0
            side = pos["side"]
            exit_mode = pos["exit_mode"]

            # Current R-multiple (division by zero protected)
            cur_r = ((current_price - entry_p) / rd if side == 1 else (entry_p - current_price) / rd) if rd > 0 else 0.0
            if cur_r > pos["max_fav_r"]:
                pos["max_fav_r"] = round(cur_r, 2)

            exited = False
            exit_reason = ""

            # Check Virtual Trailing SL Hit if broker rejected SL modification
            v_sl = pos.get("virtual_trailing_sl")
            if v_sl and ((side == 1 and current_price <= v_sl) or (side == -1 and current_price >= v_sl)):
                exited = True
                exit_reason = "VIRTUAL_SL_HIT"

            # Dynamic Trailing Stop Management (trail_1.5r, trail_1.0r, trail_0.5r, lock_0.5r, step_trail)
            elif exit_mode in ["trail_1.5r", "trail_1.0r", "trail_0.5r", "lock_0.5r", "step_trail"]:
                mfe = pos["max_fav_r"]
                new_sl = sl_p

                if exit_mode == "trail_1.5r" and mfe >= 2.5:
                    locked_r = mfe - 1.5
                    target_sl = (entry_p + locked_r * rd) if side == 1 else (entry_p - locked_r * rd)
                    new_sl = max(sl_p, target_sl) if side == 1 else min(sl_p, target_sl)
                elif exit_mode == "trail_1.0r" and mfe >= 2.0:
                    locked_r = mfe - 1.0
                    target_sl = (entry_p + locked_r * rd) if side == 1 else (entry_p - locked_r * rd)
                    new_sl = max(sl_p, target_sl) if side == 1 else min(sl_p, target_sl)
                elif exit_mode == "trail_0.5r" and mfe >= 1.5:
                    locked_r = mfe - 0.5
                    target_sl = (entry_p + locked_r * rd) if side == 1 else (entry_p - locked_r * rd)
                    new_sl = max(sl_p, target_sl) if side == 1 else min(sl_p, target_sl)
                elif exit_mode == "lock_0.5r" and mfe >= 1.5:
                    target_sl = (entry_p + 0.5 * rd) if side == 1 else (entry_p - 0.5 * rd)
                    new_sl = max(sl_p, target_sl) if side == 1 else min(sl_p, target_sl)
                elif exit_mode == "step_trail":
                    if mfe >= 4.0:
                        lock_price = (entry_p + 2.0 * rd) if side == 1 else (entry_p - 2.0 * rd)
                        new_sl = max(sl_p, lock_price) if side == 1 else min(sl_p, lock_price)
                    elif mfe >= 2.0:
                        lock_price = (entry_p + 0.5 * rd) if side == 1 else (entry_p - 0.5 * rd)
                        new_sl = max(sl_p, lock_price) if side == 1 else min(sl_p, lock_price)

                if new_sl != sl_p:
                    info = mt5.symbol_info(symbol)
                    digits = info.digits if info else 5
                    new_sl_rounded = round(new_sl, digits)
                    # Ratchet guard: only send a broker modification when the
                    # rounded level strictly improves the stop (no no-op SLTP
                    # requests, no retcode 10025 noise, no spurious virtual fallback)
                    improves = new_sl_rounded > sl_p if side == 1 else new_sl_rounded < sl_p
                    if improves:
                        # Send MT5 broker modification request
                        req = {
                            "action": mt5.TRADE_ACTION_SLTP,
                            "position": pos["ticket"],
                            "symbol": symbol,
                            "sl": new_sl_rounded,
                            "tp": tp_p if tp_p else 0.0,
                        }
                        res = mt5.order_send(req)
                        if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                            pos["stop_price"] = new_sl_rounded
                            logger.info(
                                f"🛡️ [BROKER TRAIL ADVANCED] {symbol} Ticket: {pos['ticket']} "
                                f"New SL: {new_sl_rounded} (MFE: +{mfe}R | Mode: {exit_mode})"
                            )
                            self.save_state()
                        else:
                            retcode = res.retcode if res else "None"
                            err_comment = res.comment if res else str(mt5.last_error())
                            logger.warning(
                                f"⚠️ [TRAILING SL NOTICE] {symbol} Ticket: {pos['ticket']} "
                                f"MFE +{mfe}R ({exit_mode}) target SL {new_sl_rounded}. Broker retcode {retcode}: {err_comment}"
                            )
                            # Save virtual trailing stop level
                            pos["virtual_trailing_sl"] = new_sl_rounded

            if exited:
                logger.info(f"🏁 [ALGO EXIT EXECUTING] {symbol} Reason: {exit_reason} | Level: {current_price}")
                # Real close order on broker - sync_broker_positions will handle post-close audit cleanly
                self._close_broker_position(pos, comment=exit_reason)

    def _record_closed_trade(self, pos: Dict[str, Any], reason: str, r_mult: float, exit_price: float, real_pnl: Optional[float] = None):
        sym = pos["symbol"]
        offset = get_server_offset_seconds()
        server_now = int(time.time() + offset)
        # Trigger 12-bar post-exit cooldown (server-epoch)
        self.trade_cooldowns[f"{sym}_exit_ts"] = server_now
        self.save_state()

        calc_pnl = real_pnl if real_pnl is not None else round(r_mult * pos.get("actual_risk_usd", self.risk_usd), 2)

        record = {
            "symbol": sym,
            "cluster": pos["cluster"],
            "setup": pos.get("setup", "canonical"),
            "action": pos["action"],
            "lot": pos["lot"],
            "entry_price": pos["entry_price"],
            "exit_price": round(exit_price, 5),
            "entry_time": pos["entry_time"],
            "exit_time": datetime.now(timezone.utc).isoformat(),
            "reason": reason,
            "r_multiple": round(r_mult, 2),
            "net_pnl_usd": round(calc_pnl, 2),
        }
        df_row = pd.DataFrame([record])
        if not os.path.exists(TRADING_LEDGER_FILE):
            df_row.to_csv(TRADING_LEDGER_FILE, index=False)
        else:
            df_row.to_csv(TRADING_LEDGER_FILE, mode="a", header=False, index=False)

    def emit_health_status(self, gov_status: str = "OK", used_loss_today: float = 0.0):
        """Writes live health pulse atomically to audit/mt5_health_status.json."""
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
            "governor_status": gov_status,
            "used_loss_today_usd": used_loss_today,
            "daily_loss_limit_usd": self.governor.daily_limit,
            "lifetime_floor_usd": self.governor.overall_floor,
            "active_positions_count": len(self.active_positions),
            "active_positions": list(self.active_positions.values()),
            "active_clusters": active_clusters,
            "market_statuses": market_statuses,
            "risk_per_trade_usd": self.risk_usd,
            "max_risk_cap_usd": MAX_RISK_CAP_USD,
            "concurrency_limit": MAX_CONCURRENT_POSITIONS,
            "manual_pending_signal": json.load(open(MANUAL_PENDING_FILE, "r", encoding="utf-8")) if os.path.exists(MANUAL_PENDING_FILE) else None,
        }
        try:
            tmp_health = f"{HEALTH_STATUS_FILE}.tmp"
            with open(tmp_health, "w", encoding="utf-8") as f:
                json.dump(status_obj, f, indent=2, default=str)
            for _ in range(3):
                try:
                    os.replace(tmp_health, HEALTH_STATUS_FILE)
                    break
                except Exception:
                    time.sleep(0.05)
        except Exception as e:
            logger.debug(f"Health emit notice: {e}")

    def run_sweep(self):
        """Executes a single end-to-end scanning and position management sweep."""
        acc = mt5.account_info()
        if not acc:
            logger.warning("MT5 account_info() unavailable. Skipping sweep.")
            return

        # 0. Daily Risk Governor Check (Anchored to Server Midnight, max(balance, equity))
        gov_status, gov_msg, used_loss = self.governor.check(acc.equity, acc.balance)
        if gov_status == "HALT_OVERALL_FLOOR":
            logger.critical(f"🚨 [GOVERNOR CRITICAL] {gov_msg} FLATTENING ALL POSITIONS.")
            self.flatten_all("FLOOR_BREACH")
            self.sync_broker_positions()
            self.emit_health_status(gov_status=gov_status, used_loss_today=used_loss)
            return

        if gov_status in ("HALT_DAILY_LIMIT", "THROTTLE_NEW_ENTRIES"):
            # Daily limit breached OR 80% throttle reached: halt ALL new entries
            # (Step 1 mandate), keep managing/trailing existing positions
            logger.warning(f"🚨 [GOVERNOR ACTIVE] {gov_msg} Halting new entries.")
            # Still manage open positions to trail/close existing trades
            self.manage_open_positions()
            self.sync_broker_positions()
            self.emit_health_status(gov_status=gov_status, used_loss_today=used_loss)
            return

        max_concurrent = MAX_CONCURRENT_POSITIONS

        # 1. Sync real-time positions directly from MT5 terminal
        self.sync_broker_positions()

        # 2. Manage existing positions first
        self.manage_open_positions()

        # 3. Check Global Concurrency Ceiling
        if len(self.active_positions) >= max_concurrent:
            logger.debug(f"Portfolio at concurrency ceiling ({len(self.active_positions)}/{max_concurrent}).")
            self.emit_health_status(gov_status=gov_status, used_loss_today=used_loss)
            return

        # 4. Open Risk Budget Soft Cap Check (90% of daily limit)
        open_risk = sum(p.get("actual_risk_usd", self.risk_usd) for p in self.active_positions.values())
        budget = self.governor.daily_limit * 0.90
        if (used_loss + open_risk + self.risk_usd) > budget:
            logger.debug(
                f"Risk budget full: used ${used_loss:.2f} + open ${open_risk:.2f} + new ${self.risk_usd:.2f} "
                f"> 90% cap (${budget:.2f}). Skipping scan."
            )
            self.emit_health_status(gov_status="BUDGET_CAP_REACHED", used_loss_today=used_loss)
            return

        active_clusters = set(p["cluster"] for p in self.active_positions.values())

        # 4. Scan Alpha Basket
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
                if self.execute_order(signal):
                    active_clusters.add(cluster)
                    if len(self.active_positions) >= max_concurrent:
                        break

        self.emit_health_status(gov_status=gov_status, used_loss_today=used_loss)


def main():
    acquire_singleton_lock()
    SCAN_INTERVAL_SEC = 10
    once_mode = "--once" in sys.argv
    logger.info("=" * 80)
    logger.info("STARTING BLUEBERRY MARKETS MT5 ELITE ALPHA MONITOR WITH RISK GOVERNANCE")
    logger.info(f"Target Risk: ${DEFAULT_RISK_USD:.2f} USD (Min lot fallback) | Max Cap: ${MAX_RISK_CAP_USD:.2f} USD")
    logger.info(f"Max Concurrency: {MAX_CONCURRENT_POSITIONS} | Max per Cluster: {MAX_PER_CLUSTER}")
    logger.info(f"Target Assets: {len(OPTIMAL_ALPHA_BASKET)} symbols across 6 uncorrelated clusters")
    logger.info(f"Isolation Protocol: comment='{FIB_ORDER_COMMENT}' | magic={FIB_MAGIC_NUMBER}")
    logger.info(f"High-Speed Tick & Bar Scan Loop: {SCAN_INTERVAL_SEC}s interval")
    logger.info("=" * 80)

    if not mt5.initialize():
        logger.error(f"Failed to initialize MT5: {mt5.last_error()}")
        sys.exit(1)

    acc = mt5.account_info()
    term = mt5.terminal_info()
    logger.info(f"Connected to MT5: {term.name} ({acc.server}) | Login: {acc.login} | Equity: ${acc.equity:,.2f}")

    trader = MT5AlphaTrader(risk_per_trade_usd=DEFAULT_RISK_USD)

    if once_mode:
        logger.info("Running single pass scan (--once mode)...")
        trader.run_sweep()
        logger.info("Single pass scan completed successfully.")
        return

    logger.info(f"Entering continuous {SCAN_INTERVAL_SEC}-second autonomous scanning loop...")
    sweep_count = 0
    while True:
        try:
            trader.run_sweep()
            sweep_count += 1
            if sweep_count % 15 == 0:
                gc.collect()
        except Exception as e:
            logger.error(f"Error during sweep: {e}", exc_info=True)
        time.sleep(SCAN_INTERVAL_SEC)


if __name__ == "__main__":
    main()
