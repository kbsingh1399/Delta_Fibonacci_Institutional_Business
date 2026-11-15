#!/usr/bin/env python3
"""terminal_server.py: Institutional Localhost Web Terminal & Indicator Monitor.

Serves an interactive institutional trading terminal at http://localhost:5000.
Provides real-time visibility into:
- 4H EMA 55, EMA 89, EMA 233, and ATR 14 across the entire universe
- Live mark prices, regime classification, and distance to Fibonacci shelves
- Active positions, dynamic step-trailing stops, and take-profit targets
- Portfolio heat, margin utilization, and kill-switch control
"""

from __future__ import annotations

import datetime
import http.server
import json
import logging
import math
import os
import socketserver
import sys
import threading
import time
import urllib.request
from typing import Any, Dict, List, Optional

import pandas as pd
import numpy as np

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE_DIR = os.path.join(BASE_DIR, "engine")
AUDIT_DIR = os.path.join(BASE_DIR, "audit")
if ENGINE_DIR not in sys.path:
    sys.path.insert(0, ENGINE_DIR)

from asset_universe_classifier import classify_asset, ALL_SECTORS
from delta_min_lot_sizer import fetch_delta_products
from cluster_risk_governor import get_asset_cluster, validate_new_trade_entry
from delta_live_scanner import fetch_recent_candles, calculate_ema, calculate_atr, scan_asset_setup

ACTIVE_POS_FILE = os.path.join(ENGINE_DIR, "active_positions.json")
PAPER_LEDGER_FILE = os.path.join(AUDIT_DIR, "paper_trading_ledger.csv")
KILL_SWITCH_FILE = os.path.join(BASE_DIR, "KILL_SWITCH")
PROD_API = "https://api.india.delta.exchange"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Accept": "application/json"}

PORT = 5000

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s: %(message)s")
logger = logging.getLogger("TerminalServer")


