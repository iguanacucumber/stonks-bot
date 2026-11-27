"""Executable scenarios for the v12 production simulation.

Each function is deterministic, uses the in-process reference fakes, and returns
a small JSON-able summary. They cover the ten scenarios required by
``docs/prod_sim_contract.md`` and are used by both the test suite and
``scripts/prod_sim.py scenario``.
"""

from __future__ import annotations

import time
import tracemalloc
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Iterable

from .contract import OrderRequest, RiskLimits, RunConfig, Tick
from .fakes import MemoryStore, PaperExchange
from .replay import ReplayFeed, synthetic_bars
from .simulation import Simulation, SimulationError
from .strategy import MonthlyBookStrategy

__all__ = ["SCENARIOS", "run", "run_all"]

UTC = timezone.utc
BASE = datetime(2026, 1, 2, 14, 30, tzinfo=UTC)


def _config(
    *,
    capital: float = 100_000.0,
    commission_bps: float = 0.0,
    slippage_bps: float = 0.0,
    participation_cap: float = 0.10,
    limits: RiskLimits | None = None,
    mode: str = "replay",
) -> RunConfig:
    return RunConfig(
        capital=capital,
        mode=mode,  # type: ignore[arg-type]
        speed=1.0,
        state_dir="",
        commission_bps=commission_bps,
        slippage_bps=slippage_bps,
        participation_cap=participation_cap,
        limits=limits or RiskLimits(),
    )


def _tick(
    index: int,
    symbol: str = "AAA",
    last: float = 100.0,
    *,
    volume: float = 1_000_000.0,
    spread: float = 0.02,
    session: date | None = None,
) -> Tick:
    ts = (
        BASE + timedelta(seconds=index)
        if session is None
        else datetime(session.year, session.month, session.day, 14, 30, tzinfo=UTC)
        + timedelta(seconds=index)
    )
    return Tick(
        ts=ts,
        symbol=symbol,
        bid=round(last - spread / 2, 6),
        ask=round(last + spread / 2, 6),
        last=last,
        volume=volume,
    )


def _exchange(
    config: RunConfig,
    symbols: Iterable[str],
    *,
    margin_multiplier: float | None = None,
    halted: Iterable[str] = (),
) -> PaperExchange:
    return PaperExchange(
        capital=config.capital,
        symbols=tuple(symbols),
        limits=config.limits,
        commission_bps=config.commission_bps,
        min_commission=config.min_commission,
        slippage_bps=config.slippage_bps,
        participation_cap=config.participation_cap,
        margin_multiplier=margin_multiplier,
        started_at=BASE,
        halted=halted,
    )


def _summary(name: str, sim: Simulation, **extra: Any) -> dict[str, Any]:
    account = sim.account()
    return {
        "scenario": name,
        "ok": True,
        "state": sim.state,
        "ticks": sim.status().processed_ticks,
        "orders": sim.status().orders,
        "fills": sim.status().fills,
        "errors": sim.status().errors,
        "cash": round(account.cash, 6),
        "equity": round(account.equity, 6),
        "realized_pnl": round(account.realized_pnl, 6),
        "unrealized_pnl": round(account.unrealized_pnl, 6),
        "fees": round(account.fees_paid, 6),
        "positions": {
            p.symbol: {"qty": p.qty, "avg_cost": round(p.avg_cost, 6)}
            for p in account.positions
        },
        **extra,
    }


# 1. partial fill, mark-to-market, P&L
def partial_fill() -> dict[str, Any]:
    cfg = _config(commission_bps=1.0, slippage_bps=0.0, participation_cap=0.10)
    ex = _exchange(cfg, ["AAA"])
    store = MemoryStore()
    sim = Simulation(cfg, exchange=ex, store=store)
    sim.start()
    sim.on_tick(_tick(0, last=100.0, volume=1_000_000))  # mark only
    order = sim.submit(
        OrderRequest(client_order_id="partial-1", symbol="AAA", side="BUY", qty=100)
    )
    sim.on_tick(_tick(1, last=100.0, volume=300))  # cap 30
    partial = sim.order(order.order_id)
    assert partial is not None and partial.status == "PARTIAL", partial
    assert abs(partial.filled_qty - 30) < 1e-9, partial.filled_qty
    sim.on_tick(_tick(2, last=101.0, volume=1_000))  # 70 more, then mark
    final = sim.order(order.order_id)
    assert final is not None and final.status == "FILLED", final
    assert abs(final.filled_qty - 100) < 1e-9
    account = sim.account()
    assert len(account.positions) == 1
    pos = account.positions[0]
    assert abs(pos.qty - 100) < 1e-9
    assert pos.unrealized_pnl > 0  # marked up from 100-ish to 101
    assert account.fees_paid > 0
    out = _summary(
        "partial_fill",
        sim,
        order_id=order.order_id,
        status=final.status,
        filled_qty=final.filled_qty,
        avg_price=round(final.avg_price, 6),
        position_qty=pos.qty,
        unrealized=round(pos.unrealized_pnl, 6),
    )
    sim.stop()
    return out


