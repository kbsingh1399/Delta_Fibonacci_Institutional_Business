#!/usr/bin/env python3
"""delta_trader_monitor.py: Institutional Trade Execution & Live Market Monitor.

Target Venue: Delta Exchange India (api.india.delta.exchange / cdn-ind.testnet.deltaex.org)
Base Capital: ₹5,000 INR | 10X Isolated Margin | 1R = ₹50 INR (₹55 cap)
Max Positions: 3 simultaneous trades | Max Portfolio Heat: 3.0R (₹150) | Max Margin: 60% (₹3,000)

Execution Modes:
  - demo:  Places REST bracket orders on Delta Testnet (if product exists) and logs paper fills
  - paper: Full shadow execution on live Delta India production feed with local ledger
  - live:  Places real orders on Delta India production (requires funded account and --live-confirm)
"""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import os
import sys
import tempfile
import time
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE_DIR = os.path.join(BASE_DIR, "engine")
AUDIT_DIR = os.path.join(BASE_DIR, "audit")
if ENGINE_DIR not in sys.path:
    sys.path.insert(0, ENGINE_DIR)

from asset_universe_classifier import classify_asset, get_sector_display_name, ALL_SECTORS
from delta_min_lot_sizer import calculate_delta_order_sizing, fetch_delta_products
from cluster_risk_governor import validate_new_trade_entry, get_asset_cluster
from delta_order_executor import DeltaOrderExecutor, ExecutorError

ACTIVE_POS_FILE = os.path.join(ENGINE_DIR, "active_positions.json")
PAPER_LEDGER_FILE = os.path.join(AUDIT_DIR, "paper_trading_ledger.csv")
KILL_SWITCH_FILE = os.path.join(BASE_DIR, "KILL_SWITCH")

PROD_API = "https://api.india.delta.exchange"
TESTNET_API = "https://cdn-ind.testnet.deltaex.org"
HEADERS = {"User-Agent": "delta-fibonacci-monitor/1.0", "Accept": "application/json"}

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("DeltaMonitor")


COOLDOWNS_FILE = os.path.join(AUDIT_DIR, "trade_cooldowns.json")


def load_trade_cooldowns() -> Dict[str, Any]:
    """Loads symbol cooldowns enforcing the 12-bar post-exit invariant."""
    if os.path.exists(COOLDOWNS_FILE):
        for _ in range(5):
            try:
                with open(COOLDOWNS_FILE, "r") as f:
                    return json.load(f)
            except Exception:
                time.sleep(0.05)
    return {}


def record_trade_cooldown(symbol: str, candle_ts: Optional[int] = None, reason: str = "") -> None:
    """Records post-exit timestamp to enforce 12-bar cooldown (no immediate re-entry)."""
    cooldowns = load_trade_cooldowns()
    now_ts = int(time.time())
    cooldowns[symbol] = {
        "exit_time": now_ts,
        "exit_time_str": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "candle_ts": candle_ts or now_ts,
        "reason": reason
    }
    dir_name = os.path.dirname(COOLDOWNS_FILE)
    os.makedirs(dir_name, exist_ok=True)
    tmp_path = ""
    for attempt in range(5):
        try:
            fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
            with open(fd, "w") as f:
                json.dump(cooldowns, f, indent=2)
            os.replace(tmp_path, COOLDOWNS_FILE)
            break
        except Exception:
            time.sleep(0.05)
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass


def load_active_positions() -> Dict[str, Any]:
    if os.path.exists(ACTIVE_POS_FILE):
        for _ in range(5):
            try:
                with open(ACTIVE_POS_FILE, "r") as f:
                    return json.load(f)
            except Exception:
                time.sleep(0.05)
    return {}


