"""Pure risk and allocation primitives for the v2 risk workstream.

The modules here do not touch the accounting engine. They turn causal inputs
(prices, realized volatility, drawdown, covariance) into target exposures and
hedge sizes; `scripts/risk_v3.py` applies them through the existing
`strategy.Config` / selector / overlay hooks.
"""

from .alloc import inverse_vol_weights, risk_parity_weights
from .drawdown import DrawdownRule, drawdown, ladder_scale
from .regime import above_trend, trend_scale
from .tail import (
    HedgeConfig,
    black_scholes_put,
    put_spread_cost,
    simulate_hedge,
)
from .vol import VolTarget, gross_series, next_gross, realized_vol

__all__ = [
    "DrawdownRule",
    "HedgeConfig",
    "VolTarget",
    "above_trend",
    "black_scholes_put",
    "drawdown",
    "gross_series",
    "inverse_vol_weights",
    "ladder_scale",
    "next_gross",
    "put_spread_cost",
    "realized_vol",
    "risk_parity_weights",
    "simulate_hedge",
    "trend_scale",
]
