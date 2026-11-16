#!/usr/bin/env python3
r"""mt5_alpha_trader_monitor.py: Autonomous Live Trader & Risk Governor for the
Optimal Multi-Cluster Alpha Basket on FundedNext MetaTrader 5.

Features:
- Fixed $10 USD Target Risk per Trade with dynamic broker-exact lot sizing.
- Hard Risk Cap (1.1R / $11.00 max): Strictly blocks min-lot risk inflation (Patch 1).
- Broker-Side Trailing Stops: Advanced via mt5.TRADE_ACTION_SLTP (Patch 2).
- Zero Phantom Fills: Rejections are never fabricated or recorded as open (Patch 3).
- FundedNext Daily Risk Governor: 5% daily loss limit & 10% lifetime floor (Patch 4).
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

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
AUDIT_DIR = os.path.join(BASE_DIR, "audit")
os.makedirs(AUDIT_DIR, exist_ok=True)

from engine.daily_risk_governor import DailyRiskGovernor

ACTIVE_POSITIONS_FILE = os.path.join(AUDIT_DIR, "mt5_active_positions.json")
LIVE_SIGNALS_FILE = os.path.join(AUDIT_DIR, "mt5_live_signals.json")
TRADING_LEDGER_FILE = os.path.join(AUDIT_DIR, "mt5_live_trading_ledger.csv")
HEALTH_STATUS_FILE = os.path.join(AUDIT_DIR, "mt5_health_status.json")
COOLDOWNS_FILE = os.path.join(AUDIT_DIR, "trade_cooldowns.json")
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

# Elite 20 Positive-Alpha Universe across 6 Uncorrelated Clusters (Cleaned Champion Basket)
OPTIMAL_ALPHA_BASKET = [
    # --- Crypto (4 Assets) ---
    {"symbol": "DOGUSD", "cluster": "Crypto", "setup": "canonical", "exit_mode": "fixed_3.5", "risk_usd": 10.0},
    {"symbol": "ETHUSD", "cluster": "Crypto", "setup": "wyckoff_institutional_filtered", "exit_mode": "step_trail", "risk_usd": 10.0},
    {"symbol": "ADAUSD", "cluster": "Crypto", "setup": "macro_reclaim_spring", "exit_mode": "fixed_3.5", "risk_usd": 10.0},
    {"symbol": "XLMUSD", "cluster": "Crypto", "setup": "canonical", "exit_mode": "fixed_3.0", "risk_usd": 10.0},

    # --- Commodities (1 Asset) ---
    {"symbol": "XAUUSD", "cluster": "Commodity_Energy", "setup": "canonical", "exit_mode": "fixed_3.0", "risk_usd": 10.0},

    # --- Equity Indices (2 Assets) ---
    {"symbol": "US30", "cluster": "Indices", "setup": "wyckoff_institutional_filtered", "exit_mode": "fixed_3.5", "risk_usd": 10.0},
    {"symbol": "HK50", "cluster": "Indices", "setup": "wyckoff_institutional_filtered", "exit_mode": "fixed_2.5", "risk_usd": 10.0},

    # --- Forex JPY Crosses (5 Assets) ---
    {"symbol": "NZDJPY", "cluster": "Forex_JPY", "setup": "macro_reclaim_spring", "exit_mode": "fixed_3.5", "risk_usd": 10.0},
    {"symbol": "CHFJPY", "cluster": "Forex_JPY", "setup": "canonical", "exit_mode": "fixed_3.0", "risk_usd": 10.0},
    {"symbol": "EURJPY", "cluster": "Forex_JPY", "setup": "wyckoff_swing_sweep", "exit_mode": "fixed_3.0", "risk_usd": 10.0},
    {"symbol": "USDJPY", "cluster": "Forex_JPY", "setup": "turbo_tri_tier", "exit_mode": "fixed_2.5", "risk_usd": 10.0},
    {"symbol": "CADJPY", "cluster": "Forex_JPY", "setup": "wyckoff_swing_sweep", "exit_mode": "fixed_3.0", "risk_usd": 10.0},

    # --- Forex USD & Exotic Pairs (3 Assets) ---
    {"symbol": "USDCHF", "cluster": "Forex_USD", "setup": "wyckoff_institutional_filtered", "exit_mode": "fixed_3.0", "risk_usd": 10.0},
    {"symbol": "USDCNH", "cluster": "Forex_USD", "setup": "canonical", "exit_mode": "fixed_3.0", "risk_usd": 10.0},
    {"symbol": "NZDSGD", "cluster": "Forex_Exotic", "setup": "macro_reclaim_spring", "exit_mode": "fixed_3.5", "risk_usd": 10.0},

    # --- US Equities (5 Assets) ---
    {"symbol": "MSFT", "cluster": "US_Equities", "setup": "turbo_tri_tier", "exit_mode": "fixed_3.0", "risk_usd": 10.0},
    {"symbol": "AMD", "cluster": "US_Equities", "setup": "turbo_tri_tier", "exit_mode": "fixed_3.0", "risk_usd": 10.0},
    {"symbol": "BABA", "cluster": "US_Equities", "setup": "turbo_tri_tier", "exit_mode": "step_trail", "risk_usd": 10.0},
    {"symbol": "NFLX", "cluster": "US_Equities", "setup": "turbo_tri_tier", "exit_mode": "fixed_3.0", "risk_usd": 10.0},
    {"symbol": "NKE", "cluster": "US_Equities", "setup": "canonical", "exit_mode": "fixed_3.0", "risk_usd": 10.0},
]

MAX_CONCURRENT_POSITIONS = 4
MAX_PER_CLUSTER = 1
DEFAULT_RISK_USD = 10.0
POST_EXIT_COOLDOWN_BARS = 12  # 12 H4 bars = 48 hours post-exit cooldown


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

    plus_di = np.where(atr_s > 0, 100.0 * (plus_di_s / atr_s), 0.0)
    minus_di = np.where(atr_s > 0, 100.0 * (minus_di_s / atr_s), 0.0)
    denom = plus_di + minus_di
    dx = np.where(denom > 0, 100.0 * np.abs(plus_di - minus_di) / denom, 0.0)

    adx = np.zeros(n, dtype=float)
    adx_start = period * 2
    if adx_start < n:
        adx[adx_start] = np.mean(dx[period : adx_start + 1])
        for i in range(adx_start + 1, n):
            adx[i] = (adx[i - 1] * (period - 1) + dx[i]) * alpha
    return adx


def calculate_lot_size(symbol: str, risk_usd: float, entry: float, stop: float) -> Tuple[float, float, str]:
    """Calculates broker-compliant lot size for a target dollar risk amount.
    Enforces a strict 1.1R ($11.00) hard cap to block min-lot risk inflation (Patch 1).
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

    # CRO Invariant: Hard Risk Cap (1.1R maximum)
    MAX_RISK_USD = round(risk_usd * 1.10, 2)

    raw_lot = risk_usd / one_lot_risk
    step = info.volume_step if info.volume_step > 0 else 0.01
    vol_min = info.volume_min if info.volume_min > 0 else 0.01
    vol_max = info.volume_max if info.volume_max > 0 else 100.0

    rounded_lot = round(raw_lot / step) * step

    if rounded_lot < vol_min:
        # NEVER floor upward if min lot inflates risk beyond the 1.1R hard cap!
        min_lot_risk = vol_min * one_lot_risk
        if min_lot_risk > MAX_RISK_USD:
            return 0.0, 0.0, f"MIN-LOT RISK ${min_lot_risk:.2f} > ${MAX_RISK_USD:.2f} cap - SKIP"
        final_lot = vol_min
    else:
        final_lot = min(vol_max, rounded_lot)

    decimals = max(0, -int(np.floor(np.log10(step)))) if step < 1 else 0
    final_lot = round(final_lot, decimals)
    actual_risk = round(final_lot * one_lot_risk, 2)

    if actual_risk > MAX_RISK_USD:
        return 0.0, 0.0, f"CALCULATED RISK ${actual_risk:.2f} > ${MAX_RISK_USD:.2f} cap - SKIP"

    return final_lot, actual_risk, "OK"


