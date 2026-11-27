"""Tests for the v12 simulation OMS and SQLite store.

Run: uv run python tests/test_prodsim_oms.py

Every test uses a temporary directory and a fake exchange.  No network, no
broker, no credentials.
"""

from __future__ import annotations

import copy
import sqlite3
import sys
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Iterator

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot.prodsim.contract import (  # noqa: E402
    AccountSnapshot,
    Fill,
    OrderRequest,
    OrderState,
    PositionSnapshot,
    RiskLimits,
    Tick,
    jsonable,
    utc_now,
)
from bot.prodsim.oms import (  # noqa: E402
    DuplicateOrderError,
    OrderConflict,
    OrderNotFound,
    OrderUnconfirmedError,
    SimulatedOMS,
)
from bot.prodsim.store import (  # noqa: E402
    SCHEMA_VERSION,
    SchemaVersionError,
    SQLiteStore,
    StoreConflictError,
    StoreCorruptionError,
    StoreError,
)

T0 = datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc)


@contextmanager
def raises(exc_type: type[BaseException]) -> Iterator[BaseException]:
    try:
        yield
    except exc_type:
        return
    except Exception as exc:  # noqa: BLE001
        raise AssertionError(
            f"expected {exc_type.__name__}, got {type(exc).__name__}: {exc}"
        ) from exc
    raise AssertionError(f"expected {exc_type.__name__}, nothing was raised")


def tick(
    symbol: str = "AAA",
    *,
    bid: float = 99.99,
    ask: float = 100.0,
    last: float = 100.0,
    volume: float = 300.0,
    second: int = 0,
) -> Tick:
    return Tick(
        ts=T0 + timedelta(seconds=second),
        symbol=symbol,
        bid=bid,
        ask=ask,
        last=last,
        volume=volume,
    )


