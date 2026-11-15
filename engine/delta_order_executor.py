#!/usr/bin/env python3
"""delta_order_executor.py - Signed order-execution wrapper for Delta
Exchange India with attached bracket TP/SL, built for the risk posture of
audit QR-2026-1114 / re-evaluation QR-2026-1114-R1.

SAFETY MODEL (read first)
  * DRY-RUN IS THE DEFAULT. Every order is built, signed and validated, and
    the exact request is printed - but nothing is sent - unless you pass
    live=True (CLI: --live). Test in dry-run against your account before
    flipping the switch.
  * Credentials come from the DELTA_API_KEY / DELTA_API_SECRET environment
    variables (or explicit constructor arguments). The secret is never
    logged, never included in exceptions, and never written to disk.
  * A kill-switch file stops all live orders when it exists
    (default: ./KILL_SWITCH - `touch KILL_SWITCH` halts execution).
  * Pre-trade hooks: an external validator callback (wire in your cluster
    risk governor) and a built-in risk cap that rejects any order whose
    stop-distance risk exceeds max_risk_r multiples of the per-trade budget.

AUTHENTICATION (per Delta API docs)
  signature = hex(HMAC-SHA256(api_secret,
             method + timestamp + requestPath + queryString + body))
  Headers: api-key, timestamp (epoch ms), signature.
  queryString includes the leading '?' when present; body is the exact JSON
  string for POST/PUT ('' for GET).

ORDER SCHEMA (POST /v2/orders)
  product_id, size (INTEGER contracts - the venue's order size unit),
  side ('buy' for long / 'sell' for short), order_type ('limit_order' /
  'market_order'), limit_price (string), time_in_force, client_order_id,
  and the bracket group: bracket_stop_loss_price,
  bracket_stop_loss_limit_price (optional), bracket_take_profit_price,
  bracket_trail_amount, bracket_stop_trigger_method.
  Prices are STRINGS quantised to the product tick size.

Usage:
    export DELTA_API_KEY=... DELTA_API_SECRET=...
    python3 delta_order_executor.py                      # dry-run demo
    python3 delta_order_executor.py --demo               # bracket demo (dry)
    python3 delta_order_executor.py --live --symbol XAUTUSD --side buy \\
        --contracts 22 --stop 3765.0 --take-profit 3830.0   # REAL ORDER

    from delta_order_executor import DeltaOrderExecutor
    ex = DeltaOrderExecutor()                    # dry-run
    ex = DeltaOrderExecutor(live=True)           # live - after dry-run tests

Dependencies: standard library only.
"""

from __future__ import annotations

import argparse
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Callable, Dict, Optional

BASE_URL = os.environ.get(
    "DELTA_BASE_URL",
    "https://cdn-ind.testnet.deltaex.org"
    if os.environ.get("DELTA_MCP_ENV") == "india_testnet"
    else "https://api.india.delta.exchange"
)
_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_MODULE_DIR) if os.path.basename(_MODULE_DIR) == "engine" else _MODULE_DIR
KILL_SWITCH_FILE = os.path.join(_REPO_ROOT, "KILL_SWITCH")
USER_AGENT = "delta-fibonacci-executor/1.0"


class ExecutorError(RuntimeError):
    """Raised for configuration/safety violations and API errors."""


