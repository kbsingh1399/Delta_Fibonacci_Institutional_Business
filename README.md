# Delta Fibonacci Institutional Business — Quant Trading System

Institutional-grade, multi-asset algorithmic trading engine and quantitative radar tailored for **FundedNext MT5 Prop-Firm Accounts** ($5,000 tier, `#11808165`).

---

## 1. Minimal Repository Architecture

The codebase is engineered with strict adherence to the **Karpathy Simplicity Standards** and the **Ponytail Minimal Code Protocol**: exactly 1 data dump folder and 3 primary core files.

```
Delta_Fibonacci_Institutional_Business/
├── FundedNext_Data/                      # Single historical data cache (192 parquet files: 96 _1H & 96 _4H)
├── MT5_Backtest.py                       # Single Backtesting Engine (100% Code Parity, 20 assets, dual-model, trailing SL)
├── mt5_alpha_trader_live.py              # Single Primary Live Engine (Autonomous monitor, CRO governor, trailing SL, anti-chop)
├── dashboard_server.py                   # Single Dashboard Go Live File (Streaming web interface at http://localhost:8088)
├── STANDARDIZED_TRADING_DECISION_CHAIN.md# Institutional 10-Step Decision Protocol & Risk Mandate
├── audit/                                # Runtime state tracking (positions, cooldowns, health heartbeat, ledger)
├── .gitignore                            # Excludes local binary data and transient runtime logs
└── README.md
```

---

## 2. 20 Alpha Champion Assets & Configuration

Every asset in the curated universe operates on its empirical champion timeframe, target EMA shelf, model, and trailing stop strategy:

| Symbol | Cluster | Timeframe | Champion EMA | Execution Model | Trailing SL Mode | Target Risk | Hard Risk Cap |
| :--- | :--- | :---: | :---: | :--- | :--- | :---: | :---: |
| **XAUUSD** | Commodity_Energy | **4H** | **EMA 50** | `MODEL_1_PULLBACK` | **Trail 1.5R** (at +2.5R, trail 1.5R to 8R) | $10.00 | $11.00 (1.1R) |
| **CHFJPY** | Forex_JPY | **4H** | **EMA 50** | `MODEL_2_RECLAIM` | **Trail 1.5R** (at +2.5R, trail 1.5R to 8R) | $10.00 | $11.00 (1.1R) |
| **US30** | Indices | **1H** | **EMA 65** | `MODEL_1_PULLBACK` | **Trail 1.0R** (at +2.0R, trail 1.0R to 6R) | $10.00 | $11.00 (1.1R) |
| **HK50** | Indices | **1H** | **EMA 50** | `MODEL_1_PULLBACK` | **Trail 0.5R** (at +1.5R, trail 0.5R to 6R) | $10.00 | $11.00 (1.1R) |
| **USDJPY** | Forex_JPY | **1H** | **EMA 50** | `MODEL_1_PULLBACK` | **Trail 0.5R** (at +1.5R, trail 0.5R to 6R) | $10.00 | $11.00 (1.1R) |
| **NZDJPY** | Forex_JPY | **1H** | **EMA 65** | `MODEL_1_PULLBACK` | **Fixed 3.5R** | $10.00 | $11.00 (1.1R) |
| **CADJPY** | Forex_JPY | **1H** | **EMA 55** | `MODEL_2_RECLAIM` | **Trail 1.5R** (at +2.5R, trail 1.5R to 8R) | $10.00 | $11.00 (1.1R) |
| **EURJPY** | Forex_JPY | **1H** | **EMA 277** | `MODEL_1_PULLBACK` | **Trail 1.0R** (at +2.0R, trail 1.0R to 6R) | $10.00 | $11.00 (1.1R) |
| **USDCHF** | Forex_USD | **1H** | **EMA 400** | `MODEL_1_PULLBACK` | **Trail 0.5R** (at +1.5R, trail 0.5R to 6R) | $10.00 | $11.00 (1.1R) |
| **USDCNH** | Forex_USD | **1H** | **EMA 160** | `MODEL_2_RECLAIM` | **Fixed 4.0R** | $10.00 | $11.00 (1.1R) |
| **NZDSGD** | Forex_Exotic | **1H** | **EMA 89** | `MODEL_1_PULLBACK` | **Fixed 4.0R** | $10.00 | $11.00 (1.1R) |
| **ETHUSD** | Crypto | **1H** | **EMA 100** | `MODEL_1_PULLBACK` | **Trail 1.5R** (at +2.5R, trail 1.5R to 8R) | $10.00 | $11.00 (1.1R) |
| **ADAUSD** | Crypto | **1H** | **EMA 100** | `MODEL_1_PULLBACK` | **Fixed 4.0R** | $10.00 | $11.00 (1.1R) |
| **DOGUSD** | Crypto | **1H** | **EMA 55** | `MODEL_1_PULLBACK` | **Trail 1.0R** (at +2.0R, trail 1.0R to 6R) | $10.00 | $11.00 (1.1R) |
| **XLMUSD** | Crypto | **1H** | **EMA 233** | `MODEL_1_PULLBACK` | **Trail 1.5R** (at +2.5R, trail 1.5R to 8R) | $10.00 | $11.00 (1.1R) |
| **MSFT** | US_Equities | **1H** | **EMA 65** | `MODEL_1_PULLBACK` | **Trail 1.5R** (at +2.5R, trail 1.5R to 8R) | $10.00 | $11.00 (1.1R) |
| **AMD** | US_Equities | **1H** | **EMA 89** | `MODEL_1_PULLBACK` | **Trail 1.0R** (at +2.0R, trail 1.0R to 6R) | $10.00 | $11.00 (1.1R) |
| **NKE** | US_Equities | **1H** | **EMA 75** | `MODEL_1_PULLBACK` | **Trail 0.5R** (at +1.5R, trail 0.5R to 6R) | $10.00 | $11.00 (1.1R) |
| **NFLX** | US_Equities | **1H** | **EMA 65** | `MODEL_1_PULLBACK` | **Trail 0.5R** (at +1.5R, trail 0.5R to 6R) | $10.00 | $11.00 (1.1R) |
| **BABA** | US_Equities | **1H** | **EMA 300** | `MODEL_1_PULLBACK` | **Trail 1.0R** (at +2.0R, trail 1.0R to 6R) | $10.00 | $11.00 (1.1R) |

