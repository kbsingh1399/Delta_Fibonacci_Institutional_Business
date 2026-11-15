# Institutional Code Logic, Backtest Reproducibility & Live Parity Audit
**Target Commit Audited:** `2e528b538307c79cc682063f02bd926d56390f47` (`feat(engine): set Maker Limit TP rebate model as production default (+440% equity, 9% max DD)`)  
**Target Venue:** Delta Exchange India (`api.india.delta.exchange`) | FIU-IND Compliant  
**Base Capital:** `₹5,000.00 INR` | **Leverage:** `10X` Isolated Margin | **FX Anchor:** `85.0 INR/USD`

---

## Executive Verdict

1. **Verification Suite (`python run.py --verify`):** **6 / 6 Institutional Checks PASS** (after remediating an offline socket fallback for CI/sandboxed environments and a critical repo-root `./KILL_SWITCH` path regression introduced when `delta_order_executor.py` moved under `engine/`).
2. **Backtest Reproducibility (`python run.py --backtest`):** **100% Bit-Exact Match Verified** against all reported Scenario 2 headline numbers (`₹27,031.25` final equity, `+440.63%`, `3,308` trades over `926` days, `36.1%` win rate, `Calmar 1.68`, `₹4,832.44` minimum capital floor).
3. **Forensic Parity & Accounting Audit:** While Scenario 2 reproduces bit-for-bit, line-by-line code inspection of `engine/run_inr_portfolio_backtest.py` vs. `engine/delta_live_scanner.py` and `engine/delta_order_executor.py` uncovered **material backtest-vs-live parity gaps** and **live execution edge cases**. All live execution / scanner safety bugs have been patched in-place.

---

## Task 1 — Full Verification Suite (`python run.py --verify`)

| Check | Component | Result | Key Verification Evidence |
|:---|:---|:---|:---|
| **`[1/6]`** | REST API / Snapshot Integrity | **PASS** | Live endpoints + automatic `engine/universe_snapshot.json` (`1,596` products/tickers) fallback verified |
| **`[2/6]`** | Candle Warm-Up & EMA Precision | **PASS** | `300` monotonic `4H` bars (`14,400s` step); `EMA 55/89/233` vs. textbook recursive delta = **`0.00e+00`** |
| **`[3/6]`** | Dynamic Min-Lot Sizer (`1.1R` Cap) | **PASS** | `ETHUSD` (`1 ct`, `₹52.7`), `TACUSD` (`294 ct`, `₹50.0`), `AMDBUSD` (`5 ct`, `₹44.2`), `XAUTUSD` (`11 ct`, `₹49.5`) -> `APPROVED_1R`; `BTCUSD` (`₹108.8`) & `SOLUSD` (`₹117.3`) -> `REJECTED_OVERSIZED` |
| **`[4/6]`** | Cluster Risk Governor | **PASS** | `INTCBUSD` vs `AMDBUSD` blocked (`SEMICONDUCTORS`); 4th trade `DOGEUSD` blocked (`3/3`); 3rd same-dir crypto & `-6.2%` BTC crash gate blocked |
| **`[5/6]`** | Signed Order Executor & Kill-Switch | **PASS** | `live=False` default; `1.05R` (`₹52.70`) accepted, `1.19R` (`₹59.50`) rejected; sub-cent `Decimal` tick quantization (`0.000129`) verified; repo-root `./KILL_SWITCH` halts execution |
| **`[6/6]`** | Integrated Live Scanner | **PASS** | Scans all `73` Alpha Basket perpetuals cleanly with batch-level governor state tracking |

---

## Task 2 — Backtest Reproducibility Verification (`python run.py --backtest`)

Executed `python run.py --backtest` against commit `2e528b538307c79cc682063f02bd926d56390f47`:

