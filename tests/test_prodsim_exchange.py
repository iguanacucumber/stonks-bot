"""Simulated exchange + ledger tests: fills, partials, rejects and accounting.

Standalone, no network.  Every clock value is explicit so the whole test file is
deterministic and can run twice with the same result.
"""

from __future__ import annotations

import math
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot.prodsim.contract import (  # noqa: E402
    Fill,
    OrderRequest,
    RiskLimits,
    RunConfig,
    Tick,
    jsonable,
)
from bot.prodsim.exchange import SimulatedExchange  # noqa: E402
from bot.prodsim.ledger import Ledger  # noqa: E402

TOL = 1e-8
USD = "USD"
TZ = timezone.utc


def close(actual: float, expected: float, label: str, tol: float = TOL) -> None:
    assert math.isclose(actual, expected, rel_tol=0.0, abs_tol=tol), (
        f"{label}: {actual!r} != {expected!r}"
    )


def ts(day: int = 2, hour: int = 14, minute: int = 30) -> datetime:
    return datetime(2026, 1, day, hour, minute, tzinfo=TZ)


def tick(
    *,
    symbol: str = "SPY",
    bid: float = 99.0,
    ask: float = 100.0,
    last: float = 99.5,
    volume: float = 1000.0,
    day: int = 2,
    hour: int = 14,
    minute: int = 30,
) -> Tick:
    return Tick(
        ts=ts(day, hour, minute),
        symbol=symbol,
        bid=bid,
        ask=ask,
        last=last,
        volume=volume,
    )


def request(
    client_order_id: str,
    *,
    symbol: str = "SPY",
    side: str = "BUY",
    qty: float = 10.0,
    order_type: str = "MARKET",
    limit_price: float | None = None,
    tif: str = "DAY",
    created_at: datetime | None = None,
) -> OrderRequest:
    return OrderRequest(
        client_order_id=client_order_id,
        symbol=symbol,
        side=side,  # type: ignore[arg-type]
        qty=qty,
        order_type=order_type,  # type: ignore[arg-type]
        limit_price=limit_price,
        tif=tif,  # type: ignore[arg-type]
        created_at=created_at or ts(2, 14, 29),
    )


def make_exchange(**overrides) -> SimulatedExchange:
    values = {
        "capital": 100_000.0,
        "currency": USD,
        "commission_bps": 0.0,
        "min_commission": 0.0,
        "slippage_bps": 0.0,
        "participation_cap": 0.10,
    }
    values.update(overrides)
    return SimulatedExchange(RunConfig(**values), ["SPY"])


def check_account(acct, initial: float, limits: RiskLimits, label: str = "") -> None:
    """Verify the ledger's public accounting identities."""
    prefix = f"{label}: " if label else ""
    position_value = sum(p.market_value for p in acct.positions)
    close(acct.equity, acct.cash + position_value, prefix + "equity")
    close(
        acct.unrealized_pnl,
        sum(p.unrealized_pnl for p in acct.positions),
        prefix + "unrealized",
    )
    assert acct.gross_exposure + TOL >= abs(acct.net_exposure), prefix + "gross/net"
    leverage = max(1.0, limits.max_gross)
    close(acct.margin_used, acct.gross_exposure / leverage, prefix + "margin")
    close(
        acct.buying_power,
        max(0.0, leverage * max(0.0, acct.equity) - acct.gross_exposure),
        prefix + "buying power",
    )
    close(
        acct.equity - initial,
        acct.realized_pnl + acct.unrealized_pnl - acct.fees_paid,
        prefix + "P&L identity",
    )