class FakeExchange:
    """Small deterministic exchange implementing ExchangeProtocol.

    Fills only on a tick.  Market orders cross immediately; limit orders cross
    only when the tick is through the limit.  Participation is capped so tests
    can force partial fills.
    """

    def __init__(
        self,
        *,
        symbols: tuple[str, ...] = ("AAA", "BBB"),
        participation_cap: float = 0.10,
        commission_bps: float = 1.0,
        min_commission: float = 0.0,
        cash: float = 100_000.0,
    ) -> None:
        self.symbols = set(symbols)
        self.halted: set[str] = set()
        self.participation_cap = participation_cap
        self.commission_bps = commission_bps
        self.min_commission = min_commission
        self.cash = cash
        self.orders: dict[str, OrderState] = {}
        self.by_client: dict[str, str] = {}
        self.submits = 0
        self.cancels = 0
        self.fail_before_accept: set[str] = set()
        self.fail_after_accept: set[str] = set()
        self.fill_count = 0
        self.all_fills: list[Fill] = []
        self.positions_qty: dict[str, float] = {}
        self.avg_cost: dict[str, float] = {}
        self.last: dict[str, float] = {}
        self.fees = 0.0

    # -- ExchangeProtocol -------------------------------------------------

    def submit(self, request: OrderRequest) -> OrderState:
        self.submits += 1
        client_id = request.client_order_id

        if client_id in self.fail_before_accept:
            self.fail_before_accept.remove(client_id)
            raise TimeoutError("simulated timeout before accept")

        if client_id in self.by_client:
            return copy.deepcopy(self.orders[self.by_client[client_id]])

        if request.symbol not in self.symbols:
            return self._reject(request, "unknown symbol")
        if request.symbol in self.halted:
            return self._reject(request, "symbol halted")
        if (
            request.side == "SELL"
            and request.qty > self.positions_qty.get(request.symbol, 0.0) + 1e-9
        ):
            return self._reject(request, "insufficient position")

        order_id = self._new_order_id()
        state = OrderState(
            order_id=order_id,
            client_order_id=client_id,
            symbol=request.symbol,
            side=request.side,
            qty=request.qty,
            order_type=request.order_type,
            limit_price=request.limit_price,
            tif=request.tif,
            strategy=request.strategy,
            status="ACKNOWLEDGED",
            created_at=request.created_at,
            updated_at=utc_now(),
        )
        self.orders[order_id] = state
        self.by_client[client_id] = order_id

        if client_id in self.fail_after_accept:
            self.fail_after_accept.remove(client_id)
            raise TimeoutError("simulated timeout after accept")
        return copy.deepcopy(state)

    def cancel(self, order_id: str) -> OrderState:
        self.cancels += 1
        order = self.orders.get(order_id)
        if order is None:
            raise KeyError(order_id)
        if order.is_open:
            order.status = "CANCELED"
            order.updated_at = utc_now()
        return copy.deepcopy(order)

    def on_tick(self, tick: Tick) -> list[Fill]:
        self.last[tick.symbol] = tick.last
        produced: list[Fill] = []
        for order in list(self.orders.values()):
            if not order.is_open or order.symbol != tick.symbol:
                continue
            if order.order_type == "LIMIT":
                assert order.limit_price is not None
                if order.side == "BUY" and tick.ask > order.limit_price:
                    continue
                if order.side == "SELL" and tick.bid < order.limit_price:
                    continue
            capacity = self.participation_cap * tick.volume
            qty = min(order.remaining_qty, capacity)
            if qty <= 0:
                continue
            price = tick.ask if order.side == "BUY" else tick.bid
            self.fill_count += 1
            fill = Fill(
                fill_id=f"fill-{self.fill_count:06d}",
                order_id=order.order_id,
                ts=tick.ts,
                symbol=order.symbol,
                side=order.side,
                qty=qty,
                price=price,
                commission=max(
                    self.min_commission,
                    qty * price * self.commission_bps / 10_000.0,
                ),
            )
            order.fills.append(fill)
            old_notional = order.avg_price * order.filled_qty
            order.filled_qty += qty
            order.avg_price = (old_notional + qty * price) / order.filled_qty
            order.updated_at = tick.ts
            order.status = "FILLED" if order.remaining_qty <= 1e-9 else "PARTIAL"
            self._apply_position(fill)
            self.all_fills.append(fill)
            produced.append(fill)
        return produced

    def mark(self, prices: dict[str, float]) -> None:
        for symbol, price in prices.items():
            if price > 0:
                self.last[symbol] = float(price)

    def account(self) -> AccountSnapshot:
        positions = self.positions()
        market_value = sum(p.market_value for p in positions)
        unrealized = sum(p.unrealized_pnl for p in positions)
        return AccountSnapshot(
            ts=utc_now(),
            currency="USD",
            cash=self.cash,
            buying_power=max(0.0, self.cash),
            equity=self.cash + market_value,
            gross_exposure=sum(abs(p.market_value) for p in positions),
            net_exposure=market_value,
            margin_used=0.0,
            realized_pnl=0.0,
            unrealized_pnl=unrealized,
            fees_paid=self.fees,
            positions=positions,
        )

    def positions(self) -> list[PositionSnapshot]:
        out: list[PositionSnapshot] = []
        for symbol, qty in self.positions_qty.items():
            if abs(qty) <= 1e-9:
                continue
            avg = self.avg_cost.get(symbol, 0.0)
            last = self.last.get(symbol, avg)
            out.append(
                PositionSnapshot(
                    symbol=symbol,
                    qty=qty,
                    avg_cost=avg,
                    last=last,
                    market_value=qty * last,
                    unrealized_pnl=qty * (last - avg),
                )
            )
        return out

    def open_orders(self) -> list[OrderState]:
        return [copy.deepcopy(o) for o in self.orders.values() if o.is_open]

    def order(self, order_id: str) -> OrderState | None:
        state = self.orders.get(order_id)
        return copy.deepcopy(state) if state is not None else None

    # -- test helpers ------------------------------------------------------

    def forget(self, order_id: str) -> None:
        state = self.orders.pop(order_id, None)
        if state is not None:
            self.by_client.pop(state.client_order_id, None)

    def set_position(self, symbol: str, qty: float, avg_cost: float = 100.0) -> None:
        self.positions_qty[symbol] = qty
        self.avg_cost[symbol] = avg_cost

    def _new_order_id(self) -> str:
        return f"ord-{len(self.orders) + 1:04d}"

    def _reject(self, request: OrderRequest, reason: str) -> OrderState:
        order_id = self._new_order_id()
        state = OrderState(
            order_id=order_id,
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
            updated_at=utc_now(),
            reject_reason=reason,
        )
        self.orders[order_id] = state
        self.by_client[request.client_order_id] = order_id
        return copy.deepcopy(state)

    def _apply_position(self, fill: Fill) -> None:
        signed = fill.qty if fill.side == "BUY" else -fill.qty
        old_qty = self.positions_qty.get(fill.symbol, 0.0)
        new_qty = old_qty + signed
        if fill.side == "BUY":
            old_cost = self.avg_cost.get(fill.symbol, 0.0) * old_qty
            if new_qty > 1e-9:
                self.avg_cost[fill.symbol] = (
                    old_cost + fill.qty * fill.price
                ) / new_qty
            self.cash -= fill.notional + fill.commission
        else:
            self.cash += fill.notional - fill.commission
            if abs(new_qty) <= 1e-9:
                self.avg_cost[fill.symbol] = 0.0
        self.positions_qty[fill.symbol] = new_qty
        self.fees += fill.commission


