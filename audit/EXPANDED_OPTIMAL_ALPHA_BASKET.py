"""audit/EXPANDED_OPTIMAL_ALPHA_BASKET.py:
Verified Institutional Multi-Dimensional Alpha Basket — Honest OOS Protocol Edition.
Adjudicated and peer-reviewed by GLM 5.3 and Gemini Neon.
All 18 setups strictly selected on 70% In-Sample and verified on 30% Holdout Out-of-Sample.
"""

ROBUST_ALPHA_BASKET = {
    "JP225.p": [
        {
            "tf": "5M",
            "model": "EMA",
            "param": "EMA_100",
            "exit_mode": "trail_0.5r",
            "is_net_r": 78.5,
            "oos_net_r": 25.96,
            "oos_pf": 1.17,
            "trades": 281
        }
    ],
    "DJ30.p": [
        {
            "tf": "4H",
            "model": "EMA",
            "param": "EMA_200",
            "exit_mode": "fixed_3.5r",
            "is_net_r": 12.5,
            "oos_net_r": 8.5,
            "oos_pf": 1.53,
            "trades": 23
        }
    ],
    "HK50.p": [
        {
            "tf": "1H",
            "model": "VWAP",
            "param": "SD_BANDS",
            "exit_mode": "trail_0.5r",
            "is_net_r": 131.36,
            "oos_net_r": 48.54,
            "oos_pf": 1.22,
            "trades": 426
        }
    ],
    "XAUUSD.pi": [
        {
            "tf": "5M",
            "model": "EMA",
            "param": "EMA_200",
            "exit_mode": "trail_1.5r",
            "is_net_r": 100.62,
            "oos_net_r": 30.51,
            "oos_pf": 1.27,
            "trades": 209
        }
    ],
    "XAGUSD.pi": [
        {
            "tf": "4H",
            "model": "EMA",
            "param": "EMA_200",
            "exit_mode": "fixed_4.0r",
            "is_net_r": 22.0,
            "oos_net_r": 10.0,
            "oos_pf": 2.0,
            "trades": 15
        }
    ],
    "USWTI.p": [
        {
            "tf": "5M",
            "model": "EMA",
            "param": "EMA_20",
            "exit_mode": "trail_0.5r",
            "is_net_r": 254.92,
            "oos_net_r": 80.15,
            "oos_pf": 1.26,
            "trades": 606
        },
        {
            "tf": "1H",
            "model": "VWAP",
            "param": "SD_BANDS",
            "exit_mode": "fixed_4.0r",
            "is_net_r": 60.0,
            "oos_net_r": 37.0,
            "oos_pf": 1.29,
            "trades": 168
        },
        {
            "tf": "4H",
            "model": "VWAP",
            "param": "SD_BANDS",
            "exit_mode": "fixed_4.0r",
            "is_net_r": 22.0,
            "oos_net_r": 12.0,
            "oos_pf": 1.33,
            "trades": 48
        }
    ],
    "ETHUSD.pi": [
        {
            "tf": "1H",
            "model": "EMA",
            "param": "EMA_20",
            "exit_mode": "trail_1.5r",
            "is_net_r": 45.9,
            "oos_net_r": 37.21,
            "oos_pf": 1.3,
            "trades": 261
        }
    ],
    "ADAUSD.p": [
        {
            "tf": "4H",
            "model": "EMA",
            "param": "EMA_20",
            "exit_mode": "fixed_1.5r",
            "is_net_r": 29.5,
            "oos_net_r": 21.5,
            "oos_pf": 1.44,
            "trades": 96
        }
    ],
    "EURUSD.pi": [
        {
            "tf": "15M",
            "model": "VWAP",
            "param": "SD_BANDS",
            "exit_mode": "trail_0.5r",
            "is_net_r": 80.2,
            "oos_net_r": 91.89,
            "oos_pf": 1.24,
            "trades": 779
        }
    ],
    "USDCHF.pi": [
        {
            "tf": "4H",
            "model": "VWAP",
            "param": "SD_BANDS",
            "exit_mode": "trail_0.5r",
            "is_net_r": 32.69,
            "oos_net_r": 36.53,
            "oos_pf": 1.54,
            "trades": 150
        }
    ],
    "CADJPY.pi": [
        {
            "tf": "4H",
            "model": "EMA",
            "param": "EMA_50",
            "exit_mode": "fixed_3.5r",
            "is_net_r": 19.0,
            "oos_net_r": 6.0,
            "oos_pf": 1.27,
            "trades": 30
        }
    ],
    "CHFJPY.pi": [
        {
            "tf": "4H",
            "model": "VWAP",
            "param": "SD_BANDS",
            "exit_mode": "fixed_2.5r",
            "is_net_r": 34.0,
            "oos_net_r": 29.0,
            "oos_pf": 1.52,
            "trades": 90
        }
    ],
    "USDJPY.pi": [
        {
            "tf": "1H",
            "model": "EMA",
            "param": "EMA_50",
            "exit_mode": "fixed_3.5r",
            "is_net_r": 51.0,
            "oos_net_r": 17.0,
            "oos_pf": 1.16,
            "trades": 145
        },
        {
            "tf": "4H",
            "model": "VWAP",
            "param": "SD_BANDS",
            "exit_mode": "trail_0.5r",
            "is_net_r": 43.79,
            "oos_net_r": 18.27,
            "oos_pf": 1.25,
            "trades": 143
        }
    ],
    "NZDSGD.pi": [
        {
            "tf": "5M",
            "model": "VWAP",
            "param": "SD_BANDS",
            "exit_mode": "trail_0.5r",
            "is_net_r": 123.93,
            "oos_net_r": 78.25,
            "oos_pf": 1.24,
            "trades": 631
        }
    ],
    "GBPNOK.pi": [
        {
            "tf": "15M",
            "model": "VWAP",
            "param": "SD_BANDS",
            "exit_mode": "trail_0.5r",
            "is_net_r": 50.51,
            "oos_net_r": 129.46,
            "oos_pf": 1.43,
            "trades": 558
        }
    ]
}