# ---------------------------------------------------------------- scenarios
def test_partial_fills_across_two_ticks() -> None:
    ex = make_exchange(participation_cap=0.10)
    ex.on_tick(tick(minute=30, bid=99.0, ask=100.0, last=99.5, volume=1000))

    order = ex.submit(request("partial-1", qty=100))
    assert order.status == "ACKNOWLEDGED"
    assert order.filled_qty == 0 and not order.fills
    assert ex.open_orders() and len(ex.open_orders()) == 1
    acct = ex.account()
    close(acct.cash, 100_000.0, "submit leaves cash untouched")
    assert not acct.positions

    fills = ex.on_tick(tick(minute=31, bid=100.0, ask=101.0, last=100.5, volume=500))
    assert len(fills) == 1
    fill = fills[0]
    close(fill.qty, 50.0, "first partial qty")
    close(fill.price, 101.0, "market buy takes the ask")
    assert fill.order_id == order.order_id
    assert fill.liquidity == "TAKER"

    state = ex.order(order.order_id)
    assert state is not None
    assert state.status == "PARTIAL"
    close(state.filled_qty, 50.0, "first partial filled")
    close(state.remaining_qty, 50.0, "first partial remaining")
    close(state.avg_price, 101.0, "first partial avg")

    acct = ex.account()
    close(acct.cash, 94_950.0, "cash after first fill")
    close(acct.equity, 99_975.0, "equity marked at 100.5")
    close(acct.unrealized_pnl, -25.0, "unrealized after first fill")
    close(acct.gross_exposure, 5_025.0, "gross after first fill")
    close(acct.net_exposure, 5_025.0, "net after first fill")
    close(acct.fees_paid, 0.0, "no fees")
    check_account(acct, 100_000.0, ex.limits, "partial")

    fills = ex.on_tick(tick(minute=32, bid=101.0, ask=102.0, last=101.5, volume=500))
    assert len(fills) == 1
    close(fills[0].qty, 50.0, "second partial qty")
    close(fills[0].price, 102.0, "second partial ask")

    state = ex.order(order.order_id)
    assert state is not None and state.status == "FILLED"
    close(state.filled_qty, 100.0, "full filled qty")
    close(state.avg_price, 101.5, "average price")
    assert not ex.open_orders()

    acct = ex.account()
    close(acct.cash, 89_850.0, "cash after both fills")
    close(acct.equity, 100_000.0, "equity at average cost")
    close(acct.unrealized_pnl, 0.0, "zero unrealized")
    close(acct.realized_pnl, 0.0, "zero realized")
    assert len(acct.positions) == 1
    pos = acct.positions[0]
    close(pos.qty, 100.0, "position qty")
    close(pos.avg_cost, 101.5, "position avg cost")
    close(pos.last, 101.5, "position mark")
    close(pos.market_value, 10_150.0, "position market value")
    check_account(acct, 100_000.0, ex.limits, "filled")


def test_limit_fills_respect_limit_and_slippage_cap() -> None:
    ex = make_exchange(slippage_bps=10.0)
    ex.on_tick(tick(minute=30, bid=99.0, ask=100.0, last=99.5, volume=1000))
    order = ex.submit(request("limit-1", qty=10, order_type="LIMIT", limit_price=100.5))
    assert order.status == "ACKNOWLEDGED"

    assert ex.on_tick(tick(minute=31, bid=99.0, ask=101.0, last=100.0)) == []
    state = ex.order(order.order_id)
    assert state is not None and state.status == "ACKNOWLEDGED"
    close(state.filled_qty, 0.0, "non-marketable limit")

    fills = ex.on_tick(tick(minute=32, bid=99.5, ask=100.4, last=100.0, volume=1000))
    assert len(fills) == 1
    close(fills[0].price, 100.5, "slippage capped by limit")
    close(fills[0].slippage, 1.0, "reported slippage cost")
    state = ex.order(order.order_id)
    assert state is not None and state.status == "FILLED"
    close(ex.account().cash, 98_995.0, "limit cash")

    # A sell limit is only marketable at or above the limit.
    ex2 = make_exchange()
    ex2.on_tick(tick(minute=30, bid=100.0, ask=101.0, last=100.5))
    ex2.submit(request("seed", qty=5, created_at=ts(2, 14, 29)))
    ex2.on_tick(tick(minute=31, bid=100.0, ask=101.0, last=100.5, volume=1000))
    sell = ex2.submit(
        request("sell-limit", side="SELL", qty=5, order_type="LIMIT", limit_price=102.0)
    )
    assert sell.status == "ACKNOWLEDGED"
    assert ex2.on_tick(tick(minute=32, bid=101.0, ask=102.0, last=101.5)) == []
    fills = ex2.on_tick(tick(minute=33, bid=102.0, ask=103.0, last=102.5, volume=1000))
    assert len(fills) == 1
    close(fills[0].price, 102.0, "sell limit fill price")
    assert ex2.order(sell.order_id).status == "FILLED"  # type: ignore[union-attr]