def request(
    client_id: str = "cid-1",
    *,
    symbol: str = "AAA",
    side: str = "BUY",
    qty: float = 100.0,
    order_type: str = "MARKET",
    limit_price: float | None = None,
    tif: str = "DAY",
    strategy: str = "test",
) -> OrderRequest:
    return OrderRequest(
        client_order_id=client_id,
        symbol=symbol,
        side=side,  # type: ignore[arg-type]
        qty=qty,
        order_type=order_type,  # type: ignore[arg-type]
        limit_price=limit_price,
        tif=tif,  # type: ignore[arg-type]
        strategy=strategy,
        created_at=T0,
    )


def _build(
    path: Path, *, limits: RiskLimits | None = None
) -> tuple[SQLiteStore, FakeExchange, SimulatedOMS]:
    store = SQLiteStore(path)
    exchange = FakeExchange()
    oms = SimulatedOMS(exchange, store, limits or RiskLimits())
    return store, exchange, oms


def _restart(
    path: Path, exchange: FakeExchange, *, limits: RiskLimits | None = None
) -> tuple[SQLiteStore, SimulatedOMS]:
    store = SQLiteStore(path)
    oms = SimulatedOMS(exchange, store, limits or RiskLimits())
    return store, oms


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


def test_store_schema_wal_and_order_roundtrip() -> None:
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "prodsim.db"
        store = SQLiteStore(path)
        assert store.schema_version == SCHEMA_VERSION == 1
        assert store.journal_mode == "wal"
        assert store.integrity_check() == "ok"

        fill = Fill(
            fill_id="fill-1",
            order_id="ord-1",
            ts=T0,
            symbol="AAA",
            side="BUY",
            qty=10.0,
            price=100.0,
            commission=0.1,
        )
        order = OrderState(
            order_id="ord-1",
            client_order_id="cid-1",
            symbol="AAA",
            side="BUY",
            qty=10.0,
            status="PARTIAL",
            filled_qty=10.0,
            avg_price=100.0,
            created_at=T0,
            updated_at=T0 + timedelta(seconds=1),
            fills=[fill],
        )
        seq = store.save_order_and_event(order, "order", {"kind": "order"})
        assert seq >= 1
        loaded = store.load_order("ord-1")
        assert loaded is not None
        assert loaded.client_order_id == "cid-1"
        assert loaded.fills == [fill]
        assert loaded.created_at == T0
        assert store.load_by_client_order_id("cid-1") == loaded
        assert [o.order_id for o in store.list_orders("PARTIAL")] == ["ord-1"]
        assert [o.order_id for o in store.list_orders("OPEN")] == ["ord-1"]
        assert store.list_orders("FILLED") == []
        store.close()

        reopened = SQLiteStore(path)
        again = reopened.load_order("ord-1")
        assert again is not None and again.fills == [fill]
        reopened.close()