# -----------------------------------------------------------------------------
# Data Store & Worker Cache
# -----------------------------------------------------------------------------
class TerminalDataStore:
    def __init__(self):
        self.lock = threading.Lock()
        self.tickers: Dict[str, float] = {}
        self.indicators: List[Dict[str, Any]] = []
        self.last_indicator_update = 0.0
        self.last_ticker_update = 0.0
        self.products = fetch_delta_products()
        self.deployable_symbols = self._load_universe()
        self.scanning = False

    def _load_universe(self) -> List[str]:
        try:
            from universe_screener import screen_deployable_universe
            df = screen_deployable_universe(min_24h_turnover_usd=100000.0)
            return df["symbol"].tolist()
        except Exception:
            return list(self.products.keys())[:75]

    def update_tickers(self) -> None:
        url = f"{PROD_API}/v2/tickers"
        req = urllib.request.Request(url, headers=HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            res = data.get("result", [])
            new_tickers = {}
            for t in res:
                s = t.get("symbol")
                mp = t.get("mark_price")
                if s and mp:
                    try:
                        new_tickers[s] = float(mp)
                    except Exception:
                        pass
            with self.lock:
                self.tickers.update(new_tickers)
                self.last_ticker_update = time.time()
        except Exception as e:
            logger.debug(f"Failed to fetch live tickers: {e}")

    def refresh_indicators(self) -> None:
        with self.lock:
            if self.scanning:
                return
            self.scanning = True

        logger.info(f"Computing 4H indicators across {len(self.deployable_symbols)} assets...")
        new_indicators = []
        active_pos = self.get_active_positions()

        # Prioritize symbols
        for sym in self.deployable_symbols:
            try:
                df = fetch_recent_candles(sym, resolution="240", count=1200)
                if df is None or len(df) < 250:
                    continue

                # Live mark price
                with self.lock:
                    mark = self.tickers.get(sym, float(df["close"].iloc[-1]))

                # Inject live mark price into current forming candle to match TradingView live indicators
                if mark > 0:
                    df.loc[df.index[-1], "close"] = mark
                    if mark > df["high"].iloc[-1]:
                        df.loc[df.index[-1], "high"] = mark
                    if mark < df["low"].iloc[-1]:
                        df.loc[df.index[-1], "low"] = mark

                closes = df["close"]
                ema55 = calculate_ema(closes, 55).to_numpy()
                ema89 = calculate_ema(closes, 89).to_numpy()
                ema233 = calculate_ema(closes, 233).to_numpy()
                atr = calculate_atr(df, 14)

                prev = len(df) - 2
                # Use live forming bar indicator readings (matching TradingView 4H display)
                curr_89 = float(ema89[-1])
                curr_233 = float(ema233[-1])
                curr_55 = float(ema55[-1])
                curr_atr = float(atr[-1])

                regime = "BULLISH" if curr_89 > curr_233 else ("BEARISH" if curr_89 < curr_233 else "NEUTRAL")

                dist_89 = ((mark - curr_89) / curr_89) * 100.0 if curr_89 > 0 else 0.0
                dist_233 = ((mark - curr_233) / curr_233) * 100.0 if curr_233 > 0 else 0.0

                # Candle wick metrics
                o = float(df["open"].iloc[prev])
                h = float(df["high"].iloc[prev])
                l = float(df["low"].iloc[prev])
                c = float(df["close"].iloc[prev])
                rng = h - l
                wick_pct = 0.0
                if rng > 0:
                    if regime == "BULLISH":
                        wick_pct = (min(o, c) - l) / rng * 100.0
                    else:
                        wick_pct = (h - max(o, c)) / rng * 100.0

                # Candle timing metrics
                candle_start_ts = int(df["timestamp"].iloc[prev])
                candle_close_ts = candle_start_ts + 14400
                elapsed_since_close = int(time.time()) - candle_close_ts
                forming_close_ts = int(df["timestamp"].iloc[-1]) + 14400
                minutes_to_close = max(0, (forming_close_ts - int(time.time())) // 60)

                # Setup classification
                setup = scan_asset_setup(sym, tf="4H", products_dict=self.products, live_mark_price=mark)
                status = f"MONITORING [4H Close in {minutes_to_close}m]"
                status_color = "secondary"

                if setup:
                    candidate = {
                        "symbol": setup["symbol"],
                        "actual_risk_inr": setup["risk_inr"],
                        "margin_10x_inr": setup["margin_10x_inr"],
                        "recent_pct_change": setup["recent_pct_change"],
                        "action": setup["action"],
                        "cluster": setup["cluster"]
                    }
                    is_ok, reason = validate_new_trade_entry(candidate, active_pos, equity_inr=5000.0)
                    if not is_ok:
                        status = f"BLOCKED: {reason}"
                        status_color = "warning"
                    elif setup["sizing_status"] == "REJECTED_OVERSIZED":
                        status = f"BLOCKED: OVERSIZED MIN-LOT"
                        status_color = "warning"
                    elif elapsed_since_close <= 900:
                        # Setup is fresh (within 15 minutes of candle close)
                        status = f"READY: {setup['action']} ({setup['shelf']}) [15m Window Open]"
                        status_color = "success"
                    else:
                        hours_ago = elapsed_since_close / 3600.0
                        status = f"HISTORICAL: {setup['action']} ({setup['shelf']}) [Closed {hours_ago:.1f}h ago]"
                        status_color = "secondary"
                elif abs(dist_89) <= 0.6:
                    status = f"APPROACHING EMA 89 ({dist_89:+.2f}%) [Close in {minutes_to_close}m]"
                    status_color = "info"
                elif abs(dist_233) <= 0.6:
                    status = f"APPROACHING EMA 233 ({dist_233:+.2f}%) [Close in {minutes_to_close}m]"
                    status_color = "info"

                item = {
                    "symbol": sym,
                    "sector": classify_asset(sym),
                    "cluster": get_asset_cluster(sym),
                    "mark_price": round(mark, 4),
                    "ema_55": round(curr_55, 4),
                    "ema_89": round(curr_89, 4),
                    "ema_233": round(curr_233, 4),
                    "atr_14": round(curr_atr, 4),
                    "regime": regime,
                    "dist_ema89_pct": round(dist_89, 2),
                    "dist_ema233_pct": round(dist_233, 2),
                    "wick_pct": round(wick_pct, 1),
                    "status": status,
                    "status_color": status_color,
                    "last_updated": datetime.datetime.now().strftime("%H:%M:%S")
                }
                new_indicators.append(item)
            except Exception as e:
                logger.debug(f"Indicator calculation error on {sym}: {e}")

        # Sort: READY first, then by smallest distance to EMA 89
        new_indicators.sort(key=lambda x: (0 if "READY" in x["status"] else (1 if "APPROACHING" in x["status"] else 2), abs(x["dist_ema89_pct"])))

        with self.lock:
            self.indicators = new_indicators
            self.last_indicator_update = time.time()
            self.scanning = False

        logger.info(f"Updated {len(new_indicators)} asset indicators.")

    def get_active_positions(self) -> Dict[str, Any]:
        if os.path.exists(ACTIVE_POS_FILE):
            try:
                with open(ACTIVE_POS_FILE, "r") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def get_full_state(self) -> Dict[str, Any]:
        with self.lock:
            tickers_copy = dict(self.tickers)
            indicators_copy = list(self.indicators)
            last_ind_up = self.last_indicator_update

        active = self.get_active_positions()
        total_risk = 0.0
        total_margin = 0.0
        total_unrealized_pnl = 0.0

        pos_list = []
        for sym, p in active.items():
            mark = tickers_copy.get(sym, float(p.get("entry_price", 0)))
            is_long = "BUY" in str(p.get("side", "")).upper()
            entry = float(p.get("entry_price", mark))
            contracts = int(p.get("contracts", 1))
            cv = float(p.get("contract_value", 1.0))
            pnl = (mark - entry if is_long else entry - mark) * contracts * cv * 85.0
            total_unrealized_pnl += pnl

            risk = float(p.get("actual_risk_inr", 0))
            margin = float(p.get("margin_10x_inr", 0))
            total_risk += risk
            total_margin += margin

            stop = float(p.get("current_stop", 0))
            tp = float(p.get("target_2", 0))
            dist_to_stop = (((mark - stop) / mark * 100.0) if is_long else ((stop - mark) / mark * 100.0)) if mark > 0 else 0.0
            dist_to_tp = (((tp - mark) / mark * 100.0) if is_long else ((mark - tp) / mark * 100.0)) if mark > 0 else 0.0

            pos_list.append({
                "symbol": sym,
                "side": p.get("side"),
                "shelf": p.get("shelf"),
                "entry_price": entry,
                "mark_price": mark,
                "current_stop": stop,
                "target_2": tp,
                "contracts": contracts,
                "margin_inr": round(margin, 1),
                "risk_inr": round(risk, 1),
                "unrealized_pnl_inr": round(pnl, 2),
                "dist_to_stop_pct": round(dist_to_stop, 2),
                "dist_to_tp_pct": round(dist_to_tp, 2),
                "trail_stage": p.get("trail_stage", "INITIAL"),
                "mode": p.get("execution_mode", "demo")
            })

        kill_switch = os.path.exists(KILL_SWITCH_FILE)

        # Recent ledger trades
        recent_ledger = []
        if os.path.exists(PAPER_LEDGER_FILE):
            try:
                df_l = pd.read_csv(PAPER_LEDGER_FILE)
                df_l = df_l.fillna("")
                recent_ledger = df_l.tail(10).to_dict(orient="records")
            except Exception:
                pass

        return {
            "server_time_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "server_time_ist": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S IST"),
            "kill_switch_engaged": kill_switch,
            "equity_base_inr": 5000.0,
            "active_count": len(pos_list),
            "max_positions": 3,
            "total_risk_heat_inr": round(total_risk, 2),
            "max_risk_heat_inr": 150.0,
            "total_margin_inr": round(total_margin, 2),
            "max_margin_inr": 3000.0,
            "total_unrealized_pnl_inr": round(total_unrealized_pnl, 2),
            "positions": pos_list,
            "indicators": indicators_copy,
            "ledger": recent_ledger,
            "indicators_updated_seconds_ago": round(time.time() - last_ind_up, 1) if last_ind_up > 0 else -1
        }


STORE = TerminalDataStore()


# -----------------------------------------------------------------------------
# Background Scanner & Poller Thread
# -----------------------------------------------------------------------------
def background_poller():
    logger.info("Background poller initialized.")
    # Initial scan
    STORE.update_tickers()
    STORE.refresh_indicators()

    ticker_counter = 0
    while True:
        try:
            time.sleep(2)
            ticker_counter += 1
            STORE.update_tickers()

            # Refresh 4H indicators every 3 minutes (preventing Delta API rate-limit exhaustion)
            if ticker_counter >= 90:
                ticker_counter = 0
                STORE.refresh_indicators()
        except Exception as e:
            logger.debug(f"Poller loop error: {e}")
            time.sleep(5)


# -----------------------------------------------------------------------------
# HTTP Request Handler & REST API
# -----------------------------------------------------------------------------
class TerminalHTTPHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Silence routine request logging
        return

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self.serve_html()
        elif self.path == "/api/data":
            self.serve_json(STORE.get_full_state())
        elif self.path == "/api/kill_switch":
            self.serve_json({"engaged": os.path.exists(KILL_SWITCH_FILE)})
        else:
            self.send_error(404, "Not Found")

    def do_POST(self):
        if self.path == "/api/kill_switch/toggle":
            if os.path.exists(KILL_SWITCH_FILE):
                os.remove(KILL_SWITCH_FILE)
                logger.warning("Kill switch DISENGAGED via terminal UI.")
                self.serve_json({"engaged": False, "msg": "Kill switch disengaged"})
            else:
                with open(KILL_SWITCH_FILE, "w") as f:
                    f.write("ENGAGED_VIA_TERMINAL_UI\n")
                logger.critical("🚨 Kill switch ENGAGED via terminal UI.")
                self.serve_json({"engaged": True, "msg": "Kill switch engaged"})
        elif self.path == "/api/scan_now":
            t = threading.Thread(target=STORE.refresh_indicators, daemon=True)
            t.start()
            self.serve_json({"status": "scan_started"})
        else:
            self.send_error(404, "Not Found")

    def serve_json(self, data: Any):
        def sanitize(obj):
            if isinstance(obj, float):
                if math.isnan(obj) or math.isinf(obj):
                    return None
                return obj
            elif isinstance(obj, dict):
                return {k: sanitize(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [sanitize(v) for v in obj]
            return obj

        clean_data = sanitize(data)
        content = json.dumps(clean_data, allow_nan=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(content)

    def serve_html(self):
        html_path = os.path.join(ENGINE_DIR, "web_terminal", "index.html")
        if os.path.exists(html_path):
            with open(html_path, "rb") as f:
                content = f.read()
        else:
            content = b"<h1>Terminal interface loading...</h1>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)


def run_terminal_server():
    os.makedirs(os.path.join(ENGINE_DIR, "web_terminal"), exist_ok=True)
    poller_thread = threading.Thread(target=background_poller, daemon=True)
    poller_thread.start()

    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer(("", PORT), TerminalHTTPHandler) as httpd:
        logger.info(f"==================================================================")
        logger.info(f"  DELTA FIBONACCI INSTITUTIONAL TERMINAL ACTIVE")
        logger.info(f"  URL: http://localhost:{PORT}  (or http://127.0.0.1:{PORT})")
        logger.info(f"  Live Indicators: 4H EMA 55 | EMA 89 | EMA 233 | ATR 14")
        logger.info(f"==================================================================")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            logger.info("Terminal server shutting down.")


if __name__ == "__main__":
    run_terminal_server()