def test_day_ioc_gtc_expiry() -> None:
    # IOC: partial fill expires the remainder after its first eligible tick.
    ex = make_exchange(participation_cap=0.10)
    ex.on_tick(tick(minute=30, bid=99.0, ask=100.0, last=99.5, volume=1000))
    ioc = ex.submit(request("ioc-1", qty=100, tif="IOC"))
    fills = ex.on_tick(tick(minute=31, bid=100.0, ask=101.0, last=100.5, volume=500))
    assert len(fills) == 1 and fills[0].qty == 50
    state = ex.order(ioc.order_id)
    assert state is not None
    assert state.status == "EXPIRED"
    close(state.filled_qty, 50.0, "IOC keeps its partial fill")
    assert not ex.open_orders()
    assert (
        ex.on_tick(tick(minute=32, bid=101.0, ask=102.0, last=101.5, volume=10_000))
        == []
    )
    close(ex.account().positions[0].qty, 50.0, "IOC remainder never fills")

    # DAY expires when the session date advances, even if the limit is marketable.
    ex2 = make_exchange()
    ex2.on_tick(tick(day=2, hour=9, minute=30, bid=99.0, ask=100.0, last=99.5))
    day = ex2.submit(request("day-1", qty=10, order_type="LIMIT", limit_price=50.0))
    assert (
        ex2.on_tick(tick(day=3, hour=9, minute=30, bid=99.0, ask=100.0, last=99.5))
        == []
    )
    state = ex2.order(day.order_id)
    assert state is not None
    assert state.status == "EXPIRED"
    close(state.filled_qty, 0.0, "DAY expired unfilled")
    assert not ex2.open_orders()

    # GTC survives the session boundary and fills later.
    ex3 = make_exchange()
    ex3.on_tick(tick(day=2, hour=9, minute=30, bid=99.0, ask=100.0, last=99.5))
    gtc = ex3.submit(
        request(
            "gtc-1",
            qty=10,
            order_type="LIMIT",
            limit_price=99.0,
            tif="GTC",
        )
    )
    assert ex3.on_tick(
        tick(day=3, hour=9, minute=31, bid=97.0, ask=98.0, last=97.5, volume=1000)
    )
    state = ex3.order(gtc.order_id)
    assert state is not None and state.status == "FILLED"
    close(state.avg_price, 98.0, "GTC fills at the marketable ask")


def test_commission_slippage_and_minimum() -> None:
    ex = make_exchange(commission_bps=10.0, min_commission=1.0, slippage_bps=10.0)
    ex.on_tick(tick(minute=30, ask=100.0, bid=99.9, last=99.95, volume=10_000))
    ex.submit(request("cost-1", qty=20))
    fills = ex.on_tick(tick(minute=31, ask=100.0, bid=99.9, last=99.95, volume=10_000))
    assert len(fills) == 1
    fill = fills[0]
    close(fill.price, 100.1, "10 bps adverse slippage")
    close(fill.slippage, 2.0, "slippage cost 0.1 * 20")
    close(fill.commission, 2.002, "max(min, 10 bps of 2002)")
    # A later tick marks the new position at the top-of-book last price.
    ex.on_tick(tick(minute=32, ask=100.0, bid=99.9, last=99.95, volume=10_000))
    acct = ex.account()
    close(acct.cash, 97_995.998, "cash after notional and commission")
    close(acct.fees_paid, 2.002, "fees paid")
    close(acct.unrealized_pnl, -3.0, "unrealized at 99.95")
    check_account(acct, 100_000.0, ex.limits, "commission")

    # The minimum commission binds on a tiny fill.
    ex2 = make_exchange(commission_bps=0.0, min_commission=5.0)
    ex2.on_tick(tick(minute=30, ask=100.0, bid=99.0))
    ex2.submit(request("min-1", qty=1))
    fills = ex2.on_tick(tick(minute=31, ask=100.0, bid=99.0, volume=1000))
    assert len(fills) == 1
    close(fills[0].commission, 5.0, "minimum commission")
    close(ex2.account().fees_paid, 5.0, "minimum commission paid")


