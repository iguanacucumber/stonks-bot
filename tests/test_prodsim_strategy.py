"""Monthly book strategy tests: close targets, next-open orders and guards."""

from __future__ import annotations

import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot.prodsim.contract import (  # noqa: E402
    AccountSnapshot,
    PositionSnapshot,
    RiskLimits,
    Tick,
)
from bot.prodsim.strategy import MonthlyBookStrategy  # noqa: E402

JAN_CLOSE = date(2026, 1, 30)
FEB_OPEN = date(2026, 2, 2)
TS = datetime(2026, 2, 2, 14, 30, tzinfo=timezone.utc)


def _account(equity: float = 100_000.0, cash: float | None = None) -> AccountSnapshot:
    return AccountSnapshot(
        ts=TS,
        currency="USD",
        cash=equity if cash is None else cash,
        buying_power=equity,
        equity=equity,
        gross_exposure=0.0,
        net_exposure=0.0,
        margin_used=0.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
        fees_paid=0.0,
    )


def _positions(**qty: float) -> list[PositionSnapshot]:
    return [
        PositionSnapshot(
            symbol=symbol,
            qty=value,
            avg_cost=100.0,
            last=100.0,
            market_value=value * 100.0,
            unrealized_pnl=0.0,
        )
        for symbol, value in qty.items()
    ]


def _tick(symbol: str, last: float = 100.0, spread: float = 1.0) -> Tick:
    return Tick(
        ts=TS,
        symbol=symbol,
        bid=last - spread / 2,
        ask=last + spread / 2,
        last=last,
        volume=1_000_000,
    )


def test_close_targets_then_next_open_orders() -> None:
    strategy = MonthlyBookStrategy(
        lambda session, closes: {"AAA": 0.5, "BBB": 0.5},
        always_rebalance=True,
        min_order_notional=0.0,
    )
    strategy.on_session_close(JAN_CLOSE, {"AAA": 100.0}, _account())
    assert strategy.pending_session() == JAN_CLOSE
    assert strategy.pending_weights() == {"AAA": 0.5, "BBB": 0.5}
    first = strategy.orders_for_tick(_tick("AAA", 100.0), FEB_OPEN, _account(), [])
    assert len(first) == 1
    order = first[0]
    assert order.side == "BUY" and order.symbol == "AAA"
    assert order.client_order_id == "mb-20260130-AAA-BUY"
    assert order.qty == 497  # floor(50k / 100.5 ask)
    # The same symbol/tick must never emit a second order.
    assert strategy.orders_for_tick(_tick("AAA"), FEB_OPEN, _account(), []) == []
    second = strategy.orders_for_tick(_tick("BBB"), FEB_OPEN, _account(), [])
    assert len(second) == 1 and second[0].symbol == "BBB"
    assert strategy.status()["orders"] == 2


def test_sell_delta_and_full_liquidation() -> None:
    strategy = MonthlyBookStrategy(
        lambda session, closes: {"AAA": 0.10},
        always_rebalance=True,
        min_order_notional=0.0,
    )
    strategy.on_session_close(JAN_CLOSE, {}, _account())
    orders = strategy.orders_for_tick(
        _tick("AAA"), FEB_OPEN, _account(), _positions(AAA=200)
    )
    assert len(orders) == 1
    assert orders[0].side == "SELL"
    assert orders[0].qty == 100  # target 100 shares, held 200
    # A held name absent from the target is liquidated, never shorted.
    strategy2 = MonthlyBookStrategy(
        lambda session, closes: {}, always_rebalance=True, min_order_notional=0.0
    )
    strategy2.on_session_close(JAN_CLOSE, {}, _account())
    sell_all = strategy2.orders_for_tick(
        _tick("AAA"), FEB_OPEN, _account(), _positions(AAA=30)
    )
    assert len(sell_all) == 1 and sell_all[0].side == "SELL"
    assert sell_all[0].qty == 30


def test_min_notional_and_order_cap() -> None:
    strategy = MonthlyBookStrategy(
        lambda session, closes: {"AAA": 0.0001},
        always_rebalance=True,
        min_order_notional=10_000.0,
        max_orders=1,
    )
    strategy.on_session_close(JAN_CLOSE, {}, _account())
    assert strategy.orders_for_tick(_tick("AAA"), FEB_OPEN, _account(), []) == []
    assert strategy.status()["skipped"] == 1
    # A universe with many no-target symbols must not consume the order budget.
    strategy2 = MonthlyBookStrategy(
        lambda session, closes: {"TARGET": 0.5},
        always_rebalance=True,
        min_order_notional=0.0,
        max_orders=1,
    )
    strategy2.on_session_close(JAN_CLOSE, {}, _account())
    for i in range(60):
        assert strategy2.orders_for_tick(_tick(f"N{i}"), FEB_OPEN, _account(), []) == []
    orders = strategy2.orders_for_tick(_tick("TARGET"), FEB_OPEN, _account(), [])
    assert len(orders) == 1


def test_limits_scale_and_cap_targets() -> None:
    weights = {f"T{i}": 0.8 for i in range(10)}
    limits = RiskLimits(max_position_pct=0.5, max_gross=3.0)
    strategy = MonthlyBookStrategy(
        lambda session, closes: weights,
        always_rebalance=True,
        limits=limits,
    )
    strategy.on_session_close(JAN_CLOSE, {}, _account())
    pending = strategy.pending_weights()
    assert all(w <= 0.5 + 1e-9 for w in pending.values())
    assert sum(pending.values()) <= 3.0 + 1e-9


def test_real_month_end_calendar_gate() -> None:
    strategy = MonthlyBookStrategy(lambda session, closes: {"AAA": 1.0})
    strategy.on_session_close(date(2026, 1, 29), {}, _account())
    assert strategy.pending_session() is None
    strategy.on_session_close(JAN_CLOSE, {}, _account())
    assert strategy.pending_session() == JAN_CLOSE


def test_provider_error_is_visible() -> None:
    def boom(session, closes):
        raise RuntimeError("stale cache")

    strategy = MonthlyBookStrategy(boom, always_rebalance=True)
    try:
        strategy.on_session_close(JAN_CLOSE, {}, _account())
    except RuntimeError:
        pass
    else:
        raise AssertionError("provider error was swallowed")
    assert strategy.pending_session() is None
    assert strategy.status()["errors"] == 1


def test_reset() -> None:
    strategy = MonthlyBookStrategy(
        lambda session, closes: {"AAA": 0.5}, always_rebalance=True
    )
    strategy.on_session_close(JAN_CLOSE, {}, _account())
    strategy.orders_for_tick(_tick("AAA"), FEB_OPEN, _account(), [])
    strategy.reset()
    assert strategy.pending_session() is None
    assert strategy.pending_weights() == {}
    assert strategy.status()["orders"] == 0


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok: {name}")
    print("ok: prodsim strategy")


if __name__ == "__main__":
    main()
