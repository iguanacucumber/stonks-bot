"""Simulation facade tests with in-process fakes."""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot.prodsim.clock import ClockPaused, OutOfOrderTick  # noqa: E402
from bot.prodsim.contract import OrderRequest, RiskLimits, RunConfig, Tick  # noqa: E402
from bot.prodsim.fakes import MemoryStore, PaperExchange  # noqa: E402
from bot.prodsim.replay import ReplayFeed, synthetic_bars  # noqa: E402
from bot.prodsim.simulation import Simulation, SimulationError  # noqa: E402
from bot.prodsim.strategy import MonthlyBookStrategy  # noqa: E402

T0 = datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc)


def _tick(i: int, last: float = 100.0, symbol: str = "AAA", volume: float = 1e6) -> Tick:
    return Tick(
        ts=T0 + timedelta(seconds=i),
        symbol=symbol,
        bid=last - 0.05,
        ask=last + 0.05,
        last=last,
        volume=volume,
    )


def _sim(
    *,
    capital: float = 100_000.0,
    state_dir: str = "",
    limits: RiskLimits | None = None,
    strategy: object | None = None,
) -> tuple[Simulation, PaperExchange, MemoryStore]:
    cfg = RunConfig(
        capital=capital,
        state_dir=state_dir,
        limits=limits or RiskLimits(),
    )
    ex = PaperExchange(capital=capital, symbols=("AAA", "BBB"), limits=cfg.limits)
    store = MemoryStore()
    sim = Simulation(cfg, exchange=ex, store=store, strategy=strategy)  # type: ignore[arg-type]
    return sim, ex, store


def test_start_stop_and_status_file() -> None:
    with TemporaryDirectory() as tmp:
        sim, _, _ = _sim(state_dir=tmp)
        sim.start()
        assert sim.state == "RUNNING"
        sim.on_tick(_tick(0))
        assert sim.status().processed_ticks == 1
        sim.stop()
        assert sim.state == "STOPPED"
        path = Path(tmp) / "status.json"
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["status"]["state"] == "STOPPED"
        assert data["health"]["processed_ticks"] == 1
        assert data["account"]["currency"] == "USD"


def test_pause_blocks_ticks_and_orders() -> None:
    sim, _, _ = _sim()
    sim.start()
    sim.on_tick(_tick(0))
    sim.pause()
    assert sim.state == "PAUSED"
    try:
        sim.on_tick(_tick(1))
    except ClockPaused:
        pass
    else:
        raise AssertionError("paused simulation accepted a tick")
    try:
        sim.submit(
            OrderRequest(client_order_id="p1", symbol="AAA", side="BUY", qty=1)
        )
    except SimulationError:
        pass
    else:
        raise AssertionError("paused simulation accepted an order")
    sim.resume()
    sim.on_tick(_tick(1))
    assert sim.status().processed_ticks == 2
    sim.stop()


def test_duplicate_and_out_of_order_ticks_rejected() -> None:
    sim, ex, _ = _sim()
    sim.start()
    sim.on_tick(_tick(0))
    for bad in (_tick(0), _tick(-1)):
        try:
            sim.on_tick(bad)
        except OutOfOrderTick:
            pass
        else:
            raise AssertionError(f"accepted non-monotonic tick {bad.ts}")
    assert sim.status().processed_ticks == 1
    assert not ex.fills()
    sim.stop()


def test_duplicate_order_returns_original() -> None:
    sim, ex, store = _sim()
    sim.start()
    sim.on_tick(_tick(0))
    req = OrderRequest(client_order_id="same", symbol="AAA", side="BUY", qty=5)
    one = sim.submit(req)
    two = sim.submit(req)
    assert one.order_id == two.order_id
    assert len(ex.all_orders()) == 1
    assert len(store.list_orders(limit=10)) == 1
    assert any(e["kind"] == "order.duplicate" for e in store.events(limit=20))
    sim.stop()


def test_cancel_unknown_and_kill() -> None:
    sim, ex, _ = _sim()
    sim.start()
    sim.on_tick(_tick(0))
    order = sim.submit(
        OrderRequest(client_order_id="k1", symbol="AAA", side="BUY", qty=10)
    )
    try:
        sim.cancel("missing")
    except SimulationError:
        pass
    else:
        raise AssertionError("cancel unknown order accepted")
    result = sim.kill("drill", "tester")
    assert result["canceled"] == 1
    assert sim.state == "KILLED"
    assert sim.order(order.order_id).status == "CANCELED"
    assert not ex.open_orders()
    try:
        sim.submit(
            OrderRequest(client_order_id="k2", symbol="AAA", side="BUY", qty=1)
        )
    except SimulationError:
        pass
    else:
        raise AssertionError("killed simulation accepted an order")


def test_health_outage_and_stale() -> None:
    sim, _, _ = _sim()
    sim.start()
    sim.on_tick(_tick(0))
    assert sim.health()["ready"] is True
    sim.note_session(date(2026, 1, 5))
    health = sim.health()
    assert health["ready"] is False
    assert any("outage" in issue for issue in health["issues"])
    sim.stop()
    assert sim.health()["ready"] is False