def test_rejections_leave_the_book_untouched() -> None:
    ex = make_exchange()
    ex.on_tick(tick(minute=30, ask=100.0, bid=99.0, last=99.5))

    def rejected(req: OrderRequest, needle: str) -> None:
        order = ex.submit(req)
        assert order.status == "REJECTED"
        assert needle in (order.reject_reason or ""), order.reject_reason
        assert not order.fills and order.filled_qty == 0

    rejected(request("unknown", symbol="AAPL"), "unknown symbol")
    rejected(request("nan-qty", qty=float("nan")), "positive and finite")
    rejected(request("short", side="SELL", qty=1), "short selling is disabled")
    first = ex.submit(request("dup-source", qty=1))
    assert first.status == "ACKNOWLEDGED"
    rejected(request("dup-source", qty=1), "duplicate client_order_id")

    try:
        request("zero-qty", qty=0)
        raise AssertionError("contract must reject qty <= 0")
    except ValueError:
        pass
    try:
        request("no-limit", order_type="LIMIT", limit_price=None)
        raise AssertionError("contract must reject LIMIT without price")
    except ValueError:
        pass

    # A cold-start MARKET order is accepted and fills on the first tick, which
    # is the strongest form of "fills only on a subsequent tick".
    ex_cold = make_exchange()
    cold = ex_cold.submit(request("cold-start"))
    assert cold.status == "ACKNOWLEDGED"
    cold_fills = ex_cold.on_tick(
        tick(minute=30, ask=100.0, bid=99.0, last=99.5, volume=1000)
    )
    assert len(cold_fills) == 1 and cold_fills[0].qty == 10.0
    assert ex_cold.order(cold.order_id).status == "FILLED"  # type: ignore[union-attr]

    # Price-dependent risk is deferred for a cold-start order and still fails
    # closed at the first fill rather than overdrawing the account.
    ex_deferred = make_exchange(
        capital=1_000.0,
        limits=RiskLimits(
            max_gross=1.0,
            max_order_notional=100_000.0,
            max_position_pct=0.0,
            allow_short=False,
        ),
    )
    deferred = ex_deferred.submit(request("cold-bp", qty=20))
    assert deferred.status == "ACKNOWLEDGED"
    assert (
        ex_deferred.on_tick(
            tick(minute=30, ask=100.0, bid=99.0, last=99.5, volume=1000)
        )
        == []
    )
    state = ex_deferred.order(deferred.order_id)
    assert state is not None and state.status == "REJECTED"
    assert "insufficient buying power" in (state.reject_reason or "")
    close(ex_deferred.account().cash, 1_000.0, "deferred rejection keeps cash")

    # Max order notional.
    ex_notional = make_exchange(
        limits=RiskLimits(
            max_gross=3.0,
            max_order_notional=500.0,
            max_position_pct=0.0,
            allow_short=False,
        )
    )
    ex_notional.on_tick(tick(minute=30, ask=100.0, bid=99.0))
    order = ex_notional.submit(request("too-big-notional", qty=10))
    assert order.status == "REJECTED"
    assert "max order notional" in (order.reject_reason or "")

    # Insufficient buying power: 1x leverage, 1,000 cash, 2,000 order.
    ex_bp = make_exchange(
        capital=1_000.0,
        limits=RiskLimits(
            max_gross=1.0,
            max_order_notional=100_000.0,
            max_position_pct=0.0,
            allow_short=False,
        ),
    )
    ex_bp.on_tick(tick(minute=30, ask=100.0, bid=99.0))
    order = ex_bp.submit(request("no-bp", qty=20))
    assert order.status == "REJECTED"
    assert "insufficient buying power" in (order.reject_reason or "")

    # Per-symbol concentration limit.
    ex_pos = make_exchange(
        limits=RiskLimits(
            max_gross=3.0,
            max_order_notional=100_000.0,
            max_position_pct=0.10,
            allow_short=False,
        )
    )
    ex_pos.on_tick(tick(minute=30, ask=100.0, bid=99.0))
    order = ex_pos.submit(request("too-concentrated", qty=200))
    assert order.status == "REJECTED"
    assert "position limit" in (order.reject_reason or "")

    # Halt rejects new orders but resume lifts it.
    ex_halt = make_exchange()
    ex_halt.on_tick(tick(minute=30, ask=100.0, bid=99.0))
    ex_halt.halt_symbol("SPY")
    order = ex_halt.submit(request("halted-1"))
    assert order.status == "REJECTED" and "halted" in (order.reject_reason or "")
    ex_halt.resume_symbol("SPY")
    assert ex_halt.submit(request("halted-2")).status == "ACKNOWLEDGED"

    close(ex.account().cash, 100_000.0, "rejections never touch cash")
    assert not ex.account().positions
    close(ex.account().fees_paid, 0.0, "rejections never charge fees")