| Metric | Expected (Audited Spec) | Measured (`python run.py --backtest`) | Status |
|:---|---:|---:|:---|
| **Eligible Liquid Assets (`>= ₹85 Lakh`)** | `90` | `90` | **EXACT MATCH** |
| **Alpha Basket Assets (Excl. `CRYPTO_MAJORS`)** | `73` (`69` single-TF active) | `73` (`69` single-TF active) | **EXACT MATCH** |
| **Final Realized Equity** | **`₹27,031.25 INR (+440.63%)`** | **`₹27,031.25 INR (+440.63%)`** | **EXACT MATCH** |
| **Total Realized Profit** | **`₹22,031.25 INR`** (`₹713.79/mo`) | **`₹22,031.25 INR`** (`₹713.79/mo`) | **EXACT MATCH** |
| **Total Executed Trades** | **`3,308` trades / `926` days (`~3.6/day`)** | **`3,308` trades / `926` days (`~3.6/day`)** | **EXACT MATCH** |
| **Portfolio Win Rate** | **`36.1%`** (`1,193` W / `2,115` L) | **`36.1%`** (`1,193` W / `2,115` L) | **EXACT MATCH** |
| **Minimum Capital Floor** | **`₹4,832.44 INR` (`-3.35%`)** | **`₹4,832.44 INR` (`-3.35%`)** | **EXACT MATCH** |
| **Calmar Ratio** | **`1.68`** | **`1.68`** | **EXACT MATCH** |

*Note on Drawdown Denominator:* `run_inr_portfolio_backtest.py` (line 384) divides peak rupee drawdown (`₹5,170.71`, from equity peak `₹20,613.91` to trough `₹15,443.20`) by initial `₹5,000` capital (`103.41%`). Expressed as standard **peak-to-trough equity drawdown**, peak drawdown is **`25.08%`** (`₹5,170.71 / ₹20,613.91`).

---

## Task 3 — Forensic Parity & Fee-Accounting Divergences

1. **CRITICAL — Blanket 0.050R Maker Friction Applied to Taker Stop-Loss & Trailing Exits (+₹13,112 Over-Credit):**
   - On Delta Exchange India, maker fees (0.02% + 18% GST) apply *only* when a resting Limit Take-Profit fills.
   - Stop-loss triggers (`bracket_stop_loss_price` on mark price) and trailing stops execute as Stop-Market (Taker) orders (0.05% + 18% GST + slippage = 0.138R).
   - Across the 3,308 executed trades, 2,115 (63.9%) are stop-loss exits and 2,296 (69.4%) run trailing exits (`step_trail` or `trail_89`). Only 328 trades (9.9%) actually exited via a winning fixed Limit Take-Profit.
   - Re-pricing the trade set under exact venue fee mechanics with the live margin cap yields **₹13,828.64 INR (+176.6%)** to **₹13,919.25 INR (+178.4%)**.
2. **MEDIUM — `ribbon_map` Key Mismatch in Backtest:**
   - `delta_calmar_leaderboard.csv` uses ribbon names like `Classic_50_200`, `Fib_21_55`, `Fib_89_233`, `Fib_34_89`. The backtest `ribbon_map` lacked exact keys, defaulting all 69 single-TF symbols to `(34, 89, 233)`.
3. **HIGH — Governor & Sizer Enforcement in Backtest Loop:**
   - The backtest loop checks concurrency (max 3) and clusters, but assumes continuous fractional ₹50 risk without integer lot rounding, omitting `max_same_dir_crypto = 2` and the 60% margin cap.

---

## Task 4 — Live Execution Edge Cases Remediated

1. **Repo-Root `./KILL_SWITCH` Ignored:**
   - Fixed `delta_order_executor.py` to check repo root, engine dir, CWD, and instance path.
2. **Sub-Cent `round(price, 4)` Order Corruption:**
   - Upgraded `delta_order_executor.py` to `decimal.Decimal` `ROUND_HALF_UP` tick quantization, preventing sub-cent assets (`TACUSD`, `SOPHUSD`) from collapsing to zero or invalid ticks.
3. **Crash Gate & Directional Crypto Cap Wired:**
   - Scanner now computes `BTCUSD` 2-bar closed 4H return and passes `recent_pct_change`, `action`, and `cluster` to `validate_new_trade_entry()`.
4. **Intra-Batch Over-Allocation Guard:**
   - Scanner now tracks `simulated_active` across scan batches so multiple trades in one scan cannot violate the 3-position or 2-crypto limit.
5. **Atomic Position State Writes:**
   - Upgraded `save_active_positions()` to `tempfile.mkstemp` + `os.replace` to prevent file corruption on SIGINT.
