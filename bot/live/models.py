"""Small JSON-friendly models shared by the live loop.

Everything here is plain data: no data access, no network, no pandas. That keeps the
execution path testable with synthetic bars and makes the state file auditable.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

BUY = "BUY"
SELL = "SELL"
SIDES = (BUY, SELL)

# Order lifecycle. PARTIAL and UNFILLED are terminal for a day order: the next
# monthly plan reconciles the resulting position, it does not silently re-send.
PENDING = "PENDING"
FILLED = "FILLED"
PARTIAL = "PARTIAL"
UNFILLED = "UNFILLED"
CANCELLED = "CANCELLED"

# Plan lifecycle.
PLAN_OK = "OK"
PLAN_BLOCKED = "BLOCKED"
PLAN_SETTLED = "SETTLED"


def _f(x: float | None, digits: int = 10) -> float | None:
    """Round for stable state hashes and readable JSON; keep None."""
    if x is None:
        return None
    if not math.isfinite(float(x)):
        raise ValueError(f"non-finite number in live state: {x!r}")
    return round(float(x), digits)


@dataclass
class Position:
    qty: float = 0.0
    avg_cost: float = 0.0
    last_price: float | None = None
    last_session: str | None = None

    @property
    def active(self) -> bool:
        return abs(self.qty) > 1e-12

    def value(self, mark: float | None = None) -> float:
        px = self.last_price if mark is None else mark
        if px is None:
            return 0.0
        return self.qty * float(px)

    def to_dict(self) -> dict[str, Any]:
        return {
            "qty": _f(self.qty),
            "avg_cost": _f(self.avg_cost),
            "last_price": _f(self.last_price),
            "last_session": self.last_session,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Position":
        return cls(
            qty=float(raw.get("qty", 0.0)),
            avg_cost=float(raw.get("avg_cost", 0.0)),
            last_price=(
                None if raw.get("last_price") is None else float(raw["last_price"])
            ),
            last_session=raw.get("last_session"),
        )


@dataclass
class OrderIntent:
    order_id: str
    ticker: str
    side: str
    qty: float
    ref_price: float
    reason: str
    fill_session: str | None = None
    status: str = PENDING
    filled_qty: float = 0.0

    @property
    def notional(self) -> float:
        return abs(float(self.qty) * float(self.ref_price))

    @property
    def remaining(self) -> float:
        return max(0.0, float(self.qty) - float(self.filled_qty))

    def to_dict(self) -> dict[str, Any]:
        return {
            "order_id": self.order_id,
            "ticker": self.ticker,
            "side": self.side,
            "qty": _f(self.qty),
            "ref_price": _f(self.ref_price),
            "reason": self.reason,
            "fill_session": self.fill_session,
            "status": self.status,
            "filled_qty": _f(self.filled_qty),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "OrderIntent":
        return cls(
            order_id=str(raw["order_id"]),
            ticker=str(raw["ticker"]),
            side=str(raw["side"]),
            qty=float(raw["qty"]),
            ref_price=float(raw["ref_price"]),
            reason=str(raw.get("reason", "rebalance")),
            fill_session=raw.get("fill_session"),
            status=str(raw.get("status", PENDING)),
            filled_qty=float(raw.get("filled_qty", 0.0)),
        )


@dataclass
class Fill:
    fill_id: str
    order_id: str
    ticker: str
    side: str
    qty: float
    price: float
    commission: float
    session: str
    created_at: str
    realized_pnl: float = 0.0
    # Native amounts stay authoritative. These fields preserve the trade-date FX
    # and EUR amounts so the EUR-ledger replay can reconcile fills exactly.
    currency: str = "USD"
    fx_rate: float | None = None
    fx_date: str | None = None
    notional_eur: float | None = None
    commission_eur: float | None = None
    realized_pnl_eur: float | None = None

    @property
    def notional(self) -> float:
        return abs(float(self.qty) * float(self.price))

    def to_dict(self) -> dict[str, Any]:
        return {
            "fill_id": self.fill_id,
            "order_id": self.order_id,
            "ticker": self.ticker,
            "side": self.side,
            "qty": _f(self.qty),
            "price": _f(self.price),
            "commission": _f(self.commission),
            "session": self.session,
            "created_at": self.created_at,
            "realized_pnl": _f(self.realized_pnl),
            "currency": self.currency,
            "fx_rate": _f(self.fx_rate, 8),
            "fx_date": self.fx_date,
            "notional_eur": _f(self.notional_eur),
            "commission_eur": _f(self.commission_eur),
            "realized_pnl_eur": _f(self.realized_pnl_eur),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Fill":
        def opt(key: str) -> float | None:
            value = raw.get(key)
            return None if value is None else float(value)

        return cls(
            fill_id=str(raw["fill_id"]),
            order_id=str(raw["order_id"]),
            ticker=str(raw["ticker"]),
            side=str(raw["side"]),
            qty=float(raw["qty"]),
            price=float(raw["price"]),
            commission=float(raw["commission"]),
            session=str(raw["session"]),
            created_at=str(raw["created_at"]),
            realized_pnl=float(raw.get("realized_pnl", 0.0)),
            currency=str(raw.get("currency", "USD")),
            fx_rate=opt("fx_rate"),
            fx_date=raw.get("fx_date"),
            notional_eur=opt("notional_eur"),
            commission_eur=opt("commission_eur"),
            realized_pnl_eur=opt("realized_pnl_eur"),
        )


@dataclass
class Portfolio:
    """Cash plus long positions. Margin is allowed; financing is charged separately."""

    cash: float
    positions: dict[str, Position] = field(default_factory=dict)

    def copy(self) -> "Portfolio":
        return Portfolio(
            cash=float(self.cash),
            positions={t: Position(**vars(p)) for t, p in self.positions.items()},
        )

    def equity(self, marks: dict[str, float]) -> float:
        value = 0.0
        for ticker, pos in self.positions.items():
            if not pos.active:
                continue
            px = marks.get(ticker)
            value += pos.qty * float(px if px is not None else (pos.last_price or 0.0))
        return float(self.cash) + value

    def gross(self, marks: dict[str, float]) -> float:
        value = 0.0
        for ticker, pos in self.positions.items():
            if not pos.active:
                continue
            px = marks.get(ticker)
            value += abs(pos.qty) * float(
                px if px is not None else (pos.last_price or 0.0)
            )
        return float(value)

    def weights(
        self, marks: dict[str, float], equity: float | None = None
    ) -> dict[str, float]:
        eq = self.equity(marks) if equity is None else float(equity)
        if eq <= 0:
            return {}
        out = {}
        for ticker, pos in self.positions.items():
            if not pos.active:
                continue
            px = marks.get(ticker)
            px = float(px if px is not None else (pos.last_price or 0.0))
            if px > 0:
                out[ticker] = pos.qty * px / eq
        return {t: w for t, w in out.items() if abs(w) > 1e-12}

    def apply_fill(self, fill: Fill) -> float:
        """Apply a fill, returning realized PnL before commission (long-only book)."""
        pos = self.positions.setdefault(fill.ticker, Position())
        qty = float(fill.qty)
        price = float(fill.price)
        realized = 0.0
        if fill.side == BUY:
            new_qty = pos.qty + qty
            if new_qty > 1e-12:
                pos.avg_cost = (pos.qty * pos.avg_cost + qty * price) / new_qty
            pos.qty = new_qty
            self.cash -= qty * price + float(fill.commission)
        elif fill.side == SELL:
            if qty > pos.qty + 1e-9:
                raise ValueError(
                    f"paper sell {fill.ticker} {qty} exceeds position {pos.qty}"
                )
            qty = min(qty, pos.qty)
            realized = (price - pos.avg_cost) * qty
            pos.qty = max(0.0, pos.qty - qty)
            self.cash += qty * price - float(fill.commission)
            if pos.qty <= 1e-12:
                pos.qty = 0.0
                pos.avg_cost = 0.0
        else:
            raise ValueError(f"unknown side {fill.side!r}")
        pos.last_price = price
        pos.last_session = fill.session
        return float(realized)

    def realize(self, fees_paid: float, realized_pnl: float) -> None:
        """No-op documentation hook: accounting lives in apply_fill."""
        _ = (fees_paid, realized_pnl)

    def to_dict(self) -> dict[str, Any]:
        return {
            "cash": _f(self.cash),
            "positions": {t: p.to_dict() for t, p in sorted(self.positions.items())},
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Portfolio":
        return cls(
            cash=float(raw.get("cash", 0.0)),
            positions={
                str(t): Position.from_dict(p)
                for t, p in (raw.get("positions") or {}).items()
            },
        )


@dataclass
class Plan:
    plan_id: str
    book: str
    market: str
    signal_session: str
    fill_session: str
    created_at: str
    target_weights: dict[str, float]
    current_weights: dict[str, float]
    orders: list[OrderIntent]
    status: str = PLAN_OK
    blocked_reason: str | None = None
    expected_cost: float = 0.0
    turnover: float = 0.0
    gross: float = 0.0
    equity: float = 0.0
    cash: float = 0.0
    risk: dict[str, Any] = field(default_factory=dict)
    data: dict[str, Any] = field(default_factory=dict)
    fills: list[Fill] = field(default_factory=list)
    currency: str = "USD"
    fx_rate: float | None = None
    fx_date: str | None = None
    equity_eur: float | None = None
    cash_eur: float | None = None
    gross_eur: float | None = None
    expected_cost_eur: float | None = None

    @property
    def ok(self) -> bool:
        return self.status != PLAN_BLOCKED

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "book": self.book,
            "market": self.market,
            "signal_session": self.signal_session,
            "fill_session": self.fill_session,
            "created_at": self.created_at,
            "target_weights": {
                t: _f(w) for t, w in sorted(self.target_weights.items())
            },
            "current_weights": {
                t: _f(w) for t, w in sorted(self.current_weights.items())
            },
            "orders": [o.to_dict() for o in self.orders],
            "status": self.status,
            "blocked_reason": self.blocked_reason,
            "expected_cost": _f(self.expected_cost),
            "turnover": _f(self.turnover),
            "gross": _f(self.gross),
            "equity": _f(self.equity),
            "cash": _f(self.cash),
            "risk": dict(self.risk),
            "data": dict(self.data),
            "fills": [f.to_dict() for f in self.fills],
            "currency": self.currency,
            "fx_rate": _f(self.fx_rate, 8),
            "fx_date": self.fx_date,
            "equity_eur": _f(self.equity_eur),
            "cash_eur": _f(self.cash_eur),
            "gross_eur": _f(self.gross_eur),
            "expected_cost_eur": _f(self.expected_cost_eur),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Plan":
        def opt(key: str) -> float | None:
            value = raw.get(key)
            return None if value is None else float(value)

        return cls(
            plan_id=str(raw["plan_id"]),
            book=str(raw["book"]),
            market=str(raw["market"]),
            signal_session=str(raw["signal_session"]),
            fill_session=str(raw["fill_session"]),
            created_at=str(raw.get("created_at", "")),
            target_weights={
                str(t): float(w) for t, w in (raw.get("target_weights") or {}).items()
            },
            current_weights={
                str(t): float(w) for t, w in (raw.get("current_weights") or {}).items()
            },
            orders=[OrderIntent.from_dict(x) for x in (raw.get("orders") or [])],
            status=str(raw.get("status", PLAN_OK)),
            blocked_reason=raw.get("blocked_reason"),
            expected_cost=float(raw.get("expected_cost", 0.0)),
            turnover=float(raw.get("turnover", 0.0)),
            gross=float(raw.get("gross", 0.0)),
            equity=float(raw.get("equity", 0.0)),
            cash=float(raw.get("cash", 0.0)),
            risk=dict(raw.get("risk") or {}),
            data=dict(raw.get("data") or {}),
            fills=[Fill.from_dict(x) for x in (raw.get("fills") or [])],
            currency=str(raw.get("currency", "USD")),
            fx_rate=opt("fx_rate"),
            fx_date=raw.get("fx_date"),
            equity_eur=opt("equity_eur"),
            cash_eur=opt("cash_eur"),
            gross_eur=opt("gross_eur"),
            expected_cost_eur=opt("expected_cost_eur"),
        )
