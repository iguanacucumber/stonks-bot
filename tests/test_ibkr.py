"""IBKR paper adapter: lifecycle, idempotency, retries and reconciliation.

Run: uv run python tests/test_ibkr.py

Everything uses an in-process fake transport. No gateway, network, credentials or
real order are touched. The fake follows the Client Portal response shapes used by
`ClientPortalTransport`, so the adapter sees the same order-status fields as in a
paper session.
"""

from __future__ import annotations

import sys
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from bot.live.broker import BrokerError  # noqa: E402
from bot.live.brokers.ibkr import (  # noqa: E402
    IBKRDuplicateOrder,
    IBKRPaperBroker,
    IBKRPaperConfig,
    IBKRTransientError,
    IBKRUnavailable,
)
from bot.live.config import LiveConfig  # noqa: E402
from bot.live.engine import LiveEngine  # noqa: E402
from bot.live.models import (  # noqa: E402
    REJECTED,
    OrderIntent,
    Plan,
    Portfolio,
)


class FakeTransport:
    """Small Client Portal simulator with scripted status responses."""

    def __init__(self, account: str = "DU1234567"):
        self.account = account
        self.authenticated = True
        self.accounts_called = 0
        self.place_calls = 0
        self.cancel_calls: list[str] = []
        self.replace_calls: list[tuple[str, dict]] = []
        self.orders: dict[str, dict] = {}
        self.by_coid: dict[str, str] = {}
        self.status_scripts: dict[str, list[dict]] = {}
        self.conids: dict[str, int] = {}
        self.conid_symbols: dict[int, str] = {}
        self.fail_place_times = 0
        self.fail_after_record = False
        self.positions_rows: list[dict] = []
        self.cash_value = 10_000.0
        self._next_oid = 1
        self._next_conid = 1000

    def authenticate(self) -> None:
        if not self.authenticated:
            raise BrokerError("not authenticated")

    def accounts(self) -> list[str]:
        self.accounts_called += 1
        return [self.account]

    def resolve_conid(self, symbol: str) -> int:
        if symbol not in self.conids:
            self.conids[symbol] = self._next_conid
            self.conid_symbols[self._next_conid] = symbol
            self._next_conid += 1
        return self.conids[symbol]

    def place_order(self, payload: dict) -> dict:
        self.place_calls += 1
        coid = payload["cOID"]
        if self.fail_place_times > 0:
            self.fail_place_times -= 1
            raise IBKRTransientError("simulated gateway down")
        if coid in self.by_coid:
            raise IBKRDuplicateOrder("duplicate clientOrderId")
        oid = f"OID{self._next_oid}"
        self._next_oid += 1
        ticker = self.conid_symbols[int(payload["conid"])]
        row = {
            "order_id": oid,
            "cOID": coid,
            "ticker": ticker,
            "conid": int(payload["conid"]),
            "quantity": float(payload["quantity"]),
            "remainingQuantity": float(payload["quantity"]),
            "status": "Submitted",
            "filledQuantity": 0.0,
            "avgPrice": 0.0,
            "commission": 0.0,
        }
        self.orders[oid] = row
        self.by_coid[coid] = oid
        if self.fail_after_record:
            self.fail_after_record = False
            raise IBKRTransientError("simulated timeout after broker accepted")
        return {"order_id": oid, "order_status": "Submitted"}

    def reply(self, reply_id: str) -> dict:
        return {"order_id": f"OID-reply-{reply_id}", "order_status": "Submitted"}

    def order_status(self, broker_order_id: str) -> dict:
        row = dict(self.orders[broker_order_id])
        script = self.status_scripts.get(broker_order_id)
        if script:
            row.update(script.pop(0))
            self.orders[broker_order_id].update(row)
        elif row["status"] not in ("Filled", "Cancelled", "Rejected", "Inactive"):
            row.update(
                {
                    "status": "Filled",
                    "filledQuantity": row["quantity"],
                    "remainingQuantity": 0.0,
                    "avgPrice": row.get("avgPrice") or 10.0,
                    "commission": row.get("commission") or 0.0,
                }
            )
            self.orders[broker_order_id].update(row)
        return row

    def open_orders(self) -> list[dict]:
        return [
            dict(row)
            for row in self.orders.values()
            if row["status"] not in ("Filled", "Cancelled", "Rejected", "Inactive")
        ]

    def cancel_order(self, broker_order_id: str) -> dict:
        self.cancel_calls.append(broker_order_id)
        self.orders[broker_order_id]["status"] = "Cancelled"
        return {"order_id": broker_order_id, "order_status": "Cancelled"}

    def replace_order(self, broker_order_id: str, payload: dict) -> dict:
        self.replace_calls.append((broker_order_id, payload))
        self.orders[broker_order_id].update(
            {
                "status": "Submitted",
                "quantity": float(payload["quantity"]),
                "remainingQuantity": float(payload["quantity"]),
            }
        )
        return {"order_id": broker_order_id, "order_status": "Submitted"}

    def positions(self) -> list[dict]:
        return list(self.positions_rows)

    def cash(self) -> float:
        return self.cash_value


