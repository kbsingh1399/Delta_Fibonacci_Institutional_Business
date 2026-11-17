# Delta Fibonacci Institutional Business — Quant Trading System

Institutional-grade, multi-asset algorithmic trading engine and quantitative radar tailored for **Blueberry Funded MT5 Accounts** ($2,500 Prime 2-Step tier, Account `#5126976`).

---

## 1. Minimal Repository Architecture

The codebase is engineered with strict adherence to the **Karpathy Simplicity Standards** and the **Ponytail Minimal Code Protocol**: exactly 1 data dump folder and 3 primary core files.

```
Delta_Fibonacci_Institutional_Business/
├── Blueberry_Data/                       # Single historical data cache (1H & 4H parquet files from 2023 to present)
├── MT5_Backtest.py                       # Single Backtesting Engine (100% Code Parity, 23 assets, dual-model, candle-direction trailing SL)
├── mt5_alpha_trader_live.py              # Single Primary Live Engine (Autonomous monitor, CRO governor, trailing SL, anti-chop)
├── dashboard_server.py                   # Single Dashboard Go Live File (Streaming web interface at http://localhost:8088)
├── STANDARDIZED_TRADING_DECISION_CHAIN.md# Institutional 10-Step Decision Protocol & Risk Mandate
├── audit/                                # Runtime state tracking (positions, cooldowns, health heartbeat, ledger)
├── .gitignore                            # Excludes local binary data and transient runtime logs
└── README.md
```

---

## 2. 23 Alpha Champion Assets & Configuration (Blueberry Markets)

Every asset in the curated universe operates on its empirical champion timeframe, target EMA shelf, model, and trailing stop strategy:

| Symbol | Cluster | Timeframe | Champion EMA | Execution Model | Trailing SL Mode | Target Risk | Hard Risk Cap |
| :--- | :--- | :---: | :---: | :--- | :--- | :---: | :---: |
| **SP500.p** | Indices | **1H** | **EMA 89** | `MODEL_1_PULLBACK` | **Trail 0.5R** | $10.00 | $12.00 (1.2R) |
| **JP225.p** | Indices | **1H** | **EMA 65** | `MODEL_1_PULLBACK` | **Trail 0.5R** | $10.00 | $12.00 (1.2R) |
| **DJ30.p** | Indices | **1H** | **EMA 55** | `MODEL_1_PULLBACK` | **Trail 0.5R** | $10.00 | $12.00 (1.2R) |
| **NAS100.p** | Indices | **1H** | **EMA 55** | `MODEL_1_PULLBACK` | **Trail 0.5R** | $10.00 | $12.00 (1.2R) |
| **HK50.p** | Indices | **1H** | **EMA 50** | `MODEL_1_PULLBACK` | **Trail 0.5R** | $10.00 | $12.00 (1.2R) |
| **XAUUSD.pi** | Commodity_Energy | **1H** | **EMA 55** | `MODEL_1_PULLBACK` | **Fixed 3.5R** | $10.00 | $12.00 (1.2R) |
| **XAGUSD.pi** | Commodity_Energy | **1H** | **EMA 160** | `MODEL_1_PULLBACK` | **Fixed 4.0R** | $10.00 | $12.00 (1.2R) |
| **USWTI.p** | Commodity_Energy | **1H** | **EMA 50** | `MODEL_1_PULLBACK` | **Trail 0.5R** | $10.00 | $12.00 (1.2R) |
| **BTCUSD.pi** | Crypto | **1H** | **EMA 233** | `MODEL_2_RECLAIM` | **Fixed 4.0R** | $10.00 | $12.00 (1.2R) |
| **ETHUSD.pi** | Crypto | **4H** | **EMA 89** | `MODEL_2_RECLAIM` | **Fixed 3.5R** | $10.00 | $12.00 (1.2R) |
| **XLMUSD.p** | Crypto | **1H** | **EMA 65** | `MODEL_2_RECLAIM` | **Fixed 3.5R** | $10.00 | $12.00 (1.2R) |
| **XRPUSD.pi** | Crypto | **1H** | **EMA 89** | `MODEL_2_RECLAIM` | **Trail 0.5R** | $10.00 | $12.00 (1.2R) |
| **ADAUSD.p** | Crypto | **1H** | **EMA 55** | `MODEL_1_PULLBACK` | **Fixed 4.0R** | $10.00 | $12.00 (1.2R) |
| **USDCAD.pi** | Forex_Majors | **1H** | **EMA 55** | `MODEL_1_PULLBACK` | **Fixed 3.5R** | $10.00 | $12.00 (1.2R) |
| **EURUSD.pi** | Forex_Majors | **1H** | **EMA 55** | `MODEL_1_PULLBACK` | **Trail 0.5R** | $10.00 | $12.00 (1.2R) |
| **GBPUSD.pi** | Forex_Majors | **4H** | **EMA 100** | `MODEL_2_RECLAIM` | **Fixed 3.5R** | $10.00 | $12.00 (1.2R) |
| **USDCHF.pi** | Forex_Majors | **1H** | **EMA 400** | `MODEL_2_RECLAIM` | **Trail 0.5R** | $10.00 | $12.00 (1.2R) |
| **GBPJPY.pi** | Forex_JPY | **4H** | **EMA 160** | `MODEL_1_PULLBACK` | **Fixed 2.5R** | $10.00 | $12.00 (1.2R) |
| **CADJPY.pi** | Forex_JPY | **4H** | **EMA 100** | `MODEL_1_PULLBACK` | **Fixed 2.5R** | $10.00 | $12.00 (1.2R) |
| **CHFJPY.pi** | Forex_JPY | **4H** | **EMA 160** | `MODEL_2_RECLAIM` | **Fixed 3.5R** | $10.00 | $12.00 (1.2R) |
| **USDJPY.pi** | Forex_JPY | **4H** | **EMA 89** | `MODEL_2_RECLAIM` | **Trail 0.5R** | $10.00 | $12.00 (1.2R) |
| **NZDSGD.pi** | Forex_Crosses_Exotics | **1H** | **EMA 100** | `MODEL_1_PULLBACK` | **Fixed 3.5R** | $10.00 | $12.00 (1.2R) |
| **GBPNOK.pi** | Forex_Crosses_Exotics | **1H** | **EMA 89** | `MODEL_2_RECLAIM` | **Trail 0.5R** | $10.00 | $12.00 (1.2R) |

