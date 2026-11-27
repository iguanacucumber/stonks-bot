"""v11 PEAD research: SEC 8-K item 2.02 event drift, cache-only."""

from .events import (
    attach_timing,
    eligible,
    load_bars,
    load_events,
    reaction_mask,
)
from .portfolio import PortfolioResult, buy_hold, event_returns, simulate

__all__ = [
    "PortfolioResult",
    "attach_timing",
    "buy_hold",
    "eligible",
    "event_returns",
    "load_bars",
    "load_events",
    "reaction_mask",
    "simulate",
]
