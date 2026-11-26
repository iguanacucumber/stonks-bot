"""Alpha Lab research harness: specs, walk-forward, costs, metrics, registry.

The package is deliberately small and reuses `bot.backtest` for accounting.
Nothing in here changes the shipped engine; it only builds panels, selectors
and out-of-sample reports around it.
"""

from .spec import StrategySpec
from .costs import CostModel
from .metrics import summarize_equity, bootstrap_p5, deflated_sharpe
from .walkforward import Fold, expanding_folds, walk_forward

__all__ = [
    "StrategySpec",
    "CostModel",
    "summarize_equity",
    "bootstrap_p5",
    "deflated_sharpe",
    "Fold",
    "expanding_folds",
    "walk_forward",
]
