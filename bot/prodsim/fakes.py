"""Reference in-process fakes for the production simulation.

These are **not** a broker adapter. They exist so the replay/CLI/scenario layer
is testable before ``v12/broker-sim`` and ``v12/server-core`` merge, and so
``scripts/prod_sim.py --dev`` can run without any external service. Production
runs must inject the real simulated exchange and store; there is no network path
here and no real-money code.
"""

from __future__ import annotations

import copy
import math
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from .contract import (
    AccountSnapshot,
    Fill,
    OrderRequest,
    OrderState,
    PositionSnapshot,
    RiskLimits,
    Tick,
    clamp_qty,
    utc_now,
)

__all__ = ["MemoryStore", "PaperExchange"]


def _utc(value: datetime | None) -> datetime:
    if value is None:
        return utc_now()
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class MemoryStore:
    """Thread-safe in-memory implementation of ``StoreProtocol``."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._events: list[dict[str, Any]] = []
        self._orders: dict[str, OrderState] = {}
        self._snapshots: list[AccountSnapshot] = []
        self._closed = False

    def append(self, kind: str, payload: Mapping[str, Any]) -> int:
        with self._lock:
            if self._closed:
                raise RuntimeError("store is closed")
            seq = len(self._events) + 1
            row = {
                "seq": seq,
                "kind": str(kind),
                "ts": utc_now().isoformat(),
                "payload": copy.deepcopy(dict(payload)),
            }
            self._events.append(row)
            return seq

    def events(self, since: int = 0, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            if limit < 0:
                raise ValueError("limit must be >= 0")
            out = [copy.deepcopy(e) for e in self._events if int(e["seq"]) > int(since)]
            return out[:limit]

    def event_count(self) -> int:
        with self._lock:
            return len(self._events)

    def save_order(self, order: OrderState) -> None:
        with self._lock:
            self._orders[order.order_id] = copy.deepcopy(order)

    def load_order(self, order_id: str) -> OrderState | None:
        with self._lock:
            order = self._orders.get(order_id)
            return None if order is None else copy.deepcopy(order)

    def list_orders(
        self, status: str | None = None, limit: int = 100
    ) -> list[OrderState]:
        with self._lock:
            rows = list(self._orders.values())
            if status:
                wanted = {
                    s.strip().upper() for s in str(status).split(",") if s.strip()
                }
                rows = [o for o in rows if o.status in wanted]
            rows = sorted(rows, key=lambda o: (o.created_at, o.order_id))
            return (
                [copy.deepcopy(o) for o in rows[-max(0, int(limit)) :]] if limit else []
            )

    def save_snapshot(self, account: AccountSnapshot) -> None:
        with self._lock:
            self._snapshots.append(copy.deepcopy(account))
            if len(self._snapshots) > 5000:
                del self._snapshots[:-5000]

    def last_snapshot(self) -> AccountSnapshot | None:
        with self._lock:
            return None if not self._snapshots else copy.deepcopy(self._snapshots[-1])

    def close(self) -> None:
        with self._lock:
            self._closed = True


@dataclass
class _Position:
    qty: float = 0.0
    avg_cost: float = 0.0
    last: float = 0.0
    realized: float = 0.0


@dataclass
class _Reservation:
    order_id: str
    symbol: str
    qty: float
    price: float


class PaperExchange:
    """A small, deterministic simulated broker for tests and ``--dev``.

    Matching is top-of-book only. Fills occur on a tick *after* submission, are
    capped by ``participation_cap * tick.volume``, and pay commission/slippage.
    This class has no network code and no connection to a real venue.
    """

    def __init__(
        self,
        *,
        capital: float = 100_000.0,
        currency: str = "USD",
        symbols: Sequence[str] | None = None,
        limits: RiskLimits | None = None,
        commission_bps: float = 1.0,
        min_commission: float = 0.0,
        slippage_bps: float = 2.0,
        participation_cap: float = 0.10,
        margin_multiplier: float | None = None,
        allow_short: bool = False,
        halted: Sequence[str] = (),
        started_at: datetime | None = None,
    ) -> None:
        if capital <= 0:
            raise ValueError("capital must be positive")
        self.currency = currency
        self.limits = limits or RiskLimits()
        self.commission_bps = max(0.0, float(commission_bps))
        self.min_commission = max(0.0, float(min_commission))
        self.slippage_bps = max(0.0, float(slippage_bps))
        self.participation_cap = max(0.0, float(participation_cap))
        self.margin_multiplier = (
            float(self.limits.max_gross)
            if margin_multiplier is None
            else max(0.0, float(margin_multiplier))
        )
        self.allow_short = bool(allow_short)
        self.halted = {str(s) for s in halted}
        self.symbols = None if symbols is None else {str(s) for s in symbols}
        self._cash = float(capital)
        self._started_at = _utc(started_at)
        self._now = self._started_at
        self._last_tick_ts: datetime | None = None
        self._positions: dict[str, _Position] = {}
        self._orders: dict[str, OrderState] = {}
        self._by_client: dict[str, str] = {}
        self._open: dict[str, str] = {}
        self._fills: list[Fill] = []
        self._order_seq = 0
        self._fill_seq = 0
        self._fees = 0.0
        self._lock = threading.RLock()

    # -- helpers -------------------------------------------------------
    @property
    def cash(self) -> float:
        with self._lock:
            return self._cash

    def _next_order_id(self) -> str:
        self._order_seq += 1
        return f"sim-{self._order_seq:08d}"

    def _next_fill_id(self) -> str:
        self._fill_seq += 1
        return f"fill-{self._fill_seq:08d}"

    def _last(self, symbol: str) -> float | None:
        pos = self._positions.get(symbol)
        if pos is not None and pos.last > 0:
            return pos.last
        # An order without a mark is rejected rather than priced from a stale
        # unrelated symbol.
        return None

    def _equity(self) -> float:
        mv = sum(
            p.qty * (p.last if p.last > 0 else p.avg_cost)
            for p in self._positions.values()
        )
        return self._cash + mv

    def _gross(self) -> float:
        return sum(
            abs(p.qty) * (p.last if p.last > 0 else p.avg_cost)
            for p in self._positions.values()
        )

    def _reserved(self, exclude_order_id: str | None = None) -> float:
        total = 0.0
        for order_id in self._open.values():
            if order_id == exclude_order_id:
                continue
            order = self._orders.get(order_id)
            if order is None or order.side != "BUY" or order.remaining_qty <= 0:
                continue
            price = float(order.limit_price or self._last(order.symbol) or 0.0)
            if price > 0:
                total += order.remaining_qty * price
        return total

    def _buying_power(self, exclude_order_id: str | None = None) -> float:
        equity = max(0.0, self._equity())
        room = max(0.0, equity * self.margin_multiplier - self._gross())
        return max(0.0, room - self._reserved(exclude_order_id))

    def _reserved_sell_qty(self, symbol: str) -> float:
        total = 0.0
        for order_id in self._open.values():
            order = self._orders.get(order_id)
            if order is not None and order.side == "SELL" and order.symbol == symbol:
                total += order.remaining_qty
        return total

    def _commission(self, qty: float, price: float) -> float:
        return max(self.min_commission, qty * price * self.commission_bps / 1e4)

    def _reject(
        self, request: OrderRequest, reason: str, ts: datetime | None = None
    ) -> OrderState:
        order = OrderState(
            order_id=self._next_order_id(),
            client_order_id=request.client_order_id,
            symbol=request.symbol,
            side=request.side,
            qty=request.qty,
            order_type=request.order_type,
            limit_price=request.limit_price,
            tif=request.tif,
            strategy=request.strategy,
            status="REJECTED",
            created_at=request.created_at,
            updated_at=_utc(ts or self._now),
            reject_reason=reason,
        )
        self._orders[order.order_id] = order
        self._by_client[request.client_order_id] = order.order_id
        return copy.deepcopy(order)

    # -- ExchangeProtocol ---------------------------------------------
    def submit(self, request: OrderRequest) -> OrderState:
        with self._lock:
            existing = self._by_client.get(request.client_order_id)
            if existing is not None:
                return copy.deepcopy(self._orders[existing])
            if self.symbols is not None and request.symbol not in self.symbols:
                return self._reject(request, "unknown symbol")
            if request.symbol in self.halted:
                return self._reject(request, "symbol is halted")
            if request.qty <= 0:
                return self._reject(request, "qty must be positive")
            if request.order_type == "LIMIT" and (
                request.limit_price is None or request.limit_price <= 0
            ):
                return self._reject(request, "limit price required")
            if request.order_type not in ("MARKET", "LIMIT"):
                return self._reject(request, "invalid order type")
            price = (
                float(request.limit_price)
                if request.order_type == "LIMIT"
                else self._last(request.symbol)
            )
            if price is None or price <= 0:
                return self._reject(request, "no market data for symbol")
            notional = request.qty * price
            if notional > self.limits.max_order_notional + 1e-9:
                return self._reject(
                    request,
                    f"order notional {notional:.2f} exceeds "
                    f"{self.limits.max_order_notional:.2f}",
                )
            held = self._positions.get(request.symbol)
            held_qty = held.qty if held is not None else 0.0
            if request.side == "SELL" and not self.allow_short:
                if request.qty > held_qty + 1e-9:
                    return self._reject(
                        request, "insufficient position (shorting disabled)"
                    )
            if request.side == "BUY":
                if notional > self._buying_power() + 1e-9:
                    return self._reject(
                        request,
                        f"insufficient buying power: need {notional:.2f}, "
                        f"have {self._buying_power():.2f}",
                    )
            order = OrderState(
                order_id=self._next_order_id(),
                client_order_id=request.client_order_id,
                symbol=request.symbol,
                side=request.side,
                qty=request.qty,
                order_type=request.order_type,
                limit_price=request.limit_price,
                tif=request.tif,
                strategy=request.strategy,
                status="ACKNOWLEDGED",
                created_at=request.created_at,
                updated_at=_utc(self._now),
            )
            self._orders[order.order_id] = order
            self._by_client[request.client_order_id] = order.order_id
            self._open[order.order_id] = order.order_id
            return copy.deepcopy(order)

    def cancel(self, order_id: str) -> OrderState:
        with self._lock:
            order = self._orders.get(order_id)
            if order is None:
                raise KeyError(order_id)
            if order.is_open:
                order.status = "CANCELED"
                order.updated_at = _utc(self._now)
                # filled_qty and fills are preserved deliberately: a cancel
                # racing a partial fill must never erase the filled quantity.
            self._open.pop(order.order_id, None)
            return copy.deepcopy(order)

    def on_tick(self, tick: Tick) -> list[Fill]:
        with self._lock:
            if self.symbols is not None and tick.symbol not in self.symbols:
                return []
            now = _utc(tick.ts)
            # Simulated replay time may be earlier than the wall-clock start;
            # monotonicity is judged against the previous *tick*, not startup.
            if self._last_tick_ts is not None and now <= self._last_tick_ts:
                return []
            self._last_tick_ts = now
            self._now = now
            pos = self._positions.setdefault(tick.symbol, _Position(last=tick.last))
            pos.last = float(tick.last)
            fills: list[Fill] = []
            for order_id in list(self._open.values()):
                order = self._orders.get(order_id)
                if order is None or not order.is_open or order.symbol != tick.symbol:
                    continue
                base_price: float | None
                if order.order_type == "MARKET":
                    base_price = float(tick.ask if order.side == "BUY" else tick.bid)
                elif order.side == "BUY":
                    base_price = (
                        min(float(order.limit_price), tick.ask)
                        if order.limit_price is not None
                        and tick.ask <= order.limit_price
                        else None
                    )
                else:
                    base_price = (
                        max(float(order.limit_price), tick.bid)
                        if order.limit_price is not None
                        and tick.bid >= order.limit_price
                        else None
                    )
                if base_price is None or base_price <= 0:
                    continue
                slip = base_price * self.slippage_bps / 1e4
                exec_price = (
                    base_price + slip
                    if order.side == "BUY"
                    else max(1e-9, base_price - slip)
                )
                capacity = remaining = order.remaining_qty
                if self.participation_cap > 0:
                    capacity = min(
                        capacity, self.participation_cap * float(tick.volume)
                    )
                capacity = clamp_qty(max(0.0, capacity), 1)
                if order.side == "SELL" and not self.allow_short:
                    pos_qty = max(0.0, self._positions.get(order.symbol, _Position()).qty)
                    capacity = min(capacity, pos_qty)
                if order.side == "BUY" and capacity > 0:
                    room_qty = clamp_qty(
                        self._buying_power(exclude_order_id=order.order_id) / exec_price,
                        1,
                    )
                    capacity = min(capacity, room_qty)
                if capacity <= 0:
                    if order.tif == "IOC":
                        order.status = "CANCELED"
                        order.updated_at = now
                        self._open.pop(order_id, None)
                    continue
                qty = min(remaining, capacity)
                commission = self._commission(qty, exec_price)
                if order.side == "BUY":
                    self._cash -= qty * exec_price + commission
                    p = self._positions.setdefault(
                        tick.symbol, _Position(last=tick.last)
                    )
                    new_qty = p.qty + qty
                    p.avg_cost = (
                        (p.avg_cost * p.qty + exec_price * qty) / new_qty
                        if new_qty > 1e-12
                        else 0.0
                    )
                    p.qty = new_qty
                    p.last = tick.last
                else:
                    p = self._positions.setdefault(
                        tick.symbol, _Position(last=tick.last)
                    )
                    closing = min(qty, p.qty)
                    p.realized += (exec_price - p.avg_cost) * closing - commission
                    p.qty -= closing
                    if p.qty <= 1e-12:
                        p.qty = 0.0
                        p.avg_cost = 0.0
                    p.last = tick.last
                    self._cash += qty * exec_price - commission
                self._fees += commission
                prev_filled = order.filled_qty
                order.filled_qty += qty
                order.avg_price = (
                    (order.avg_price * prev_filled + exec_price * qty)
                    / order.filled_qty
                    if order.filled_qty > 1e-12
                    else 0.0
                )
                order.updated_at = now
                order.status = "FILLED" if order.remaining_qty <= 1e-9 else "PARTIAL"
                fill = Fill(
                    fill_id=self._next_fill_id(),
                    order_id=order.order_id,
                    ts=now,
                    symbol=order.symbol,
                    side=order.side,
                    qty=qty,
                    price=exec_price,
                    commission=commission,
                    slippage=abs(exec_price - base_price) * qty,
                    liquidity="TAKER",
                )
                order.fills.append(fill)
                fills.append(fill)
                self._fills.append(fill)
                if not order.is_open:
                    self._open.pop(order_id, None)
                elif order.tif == "IOC":
                    order.status = "CANCELED"
                    self._open.pop(order_id, None)
            return fills

    def mark(self, prices: Mapping[str, float]) -> None:
        with self._lock:
            for symbol, price in prices.items():
                if not math.isfinite(float(price)) or float(price) <= 0:
                    continue
                pos = self._positions.setdefault(str(symbol), _Position())
                pos.last = float(price)

    def account(self) -> AccountSnapshot:
        with self._lock:
            positions = self._position_rows()
            mv = sum(p.market_value for p in positions)
            unrealized = sum(p.unrealized_pnl for p in positions)
            realized = sum(p.realized_pnl for p in positions)
            gross = sum(abs(p.market_value) for p in positions)
            net = sum(p.market_value for p in positions)
            equity = self._cash + mv
            return AccountSnapshot(
                ts=self._now,
                currency=self.currency,
                cash=self._cash,
                buying_power=self._buying_power(),
                equity=equity,
                gross_exposure=gross,
                net_exposure=net,
                margin_used=max(0.0, gross - max(0.0, equity)),
                realized_pnl=realized,
                unrealized_pnl=unrealized,
                fees_paid=self._fees,
                positions=positions,
            )

    def _position_rows(self) -> list[PositionSnapshot]:
        rows = []
        for symbol, p in sorted(self._positions.items()):
            if abs(p.qty) <= 1e-12:
                continue
            last = p.last if p.last > 0 else p.avg_cost
            mv = p.qty * last
            rows.append(
                PositionSnapshot(
                    symbol=symbol,
                    qty=p.qty,
                    avg_cost=p.avg_cost,
                    last=last,
                    market_value=mv,
                    unrealized_pnl=(last - p.avg_cost) * p.qty,
                    realized_pnl=p.realized,
                    currency=self.currency,
                )
            )
        return rows

    def positions(self) -> list[PositionSnapshot]:
        with self._lock:
            return self._position_rows()

    def open_orders(self) -> list[OrderState]:
        with self._lock:
            return [copy.deepcopy(self._orders[i]) for i in self._open.values()]

    def order(self, order_id: str) -> OrderState | None:
        with self._lock:
            order = self._orders.get(order_id)
            return None if order is None else copy.deepcopy(order)

    # -- diagnostics ---------------------------------------------------
    def all_orders(self) -> list[OrderState]:
        with self._lock:
            return [copy.deepcopy(o) for o in self._orders.values()]

    def fills(self) -> list[Fill]:
        with self._lock:
            return list(self._fills)

    def export_state(self) -> dict[str, Any]:
        """Opaque state snapshot for a restart test; not part of the protocol."""
        with self._lock:
            return {
                "cash": self._cash,
                "fees": self._fees,
                "now": self._now.isoformat(),
                "positions": {
                    s: [p.qty, p.avg_cost, p.last, p.realized]
                    for s, p in self._positions.items()
                },
                "orders": [copy.deepcopy(o) for o in self._orders.values()],
                "by_client": dict(self._by_client),
                "open": dict(self._open),
                "order_seq": self._order_seq,
                "fill_seq": self._fill_seq,
                "fills": len(self._fills),
            }
