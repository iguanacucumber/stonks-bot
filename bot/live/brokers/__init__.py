"""Execution adapters for the live paper loop.

Only paper adapters live here. ``paper`` is the deterministic local simulator;
``ibkr-paper`` talks to the IBKR Client Portal Web API on a paper account.
There is deliberately no real-money adapter.
"""

from __future__ import annotations

from typing import Any

from ..broker import PaperBroker
from .ibkr import IBKRPaperBroker, IBKRPaperConfig

__all__ = [
    "IBKRPaperBroker",
    "IBKRPaperConfig",
    "make_broker",
]


def make_broker(cfg, *, broker: Any = None, env: dict[str, str] | None = None):
    """Build the configured execution broker.

    Tests inject ``broker`` directly. Production constructs lazily so a dry-run
    never needs IBKR credentials or a gateway session.
    """
    if broker is not None:
        return broker
    name = getattr(cfg, "broker", "paper")
    if name == "paper":
        return PaperBroker(
            slippage_bps=cfg.slippage_bps,
            commission_bps=cfg.commission_bps,
            min_commission=cfg.min_commission,
            max_participation=cfg.max_participation,
            fractional_shares=cfg.fractional_shares,
        )
    if name == "ibkr-paper":
        return IBKRPaperBroker.from_env(env=env)
    raise ValueError(f"unknown broker {name!r}; choose paper or ibkr-paper")