def test_sell_realizes_pnl_and_account_identities() -> None:
    ex = make_exchange()
    ex.on_tick(tick(minute=30, bid=99.9, ask=100.0, last=100.0))
    buy = ex.submit(request("buy-10", qty=10))
    ex.on_tick(tick(minute=31, bid=99.9, ask=100.0, last=100.0, volume=1000))
    assert ex.order(buy.order_id).status == "FILLED"  # type: ignore[union-attr]
    close(ex.account().cash, 99_000.0, "cash after buy")

    ex.on_tick(tick(minute=32, bid=110.0, ask=110.1, last=110.0))
    sell = ex.submit(request("sell-4", side="SELL", qty=4))
    fills = ex.on_tick(tick(minute=33, bid=110.0, ask=110.1, last=110.0, volume=1000))
    assert len(fills) == 1 and fills[0].price == 110.0
    acct = ex.account()
    close(acct.realized_pnl, 40.0, "realized on 4 shares")
    close(acct.unrealized_pnl, 60.0, "unrealized on 6 shares")
    close(acct.cash, 99_440.0, "cash after partial sell")
    close(acct.equity, 100_100.0, "equity after partial sell")
    pos = acct.positions[0]
    close(pos.qty, 6.0, "remaining qty")
    close(pos.avg_cost, 100.0, "avg cost unchanged")
    check_account(acct, 100_000.0, ex.limits, "partial sell")

    ex.on_tick(tick(minute=34, bid=90.0, ask=90.1, last=90.0))
    ex.submit(request("sell-6", side="SELL", qty=6))
    fills = ex.on_tick(tick(minute=35, bid=90.0, ask=90.1, last=90.0, volume=1000))
    assert len(fills) == 1 and fills[0].price == 90.0
    acct = ex.account()
    close(acct.realized_pnl, -20.0, "total realized")
    close(acct.cash, 99_980.0, "cash after closing")
    close(acct.equity, 99_980.0, "flat equity")
    assert not acct.positions
    check_account(acct, 100_000.0, ex.limits, "closed")
    assert ex.order(sell.order_id).status == "FILLED"  # type: ignore[union-attr]


def test_shorting_when_explicitly_enabled() -> None:
    limits = RiskLimits(
        max_gross=3.0,
        max_order_notional=100_000.0,
        max_position_pct=1.0,
        allow_short=True,
    )
    ex = make_exchange(limits=limits)
    ex.on_tick(tick(minute=30, bid=100.0, ask=100.0, last=100.0))
    short = ex.submit(request("short-1", side="SELL", qty=10))
    fills = ex.on_tick(tick(minute=31, bid=100.0, ask=100.0, last=100.0, volume=1000))
    assert len(fills) == 1 and fills[0].price == 100.0
    acct = ex.account()
    close(acct.cash, 101_000.0, "short proceeds")
    close(acct.equity, 100_000.0, "short equity at entry")
    close(acct.net_exposure, -1_000.0, "net short exposure")
    close(acct.gross_exposure, 1_000.0, "gross short exposure")
    pos = acct.positions[0]
    close(pos.qty, -10.0, "short qty")
    close(pos.avg_cost, 100.0, "short avg")
    check_account(acct, 100_000.0, limits, "short entry")

    ex.on_tick(tick(minute=32, bid=90.0, ask=90.0, last=90.0))
    cover = ex.submit(request("cover-1", qty=10))
    fills = ex.on_tick(tick(minute=33, bid=90.0, ask=90.0, last=90.0, volume=1000))
    assert len(fills) == 1 and fills[0].price == 90.0
    acct = ex.account()
    close(acct.realized_pnl, 100.0, "short realized")
    close(acct.cash, 100_100.0, "cash after cover")
    close(acct.equity, 100_100.0, "equity after cover")
    assert not acct.positions
    assert ex.order(short.order_id).status == "FILLED"  # type: ignore[union-attr]
    assert ex.order(cover.order_id).status == "FILLED"  # type: ignore[union-attr]
    check_account(acct, 100_000.0, limits, "short cover")


