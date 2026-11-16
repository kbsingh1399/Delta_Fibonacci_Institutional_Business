# FundedNext MT5 Fibonacci Institutional Business & Production Engine

> **Deployment Venue**: FundedNext MetaTrader 5 (`FundedNext-Server`) | Account `#11808165`  
> **Base Capital / Balance**: $4,701.64 USD  
> **Per-Trade Risk Budget**: Fixed $10.00 USD (Strict invariant)  
> **Portfolio Concurrency**: Max 3 concurrent positions, Max 1 per cluster  
> **Timeframe**: 4-Hour (H4) Candle Close  
> **Live Dashboard**: [http://localhost:8088](http://localhost:8088)  
> **Repository**: `https://github.com/kbsingh1399/Delta_Fibonacci_Institutional_Business.git`  
> **Branch**: `main`  

---

## 1. Executive Summary & Strategy Formulation

The **FundedNext MT5 Fibonacci Institutional Engine** is an autonomous algorithmic trading system deployed on FundedNext MetaTrader 5. It systematically captures institutional liquidity shelf rejections at Fibonacci Exponential Moving Averages ($EMA_{89}$ and $EMA_{55}$) across 8 uncorrelated asset clusters.

### 1.1 Core Mathematical Invariants (Zero Deviation Between Backtest & Live)

```
       BULLISH REGIME: EMA 89 > EMA 233
       ─────────────────────────────────────────────────────────────
       [Price Trending Above EMAs] ───┐
                                      ▼  Pullback into Value Zone
       EMA 89 / 55 (Mid Shelf) ───────┴──────▲ (Long Entry: Lower Wick >= 25%)
       ─────────────────────────────────────────────────────────────

       BEARISH REGIME: EMA 89 < EMA 233
       ─────────────────────────────────────────────────────────────
       [Price Trending Below EMAs] ───┐
                                      ▼  Rally into Value Zone
       EMA 89 / 55 (Mid Shelf) ───────┴──────▼ (Short Entry: Upper Wick >= 25%)
       ─────────────────────────────────────────────────────────────
```

1. **Color-Agnostic Wick Geometry**:
   - Longs: $\text{Lower Wick} = \frac{\min(\text{Open}, \text{Close}) - \text{Low}}{\text{High} - \text{Low}} \ge 25\%$.
   - Shorts: $\text{Upper Wick} = \frac{\text{High} - \max(\text{Open}, \text{Close})}{\text{High} - \text{Low}} \ge 25\%$.
2. **Mid EMA 89 & 55 Shelf Priority (Zero EMA233 Drag)**:
   - Forensic 360-degree quantitative audit proved that EMA 89 pullbacks provide the highest continuation edge (**+159.60R / Calmar 6.46**), while deep pullbacks to EMA 233 signal trend exhaustion.
3. **Execution Freshness**:
   - Trades are evaluated strictly on confirmed H4 candle close within a 1,200-second freshness window. Stale bars are never traded.
4. **Precision Money Management**:
   - Stop Loss: 1.0R = $\text{Low} - (0.30 \times \text{ATR}_{14})$ for Longs, $\text{High} + (0.30 \times \text{ATR}_{14})$ for Shorts.
   - Target: Optimal assigned fixed R:R (2.5R to 3.5R depending on cluster volatility) or step trail.
   - Sizing: Exact dynamic lot size for $10.00 USD risk based on broker margin and tick value.

---

## 2. Elite 21 Positive-Alpha Basket (8 Uncorrelated Clusters)

| Cluster | Assets | Setups | Exits |
|---|---|---|---|
| **Crypto** | `DOGUSD`, `ETHUSD`, `XLMUSD` | `canonical`, `wyckoff_institutional_filtered` | 3.5R, Step Trail, 3.0R |
| **Commodities** | `XAUUSD` | `canonical` | 3.0R |
| **Equity Indices** | `US30`, `EUSTX50`, `FRA40`, `HK50` | `wyckoff_institutional_filtered` | 3.5R, 3.0R, 2.5R |
| **Forex JPY** | `CHFJPY`, `EURJPY`, `USDJPY`, `CADJPY` | `canonical`, `wyckoff_swing_sweep`, `turbo_tri_tier` | 3.0R, 2.5R |
| **Forex USD** | `USDCHF`, `USDCNH` | `wyckoff_institutional_filtered`, `canonical` | 3.0R |
| **Forex EUR** | `EURNOK` | `wyckoff_swing_sweep` | 3.0R |
| **US Equities** | `MSFT`, `AMD`, `BABA`, `NFLX`, `NKE` | `turbo_tri_tier`, `canonical` | 3.0R, Step Trail |
| **EU Equities** | `MC` | `canonical` | 3.5R |

---

## 3. Backtest & 360-Degree Quantitative Performance

| Time Window | Trades | Win Rate | Net Return | Net Profit ($10 Risk) | Profit Factor | Max Drawdown | Calmar Ratio |
|---|---|---|---|---|---|---|---|
| **2024 (H2)** | 187 | 32.1% | **+40.27R** | **+$402.70** | 1.30 | -13.00R | 3.10 |
| **2025 (Full Year)** | 273 | 30.4% | **+44.94R** | **+$449.40** | 1.23 | -24.70R | 1.82 |
| **2026 (YTD)** | 249 | 31.3% | **+70.00R** | **+$700.00** | 1.39 | -19.95R | 3.51 |
| **ALL-TIME (2024–2026)** | **701** | **31.4%** | **+159.60R** | **+$1,596.00** | **1.32** | **-24.70R** | **6.46** |

---

## 4. Architecture & Primary Files

```
├── engine/
│   └── mt5_alpha_trader_monitor.py      # Core live trading daemon (10s scan loop, $10 risk)
├── dashboard_server.py                  # Single-file real-time web dashboard (port 8088)
├── dump_fundednext_data.py              # Historical MT5 bar harvester
├── MT5_Backtest.py                      # Vectorized MT5 backtesting engine
├── research/
│   ├── find_best_alpha_basket.py        # Alpha basket optimization engine
│   ├── health_check_alpha_basket.py     # Hourly autonomous health check script
│   └── scan_all_mt5_assets.py           # MT5 symbol specification scanner
├── audit/
│   ├── fundednext_h4_executed_trades.csv # Executed trades ledger
│   ├── mt5_health_status.json           # Live daemon pulse (heartbeat)
│   └── mt5_hourly_health_log.json       # Hourly persistent audit logs
└── scratch/                             # Quantitative audit & validation scripts
```

---

## 5. Live Operations & RAM Optimization

- **Trading Monitor**: `python -u engine/mt5_alpha_trader_monitor.py`
- **Dashboard**: `python dashboard_server.py` (accessible at `http://localhost:8088`)
- **Health Check**: `python research/health_check_alpha_basket.py`
- **RAM Optimization**: Garbage collection (`gc.collect()`) executes automatically every 15 scan cycles, preventing memory leaks and maintaining minimal RAM footprint (<110MB).