class MT5AlphaTrader:
    def __init__(self, risk_per_trade_usd: float = DEFAULT_RISK_USD):
        self.risk_usd = risk_per_trade_usd
        self.active_positions: Dict[str, Any] = {}
        self.trade_cooldowns: Dict[str, Any] = {}
        self.governor = DailyRiskGovernor(initial_balance=4701.64, daily_pct=0.05, overall_pct=0.10)
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
        """Scans an asset for its assigned Alpha setup on confirmed H4 candle close."""
        symbol = item["symbol"]
        setup = item["setup"]
        exit_mode = item["exit_mode"]
        cluster = item["cluster"]

        rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H4, 0, 300)
        if rates is None or len(rates) < 250:
            return None

        df = pd.DataFrame(rates)
        c = df["close"].to_numpy()
        h = df["high"].to_numpy()
        l = df["low"].to_numpy()
        o = df["open"].to_numpy()
        t = df["time"].to_numpy()
        n = len(c)

        tick = mt5.symbol_info_tick(symbol)
        if not tick:
            return None

        prev = n - 2
        candle_close_ts = int(t[prev] + 4 * 3600)

        # 1. Post-Exit Cooldown (12 H4 bars = 48 hours)
        exit_ts = self.trade_cooldowns.get(f"{symbol}_exit_ts", 0)
        if tick.time < exit_ts + (POST_EXIT_COOLDOWN_BARS * 4 * 3600):
            remaining_h = round((exit_ts + POST_EXIT_COOLDOWN_BARS * 4 * 3600 - tick.time) / 3600, 1)
            logger.debug(f"⏳ [COOLDOWN ACTIVE] {symbol} in post-exit cooldown ({remaining_h}h remaining)")
            return None

        # 2. Bar Freshness Check: only trigger within 20 minutes of confirmed 4H close
        last_eval_bar = self.trade_cooldowns.get(f"{symbol}_last_bar", 0)
        if candle_close_ts == last_eval_bar:
            return None

        elapsed_since_close = tick.time - candle_close_ts
        if elapsed_since_close > 1200 or elapsed_since_close < 0:
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

        # Invariant: Institutional Pullback Origin Validation
        came_from_above = (o_bar >= ema89[prev] * 0.998) or (c[prev - 1] >= ema89[prev - 1] * 0.998)
        came_from_below = (o_bar <= ema89[prev] * 1.002) or (c[prev - 1] <= ema89[prev - 1] * 1.002)

        if setup == "canonical":
            if ema89[prev] > ema233[prev] and came_from_above:
                if lower_wick >= 0.25 and (l_bar <= ema89[prev] * 1.002 or l_bar <= ema55[prev] * 1.002) and (c_bar >= ema89[prev]):
                    sig = 1; shelf_type = "MID"
            elif ema89[prev] < ema233[prev] and came_from_below:
                if upper_wick >= 0.25 and (h_bar >= ema89[prev] * 0.998 or h_bar >= ema55[prev] * 0.998) and (c_bar <= ema89[prev]):
                    sig = -1; shelf_type = "MID"

        elif setup == "turbo_tri_tier":
            if ema89[prev] > ema233[prev] and c_bar >= ema233[prev] and came_from_above:
                if lower_wick >= 0.25:
                    if adx[prev] >= 22 and (l_bar <= ema34[prev] * 1.002 or l_bar <= ema55[prev] * 1.002) and (c_bar >= ema55[prev]):
                        sig = 1; shelf_type = "TURBO"
                    elif (l_bar <= ema89[prev] * 1.002) and (c_bar >= ema89[prev]):
                        sig = 1; shelf_type = "MID"
            elif ema89[prev] < ema233[prev] and c_bar <= ema233[prev] and came_from_below:
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
            sep = abs(ema89[prev] - ema233[prev]) / a_bar
            if 0.25 <= sep <= 2.50 and adx[prev] >= 20.0:
                if ema89[prev] > ema233[prev] and lower_wick >= 0.25 and came_from_above:
                    if (l_bar <= ema89[prev] * 1.002 or l_bar <= ema55[prev] * 1.002) and (c_bar >= ema89[prev]):
                        sig = 1; shelf_type = "MID"
                elif ema89[prev] < ema233[prev] and upper_wick >= 0.25 and came_from_below:
                    if (h_bar >= ema89[prev] * 0.998 or h_bar >= ema55[prev] * 0.998) and (c_bar <= ema89[prev]):
                        sig = -1; shelf_type = "MID"

        elif setup == "macro_reclaim_spring":
            # Model 2: Macro Dwell Reclaim Spring (12h-48h dwell under/above EMA 233 + EMA 89 shelf defense with 33% pinbar)
            if ema89[prev] > ema233[prev] and came_from_above and lower_wick >= 0.33:
                if (l_bar <= ema89[prev] * 1.002) and (c_bar >= ema89[prev]):
                    recent_below = sum(1 for b in range(max(0, prev - 35), prev) if c[b] < ema233[b])
                    if 3 <= recent_below <= 25:
                        sig = 1; shelf_type = "RECLAIM_SPRING"
            elif ema89[prev] < ema233[prev] and came_from_below and upper_wick >= 0.33:
                if (h_bar >= ema89[prev] * 0.998) and (c_bar <= ema89[prev]):
                    recent_above = sum(1 for b in range(max(0, prev - 35), prev) if c[b] > ema233[b])
                    if 3 <= recent_above <= 25:
                        sig = -1; shelf_type = "RECLAIM_SPRING"

        # Dual-Model Engine: If primary model produced no signal, check Model 2 for elite reclaim assets
        RECLAIM_SPRING_UNIVERSE = ["US30", "DOGUSD", "ETHUSD", "USDCNH", "BABA", "ADAUSD", "NZDJPY", "NZDSGD"]
        if sig == 0 and symbol in RECLAIM_SPRING_UNIVERSE:
            if ema89[prev] > ema233[prev] and came_from_above and lower_wick >= 0.33:
                if (l_bar <= ema89[prev] * 1.002) and (c_bar >= ema89[prev]):
                    recent_below = sum(1 for b in range(max(0, prev - 35), prev) if c[b] < ema233[b])
                    if 3 <= recent_below <= 25:
                        sig = 1; shelf_type = "RECLAIM_SPRING"
            elif ema89[prev] < ema233[prev] and came_from_below and upper_wick >= 0.33:
                if (h_bar >= ema89[prev] * 0.998) and (c_bar <= ema89[prev]):
                    recent_above = sum(1 for b in range(max(0, prev - 35), prev) if c[b] > ema233[b])
                    if 3 <= recent_above <= 25:
                        sig = -1; shelf_type = "RECLAIM_SPRING"

        # Mark bar as evaluated
        self.trade_cooldowns[f"{symbol}_last_bar"] = candle_close_ts
        self.save_state()

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

        # 3. Microstructure Spread vs Risk Friction Gate
        spread = (tick.ask - tick.bid) if (tick and tick.ask > 0 and tick.bid > 0) else 0.0
        spread_in_r = spread / risk_dist if risk_dist > 0 else 1.0
        if spread_in_r > 0.15:
            logger.info(
                f"⚠️ [FRICTION GATE BLOCKED] {symbol} spread {spread:.5f} is {spread_in_r:.1%} of 1R "
                f"(exceeds 15.0% institutional ceiling) - SKIP"
            )
            return None

        # Determine Target Price
        if shelf_type == "RECLAIM_SPRING":
            fixed_rr = 3.5
        else:
            is_fixed = exit_mode.startswith("fixed_")
            fixed_rr = float(exit_mode.split("_")[1]) if is_fixed else 3.0
        tp_price = (curr_price + fixed_rr * risk_dist) if sig == 1 else (curr_price - fixed_rr * risk_dist)

        # Calculate Lot Size for $10 USD Target Risk (with 1.1R hard cap)
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

        if order_res and order_res.retcode == mt5.TRADE_RETCODE_DONE:
            ticket = order_res.order
            logger.info(f"✅ [MT5 FILL SUCCESS] {symbol} Ticket: {ticket}")
        else:
            err_msg = order_res.comment if order_res else str(mt5.last_error())
            retcode = order_res.retcode if order_res else "None"
            logger.error(f"❌ [ORDER REJECTED - NOT RECORDED] {symbol} retcode {retcode}: {err_msg}")
            return False  # Strictly NO ticket fabrication, NO phantom OPEN state

        pos_entry = {
            "ticket": ticket,
            "symbol": symbol,
            "cluster": signal["cluster"],
            "setup": signal["setup"],
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

                target_rr = 3.5 if (exit_mode == "fixed_3.5" or setup == "macro_reclaim_spring") else 3.0
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

        # 2. Detect closed MT5 positions (strict reconciliation with broker)
        for sym, pos in list(self.active_positions.items()):
            if pos.get("ticket") not in live_tickets:
                logger.info(f"🏁 [BROKER POSITION CLOSED] {sym} Ticket: {pos.get('ticket')} resolved on MT5")
                self.active_positions.pop(sym, None)
                changed = True

        if changed:
            self.save_state()

    def manage_open_positions(self):
        """Monitors open positions, enforces targets/stops, and sends real broker trailing stops (Patch 2)."""
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
            if sl_p > 0 and ((side == 1 and current_price <= sl_p) or (side == -1 and current_price >= sl_p)):
                exited = True
                exit_reason = "SL_HIT"
                exit_r = (sl_p - entry_p) / rd if side == 1 else (entry_p - sl_p) / rd

            # Check TP Hit
            elif tp_p > 0 and ((side == 1 and current_price >= tp_p) or (side == -1 and current_price <= tp_p)):
                exited = True
                exit_reason = "TP_HIT"
                exit_r = (tp_p - entry_p) / rd if side == 1 else (entry_p - tp_p) / rd

            # Real Broker-Side Trailing Stop Management (step_trail)
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
                    info = mt5.symbol_info(symbol)
                    digits = info.digits if info else 5
                    new_sl_rounded = round(new_sl, digits)

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
                            f"New SL: {new_sl_rounded} (MFE: +{mfe}R)"
                        )
                        self.save_state()
                    else:
                        retcode = res.retcode if res else "None"
                        err_comment = res.comment if res else str(mt5.last_error())
                        logger.error(
                            f"⚠️ [BROKER TRAIL FAILED] {symbol} Ticket: {pos['ticket']} "
                            f"retcode {retcode}: {err_comment}"
                        )
                        # Do NOT update local state: local state must strictly mirror broker!

            if exited:
                logger.info(f"🏁 [POSITION CLOSED] {symbol} Reason: {exit_reason} | Result: {exit_r:+.2f}R")
                to_remove.append((symbol, exit_reason, exit_r, current_price))

        for sym, reason, r_mult, exit_price in to_remove:
            closed_pos = self.active_positions.pop(sym, None)
            if closed_pos:
                self._record_closed_trade(closed_pos, reason, r_mult, exit_price)
            self.save_state()

    def _record_closed_trade(self, pos: Dict[str, Any], reason: str, r_mult: float, exit_price: float):
        sym = pos["symbol"]
        # Trigger 12-bar post-exit cooldown
        self.trade_cooldowns[f"{sym}_exit_ts"] = int(time.time())
        self.save_state()

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
            "net_pnl_usd": round(r_mult * pos["actual_risk_usd"], 2),
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
            "max_risk_cap_usd": round(self.risk_usd * 1.10, 2),
            "concurrency_limit": MAX_CONCURRENT_POSITIONS,
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

        # 0. Daily Risk Governor Check
        from_ts = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        deals = mt5.history_deals_get(from_ts, datetime.now(timezone.utc))
        closed_pnl_today = sum(d.profit for d in deals) if deals else 0.0

        gov_status, gov_msg, used_loss = self.governor.check(acc.equity, closed_pnl_today, int(time.time()))
        if gov_status in ["HALT_DAILY_LIMIT", "HALT_OVERALL_FLOOR"]:
            logger.warning(f"🚨 [GOVERNOR ACTIVE] {gov_msg} Halting new entries.")
            # Still manage open positions to trail/close existing trades
            self.manage_open_positions()
            self.sync_broker_positions()
            self.emit_health_status(gov_status=gov_status, used_loss_today=used_loss)
            return

        max_concurrent = 1 if gov_status == "THROTTLE_NEW_ENTRIES" else MAX_CONCURRENT_POSITIONS

        # 1. Sync real-time positions directly from MT5 terminal
        self.sync_broker_positions()

        # 2. Manage existing positions first
        self.manage_open_positions()

        # 3. Check Global Concurrency Ceiling
        if len(self.active_positions) >= max_concurrent:
            logger.debug(f"Portfolio at concurrency ceiling ({len(self.active_positions)}/{max_concurrent}).")
            self.emit_health_status(gov_status=gov_status, used_loss_today=used_loss)
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
    logger.info("=" * 80)
    logger.info("STARTING FUNDEDNEXT MT5 ELITE ALPHA MONITOR WITH CRO RISK GOVERNANCE")
    logger.info(f"Target Risk per trade: ${DEFAULT_RISK_USD:.2f} USD | 1.1R Hard Cap: ${DEFAULT_RISK_USD*1.1:.2f} USD")
    logger.info(f"Max Concurrency: {MAX_CONCURRENT_POSITIONS} | Max per Cluster: {MAX_PER_CLUSTER}")
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