---

## 3. Core Strategy Safeguards

1. **Strict Champion Model Isolation**: Assets assigned to `MODEL_1` execute ONLY Model 1 (Pullback Rejection). If slope or rejection wick criteria fail, the setup terminates immediately with zero cross-model fallback.
2. **Anti-Whipsaw Kaufman ER & Banded Crossing Gate**: Evaluates 10-bar Kaufman Efficiency Ratio ($ER \ge 0.28$). If $ER < 0.28$, market is classified as noise and skipped. Requires true penetration beyond $\pm 0.25 \times \text{ATR}$ across the EMA; $\ge 2$ band crossings in 12 bars silences all setups.
3. **ATR-Normalized Slope Filter**: Model 1 requires normalized slope $\ge +0.02$ ATR/bar for BUY ($\le -0.02$ ATR/bar for SELL) over lookback (4 bars on 1H, 3 bars on 4H).
4. **Decisive Clearance Gate**: For Model 1, candle close must exceed EMA by $\ge 0.05 \times \text{ATR}$. For Model 2 Reclaims, candle close must exceed EMA by $\ge 0.12 \times \text{ATR}$ (or $1.5\times$ spread), eliminating noise trap blips.
5. **Causal Candle Closure**: All signals evaluate strictly upon completed bar close (zero lookahead).

---

## 4. Execution & Risk Governance

* **Dual-Mode Execution Pipeline**:
  * The engine first attempts automated Python order execution via `mt5.order_send()`.
  * If the broker evaluation server restricts automated EA orders (`retcode 10026`), the engine outputs an actionable console alert and surfaces a notification banner on the live dashboard (`http://localhost:8088`) with a 1-click **"COPY SETUP"** button.
  * Once punched in MT5 GUI, the background daemon auto-syncs the live ticket within 10 seconds via `sync_broker_positions()`, tracks MFE, and manages trailing SL.
* **FundedNext $5,000 Challenge Risk Governor**:
  * Lifetime 10% static floor: **$4,500.00 USD**.
  * Daily 5% loss limit: **$250.00 USD**, anchored to 00:00 broker server time (UTC+2) to `max(balance, equity)`.
  * 80% throttle threshold: **$200.00 USD** (halts new entries, manages open positions).
  * 90% risk budget soft cap: `used_loss + open_risk + new_trade_risk <= $225.00`.
  * Concurrency: Max 4 global slots, max 1 open position per cluster.
  * Hard risk cap per trade: **$11.00 USD** (1.1R maximum).

---

## 5. Quickstart

### Backtesting (100% Code-Parity on 20 Champion Assets)
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
