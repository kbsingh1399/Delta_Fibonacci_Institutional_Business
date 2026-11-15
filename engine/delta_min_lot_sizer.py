"""delta_min_lot_sizer.py: Institutional Delta Exchange India Position & Lot Sizer.
Accurately maps fractional contract values (0.0... lots) and 10X leverage margin
for ₹5,000 INR base capital on api.india.delta.exchange.
"""

import os
import sys
import json
import time
import urllib.request
from typing import Dict, Any, Optional, Tuple

DELTA_API = "https://api.india.delta.exchange"
DELTA_FX_RATE = 85.0  # Delta Exchange India platform conversion rate (USD/INR)
CACHE_FILE = os.path.join(os.path.dirname(__file__), "delta_products_cache.json")
CACHE_EXPIRY_SEC = 3600  # 1 hour cache

def fetch_delta_products(force_refresh: bool = False) -> Dict[str, Dict[str, Any]]:
    """Fetches and caches perpetual futures specifications from Delta Exchange India."""
    if not force_refresh and os.path.exists(CACHE_FILE):
        try:
            mtime = os.path.getmtime(CACHE_FILE)
            if time.time() - mtime < CACHE_EXPIRY_SEC:
                with open(CACHE_FILE, "r") as f:
                    return json.load(f)
        except Exception:
            pass

    url = f"{DELTA_API}/v2/products"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode('utf-8')).get('result', [])
        
        products = {}
        for p in data:
            if p.get('contract_type') == 'perpetual_futures' and p.get('trading_status') == 'operational':
                sym = p.get('symbol')
                products[sym] = {
                    'id': p.get('id'),
                    'symbol': sym,
                    'contract_value': float(p.get('contract_value', 1.0)),
                    'tick_size': float(p.get('tick_size', 0.01)),
                    'initial_margin': float(p.get('initial_margin', 10.0)),
                    'maintenance_margin': float(p.get('maintenance_margin', 5.0)),
                    'quoting_asset': p.get('quoting_asset', {}).get('symbol', 'USD')
                }
        
        with open(CACHE_FILE, "w") as f:
            json.dump(products, f, indent=2)
        return products
    except Exception as e:
        if os.path.exists(CACHE_FILE):
            with open(CACHE_FILE, "r") as f:
                return json.load(f)
        raise RuntimeError(f"Failed to fetch Delta India products: {e}")

def compute_safe_leverage(
    entry_price: float,
    stop_price: float,
    target_leverage: float = 25.0,
    maintenance_margin_pct: float = 0.025
) -> float:
    """Computes maximum safe leverage so liquidation price sits beyond stop loss with buffer.
    Guarantees premature liquidation cannot happen before stop-loss executes.
    """
    if entry_price <= 0:
        return target_leverage
    stop_dist_pct = abs(entry_price - stop_price) / entry_price
    required_margin_pct = stop_dist_pct + maintenance_margin_pct + 0.010
    max_safe = 1.0 / required_margin_pct if required_margin_pct > 0 else target_leverage
    return max(1.0, min(float(target_leverage), float(int(max_safe))))