def save_active_positions(pos_dict: Dict[str, Any]) -> None:
    dir_name = os.path.dirname(ACTIVE_POS_FILE)
    os.makedirs(dir_name, exist_ok=True)
    tmp_path = ""
    for attempt in range(5):
        try:
            fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
            with open(fd, "w") as f:
                json.dump(pos_dict, f, indent=2)
            os.replace(tmp_path, ACTIVE_POS_FILE)
            return
        except Exception as e:
            if attempt < 4:
                time.sleep(0.05 * (attempt + 1))
            else:
                logger.error(f"Failed to atomic-write {ACTIVE_POS_FILE} after 5 attempts: {e}")
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass


def append_ledger(row: Dict[str, Any]) -> None:
    os.makedirs(AUDIT_DIR, exist_ok=True)
    fieldnames = [
        "timestamp", "event", "symbol", "side", "shelf", "entry_price",
        "exit_price", "stop_price", "target_1", "target_2", "contracts",
        "margin_10x_inr", "risk_inr", "realized_pnl_inr", "r_multiple",
        "execution_mode", "notes"
    ]
    df_row = pd.DataFrame([row])
    for col in fieldnames:
        if col not in df_row.columns:
            df_row[col] = ""
    df_row = df_row[fieldnames]

    header = not os.path.exists(PAPER_LEDGER_FILE)
    df_row.to_csv(PAPER_LEDGER_FILE, mode="a", header=header, index=False)


def fetch_live_tickers(base_url: str = PROD_API) -> Dict[str, float]:
    """Fetches real-time mark prices across all instruments in a single call."""
    url = f"{base_url}/v2/tickers"
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        res = data.get("result", [])
        return {
            t["symbol"]: float(t.get("mark_price") or 0.0)
            for t in res if t.get("symbol")
        }
    except Exception as e:
        logger.warning(f"Error fetching live tickers from {url}: {e}")
        return {}


def check_kill_switch() -> bool:
    candidates = [
        KILL_SWITCH_FILE,
        os.path.join(ENGINE_DIR, "KILL_SWITCH"),
        os.path.abspath("KILL_SWITCH")
    ]
    for p in candidates:
        if os.path.exists(p):
            return True
    return False


