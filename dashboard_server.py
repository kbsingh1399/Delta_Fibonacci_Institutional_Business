#!/usr/bin/env python3
"""dashboard_server.py: Single-File Localhost Web Server & Real-time Live Radar
for MT5 Fibonacci EMA 89/233 Rejection & Proximity Calculations.

Runs on http://localhost:8088
Features:
- Live streaming calculations: EMA 34, 55, 89, 233, ATR(14), ADX(14).
- Real-time Proximity Radar: Distance to EMA 89 & 233 in ATR units & points.
- Visual Wick Geometry: Upper wick %, Lower wick %, Body %, Bull/Bear direction.
- Active MT5 Account & Positions Tracker.
- Fast, asynchronous background cache with 2-second client auto-refresh.
"""

import os
import sys
import time
import json
import threading
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
import pandas as pd
import numpy as np
import MetaTrader5 as mt5

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

try:
    from engine.mt5_alpha_trader_monitor import (
        OPTIMAL_ALPHA_BASKET,
        calculate_ema,
        calculate_atr,
        compute_adx,
    )
except ImportError:
    OPTIMAL_ALPHA_BASKET = [
        {"symbol": "US30", "cluster": "Indices", "setup": "wyckoff_institutional_filtered"},
        {"symbol": "XAUUSD", "cluster": "Commodity_Energy", "setup": "canonical"},
        {"symbol": "USDJPY", "cluster": "Forex_JPY", "setup": "turbo_tri_tier"},
        {"symbol": "ETHUSD", "cluster": "Crypto", "setup": "wyckoff_institutional_filtered"},
        {"symbol": "DOGUSD", "cluster": "Crypto", "setup": "canonical"},
    ]

# Global Data Cache
LIVE_DATA_CACHE = {
    "timestamp": datetime.now(timezone.utc).isoformat(),
    "account": {},
    "positions": [],
    "recent_deals": [],
    "radar_28": [],
    "radar_all": [],
    "next_4h_close_sec": 0,
    "last_update": time.time(),
}
CACHE_LOCK = threading.Lock()

def calculate_candle_metrics(rates, prev_idx=-1):
    c = rates['close']
    h = rates['high']
    l = rates['low']
    o = rates['open']
    rng = max(1e-6, h[prev_idx] - l[prev_idx])
    top = max(o[prev_idx], c[prev_idx])
    bot = min(o[prev_idx], c[prev_idx])
    up_wick = (h[prev_idx] - top) / rng
    low_wick = (bot - l[prev_idx]) / rng
    body = abs(c[prev_idx] - o[prev_idx]) / rng
    direction = "BULL" if c[prev_idx] >= o[prev_idx] else "BEAR"
    return up_wick, low_wick, body, direction

