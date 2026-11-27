"""End-to-end checks for the merged v12 exchange + OMS + SQLite stack."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot.prodsim.contract import OrderRequest, RunConfig, Tick  # noqa: E402
from bot.prodsim.exchange import SimulatedExchange  # noqa: E402
from bot.prodsim.oms import SimulatedOMS  # noqa: E402
from bot.prodsim.store import SQLiteStore  # noqa: E402


def _tick(ts: datetime, bid: float = 99.99, ask: float = 100.01) -> Tick:
    return Tick(
        ts=ts,
        symbol="SPY",
        bid=bid,
        ask=ask,
        last=(bid + ask) / 2,
        volume=500,
    )


def test_partial_fill_and_account_identity() -> None:
    with TemporaryDirectory() as td:
        cfg = RunConfig(
            capital=100_000, symbols=("SPY",), commission_bps=0, slippage_bps=0
        )
        exchange = SimulatedExchange(cfg, ["SPY"])
        store = SQLiteStore(Path(td) / "sim.sqlite3")
        oms = SimulatedOMS(exchange, store, cfg)
        ts = datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc)
        order = oms.submit(
            OrderRequest(client_order_id="c1", symbol="SPY", side="BUY", qty=100)
        )
        assert order.status == "ACKNOWLEDGED"

        fills = oms.on_tick(_tick(ts))
        assert sum(f.qty for f in fills) == 50
        order = oms.order(order.order_id)
        assert order is not None and order.status == "PARTIAL"
        assert order.filled_qty == 50

        fills = oms.on_tick(_tick(ts + timedelta(minutes=1)))
        assert sum(f.qty for f in fills) == 50
        order = oms.order(order.order_id)
        assert order is not None and order.status == "FILLED"
        assert order.filled_qty == 100

        account = oms.account()
        assert (
            abs(account.cash + account.positions[0].market_value - account.equity)
            < 1e-6
        )
        assert account.positions[0].qty == 100
        store.close()


def test_idempotent_duplicate_never_submits_twice() -> None:
    with TemporaryDirectory() as td:
        cfg = RunConfig(
            capital=100_000, symbols=("SPY",), commission_bps=0, slippage_bps=0
        )
        exchange = SimulatedExchange(cfg, ["SPY"])
        store = SQLiteStore(Path(td) / "sim.sqlite3")
        oms = SimulatedOMS(exchange, store, cfg)
        req = OrderRequest(client_order_id="dup", symbol="SPY", side="BUY", qty=10)
        first = oms.submit(req)
        second = oms.submit(req)
        assert first.order_id == second.order_id
        assert len(exchange.open_orders()) == 1
        store.close()


def test_cancel_race_keeps_partial_fill() -> None:
    with TemporaryDirectory() as td:
        cfg = RunConfig(
            capital=100_000, symbols=("SPY",), commission_bps=0, slippage_bps=0
        )
        exchange = SimulatedExchange(cfg, ["SPY"])
        store = SQLiteStore(Path(td) / "sim.sqlite3")
        oms = SimulatedOMS(exchange, store, cfg)
        ts = datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc)
        order = oms.submit(
            OrderRequest(client_order_id="cancel", symbol="SPY", side="BUY", qty=100)
        )
        oms.on_tick(_tick(ts))
        canceled = oms.cancel(order.order_id)
        assert canceled.status == "CANCELED"
        assert canceled.filled_qty == 50
        assert oms.on_tick(_tick(ts + timedelta(minutes=1))) == []
        store.close()


def test_restart_recovery_does_not_double_fill() -> None:
    with TemporaryDirectory() as td:
        path = Path(td) / "sim.sqlite3"
        cfg = RunConfig(
            capital=100_000, symbols=("SPY",), commission_bps=0, slippage_bps=0
        )
        exchange = SimulatedExchange(cfg, ["SPY"])
        store = SQLiteStore(path)
        oms = SimulatedOMS(exchange, store, cfg)
        ts = datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc)
        order = oms.submit(
            OrderRequest(client_order_id="restart", symbol="SPY", side="BUY", qty=100)
        )
        oms.on_tick(_tick(ts))
        store.close()

        store2 = SQLiteStore(path)
        oms2 = SimulatedOMS(exchange, store2, cfg)
        assert oms2.order(order.order_id).filled_qty == 50
        oms2.on_tick(_tick(ts + timedelta(minutes=1)))
        recovered = oms2.order(order.order_id)
        assert recovered is not None and recovered.filled_qty == 100
        assert recovered.status == "FILLED"
        store2.close()


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok: {name}")
    print("ok: prodsim stack")


if __name__ == "__main__":
    main()