def _broker(fake: FakeTransport, **kw) -> IBKRPaperBroker:
    cfg = IBKRPaperConfig(
        account_id=fake.account,
        base_url="https://localhost:5000/v1/api",
        poll_interval=0.0,
        max_poll_seconds=1.0,
        max_retries=2,
        backoff_base=0.0,
        **kw,
    )
    return IBKRPaperBroker(fake, cfg, sleeper=lambda _: None)


def _plan(order: OrderIntent, fill_session: str = "2026-01-02") -> Plan:
    return Plan(
        plan_id="plan-1",
        book="monthly",
        market="US",
        signal_session="2025-12-31",
        fill_session=fill_session,
        created_at="2026-01-01T00:00:00+00:00",
        target_weights={order.ticker: 0.1},
        current_weights={},
        orders=[order],
    )


def test_config_is_paper_only_and_env_only() -> None:
    cfg = LiveConfig(broker="ibkr-paper")
    assert cfg.broker == "ibkr-paper"
    try:
        LiveConfig(broker="live")
        raise AssertionError("real-money broker name must be rejected")
    except ValueError as exc:
        assert "unknown broker" in str(exc)
    try:
        IBKRPaperConfig(account_id="U1234567")
        raise AssertionError("non-DU account must be rejected")
    except Exception as exc:
        assert "paper" in str(exc).lower()
    try:
        IBKRPaperConfig.from_env({})
        raise AssertionError("missing account must be rejected")
    except Exception as exc:
        assert "IBKR_PAPER_ACCOUNT" in str(exc)
    cfg = IBKRPaperConfig.from_env(
        {
            "IBKR_PAPER_ACCOUNT": "DU123",
            "IBKR_PAPER_BASE_URL": "https://localhost:5000/v1/api/",
            "IBKR_PAPER_MAX_RETRIES": "4",
            "IBKR_PAPER_VERIFY_SSL": "false",
        }
    )
    assert cfg.account_id == "DU123"
    assert cfg.base_url == "https://localhost:5000/v1/api"
    assert cfg.max_retries == 4 and cfg.verify_ssl is False
    assert (
        LiveConfig(broker="paper").signature()
        != LiveConfig(broker="ibkr-paper").signature()
    )


def test_plan_never_contacts_ibkr() -> None:
    from test_live import _cfg, fixed_fx, synthetic

    panel, mem, _ = synthetic()
    fake = FakeTransport()
    with TemporaryDirectory() as tmp, fixed_fx({"default": 1.10}):
        cfg = _cfg(tmp).replace(broker="ibkr-paper")
        engine = LiveEngine(cfg, broker=_broker(fake))
        result = engine.plan(
            now="2021-12-01T02:00:00+00:00",
            as_of="2021-11-30",
            panel=panel,
            membership=mem,
        )
        assert result.plan is not None
        assert fake.accounts_called == 0
        assert fake.place_calls == 0
        assert not fake.orders