# 2. duplicate idempotency
def duplicate_idempotency() -> dict[str, Any]:
    cfg = _config()
    ex = _exchange(cfg, ["AAA"])
    store = MemoryStore()
    sim = Simulation(cfg, exchange=ex, store=store)
    sim.start()
    sim.on_tick(_tick(0))
    req = OrderRequest(client_order_id="dup-1", symbol="AAA", side="BUY", qty=10)
    first = sim.submit(req)
    second = sim.submit(req)
    assert first.order_id == second.order_id
    assert sim.status().orders == 1
    assert len(ex.all_orders()) == 1
    sim.on_tick(_tick(1))
    sim.on_tick(_tick(2))
    account = sim.account()
    assert abs(account.positions[0].qty - 10) < 1e-9
    assert sim.status().fills == 1
    out = _summary(
        "duplicate",
        sim,
        order_id=first.order_id,
        duplicate_same_order=first.order_id == second.order_id,
        broker_orders=len(ex.all_orders()),
    )
    sim.stop()
    return out


# 3. cancel/replace race after a partial fill
def cancel_race() -> dict[str, Any]:
    cfg = _config(participation_cap=0.10)
    ex = _exchange(cfg, ["AAA"])
    store = MemoryStore()
    sim = Simulation(cfg, exchange=ex, store=store)
    sim.start()
    sim.on_tick(_tick(0))
    order = sim.submit(
        OrderRequest(client_order_id="race-1", symbol="AAA", side="BUY", qty=100)
    )
    sim.on_tick(_tick(1, volume=300))  # fills 30
    canceled = sim.cancel(order.order_id)
    assert canceled.status == "CANCELED", canceled
    assert abs(canceled.filled_qty - 30) < 1e-9, canceled.filled_qty
    sim.on_tick(_tick(2, volume=1_000))
    after = sim.order(order.order_id)
    assert after is not None and abs(after.filled_qty - 30) < 1e-9
    assert abs(sim.account().positions[0].qty - 30) < 1e-9
    out = _summary(
        "cancel_race",
        sim,
        status=canceled.status,
        filled_qty=canceled.filled_qty,
        position_qty=30.0,
    )
    sim.stop()
    return out


# 4. buying power / max order notional
def buying_power() -> dict[str, Any]:
    cfg = _config(
        capital=1_000.0,
        limits=RiskLimits(max_gross=1.0, max_order_notional=100_000.0),
    )
    ex = _exchange(cfg, ["AAA"], margin_multiplier=1.0)
    store = MemoryStore()
    sim = Simulation(cfg, exchange=ex, store=store)
    sim.start()
    sim.on_tick(_tick(0))
    bp = sim.submit(
        OrderRequest(client_order_id="bp-1", symbol="AAA", side="BUY", qty=100)
    )
    assert bp.status == "REJECTED", bp
    assert "buying power" in (bp.reject_reason or ""), bp.reject_reason
    big_cfg = _config(
        capital=1_000_000.0,
        limits=RiskLimits(max_order_notional=5_000.0),
    )
    ex2 = _exchange(big_cfg, ["AAA"])
    sim2 = Simulation(big_cfg, exchange=ex2, store=MemoryStore())
    sim2.start()
    sim2.on_tick(_tick(0))
    big = sim2.submit(
        OrderRequest(client_order_id="bp-2", symbol="AAA", side="BUY", qty=100)
    )
    assert big.status == "REJECTED", big
    assert "notional" in (big.reject_reason or ""), big.reject_reason
    out = _summary(
        "buying_power",
        sim,
        buying_power_reason=bp.reject_reason,
        notional_reason=big.reject_reason,
    )
    sim.stop()
    sim2.stop()
    return out


