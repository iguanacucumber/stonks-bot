"""Investor profiles that need a custom selector on top of the shipped book.

`balanced` is the risk workstream's A6: 70% of the primary US book plus 30% SPY,
total target gross 2.05 before the daily 200-day cut. It is a post-hoc, paper-only
risk preference, not a validated default. The selector returns unscaled weights;
`strategy.Config.leverage` scales them to the target gross.
"""

from __future__ import annotations

import pandas as pd

from . import backtest


def _spy_selector(mkt, date, held, banned) -> dict[str, float]:
    return {"SPY": 1.0}


def blend_selector(etf_selector, core_weight: float = 0.70):
    """Blend the shipped selector with an ETF sleeve at a fixed capital weight.

    The 2.5 divisor keeps the sleeve in the same unscaled units as the shipped
    book, so `leverage=2.5` produces the researched 2.05x total gross:
    0.70 * 2.5 + 0.30 = 2.05.
    """
    if not 0.0 < core_weight < 1.0:
        raise ValueError("core_weight must be in (0, 1)")

    def selector(mkt, date, held, banned):
        core = backtest.target_book(mkt, date, held, banned)
        sleeve = etf_selector(mkt, date, held, banned) if etf_selector else {}
        out = {t: w * core_weight for t, w in core.items()}
        for ticker, weight in sleeve.items():
            out[ticker] = out.get(ticker, 0.0) + (1.0 - core_weight) / 2.5 * weight
        return out

    return selector


balanced_selector = blend_selector(_spy_selector, 0.70)

SELECTORS = {
    "balanced": balanced_selector,
}


def selector_for(profile: str):
    """Return a custom selector for a profile, or None for the shipped default."""
    return SELECTORS.get(profile)


def profile_leverage(profile: str) -> float:
    """Research leverage for a profile; the CLI config is scaled separately."""
    return 2.5


def summarize_targets(targets: dict[str, float]) -> pd.Series:
    """Small helper for tests/reports: target weights sorted by size."""
    return pd.Series(targets, dtype=float).sort_values(ascending=False)