def test_store_client_id_is_unique() -> None:
    with TemporaryDirectory() as tmp:
        store = SQLiteStore(Path(tmp) / "prodsim.db")
        first = OrderState(
            order_id="ord-a",
            client_order_id="cid-unique",
            symbol="AAA",
            side="BUY",
            qty=1.0,
            created_at=T0,
            updated_at=T0,
        )
        store.save_order(first)
        duplicate_payload = OrderState(
            order_id="ord-b",
            client_order_id="cid-unique",  # same idempotency key
            symbol="AAA",
            side="BUY",
            qty=2.0,
            created_at=T0,
            updated_at=T0,
        )
        with raises(StoreConflictError):
            store.save_order(duplicate_payload)
        assert [o.order_id for o in store.list_orders()] == ["ord-a"]
        assert store.load_order("ord-a") == first
        assert store.load_order("ord-b") is None

        # Same order id can be updated in place (status transitions).
        first.status = "ACKNOWLEDGED"
        first.updated_at = T0 + timedelta(seconds=1)
        store.save_order(first)
        assert store.load_order("ord-a").status == "ACKNOWLEDGED"  # type: ignore[union-attr]
        store.close()


def test_store_order_and_event_write_is_atomic() -> None:
    with TemporaryDirectory() as tmp:
        store = SQLiteStore(Path(tmp) / "prodsim.db")
        original = OrderState(
            order_id="ord-atomic",
            client_order_id="cid-atomic",
            symbol="AAA",
            side="BUY",
            qty=10.0,
            status="ACKNOWLEDGED",
            created_at=T0,
            updated_at=T0,
        )
        store.save_order(original)
        changed = replace(original, status="FILLED", filled_qty=10.0)
        bad_event = {"kind": "fill", "price": float("nan")}  # not JSON-safe
        with raises(StoreError):
            store.save_order_with_events(changed, [("fill", bad_event)])
        # The failed event must roll the order update back too.
        loaded = store.load_order("ord-atomic")
        assert loaded is not None and loaded.status == "ACKNOWLEDGED"
        assert store.events(limit=100) == []
        store.close()


def test_store_journal_is_append_only_and_paged() -> None:
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "prodsim.db"
        store = SQLiteStore(path)
        seqs = [
            store.append("order", {"kind": "order", "n": 1}),
            store.append("fill", {"kind": "fill", "n": 2}),
            store.append("order", {"kind": "order", "n": 3}),
        ]
        assert seqs == sorted(seqs)
        assert store.last_event_seq() == seqs[-1]
        page = store.events(limit=2)
        assert [e["seq"] for e in page] == seqs[:2]
        page2 = store.events(since=page[-1]["seq"], limit=2)
        assert [e["seq"] for e in page2] == seqs[2:]
        assert [e["kind"] for e in store.events(kind="order")] == ["order", "order"]

        # The journal refuses UPDATE/DELETE even from another connection.
        other = sqlite3.connect(path)
        try:
            with raises(sqlite3.IntegrityError):
                other.execute(
                    "UPDATE events SET kind = 'tampered' WHERE seq = ?", (seqs[0],)
                )
            with raises(sqlite3.IntegrityError):
                other.execute("DELETE FROM events WHERE seq = ?", (seqs[0],))
            other.rollback()
        finally:
            other.close()
        assert store.events(limit=10)[0]["kind"] == "order"
        store.close()


def test_store_snapshots_roundtrip() -> None:
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "prodsim.db"
        store = SQLiteStore(path)
        first = AccountSnapshot(
            ts=T0,
            currency="USD",
            cash=100.0,
            buying_power=100.0,
            equity=100.0,
            gross_exposure=0.0,
            net_exposure=0.0,
            margin_used=0.0,
            realized_pnl=0.0,
            unrealized_pnl=0.0,
            fees_paid=0.0,
        )
        second = AccountSnapshot(
            ts=T0 + timedelta(minutes=1),
            currency="USD",
            cash=110.0,
            buying_power=110.0,
            equity=120.0,
            gross_exposure=10.0,
            net_exposure=10.0,
            margin_used=0.0,
            realized_pnl=1.0,
            unrealized_pnl=9.0,
            fees_paid=0.5,
            positions=[
                PositionSnapshot(
                    symbol="AAA",
                    qty=1.0,
                    avg_cost=100.0,
                    last=110.0,
                    market_value=110.0,
                    unrealized_pnl=10.0,
                )
            ],
        )
        assert store.last_snapshot() is None
        store.save_snapshot(first)
        store.save_snapshot(second)
        loaded = store.last_snapshot()
        assert loaded is not None
        assert loaded.ts == second.ts
        assert loaded.positions[0].symbol == "AAA"
        assert loaded.positions[0].market_value == 110.0
        store.close()
        reopened = SQLiteStore(path)
        assert reopened.last_snapshot() == second
        reopened.close()


