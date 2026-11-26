"""Dividend-tax adjustment for adjusted-close backtests.

Yahoo `adjclose` reinvests gross dividends. The French CTO taxes dividends in
the year they are paid. For equity ETFs the drag is small (SMH ~0.3%/yr at
31.4%); for TLT/IEF/BIL it is not (roughly 0.6-1.0%/yr). This module estimates
the per-ETF dividend yield from the `adjclose/close` ratio and subtracts the
tax from the engine's daily equity path.

It is a post-processing overlay, not a change to the signal or the engine.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def daily_dividend_yield(panel: pd.DataFrame) -> pd.DataFrame:
    """Daily gross dividend yield per ticker, inferred from adjclose/close."""
    px = panel.pivot(index="date", columns="ticker", values="adjclose").sort_index()
    raw = panel.pivot(index="date", columns="ticker", values="close").sort_index()
    ratio = (px / raw).replace([np.inf, -np.inf], np.nan)
    # A split changes both series equally; a dividend raises the adjusted ratio.
    y = ratio.pct_change(fill_method=None).clip(lower=0.0, upper=0.25)
    return y.fillna(0.0)


def apply_dividend_tax(
    equity: pd.Series,
    weights: pd.DataFrame,
    yields: pd.DataFrame,
    tax_rate: float = 0.314,
) -> tuple[pd.Series, pd.Series]:
    """Subtract annual dividend tax from a gross-dividend equity path.

    Returns (adjusted equity, daily gross dividend yield of the book).
    """
    eq = equity.dropna()
    w = weights.reindex(index=eq.index).fillna(0.0)
    cols = [c for c in w.columns if c in yields.columns]
    if not cols:
        return eq, pd.Series(0.0, index=eq.index)
    y = yields.reindex(index=eq.index, columns=cols).fillna(0.0)
    daily_yield = (w[cols] * y).sum(axis=1)
    factor = (1.0 - tax_rate * daily_yield).cumprod()
    return eq * factor, daily_yield


def annual_dividend_summary(daily_yield: pd.Series) -> dict[str, float]:
    """Average annual gross yield and tax drag of the book."""
    if daily_yield.empty:
        return {"avg_yield": 0.0, "tax_drag": 0.0}
    annual = daily_yield.groupby(daily_yield.index.year).sum()
    avg = float(annual.mean())
    return {"avg_yield": avg, "tax_drag": avg * 0.314}
