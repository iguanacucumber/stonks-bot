"""Contract-level checks for the v12 production simulation."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot.prodsim.contract import (
    AccountSnapshot,
    Fill,
    OrderRequest,
    OrderState,
    RiskLimits,
    RunConfig,
    Tick,
    clamp_qty,
    iso,
    jsonable,
    order_event,
    validate_request,
)


def test_tick_validation() -> None:
    ts = datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc)
    tick = Tick(ts=ts, symbol="SPY", bid=100.0, ask=100.01, last=100.0, volume=1_000)
    assert tick.symbol == "SPY"
    for kwargs in (
        {"bid": 0.0},
        {"ask": 0.0},
        {"last": 0.0},
        {"bid": 101.0, "ask": 100.0},
    ):
        vals = dict(ts=ts, symbol="SPY", bid=100.0, ask=100.01, last=100.0)
        vals.update(kwargs)
        try:
            Tick(**vals)
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected ValueError for {kwargs}")


def test_order_request_validation() -> None:
    base = dict(client_order_id="c1", symbol="SPY", side="BUY", qty=10)
    assert OrderRequest(**base).order_type == "MARKET"
    for extra in (
        {"client_order_id": ""},
        {"qty": 0},
        {"qty": -1},
        {"side": "HOLD"},
        {"order_type": "STOP"},
        {"order_type": "LIMIT"},
        {"order_type": "LIMIT", "limit_price": 0},
        {"tif": "FOK"},
    ):
        vals = dict(base)
        vals.update(extra)
        try:
            OrderRequest(**vals)
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected ValueError for {extra}")


def test_risk_validation_and_lots() -> None:
    limits = RiskLimits()
    req = OrderRequest(client_order_id="c1", symbol="SPY", side="BUY", qty=10)
    assert validate_request(req, limits) is None
    assert (
        validate_request(
            OrderRequest(client_order_id="s1", symbol="SPY", side="SELL", qty=1), limits
        )
        == "short selling is disabled"
    )
    limit = OrderRequest(
        client_order_id="l1",
        symbol="SPY",
        side="BUY",
        qty=1,
        order_type="LIMIT",
        limit_price=99.0,
    )
    assert validate_request(limit, limits) is None
    assert clamp_qty(10.9, 1) == 10.0
    assert clamp_qty(10.9, 10) == 10.0


def test_state_and_event_serialization() -> None:
    ts = datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc)
    fill = Fill(
        fill_id="f1",
        order_id="o1",
        ts=ts,
        symbol="SPY",
        side="BUY",
        qty=10,
        price=100.0,
        commission=0.01,
    )
    order = OrderState(
        order_id="o1",
        client_order_id="c1",
        symbol="SPY",
        side="BUY",
        qty=10,
        status="FILLED",
        filled_qty=10,
        avg_price=100.0,
        created_at=ts,
        updated_at=ts,
        fills=[fill],
    )
    assert order.remaining_qty == 0 and not order.is_open
    payload = order_event(order)
    assert payload["status"] == "FILLED" and payload["updated_at"] == iso(ts)
    assert jsonable(order)["fills"][0]["fill_id"] == "f1"
    account = AccountSnapshot(
        ts=ts,
        currency="USD",
        cash=1_000.0,
        buying_power=1_000.0,
        equity=1_000.0,
        gross_exposure=0.0,
        net_exposure=0.0,
        margin_used=0.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
        fees_paid=0.0,
    )
    assert jsonable(account)["cash"] == 1_000.0


def test_run_config_validation() -> None:
    assert RunConfig().mode == "replay"
    for kwargs in ({"capital": 0}, {"speed": 0}, {"mode": "real-money"}):
        try:
            RunConfig(**kwargs)
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected ValueError for {kwargs}")


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok: {name}")
    print("ok: prodsim contract")


if __name__ == "__main__":
    main()
