"""Live autopilot: safe, idempotent daily production loop for the research book.

Paper only by default. The package reuses the signal and accounting definitions in
``bot.strategy`` / ``bot.backtest`` and adds execution state, order intents, safety
rails and an auditable ledger. There is deliberately no real broker adapter here.
"""

from .config import BEST_EVIDENCE_MARKET, LiveConfig, book_overrides
from .engine import LiveEngine
from .models import Fill, OrderIntent, Plan, Portfolio, Position
from .state import StateStore

__all__ = [
    "BEST_EVIDENCE_MARKET",
    "LiveConfig",
    "book_overrides",
    "LiveEngine",
    "Fill",
    "OrderIntent",
    "Plan",
    "Portfolio",
    "Position",
    "StateStore",
]