class DeltaTraderMonitor:
    def __init__(self,
                 mode: str = "demo",
                 equity_inr: float = 5000.0,
                 risk_pct: float = 0.01,
                 leverage: float = 10.0,
                 tf: str = "4H",
                 poll_interval_sec: int = 60,
                 testnet_key: Optional[str] = None,
                 testnet_secret: Optional[str] = None,
                 prod_key: Optional[str] = None,
                 prod_secret: Optional[str] = None):
        self.mode = mode.lower()  # 'demo', 'paper', or 'live'
        self.equity_inr = equity_inr
        self.risk_pct = risk_pct
        self.leverage = leverage
        self.tf = tf
        self.poll_interval = poll_interval_sec

        # Auto-load local credentials from ~/.delta-exchange-mcp/config.env if available
        cfg_path = os.path.expanduser("~/.delta-exchange-mcp/config.env")
        if os.path.exists(cfg_path):
            try:
                with open(cfg_path) as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, v = line.split("=", 1)
                            clean_k = k.strip()
                            clean_v = v.strip().strip('"').strip("'")
                            if clean_k not in os.environ:
                                os.environ[clean_k] = clean_v
            except Exception:
                pass

        # Determine credentials & executor
        self.testnet_key = testnet_key or os.environ.get("DELTA_TESTNET_API_KEY", "LocHjRoKYpkf8CZG130tdnWK4OfgQb")
        self.testnet_secret = testnet_secret or os.environ.get("DELTA_TESTNET_API_SECRET", "Tkyxf3xrOvl8QSFvsK91XuosSq0VUxth1PikjtxsPLDfBH1Tf0KdPDHzvglX")
        self.prod_key = (
            prod_key
            or os.environ.get("DELTA_LIVE_PROD_KEY")
            or os.environ.get("DELTA_API_KEY", "")
        )
        self.prod_secret = (
            prod_secret
            or os.environ.get("DELTA_LIVE_PROD_SECRET")
            or os.environ.get("DELTA_API_SECRET", "")
        )

        self.executor: Optional[DeltaOrderExecutor] = None
        if self.mode == "demo":
            self.executor = DeltaOrderExecutor(
                api_key=self.testnet_key,
                api_secret=self.testnet_secret,
                base_url=TESTNET_API,
                live=True
            )
        elif self.mode == "live":
            self.executor = DeltaOrderExecutor(
                api_key=self.prod_key,
                api_secret=self.prod_secret,
                base_url=PROD_API,
                live=True
            )

        self.products = fetch_delta_products()
        self.deployable_symbols = self._get_deployable_universe()
        self.testnet_products = self._get_testnet_products()

    def _get_deployable_universe(self) -> List[str]:
        try:
            from universe_screener import screen_deployable_universe
            df_deploy = screen_deployable_universe(min_24h_turnover_usd=100000.0)
            all_syms = df_deploy["symbol"].tolist()
            # Alpha basket: exclude Crypto Majors tape anchor
            return [s for s in all_syms if get_asset_cluster(s) != "CRYPTO_MAJORS"]
        except Exception as e:
            logger.warning(f"Universe screener failed ({e}); falling back to cached catalog")
            return [s for s in self.products.keys() if get_asset_cluster(s) != "CRYPTO_MAJORS"]

    def _get_testnet_products(self) -> Dict[str, dict]:
        try:
            url = f"{TESTNET_API}/v2/products"
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8")).get("result", [])
            return {p["symbol"]: p for p in data if p.get("contract_type") == "perpetual_futures"}
        except Exception:
            return {}

    def monitor_open_positions(self, ticker_map: Dict[str, float]) -> None:
        """Inspects live mark prices and venue order status; synchronizes TP, SL, and Trailing Stops."""
        active = load_active_positions()
        if not active:
            return

        modified = False
        to_close = []

        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # In live mode with executor: reconcile with Delta Exchange venue positions
        venue_positions_map = {}
        if self.mode == "live" and self.executor:
            try:
                v_positions = self.executor.get_margined_positions()
                for vp in v_positions:
                    psym = vp.get("product_symbol")
                    if psym:
                        venue_positions_map[psym] = vp
            except Exception as e:
                logger.debug(f"Venue position reconciliation check error: {e}")

        for sym, pos in list(active.items()):
            # 0. Check if this position is a live trade and has been closed on Delta Exchange
            if self.mode == "live" and self.executor and venue_positions_map:
                if sym not in venue_positions_map or int(venue_positions_map[sym].get("size", 0)) == 0:
                    # Delta Exchange venue closed the position (TP or SL bracket fill)!
                    entry = float(pos["entry_price"])
                    stop = float(pos["current_stop"])
                    t2 = float(pos["target_2"])
                    mark = ticker_map.get(sym, entry)
                    is_long = "BUY" in pos["side"].upper()
                    # Determine whether TP or SL filled based on distance to targets
                    dist_to_tp = abs(mark - t2)
                    dist_to_sl = abs(mark - stop)
                    exit_price = t2 if dist_to_tp <= dist_to_sl else stop
                    event_type = "TAKE_PROFIT" if dist_to_tp <= dist_to_sl else "STOP_LOSS"
                    contracts = int(pos["contracts"])
                    cv = float(pos.get("contract_value", 1.0))
                    risk_inr = float(pos["actual_risk_inr"])
                    realized_pnl = (exit_price - entry if is_long else entry - exit_price) * contracts * cv * 85.0
                    r_mult = realized_pnl / risk_inr if risk_inr > 0 else (2.5 if event_type == "TAKE_PROFIT" else -1.0)
                    logger.info(f"🔄 [VENUE FILL DETECTED] {sym} closed on Delta Exchange via bracket {event_type} | Realized PnL: ₹{realized_pnl:+.2f} ({r_mult:+.2f}R)")
                    append_ledger({
                        "timestamp": now_str,
                        "event": event_type,
                        "symbol": sym,
                        "side": pos["side"],
                        "shelf": pos.get("shelf", ""),
                        "entry_price": entry,
                        "exit_price": exit_price,
                        "stop_price": stop,
                        "target_1": pos["target_1"],
                        "target_2": t2,
                        "contracts": contracts,
                        "margin_10x_inr": pos.get("margin_10x_inr", 0),
                        "risk_inr": risk_inr,
                        "realized_pnl_inr": round(realized_pnl, 2),
                        "r_multiple": round(r_mult, 2),
                        "execution_mode": pos.get("execution_mode", self.mode),
                        "notes": f"Delta Exchange bracket fill confirmed (Venue Order: {pos.get('venue_order_id')})"
                    })
                    record_trade_cooldown(sym, reason=f"VENUE_{event_type}")
                    to_close.append(sym)
                    continue

            mark = ticker_map.get(sym)
            if not mark or mark <= 0:
                continue

            side = pos["side"].upper()
            entry = float(pos["entry_price"])
            stop = float(pos["current_stop"])
            t1 = float(pos["target_1"])
            t2 = float(pos["target_2"])
            contracts = int(pos["contracts"])
            cv = float(pos.get("contract_value", 1.0))
            risk_inr = float(pos["actual_risk_inr"])
            trail_stage = pos.get("trail_stage", "INITIAL")

            # Update highest/lowest favorable mark
            if "BUY" in side:
                pos["highest_favorable"] = max(pos.get("highest_favorable", entry), mark)
            else:
                pos["lowest_favorable"] = min(pos.get("lowest_favorable", entry), mark)

            is_long = "BUY" in side

            # 1. Check Take Profit Hit (Target 2 or full target)
            tp_hit = (mark >= t2) if is_long else (mark <= t2)
            if tp_hit:
                realized_pnl = (t2 - entry if is_long else entry - t2) * contracts * cv * 85.0
                # Maker rebate capture (+0.050R friction advantage)
                realized_pnl += (0.0002 * t2 * contracts * cv * 85.0)
                r_mult = realized_pnl / risk_inr if risk_inr > 0 else 2.5
                logger.info(f"🏆 [TAKE PROFIT HIT] {sym} | Exit: {t2} | PnL: +₹{realized_pnl:.2f} (+{r_mult:.2f}R)")
                append_ledger({
                    "timestamp": now_str,
                    "event": "TAKE_PROFIT",
                    "symbol": sym,
                    "side": side,
                    "shelf": pos.get("shelf", ""),
                    "entry_price": entry,
                    "exit_price": t2,
                    "stop_price": stop,
                    "target_1": t1,
                    "target_2": t2,
                    "contracts": contracts,
                    "margin_10x_inr": pos.get("margin_10x_inr", 0),
                    "risk_inr": risk_inr,
                    "realized_pnl_inr": round(realized_pnl, 2),
                    "r_multiple": round(r_mult, 2),
                    "execution_mode": pos.get("execution_mode", self.mode),
                    "notes": "Target 2 Maker Limit TP filled with maker rebate"
                })
                # Cancel orphaned venue SL order if still open
                if self.mode == "live" and self.executor and pos.get("sl_order_id"):
                    prod_id = self.products.get(sym, {}).get("id")
                    try:
                        self.executor.cancel_order(pos["sl_order_id"], prod_id)
                        logger.info(f"Cancelled venue SL order {pos['sl_order_id']} for {sym}")
                    except Exception as e:
                        logger.debug(f"SL cancel note: {e}")
                record_trade_cooldown(sym, reason="TAKE_PROFIT")
                to_close.append(sym)
                continue

            # 2. Check Stop Loss Hit
            sl_hit = (mark <= stop) if is_long else (mark >= stop)
            if sl_hit:
                realized_pnl = (stop - entry if is_long else entry - stop) * contracts * cv * 85.0
                # Taker fee deduction on stop exit
                realized_pnl -= (0.0005 * stop * contracts * cv * 85.0)
                r_mult = realized_pnl / risk_inr if risk_inr > 0 else -1.0
                logger.warning(f"🛑 [STOP LOSS HIT] {sym} | Exit: {stop} | PnL: ₹{realized_pnl:.2f} ({r_mult:.2f}R)")
                append_ledger({
                    "timestamp": now_str,
                    "event": "STOP_LOSS",
                    "symbol": sym,
                    "side": side,
                    "shelf": pos.get("shelf", ""),
                    "entry_price": entry,
                    "exit_price": stop,
                    "stop_price": stop,
                    "target_1": t1,
                    "target_2": t2,
                    "contracts": contracts,
                    "margin_10x_inr": pos.get("margin_10x_inr", 0),
                    "risk_inr": risk_inr,
                    "realized_pnl_inr": round(realized_pnl, 2),
                    "r_multiple": round(r_mult, 2),
                    "execution_mode": pos.get("execution_mode", self.mode),
                    "notes": "Stop loss breach executed"
                })
                # Cancel orphaned venue TP order if still open
                if self.mode == "live" and self.executor and pos.get("tp_order_id"):
                    prod_id = self.products.get(sym, {}).get("id")
                    try:
                        self.executor.cancel_order(pos["tp_order_id"], prod_id)
                        logger.info(f"Cancelled venue TP order {pos['tp_order_id']} for {sym}")
                    except Exception as e:
                        logger.debug(f"TP cancel note: {e}")
                record_trade_cooldown(sym, reason="STOP_LOSS")
                to_close.append(sym)
                continue

            # 3. Dynamic Step Trail (Move Stop to Break-Even at 1.5R target)
            step_trail_reached = (mark >= t1) if is_long else (mark <= t1)
            if step_trail_reached and trail_stage == "INITIAL":
                pos["current_stop"] = entry
                pos["trail_stage"] = "BREAK_EVEN_LOCKED"
                modified = True
                logger.info(f"🛡️ [RISK TIGHTENED] {sym} reached 1.5R target ({t1}) -> Stop moved to Break-Even ({entry})")
                # Synchronize venue stop order
                if self.mode == "live" and self.executor and pos.get("sl_order_id"):
                    prod_id = self.products.get(sym, {}).get("id")
                    if prod_id:
                        try:
                            self.executor.edit_order(order_id=pos["sl_order_id"], product_id=int(prod_id), stop_price=entry)
                            logger.info(f"🛡️ [VENUE BRACKET UPDATED] {sym} SL Order {pos['sl_order_id']} moved to Break-Even ({entry}) on Delta Exchange")
                        except Exception as edit_err:
                            logger.warning(f"Could not update venue SL order {pos['sl_order_id']}: {edit_err}")
                append_ledger({
                    "timestamp": now_str,
                    "event": "STEP_TRAIL_BE",
                    "symbol": sym,
                    "side": side,
                    "shelf": pos.get("shelf", ""),
                    "entry_price": entry,
                    "exit_price": "",
                    "stop_price": entry,
                    "target_1": t1,
                    "target_2": t2,
                    "contracts": contracts,
                    "margin_10x_inr": pos.get("margin_10x_inr", 0),
                    "risk_inr": risk_inr,
                    "realized_pnl_inr": 0.0,
                    "r_multiple": 0.0,
                    "execution_mode": pos.get("execution_mode", self.mode),
                    "notes": f"Moved stop to BE {entry} as price reached {t1}"
                })

        for sym in to_close:
            del active[sym]
            modified = True

        if modified:
            save_active_positions(active)

    def scan_and_punch_trades(self, ticker_map: Dict[str, float]) -> List[dict]:
        """Scans the Alpha Basket and punches approved setups into active positions."""
        from delta_live_scanner import scan_asset_setup

        active = load_active_positions()
        simulated_active = dict(active)
        punched_trades = []

        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cooldowns = load_trade_cooldowns()
        now_ts = int(time.time())
        cooldown_sec = 12 * 4 * 3600  # 12 4H bars = 48 hours cooldown

        for sym in self.deployable_symbols:
            # Skip if already holding a position
            if sym in simulated_active:
                continue

            # Enforce 12-bar post-exit cooldown invariant
            cd = cooldowns.get(sym)
            if cd:
                exit_time = cd.get("exit_time", 0)
                if (now_ts - exit_time) < cooldown_sec:
                    continue

            try:
                setup = scan_asset_setup(
                    sym, tf=self.tf, equity_inr=self.equity_inr,
                    risk_pct=self.risk_pct, leverage=self.leverage,
                    products_dict=self.products,
                    live_mark_price=ticker_map.get(sym),
                    max_bar_age_sec=900  # 15 minutes freshness gate
                )
                if not setup:
                    continue

                # Prepare candidate for Cluster Risk Governor
                candidate_order = {
                    "symbol": setup["symbol"],
                    "actual_risk_inr": setup["risk_inr"],
                    "margin_10x_inr": setup["margin_10x_inr"],
                    "recent_pct_change": setup["recent_pct_change"],
                    "action": setup["action"],
                    "cluster": setup["cluster"]
                }
                is_gov_ok, gov_reason = validate_new_trade_entry(
                    candidate_order,
                    simulated_active,
                    equity_inr=self.equity_inr
                )

                is_ready = is_gov_ok and setup["sizing_status"] != "REJECTED_OVERSIZED"

                if not is_ready:
                    continue

                # EXECUTE / PUNCH TRADE
                sym_side = "BUY" if "BUY" in setup["action"] else "SELL"
                venue_order_id = "LOCAL-PAPER"

                # Check if asset is supported on connected venue executor
                cid = f"FIB4H_{int(time.time())}_{sym}"
                if self.mode == "demo" and self.executor and sym in self.testnet_products:
                    try:
                        logger.info(f"Sending real Testnet order for {sym} to Delta Demo API...")
                        res = self.executor.place_bracket_order(
                            symbol=sym,
                            side=sym_side.lower(),
                            contracts=setup["contracts"],
                            stop_price=setup["stop"],
                            entry_type="limit",
                            limit_price=setup["entry"],
                            take_profit_price=setup["t2"],
                            client_order_id=cid
                        )
                        venue_order_id = str(res.get("result", {}).get("id", "TESTNET-FILLED"))
                        logger.info(f"Testnet bracket order accepted: {venue_order_id}")
                    except Exception as e:
                        logger.warning(f"Testnet order failed ({e}); falling back to local paper fill")
                        venue_order_id = "DEMO-FALLBACK"

                elif self.mode == "live" and self.executor:
                    try:
                        logger.warning(f"🚨 SENDING LIVE PRODUCTION ORDER FOR {sym}...")
                        prod_id = self.products.get(sym, {}).get("id")
                        safe_lev = setup.get("effective_leverage", self.leverage)
                        if prod_id:
                            try:
                                self.executor.set_leverage(int(prod_id), safe_lev)
                                logger.info(f"Set {sym} leverage to safe {safe_lev}X (guaranteeing SL before Liq)")
                            except Exception as lev_err:
                                if "max_leverage_exceeded" in str(lev_err):
                                    try:
                                        self.executor.set_leverage(int(prod_id), 20)
                                        logger.info(f"Clamped {sym} leverage to venue maximum 20X")
                                    except Exception as e2:
                                        logger.error(f"Failed to set clamped 20X leverage for {sym}: {e2}. ABORTING order dispatch.")
                                        continue
                                else:
                                    logger.error(f"Could not set {safe_lev}X leverage for {sym}: {lev_err}. ABORTING order dispatch.")
                                    continue
                        res = self.executor.place_bracket_order(
                            symbol=sym,
                            side=sym_side.lower(),
                            contracts=setup["contracts"],
                            stop_price=setup["stop"],
                            entry_type="limit",
                            limit_price=setup["entry"],
                            take_profit_price=setup["t2"],
                            client_order_id=cid
                        )
                        venue_order_id = str(res.get("result", {}).get("id", "LIVE-ORDER"))
                        logger.info(f"Live order accepted: {venue_order_id}")
                    except Exception as e:
                        logger.error(f"Live order execution failed: {e}")
                        continue

                # Record in active positions
                position_record = {
                    "symbol": sym,
                    "side": setup["action"],
                    "shelf": setup["shelf"],
                    "entry_price": setup["entry"],
                    "current_stop": setup["stop"],
                    "original_stop": setup["stop"],
                    "target_1": setup["t1"],
                    "target_2": setup["t2"],
                    "contracts": setup["contracts"],
                    "contract_value": setup["contract_value"],
                    "underlying_units": setup["underlying_units"],
                    "margin_10x_inr": setup["margin_10x_inr"],
                    "actual_risk_inr": setup["risk_inr"],
                    "cluster": setup["cluster"],
                    "tf": setup["tf"],
                    "entry_time": now_str,
                    "trail_stage": "INITIAL",
                    "venue_order_id": venue_order_id,
                    "entry_order_id": venue_order_id,
                    "client_order_id": cid,
                    "effective_leverage": setup.get("effective_leverage", self.leverage),
                    "execution_mode": self.mode
                }
                active[sym] = position_record
                simulated_active[sym] = {
                    "side": setup["action"],
                    "actual_risk_inr": setup["risk_inr"],
                    "margin_10x_inr": setup["margin_10x_inr"],
                    "cluster": setup["cluster"]
                }
                save_active_positions(active)

                # Record in paper trading ledger
                append_ledger({
                    "timestamp": now_str,
                    "event": "ENTRY",
                    "symbol": sym,
                    "side": setup["action"],
                    "shelf": setup["shelf"],
                    "entry_price": setup["entry"],
                    "exit_price": "",
                    "stop_price": setup["stop"],
                    "target_1": setup["t1"],
                    "target_2": setup["t2"],
                    "contracts": setup["contracts"],
                    "margin_10x_inr": setup["margin_10x_inr"],
                    "risk_inr": setup["risk_inr"],
                    "realized_pnl_inr": 0.0,
                    "r_multiple": 0.0,
                    "execution_mode": self.mode,
                    "notes": f"Punched {setup['action']} @ {setup['entry']} (Order ID: {venue_order_id})"
                })

                logger.info(f"🎯 [TRADE PUNCHED] {sym} {setup['action']} @ {setup['entry']} | Lots: {setup['contracts']} | Stop: {setup['stop']} | TP: {setup['t2']} | Risk: ₹{setup['risk_inr']:.1f} ({venue_order_id})")
                punched_trades.append(setup)

            except Exception as e:
                logger.debug(f"Scan error on {sym}: {e}")
                continue

        return punched_trades

    def print_status_dashboard(self, ticker_map: Dict[str, float]) -> None:
        active = load_active_positions()
        total_risk = sum(p["actual_risk_inr"] for p in active.values())
        total_margin = sum(p["margin_10x_inr"] for p in active.values())
        total_unrealized_pnl = 0.0

        print("\n" + "=" * 115)
        print(f"DELTA EXCHANGE INDIA INSTITUTIONAL TRADER | MODE: {self.mode.upper()} | BASE CAPITAL: ₹{self.equity_inr:,.0f}")
        print(f"Active Trades: {len(active)}/3 | Total Risk Heat: ₹{total_risk:.1f}/₹150 ({(total_risk/self.equity_inr)*100:.1f}%) | Margin: ₹{total_margin:.1f}/₹3,000 ({(total_margin/self.equity_inr)*100:.1f}%)")
        print("=" * 115)

        if not active:
            print("  No active open positions. Monitoring market feed for Tiered Fib 89/233 setups...")
        else:
            rows = []
            for sym, pos in active.items():
                mark = ticker_map.get(sym, float(pos["entry_price"]))
                is_long = "BUY" in pos["side"].upper()
                pnl = (mark - pos["entry_price"] if is_long else pos["entry_price"] - mark) * pos["contracts"] * pos["contract_value"] * 85.0
                total_unrealized_pnl += pnl
                rows.append({
                    "Symbol": sym,
                    "Side": pos["side"],
                    "Entry": pos["entry_price"],
                    "Mark": mark,
                    "Stop": pos["current_stop"],
                    "TP_2": pos["target_2"],
                    "Lots": pos["contracts"],
                    "Margin": f"₹{pos['margin_10x_inr']:.1f}",
                    "Risk": f"₹{pos['actual_risk_inr']:.1f}",
                    "Unrealized PnL": f"₹{pnl:+.2f}",
                    "Trail": pos.get("trail_stage", "INIT")
                })
            df_disp = pd.DataFrame(rows)
            print(df_disp.to_string(index=False))
            print(f"\n  Total Unrealized Portfolio PnL: ₹{total_unrealized_pnl:+.2f} INR")

        print("-" * 115)

    def run_cycle(self) -> None:
        """Executes one complete monitoring and trade-punching cycle."""
        if check_kill_switch():
            logger.critical("🚨 KILL SWITCH DETECTED! Trading halted immediately. No orders sent.")
            return

        ticker_map = fetch_live_tickers(PROD_API)
        if not ticker_map:
            logger.warning("No tickers returned from Delta API. Skipping cycle.")
            return

        # 1. Monitor existing positions
        self.monitor_open_positions(ticker_map)

        # 2. Scan and punch new trades if capacity exists
        active = load_active_positions()
        if len(active) < 3:
            self.scan_and_punch_trades(ticker_map)

        # 3. Print institutional status dashboard
        self.print_status_dashboard(ticker_map)

    def run_loop(self) -> None:
        """Continuous execution loop."""
        logger.info(f"Starting Delta Institutional Trader daemon in {self.mode.upper()} mode...")
        logger.info(f"Monitoring {len(self.deployable_symbols)} Alpha Basket assets on {self.tf} timeframe...")
        try:
            while True:
                self.run_cycle()
                time.sleep(self.poll_interval)
        except KeyboardInterrupt:
            logger.info("Trader monitor stopped by user.")