def test_submit_is_idempotent() -> None:
    fake = FakeTransport()
    broker = _broker(fake)
    order = OrderIntent("ord-1", "SMH", "BUY", 10.0, 10.0, "rebalance", "2026-01-02")
    first = broker.submit(order, "2026-01-02", "t")
    second = broker.submit(order, "2026-01-02", "t")
    assert first["broker_order_id"] == second["broker_order_id"]
    assert fake.place_calls == 1
    assert len(fake.orders) == 1


def test_submit_timeout_adopts_existing_order() -> None:
    fake = FakeTransport()
    fake.fail_after_record = True
    broker = _broker(fake)
    order = OrderIntent("ord-2", "QQQ", "BUY", 1.5, 100.0, "rebalance", "2026-01-02")
    rec = broker.submit(order, "2026-01-02", "t")
    assert rec["broker_order_id"] in fake.orders
    # First call recorded the order and timed out; the retry hit duplicate cOID.
    assert fake.place_calls == 2
    assert len(fake.orders) == 1
    assert len(fake.by_coid) == 1


def test_partial_then_full_fill_cycle_and_reconcile() -> None:
    fake = FakeTransport()
    broker = _broker(fake)
    order = OrderIntent("ord-3", "SMH", "BUY", 10.0, 10.0, "rebalance", "2026-01-02")
    plan = _plan(order)
    rec = broker.submit(order, plan.fill_session, "t")
    oid = rec["broker_order_id"]
    fake.status_scripts[oid] = [
        {
            "status": "PartiallyFilled",
            "filledQuantity": 4.0,
            "remainingQuantity": 6.0,
            "avgPrice": 10.1,
            "commission": 1.0,
        },
        {
            "status": "Filled",
            "filledQuantity": 10.0,
            "remainingQuantity": 0.0,
            "avgPrice": 10.2,
            "commission": 2.0,
        },
    ]
    portfolio = Portfolio(cash=1_000.0)
    fills = broker.execute(plan, {}, "t", portfolio)
    assert len(fills) == 2
    assert abs(fills[0].qty - 4.0) < 1e-9
    assert abs(fills[1].qty - 6.0) < 1e-9
    assert abs(portfolio.positions["SMH"].qty - 10.0) < 1e-9
    assert abs(portfolio.positions["SMH"].avg_cost - 10.2) < 1e-9
    assert abs(portfolio.cash - (1_000.0 - 102.0 - 2.0)) < 1e-9
    assert order.status == "FILLED"

    fake.positions_rows = [{"ticker": "SMH", "position": 10.0, "avgCost": 10.2}]
    fake.cash_value = portfolio.cash
    rep = broker.reconcile(portfolio)
    assert rep.ok, rep.issues
    assert abs(rep.broker_cash - portfolio.cash) < 1e-9


def test_reject_returns_no_fill() -> None:
    fake = FakeTransport()
    broker = _broker(fake)
    order = OrderIntent("ord-4", "TLT", "BUY", 2.0, 90.0, "rebalance", "2026-01-02")
    plan = _plan(order)
    rec = broker.submit(order, plan.fill_session, "t")
    fake.status_scripts[rec["broker_order_id"]] = [{"status": "Rejected"}]
    portfolio = Portfolio(cash=1_000.0)
    fills = broker.execute(plan, {}, "t", portfolio)
    assert fills == []
    assert order.status == REJECTED
    assert not portfolio.positions


