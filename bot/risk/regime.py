"""Causal trend / regime filters used by the v3 overlays.

All functions use the close at the decision date and earlier. The caller fills
at the next open. Missing history is treated as "no signal" (trend off for a
gate, scale 1.0 for an exposure multiplier) rather than silently using a future
observation.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def sma(series: pd.Series, window: int = 200) -> pd.Series:
    """Simple moving average with a full-window requirement."""
    if window < 1:
        raise ValueError("window must be >= 1")
    return series.astype(float).rolling(window, min_periods=window).mean()


def above_trend(series: pd.Series, window: int = 200) -> pd.Series:
    """True when the close is strictly above its `window`-day SMA.

    NaN history is False: a gate with no signal stays closed.
    """
    avg = sma(series, window)
    return (series.astype(float) > avg).fillna(False).astype(bool)


def trend_scale(
    series: pd.Series,
    window: int = 200,
    scale: float = 0.5,
    default: float = 1.0,
) -> pd.Series:
    """Exposure multiplier: `scale` below trend, 1.0 above, default when NaN."""
    if not 0.0 <= scale <= 1.0:
        raise ValueError("scale must be in [0, 1]")
    above = above_trend(series, window)
    avg = sma(series, window)
    out = pd.Series(float(scale), index=series.index, dtype=float)
    out[above] = 1.0
    out[avg.isna()] = float(default)
    return out


def dual_above(first: pd.Series, second: pd.Series, window: int = 200) -> pd.Series:
    """True only when both series are above their own `window`-day SMA."""
    a = above_trend(first, window)
    b = above_trend(second, window)
    return (a & b).astype(bool)


def momentum(series: pd.Series, lookback: int = 252, skip: int = 0) -> pd.Series:
    """Causal total return from `lookback` days ago, optionally skipping recent days."""
    if lookback < 1 or skip < 0 or skip >= lookback:
        raise ValueError("need 1 <= lookback and 0 <= skip < lookback")
    return series.astype(float).shift(skip) / series.astype(float).shift(lookback) - 1.0


def gate_scale(signal: pd.Series, scale: float = 0.5) -> pd.Series:
    """Map a boolean gate to an exposure multiplier (True -> 1.0, False -> scale)."""
    if not 0.0 <= scale <= 1.0:
        raise ValueError("scale must be in [0, 1]")
    return (
        signal.fillna(False)
        .astype(float)
        .where(lambda x: x > 0, scale)
        .clip(lower=scale, upper=1.0)
    )