def test_exchange_split_and_dividend() -> None:
    ex = make_exchange()
    ex.on_tick(tick(minute=30, bid=99.0, ask=100.0, last=100.0, volume=1000))
    ex.submit(request("ca-buy", qty=10))
    ex.on_tick(tick(minute=31, bid=99.0, ask=100.0, last=100.0, volume=1000))
    acct = ex.account()
    close(acct.cash, 99_000.0, "pre-split cash")
    close(acct.positions[0].qty, 10.0, "pre-split qty")

    ex.split("SPY", 2.0)
    acct = ex.account()
    close(acct.positions[0].qty, 20.0, "post-split qty")
    close(acct.positions[0].avg_cost, 50.0, "post-split avg")
    close(acct.positions[0].last, 50.0, "post-split mark")
    close(acct.equity, 100_000.0, "split leaves equity unchanged")
    check_account(acct, 100_000.0, ex.limits, "exchange split")

    close(ex.dividend("SPY", 0.5), 10.0, "exchange dividend amount")
    acct = ex.account()
    close(acct.cash, 99_010.0, "dividend cash")
    close(acct.realized_pnl, 10.0, "dividend realized")
    check_account(acct, 100_000.0, ex.limits, "exchange dividend")

    ex.mark({"SPY": 60.0})
    acct = ex.account()
    close(acct.unrealized_pnl, 200.0, "post-split mark P&L")
    close(acct.equity, 100_210.0, "post-split mark equity")
    check_account(acct, 100_000.0, ex.limits, "exchange post-split mark")


def test_ledger_corporate_actions_and_identity() -> None:
    ledger = Ledger(10_000.0, currency=USD)
    fill = Fill(
        fill_id="f1",
        order_id="o1",
        ts=ts(),
        symbol="SPY",
        side="BUY",
        qty=10.0,
        price=100.0,
        commission=1.0,
    )
    ledger.apply_fill(fill)
    close(ledger.cash, 8_999.0, "buy cash")
    close(ledger.equity(), 9_999.0, "equity after buy")
    close(ledger.identity_gap(), 0.0, "identity after buy")

    ledger.apply_split("SPY", 2.0)
    pos = ledger.position("SPY")
    assert pos is not None
    close(pos.qty, 20.0, "post-split qty")
    close(pos.avg_cost, 50.0, "post-split avg")
    close(pos.last, 50.0, "post-split mark")
    close(ledger.equity(), 9_999.0, "split leaves equity unchanged")

    amount = ledger.apply_dividend("SPY", 0.5)
    close(amount, 10.0, "dividend cash")
    close(ledger.cash, 9_009.0, "cash after dividend")
    close(ledger.realized_pnl, 10.0, "dividend is realized income")
    close(ledger.identity_gap(), 0.0, "identity after dividend")

    ledger.mark({"SPY": 60.0})
    close(ledger.unrealized_pnl(), 200.0, "unrealized after mark")
    close(ledger.equity(), 10_209.0, "equity after mark")
    close(ledger.identity_gap(), 0.0, "identity after mark")

    try:
        ledger.apply_split("SPY", 0.0)
        raise AssertionError("split ratio 0 must be rejected")
    except ValueError:
        pass


def test_cancel_after_partial_keeps_fills() -> None:
    ex = make_exchange(participation_cap=0.10)
    ex.on_tick(tick(minute=30, bid=99.0, ask=100.0, last=99.5))
    order = ex.submit(request("cancel-partial", qty=100))
    ex.on_tick(tick(minute=31, bid=100.0, ask=101.0, last=100.5, volume=500))
    state = ex.order(order.order_id)
    assert state is not None and state.status == "PARTIAL"
    close(state.filled_qty, 50.0, "partial before cancel")

    canceled = ex.cancel(order.order_id)
    assert canceled.status == "CANCELED"
    close(canceled.filled_qty, 50.0, "cancel preserves filled qty")
    close(canceled.avg_price, 101.0, "cancel preserves avg price")
    assert len(canceled.fills) == 1
    assert not ex.open_orders()

    assert (
        ex.on_tick(tick(minute=32, bid=102.0, ask=103.0, last=102.5, volume=10_000))
        == []
    )
    close(ex.account().positions[0].qty, 50.0, "canceled remainder never fills")
    assert ex.cancel(order.order_id).status == "CANCELED"
    try:
        ex.cancel("missing")
        raise AssertionError("unknown order cancel must raise")
    except KeyError:
        pass