def test_metrics_and_events() -> None:
    sim, _, store = _sim()
    sim.start()
    sim.on_tick(_tick(0))
    sim.submit(OrderRequest(client_order_id="m1", symbol="AAA", side="BUY", qty=1))
    sim.on_tick(_tick(1))
    text = sim.metrics()
    assert "prodsim_ready" in text
    assert "prodsim_ticks_total 2" in text
    assert "prodsim_orders_total 1" in text
    assert "prodsim_fills_total 1" in text
    events = sim.events(limit=100)
    assert events == sorted(events, key=lambda e: e["seq"])
    kinds = {e["kind"] for e in events}
    assert {"run.started", "order.submitted", "fill"} <= kinds
    assert sim.status().fills == 1
    sim.stop()


def test_run_feed_finite_and_strategy() -> None:
    cfg = RunConfig(capital=100_000.0, state_dir="", participation_cap=1.0)
    ex = PaperExchange(capital=cfg.capital, symbols=("AAA", "BBB"), limits=cfg.limits)
    store = MemoryStore()
    strategy = MonthlyBookStrategy(
        lambda session, closes: {"AAA": 0.5, "BBB": 0.5},
        always_rebalance=True,
        min_order_notional=0.0,
    )
    sim = Simulation(cfg, exchange=ex, store=store, strategy=strategy)
    bars = synthetic_bars(
        ["AAA", "BBB"],
        [date(2026, 1, 26), date(2026, 1, 27)],
        seed=5,
        volume=5_000_000,
    )
    sim.run_feed(ReplayFeed(bars, seed=5, ticks_per_bar=3), join_timeout=20)
    assert sim.state == "STOPPED"
    assert sim.status().processed_ticks == 12
    assert sim.status().orders >= 2
    assert sim.status().fills >= 2
    assert strategy.pending_session() == date(2026, 1, 26)
    assert len(sim.positions()) >= 1
    assert sim.account().equity > 0
    sim.stop()


def test_feed_error_fails_closed() -> None:
    sim, _, _ = _sim()
    sim.start()

    def broken():
        yield _tick(0)
        raise RuntimeError("cache vanished")

    sim.run_feed(broken(), join_timeout=20)
    assert sim.state == "ERROR"
    assert sim.status().errors >= 1
    assert sim.health()["ready"] is False
    assert any(e["kind"] == "run.error" for e in sim.events(limit=50))
    sim.stop()


def test_api_wiring_and_shutdown() -> None:
    sim, _, _ = _sim()
    sim.start()
    api = sim.start_api(port=0, token="", dev=True)
    assert api is not None
    assert api.base_url.startswith("http://127.0.0.1:")
    assert any(e["kind"] == "api.started" for e in sim.events(limit=20))
    sim.stop()  # also shuts the API listener down
    assert api.wait_closed(timeout=5)


def test_real_oms_stack_with_sqlite() -> None:
    from bot.prodsim.exchange import SimulatedExchange
    from bot.prodsim.oms import SimulatedOMS
    from bot.prodsim.store import SQLiteStore

    with TemporaryDirectory() as tmp:
        cfg = RunConfig(
            capital=100_000.0,
            state_dir=tmp,
            symbols=("AAA",),
            start="2026-01-02",
        )
        store = SQLiteStore(Path(tmp) / "prodsim.sqlite3")
        exchange = SimulatedExchange(config=cfg, instruments=("AAA",))
        oms = SimulatedOMS(exchange, store, cfg)
        sim = Simulation(cfg, exchange=exchange, store=store, oms=oms)
        sim.start()
        sim.on_tick(_tick(0, volume=1e6))
        req = OrderRequest(client_order_id="real-1", symbol="AAA", side="BUY", qty=10)
        first = sim.submit(req)
        duplicate = sim.submit(req)
        assert first.order_id == duplicate.order_id
        assert len(oms.orders(limit=20)) == 1
        sim.on_tick(_tick(1, volume=1e6))
        filled = sim.order(first.order_id)
        assert filled is not None and filled.status == "FILLED"
        assert abs(filled.filled_qty - 10) < 1e-9
        assert sim.status().fills == 1
        # The durable store, not the facade, is the order source of truth.
        assert store.load_order(first.order_id) is not None
        sim.close()
        assert store.closed


def test_restore_idempotency_from_store() -> None:
    cfg = RunConfig(capital=100_000.0, state_dir="")
    ex = PaperExchange(capital=cfg.capital, symbols=("AAA",), limits=cfg.limits)
    store = MemoryStore()
    sim1 = Simulation(cfg, exchange=ex, store=store)
    sim1.start()
    sim1.on_tick(_tick(0))
    req = OrderRequest(client_order_id="persisted", symbol="AAA", side="BUY", qty=3)
    original = sim1.submit(req)
    sim1.stop()
    sim2 = Simulation(cfg, exchange=ex, store=store)
    sim2.start()
    again = sim2.submit(req)
    assert again.order_id == original.order_id
    assert len(ex.all_orders()) == 1
    sim2.stop()


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok: {name}")
    print("ok: prodsim simulation")


if __name__ == "__main__":
    main()
