"""tests/test_quant_robustness.py: Unit tests for Institutional Quantitative Robustness
and Anti-Curve Fitting Suite (Numba Monte Carlo, Deflated Sharpe, Train/Test Split).
"""

import pytest
import numpy as np
import pandas as pd

from engine.quant_robustness import (
    run_numba_monte_carlo,
    calculate_deflated_sharpe_ratio,
    split_train_test_data,
    verify_anti_curve_fit
)


def test_numba_monte_carlo_execution():
    # Synthetic positive edge series: 60% win rate at 2.0R, 40% loss at -1.0R
    r_multiples = np.array([2.0, -1.0, 2.0, 2.0, -1.0, 2.0, -1.0, 2.0, -1.0, 2.0] * 10, dtype=float)
    mc = run_numba_monte_carlo(r_multiples, n_sims=5000, seed=42)

    assert mc["mc_median_r"] > 0
    assert mc["mc_p_profit"] >= 0.95
    assert mc["mc_max_dd_95"] > 0
    assert mc["mc_risk_of_ruin_20r"] < 0.05


def test_deflated_sharpe_ratio():
    # Consistent positive returns
    positive_series = np.array([1.5, 0.8, -1.0, 2.0, 1.2, -0.9, 1.8, 2.2, -1.0, 1.1] * 10)
    dsr_pos = calculate_deflated_sharpe_ratio(positive_series, n_trials=30)
    assert 0.0 <= dsr_pos <= 1.0
    assert dsr_pos > 0.70

    # Flat / losing series
    flat_series = np.array([-1.0, 1.0, -1.0, 1.0, -1.0, 1.0] * 10)
    dsr_flat = calculate_deflated_sharpe_ratio(flat_series, n_trials=30)
    assert dsr_flat < 0.60


def test_split_train_test_data():
    dates = pd.date_range("2026-01-01", periods=100, freq="1h")
    df = pd.DataFrame({
        "time": dates,
        "close": np.linspace(100, 200, 100)
    })
    train_df, test_df = split_train_test_data(df, train_ratio=0.70)

    assert len(train_df) == 70
    assert len(test_df) == 30
    # Strict chronological ordering: test start time > train end time
    assert test_df["time"].iloc[0] > train_df["time"].iloc[-1]


def test_verify_anti_curve_fit_criteria():
    is_metrics = {"net_r": 150.0, "profit_factor": 1.45, "total_trades": 100}
    oos_metrics_good = {"net_r": 45.0, "profit_factor": 1.35, "total_trades": 40}
    mc_metrics_good = {"mc_p_profit": 0.98, "mc_risk_of_ruin_20r": 0.005}

    # Should pass all
    res_good = verify_anti_curve_fit(is_metrics, oos_metrics_good, mc_metrics_good, dsr=0.92)
    assert res_good["passes_all"] is True
    assert res_good["verdict"] == "INSTITUTIONAL_ALPHA"

    # Should fail due to overfitted OOS (losing in holdout)
    oos_metrics_bad = {"net_r": -12.0, "profit_factor": 0.85, "total_trades": 35}
    res_bad = verify_anti_curve_fit(is_metrics, oos_metrics_bad, mc_metrics_good, dsr=0.50)
    assert res_bad["passes_all"] is False
    assert res_bad["verdict"] == "CURVE_FIT_REJECTED"
