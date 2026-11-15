#!/usr/bin/env python3
"""universe_screener.py - Honest tradable-universe screener for Delta Exchange
India. Companion to the quant review re-evaluation (QR-2026-1114-R1) of the
v2.0 basket-scaling proposal.

WHY THIS EXISTS
The v2 letter claims "242 of 250 perpetuals (96.8%) qualify" for the basket
under a lot-size filter (1-contract stop risk <= Rs 55, 10X margin <=
Rs 2,500). That filter verifies (242-244 of 250 depending on stop distance),
but listing eligibility says nothing about whether an asset can actually be
TRADED at plan size: at the live pull of 14 Nov 2026, only 33 of 250 perps
turn over >= $1M/day and 82 turn over >= $100k/day, while 200 of 250 sit in
the 0.05% taker tier (vs 0.02%/0.01% for the originally audited seven) - and
the flagship ARMBUSD book itself turned over just $9,010 that day.

This tool applies the full stack of feasibility filters, tier by tier, so the
promotion decision is made on the binding constraints:

  T1 LOT        1-contract stop risk <= risk cap AND margin <= margin cap
  T2 LIQUIDITY  24h USD turnover >= volume floor (spread/exit reality)
  T3 FRICTION   estimated round-trip cost in R (fees + 18% GST, at the
                symbol's stop distance) <= friction cap
  T4 MC-GATE    (informational) assets whose 4H candles co-moved in the two
                audited crash tapes - flagged for the cluster governor

Tier results are reported cumulatively; only T1+T2+T3 passers belong in a
live basket at Rs 5,000 scale.

Usage:
    python3 universe_screener.py                       # live pull, print tiers
    python3 universe_screener.py --volume-floor 100000 --stop-pct 0.02
    python3 universe_screener.py --csv universe.csv --json universe.json
    python3 universe_screener.py --offline             # use cached snapshot

Dependencies: standard library only.
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.request
from typing import Dict, List, Optional

BASE_URL = "https://api.india.delta.exchange"
INR_PER_USD = 85.0            # Delta India platform rate (fixed)
GST_ON_FEES = 0.18            # 18% GST charged on trading fees

DEFAULT_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "universe_snapshot.json")
CACHE_TTL_SEC = 3600

# Median measured entry-to-stop distances (audit QR-2026-1114, Table 4) for
# the assets where the audited configs ran; others use the class default.
MEASURED_STOP_PCT = {
    "ARMBUSD": 0.0092, "MRVLBUSD": 0.0084, "HOODBUSD": 0.0076,
    "XAUTUSD": 0.0067, "PAXGUSD": 0.0061, "BANKUSD": 0.0135, "GRAMUSD": 0.0127,
}
DEFAULT_STOP_PCT = 0.0100     # generic 1% stop for unmeasured assets


def _http_get(url: str, timeout: float = 40.0) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "universe-screener/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def load_snapshot(cache_path: str = DEFAULT_CACHE, offline: bool = False,
                  ttl: float = CACHE_TTL_SEC) -> tuple:
    """Return (products, tickers). Live pull with on-disk cache fallback."""
    fresh = None
    if not offline:
        try:
            products = _http_get(f"{BASE_URL}/v2/products")["result"]
            tickers = {t["symbol"]: t for t in _http_get(f"{BASE_URL}/v2/tickers")["result"]}
            fresh = (products, tickers)
            with open(cache_path, "w") as f:
                json.dump({"pulled_at": int(time.time()),
                           "products": products, "tickers": tickers}, f)
        except Exception as e:  # network down -> fall back to cache
            print(f"note: live pull failed ({e.__class__.__name__}); using cache")
    if fresh:
        return fresh
    if os.path.exists(cache_path):
        with open(cache_path) as f:
            snap = json.load(f)
        age = time.time() - snap.get("pulled_at", 0)
        if offline and age > ttl:
            print(f"note: cached snapshot is {age/3600:.1f}h old")
        return snap["products"], snap["tickers"]
    raise RuntimeError("no live connection and no cached snapshot; run online once")


def screen(products: list, tickers: dict,
           risk_cap_inr: float = 55.0,
           margin_cap_inr: float = 2500.0,
           volume_floor_usd: float = 100_000.0,
           friction_cap_r: float = 0.20,
           stop_pct: Optional[float] = None,
           equity_inr: float = 5000.0,
           max_concurrent: int = 3) -> List[dict]:
    """Score every operational perpetual against the tiered filters."""
    out = []
    for p in products:
        if p.get("contract_type") != "perpetual_futures":
            continue
        if p.get("trading_status") not in (None, "operational"):
            continue
        sym = p["symbol"]
        t = tickers.get(sym, {})
        px = float(t.get("mark_price") or 0)
        cv = float(p.get("contract_value") or 0)
        if px <= 0 or cv <= 0:
            out.append({"symbol": sym, "excluded": "no mark price"})
            continue
        taker = float(p.get("taker_commission_rate") or 0.0005)
        stop = stop_pct or MEASURED_STOP_PCT.get(sym, DEFAULT_STOP_PCT)
        notional_inr = px * cv * INR_PER_USD
        margin_inr = notional_inr / 10.0
        risk1_inr = notional_inr * stop
        turnover_usd = float(t.get("turnover") or 0)
        # Round-trip fee cost in R at this symbol's stop distance (spread and
        # slippage excluded - they only add).
        fee_rt_pct = 2.0 * taker * (1.0 + GST_ON_FEES)
        fee_r = fee_rt_pct / stop
        agg_margin_ok = margin_inr * max_concurrent <= 0.60 * equity_inr

        row = {
            "symbol": sym, "mark_usd": round(px, 6), "cv": cv,
            "taker_bps": round(taker * 10_000, 1),
            "stop_pct": round(stop * 100, 2),
            "min_lot_notional_inr": round(notional_inr, 0),
            "min_lot_margin_inr": round(margin_inr, 0),
            "risk_1ct_inr": round(risk1_inr, 2),
            "risk_1ct_r": round(risk1_inr / (equity_inr * 0.01), 2),
            "turnover_24h_usd": round(turnover_usd, 0),
            "fee_rt_r": round(fee_r, 3),
            "t1_lot": risk1_inr <= risk_cap_inr and margin_inr <= margin_cap_inr,
            "t1b_aggregate_margin_ok": agg_margin_ok,
            "t2_liquidity": turnover_usd >= volume_floor_usd,
            "t3_friction": fee_r <= friction_cap_r,
            "excluded": None,
        }
        row["eligible"] = row["t1_lot"] and row["t2_liquidity"] and row["t3_friction"]
        out.append(row)
    return out


def summarize(rows: List[dict]) -> dict:
    scored = [r for r in rows if not r.get("excluded")]
    t1 = [r for r in scored if r["t1_lot"]]
    t12 = [r for r in t1 if r["t2_liquidity"]]
    t123 = [r for r in t12 if r["t3_friction"]]
    return {
        "perpetuals_scored": len(scored),
        "t1_lot_eligible": len(t1),
        "t1_plus_liquidity": len(t12),
        "fully_eligible": len(t123),
        "fee_tier_counts_t1": _count(t1, "taker_bps"),
        "fee_tier_counts_full": _count(t123, "taker_bps"),
    }


def _count(rows: List[dict], key: str) -> dict:
    c: Dict[float, int] = {}
    for r in rows:
        c[r[key]] = c.get(r[key], 0) + 1
    return {f"{k}bps": v for k, v in sorted(c.items())}


def screen_deployable_universe(min_24h_turnover_usd: float = 100_000.0,
                               risk_cap_inr: float = 55.0,
                               margin_cap_inr: float = 2500.0,
                               offline: bool = False):
    """Convenience helper returning a pandas DataFrame of eligible liquid assets."""
    import pandas as pd
    products, tickers = load_snapshot(offline=offline)
    rows = screen(products, tickers, risk_cap_inr=risk_cap_inr,
                  margin_cap_inr=margin_cap_inr,
                  volume_floor_usd=min_24h_turnover_usd)
    elig = [r for r in rows if r.get("eligible")]
    return pd.DataFrame(elig)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--risk-cap", type=float, default=55.0,
                    help="1-contract stop risk cap in INR (default 55)")
    ap.add_argument("--margin-cap", type=float, default=2500.0,
                    help="10X margin cap per position in INR (default 2500)")
    ap.add_argument("--volume-floor", type=float, default=100_000.0,
                    help="24h USD turnover floor (default 100000)")
    ap.add_argument("--friction-cap", type=float, default=0.20,
                    help="max fee cost in R at stop distance (default 0.20)")
    ap.add_argument("--stop-pct", type=float, default=None,
                    help="override stop distance for all symbols (default: "
                         "audited medians / 1%% generic)")
    ap.add_argument("--max-concurrent", type=int, default=3,
                    help="concurrent positions for aggregate margin check")
    ap.add_argument("--csv", default=None, help="write full table to CSV")
    ap.add_argument("--json", default=None, help="write rows+summary to JSON")
    ap.add_argument("--offline", action="store_true", help="use cached snapshot")
    args = ap.parse_args()

    products, tickers = load_snapshot(offline=args.offline)
    rows = screen(products, tickers, risk_cap_inr=args.risk_cap,
                  margin_cap_inr=args.margin_cap,
                  volume_floor_usd=args.volume_floor,
                  friction_cap_r=args.friction_cap, stop_pct=args.stop_pct,
                  max_concurrent=args.max_concurrent)
    s = summarize(rows)

    print("Delta India Universe Screener - QR-2026-1114-R1 (INR Denominated)")
    vol_floor_inr = args.volume_floor * INR_PER_USD
    vol_floor_str = f"₹{vol_floor_inr/1e7:.2f} Cr" if vol_floor_inr >= 1e7 else f"₹{vol_floor_inr/1e5:.1f} Lakh"
    print(f"Filters: 1-Lot Risk <= ₹{args.risk_cap:.0f} | 10X Margin <= ₹{args.margin_cap:,.0f} | "
          f"24h Turnover >= {vol_floor_str} | Fee Cost <= {args.friction_cap}R\n")
    print(f"Perpetuals Scored:            {s['perpetuals_scored']:>4}")
    print(f"T1  Lot-Feasible:             {s['t1_lot_eligible']:>4}")
    print(f"T2  + Liquidity Floor:        {s['t1_plus_liquidity']:>4}   (>= {vol_floor_str})")
    print(f"T3  + Fee-Friction Cap:       {s['fully_eligible']:>4}   <- Active Deployable Basket")
    print(f"Fee Tiers (T1):               {s['fee_tier_counts_t1']}")
    print(f"Fee Tiers (T3):               {s['fee_tier_counts_full']}")

    elig = sorted((r for r in rows if r.get("eligible")),
                  key=lambda r: -r["turnover_24h_usd"])
    print(f"\nFully Eligible Active Basket ({len(elig)} Assets), by 24h Turnover:")
    print(f"{'Symbol':14} {'1-Lot Notional':>15} {'10X Margin':>12} {'1-Lot Risk':>12} {'Fee Drag':>10} {'24h Turnover (INR)':>20}")
    for r in elig:
        t_inr = r['turnover_24h_usd'] * INR_PER_USD
        t_str = f"₹{t_inr/1e7:>8.2f} Cr" if t_inr >= 1e7 else f"₹{t_inr/1e5:>8.1f} Lakh"
        print(f"{r['symbol']:14} ₹{r['min_lot_notional_inr']:>14,.0f} ₹{r['min_lot_margin_inr']:>11,.0f} ₹{r['risk_1ct_inr']:>11.2f} "
              f"{r['fee_rt_r']:>9.3f}R {t_str:>20}")

    # spotlight on the audited headline seven
    print("\nAudited Headline Seven under the same INR filters:")
    for sym in ["ARMBUSD", "MRVLBUSD", "HOODBUSD", "XAUTUSD", "PAXGUSD",
                "BANKUSD", "GRAMUSD"]:
        r = next((x for x in rows if x["symbol"] == sym), None)
        if r:
            t_inr = r['turnover_24h_usd'] * INR_PER_USD
            t_str = f"₹{t_inr/1e7:>7.2f} Cr" if t_inr >= 1e7 else f"₹{t_inr/1e5:>7.1f} Lakh"
            print(f"  {sym:10} 24h Turnover {t_str:>12}  "
                  f"T1={r['t1_lot']} T2={r['t2_liquidity']} T3={r['t3_friction']} "
                  f"-> eligible={r['eligible']}")

    if args.csv:
        import csv as _csv
        keys = [k for k in rows[0].keys()]
        with open(args.csv, "w", newline="") as f:
            w = _csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            w.writerows(rows)
        print(f"\nwrote {args.csv}")
    if args.json:
        with open(args.json, "w") as f:
            json.dump({"summary": s, "rows": rows}, f, indent=1)
        print(f"wrote {args.json}")


if __name__ == "__main__":
    main()