class DeltaOrderExecutor:
    """Minimal, safety-first REST client for placing bracket orders."""

    def __init__(self,
                 api_key: Optional[str] = None,
                 api_secret: Optional[str] = None,
                 base_url: str = BASE_URL,
                 live: bool = False,
                 kill_switch: str = KILL_SWITCH_FILE,
                 # QR-2026-1115: aligned with the lot sizer's hard cap so a
                 # sizer-approved min-lot trade (up to Rs 55 / 1.1R) is not
                 # rejected here at order time. The executor remains the
                 # final authority at this bound.
                 max_risk_r: float = 1.1,
                 risk_budget_inr: float = 50.0,
                 inr_per_usd: float = 85.0,
                 max_contracts: int = 10_000,
                 request_timeout: float = 30.0,
                 max_retries: int = 3,
                 rate_limit_sec: float = 0.25):
        self.api_key = api_key or os.environ.get("DELTA_API_KEY", "")
        self.api_secret = (api_secret or
                           os.environ.get("DELTA_API_SECRET", "")).encode()
        self.base_url = base_url.rstrip("/")
        self.live = live
        self.kill_switch = kill_switch
        self.max_risk_r = max_risk_r
        self.risk_budget_inr = risk_budget_inr
        self.inr_per_usd = inr_per_usd
        self.max_contracts = max_contracts
        self.request_timeout = request_timeout
        self.max_retries = max_retries
        self.rate_limit_sec = rate_limit_sec
        self._last_request_ts = 0.0
        self._products: Dict[str, dict] = {}
        # external pre-trade validator: callable(order_dict) -> (bool, reason)
        self.pre_trade_hook: Optional[Callable[[dict], tuple]] = None

    # ------------------------------------------------------------------ auth

    def _sign(self, method: str, timestamp: str, path: str,
              query: str, body: str) -> str:
        payload = f"{method}{timestamp}{path}{query}{body}"
        return hmac.new(self.api_secret, payload.encode(),
                        hashlib.sha256).hexdigest()

    def _check_safety(self, requires_auth: bool) -> None:
        ks_candidates = [
            self.kill_switch,
            os.path.join(_REPO_ROOT, "KILL_SWITCH"),
            os.path.join(_MODULE_DIR, "KILL_SWITCH"),
            os.path.abspath("KILL_SWITCH"),
        ]
        for ks_path in ks_candidates:
            if ks_path and os.path.exists(ks_path):
                raise ExecutorError(
                    f"KILL SWITCH engaged ({ks_path} exists); "
                    f"refusing to send live orders")
        if requires_auth and not (self.api_key and self.api_secret):
            raise ExecutorError(
                "missing credentials: set DELTA_API_KEY and DELTA_API_SECRET")

    # -------------------------------------------------------------- transport

    def _request(self, method: str, path: str, query: str = "",
                 body: Optional[dict] = None, requires_auth: bool = False,
                 _retry: int = 0) -> dict:
        self._check_safety(requires_auth)
        body_str = json.dumps(body, separators=(",", ":")) if body is not None else ""
        url = self.base_url + path + (query if query else "")
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if requires_auth:
            ts = str(int(time.time()))
            headers["api-key"] = self.api_key
            headers["timestamp"] = ts
            headers["signature"] = self._sign(method.upper(), ts, path,
                                              query or "", body_str)
        # polite rate limiting
        wait = self.rate_limit_sec - (time.time() - self._last_request_ts)
        if wait > 0:
            time.sleep(wait)
        self._last_request_ts = time.time()

        data = body_str.encode() if body is not None else None
        req = urllib.request.Request(url, data=data, headers=headers,
                                     method=method.upper())
        if not self.live and body is not None and "orders" in path:
            # DRY-RUN: show exactly what would be signed and sent
            out = {"dry_run": True, "method": method.upper(),
                   "url": url, "headers": {k: v for k, v in headers.items()
                                           if k in ("api-key", "timestamp",
                                                    "signature", "Content-Type")},
                   "body": body}
            print(json.dumps(out, indent=2))
            return {"result": {"dry_run": True, "order": body},
                    "meta": {"note": "dry-run: nothing was sent"}}

        try:
            with urllib.request.urlopen(req, timeout=self.request_timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", "replace")[:500]
            retryable = e.code == 429 or 500 <= e.code < 600
            if retryable and _retry < self.max_retries and body is None:
                # retry idempotent GETs only; never auto-retry order placement
                time.sleep(0.5 * (2 ** _retry))
                return self._request(method, path, query, body,
                                     requires_auth, _retry + 1)
            raise ExecutorError(f"HTTP {e.code} {e.reason}: {err_body}") from e
        except urllib.error.URLError as e:
            raise ExecutorError(f"network error: {e.reason}") from e

    # ------------------------------------------------------------- public API

    def get_products(self, refresh: bool = False) -> Dict[str, dict]:
        """{symbol: spec} for operational perpetual futures (cached)."""
        if refresh or not self._products:
            try:
                res = self._request("GET", "/v2/products")["result"]
                self._products = {p["symbol"]: p for p in res
                                  if p.get("contract_type") == "perpetual_futures"
                                  and p.get("trading_status") == "operational"}
            except ExecutorError:
                if self.live:
                    raise
                # Offline dry-run fallback: load from local product cache or snapshot
                for cpath in (
                    os.path.join(_MODULE_DIR, "delta_products_cache.json"),
                    os.path.join(_MODULE_DIR, "universe_snapshot.json"),
                ):
                    if os.path.exists(cpath):
                        with open(cpath, "r") as f:
                            raw = json.load(f)
                        if isinstance(raw, dict) and "products" in raw:
                            self._products = {
                                p["symbol"]: p for p in raw["products"]
                                if p.get("contract_type") == "perpetual_futures"
                                and p.get("trading_status") in (None, "operational")
                            }
                        elif isinstance(raw, dict):
                            self._products = raw
                        if self._products:
                            break
                if not self._products:
                    raise
        return self._products

    def get_ticker(self, symbol: str) -> dict:
        try:
            return self._request("GET", f"/v2/tickers/{symbol}")["result"]
        except ExecutorError:
            if self.live:
                raise
            spec = self.get_products().get(symbol, {})
            mp = spec.get("mark_price") or spec.get("close") or 100.0
            return {"symbol": symbol, "mark_price": float(mp), "bid": float(mp), "ask": float(mp)}

    def get_balances(self) -> list:
        return self._request("GET", "/v2/wallet/balances",
                             requires_auth=True)["result"]

    def get_positions(self) -> list:
        return self._request("GET", "/v2/positions",
                             requires_auth=True)["result"]

    def get_order(self, order_id: str) -> dict:
        qs = f"?id={order_id}&state=filled,open"
        return self._request("GET", "/v2/orders", query=qs,
                             requires_auth=True)["result"]

    def cancel_order(self, order_id: str) -> dict:
        self._check_safety(True)
        return self._request("PUT", "/v2/orders/cancel_by_id",
                             body={"id": order_id}, requires_auth=True)

    # ------------------------------------------------------------ order path

    @staticmethod
    def _tick_round(price: float, tick: float) -> str:
        if tick <= 0:
            return f"{price:.8f}".rstrip("0").rstrip(".")
        d_price = Decimal(str(price))
        d_tick = Decimal(str(tick))
        quantized = (d_price / d_tick).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * d_tick
        res = f"{quantized:f}"
        if "." in res:
            res = res.rstrip("0").rstrip(".")
        return res

    def _risk_check(self, symbol: str, spec: dict, contracts: int,
                    entry: float, stop: float) -> None:
        if contracts < 1:
            raise ExecutorError("contracts must be >= 1 (integer)")
        if contracts > self.max_contracts:
            raise ExecutorError(
                f"contracts {contracts} exceeds sanity cap {self.max_contracts}")
        cv = float(spec["contract_value"])
        risk_inr = abs(entry - stop) * contracts * cv * self.inr_per_usd
        risk_r = risk_inr / self.risk_budget_inr
        if risk_r > self.max_risk_r + 1e-9:
            raise ExecutorError(
                f"order risk Rs {risk_inr:.2f} = {risk_r:.2f}R exceeds the "
                f"{self.max_risk_r:.1f}R per-trade cap (risk budget Rs "
                f"{self.risk_budget_inr:.0f}) - re-size with the lot sizer")

    def place_bracket_order(self,
                            symbol: str,
                            side: str,
                            contracts: int,
                            stop_price: float,
                            entry_type: str = "market",
                            limit_price: Optional[float] = None,
                            take_profit_price: Optional[float] = None,
                            trail_amount: Optional[float] = None,
                            reduce_only: bool = False,
                            client_order_id: Optional[str] = None,
                            ) -> dict:
        """Place an order with an attached bracket stop-loss / take-profit.

        side: 'buy' (long) or 'sell' (short). stop_price is REQUIRED - an
        unbracketed position violates the system's risk model, so the
        executor refuses to send one. For a long, TP must be above entry and
        the stop below; mirrored for a short.
        """
        side = side.lower()
        if side not in ("buy", "sell"):
            raise ExecutorError("side must be 'buy' or 'sell'")
        if entry_type not in ("market", "limit"):
            raise ExecutorError("entry_type must be 'market' or 'limit'")
        if entry_type == "limit" and limit_price is None:
            raise ExecutorError("limit orders need limit_price")
        if stop_price <= 0:
            raise ExecutorError("stop_price is required (no unbracketed orders)")

        spec = self.get_products().get(symbol)
        if not spec:
            raise ExecutorError(f"unknown or non-operational symbol {symbol}")
        tick = float(spec.get("tick_size") or 0)
        cv = float(spec["contract_value"])

        # reference price for the risk check: live mark, or the limit price
        ref = limit_price if entry_type == "limit" else None
        if ref is None:
            try:
                ref = float(self.get_ticker(symbol)["mark_price"])
            except Exception:
                raise ExecutorError(
                    "cannot fetch mark price for the risk check; "
                    "pass entry_type='limit' with limit_price")
        self._risk_check(symbol, spec, contracts, ref, stop_price)

        # bracket sanity relative to the reference price
        if side == "buy" and stop_price >= ref * 1.001:
            raise ExecutorError(f"long stop {stop_price} must be below entry ~{ref}")
        if side == "sell" and stop_price <= ref * 0.999:
            raise ExecutorError(f"short stop {stop_price} must be above entry ~{ref}")
        if take_profit_price is not None:
            if side == "buy" and take_profit_price <= ref:
                raise ExecutorError("long take-profit must be above entry")
            if side == "sell" and take_profit_price >= ref:
                raise ExecutorError("short take-profit must be below entry")

        body = {
            "product_id": int(spec["id"]),
            "size": int(contracts),
            "side": side,
            "order_type": "market_order" if entry_type == "market" else "limit_order",
            "bracket_stop_loss_price": self._tick_round(stop_price, tick),
            "bracket_stop_trigger_method": "mark_price",
        }
        if entry_type == "limit":
            body["limit_price"] = self._tick_round(limit_price, tick)
            body["time_in_force"] = "gtc"
        if take_profit_price is not None:
            body["bracket_take_profit_price"] = self._tick_round(take_profit_price, tick)
        if trail_amount is not None:
            # trailing distance in price units, applied to the SL leg
            body["bracket_trail_amount"] = self._tick_round(trail_amount, tick)
        if reduce_only:
            body["reduce_only"] = True
        if client_order_id:
            body["client_order_id"] = client_order_id

        if self.pre_trade_hook is not None:
            ok, reason = self.pre_trade_hook(body)
            if not ok:
                raise ExecutorError(f"pre-trade hook rejected the order: {reason}")

        res = self._request("POST", "/v2/orders", body=body, requires_auth=True)
        if (res.get("result") or {}).get("dry_run"):
            return res["result"]       # {"dry_run": true, "order": body}
        order = (res.get("result") or {})
        if not order.get("id"):
            raise ExecutorError(f"order rejected by venue: {json.dumps(res)[:300]}")
        return res


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _demo(ex: DeltaOrderExecutor, symbol: str, side: str, contracts: int,
          stop: Optional[float], tp: Optional[float]) -> None:
    t = ex.get_ticker(symbol)
    mark = float(t["mark_price"])
    if stop is None:
        # size the demo stop to ~0.9R of the Rs 50 budget at `contracts` lots
        spec = ex.get_products()[symbol]
        cv = float(spec["contract_value"])
        dist = 45.0 / (contracts * cv * ex.inr_per_usd)
        stop = mark - dist if side == "buy" else mark + dist
        tp = mark + 2 * dist if side == "buy" else mark - 2 * dist
    print(f"{symbol} mark {mark} | placing {side} {contracts} contracts "
          f"stop {stop} TP {tp or '-'} (live={ex.live})")
    res = ex.place_bracket_order(symbol, side, contracts, stop,
                                 take_profit_price=tp)
    print("venue response:", json.dumps(res)[:400])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--live", action="store_true",
                    help="SEND REAL ORDERS (default is dry-run)")
    ap.add_argument("--symbol", default="XAUTUSD")
    ap.add_argument("--side", default="buy", choices=["buy", "sell"])
    ap.add_argument("--contracts", type=int, default=22)
    ap.add_argument("--stop", type=float, default=None,
                    help="bracket stop price (default: 1%% from mark)")
    ap.add_argument("--take-profit", type=float, default=None)
    ap.add_argument("--max-risk-r", type=float, default=1.1,
                    help="risk cap in R multiples of the Rs 50 budget")
    args = ap.parse_args()

    ex = DeltaOrderExecutor(live=args.live, max_risk_r=args.max_risk_r)
    mode = "LIVE" if args.live else "DRY-RUN"
    print(f"Delta Order Executor - {mode} | kill-switch: {ex.kill_switch}")
    if args.live and os.path.exists(ex.kill_switch):
        print("kill switch file exists - refusing to run"); sys.exit(1)
    _demo(ex, args.symbol, args.side, args.contracts, args.stop,
          args.take_profit)
    if not args.live:
        print("\n(dry-run: request was signed and validated but NOT sent; "
              "use --live only after governor + validator sign-off)")


if __name__ == "__main__":
    main()
