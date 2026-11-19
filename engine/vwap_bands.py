"""engine/vwap_bands.py: High-performance vectorized Session-Anchored VWAP
and Standard Deviation Bands Engine (-2, -1, 0, +1, +2 SD).
"""

from typing import Union
import numpy as np
import pandas as pd


def calculate_session_vwap_bands(
    df: pd.DataFrame,
    anchor_mode: str = "broker",
    min_volume: float = 1.0
) -> pd.DataFrame:
    """Computes session-anchored VWAP and ±1.0, ±2.0 standard deviation bands.

    Parameters:
    - df: DataFrame with columns ['time', 'high', 'low', 'close'] and optional ['tick_volume', 'real_volume'].
    - anchor_mode: 'utc' for 00:00 UTC anchor (Crypto 24/7), 'broker' for 00:00 Broker Server Time (UTC+2 rollover for Forex/Indices).
    - min_volume: minimum volume floor per bar to prevent division-by-zero.

    Returns:
    - df copy with columns ['vwap', 'vwap_p1', 'vwap_p2', 'vwap_m1', 'vwap_m2', 'vwap_std'].
    """
    out = df.copy()
    if not pd.api.types.is_datetime64_any_dtype(out["time"]):
        out["time"] = pd.to_datetime(out["time"], utc=True)

    # Resolve Volume: prefer tick_volume, fallback to real_volume, fallback to 1.0
    if "tick_volume" in out.columns and (out["tick_volume"] > 0).any():
        vol = out["tick_volume"].astype(float).clip(lower=min_volume)
    elif "real_volume" in out.columns and (out["real_volume"] > 0).any():
        vol = out["real_volume"].astype(float).clip(lower=min_volume)
    else:
        vol = pd.Series(1.0, index=out.index)

    # Determine Session ID based on anchor mode
    # Broker rollover is standard at 00:00 broker time (~UTC+2)
    if anchor_mode == "broker":
        session_time = out["time"] + pd.Timedelta(hours=2)
    else:
        session_time = out["time"]

    session_id = session_time.dt.date

    # Typical Price P = (High + Low + Close) / 3
    tp = (out["high"].astype(float) + out["low"].astype(float) + out["close"].astype(float)) / 3.0
    pv = tp * vol
    pv2 = vol * (tp ** 2)

    # Vectorized cumulative sums grouped by session
    cum_vol = vol.groupby(session_id).cumsum()
    cum_pv = pv.groupby(session_id).cumsum()
    cum_pv2 = pv2.groupby(session_id).cumsum()

    # VWAP = sum(P * V) / sum(V)
    vwap = cum_pv / cum_vol

    # Variance = E[P^2] - (E[P])^2 = (sum(V * P^2) / sum(V)) - VWAP^2
    variance = (cum_pv2 / cum_vol) - (vwap ** 2)
    std = np.sqrt(np.maximum(0.0, variance))

    # Bands
    out["vwap"] = vwap
    out["vwap_std"] = std
    out["vwap_p1"] = vwap + 1.0 * std
    out["vwap_p2"] = vwap + 2.0 * std
    out["vwap_m1"] = vwap - 1.0 * std
    out["vwap_m2"] = vwap - 2.0 * std

    return out
