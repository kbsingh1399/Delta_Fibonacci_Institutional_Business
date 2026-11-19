"""audit/EXPANDED_OPTIMAL_ALPHA_BASKET.py:
Verified Institutional Multi-Dimensional Alpha Basket.
All setups passed strict 70/30 Out-of-Sample Holdout and 10,000 Numba Monte Carlo Bootstrap.
"""

ROBUST_ALPHA_BASKET = {
    "GBPNOK.pi": [
        {
            "tf": "15M",
            "model": "VWAP",
            "param": "SD_BANDS",
            "exit_mode": "trail_0.5r",
            "oos_net_r": 129.46,
            "oos_pf": 1.43,
            "mc_p_profit": 0.9965,
            "mc_max_dd_95": 65.61,
            "deflated_sharpe": 0.0
        },
        {
            "tf": "5M",
            "model": "VWAP",
            "param": "SD_BANDS",
            "exit_mode": "trail_1.5r",
            "oos_net_r": 89.89,
            "oos_pf": 1.56,
            "mc_p_profit": 0.9924,
            "mc_max_dd_95": 54.58,
            "deflated_sharpe": 0.951
        }
    ],
    "CADJPY.pi": [
        {
            "tf": "5M",
            "model": "VWAP",
            "param": "SD_BANDS",
            "exit_mode": "trail_0.5r",
            "oos_net_r": 92.67,
            "oos_pf": 1.28,
            "mc_p_profit": 0.9993,
            "mc_max_dd_95": 51.18,
            "deflated_sharpe": 0.0
        }
    ],
    "USWTI.p": [
        {
            "tf": "5M",
            "model": "EMA",
            "param": "EMA_20",
            "exit_mode": "trail_0.5r",
            "oos_net_r": 80.15,
            "oos_pf": 1.26,
            "mc_p_profit": 1.0,
            "mc_max_dd_95": 34.05,
            "deflated_sharpe": 1.0
        },
        {
            "tf": "15M",
            "model": "VWAP",
            "param": "SD_BANDS",
            "exit_mode": "trail_0.5r",
            "oos_net_r": 75.6,
            "oos_pf": 1.18,
            "mc_p_profit": 0.9999,
            "mc_max_dd_95": 50.5,
            "deflated_sharpe": 0.6556
        }
    ],
    "DJ30.p": [
        {
            "tf": "15M",
            "model": "EMA",
            "param": "EMA_20",
            "exit_mode": "trail_0.5r",
            "oos_net_r": 76.5,
            "oos_pf": 1.28,
            "mc_p_profit": 0.9997,
            "mc_max_dd_95": 44.39,
            "deflated_sharpe": 1.0
        },
        {
            "tf": "5M",
            "model": "VWAP",
            "param": "SD_BANDS",
            "exit_mode": "trail_0.5r",
            "oos_net_r": 73.32,
            "oos_pf": 1.21,
            "mc_p_profit": 0.9518,
            "mc_max_dd_95": 73.93,
            "deflated_sharpe": 0.0
        }
    ],
    "XLMUSD.p": [
        {
            "tf": "5M",
            "model": "VWAP",
            "param": "SD_BANDS",
            "exit_mode": "trail_1.5r",
            "oos_net_r": 63.96,
            "oos_pf": 1.23,
            "mc_p_profit": 0.9997,
            "mc_max_dd_95": 53.93,
            "deflated_sharpe": 0.9961
        }
    ],
    "ETHUSD.pi": [
        {
            "tf": "1H",
            "model": "EMA",
            "param": "EMA_20",
            "exit_mode": "trail_0.5r",
            "oos_net_r": 62.93,
            "oos_pf": 1.48,
            "mc_p_profit": 0.96,
            "mc_max_dd_95": 49.06,
            "deflated_sharpe": 0.0
        }
    ],
    "BTCUSD.pi": [
        {
            "tf": "15M",
            "model": "EMA",
            "param": "EMA_50",
            "exit_mode": "trail_0.5r",
            "oos_net_r": 57.63,
            "oos_pf": 1.26,
            "mc_p_profit": 0.9941,
            "mc_max_dd_95": 48.41,
            "deflated_sharpe": 0.0
        },
        {
            "tf": "4H",
            "model": "VWAP",
            "param": "SD_BANDS",
            "exit_mode": "trail_1.0r",
            "oos_net_r": 20.23,
            "oos_pf": 1.15,
            "mc_p_profit": 0.9994,
            "mc_max_dd_95": 33.26,
            "deflated_sharpe": 1.0
        }
    ],
    "EURUSD.pi": [
        {
            "tf": "15M",
            "model": "EMA",
            "param": "EMA_50",
            "exit_mode": "trail_0.5r",
            "oos_net_r": 46.71,
            "oos_pf": 1.22,
            "mc_p_profit": 0.9466,
            "mc_max_dd_95": 60.12,
            "deflated_sharpe": 0.0
        }
    ],
    "SP500.p": [
        {
            "tf": "5M",
            "model": "EMA",
            "param": "EMA_50",
            "exit_mode": "trail_0.5r",
            "oos_net_r": 42.6,
            "oos_pf": 1.21,
            "mc_p_profit": 0.998,
            "mc_max_dd_95": 42.0,
            "deflated_sharpe": 1.0
        },
        {
            "tf": "1H",
            "model": "EMA",
            "param": "EMA_50",
            "exit_mode": "trail_0.5r",
            "oos_net_r": 23.03,
            "oos_pf": 1.2,
            "mc_p_profit": 1.0,
            "mc_max_dd_95": 25.37,
            "deflated_sharpe": 1.0
        }
    ],
    "NAS100.p": [
        {
            "tf": "1H",
            "model": "EMA",
            "param": "EMA_20",
            "exit_mode": "fixed_3.0r",
            "oos_net_r": 38.0,
            "oos_pf": 1.27,
            "mc_p_profit": 0.9684,
            "mc_max_dd_95": 53.0,
            "deflated_sharpe": 0.9226
        }
    ],
    "USDCHF.pi": [
        {
            "tf": "4H",
            "model": "VWAP",
            "param": "SD_BANDS",
            "exit_mode": "trail_0.5r",
            "oos_net_r": 36.53,
            "oos_pf": 1.54,
            "mc_p_profit": 0.995,
            "mc_max_dd_95": 26.13,
            "deflated_sharpe": 1.0
        }
    ],
    "USDCAD.pi": [
        {
            "tf": "4H",
            "model": "EMA",
            "param": "EMA_20",
            "exit_mode": "fixed_2.0r",
            "oos_net_r": 32.0,
            "oos_pf": 1.8,
            "mc_p_profit": 0.9567,
            "mc_max_dd_95": 26.0,
            "deflated_sharpe": 1.0
        },
        {
            "tf": "1H",
            "model": "EMA",
            "param": "EMA_100",
            "exit_mode": "trail_0.5r",
            "oos_net_r": 19.11,
            "oos_pf": 1.29,
            "mc_p_profit": 0.9713,
            "mc_max_dd_95": 34.3,
            "deflated_sharpe": 0.9999
        }
    ],
    "HK50.p": [
        {
            "tf": "1H",
            "model": "EMA",
            "param": "EMA_20",
            "exit_mode": "fixed_4.0r",
            "oos_net_r": 30.0,
            "oos_pf": 1.35,
            "mc_p_profit": 0.9922,
            "mc_max_dd_95": 39.0,
            "deflated_sharpe": 1.0
        }
    ],
    "XAUUSD.pi": [
        {
            "tf": "1H",
            "model": "VWAP",
            "param": "SD_BANDS",
            "exit_mode": "fixed_3.5r",
            "oos_net_r": 29.5,
            "oos_pf": 1.24,
            "mc_p_profit": 0.9981,
            "mc_max_dd_95": 40.0,
            "deflated_sharpe": 1.0
        },
        {
            "tf": "4H",
            "model": "EMA",
            "param": "EMA_50",
            "exit_mode": "trail_1.0r",
            "oos_net_r": 12.55,
            "oos_pf": 1.95,
            "mc_p_profit": 0.9813,
            "mc_max_dd_95": 15.54,
            "deflated_sharpe": 1.0
        }
    ],
    "JP225.p": [
        {
            "tf": "5M",
            "model": "EMA",
            "param": "EMA_100",
            "exit_mode": "trail_0.5r",
            "oos_net_r": 25.96,
            "oos_pf": 1.17,
            "mc_p_profit": 0.9955,
            "mc_max_dd_95": 40.96,
            "deflated_sharpe": 1.0
        }
    ],
    "XRPUSD.pi": [
        {
            "tf": "4H",
            "model": "EMA",
            "param": "EMA_20",
            "exit_mode": "trail_1.0r",
            "oos_net_r": 25.22,
            "oos_pf": 1.25,
            "mc_p_profit": 0.9736,
            "mc_max_dd_95": 39.14,
            "deflated_sharpe": 0.9996
        }
    ],
    "CHFJPY.pi": [
        {
            "tf": "4H",
            "model": "VWAP",
            "param": "SD_BANDS",
            "exit_mode": "trail_1.5r",
            "oos_net_r": 21.95,
            "oos_pf": 1.43,
            "mc_p_profit": 0.9419,
            "mc_max_dd_95": 33.74,
            "deflated_sharpe": 0.9902
        }
    ],
    "GBPUSD.pi": [
        {
            "tf": "4H",
            "model": "EMA",
            "param": "EMA_100",
            "exit_mode": "fixed_3.5r",
            "oos_net_r": 19.5,
            "oos_pf": 1.75,
            "mc_p_profit": 0.9911,
            "mc_max_dd_95": 17.5,
            "deflated_sharpe": 1.0
        }
    ]
}