# 5. data outage: no tick => not ready, no fabricated fill
def data_outage() -> dict[str, Any]:
    cfg = _config()
    ex = _exchange(cfg, ["AAA"])
    store = MemoryStore()
    sim = Simulation(cfg, exchange=ex, store=store)
    sim.start()
    sim.on_tick(_tick(0))
    before = sim.account()
    sim.note_session(date(2026, 1, 5))  # next session produces no tick
    health = sim.health()
    assert health["ready"] is False, health
    assert any("outage" in issue for issue in health["issues"]), health
    after = sim.account()
    assert before.cash == after.cash and before.equity == after.equity
    out = _summary(
        "data_outage",
        sim,
        ready=health["ready"],
        issues=health["issues"],
        cash_unchanged=before.cash == after.cash,
    )
    sim.stop()
    return out


# 6/7. restart and store/broker ordering
def restart() -> dict[str, Any]:
    cfg = _config()
    ex = _exchange(cfg, ["AAA"])
    store = MemoryStore()
    sim1 = Simulation(cfg, exchange=ex, store=store)
    sim1.start()
    sim1.on_tick(_tick(0))
    req = OrderRequest(client_order_id="restart-1", symbol="AAA", side="BUY", qty=10)
    order = sim1.submit(req)
    sim1.on_tick(_tick(1, volume=50))  # cap 5 of 10 -> partial
    partial = sim1.order(order.order_id)
    assert partial is not None and partial.status == "PARTIAL"
    sim1.stop()
    # New process, same durable store and broker: a replay of the same client
    # order id must return the original, not submit a second order.
    sim2 = Simulation(cfg, exchange=ex, store=store)
    sim2.start()
    replayed = sim2.submit(req)
    assert replayed.order_id == order.order_id
    assert len(ex.all_orders()) == 1
    sim2.on_tick(_tick(2, volume=1_000))
    account = sim2.account()
    assert abs(account.positions[0].qty - 10) < 1e-9
    assert sim2.status().fills == 1  # one partial before restart, one after
    assert len(ex.fills()) == 2
    # Store-ahead-of-ack: a durable order with no broker counterpart must not
    # be re-sent. (The API/OMS will reconcile it; replay itself must not double.)
    store3 = MemoryStore()
    ex3 = _exchange(cfg, ["AAA"])
    store3.save_order(partial or order)
    sim3 = Simulation(cfg, exchange=ex3, store=store3)
    sim3.start()
    again = sim3.submit(req)
    assert again.order_id == order.order_id
    assert len(ex3.all_orders()) == 0
    out = _summary(
        "restart",
        sim2,
        order_id=order.order_id,
        broker_orders=len(ex.all_orders()),
        store_ahead_resubmitted=len(ex3.all_orders()),
        fills_after_restart=sim2.status().fills,
    )
    sim2.stop()
    sim3.stop()
    return out


# 8. kill switch
def kill_switch() -> dict[str, Any]:
    cfg = _config(participation_cap=0.10)
    ex = _exchange(cfg, ["AAA"])
    store = MemoryStore()
    sim = Simulation(cfg, exchange=ex, store=store)
    sim.start()
    sim.on_tick(_tick(0))
    order = sim.submit(
        OrderRequest(client_order_id="kill-1", symbol="AAA", side="BUY", qty=100)
    )
    result = sim.kill("operator drill", "test")
    assert result["state"] == "KILLED" and result["canceled"] == 1, result
    canceled = sim.order(order.order_id)
    assert canceled is not None and canceled.status == "CANCELED"
    assert not ex.open_orders()
    try:
        sim.submit(
            OrderRequest(client_order_id="kill-2", symbol="AAA", side="BUY", qty=1)
        )
    except SimulationError:
        blocked = True
    else:  # pragma: no cover - assert below gives the failure
        blocked = False
    assert blocked, "kill switch did not block a new order"
    out = _summary(
        "kill",
        sim,
        kill=result,
        status=canceled.status,
        new_orders_blocked=blocked,
    )
    return out


