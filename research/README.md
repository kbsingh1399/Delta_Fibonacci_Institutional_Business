# Research & Exploratory Studies

This directory contains exploratory research scripts, parameter optimizers, and historical grid search tools used during the mathematical formulation and stress-testing of the Delta Fibonacci Institutional Trading Strategy.

> **Note**: For production trading and live validation, use the core modules in [`engine/`](../engine/) or run via [`run.py`](../run.py).

---

## File Catalog

| File | Purpose |
|------|---------|
| `delta_master_calmar_optimizer.py` | Grid search across EMA spans (21/55/89, 34/89/144, 34/89/233, 55/89/233) and exit modes to maximize the Calmar ratio. |
| `mtf_confluence_optimizer.py` | Multi-Timeframe (MTF) 4H macro gate + 1H precision entry confluence simulator. |
| `simulate_business_portfolio.py` | Prototype event-driven portfolio simulator across multi-sector baskets. |
| `basket_scaling_study.py` | Comparative study evaluating portfolio performance scaling from 7 to 82+ liquid assets. |
| `generate_active_inr_universe.py` | Script used to generate active INR liquid universe candidate tables. |
| `monte_carlo_portfolio_stress.py` | 20,000-path Monte Carlo bootstrap simulation under fat-tailed execution slippage. |
| `run_full_business_audit.py` | Batch sweep script that produced the initial `audit/delta_calmar_leaderboard.csv`. |
| `trailing_stop_optimizer.py` | Parametric trailing stop loss and profit target ratchet evaluation. |
| `walk_forward_validator.py` | Walk-forward validation harness calculating Deflated Sharpe / Calmar ratios. |
| `z_ai_re_evaluation_v2_text.txt` | Quant review notes and institutional remediation feedback memo. |
