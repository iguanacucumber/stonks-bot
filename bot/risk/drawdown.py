"""Drawdown measurement and pre-registered circuit-breaker state machines.

The engine implements a single-step breaker: at a drawdown of `stop` from the
running equity peak, cut gross to `scale` until the drawdown recovers to
`recover`. The functions here are the pure reference used by the tests and by
the report; the run itself uses the existing engine hook, not a forked loop.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


def drawdown(equity: pd.Series) -> pd.Series:
    """Drawdown from the running peak, as a negative fraction (first value 0)."""
    if equity.empty:
        return equity.astype(float)
    peak = equity.cummax()
    return (equity / peak - 1.0).astype(float)


@dataclass(frozen=True)
class DrawdownRule:
    """Single-step breaker, matching `strategy.Config.dd_*` semantics."""

    stop: float
    scale: float = 0.60
    recover: float = 0.15

    def __post_init__(self) -> None:
        if not 0.0 < self.stop < 1.0:
            raise ValueError("stop must be in (0, 1)")
        if not 0.0 < self.scale <= 1.0:
            raise ValueError("scale must be in (0, 1]")
        if not 0.0 <= self.recover < self.stop:
            raise ValueError("recover must be in [0, stop)")


def breaker_active(dd: float, active: bool, rule: DrawdownRule) -> bool:
    """Advance the breaker state by one observation.

    Enter at `dd <= -stop`; leave only at `dd >= -recover`. A NaN drawdown
    leaves the state unchanged.
    """
    if not np.isfinite(dd):
        return bool(active)
    if not active and dd <= -rule.stop:
        return True
    if active and dd >= -rule.recover:
        return False
    return bool(active)


def ladder_scale(
    dd: float,
    rungs: tuple[tuple[float, float], ...],
    default: float = 1.0,
) -> float:
    """Scale from a multi-rung drawdown ladder (diagnostic/reference only).

    `rungs` is a sequence of `(trigger, scale)` pairs, e.g.
    `((0.15, 0.75), (0.30, 0.50), (0.45, 0.25))`. The deepest breached trigger
    wins. This is not wired into the engine: `results/risk_v2.md` already showed
    the single-step trigger is path-specific, and adding rungs would multiply
    the number of fitted thresholds.
    """
    if not np.isfinite(dd):
        return float(default)
    chosen = float(default)
    for trigger, scale in sorted(rungs, key=lambda x: x[0]):
        if dd <= -float(trigger):
            chosen = float(scale)
    return chosen


def ladder_path(
    equity: pd.Series,
    rungs: tuple[tuple[float, float], ...],
    default: float = 1.0,
) -> pd.Series:
    """Point-in-time ladder scale for every date (no state, no recovery band)."""
    dd = drawdown(equity)
    return dd.map(lambda x: ladder_scale(float(x), rungs, default)).astype(float)