def main():
    parser = argparse.ArgumentParser(description="Delta Exchange India Trade & Monitor Engine")
    parser.add_argument("--mode", choices=["demo", "paper", "live"], default="demo", help="Execution mode")
    parser.add_argument("--capital", type=float, default=5000.0, help="Base capital in INR")
    parser.add_argument("--risk", type=float, default=0.01, help="Risk per trade (0.01 for 1R = ₹50)")
    parser.add_argument("--leverage", type=float, default=10.0, help="Leverage multiplier (10X default)")
    parser.add_argument("--tf", choices=["4H", "1H"], default="4H", help="Scanning timeframe")
    parser.add_argument("--interval", type=int, default=60, help="Polling interval in seconds")
    parser.add_argument("--once", action="store_true", help="Run a single cycle and exit")
    parser.add_argument("--live-confirm", action="store_true", help="Mandatory flag to confirm real live trading")
    args = parser.parse_args()

    if args.mode == "live" and not args.live_confirm:
        print("\n[ERROR] To trade with real live money, you must explicitly supply --live-confirm.")
        sys.exit(1)

    monitor = DeltaTraderMonitor(
        mode=args.mode,
        equity_inr=args.capital,
        risk_pct=args.risk,
        leverage=args.leverage,
        tf=args.tf,
        poll_interval_sec=args.interval
    )

    if args.once:
        monitor.run_cycle()
    else:
        monitor.run_loop()


if __name__ == "__main__":
    main()