def calculate_delta_order_sizing(
    symbol: str,
    entry_price: float,
    stop_price: float,
    equity_inr: float = 5000.0,
    risk_pct: float = 0.01,
    leverage: float = 10.0,
    max_capital_margin_pct: float = 0.50, # Max 50% account margin per single trade (₹2500)
    products_dict: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Calculates integer contract lots, required 10X margin, and exact rupee risk.
    
    Delta Exchange Rule:
    - Order 'size' in API is an INTEGER number of contracts (1, 2, 3, ...).
    - 1 contract represents `contract_value` units of underlying asset (e.g. 0.001 BTC, 0.01 ETH, 0.01 ARM).
    - Leverage reduces required margin (Margin = Notional / Leverage).
    - Risk is determined by stop distance (Risk = Lots * contract_value * |entry - stop| * 85 INR).
    """
    if products_dict is None:
        products_dict = fetch_delta_products()
        
    spec = products_dict.get(symbol)
    if not spec:
        # Fallback to standard 1.0 contract value if unlisted
        contract_value = 1.0
        tick_size = 0.01
    else:
        contract_value = spec['contract_value']
        tick_size = spec['tick_size']

    # 1. Target Risk in INR and USD
    target_risk_inr = equity_inr * risk_pct  # e.g. ₹50 for 1% of ₹5,000
    target_risk_usd = target_risk_inr / DELTA_FX_RATE

    # 2. Risk Distance per unit of underlying
    risk_dist_usd = abs(entry_price - stop_price)
    if risk_dist_usd <= 0:
        return {'status': 'REJECTED_ZERO_RISK', 'reason': 'Stop price equals entry price'}

    # 3. Risk per single contract (1 lot)
    contract_risk_usd = risk_dist_usd * contract_value
    contract_risk_inr = contract_risk_usd * DELTA_FX_RATE

    # 4. Compute optimal integer contract lots
    # Floor (never round ABOVE the risk budget); min-lot forces 1 contract and
    # the risk cap below decides. QR-2026-1115 fix: round() could overshoot.
    ideal_lots = target_risk_inr / contract_risk_inr
    contracts = max(1, int(ideal_lots))

    # 5. Financial Metrics for calculated position
    actual_risk_inr = contracts * contract_risk_inr
    actual_risk_pct = (actual_risk_inr / equity_inr) * 100.0
    
    maint_margin_pct = float(spec.get('maintenance_margin', 2.5)) / 100.0 if spec else 0.025
    safe_leverage = compute_safe_leverage(entry_price, stop_price, target_leverage=leverage, maintenance_margin_pct=maint_margin_pct)
    if spec and spec.get('initial_margin'):
        try:
            prod_max_lev = 100.0 / float(spec['initial_margin'])
            safe_leverage = min(safe_leverage, prod_max_lev)
        except Exception:
            pass

    notional_usd = contracts * contract_value * entry_price
    notional_inr = notional_usd * DELTA_FX_RATE
    margin_required_inr = notional_inr / safe_leverage

    # 6. Safety & Allocation Validation
    status = "APPROVED_1R"
    reason = "Exact target risk sizing achieved."
    
    if margin_required_inr > (equity_inr * max_capital_margin_pct):
        status = "REJECTED_MARGIN_EXCEEDED"
        reason = f"Required margin (₹{margin_required_inr:.1f} at {safe_leverage}X) exceeds {max_capital_margin_pct*100}% of equity (₹{equity_inr*max_capital_margin_pct:.1f})"
    elif actual_risk_inr > (target_risk_inr * 1.1):
        # QR-2026-1115 fix: hard cap at 1.1R (Rs 55 on the Rs 50 budget).
        # The former APPROVED_FLEX band (1.25x-2.5x target) is removed.
        status = "REJECTED_OVERSIZED"
        reason = f"Sized risk ₹{actual_risk_inr:.1f} ({actual_risk_pct:.1f}%) exceeds the 1.1R hard cap (₹{target_risk_inr * 1.1:.0f})"

    return {
        'symbol': symbol,
        'status': status,
        'reason': reason,
        'contracts': contracts,
        'contract_value': contract_value,
        'underlying_units': round(contracts * contract_value, 6),
        'entry_price': entry_price,
        'stop_price': stop_price,
        'tick_size': tick_size,
        'effective_leverage': safe_leverage,
        'notional_usd': round(notional_usd, 2),
        'notional_inr': round(notional_inr, 2),
        'margin_10x_inr': round(margin_required_inr, 2),
        'actual_risk_inr': round(actual_risk_inr, 2),
        'actual_risk_pct': round(actual_risk_pct, 2),
        'target_risk_inr': round(target_risk_inr, 2)
    }

if __name__ == "__main__":
    print("Testing Delta Exchange India Position & Lot Sizer (Equity: ₹5,000, 10X Leverage)...")
    products = fetch_delta_products()
    print(f"Loaded {len(products)} active perpetual products.")
    
    test_cases = [
        ("ETHUSD", 3100.0, 3038.0),   # 2% stop
        ("BTCUSD", 64000.0, 62400.0), # 2.5% stop
        ("ARMBUSD", 140.0, 135.8),    # 3% stop
        ("XAUTUSD", 2600.0, 2561.0),  # 1.5% stop
        ("DOGEUSD", 0.15, 0.1455),    # 3% stop
        ("SOLUSD", 210.0, 204.75),    # 2.5% stop
    ]
    
    print("\n" + "="*105)
    print(f"{'Symbol':<10} {'Lots':<6} {'ContractVal':<12} {'Notional(₹)':<14} {'10X Margin(₹)':<14} {'Risk(₹)':<10} {'Risk%':<8} {'Status':<16}")
    print("="*105)
    for sym, entry, stop in test_cases:
        res = calculate_delta_order_sizing(sym, entry, stop, equity_inr=5000.0, risk_pct=0.01, leverage=10.0, products_dict=products)
        print(f"{res['symbol']:<10} {res.get('contracts', 0):<6} {str(res.get('contract_value', '')):<12} ₹{res.get('notional_inr', 0):<12.1f} ₹{res.get('margin_10x_inr', 0):<12.1f} ₹{res.get('actual_risk_inr', 0):<8.1f} {res.get('actual_risk_pct', 0):<6.2f}% {res['status']:<16}")
