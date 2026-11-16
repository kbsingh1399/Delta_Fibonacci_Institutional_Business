# 🏛️ Master Quantitative Strategy Peer-Review Prompt
### For Hostile Peer Review on Arena.ai, Claude 3.7 / Sonnet 4, OpenAI o1/o3/GPT-4o, DeepSeek-R1, & Z.ai

---

Copy and paste the entire prompt block below directly into any frontier AI:

```markdown
# 🏛️ INSTITUTIONAL QUANTITATIVE AUDIT & CODEBASE REVIEW DIRECTIVE

### ROLE & PERSONA
You are a **Managing Director of Quantitative Research & Hostile Chief Risk Officer (CRO)** at a multi-billion dollar systematic macro hedge fund. Your expertise covers statistical arbitrage, market microstructure, execution friction, and rigorous econometric validation. Your duty is to conduct a **360-degree, zero-flattery, mathematically adversarial audit** of an automated live algorithmic trading strategy.

---

### REPOSITORY & SYSTEM METADATA
- **Repository**: `https://github.com/kbsingh1399/Delta_Fibonacci_Institutional_Business.git`
- **Branch**: `main`
- **Latest Commit**: `3eb6d4011fd3d23cb28350cd9d02a067b24886eb`
- **Execution Venue**: FundedNext MetaTrader 5 (`FundedNext-Server` v6230, Login `#11808165`)
- **Capital**: $4,701.64 USD Balance / Equity
- **Risk Budget**: Strict $10.00 USD fixed risk per trade (dynamic fractional lot sizing)
- **Timeframe**: 4-Hour (H4) Candle Close execution
- **Execution Engine**: `engine/mt5_alpha_trader_monitor.py` (autonomous 10-second tick scan loop)
- **Dashboard**: `dashboard_server.py` (real-time streaming radar on port 8088)

---

### CORE STRATEGY SPECIFICATION

#### 1. Setup Architecture: Fibonacci EMA Shelf Rejection
The strategy identifies institutional liquidity absorption and trend continuation at Fibonacci exponential moving average shelves ($EMA_{89}$ and $EMA_{55}$) filtered by macro trend direction ($EMA_{89}$ vs $EMA_{233}$):

- **Bullish Regime**: $EMA_{89} > EMA_{233}$
  - **Mid Shelf Entry**: Candle touches $EMA_{89} \times 1.002$ or $EMA_{55} \times 1.002$, closes $\ge EMA_{89}$, with **Lower Wick $\ge 25\%$** of total candle range.
- **Bearish Regime**: $EMA_{89} < EMA_{233}$
  - **Mid Shelf Entry**: Candle touches $EMA_{89} \times 0.998$ or $EMA_{55} \times 0.998$, closes $\le EMA_{89}$, with **Upper Wick $\ge 25\%$** of total candle range.
- **Color-Agnostic Invariant**: Candle color (green/red) is strictly ignored; only wick geometry and boundary rejection matter.
  $$\text{Lower Wick} = \frac{\min(\text{Open}, \text{Close}) - \text{Low}}{\text{High} - \text{Low}} \ge 0.25$$
  $$\text{Upper Wick} = \frac{\text{High} - \max(\text{Open}, \text{Close})}{\text{High} - \text{Low}} \ge 0.25$$

#### 2. Risk & Money Management
- **Stop Loss**:
  - Longs: $\text{SL} = \text{Low} - (0.30 \times \text{ATR}_{14})$
  - Shorts: $\text{SL} = \text{High} + (0.30 \times \text{ATR}_{14})$
- **Take Profit**: Fixed $2.5\text{R}$ to $3.5\text{R}$ asymmetric payoff ($3:1$ target risk-to-reward ratio) or step-trail runner (+0.5R locked at +2R, +2.0R locked at +4R, EMA55 trailing at +6R).
- **Execution Invariant**: Validated strictly on closed 4H bars within a 1,200-second freshness window.

#### 3. Portfolio Governance & Cluster Firewall
- **Max Portfolio Concurrency**: Maximum 3 concurrent open positions.
- **Cluster Isolation**: Maximum 1 open position per cluster across 8 uncorrelated asset classes:
  1. `Crypto` (3): `DOGUSD` (3.5R), `ETHUSD` (Step Trail), `XLMUSD` (3.0R)
  2. `Commodity_Energy` (1): `XAUUSD` (3.0R)
  3. `Indices` (4): `US30` (3.5R), `EUSTX50` (3.0R), `FRA40` (3.0R), `HK50` (2.5R)
  4. `Forex_JPY` (4): `CHFJPY` (3.0R), `EURJPY` (3.0R), `USDJPY` (2.5R), `CADJPY` (3.0R)
  5. `Forex_USD` (2): `USDCHF` (3.0R), `USDCNH` (3.0R)
  6. `Forex_EUR` (1): `EURNOK` (3.0R)
  7. `US_Equities` (5): `MSFT` (3.0R), `AMD` (3.0R), `BABA` (Step Trail), `NFLX` (3.0R), `NKE` (3.0R)
  8. `EU_Equities` (1): `MC` (3.5R)
  *Total*: 21 Elite Assets (pruned from 28 after dropping 7 persistent drag assets: `LNKUSD`, `XAGUSD`, `US2000`, `AUDJPY`, `NVDA`, `MBG`, `GOOG`).

