"""Corporate actions for the paper/live state.

The bot state is a USD portfolio. A split adjusts share quantity and average cost
without changing cash; a cash dividend adds net cash and records the gross and
withholding amounts. Both events are persisted in the state and in a per-book
corporate-action CSV, and the EUR view is updated at the event FX rate.

These are accounting events, not trading signals. They are applied only through
the explicit `scripts/live.py corporate` command (or the functions below); the
broker adapter never fabricates them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import Portfolio


@dataclass(frozen=True)
class CorporateAction:
    event_id: str
    kind: str  # "split" | "dividend"
    ticker: str
    session: str
    ratio: float = 1.0
    amount_per_share: float = 0.0
    withholding_rate: float = 0.0
    fx_rate: float | None = None
    note: str = ""

    def __post_init__(self) -> None:
        if self.kind not in ("split", "dividend"):
            raise ValueError("kind must be 'split' or 'dividend'")
        if not self.event_id or not self.ticker or not self.session:
            raise ValueError("event_id, ticker and session are required")
        if self.kind == "split" and self.ratio <= 0:
            raise ValueError("split ratio must be > 0")
        if self.kind == "dividend" and self.amount_per_share < 0:
            raise ValueError("dividend amount_per_share must be >= 0")
        if not 0.0 <= self.withholding_rate < 1.0:
            raise ValueError("withholding_rate must be in [0, 1)")
        if self.fx_rate is not None and self.fx_rate <= 0:
            raise ValueError("fx_rate must be > 0")

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "kind": self.kind,
            "ticker": self.ticker.upper(),
            "session": self.session,
            "ratio": float(self.ratio),
            "amount_per_share": float(self.amount_per_share),
            "withholding_rate": float(self.withholding_rate),
            "fx_rate": None if self.fx_rate is None else float(self.fx_rate),
            "note": self.note,
        }


def apply_split(portfolio: Portfolio, action: CorporateAction) -> dict[str, Any]:
    """Apply a split to a held position; returns the persistent event row."""
    if action.kind != "split":
        raise ValueError("not a split")
    pos = portfolio.positions.get(action.ticker.upper())
    if pos is None or abs(pos.qty) <= 1e-12:
        raise ValueError(f"cannot split {action.ticker}: no open position")
    qty_before = float(pos.qty)
    avg_before = float(pos.avg_cost)
    last_before = pos.last_price
    pos.qty = qty_before * float(action.ratio)
    pos.avg_cost = avg_before / float(action.ratio)
    if last_before is not None:
        pos.last_price = float(last_before) / float(action.ratio)
    event = action.to_dict()
    event.update(
        {
            "qty_before": qty_before,
            "qty_after": float(pos.qty),
            "avg_cost_before": avg_before,
            "avg_cost_after": float(pos.avg_cost),
            "cash_delta": 0.0,
            "gross_cash": 0.0,
            "withholding": 0.0,
            "net_cash": 0.0,
            "cash_eur_delta": 0.0,
        }
    )
    return event


def apply_dividend(portfolio: Portfolio, action: CorporateAction) -> dict[str, Any]:
    """Add the net cash dividend; returns the persistent event row."""
    if action.kind != "dividend":
        raise ValueError("not a dividend")
    pos = portfolio.positions.get(action.ticker.upper())
    if pos is None or abs(pos.qty) <= 1e-12:
        raise ValueError(f"cannot pay dividend {action.ticker}: no open position")
    qty = float(pos.qty)
    gross = qty * float(action.amount_per_share)
    withholding = gross * float(action.withholding_rate)
    net = gross - withholding
    cash_before = float(portfolio.cash)
    portfolio.cash += net
    event = action.to_dict()
    event.update(
        {
            "qty_before": qty,
            "qty_after": qty,
            "cash_before": cash_before,
            "cash_after": float(portfolio.cash),
            "gross_cash": gross,
            "withholding": withholding,
            "net_cash": net,
            "cash_eur_delta": net * float(action.fx_rate or 0.0),
        }
    )
    return event


def apply_event(portfolio: Portfolio, event: dict[str, Any]) -> None:
    """Apply a persisted event dict to a portfolio (used by reconciliation)."""
    if event.get("kind") == "split":
        pos = portfolio.positions.get(str(event["ticker"]).upper())
        if pos is None:
            raise ValueError(f"split {event['event_id']}: no position")
        pos.qty = float(event["qty_after"])
        pos.avg_cost = float(event["avg_cost_after"])
        if pos.last_price is not None:
            pos.last_price = (
                float(pos.last_price)
                * float(event["qty_before"])
                / float(event["qty_after"])
                if abs(float(event["qty_after"])) > 1e-12
                else pos.last_price
            )
    elif event.get("kind") == "dividend":
        portfolio.cash += float(event["net_cash"])
    else:
        raise ValueError(f"unknown corporate action {event.get('kind')!r}")


def apply_to_state(state: Any, action: CorporateAction) -> dict[str, Any]:
    """Apply an action to a LiveState-like object and its EUR fields."""
    if action.kind == "split":
        event = apply_split(state.portfolio, action)
    else:
        event = apply_dividend(state.portfolio, action)
        state.dividend_income += float(event["gross_cash"])
        state.dividend_withholding += float(event["withholding"])
        if action.fx_rate is not None:
            state.dividend_income_eur += float(event["net_cash"]) * action.fx_rate
            state.dividend_withholding_eur += (
                float(event["withholding"]) * action.fx_rate
            )
            state.last_fx_rate = float(action.fx_rate)
            state.last_fx_date = action.session
            state.last_cash_eur = float(state.portfolio.cash) * float(action.fx_rate)
            state.last_equity_eur = float(state.portfolio.equity({})) * float(
                action.fx_rate
            )
            state.peak_equity = max(
                float(state.peak_equity), float(state.portfolio.equity({}))
            )
    state.corporate_actions.append(event)
    return event
