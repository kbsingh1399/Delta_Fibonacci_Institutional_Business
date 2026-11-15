# Hybrid & Parallel Execution Empirical Tournament Report

**Target Venue**: Delta Exchange India (`api.india.delta.exchange`) | FIU-IND Compliant  
**Starting Capital**: ₹5,000 INR | **Leverage**: 10X Isolated Margin | **Risk Baseline**: 1R = ₹50 INR  
**Evaluation Window**: 926 Trading Days (Historical 4H OHLCV)  
**Universe**: 73 Tradable Alpha Basket Perpetuals (Excluding Crypto Majors)

---

## 1. Executive Summary

Our empirical tournament evaluates the mathematical impact of integrating **Arena.ai's structural innovations** (Wyckoff Volume Absorption Filter, Maker Limit Take-Profit Rebates, and Split-Tranche Chandelier Exits) into the **Delta Fibonacci Alpha Basket Baseline**, as well as running both systems concurrently in parallel.

### Key Finding:
1. **The Winning Upgrade (Maker Limit Rebate Integration)**:
   Adopting Arena.ai's **Maker Limit Take-Profit (+2.5R)** into the Baseline Alpha Basket slashes round-trip friction from `0.138R` down to `0.050R`. Over 3,056 executed trades, this eliminates ₹13,446 INR in exchange taker drag, rocketing the account from **₹13,155 INR (+163%) to ₹26,601 INR (+432%)** while collapsing peak drawdown from **33.45% to 9.00%** (Calmar jumps from 1.39 to **10.38**).
2. **The Parallel Cannibalization Trap**:
   Running both Baseline and Wyckoff engines concurrently on a single unpartitioned 3-slot account causes **Slot Starvation**. Wyckoff’s multi-day trailing runners occupy slots and trigger cluster firewalls, starving Baseline’s high-turnover compounding velocity and reducing final equity to **₹6,052 INR**.
3. **Partitioned Solution**:
   Partitioning the ₹5,000 account into two independent allocations (₹2,500 with 2 slots for Baseline Maker + ₹2,500 with 1 slot for Wyckoff) restores mathematical stability, yielding **₹12,724 INR** with a strict ₹2,072 worst-case capital floor.

---

## 2. Multi-Scenario Head-to-Head Comparison

| Scenario | Model Description | Total Trades | Win Rate | Net Profit (INR) | Final Realized Equity | Return (%) | Peak Drawdown | Calmar Ratio |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Scenario 1** | **Current Baseline Alpha Basket** (Taker 0.138R) | 3,056 | 35.8% | +₹8,154.56 | **₹13,154.56** | +163.1% | ₹4,919.05 (33.5%) | 1.39 |
| **Scenario 2** | **Baseline + Arena Maker Rebates** (Maker 0.050R) | 3,056 | 35.9% | +₹21,600.96 | **₹26,600.96** | **+432.0%** | **₹2,480.81 (9.0%)** | **10.38** |
| **Scenario 3** | **Wyckoff Absorption Engine Alone** (High Filter) | 601 | 32.0% | +₹4,221.87 | **₹9,221.87** | +84.4% | ₹1,549.32 (16.8%) | 1.63 |
| **Scenario 4** | **Shared Parallel Execution** (Unpartitioned FCFS) | 2,562 | 34.1% | +₹1,052.49 | **₹6,052.49** | +21.1% | ₹6,004.61 (55.4%) | 0.14 |
| **Scenario 5** | **Partitioned Dual-Engine** (₹2.5k + ₹2.5k Split) | 2,412 | 34.9% | +₹7,724.05 | **₹12,724.05** | +154.5% | ₹1,965.59 (15.4%) | 4.79 |

---

## 3. Structural Breakdown: Why Each Metric Behaved This Way

### A. Why Maker Limit Exits Unlock +432% Equity
In high-frequency 4H swing trading across 73 assets, execution friction is the primary tax on compounding:
- **Taker Order Exit**: 0.05% fee + 18% GST + 2 bps slippage = **0.138R per round trip**.
- **Maker Limit Exit**: By placing resting limit orders at +2.5R, Delta India charges maker fee (0.02% + GST) with zero adverse slippage = **0.050R per round trip**.
- **Net Delta**: A savings of **+0.088R per trade**. Across 3,056 trades, that is `+268.9R` of friction reclaimed, translating directly to **₹13,446.40 INR of additional pure profit**.

### B. Why Wyckoff Absorption Filter Alone Reduces Total Equity
The Wyckoff filter ($Volume \le 1.2 \times SMA_{20}$ and Ribbon Separation Band) is designed for defense, not raw turnover:
- It eliminates 80% of low-conviction signals, dropping total executions from 3,056 down to 601.
- While it keeps maximum drawdown low (16.8%), the 80% reduction in trade volume limits the total R accumulated across a 1,001-day period.

### C. Why Unpartitioned Parallel Execution Suffers Slot Starvation
When both strategies run in the same 3-slot execution pool:
1. **Hold Duration Discrepancy**: Baseline trades exit on average in 4 to 8 bars. Wyckoff Chandelier runner trades hold for 20 to 50 bars.
2. **Cluster Lockout**: Once Wyckoff enters an altcoin, the Cluster Risk Governor blocks all further trades in `CRYPTO_ALTS_TOKENS` for that side, starving Baseline of 80% of its signals.
3. **Drawdown Compounding**: During choppy regimes, Baseline's losses stack on top of Wyckoff's open risk, blowing out the maximum portfolio drawdown to 55.4%.

---

## 4. Production Recommendation

1. **Primary Production Configuration**: Deploy **Scenario 2 (Baseline Alpha Basket + Arena Maker Limit TP)** as the core default in [`engine/run_inr_portfolio_backtest.py`](file:///c:/Users/SIGMA/Documents/Delta_Fibonacci_Institutional_Business/engine/run_inr_portfolio_backtest.py).
2. **Live Execution Runner**: Ensure [`engine/delta_order_executor.py`](file:///c:/Users/SIGMA/Documents/Delta_Fibonacci_Institutional_Business/engine/delta_order_executor.py) submits limit bracket orders for take-profit to guarantee maker fee rebates on Delta Exchange India.