def test_cancel_and_replace() -> None:
    fake = FakeTransport()
    broker = _broker(fake)
    order = OrderIntent("ord-5", "SPY", "BUY", 2.0, 100.0, "rebalance", "2026-01-02")
    rec = broker.submit(order, "2026-01-02", "t")
    oid = rec["broker_order_id"]
    broker.cancel(oid)
    assert fake.cancel_calls == [oid]
    broker.replace(oid, qty=1.0, limit_price=99.5)
    assert fake.replace_calls
    sent_id, payload = fake.replace_calls[0]
    assert sent_id == oid
    assert payload["orderType"] == "LMT"
    assert payload["quantity"] == 1.0
    assert payload["price"] == 99.5


def test_broker_down_retries_then_fails_closed() -> None:
    fake = FakeTransport()
    fake.fail_place_times = 10
    broker = _broker(fake)
    order = OrderIntent("ord-6", "SMH", "BUY", 1.0, 10.0, "rebalance", "2026-01-02")
    try:
        broker.submit(order, "2026-01-02", "t")
        raise AssertionError("broker down must raise")
    except IBKRUnavailable:
        pass
    assert fake.place_calls == 3  # initial + two retries


def test_stale_position_fails_reconcile() -> None:
    fake = FakeTransport()
    broker = _broker(fake)
    portfolio = Portfolio(cash=1_000.0)
    from bot.live.models import Position

    portfolio.positions["SMH"] = Position(10.0, 10.0, 10.0)
    fake.positions_rows = [{"ticker": "SMH", "position": 9.0, "avgCost": 10.0}]
    fake.cash_value = 1_000.0
    rep = broker.reconcile(portfolio)
    assert not rep.ok
    assert any("SMH" in issue for issue in rep.issues)
    fake.positions_rows = [{"ticker": "SMH", "position": 10.0, "avgCost": 10.0}]
    fake.cash_value = 998.0
    rep = broker.reconcile(portfolio)
    assert not rep.ok
    assert any("cash" in issue for issue in rep.issues)


def test_engine_end_to_end_submit_fill_reconcile() -> None:
    # Reuse the synthetic live panel and FX patch from the live test.
    from test_live import _cfg, fixed_fx, synthetic

    panel, mem, dates = synthetic()
    first, settle = "2021-11-30", "2021-12-01"
    fake = FakeTransport()
    with TemporaryDirectory() as tmp, fixed_fx({"default": 1.10}):
        cfg = _cfg(tmp).replace(broker="ibkr-paper")
        broker = _broker(fake)
        engine = LiveEngine(cfg, broker=broker)
        r1 = engine.run(
            now=f"{first}T22:00:00+00:00",
            as_of=first,
            panel=panel,
            membership=mem,
        )
        assert r1.created and r1.plan is not None and r1.plan.orders
        assert not fake.orders  # first run only planned; nothing submitted
        r2 = engine.run(
            now=f"{settle}T22:00:00+00:00",
            as_of=settle,
            panel=panel,
            membership=mem,
        )
        assert r2.settled_plan is not None and r2.fills
        assert fake.orders
        state = engine.store.load()
        assert state is not None and state.all_fills()
        fake.positions_rows = [
            {"ticker": ticker, "position": pos.qty, "avgCost": pos.avg_cost}
            for ticker, pos in state.portfolio.positions.items()
            if abs(pos.qty) > 1e-12
        ]
        fake.cash_value = state.portfolio.cash
        rep = broker.reconcile(state.portfolio)
        assert rep.ok, rep.issues


def main() -> None:
    checks = [
        test_config_is_paper_only_and_env_only,
        test_plan_never_contacts_ibkr,
        test_submit_is_idempotent,
        test_submit_timeout_adopts_existing_order,
        test_partial_then_full_fill_cycle_and_reconcile,
        test_reject_returns_no_fill,
        test_cancel_and_replace,
        test_broker_down_retries_then_fails_closed,
        test_stale_position_fails_reconcile,
        test_engine_end_to_end_submit_fill_reconcile,
    ]
    for fn in checks:
        fn()
        print(f"ok: {fn.__name__}")
    print(f"ok: {len(checks)} IBKR paper checks")


if __name__ == "__main__":
    main()
