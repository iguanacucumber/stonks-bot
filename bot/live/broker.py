"""Order intents and the paper execution adapter.

``PaperBroker`` fills a day order at the next session's open, with slippage, a
minimum commission and a participation cap on the open-day volume. A cap that binds
leaves a PARTIAL fill; it is never rounded up to a fabricated full fill.

``Broker`` is the only place a future real adapter should need to plug in. The live
engine never talks to a network and never reads credentials.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol

from .models import BUY, FILLED, PARTIAL, UNFILLED, Fill, OrderIntent, Plan, Portfolio

TOL = 1e-9


@dataclass(frozen=True)
class Bar:
    """One session of adjusted OHLCV for one symbol."""

    open: float
    close: float
    volume: float = 0.0

    def valid(self) -> bool:
        return (
            math.isfinite(self.open)
            and math.isfinite(self.close)
            and self.open > 0
            and self.close > 0
            and math.isfinite(self.volume)
            and self.volume >= 0
        )


class BrokerError(RuntimeError):
    """Base class for adapter failures that must fail the live run closed."""


class Broker(Protocol):
    """Minimal execution surface. Read-only except for ``execute``."""

    name: str

    def execute(
        self,
        plan: Plan,
        bars: dict[str, Bar],
        now: str,
        portfolio: Portfolio,
    ) -> list[Fill]:
        """Fill the plan's pending day orders, mutating ``portfolio``."""


class PaperBroker:
    """Deterministic next-open paper fills, no network."""

    name = "paper"

    def __init__(
        self,
        *,
        slippage_bps: float = 5.0,
        commission_bps: float = 5.0,
        min_commission: float = 1.0,
        max_participation: float = 0.02,
        fractional_shares: bool = True,
    ):
        if max_participation <= 0:
            raise ValueError("max_participation must be > 0")
        self.slippage_bps = float(slippage_bps)
        self.commission_bps = float(commission_bps)
        self.min_commission = float(min_commission)
        self.max_participation = float(max_participation)
        self.fractional_shares = bool(fractional_shares)

    def fill_price(self, side: str, open_price: float) -> float:
        slip = self.slippage_bps / 1e4
        return open_price * (1.0 + slip) if side == BUY else open_price * (1.0 - slip)

    def commission(self, notional: float) -> float:
        if notional <= 0:
            return 0.0
        return max(self.min_commission, notional * self.commission_bps / 1e4)

    def _round_qty(self, qty: float) -> float:
        if self.fractional_shares:
            return math.floor(qty * 1e8) / 1e8
        return float(math.floor(qty))

    def execute(
        self,
        plan: Plan,
        bars: dict[str, Bar],
        now: str,
        portfolio: Portfolio,
    ) -> list[Fill]:
        fills: list[Fill] = []
        # Sells first, even if a hand-built plan lists buys first. A partial or
        # unfilled sell can never be hidden by a buy that uses its expected cash.
        orders = sorted(
            plan.orders, key=lambda o: (0 if o.side == "SELL" else 1, o.ticker)
        )
        for order in orders:
            if order.status not in ("PENDING", PARTIAL):
                continue
            bar = bars.get(order.ticker)
            if bar is None or not bar.valid():
                order.status = UNFILLED
                continue
            available = max(0.0, float(order.remaining))
            if available <= TOL:
                order.status = FILLED if order.filled_qty > TOL else UNFILLED
                continue
            if self.max_participation < 1.0:
                cap = max(0.0, bar.volume * self.max_participation)
                if cap <= 0:
                    order.status = UNFILLED
                    continue
                available = min(available, cap)
            qty = self._round_qty(available)
            if qty <= TOL:
                order.status = UNFILLED
                continue
            price = self.fill_price(order.side, bar.open)
            if not math.isfinite(price) or price <= 0:
                order.status = UNFILLED
                continue
            notional = qty * price
            commission = self.commission(notional)
            fill = Fill(
                fill_id=f"{order.order_id}:{plan.fill_session}",
                order_id=order.order_id,
                ticker=order.ticker,
                side=order.side,
                qty=qty,
                price=price,
                commission=commission,
                session=plan.fill_session,
                created_at=now,
            )
            fill.realized_pnl = portfolio.apply_fill(fill)
            order.filled_qty += qty
            order.status = FILLED if order.filled_qty >= order.qty - TOL else PARTIAL
            fills.append(fill)
        return fills


def estimate_cost(orders: list[OrderIntent], cfg) -> float:
    """Commission + slippage estimate at reference prices, in account currency."""
    commission = 0.0
    slippage = 0.0
    for order in orders:
        notional = order.notional
        if notional <= 0:
            continue
        commission += max(cfg.min_commission, notional * cfg.commission_bps / 1e4)
        slippage += notional * cfg.slippage_bps / 1e4
    return float(commission + slippage)
