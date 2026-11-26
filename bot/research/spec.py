"""A strategy specification is the pre-registration record for one experiment.

It is not a backtest config. It records what was decided *before* the run:
universe, parameters, decision time, fill time and cost model. The walk-forward
runner turns it into `bot.strategy.Config` plus a selector.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class StrategySpec:
    name: str
    family: str
    universe: tuple[str, ...]
    params: dict[str, Any] = field(default_factory=dict)
    decision_time: str = "month-end close"
    fill_time: str = "next open"
    cost_model: str = "fr_cto_ibkr"
    hypothesis: str = ""
    pass_rule: str = ""
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("name is required")
        if not self.universe:
            raise ValueError("universe is required")
        if self.fill_time not in ("next open", "next close", "same close"):
            raise ValueError(f"unsupported fill_time: {self.fill_time}")

    def params_json(self) -> str:
        return json.dumps(self.params, sort_keys=True, default=str)

    def row(self) -> dict[str, Any]:
        return {
            "strategy": self.name,
            "family": self.family,
            "universe": ",".join(self.universe),
            "decision_time": self.decision_time,
            "fill_time": self.fill_time,
            "cost_model": self.cost_model,
            "params_json": self.params_json(),
            "hypothesis": self.hypothesis,
            "pass_rule": self.pass_rule,
        }
