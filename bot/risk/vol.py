"""Causal volatility targeting with no-trade bands and asymmetric hysteresis.

Decision timing: every input is computed from closes up to and including the
decision date `t`. The resulting target is executed at the next open by the
engine. This module never looks at a future bar.

The rule is deliberately boring: realized vol up -> cut gross; realized vol down
-> add gross only after a lower vol has persisted through the hysteresis band.
That asymmetry is what stops a monthly rule from whipsawing around one threshold.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class VolTarget:
    """Parameters for one volatility-targeting rule.

    target: annualized portfolio volatility target (e.g. 0.30).
    max_gross / min_gross: exposure bounds.
    band: relative no-trade band; a move smaller than this is ignored.
    hysteresis: extra vol relief required before adding exposure.
    max_step: maximum absolute gross change per decision.
    annualize: trading days per year used by :func:`realized_vol`.
    """

    target: float
    max_gross: float = 2.5
    min_gross: float = 0.5
    band: float = 0.10
    hysteresis: float = 0.10
    max_step: float = 0.5
    annualize: int = 252

    def __post_init__(self) -> None:
        if not np.isfinite(self.target) or self.target <= 0:
            raise ValueError("target must be a positive finite number")
        if self.max_gross <= 0:
            raise ValueError("max_gross must be positive")
        if self.min_gross < 0 or self.min_gross > self.max_gross:
            raise ValueError("min_gross must be in [0, max_gross]")
        if not 0.0 <= self.band < 1.0:
            raise ValueError("band must be in [0, 1)")
        if not 0.0 <= self.hysteresis < 1.0:
            raise ValueError("hysteresis must be in [0, 1)")
        if self.max_step <= 0:
            raise ValueError("max_step must be positive")
        if self.annualize < 1:
            raise ValueError("annualize must be >= 1")


def realized_vol(
    returns: pd.Series,
    window: int = 20,
    annualize: int = 252,
    min_periods: int | None = None,
) -> pd.Series:
    """Annualized rolling standard deviation of simple returns.

    The value at `t` uses returns through `t` and is therefore usable at the
    close of `t` for a next-open fill. The first `window - 1` values are NaN.
    """
    if window < 2:
        raise ValueError("window must be >= 2")
    if annualize < 1:
        raise ValueError("annualize must be >= 1")
    mp = window if min_periods is None else int(min_periods)
    if mp < 2 or mp > window:
        raise ValueError("min_periods must be in [2, window]")
    return returns.astype(float).rolling(window, min_periods=mp).std(ddof=1) * np.sqrt(
        annualize
    )


def raw_gross(
    sigma: float,
    target: float,
    max_gross: float,
    min_gross: float = 0.0,
) -> float:
    """Un-smoothed target gross for one volatility observation.

    A non-finite sigma returns NaN (the caller should hold the previous gross).
    Zero or negative sigma is treated as unlimited risk capacity and returns
    `max_gross`; negative realized vol is impossible for a variance estimate,
    but the boundary is explicit rather than silently propagating.
    """
    if not np.isfinite(sigma):
        return float("nan")
    if target <= 0 or max_gross <= 0:
        raise ValueError("target and max_gross must be positive")
    if min_gross < 0 or min_gross > max_gross:
        raise ValueError("min_gross must be in [0, max_gross]")
    if sigma <= 0:
        return float(max_gross)
    return float(np.clip(target / sigma, min_gross, max_gross))


def next_gross(
    sigma: float,
    prev_gross: float | None,
    cfg: VolTarget,
) -> float:
    """One step of the banded/hysteretic volatility-target state machine.

    - No previous gross: take the raw target (bounded).
    - De-risk: cut when the raw target is at least `band` below the previous.
    - Re-risk: add only when the raw target is at least `band` above the
      previous **and** sigma is below `target * (1 - hysteresis)`.
    - Every move is capped at `max_step`; the result is clipped to the bounds.
    - NaN sigma holds the previous gross (or `max_gross` at inception).
    """
    if prev_gross is None:
        raw = raw_gross(sigma, cfg.target, cfg.max_gross, cfg.min_gross)
        if not np.isfinite(raw):
            return float(cfg.max_gross)
        return float(np.clip(raw, cfg.min_gross, cfg.max_gross))

    prev = float(np.clip(prev_gross, cfg.min_gross, cfg.max_gross))
    raw = raw_gross(sigma, cfg.target, cfg.max_gross, cfg.min_gross)
    if not np.isfinite(raw):
        return prev

    if raw < prev:
        if raw > prev * (1.0 - cfg.band):
            return prev
        stepped = max(raw, prev - cfg.max_step)
        return float(np.clip(stepped, cfg.min_gross, cfg.max_gross))

    if raw > prev:
        add_ok = raw >= prev * (1.0 + cfg.band)
        vol_ok = sigma <= cfg.target * (1.0 - cfg.hysteresis)
        if not (add_ok and vol_ok):
            return prev
        stepped = min(raw, prev + cfg.max_step)
        return float(np.clip(stepped, cfg.min_gross, cfg.max_gross))

    return prev


def gross_series(
    sigma: pd.Series,
    cfg: VolTarget,
    initial: float | None = None,
) -> pd.Series:
    """Run :func:`next_gross` over a causal sigma series.

    `initial=None` lets the first observation set the starting gross. Pass 2.5
    to keep a book at its old exposure until the first valid signal.
    """
    prev = initial
    out: list[float] = []
    for value in sigma.astype(float):
        prev = next_gross(value, prev, cfg)
        out.append(prev)
    return pd.Series(out, index=sigma.index, name="target_gross", dtype=float)
