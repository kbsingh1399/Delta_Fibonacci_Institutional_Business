# Multi-Dimensional VWAP Standard Deviation Bands & MTF EMA Candle Rejection System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a complete, institutional Python-based multi-dimensional testing and execution engine on MetaTrader 5 (Blueberry Markets SVG Live) that sweeps, backtests, and validates price action "kiss and bounce" candle rejections across session VWAP bands (-2, -1, 0, +1, +2 SD) and multi-timeframe EMAs (5M, 15M, 1H, 4H) for the 23 Champion assets, enabling concurrent multi-setup execution per asset.

**Architecture:** A decoupled 5-module pipeline:
1. Multi-Timeframe Parquet Data Lake (`dump_blueberry_mtf.py`)
2. Session-Anchored VWAP Standard Deviation Engine (`engine/vwap_bands.py`)
3. Candle Rejection Geometry & Signal Engine (`engine/candle_rejection.py`)
4. Zero-Lookahead Multi-Dimensional Grid Backtester (`engine/multidim_grid_backtest.py`)
5. Portfolio Multi-Criteria Matrix Optimizer & Reporter (`scripts/optimize_multidim_basket.py`)

**Tech Stack:** Python 3.10+, MetaTrader5 API, pandas, numpy, pyarrow, scipy.
**Spec:** [docs/superpowers/specs/2026-11-19-multidim-vwap-ema-rejection-design.md](file:///c:/Users/SIGMA/Documents/Delta_Fibonacci_Institutional_Business/docs/superpowers/specs/2026-11-19-multidim-vwap-ema-rejection-design.md)

---

## Global Constraints & Invariants
- **Zero Regression**: Existing production files (`mt5_alpha_trader_live.py`, `MT5_Backtest.py`) remain 100% untouched and pristine.
- **Target Risk**: Fixed \$5.00 risk per trade with 0.01 min-lot fallback and account equity protection.
- **Zero Lookahead**: Signals confirm strictly on candle close ($i$); trade entry executes strictly at the open of the subsequent candle ($i+1$).
- **Realistic Slippage & Commission**: Account for broker spread and raw commissions (\$7/lot round turn).
- **Concurrency Model**: Concurrent independent positions permitted across timeframes with distinct magic numbers and independent \$5 risk limits.

---

## Review Focus & Critical Edge Cases
1. **Low Bar-Count VWAP Distortion**: Session VWAP standard deviations must not collapse on early session bars ($N < 5$); minimum sample guards must be enforced.
2. **Intrabar Path Modeling**: Backtesting trailing stops must simulate realistic adverse path sequencing (low-first on bullish bars, high-first on bearish bars) to prevent optimistic fill bias.
3. **Multi-Timeframe Timestamp Alignment**: Ensure 5M, 15M, 1H, and 4H bar open/close timestamps align precisely to prevent future information leakage.
4. **Broker Volume Discrepancies**: Use `tick_volume` consistently across Forex, Indices, and Crypto, handling assets where `real_volume` is zero.
5. **Session Midnight Rollover**: Ensure session anchor resets correctly at 00:00 UTC for Crypto and 00:00 Broker Server Time for Forex/Indices without duplicate bars.

---

## Task Decomposition

### Task 1: Multi-Timeframe MT5 Data Lake Ingestion (`scripts/dump_blueberry_mtf.py`)
**Files:**
- Create: `scripts/dump_blueberry_mtf.py`
- Create: `tests/test_data_lake.py`
- Output: `Blueberry_Data/{symbol}_{tf}.parquet` for 23 assets across `5M`, `15M`, `1H`, `4H`.

- [ ] Step 1.1: Write failing unit test in `tests/test_data_lake.py` verifying parquet lake loading, time schema, and ascending chronological order.
- [ ] Step 1.2: Implement `scripts/dump_blueberry_mtf.py` using `MetaTrader5.copy_rates_from_pos` to pull up to 50,000 bars for 5M and 15M, and full depth for 1H and 4H.
- [ ] Step 1.3: Run MT5 extraction script to populate `Blueberry_Data/` for the 23 Champion assets.
- [ ] Step 1.4: Run `tests/test_data_lake.py` and verify all tests pass.
- [ ] Step 1.5: Commit changes to git.

---

### Task 2: Session-Anchored VWAP & Standard Deviation Bands Engine (`engine/vwap_bands.py`)
**Files:**
- Create: `engine/vwap_bands.py`
- Create: `tests/test_vwap_bands.py`

- [ ] Step 2.1: Write unit test in `tests/test_vwap_bands.py` validating VWAP calculation, standard deviation band offsets, and session reset handling against known analytical vectors.
- [ ] Step 2.2: Implement `calculate_session_vwap_bands(df, anchor_mode="broker" | "utc")` in `engine/vwap_bands.py` computing:
  - Typical Price: $(H + L + C) / 3$
  - Cumulative VWAP: $\sum(P \times V) / \sum V$
  - Cumulative $\sigma$: $\sqrt{\sum V(P - \text{VWAP})^2 / \sum V}$
  - Bands: $\text{VWAP}$, $\text{VWAP} \pm 1.0\sigma$, $\text{VWAP} \pm 2.0\sigma$.
- [ ] Step 2.3: Add variance stabilization guard for the first 3 bars of a session to prevent division-by-zero or collapsed bands.
- [ ] Step 2.4: Run `tests/test_vwap_bands.py` and verify all tests pass.
- [ ] Step 2.5: Commit changes to git.

---

### Task 3: Candle Rejection Geometry & Signal Engine (`engine/candle_rejection.py`)
**Files:**
- Create: `engine/candle_rejection.py`
- Create: `tests/test_candle_rejection.py`

- [ ] Step 3.1: Write unit tests in `tests/test_candle_rejection.py` with synthetic candlestick patterns (pinbar touches, deep sweeps, body closes, false crosses) to assert signal accuracy.
- [ ] Step 3.2: Implement wick ratio and level touch detection:
  - $W_{lower} = (B_{bot} - L) / \text{Range} \ge 0.35$
  - $W_{upper} = (H - B_{top}) / \text{Range} \ge 0.35$
  - Touch tolerance: Level within $\pm 0.15 \times \text{ATR}$.
- [ ] Step 3.3: Implement the 6 VWAP band rules:
  - Below 0 SD & rejects 0 SD $\rightarrow$ SHORT
  - Below -1 SD & rejects -1 SD $\rightarrow$ SHORT
  - Above/Sweeping -2 SD & rejects -2 SD $\rightarrow$ LONG
  - Above 0 SD & rejects 0 SD $\rightarrow$ LONG
  - Above +1 SD & rejects +1 SD $\rightarrow$ LONG
  - Below/Sweeping +2 SD & rejects +2 SD $\rightarrow$ SHORT
- [ ] Step 3.4: Implement Multi-Timeframe EMA Kiss & Rejection rules for EMAs [20, 50, 100, 200].
- [ ] Step 3.5: Run `tests/test_candle_rejection.py` and verify all tests pass.
- [ ] Step 3.6: Commit changes to git.

---

### Task 4: Zero-Lookahead Multi-Dimensional Grid Backtester (`engine/multidim_grid_backtest.py`)
**Files:**
- Create: `engine/multidim_grid_backtest.py`
- Create: `tests/test_multidim_backtest.py`

- [ ] Step 4.1: Write test in `tests/test_multidim_backtest.py` verifying zero-lookahead entry (next bar open), stop-loss hit detection, and trailing exit mechanics.
- [ ] Step 4.2: Implement `simulate_multidim_backtest(df, symbol, tf, indicator_type, level_param, exit_mode, risk_usd=5.0)`:
  - Supports Fixed R:R (`1.5R`, `2.0R`, `2.5R`, `3.0R`, `3.5R`, `4.0R`) and Trailing exits (`trail_0.5R`, `trail_1.0R`, `trail_1.5R`).
  - Realistic intrabar execution path model (low-first for bullish, high-first for bearish).
  - 1.5 ATR stop loss from extreme candle wick.
  - Timeframe-specific cooldown (6 bars).
- [ ] Step 4.3: Validate execution metrics: Total Trades, Win Rate, Net R, Profit Factor, Max Drawdown, Expectancy.
- [ ] Step 4.4: Run tests and verify zero lookahead.
- [ ] Step 4.5: Commit changes to git.

---

### Task 5: Portfolio Multi-Criteria Matrix Optimizer & Reporter (`scripts/optimize_multidim_basket.py`)
**Files:**
- Create: `scripts/optimize_multidim_basket.py`
- Output: `audit/asset_multidim_champions.json`
- Output: `audit/MULTIDIM_ALPHA_BASKET_REPORT.md`

- [ ] Step 5.1: Build multiprocessing grid optimizer sweeping all 23 assets across:
  - Timeframes: `5M`, `15M`, `1H`, `4H`
  - Indicators: VWAP Bands (`-2`, `-1`, `0`, `+1`, `+2`) and EMAs (`20`, `50`, `100`, `200`)
  - Exits: `1.5R`, `2.0R`, `2.5R`, `3.0R`, `3.5R`, `4.0R`, `trail_0.5R`, `trail_1.0R`, `trail_1.5R`
- [ ] Step 5.2: Apply institutional qualification gates:
  - Minimum Trades: $\ge 25$
  - Profit Factor: $\ge 1.30$
  - Expected Value ($EV$): $> 0.15R$ / trade
  - Max Drawdown: $< 6.0R$
- [ ] Step 5.3: Select top champion setup per timeframe per asset, producing the Multi-Criteria Champion Matrix.
- [ ] Step 5.4: Execute optimizer across the 23 Champion assets and save `audit/asset_multidim_champions.json` and Markdown report.
- [ ] Step 5.5: Commit results to git.

---

### Task 6: Multi-Criteria Live Dispatcher Specification & Verification Interface (`engine/mt5_multicriteria_trader.py`)
**Files:**
- Create: `engine/mt5_multicriteria_trader.py`
- Create: `tests/test_multicriteria_dispatch.py`

- [ ] Step 6.1: Write unit test verifying multi-setup subscription, independent magic number assignment, and non-blocking trade dispatch.
- [ ] Step 6.2: Implement `MT5MultiCriteriaTrader` class:
  - Ingests `audit/asset_multidim_champions.json`.
  - Subscribes to live bar feeds across 5M, 15M, 1H, 4H.
  - Dispatches orders with tag `fib_mtf_{tf}_{setup}` and unique magic number.
  - Enforces \$5.00 fixed risk per trade and independent 10-second trailing ratchet loop.
- [ ] Step 6.3: Run verification tests and dry-run connection check against MT5 terminal.
- [ ] Step 6.4: Commit changes to git.

---

## Verification Plan

### Automated Verification
```powershell
# Run full unit test suite
pytest tests/test_data_lake.py tests/test_vwap_bands.py tests/test_candle_rejection.py tests/test_multidim_backtest.py tests/test_multicriteria_dispatch.py -v

# Run MTF Data Download for 23 Assets
python scripts/dump_blueberry_mtf.py --assets 23

# Run Multi-Dimensional Grid Optimization
python scripts/optimize_multidim_basket.py
```

### Manual Verification
1. Inspect `audit/asset_multidim_champions.json` to verify that each of the 23 assets has qualified multi-timeframe setups.
2. Verify that high-probability candle rejections match the visual signatures provided in the Coinglass reference charts.
3. Review cumulative portfolio equity curves and drawdown metrics in `audit/MULTIDIM_ALPHA_BASKET_REPORT.md`.