def background_market_scanner():
    """Continuously refreshes MT5 data, EMAs, and proximity radar in memory."""
    if not mt5.initialize():
        print("[FATAL] MT5 could not be initialized in background worker.")
        return

    print(">> [DASHBOARD SCANNER] Background market calculation thread started.")
    alpha_symbols = {item["symbol"]: item for item in OPTIMAL_ALPHA_BASKET}

    while True:
        try:
            acc = mt5.account_info()
            term = mt5.terminal_info()

            account_data = {
                "login": acc.login if acc else None,
                "server": acc.server if acc else None,
                "balance": acc.balance if acc else 0.0,
                "equity": acc.equity if acc else 0.0,
                "margin": acc.margin if acc else 0.0,
                "margin_free": acc.margin_free if acc else 0.0,
                "margin_level": acc.margin_level if acc else 0.0,
                "connected": term.connected if term else False,
                "ping": term.ping_last / 1000.0 if term else 0.0,
            }

            # Live open positions
            mt5_pos = mt5.positions_get()
            positions_data = []
            if mt5_pos:
                for p in mt5_pos:
                    sym_info = mt5.symbol_info(p.symbol)
                    pt = sym_info.point if sym_info else 0.01
                    rd = abs(p.price_open - p.sl) if p.sl > 0 else (0.01 * p.price_open)
                    cur_p = p.price_current
                    r_mult = ((cur_p - p.price_open) / rd) if p.type == mt5.ORDER_TYPE_BUY else ((p.price_open - cur_p) / rd)
                    positions_data.append({
                        "ticket": p.ticket,
                        "symbol": p.symbol,
                        "type": "BUY" if p.type == mt5.ORDER_TYPE_BUY else "SELL",
                        "volume": p.volume,
                        "price_open": p.price_open,
                        "price_current": cur_p,
                        "sl": p.sl,
                        "tp": p.tp,
                        "profit": round(p.profit, 2),
                        "r_multiple": round(r_mult, 2),
                        "cluster": alpha_symbols.get(p.symbol, {}).get("cluster", "External"),
                    })

            # Closed deals from today
            from_t = datetime.now(timezone.utc) - pd.Timedelta(days=1)
            deals = mt5.history_deals_get(from_t, datetime.now(timezone.utc))
            closed_deals = []
            if deals:
                for d in reversed(deals):
                    if d.entry == 1:  # DEAL_ENTRY_OUT
                        closed_deals.append({
                            "ticket": d.ticket,
                            "symbol": d.symbol,
                            "volume": d.volume,
                            "price": d.price,
                            "profit": round(d.profit, 2),
                            "comment": d.comment,
                            "time": datetime.fromtimestamp(d.time, tz=timezone.utc).strftime("%H:%M:%S UTC"),
                        })
            recent_deals = closed_deals[:5]

            # Scan All 90 symbols & 28 Alpha symbols
            all_tradeable = [s.name for s in mt5.symbols_get() if s.trade_mode == mt5.SYMBOL_TRADE_MODE_FULL]
            radar_all_list = []
            radar_28_list = []
            next_close_sec = 0

            for sym in all_tradeable:
                rates = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_H4, 0, 250)
                if rates is None or len(rates) < 240:
                    continue
                tick = mt5.symbol_info_tick(sym)
                if not tick or tick.bid <= 0:
                    continue

                c = rates['close']
                h = rates['high']
                l = rates['low']
                t = rates['time']
                n = len(rates)

                # Time left in 4H bar
                bar_open = int(t[-1])
                bar_close = bar_open + 4 * 3600
                time_left = max(0, int(bar_close - tick.time))
                if next_close_sec == 0:
                    next_close_sec = time_left

                e34 = calculate_ema(c, 34)[-1]
                e55 = calculate_ema(c, 55)[-1]
                e89 = calculate_ema(c, 89)[-1]
                e233 = calculate_ema(c, 233)[-1]
                atr = calculate_atr(h, l, c, 14)[-1]
                adx = compute_adx(h, l, c, 14)[-1]

                curr_price = tick.bid
                d89_pts = curr_price - e89
                d233_pts = curr_price - e233
                d89_atr = abs(d89_pts) / atr if atr > 0 else 99.0
                d233_atr = abs(d233_pts) / atr if atr > 0 else 99.0

                min_dist_atr = min(d89_atr, d233_atr)
                nearest_shelf = "EMA89" if d89_atr < d233_atr else "EMA233"
                shelf_val = e89 if nearest_shelf == "EMA89" else e233

                # Shelf Status Classification
                if min_dist_atr <= 0.05:
                    status = "TOUCHING"
                    status_badge = "badge-danger"
                elif min_dist_atr <= 0.20:
                    status = "TESTING"
                    status_badge = "badge-warning"
                elif min_dist_atr <= 0.50:
                    status = "APPROACHING"
                    status_badge = "badge-primary"
                elif min_dist_atr <= 1.00:
                    status = "PULLBACK"
                    status_badge = "badge-info"
                else:
                    status = "EXTENDED"
                    status_badge = "badge-neutral"

                # Forming bar geometry
                up_wick, low_wick, body, direction = calculate_candle_metrics(rates, prev_idx=-1)
                
                # Precise Trend & Shelf Geometry Check
                # Bullish regime: EMA89 > EMA233 AND price >= EMA233
                # Bearish regime: EMA89 < EMA233 AND price <= EMA233
                trend_regime = "BULL" if e89 > e233 and curr_price >= e233 else ("BEAR" if e89 < e233 and curr_price <= e233 else "MIXED")
                is_above_shelf = curr_price >= shelf_val

                signal_hint = "MONITORING"
                if min_dist_atr <= 0.35:
                    if trend_regime == "BULL" and is_above_shelf:
                        # Pulling back from above to test shelf as support
                        if rates['low'][-1] <= shelf_val * 1.003 and low_wick >= 0.25:
                            signal_hint = "BULLISH REJECTION (SUPPORT BOUNCE)"
                        else:
                            signal_hint = f"TESTING {nearest_shelf} AS SUPPORT"
                    elif trend_regime == "BEAR" and not is_above_shelf:
                        # Rallying from below to test shelf as resistance
                        if rates['high'][-1] >= shelf_val * 0.997 and up_wick >= 0.25:
                            signal_hint = "BEARISH REJECTION (RESISTANCE)"
                        else:
                            signal_hint = f"TESTING {nearest_shelf} AS RESISTANCE"
                    else:
                        # Price is on counter side of shelf
                        side = "ABOVE" if is_above_shelf else "BELOW"
                        signal_hint = f"{side} {nearest_shelf} (CONSOLIDATING)"
                elif min_dist_atr <= 0.60:
                    signal_hint = f"APPROACHING {nearest_shelf}"
                else:
                    signal_hint = "EXTENDED"

                is_alpha = sym in alpha_symbols
                cluster = alpha_symbols[sym]["cluster"] if is_alpha else "General"
                setup = alpha_symbols[sym]["setup"] if is_alpha else "canonical"

                sym_record = {
                    "symbol": sym,
                    "cluster": cluster,
                    "setup": setup,
                    "is_alpha": is_alpha,
                    "price": curr_price,
                    "bid": tick.bid,
                    "ask": tick.ask,
                    "spread_pts": round((tick.ask - tick.bid) / (mt5.symbol_info(sym).point or 0.01), 1),
                    "ema34": round(e34, 4),
                    "ema55": round(e55, 4),
                    "ema89": round(e89, 4),
                    "ema233": round(e233, 4),
                    "atr": round(atr, 4),
                    "adx": round(adx, 1),
                    "nearest_shelf": nearest_shelf,
                    "shelf_price": round(shelf_val, 4),
                    "dist_atr": round(min_dist_atr, 2),
                    "dist_pts": round(curr_price - shelf_val, 4),
                    "status": status,
                    "status_badge": status_badge,
                    "up_wick_pct": round(up_wick * 100, 1),
                    "low_wick_pct": round(low_wick * 100, 1),
                    "body_pct": round(body * 100, 1),
                    "direction": direction,
                    "trend_regime": trend_regime,
                    "signal_hint": signal_hint,
                }

                radar_all_list.append(sym_record)
                if is_alpha:
                    radar_28_list.append(sym_record)

            # Sort both by proximity (closest to EMA first)
            radar_all_list.sort(key=lambda x: x["dist_atr"])
            radar_28_list.sort(key=lambda x: x["dist_atr"])

            with CACHE_LOCK:
                LIVE_DATA_CACHE["timestamp"] = datetime.now(timezone.utc).isoformat()
                LIVE_DATA_CACHE["account"] = account_data
                LIVE_DATA_CACHE["positions"] = positions_data
                LIVE_DATA_CACHE["recent_deals"] = recent_deals
                LIVE_DATA_CACHE["radar_28"] = radar_28_list
                LIVE_DATA_CACHE["radar_all"] = radar_all_list
                LIVE_DATA_CACHE["next_4h_close_sec"] = next_close_sec
                LIVE_DATA_CACHE["last_update"] = time.time()

        except Exception as e:
            print(f"[ERROR in dashboard scanner loop]: {e}")

        time.sleep(2)  # High-speed refresh cycle

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Delta Fibonacci — Live Institutional MT5 EMA Radar</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&family=Plus+Jakarta+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-base: #090c10;
            --bg-card: #131720;
            --bg-card-sub: #181d28;
            --border: #232936;
            --border-highlight: #3b4457;
            --text-main: #f0f3f6;
            --text-muted: #8b949e;
            --accent-cyan: #00d2ff;
            --accent-green: #00e676;
            --accent-amber: #ffb300;
            --accent-crimson: #ff1744;
            --accent-purple: #b388ff;
        }

        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            background-color: var(--bg-base);
            color: var(--text-main);
            font-family: 'Plus Jakarta Sans', -apple-system, sans-serif;
            padding: 20px;
            font-size: 13px;
            line-height: 1.5;
        }
        .mono { font-family: 'JetBrains Mono', monospace; }

        /* Header */
        .header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 16px 24px;
            margin-bottom: 20px;
        }
        .header-title h1 {
            font-size: 18px;
            font-weight: 700;
            letter-spacing: -0.3px;
            display: flex;
            align-items: center;
            gap: 10px;
        }
        .header-title h1 span.badge {
            background: rgba(0, 210, 255, 0.12);
            color: var(--accent-cyan);
            border: 1px solid rgba(0, 210, 255, 0.3);
            font-size: 11px;
            padding: 2px 8px;
            border-radius: 6px;
            font-weight: 600;
        }
        .header-meta {
            color: var(--text-muted);
            font-size: 12px;
            margin-top: 4px;
        }
        .header-stats {
            display: flex;
            gap: 20px;
            align-items: center;
        }
        .stat-box {
            text-align: right;
        }
        .stat-label {
            font-size: 11px;
            text-transform: uppercase;
            color: var(--text-muted);
            font-weight: 600;
            letter-spacing: 0.5px;
        }
        .stat-value {
            font-size: 16px;
            font-weight: 700;
        }
        .pulse-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: var(--accent-green);
            display: inline-block;
            box-shadow: 0 0 10px var(--accent-green);
            animation: pulse 1.5s infinite;
        }
        @keyframes pulse {
            0% { transform: scale(0.95); opacity: 0.7; }
            50% { transform: scale(1.3); opacity: 1; }
            100% { transform: scale(0.95); opacity: 0.7; }
        }

        /* Top Grid */
        .top-grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 20px;
            margin-bottom: 20px;
        }
        .card {
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 16px 20px;
        }
        .card-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 12px;
            border-bottom: 1px solid var(--border);
            padding-bottom: 8px;
        }
        .card-title {
            font-size: 13px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            color: var(--accent-cyan);
            display: flex;
            align-items: center;
            gap: 6px;
        }

        /* Tables */
        table {
            width: 100%;
            border-collapse: collapse;
            text-align: left;
        }
        th {
            color: var(--text-muted);
            font-size: 11px;
            font-weight: 600;
            text-transform: uppercase;
            padding: 10px 12px;
            border-bottom: 1px solid var(--border);
            background: rgba(0,0,0,0.15);
        }
        td {
            padding: 10px 12px;
            border-bottom: 1px solid rgba(255,255,255,0.03);
            vertical-align: middle;
        }
        tr:hover td {
            background: var(--bg-card-sub);
        }

        /* Badges */
        .badge {
            display: inline-flex;
            align-items: center;
            padding: 2px 8px;
            border-radius: 5px;
            font-size: 11px;
            font-weight: 700;
            text-transform: uppercase;
        }
        .badge-danger {
            background: rgba(255, 23, 68, 0.15);
            color: var(--accent-crimson);
            border: 1px solid rgba(255, 23, 68, 0.4);
            animation: pulse-danger 1s infinite;
        }
        .badge-warning {
            background: rgba(255, 179, 0, 0.15);
            color: var(--accent-amber);
            border: 1px solid rgba(255, 179, 0, 0.4);
        }
        .badge-primary {
            background: rgba(0, 210, 255, 0.15);
            color: var(--accent-cyan);
            border: 1px solid rgba(0, 210, 255, 0.4);
        }
        .badge-info {
            background: rgba(179, 136, 255, 0.12);
            color: var(--accent-purple);
            border: 1px solid rgba(179, 136, 255, 0.3);
        }
        .badge-neutral {
            background: rgba(255, 255, 255, 0.05);
            color: var(--text-muted);
            border: 1px solid var(--border);
        }
        @keyframes pulse-danger {
            0% { box-shadow: 0 0 0 0 rgba(255, 23, 68, 0.4); }
            70% { box-shadow: 0 0 0 6px rgba(255, 23, 68, 0); }
            100% { box-shadow: 0 0 0 0 rgba(255, 23, 68, 0); }
        }

        /* Wick Geometry Bar */
        .wick-bar-container {
            display: flex;
            align-items: center;
            gap: 6px;
            width: 140px;
        }
        .wick-progress {
            display: flex;
            height: 8px;
            width: 90px;
            border-radius: 4px;
            overflow: hidden;
            background: #222;
        }
        .wick-seg-up { background: var(--accent-crimson); }
        .wick-seg-body { background: #555; }
        .wick-seg-low { background: var(--accent-green); }

        /* Filter Controls */
        .controls {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 12px;
            gap: 15px;
        }
        .btn-group {
            display: flex;
            gap: 8px;
        }
        .btn {
            background: var(--bg-card-sub);
            border: 1px solid var(--border);
            color: var(--text-main);
            padding: 8px 14px;
            border-radius: 8px;
            font-size: 12px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s;
        }
        .btn.active {
            background: rgba(0, 210, 255, 0.15);
            border-color: var(--accent-cyan);
            color: var(--accent-cyan);
        }
        .search-box {
            background: var(--bg-card-sub);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 8px 14px;
            color: #fff;
            font-size: 12px;
            outline: none;
            width: 220px;
        }
        .search-box:focus {
            border-color: var(--accent-cyan);
        }

        .green { color: var(--accent-green); }
        .crimson { color: var(--accent-crimson); }
        .amber { color: var(--accent-amber); }
        .cyan { color: var(--accent-cyan); }
    </style>
</head>
<body>

    <!-- Header Section -->
    <div class="header">
        <div class="header-title">
            <h1>
                <span class="pulse-dot"></span>
                DELTA FIBONACCI QUANT TERMINAL
                <span class="badge">MT5 LIVE RADAR</span>
            </h1>
            <div class="header-meta">
                Account: <strong class="mono" id="acc-login">#11808165</strong> (<span id="acc-server">FundedNext-Server</span>) |
                Ping: <span id="acc-ping" class="mono">--</span>ms |
                Scan Cycle: <strong class="cyan mono">High-Speed 10s</strong> |
                Status: <span class="green font-bold">ONLINE</span>
            </div>
        </div>
        <div class="header-stats">
            <div class="stat-box">
                <div class="stat-label">Balance</div>
                <div class="stat-value mono cyan" id="acc-balance">$0.00</div>
            </div>
            <div class="stat-box">
                <div class="stat-label">Live Equity</div>
                <div class="stat-value mono green" id="acc-equity">$0.00</div>
            </div>
            <div class="stat-box">
                <div class="stat-label">Next 4H Bar Close</div>
                <div class="stat-value mono amber" id="time-countdown">--:--:--</div>
            </div>
        </div>
    </div>

    <!-- Top Two Cards: Live Positions & Proximity Highlights -->
    <div class="top-grid">
        <!-- Live Open Positions Card -->
        <div class="card">
            <div class="card-header">
                <div class="card-title">🛡️ Active MT5 Positions (<span id="pos-count">0</span> / 3)</div>
                <span class="mono" style="font-size: 11px; color: var(--text-muted);">$10 Fixed Risk / Trade</span>
            </div>
            <div style="overflow-x: auto;">
                <table>
                    <thead>
                        <tr>
                            <th>Ticket</th>
                            <th>Symbol</th>
                            <th>Action</th>
                            <th>Lots</th>
                            <th>Entry</th>
                            <th>Stop Loss</th>
                            <th>Take Profit</th>
                            <th>Current</th>
                            <th>PnL</th>
                        </tr>
                    </thead>
                    <tbody id="pos-table-body">
                        <tr><td colspan="9" style="text-align:center; color: var(--text-muted);">No open positions. 3 concurrency slots ready.</td></tr>
                    </tbody>
                </table>
            </div>
        </div>

        <!-- Proximity Highlights Card (Closest to Shelves) -->
        <div class="card">
            <div class="card-header">
                <div class="card-title">🎯 Live Proximity Highlights (Approaching Shelves)</div>
                <span class="badge badge-warning" style="font-size: 10px;">≤ 0.50 ATR</span>
            </div>
            <div style="overflow-x: auto;">
                <table>
                    <thead>
                        <tr>
                            <th>Symbol</th>
                            <th>Shelf</th>
                            <th>Distance (ATR)</th>
                            <th>Price</th>
                            <th>Shelf Level</th>
                            <th>Signal Radar</th>
                        </tr>
                    </thead>
                    <tbody id="proximity-highlights-body">
                        <tr><td colspan="6" style="text-align:center; color: var(--text-muted);">Calculating live distances...</td></tr>
                    </tbody>
                </table>
            </div>
        </div>
    </div>

    <!-- Full Radar Controls -->
    <div class="controls">
        <div class="btn-group">
            <button class="btn active" id="btn-alpha" onclick="setUniverseFilter('alpha')">⭐ 28 Alpha Universe</button>
            <button class="btn" id="btn-all" onclick="setUniverseFilter('all')">🌐 All 90 MT5 Symbols</button>
            <button class="btn" id="btn-touching" onclick="setApproachFilter('touching')">🔥 Approaching / Touching (≤ 0.5 ATR)</button>
        </div>
        <input type="text" class="search-box mono" id="search-input" placeholder="🔍 Search symbol (e.g. US30, XAUUSD)..." onkeyup="renderTable()">
    </div>

    <!-- Main Table: Live EMAs & Setup Scanner -->
    <div class="card" style="padding: 0; overflow: hidden;">
        <div style="overflow-x: auto;">
            <table>
                <thead>
                    <tr>
                        <th>Symbol</th>
                        <th>Cluster</th>
                        <th>Live Price</th>
                        <th>EMA 34</th>
                        <th>EMA 55</th>
                        <th>EMA 89</th>
                        <th>EMA 233</th>
                        <th>Target Shelf</th>
                        <th>Shelf Proximity</th>
                        <th>ATR (14)</th>
                        <th>ADX</th>
                        <th>4H Wick Geometry</th>
                        <th>Action Status</th>
                    </tr>
                </thead>
                <tbody id="main-radar-body" class="mono">
                    <tr><td colspan="13" style="text-align:center; padding: 30px;">Loading live MT5 calculations...</td></tr>
                </tbody>
            </table>
        </div>
    </div>

    <script>
        let currentUniverse = 'alpha';
        let currentApproachFilter = 'all';
        let rawData = null;

        function setUniverseFilter(type) {
            currentUniverse = type;
            document.getElementById('btn-alpha').classList.toggle('active', type === 'alpha');
            document.getElementById('btn-all').classList.toggle('active', type === 'all');
            renderTable();
        }

        function setApproachFilter(type) {
            if (currentApproachFilter === type) {
                currentApproachFilter = 'all';
                document.getElementById('btn-touching').classList.remove('active');
            } else {
                currentApproachFilter = type;
                document.getElementById('btn-touching').classList.add('active');
            }
            renderTable();
        }

        async function fetchLiveData() {
            try {
                const res = await fetch('/api/data');
                if (!res.ok) return;
                rawData = await res.json();
                updateUI();
            } catch (e) {
                console.error("Data fetch error:", e);
            }
        }

        function updateUI() {
            if (!rawData) return;

            // Account
            const acc = rawData.account || {};
            document.getElementById('acc-login').textContent = '#' + (acc.login || '--');
            document.getElementById('acc-server').textContent = acc.server || '--';
            document.getElementById('acc-ping').textContent = acc.ping ? acc.ping.toFixed(1) : '--';
            document.getElementById('acc-balance').textContent = '$' + (acc.balance || 0).toLocaleString('en-US', {minimumFractionDigits: 2});
            document.getElementById('acc-equity').textContent = '$' + (acc.equity || 0).toLocaleString('en-US', {minimumFractionDigits: 2});

            // Countdown timer
            let sec = rawData.next_4h_close_sec || 0;
            let hrs = Math.floor(sec / 3600);
            let mins = Math.floor((sec % 3600) / 60);
            let s = Math.floor(sec % 60);
            document.getElementById('time-countdown').textContent = `${hrs}h ${mins.toString().padStart(2, '0')}m ${s.toString().padStart(2, '0')}s`;

            // Positions Table
            const pos = rawData.positions || [];
            document.getElementById('pos-count').textContent = pos.length;
            const posBody = document.getElementById('pos-table-body');
            if (pos.length === 0) {
                posBody.innerHTML = `<tr><td colspan="9" style="text-align:center; color: var(--text-muted); padding: 16px;">No open positions. 3 concurrency slots ready.</td></tr>`;
            } else {
                posBody.innerHTML = pos.map(p => `
                    <tr>
                        <td class="mono">#${p.ticket}</td>
                        <td class="font-bold cyan">${p.symbol}</td>
                        <td class="${p.type === 'BUY' ? 'green' : 'crimson'} font-bold">${p.type}</td>
                        <td class="mono">${p.volume}</td>
                        <td class="mono">${p.price_open}</td>
                        <td class="mono crimson">${p.sl}</td>
                        <td class="mono green">${p.tp}</td>
                        <td class="mono font-bold">${p.price_current}</td>
                        <td class="mono font-bold ${p.profit >= 0 ? 'green' : 'crimson'}">
                            ${p.profit >= 0 ? '+' : ''}$${p.profit.toFixed(2)} (${p.r_multiple >= 0 ? '+' : ''}${p.r_multiple}R)
                        </td>
                    </tr>
                `).join('');
            }

            // Proximity Highlights Table (Top 5 approaching)
            const list = rawData.radar_all || [];
            const highBody = document.getElementById('proximity-highlights-body');
            const approaching = list.filter(x => x.dist_atr <= 0.50).slice(0, 5);
            if (approaching.length === 0) {
                highBody.innerHTML = `<tr><td colspan="6" style="text-align:center; color: var(--text-muted); padding: 16px;">All assets currently > 0.50 ATR away from shelves.</td></tr>`;
            } else {
                highBody.innerHTML = approaching.map(x => `
                    <tr>
                        <td class="font-bold cyan">${x.symbol} <span style="font-size:10px; color:var(--text-muted)">(${x.cluster})</span></td>
                        <td><span class="badge ${x.nearest_shelf === 'EMA89' ? 'badge-primary' : 'badge-info'}">${x.nearest_shelf}</span></td>
                        <td class="mono font-bold ${x.dist_atr <= 0.1 ? 'crimson' : (x.dist_atr <= 0.25 ? 'amber' : 'cyan')}">${x.dist_atr.toFixed(2)} ATR</td>
                        <td class="mono">${x.price.toFixed(4)}</td>
                        <td class="mono text-muted">${x.shelf_price.toFixed(4)}</td>
                        <td><span class="badge ${x.status_badge}">${x.status}</span></td>
                    </tr>
                `).join('');
            }

            renderTable();
        }

        function renderTable() {
            if (!rawData) return;
            const search = document.getElementById('search-input').value.toUpperCase().trim();
            const source = (currentUniverse === 'alpha') ? (rawData.radar_28 || []) : (rawData.radar_all || []);

            let filtered = source;
            if (currentApproachFilter === 'touching') {
                filtered = filtered.filter(x => x.dist_atr <= 0.50);
            }
            if (search) {
                filtered = filtered.filter(x => x.symbol.includes(search) || x.cluster.toUpperCase().includes(search));
            }

            const tbody = document.getElementById('main-radar-body');
            if (filtered.length === 0) {
                tbody.innerHTML = `<tr><td colspan="13" style="text-align:center; padding: 24px; color: var(--text-muted);">No symbols match the current filters.</td></tr>`;
                return;
            }

            tbody.innerHTML = filtered.map(x => {
                const wickTotal = x.up_wick_pct + x.body_pct + x.low_wick_pct || 100;
                const upW = (x.up_wick_pct / wickTotal * 90);
                const bW = (x.body_pct / wickTotal * 90);
                const lW = (x.low_wick_pct / wickTotal * 90);

                return `
                    <tr>
                        <td class="font-bold cyan" style="font-size: 13px;">
                            ${x.symbol}
                            ${x.is_alpha ? '⭐' : ''}
                        </td>
                        <td style="color: var(--text-muted); font-size: 11px;">${x.cluster}</td>
                        <td class="font-bold" style="color: #fff;">${x.price.toFixed(4)}</td>
                        <td style="color: #bbb;">${x.ema34.toFixed(4)}</td>
                        <td style="color: #bbb;">${x.ema55.toFixed(4)}</td>
                        <td style="color: var(--accent-cyan); font-weight: 600;">${x.ema89.toFixed(4)}</td>
                        <td style="color: var(--accent-purple); font-weight: 600;">${x.ema233.toFixed(4)}</td>
                        <td>
                            <span class="badge ${x.nearest_shelf === 'EMA89' ? 'badge-primary' : 'badge-info'}">
                                ${x.nearest_shelf}
                            </span>
                        </td>
                        <td>
                            <span class="badge ${x.status_badge}">
                                ${x.dist_atr.toFixed(2)} ATR (${x.status})
                            </span>
                        </td>
                        <td style="color: #888;">${x.atr.toFixed(4)}</td>
                        <td class="${x.adx >= 20 ? 'green font-bold' : 'amber'}">${x.adx.toFixed(1)}</td>
                        <td>
                            <div class="wick-bar-container">
                                <div class="wick-progress" title="Up: ${x.up_wick_pct}% | Body: ${x.body_pct}% | Low: ${x.low_wick_pct}%">
                                    <div class="wick-seg-up" style="width: ${upW}px;"></div>
                                    <div class="wick-seg-body" style="width: ${bW}px;"></div>
                                    <div class="wick-seg-low" style="width: ${lW}px;"></div>
                                </div>
                                <span style="font-size: 10px; color: ${x.direction === 'BULL' ? 'var(--accent-green)' : 'var(--accent-crimson)'};">
                                    ${x.direction === 'BULL' ? '▲' : '▼'}${x.direction === 'BULL' ? x.low_wick_pct : x.up_wick_pct}%
                                </span>
                            </div>
                        </td>
                        <td>
                            <span style="font-size: 11px; font-weight: 600; color: ${x.signal_hint.includes('READY') ? 'var(--accent-green)' : 'var(--text-muted)'};">
                                ${x.signal_hint}
                            </span>
                        </td>
                    </tr>
                `;
            }).join('');
        }

        // Start 2-second live polling loop
        fetchLiveData();
        setInterval(fetchLiveData, 2000);
    </script>
</body>
</html>
"""

def default_json_serializer(obj):
    if isinstance(obj, (np.integer, np.int64, np.int32)):
        return int(obj)
    elif isinstance(obj, (np.floating, np.float64, np.float32)):
        return float(obj)
    elif isinstance(obj, (np.ndarray,)):
        return obj.tolist()
    elif isinstance(obj, (datetime,)):
        return obj.isoformat()
    return str(obj)

class DashboardRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(DASHBOARD_HTML.encode("utf-8"))
        elif self.path == "/api/data":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            with CACHE_LOCK:
                payload = json.dumps(LIVE_DATA_CACHE, default=default_json_serializer)
            self.wfile.write(payload.encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        # Silence standard HTTP access logging to keep terminal clean
        return

def main():
    port = 8088
    # Start background calculator thread
    worker = threading.Thread(target=background_market_scanner, daemon=True)
    worker.start()

    server = HTTPServer(("0.0.0.0", port), DashboardRequestHandler)
    print("=" * 80)
    print(f"🚀 DELTA FIBONACCI LIVE QUANT RADAR DASHBOARD RUNNING")
    print(f"👉 Localhost URL: http://localhost:{port}")
    print(f"👉 LAN Network URL: http://127.0.0.1:{port}")
    print("=" * 80)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down dashboard server...")
        server.server_close()
        mt5.shutdown()

if __name__ == "__main__":
    main()