---

### BACKTEST RESULTS ACROSS ALL TIME & DIRECTIONS (2024–2026)

| Time Horizon | Direction | Trades | Win Rate | Total Net Return | Net Profit ($10 Risk) | Profit Factor | Max Drawdown | Calmar Ratio |
|---|---|---|---|---|---|---|---|---|
| **2024 (H2)** | Both (All) | 187 | 32.1% | **+40.27 R** | +$402.70 | 1.30 | -13.00 R | **3.10** |
| 2024 (H2) | Longs Only | 132 | 35.6% | +50.02 R | +$500.20 | 1.56 | -6.30 R | 7.94 |
| 2024 (H2) | Shorts Only | 106 | 27.4% | -2.80 R | -$28.00 | 0.97 | -22.00 R | -0.13 |
| **2025 (Full)** | Both (All) | 273 | 30.4% | **+44.94 R** | +$449.40 | 1.23 | -24.70 R | **1.82** |
| 2025 (Full) | Longs Only | 174 | 31.0% | +27.19 R | +$271.90 | 1.22 | -12.35 R | 2.20 |
| 2025 (Full) | Shorts Only | 151 | 29.8% | +15.95 R | +$159.50 | 1.14 | -19.00 R | 0.84 |
| **2026 (YTD)** | Both (All) | 249 | 31.3% | **+70.00 R** | +$700.00 | 1.39 | -19.95 R | **3.51** |
| 2026 (YTD) | Longs Only | 126 | 27.8% | +0.70 R | +$7.00 | 1.01 | -19.95 R | 0.04 |
| 2026 (YTD) | Shorts Only | 162 | 33.3% | +71.35 R | +$713.50 | 1.63 | -18.05 R | 3.95 |
| **ALL-TIME (24-26)** | **Both (All)** | **701** | **31.4%** | **+159.60 R** | **+$1,596.00** | **1.32** | **-24.70 R** | **6.46** |

#### Multi-Timeframe Discovery:
- **H4 (4-Hour)**: **+77.50 R**, PF 1.22, WR 30.2% (The Institutional Sweet Spot).
- **H1 (1-Hour)**: **-123.90 R**, PF 0.82, WR 22.6% (Catastrophic retail noise & spread friction).
- **MTF (4H Macro + 1H Entry)**: **-76.50 R**, PF 0.86, WR 23.5% (Intraday false wicks pierce stops).

---

### YOUR AUDIT MISSION: ANSWER THESE 6 ADVERSARIAL QUESTIONS

Please conduct a deep forensic critique answering:

1. **Selection Bias & Basket Pruning**:
   - We dropped 7 assets (`LNKUSD`, `XAGUSD`, `US2000`, `AUDJPY`, `NVDA`, `MBG`, `GOOG`) that were consistent negative drags. Does this constitute lookahead/selection bias, or is it legitimate asset-class regime filtration? How should we periodically re-screen the universe out-of-sample?
2. **The EMA 89 vs EMA 233 Shelf Dichotomy**:
   - Our ablation test proved that entering on pullbacks to EMA 89 generates +159.60R, while entries at EMA 233 produced an aggregate -48R drag. What is the precise market microstructure reason for this (momentum continuation vs trend breakdown)? Is disabling the 233 shelf completely sound?
3. **Directional Complementarity**:
   - In 2024–2025, Longs drove +77.21R while Shorts broke even. In 2026, Shorts drove +71.35R while Longs broke even. Is this "two wings of an airplane" dynamic a robust feature of trend-following, or an artifact of market regime shifts?
4. **Execution Latency & Spread Friction on FundedNext MT5**:
   - H4 candle close evaluation introduces potential spread widening during rollover (21:00–22:00 UTC). Does the 0.30 ATR stop buffer adequately insulate against broker slippage and spread spikes on prop firm accounts?
5. **Drawdown Protection & Prop Firm Safety**:
   - FundedNext enforces a 5% daily drawdown and 10% overall drawdown limit. With $10 risk per trade on a $4,700 account (0.21% risk per trade) and Max 3 concurrent positions (0.63% maximum portfolio heat), what is the mathematical probability of a drawdown breach based on our Monte Carlo distribution?
6. **Next Level Upgrades**:
   - What high-impact, mathematically sound enhancements should be introduced next (e.g., dynamic volatility position sizing, session time filters, regime volatility switches, or correlation matrix gates)?

Provide an unsparing, institutional-grade evaluation with concrete mathematical reasoning and code recommendations.
```