def test_halt_blocks_resting_fill_until_resume() -> None:
    ex = make_exchange()
    ex.on_tick(tick(minute=30, bid=99.0, ask=100.0, last=99.5))
    order = ex.submit(request("halt-resting", qty=10))
    ex.halt_symbol("SPY")
    assert (
        ex.on_tick(tick(minute=31, bid=100.0, ask=101.0, last=100.5, volume=1000)) == []
    )
    state = ex.order(order.order_id)
    assert state is not None and state.status == "ACKNOWLEDGED"
    close(state.filled_qty, 0.0, "no fill while halted")
    ex.resume_symbol("SPY")
    fills = ex.on_tick(tick(minute=32, bid=101.0, ask=102.0, last=101.5, volume=1000))
    assert len(fills) == 1 and fills[0].price == 102.0
    assert ex.order(order.order_id).status == "FILLED"  # type: ignore[union-attr]


def test_bad_tick_sequence_is_rejected() -> None:
    ex = make_exchange()
    first = tick(minute=30, bid=99.0, ask=100.0, last=99.5)
    ex.on_tick(first)
    for bad in (
        first,
        tick(minute=29, bid=99.0, ask=100.0, last=99.5),
    ):
        try:
            ex.on_tick(bad)
            raise AssertionError("duplicate/out-of-order tick must raise")
        except ValueError:
            pass
    # A symbol outside the closed universe is ignored, never filled.
    assert ex.on_tick(tick(symbol="AAPL", minute=31)) == []


def test_replay_is_deterministic_and_identities_hold() -> None:
    def scenario():
        limits = RiskLimits(
            max_gross=2.0,
            max_order_notional=100_000.0,
            max_position_pct=1.0,
            allow_short=True,
        )
        ex = make_exchange(
            capital=50_000.0,
            commission_bps=2.0,
            min_commission=1.0,
            slippage_bps=5.0,
            participation_cap=0.20,
            limits=limits,
        )
        fills: list[Fill] = []
        for i in range(30):
            day = 2 + i // 10
            minute = 30 + i % 10
            mx = 100.0 + (i % 7) - 3.0
            fills.extend(
                ex.on_tick(
                    tick(
                        day=day,
                        minute=minute,
                        bid=mx - 0.05,
                        ask=mx + 0.05,
                        last=mx,
                        volume=2_000,
                    )
                )
            )
            if i in (0, 5, 12):
                side = "SELL" if i == 12 else "BUY"
                order = ex.submit(
                    request(
                        f"det-{i}",
                        side=side,
                        qty=10,
                        created_at=ts(day, 14, minute),
                    )
                )
                assert order.status in ("ACKNOWLEDGED", "REJECTED")
            acct = ex.account()
            check_account(acct, 50_000.0, limits, f"tick {i}")

            expected_cash = 50_000.0
            for fill in fills:
                sign = -1.0 if fill.side == "BUY" else 1.0
                expected_cash += sign * fill.qty * fill.price - fill.commission
            close(acct.cash, expected_cash, f"cash from fills at tick {i}", 1e-6)
            close(
                acct.fees_paid,
                sum(f.commission for f in fills),
                f"fees from fills at tick {i}",
                1e-6,
            )
        return jsonable(ex.account()), [jsonable(f) for f in fills]

    first = scenario()
    second = scenario()
    assert first == second, "same ticks/orders must produce the same account"
    assert first[1], "scenario must generate fills"


def main() -> None:
    tests = [
        value for key, value in sorted(globals().items()) if key.startswith("test_")
    ]
    for fn in tests:
        fn()
        print(f"ok: {fn.__name__}")
    print(f"ok: {len(tests)} prodsim exchange tests")


if __name__ == "__main__":
    main()