def test_store_corruption_and_version_guard() -> None:
    with TemporaryDirectory() as tmp:
        garbage = Path(tmp) / "garbage.db"
        garbage.write_bytes(b"this is not a sqlite database")
        with raises(StoreError):
            SQLiteStore(garbage)

        future = Path(tmp) / "future.db"
        conn = sqlite3.connect(future)
        conn.execute("PRAGMA user_version = 99")
        conn.commit()
        conn.close()
        with raises(SchemaVersionError):
            SQLiteStore(future)

        corrupt = Path(tmp) / "corrupt.db"
        store = SQLiteStore(corrupt)
        store.save_order(
            OrderState(
                order_id="ord-x",
                client_order_id="cid-x",
                symbol="AAA",
                side="BUY",
                qty=1.0,
                created_at=T0,
                updated_at=T0,
            )
        )
        store.close()
        conn = sqlite3.connect(corrupt)
        conn.execute("UPDATE orders SET payload = '{not-json' WHERE order_id = 'ord-x'")
        conn.commit()
        conn.close()
        reopened = SQLiteStore(corrupt)
        with raises(StoreCorruptionError):
            reopened.load_order("ord-x")
        with raises(StoreCorruptionError):
            reopened.list_orders()
        reopened.close()


# ---------------------------------------------------------------------------
# OMS: idempotency, transitions, cancel
# ---------------------------------------------------------------------------


def test_duplicate_client_id_is_idempotent_and_conflict_safe() -> None:
    with TemporaryDirectory() as tmp:
        store, exchange, oms = _build(Path(tmp) / "prodsim.db")
        req = request("cid-1", qty=100.0)

        first = oms.submit(req)
        assert first.status == "ACKNOWLEDGED"
        assert exchange.submits == 1
        assert len(exchange.orders) == 1

        duplicate = oms.submit(req)
        assert duplicate.order_id == first.order_id
        assert exchange.submits == 1  # no second broker submit
        assert len(exchange.orders) == 1
        assert len(oms.orders()) == 1

        oms.on_tick(tick(volume=300.0, second=0))
        assert oms.order(first.order_id).status == "PARTIAL"  # type: ignore[union-attr]
        duplicate_after_fill = oms.submit(req)
        assert duplicate_after_fill.order_id == first.order_id
        assert exchange.submits == 1
        assert exchange.fill_count == 1

        with raises(OrderConflict):
            oms.submit(replace(req, qty=50.0))
        assert exchange.submits == 1
        assert len(exchange.orders) == 1
        assert len(oms.orders()) == 1
        assert DuplicateOrderError is OrderConflict
        assert any(e["kind"] == "order_conflict" for e in store.events())
        assert "cid-1" in str(store.events()[-1]["payload"])
        store.close()


