"""Cash, position and account accounting for the simulated exchange.

The ledger is deliberately small and deterministic: it consumes contract
``Fill`` objects and produces contract ``AccountSnapshot``/``PositionSnapshot``
objects.  It never touches the network, a database or the wall clock.

Accounting conventions
----------------------
* ``cash`` moves by the signed trade notional minus the commission.
* ``realized_pnl`` is *gross* trading P&L: commissions are not subtracted from
  it.  They are reported separately in ``fees_paid`` and already deducted from
  cash.  Dividends are credited to ``realized_pnl`` because the contract has no
  separate income field.
* ``avg_cost`` is the entry price, excluding commissions.  A split scales qty
  and divides avg cost; it does not change equity.
* ``equity = cash + sum(qty * last)``.
* ``gross_exposure = sum(abs(qty * last))`` and ``net_exposure`` keeps the sign.
* Buying power treats ``RiskLimits.max_gross`` as the maximum gross exposure as
  a multiple of equity (3.0 => 3x).  ``margin_used = gross / leverage``.
* The invariant checked by :meth:`Ledger.identity_gap` is::

      equity - initial_cash == realized_pnl + unrealized_pnl - fees_paid

  Dividends are included in ``realized_pnl`` so the identity also holds after a
  corporate action.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Mapping

from .contract import (
    AccountSnapshot,
    Fill,
    PositionSnapshot,
    RiskLimits,
)

TOL = 1e-9


@dataclass
class Position:
    """Signed position bookkeeping.  Negative qty is a short."""

    symbol: str
    qty: float = 0.0
    avg_cost: float = 0.0
    last: float = 0.0
    realized_pnl: float = 0.0
    currency: str = "USD"

    @property
    def market_value(self) -> float:
        return self.qty * self.last

    @property
    def unrealized_pnl(self) -> float:
        return self.qty * (self.last - self.avg_cost)

    def snapshot(self) -> PositionSnapshot:
        return PositionSnapshot(
            symbol=self.symbol,
            qty=self.qty,
            avg_cost=self.avg_cost,
            last=self.last,
            market_value=self.market_value,
            unrealized_pnl=self.unrealized_pnl,
            realized_pnl=self.realized_pnl,
            currency=self.currency,
        )


class Ledger:
    """Cash + margin + position accounting for one simulated account."""

    def __init__(
        self,
        starting_cash: float,
        *,
        currency: str = "USD",
        limits: RiskLimits | None = None,
    ) -> None:
        cash = float(starting_cash)
        if not math.isfinite(cash) or cash <= 0:
            raise ValueError("starting_cash must be positive and finite")
        self.limits = limits if limits is not None else RiskLimits()
        if not math.isfinite(float(self.limits.max_gross)) or (
            float(self.limits.max_gross) <= 0
        ):
            raise ValueError("max_gross must be positive and finite")
        self.currency = str(currency)
        self.initial_cash = cash
        self.cash = cash
        self.realized_pnl = 0.0
        self.fees_paid = 0.0
        self._positions: dict[str, Position] = {}

    # ------------------------------------------------------------------ marks
    def mark(self, prices: Mapping[str, float]) -> None:
        """Set the mark price for existing positions."""
        for symbol, raw in prices.items():
            price = float(raw)
            if not math.isfinite(price) or price <= 0:
                raise ValueError(f"mark for {symbol!r} must be positive and finite")
            pos = self._positions.get(symbol)
            if pos is not None and abs(pos.qty) > TOL:
                pos.last = price

    def position(self, symbol: str) -> Position | None:
        return self._positions.get(symbol)

    def position_qty(self, symbol: str) -> float:
        pos = self._positions.get(symbol)
        return 0.0 if pos is None else pos.qty

    # ------------------------------------------------------------------ fills
    def apply_fill(self, fill: Fill) -> float:
        """Apply one contract fill; return the realized P&L delta (gross)."""
        return self.apply_trade(
            symbol=fill.symbol,
            side=fill.side,
            qty=fill.qty,
            price=fill.price,
            commission=fill.commission,
        )

    def apply_trade(
        self,
        *,
        symbol: str,
        side: str,
        qty: float,
        price: float,
        commission: float = 0.0,
    ) -> float:
        """Apply a signed trade, update cash and return realized P&L delta."""
        qty = float(qty)
        price = float(price)
        commission = float(commission)
        if not symbol:
            raise ValueError("symbol is required")
        if side not in ("BUY", "SELL"):
            raise ValueError(f"invalid side {side!r}")
        if not math.isfinite(qty) or qty <= 0:
            raise ValueError("qty must be positive and finite")
        if not math.isfinite(price) or price <= 0:
            raise ValueError("price must be positive and finite")
        if not math.isfinite(commission) or commission < 0:
            raise ValueError("commission must be non-negative and finite")

        signed = qty if side == "BUY" else -qty
        pos = self._positions.get(symbol)
        if pos is None:
            pos = Position(symbol=symbol, currency=self.currency)
            self._positions[symbol] = pos

        old_qty = pos.qty
        new_qty = old_qty + signed
        realized_delta = 0.0

        if abs(old_qty) <= TOL:
            new_avg = price
        elif old_qty * signed > 0:
            # Adding in the same direction: quantity-weighted average cost.
            new_avg = (abs(old_qty) * pos.avg_cost + abs(signed) * price) / abs(new_qty)
        else:
            # Reducing, closing or crossing through zero.
            closed = min(abs(old_qty), abs(signed))
            if old_qty > 0:
                realized_delta = closed * (price - pos.avg_cost)
            else:
                realized_delta = closed * (pos.avg_cost - price)
            if abs(signed) > abs(old_qty) + TOL:
                new_avg = price
            elif abs(new_qty) <= TOL:
                new_avg = 0.0
            else:
                new_avg = pos.avg_cost

        if abs(new_qty) <= TOL:
            new_qty = 0.0
            new_avg = 0.0
        pos.qty = new_qty
        pos.avg_cost = new_avg
        pos.last = price
        pos.realized_pnl += realized_delta

        # Signed notional: BUY pays, SELL receives.  Commission always pays.
        self.cash -= signed * price
        self.cash -= commission
        self.fees_paid += commission
        self.realized_pnl += realized_delta
        return realized_delta

    # -------------------------------------------------------- account metrics
    def market_value(self) -> float:
        return sum(p.market_value for p in self._positions.values())

    def gross_exposure(self) -> float:
        return sum(abs(p.market_value) for p in self._positions.values())

    def net_exposure(self) -> float:
        return sum(p.market_value for p in self._positions.values())

    def unrealized_pnl(self) -> float:
        return sum(p.unrealized_pnl for p in self._positions.values())

    def equity(self) -> float:
        return self.cash + self.market_value()

    def leverage(self, limits: RiskLimits | None = None) -> float:
        cfg = self.limits if limits is None else limits
        return max(1.0, float(cfg.max_gross))

    def margin_used(self, limits: RiskLimits | None = None) -> float:
        return self.gross_exposure() / self.leverage(limits)

    def buying_power(self, limits: RiskLimits | None = None) -> float:
        """Gross capacity left: ``leverage * equity - gross``, floored at zero."""
        cfg = self.limits if limits is None else limits
        equity = self.equity()
        if equity <= 0:
            return 0.0
        capacity = self.leverage(cfg) * equity
        return max(0.0, capacity - self.gross_exposure())

    def identity_gap(self) -> float:
        """``equity - initial`` minus ``realized + unrealized - fees``."""
        return (
            self.equity()
            - self.initial_cash
            - (self.realized_pnl + self.unrealized_pnl() - self.fees_paid)
        )

    # ---------------------------------------------------------------- checks
    def check_order(
        self,
        *,
        symbol: str,
        side: str,
        qty: float,
        price: float,
        commission: float = 0.0,
        limits: RiskLimits | None = None,
    ) -> str | None:
        """Return a rejection reason, or ``None`` if the order fits the account.

        The shared ``validate_request`` rejects *every* sell when shorting is
        disabled, which would also block closing a long.  The exchange applies
        that helper for the other checks and calls this method for the precise
        rule: a sell may not exceed the quantity currently held.
        """
        cfg = self.limits if limits is None else limits
        qty = float(qty)
        price = float(price)
        commission = float(commission)
        if not math.isfinite(qty) or qty <= 0:
            return "qty must be positive and finite"
        if not math.isfinite(price) or price <= 0:
            return "price must be positive and finite"
        if not math.isfinite(commission) or commission < 0:
            return "commission must be non-negative and finite"
        if side not in ("BUY", "SELL"):
            return f"invalid side {side!r}"

        current_qty = self.position_qty(symbol)
        signed = qty if side == "BUY" else -qty
        new_qty = current_qty + signed

        if side == "SELL" and qty > max(0.0, current_qty) + TOL:
            if not cfg.allow_short:
                return "short selling is disabled"

        # A per-symbol concentration limit, expressed as a fraction of equity.
        if (
            cfg.max_position_pct > 0
            and abs(new_qty) > abs(current_qty) + TOL
            and abs(new_qty) * price
            > cfg.max_position_pct * max(0.0, self.equity()) + TOL
        ):
            return "position limit exceeded"

        # Only the increase in gross exposure needs new buying power.
        before = abs(current_qty * price)
        after = abs(new_qty * price)
        incremental = max(0.0, after - before)
        required = incremental + commission
        if required > self.buying_power(cfg) + TOL:
            return "insufficient buying power"
        return None

    # ------------------------------------------------------------- snapshots
    def positions(self) -> list[PositionSnapshot]:
        return [
            self._positions[symbol].snapshot()
            for symbol in sorted(self._positions)
            if abs(self._positions[symbol].qty) > TOL
        ]

    def snapshot(self, ts: datetime) -> AccountSnapshot:
        equity = self.equity()
        gross = self.gross_exposure()
        leverage = self.leverage()
        return AccountSnapshot(
            ts=ts,
            currency=self.currency,
            cash=self.cash,
            buying_power=max(0.0, leverage * max(0.0, equity) - gross),
            equity=equity,
            gross_exposure=gross,
            net_exposure=self.net_exposure(),
            margin_used=gross / leverage,
            realized_pnl=self.realized_pnl,
            unrealized_pnl=self.unrealized_pnl(),
            fees_paid=self.fees_paid,
            positions=self.positions(),
        )

    # ----------------------------------------------------- corporate actions
    def apply_split(self, symbol: str, ratio: float) -> None:
        """Apply a split where ``ratio`` new shares replace one old share."""
        ratio = float(ratio)
        if not math.isfinite(ratio) or ratio <= 0:
            raise ValueError("split ratio must be positive and finite")
        pos = self._positions.get(symbol)
        if pos is None or abs(pos.qty) <= TOL:
            return
        pos.qty *= ratio
        pos.avg_cost /= ratio
        if pos.last > 0:
            pos.last /= ratio

    def apply_dividend(self, symbol: str, per_share: float) -> float:
        """Credit a cash dividend; shorts pay it.  Returns the cash movement."""
        per_share = float(per_share)
        if not math.isfinite(per_share):
            raise ValueError("dividend must be finite")
        pos = self._positions.get(symbol)
        if pos is None or abs(pos.qty) <= TOL:
            return 0.0
        amount = pos.qty * per_share
        self.cash += amount
        self.realized_pnl += amount
        pos.realized_pnl += amount
        return amount


__all__ = ["Ledger", "Position"]
