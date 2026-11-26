"""Dividend-tax drag for adjusted-close backtests.

Adjusted closes reinvest gross dividends. A French CTO pays PFU on the dividend
when it is distributed, so a backtest that only taxes realized price gains
overstates after-tax wealth. This module applies an explicit, transparent drag:

    daily_drag_t = tax_rate / 252 * sum_i(weight_i,t * dividend_yield_i)

The weights are end-of-day portfolio weights (including leverage, excluding
cash). Yields are annualized fractions and are deliberately round numbers, not
point-in-time dividend histories; the goal is to stop bond/cash sleeves from
looking better than they are, not to model withholding to the basis point.

The drag is applied to the EUR-ledger equity path. It is an approximation: the
exit tax is scaled with the path rather than recomputed lot by lot.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# Annual distribution yields, fractions. Bond/cash ETFs are the important ones;
# GLD distributes nothing. Equity defaults are conservative mid-points.
DIVIDEND_YIELDS: dict[str, float] = {
    "SMH": 0.010,
    "SOXX": 0.010,
    "SPY": 0.013,
    "QQQ": 0.006,
    "VOO": 0.013,
    "TLT": 0.038,
    "IEF": 0.028,
    "BIL": 0.045,
    "GLD": 0.000,
}
DEFAULT_EQUITY_YIELD = 0.012


def yield_for(ticker: str, default: float = DEFAULT_EQUITY_YIELD) -> float:
    """Annual dividend yield for a ticker, with an equity default."""
    return float(DIVIDEND_YIELDS.get(ticker, default))


def daily_dividend_tax(
    weights: pd.DataFrame,
    tax_rate: float = 0.314,
    yields: dict[str, float] | None = None,
    default_yield: float = DEFAULT_EQUITY_YIELD,
    days: int = 252,
) -> pd.Series:
    """Daily fractional dividend-tax drag from end-of-day weights.

    `weights` must be a date x ticker frame of portfolio weights (cash may be
    present and is ignored). Negative weights are not expected in this book; if
    present they reduce the drag, which is the correct sign for a short payer.
    """
    if not 0.0 <= tax_rate < 1.0:
        raise ValueError("tax_rate must be in [0, 1)")
    if days < 1:
        raise ValueError("days must be >= 1")
    if weights.empty:
        return pd.Series(dtype=float)
    table = dict(DIVIDEND_YIELDS if yields is None else yields)
    cols = [c for c in weights.columns if c != "CASH"]
    if not cols:
        return pd.Series(0.0, index=weights.index)
    y = pd.Series([table.get(c, default_yield) for c in cols], index=cols, dtype=float)
    weighted = weights[cols].astype(float).mul(y, axis=1).sum(axis=1)
    return (weighted * tax_rate / days).clip(lower=0.0)


def apply_dividend_tax(
    equity: pd.Series,
    drag: pd.Series,
) -> pd.Series:
    """Compound a daily fractional drag onto an equity path."""
    d = drag.reindex(equity.index).fillna(0.0).clip(lower=0.0, upper=0.5)
    factor = (1.0 - d).cumprod()
    return (equity.astype(float) * factor).rename("equity_net_dividend_tax")


def after_tax_with_dividend_tax(
    after_tax: float,
    equity: pd.Series,
    equity_net: pd.Series,
) -> float:
    """Scale an after-tax terminal amount by the equity-path drag factor.

    This keeps the exit-tax treatment proportional to the path. It is an
    approximation, stated in the report; it is conservative when the book is in
    a high-distribution sleeve.
    """
    if equity.empty or equity.iloc[-1] <= 0:
        return float(after_tax)
    factor = float(equity_net.iloc[-1] / equity.iloc[-1])
    return float(after_tax) * max(factor, 0.0)