# 9. same-day replay idempotency
def replay_idempotency() -> dict[str, Any]:
    sessions = [date(2026, 1, 26), date(2026, 1, 27), date(2026, 1, 28)]

    def run_once() -> tuple[dict[str, Any], int, int]:
        cfg = _config(capital=100_000.0, participation_cap=1.0)
        ex = _exchange(cfg, ["AAA", "BBB"])
        store = MemoryStore()
        strategy = MonthlyBookStrategy(
            lambda session, closes: {"AAA": 0.5, "BBB": 0.5},
            always_rebalance=True,
            min_order_notional=0.0,
            limits=cfg.limits,
        )
        sim = Simulation(cfg, exchange=ex, store=store, strategy=strategy)
        bars = synthetic_bars(["AAA", "BBB"], sessions, seed=11, volume=2_000_000)
        feed = ReplayFeed(bars, seed=11, ticks_per_bar=3)
        sim.run_feed(feed, join_timeout=30)
        account = sim.account()
        summary = {
            "cash": round(account.cash, 6),
            "equity": round(account.equity, 6),
            "positions": {
                p.symbol: (round(p.qty, 6), round(p.avg_cost, 6))
                for p in account.positions
            },
            "realized": round(account.realized_pnl, 6),
            "fees": round(account.fees_paid, 6),
            "orders": sim.status().orders,
            "fills": sim.status().fills,
        }
        counts = (len(ex.all_orders()), sim.status().fills)
        sim.stop()
        return summary, counts[0], counts[1]

    first, orders1, fills1 = run_once()
    second, orders2, fills2 = run_once()
    assert first == second, (first, second)
    assert orders1 == orders2 and fills1 == fills2
    return {
        "scenario": "replay_idempotency",
        "ok": True,
        "orders": orders1,
        "fills": fills1,
        "first": first,
        "second": second,
        "identical": first == second,
    }


# 10. load: >=10k ticks and >=1k orders in-process
def load(ticks: int = 10_000, orders: int = 1_000) -> dict[str, Any]:
    if ticks < 10_000 or orders < 1_000:
        raise ValueError("load scenario requires >=10k ticks and >=1k orders")
    cfg = _config(
        capital=10_000_000.0,
        participation_cap=1.0,
        limits=RiskLimits(max_gross=10.0, max_order_notional=100_000.0),
    )
    symbols = [f"T{i}" for i in range(20)]
    ex = _exchange(cfg, symbols)
    store = MemoryStore()
    sim = Simulation(cfg, exchange=ex, store=store, snapshot_interval=500)
    sim.start()
    tracemalloc.start()
    started = time.perf_counter()
    submitted = 0
    every = max(1, ticks // orders)
    for i in range(ticks):
        symbol = symbols[i % len(symbols)]
        tick = _tick(i, symbol=symbol, last=100.0 + (i % 7) * 0.01)
        sim.on_tick(tick)
        if i % every == 0 and submitted < orders:
            sim.submit(
                OrderRequest(
                    client_order_id=f"load-{submitted:06d}",
                    symbol=symbol,
                    side="BUY",
                    qty=1,
                )
            )
            submitted += 1
    elapsed = time.perf_counter() - started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    # Drain open orders so the final account is stable.
    for i in range(ticks, ticks + 100):
        sim.on_tick(_tick(i, symbol=symbols[i % len(symbols)], last=100.0))
    account = sim.account()
    assert submitted == orders, submitted
    assert sim.status().processed_ticks == ticks + 100
    assert sim.status().orders == orders
    assert store.event_count() < orders * 5 + 1000
    assert peak < 120 * 1024 * 1024, f"peak memory {peak / 1e6:.1f} MB"
    out = {
        "scenario": "load",
        "ok": True,
        "ticks": sim.status().processed_ticks,
        "orders": submitted,
        "fills": sim.status().fills,
        "errors": sim.status().errors,
        "elapsed_s": round(elapsed, 3),
        "peak_mb": round(peak / 1e6, 1),
        "events": store.event_count(),
        "open_orders": len(ex.open_orders()),
        "equity": round(account.equity, 2),
    }
    sim.stop()
    return out


SCENARIOS: dict[str, Callable[..., dict[str, Any]]] = {
    "partial_fill": partial_fill,
    "duplicate": duplicate_idempotency,
    "cancel_race": cancel_race,
    "buying_power": buying_power,
    "data_outage": data_outage,
    "restart": restart,
    "kill": kill_switch,
    "replay_idempotency": replay_idempotency,
    "load": load,
}


def run(name: str, **kwargs: Any) -> dict[str, Any]:
    try:
        fn = SCENARIOS[name]
    except KeyError as exc:
        raise ValueError(
            f"unknown scenario {name!r}; choose {sorted(SCENARIOS)}"
        ) from exc
    return fn(**kwargs)


def run_all(*, include_load: bool = True) -> list[dict[str, Any]]:
    out = []
    for name, fn in SCENARIOS.items():
        if name == "load" and not include_load:
            continue
        out.append(fn())
    return out