def test_partial_then_filled_transitions() -> None:
    with TemporaryDirectory() as tmp:
        store, exchange, oms = _build(Path(tmp) / "prodsim.db")
        order = oms.submit(request("cid-partial", qty=100.0))
        assert order.status == "ACKNOWLEDGED"

        accepted = oms.on_tick(tick(volume=300.0, second=0))
        assert len(accepted) == 1 and accepted[0].qty == 30.0
        state = oms.order(order.order_id)
        assert state is not None
        assert state.status == "PARTIAL"
        assert state.filled_qty == 30.0
        assert state.remaining_qty == 70.0

        accepted = oms.on_tick(tick(volume=300.0, second=1))
        assert len(accepted) == 1
        state = oms.order(order.order_id)
        assert state is not None and state.filled_qty == 60.0

        accepted = oms.on_tick(tick(volume=500.0, second=2))
        assert len(accepted) == 1 and accepted[0].qty == 40.0
        state = oms.order(order.order_id)
        assert state is not None
        assert state.status == "FILLED"
        assert state.filled_qty == 100.0
        assert len(state.fills) == 3
        assert abs(state.avg_price - 100.0) < 1e-9
        assert oms.open_orders() == []

        durable = store.load_order(order.order_id)
        assert durable is not None and durable.status == "FILLED"
        assert [e["kind"] for e in store.events()].count("fill") == 3
        assert exchange.fill_count == 3
        store.close()


def test_cancel_race_preserves_partial_fill() -> None:
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "prodsim.db"
        store, exchange, oms = _build(path)
        order = oms.submit(request("cid-cancel", qty=100.0))
        oms.on_tick(tick(volume=400.0, second=0))  # 40 shares

        canceled = oms.cancel(order.order_id)
        assert canceled.status == "CANCELED"
        assert canceled.filled_qty == 40.0
        assert len(canceled.fills) == 1
        assert exchange.cancels == 1

        # Cancel is idempotent and cannot fabricate more fills.
        again = oms.cancel(order.order_id)
        assert again.status == "CANCELED" and again.filled_qty == 40.0
        assert exchange.cancels == 1
        assert oms.on_tick(tick(volume=10_000.0, second=1)) == []
        final = oms.order(order.order_id)
        assert final is not None
        assert final.status == "CANCELED"
        assert final.filled_qty == 40.0
        assert len(final.fills) == 1
        store.close()

        # Restart: the partial fill survives and is not duplicated.
        store2, oms2 = _restart(path, exchange)
        restored = oms2.order(order.order_id)
        assert restored is not None
        assert restored.status == "CANCELED"
        assert restored.filled_qty == 40.0
        assert len(restored.fills) == 1
        assert exchange.fill_count == 1
        store2.close()


def test_rejected_transitions_and_local_risk_checks() -> None:
    with TemporaryDirectory() as tmp:
        store, exchange, oms = _build(Path(tmp) / "prodsim.db")

        rejected = oms.submit(request("cid-unknown", symbol="ZZZ", qty=1.0))
        assert rejected.status == "REJECTED"
        assert rejected.reject_reason == "unknown symbol"
        assert exchange.submits == 1
        assert oms.cancel(rejected.order_id).status == "REJECTED"
        assert (
            oms.submit(request("cid-unknown", symbol="ZZZ", qty=1.0)).order_id
            == rejected.order_id
        )
        assert exchange.submits == 1
        assert [o.order_id for o in oms.orders(status="REJECTED")] == [
            rejected.order_id
        ]

        # Local max-notional gate: no broker submit at all.
        limits = RiskLimits(max_order_notional=1_000.0)
        store2 = SQLiteStore(Path(tmp) / "limits.db")
        exchange2 = FakeExchange()
        oms2 = SimulatedOMS(exchange2, store2, limits)
        big = oms2.submit(
            request("cid-big", order_type="LIMIT", qty=100.0, limit_price=50.0)
        )
        assert big.status == "REJECTED"
        assert "max order notional" in (big.reject_reason or "")
        assert exchange2.submits == 0
        # Same id, same payload => same rejected order; changed payload => conflict.
        assert (
            oms2.submit(
                request("cid-big", order_type="LIMIT", qty=100.0, limit_price=50.0)
            ).order_id
            == big.order_id
        )
        with raises(OrderConflict):
            oms2.submit(
                request("cid-big", order_type="LIMIT", qty=101.0, limit_price=50.0)
            )

        # Shorting is rejected locally when no position exists.
        short = oms2.submit(
            request(
                "cid-short",
                side="SELL",
                qty=10.0,
                order_type="LIMIT",
                limit_price=100.0,
            )
        )
        assert short.status == "REJECTED"
        assert "short selling" in (short.reject_reason or "")
        assert exchange2.submits == 0

        # Closing a long is allowed (not mistaken for a short).
        store3 = SQLiteStore(Path(tmp) / "close.db")
        exchange3 = FakeExchange()
        exchange3.set_position("AAA", 10.0, 100.0)
        oms3 = SimulatedOMS(exchange3, store3)
        close = oms3.submit(request("cid-close", side="SELL", qty=10.0))
        assert close.status == "ACKNOWLEDGED"
        oms3.on_tick(tick(volume=200.0, second=0))
        closed = oms3.order(close.order_id)
        assert closed is not None and closed.status == "FILLED"
        assert exchange3.positions_qty["AAA"] == 0.0
        store.close()
        store2.close()
        store3.close()