---

## 3. Core Strategy Safeguards

1. **Strict Champion Model Isolation**: Assets assigned to `MODEL_1` execute ONLY Model 1 (Pullback Rejection). Assets assigned to `MODEL_2` execute ONLY Model 2 (Reclaim Spring).
2. **Anti-Whipsaw Kaufman ER & Pinbar Wick Exemption Gate**: Evaluates 10-bar Kaufman Efficiency Ratio ($ER \ge 0.18$). If $ER < 0.18$, noise is filtered unless an institutional pinbar rejection wick ($\ge 35\%$) is present at the champion EMA shelf. Requires true penetration beyond $\pm 0.25 \times \text{ATR}$ across the EMA; $\ge 2$ band crossings in 12 bars silences all setups.
3. **ATR-Normalized Slope Filter**: Model 1 requires normalized slope $\ge +0.02$ ATR/bar for BUY ($\le -0.02$ ATR/bar for SELL) over lookback (4 bars on 1H, 3 bars on 4H).
4. **Decisive Clearance Gate**: For Model 1, candle close must exceed EMA by $\ge 0.05 \times \text{ATR}$. For Model 2 Reclaims, candle close must exceed EMA by $\ge 0.12 \times \text{ATR}$ (or $1.5\times$ spread), eliminating noise trap blips.
5. **Causal Candle Closure & Post-Exit Cooldown**: All signals evaluate strictly upon completed bar close (zero lookahead). Enforces strict 12-bar post-exit cooldown (12h for 1H, 48h for 4H).

---

## 4. Execution & Risk Governance (Multi-Algorithm Coexistence)

* **Multi-EA Isolation**:
  * Other algorithmic strategies operate on account `#5126976`.
  * The Fibonacci engine places orders strictly tagged with `comment="fib_orders"` and `magic=202611`.
  * The position synchronization loop monitors and manages **only** orders with `fib_orders`, completely ignoring all external algorithm trades.
* **Blueberry Funded Prime 2-Step Risk Governor**:
  * Lifetime 10% static floor: **$2,250.00 USD** ($250.00 buffer on $2,500 starting equity).
  * Daily 4% loss limit: **$100.00 USD**, anchored to 00:00 broker server time (UTC+2) to `max(balance, equity)`.
  * 80% throttle threshold: **$80.00 USD** (halts new entries, manages open positions).
  * 90% risk budget soft cap: `used_loss + open_risk + new_trade_risk <= $90.00`.
  * Target risk per trade: **$10.00 USD** (1.0R) or broker minimum lot size.
  * Hard risk cap per trade: **$12.00 USD** (1.2R maximum).
  * Concurrency: Max 4 global slots, max 1 open position per cluster.

---

## 5. Quickstart

### Backtesting (100% Code-Parity on Blueberry Markets Data)
```bash
python MT5_Backtest.py
```

### Live Engine & Dashboard
```bash
# Start autonomous monitor daemon
python -u mt5_alpha_trader_live.py

# Start live web dashboard
python dashboard_server.py
```
Dashboard available at `http://localhost:8088`.