# ---------------------------------------------------------------------------
# OMS: crash recovery / restart / timeout
# ---------------------------------------------------------------------------


def test_broker_timeout_after_accept_reconciles_without_duplicate() -> None:
    with TemporaryDirectory() as tmp:
        store = SQLiteStore(Path(tmp) / "prodsim.db")
        exchange = FakeExchange()
        exchange.fail_after_accept.add("cid-timeout")
        oms = SimulatedOMS(exchange, store)
        order = oms.submit(request("cid-timeout", qty=100.0))
        assert exchange.submits == 1
        assert order.status == "ACKNOWLEDGED"
        assert len(exchange.orders) == 1
        assert oms.submit(request("cid-timeout", qty=100.0)).order_id == order.order_id
        assert exchange.submits == 1
        store.close()


def test_broker_timeout_before_accept_retries_once() -> None:
    with TemporaryDirectory() as tmp:
        store = SQLiteStore(Path(tmp) / "prodsim.db")
        exchange = FakeExchange()
        exchange.fail_before_accept.add("cid-retry")
        oms = SimulatedOMS(exchange, store)
        order = oms.submit(request("cid-retry", qty=100.0))
        assert exchange.submits == 2  # first failed, one retry
        assert len(exchange.orders) == 1
        oms.on_tick(tick(volume=10_000.0, second=0))
        final = oms.order(order.order_id)
        assert final is not None and final.status == "FILLED"
        assert exchange.fill_count == 1
        store.close()


def test_crash_after_exchange_ack_before_store_write_adopts_order() -> None:
    class CrashStore(SQLiteStore):
        """Fails the first order write to emulate a crash after the broker ack."""

        def __init__(self, path: str | Path) -> None:
            super().__init__(path)
            self.fail_next_order_write = True

        def save_order_with_events(
            self, order: OrderState, events: object = ()
        ) -> list[int]:
            if self.fail_next_order_write:
                self.fail_next_order_write = False
                raise StoreError("simulated crash before order persist")
            return super().save_order_with_events(order, events)

    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "prodsim.db"
        store = CrashStore(path)
        exchange = FakeExchange()
        oms = SimulatedOMS(exchange, store)
        with raises(StoreError):
            oms.submit(request("cid-crash-ack", qty=100.0))
        assert len(exchange.orders) == 1
        assert store.list_orders() == []  # order never became durable
        store.close()

        # Restart: the intent event is still there, and the open exchange order
        # is adopted by client id.  It is not submitted again.
        store2, oms2 = _restart(path, exchange)
        recovered = oms2.orders()
        assert len(recovered) == 1
        assert recovered[0].client_order_id == "cid-crash-ack"
        assert exchange.submits == 1
        oms2.on_tick(tick(volume=10_000.0, second=0))
        assert exchange.fill_count == 1
        filled = oms2.order(recovered[0].order_id)
        assert filled is not None and filled.status == "FILLED"
        assert len(filled.fills) == 1
        store2.close()

        # And once more: the fill stays a single fill.
        store3, oms3 = _restart(path, exchange)
        again = oms3.orders()
        assert len(again) == 1
        assert again[0].status == "FILLED"
        assert len(again[0].fills) == 1
        assert exchange.fill_count == 1
        store3.close()


def test_crash_before_broker_ack_does_not_resubmit_intent() -> None:
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "prodsim.db"
        store = SQLiteStore(path)
        req = request("cid-intent-only", qty=100.0)
        store.append(
            "order_intent",
            {"kind": "order_intent", "intent_id": "intent-1", "request": jsonable(req)},
        )
        store.close()

        exchange = FakeExchange()
        store2, oms = _restart(path, exchange)
        report = oms.recovery_report
        assert report["unconfirmed"] == 1
        assert exchange.submits == 0
        assert oms.orders() == []
        with raises(OrderUnconfirmedError):
            oms.submit(req)
        assert exchange.submits == 0  # fail closed, never a blind resubmit
        assert oms.on_tick(tick(volume=10_000.0, second=0)) == []
        assert exchange.fill_count == 0
        assert any(
            e["payload"].get("kind") == "order_unconfirmed" for e in store2.events()
        )
        store2.close()


def test_restart_reloads_partial_and_never_double_fills() -> None:
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "prodsim.db"
        store, exchange, oms = _build(path)
        order = oms.submit(request("cid-restart", qty=100.0))
        oms.on_tick(tick(volume=300.0, second=0))  # 30
        partial = oms.order(order.order_id)
        assert partial is not None and partial.filled_qty == 30.0
        store.close()

        store2, oms2 = _restart(path, exchange)
        reloaded = oms2.order(order.order_id)
        assert reloaded is not None
        assert reloaded.status == "PARTIAL"
        assert reloaded.filled_qty == 30.0
        oms2.on_tick(tick(volume=10_000.0, second=1))  # remaining 70
        done = oms2.order(order.order_id)
        assert done is not None
        assert done.status == "FILLED"
        assert done.filled_qty == 100.0
        assert len(done.fills) == 2
        assert len({f.fill_id for f in done.fills}) == 2
        store2.close()

        store3, oms3 = _restart(path, exchange)
        final = oms3.order(order.order_id)
        assert final is not None
        assert final.status == "FILLED"
        assert final.filled_qty == 100.0
        assert len(final.fills) == 2
        assert exchange.fill_count == 2
        fill_events = [e for e in store3.events(limit=10_000) if e["kind"] == "fill"]
        assert len(fill_events) == 2
        assert len({e["payload"]["fill_id"] for e in fill_events}) == 2
        store3.close()


def test_recovery_finalizes_missing_order_and_preserves_partial() -> None:
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "prodsim.db"
        store, exchange, oms = _build(path)
        order = oms.submit(request("cid-missing", qty=100.0))
        oms.on_tick(tick(volume=300.0, second=0))  # 30
        exchange.forget(order.order_id)
        store.close()

        store2, oms2 = _restart(path, exchange)
        report = oms2.recovery_report
        assert report["finalized"] == 1
        recovered = oms2.order(order.order_id)
        assert recovered is not None
        assert recovered.status == "CANCELED"
        assert recovered.filled_qty == 30.0
        assert len(recovered.fills) == 1
        assert "filled qty preserved" in (recovered.reject_reason or "")
        assert exchange.fill_count == 1
        assert oms2.on_tick(tick(volume=10_000.0, second=1)) == []
        store2.close()


def test_order_queries_account_positions_and_snapshot() -> None:
    with TemporaryDirectory() as tmp:
        store, exchange, oms = _build(Path(tmp) / "prodsim.db")
        one = oms.submit(request("cid-a", qty=10.0))
        two = oms.submit(request("cid-b", qty=20.0))
        assert {o.order_id for o in oms.open_orders()} == {one.order_id, two.order_id}
        assert len(oms.orders(status="OPEN")) == 2
        assert oms.order("missing") is None
        with raises(OrderNotFound):
            oms.cancel("missing")

        assert isinstance(oms.account(), AccountSnapshot)
        assert isinstance(oms.positions(), list)
        snapshot = oms.snapshot()
        assert isinstance(snapshot, AccountSnapshot)
        assert store.last_snapshot() is not None
        oms.mark({"AAA": 101.0})
        assert exchange.last["AAA"] == 101.0
        store.close()


def main() -> None:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"ok: {fn.__name__}")
    print(f"ok: {len(tests)} prodsim OMS/store tests")


if __name__ == "__main__":
    main()
