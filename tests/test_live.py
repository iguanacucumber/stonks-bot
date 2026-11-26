"""Live autopilot: US/TECH config, FX ledger fields, idempotency and rails."""

from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import paper_daily as paper_cli  # noqa: E402
from bot.live import calendar, reconcile, report  # noqa: E402
from bot.live.broker import Bar, PaperBroker  # noqa: E402
from bot.live.config import (  # noqa: E402
    BEST_EVIDENCE_MARKET,
    BOOK_OVERRIDES,
    LiveConfig,
    load_config,
)
from bot.live.engine import LiveEngine  # noqa: E402
from bot.live.models import (  # noqa: E402
    PARTIAL,
    PENDING,
    OrderIntent,
    Plan,
    Portfolio,
)  # noqa: E402
from bot.live.signal import SnapshotError, build_snapshot, eur_fx, levered_targets  # noqa: E402
from bot.live.rails import check_rails, desired_targets, evaluate_risk  # noqa: E402
from bot.live.state import LiveState, StateStore  # noqa: E402

TOL = 1e-9


def _cfg(tmp: str, **kw) -> LiveConfig:
    base = dict(
        book="monthly",
        market="US",
        start="2020-01-01",
        capital=10_000.0,
        leverage=1.0,
        trend_ma=0,
        financing_bps=0.0,
        dd_stop=0.0,
        max_gross=2.0,
        max_turnover=2.0,
        slippage_bps=0.0,
        commission_bps=0.0,
        min_commission=0.0,
        max_participation=1.0,
        state_dir=tmp,
    )
    base.update(kw)
    return LiveConfig(**base)


def synthetic(days: int = 523, n: int = 12, seed: int = 3):
    """A deterministic panel with a weak core so the momentum sleeve trades."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2020-01-01", periods=days)
    rows = []
    for k in range(n):
        ticker = f"T{k:02d}"
        px = 50 * np.exp(np.cumsum(rng.normal(0.0008, 0.015, days)))
        vol = rng.integers(2_000_000, 5_000_000, days)
        for d, p, v in zip(dates, px, vol):
            rows.append((d, ticker, p, p, 0.0, float(v)))
    for ticker, drift, base in (("SPY", 0.0002, 100.0), ("SMH", 0.0001, 80.0)):
        px = base * np.exp(np.cumsum(rng.normal(drift, 0.008, days)))
        for d, p in zip(dates, px):
            rows.append((d, ticker, p, p, 0.0, 10_000_000))
    panel = pd.DataFrame(
        rows, columns=["date", "ticker", "adjclose", "open", "high", "volume"]
    )
    panel["low"] = panel["adjclose"]
    panel["close"] = panel["adjclose"]
    mem = pd.DataFrame(
        {"date": [dates[0]] * n, "ticker": [f"T{k:02d}" for k in range(n)]}
    )
    return panel, mem, dates


@contextmanager
def fixed_fx(rate_by_session: dict[str, float] | None = None, missing: bool = False):
    """Patch the cached FX reader so the test never needs network or cache."""
    rates = rate_by_session or {}

    def close_on(ticker: str, session: pd.Timestamp):
        if missing or ticker != "EURUSD=X":
            return None, None
        key = str(pd.Timestamp(session).date())
        return rates.get(key, rates.get("default", 1.10)), pd.Timestamp(session)

    with patch("bot.live.signal._close_on", side_effect=close_on):
        yield


def extra_tests() -> None:
    """Operations coverage added for the production loop (no network)."""
    from dataclasses import replace

    from bot import backtest, strategy
    from bot.live.engine import resolve_session

    panel, mem, dates = synthetic()
    s1, s2 = "2021-11-30", "2021-12-01"

    # Reuse check: the live target is exactly the backtest's post-fill book.
    with TemporaryDirectory() as tmp, fixed_fx({"default": 1.10}):
        cfg = _cfg(tmp)
        snap = build_snapshot(cfg, s1, panel=panel, membership=mem)
        live = levered_targets(snap)
        scfg = replace(cfg.strategy_config(), cost_bps=0.0)
        mkt = strategy.Market(panel, mem, scfg)
        res = backtest.run(mkt)
        shadow = {
            t: float(w)
            for t, w in res.weights_open.loc[pd.Timestamp(s2)].items()
            if pd.notna(w) and float(w) > 1e-9
        }
        assert set(live) == set(shadow), (live, shadow)
        for t in live:
            assert abs(live[t] - shadow[t]) < 1e-6, (t, live[t], shadow[t])

    # Dry-run must not create state; crash between state and ledger must repair.
    with TemporaryDirectory() as tmp, fixed_fx({"default": 1.10}):
        cfg = _cfg(tmp)
        engine = LiveEngine(cfg)
        dry = engine.plan(
            now="2021-12-01T02:00:00+00:00", as_of=s1, panel=panel, membership=mem
        )
        assert not dry.blocked and dry.orders
        assert not Path(cfg.state_path).exists() and not Path(cfg.ledger_path).exists()

        r1 = engine.run(
            now="2021-12-01T02:00:00+00:00", as_of=s1, panel=panel, membership=mem
        )
        assert r1.created and r1.persisted and r1.orders
        Path(cfg.ledger_path).unlink()
        r1b = engine.run(
            now="2021-12-01T03:00:00+00:00", as_of=s1, panel=panel, membership=mem
        )
        assert not r1b.created and r1b.plan.plan_id == r1.plan.plan_id
        assert len(engine.store.ledger_rows()) == 1

        r2 = engine.run(
            now="2021-12-02T02:00:00+00:00", as_of=s2, panel=panel, membership=mem
        )
        assert r2.settled_plan is not None and r2.fills
        Path(cfg.ledger_path).unlink()
        state = engine.store.load()
        rep = reconcile.reconcile(cfg, state, engine.store, repair_ledger=True)
        assert rep.ok, rep.issues
        assert len(engine.store.ledger_rows()) == 2

        state = engine.store.load()
        state.portfolio.cash += 1.0
        engine.store.save(state)
        bad = reconcile.reconcile(cfg, state, engine.store)
        assert not bad.ok and any("cash:" in x for x in bad.issues), bad.issues

    # Gross, turnover and duplicate-order rails all fail closed.
    with TemporaryDirectory() as tmp, fixed_fx({"default": 1.10}):
        cfg = _cfg(tmp, max_gross=0.5)
        res = LiveEngine(cfg).plan(
            now="2021-12-01T02:00:00+00:00", as_of=s1, panel=panel, membership=mem
        )
        assert res.blocked and "max_gross" in (res.blocked_reason or "")
        assert res.orders == []
    with TemporaryDirectory() as tmp, fixed_fx({"default": 1.10}):
        cfg = _cfg(tmp, max_turnover=0.05)
        res = LiveEngine(cfg).plan(
            now="2021-12-01T02:00:00+00:00", as_of=s1, panel=panel, membership=mem
        )
        assert res.blocked and "max_turnover" in (res.blocked_reason or "")
    with TemporaryDirectory() as tmp, fixed_fx({"default": 1.10}):
        cfg = _cfg(tmp)
        snap = build_snapshot(cfg, s1, panel=panel, membership=mem)
        issues = check_rails(
            cfg,
            {"AAA": 0.1},
            [OrderIntent("dup", "AAA", "BUY", 1.0, 10.0, "rebalance", s2)],
            Portfolio(cash=1000.0),
            {"AAA": 10.0},
            1000.0,
            {"dup"},
            set(),
            s2,
        )
        assert any("duplicate order id" in x for x in issues), issues
        assert snap.session is not None

    # Drawdown breaker scales the current book and recovers only past hysteresis.
    with TemporaryDirectory() as tmp, fixed_fx({"default": 1.10}):
        cfg = _cfg(tmp, dd_stop=0.30, dd_scale=0.5)
        snap = build_snapshot(cfg, s1, panel=panel, membership=mem)
        state = LiveState.new(cfg)
        state.peak_equity = 20_000.0
        state.portfolio.cash = 10_000.0
        risk = evaluate_risk(cfg, state, snap, equity=10_000.0)
        assert risk.risk_off and risk.risk_reason == "dd"
        target, reason = desired_targets(
            cfg,
            state,
            snap,
            risk,
            strategy_target={},
            current_weights={"AAA": 0.5},
            due=False,
            initialized=False,
        )
        assert reason == "risk_off" and abs(target["AAA"] - 0.25) < TOL

    # Stale held symbol blocks a run and leaves no pending order.
    with TemporaryDirectory() as tmp, fixed_fx({"default": 1.10}):
        cfg = _cfg(tmp)
        store = StateStore(cfg)
        state = LiveState.new(cfg, now="2021-12-01T00:00:00+00:00")
        from bot.live.models import Position

        state.portfolio.cash = 9_000.0
        state.portfolio.positions["ZZZ"] = Position(10.0, 100.0, 100.0)
        state.target_weights = {"ZZZ": 0.1}
        store.save(state)
        res = LiveEngine(cfg).plan(
            now="2021-12-02T02:00:00+00:00", as_of=s2, panel=panel, membership=mem
        )
        assert res.blocked and "cannot mark book" in (res.blocked_reason or "")
        assert res.orders == []

    # Holiday CLI exits non-zero without creating state.
    import live as live_cli

    with TemporaryDirectory() as tmp:
        rc = live_cli.main(
            [
                "plan",
                "--book",
                "monthly",
                "--market",
                "TECH",
                "--state-dir",
                tmp,
                "--as-of",
                "2026-11-26",
                "--now",
                "2026-11-27T03:00:00+00:00",
            ]
        )
        assert rc == 2
        assert not list(Path(tmp).glob("*.json"))
    _ = resolve_session("2026-11-27T03:00:00+00:00", None)


def main() -> None:
    assert BOOK_OVERRIDES == paper_cli.BOOKS, "live book drifted from paper book"
    assert BEST_EVIDENCE_MARKET == "US"

    # Config: new live plans target US by default, any market stays explicit.
    cfg = LiveConfig()
    assert cfg.market == "US"
    assert cfg.native_currency == "USD" and cfg.currency == "USD"
    assert cfg.reporting_currency == "EUR"
    assert cfg.market_label == "S&P 500"
    assert "monthly" in cfg.book_label
    assert cfg.recommended_market == "US"
    assert "US monthly is the best-evidence" in cfg.recommendation
    assert cfg.state_path.name == "monthly_us.json"
    tech = LiveConfig(market="TECH")
    assert tech.native_currency == "USD"
    assert tech.state_path.name == "monthly_tech.json"
    assert "validation" in tech.recommendation
    assert LiveConfig(market="FR").native_currency == "EUR"
    assert LiveConfig(market="GB").native_currency == "GBP"
    try:
        LiveConfig(currency="EUR", market="US")
        raise AssertionError("a mismatched native currency must be rejected")
    except ValueError as exc:
        assert "does not match US" in str(exc)
    try:
        LiveConfig(market="NOPE")
        raise AssertionError("an unknown market must be rejected")
    except ValueError as exc:
        assert "unknown market" in str(exc)

    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "live.toml"
        path.write_text('book = "tax"\nmarket = "US"\ncapital = 5000\n')
        loaded = load_config(path)
        assert loaded.book == "tax" and loaded.market == "US"
        assert load_config(path, market="TECH").market == "TECH"

    # FX is stored as EUR per native unit; missing FX is reported, never invented.
    with fixed_fx({"default": 1.10}):
        us = eur_fx(LiveConfig(market="US"), pd.Timestamp("2021-12-31"))
        assert abs(float(us["rate"]) - 1.0 / 1.10) < TOL
        assert us["fresh"] and us["date"] == "2021-12-31"
        fr = eur_fx(LiveConfig(market="FR"), pd.Timestamp("2021-12-31"))
        assert fr["rate"] == 1.0 and fr["native"] == "EUR"

    def gbp_close(ticker: str, session: pd.Timestamp):
        return (
            {"EURUSD=X": 1.10, "GBPUSD=X": 1.30}.get(ticker),
            pd.Timestamp(session),
        )

    with patch("bot.live.signal._close_on", side_effect=gbp_close):
        gb = eur_fx(LiveConfig(market="GB"), pd.Timestamp("2021-12-31"))
    assert abs(float(gb["rate"]) - 1.30 / 1.10) < TOL
    with fixed_fx(missing=True):
        missing = eur_fx(LiveConfig(market="US"), pd.Timestamp("2021-12-31"))
        assert missing["rate"] is None and not missing["fresh"]

    panel, mem, dates = synthetic()
    first, settle, later = "2021-11-30", "2021-12-01", "2021-12-03"
    with (
        TemporaryDirectory() as tmp,
        fixed_fx({first: 1.10, settle: 1.10, later: 1.20}),
    ):
        cfg = LiveConfig(
            book="monthly",
            market="US",
            start=str(dates[0].date()),
            capital=10_000.0,
            trend_ma=0,
            state_dir=tmp,
        )
        engine = LiveEngine(cfg)

        # A holiday is a hard stop before any state is touched.
        try:
            build_snapshot(cfg, "2021-12-25", panel=panel, membership=mem)
            raise AssertionError("Christmas must not build a live snapshot")
        except SnapshotError as exc:
            assert "market closed" in str(exc)
        assert not calendar.is_session("2021-12-25")

        # Stale data also fails closed: the benchmark has no bar on the session.
        try:
            build_snapshot(
                cfg,
                "2021-12-31",
                panel=panel[panel["date"] < pd.Timestamp("2021-12-20")],
                membership=mem,
            )
            raise AssertionError("stale panel must not build a live snapshot")
        except SnapshotError as exc:
            assert "stale data" in str(exc)

        # First paper session: plan targets, write state + one ledger row.
        r1 = engine.run(
            now=f"{first}T22:00:00+00:00",
            as_of=first,
            panel=panel,
            membership=mem,
        )
        assert not r1.blocked and r1.created and r1.persisted
        assert r1.orders, "the synthetic sleeve should produce orders"
        assert abs(float(r1.fx["rate"]) - 1.0 / 1.10) < TOL
        assert r1.plan.currency == "USD"
        assert abs(float(r1.plan.equity_eur) - r1.equity / 1.10) < 1e-6
        assert r1.plan.fx_rate is not None and r1.plan.fx_date == first
        state = engine.store.load()
        assert state.pending_plan is not None
        plan_id = state.pending_plan.plan_id
        assert state.pending_plan.fill_session == settle

        # Report/JSON label the book, market and both currencies.
        text = report.format_report(r1, cfg, mode="run", forward_gate="WAIT 0/5")
        assert "book=monthly" in text and "market=US (S&P 500)" in text
        assert "native=USD" in text and "reporting=EUR" in text
        assert "US monthly is the best-evidence live book" in text
        assert "TECH monthly remains the forward validation" in text
        payload = json.loads(report.plan_json(r1, cfg))
        assert payload["currency"] == "USD"
        assert payload["reporting_currency"] == "EUR"
        assert payload["config"]["market"] == "US"
        assert payload["config"]["market_label"] == "S&P 500"
        assert payload["config"]["recommended_market"] == "US"
        assert payload["equity_eur"] is not None
        assert payload["plan"]["currency"] == "USD"
        assert payload["plan"]["equity_eur"] is not None
        tech_text = report.format_report(r1, tech, mode="plan")
        assert "market=TECH (NASDAQ tech)" in tech_text
        assert "validation/research" in tech_text

        # Three sessions later the pending plan fills at the fill-session FX, not
        # at today's FX. That distinction is what lets the EUR ledger reconcile.
        r2 = engine.run(
            now=f"{later}T22:00:00+00:00",
            as_of=later,
            panel=panel,
            membership=mem,
        )
        assert not r2.blocked and r2.settled_plan is not None
        assert r2.settled_plan.plan_id == plan_id
        assert r2.fills and all(f.fx_rate == r2.fills[0].fx_rate for f in r2.fills)
        assert abs(float(r2.fills[0].fx_rate) - 1.0 / 1.10) < TOL
        assert r2.fills[0].fx_date == settle
        for fill in r2.fills:
            assert abs(float(fill.notional_eur) - fill.notional / 1.10) < 1e-6
            assert abs(float(fill.commission_eur) - fill.commission / 1.10) < 1e-6

        state = engine.store.load()
        assert state.currency == "USD"
        assert state.last_fx_date == later
        assert abs(float(state.last_fx_rate) - 1.0 / 1.20) < 1e-6
        assert abs(state.last_equity_eur - state.peak_equity / 1.20) < 1e-5
        assert abs(state.last_cash_eur - state.portfolio.cash / 1.20) < 1e-5
        assert abs(state.initial_capital_eur - 10_000.0 / 1.10) < 1e-5
        fill_fees_eur = sum(
            float(f.commission_eur) for plan in state.plans.values() for f in plan.fills
        )
        assert abs(state.fees_paid_eur - fill_fees_eur) < 1e-9
        assert state.fees_paid_eur > 0

        rows = engine.store.ledger_rows()
        assert len(rows) == 2
        current = [r for r in rows if r["session"] == later][0]
        assert current["currency"] == "USD"
        assert abs(float(current["fx_rate"]) - 1.0 / 1.20) < 1e-6
        assert current["fx_date"] == later
        assert abs(float(current["equity_eur"]) - state.last_equity_eur) < 1e-5
        assert abs(float(current["cash_eur"]) - state.last_cash_eur) < 1e-5
        assert "fees_eur" in current and float(current["fees_eur"]) > 0
        assert "realized_pnl_eur" in current
        ledger_fills = json.loads(current["fills"])
        assert ledger_fills and all(f["fx_rate"] is not None for f in ledger_fills)
        assert all(f["notional_eur"] is not None for f in ledger_fills)

        # Rerunning the same completed session is a no-op, including the ledger.
        r3 = engine.run(
            now=f"{later}T23:00:00+00:00",
            as_of=later,
            panel=panel,
            membership=mem,
        )
        assert r3.created is False and r3.plan.plan_id == r2.plan.plan_id
        assert len(engine.store.ledger_rows()) == 2

        rep = reconcile.reconcile(cfg, state, engine.store)
        assert rep.ok, rep.issues
        assert rep.metrics["last_fx_rate"] == state.last_fx_rate
        assert rep.metrics["fees_paid_eur"] > 0

    # Partial fills are explicit: a participation cap is never rounded up.
    plan = Plan(
        plan_id="partial",
        book="monthly",
        market="US",
        signal_session="2021-12-01",
        fill_session="2021-12-02",
        created_at="2021-12-01T22:00:00+00:00",
        target_weights={"AAA": 1.0},
        current_weights={},
        orders=[
            OrderIntent(
                order_id="o1",
                ticker="AAA",
                side="BUY",
                qty=100.0,
                ref_price=10.0,
                reason="initial",
                fill_session="2021-12-02",
                status=PENDING,
            )
        ],
    )
    broker = PaperBroker(
        slippage_bps=0.0,
        commission_bps=0.0,
        min_commission=0.0,
        max_participation=0.10,
    )
    portfolio = Portfolio(cash=100_000.0)
    fills = broker.execute(
        plan,
        {"AAA": Bar(open=10.0, close=10.0, volume=100.0)},
        "2021-12-02T22:00:00+00:00",
        portfolio,
    )
    assert len(fills) == 1 and abs(fills[0].qty - 10.0) < TOL
    assert plan.orders[0].status == PARTIAL
    assert abs(portfolio.positions["AAA"].qty - 10.0) < TOL

    extra_tests()

    print(
        "ok: live default US, TECH override, EUR FX state/ledger, "
        "idempotency, holiday/stale gates, partial fills"
    )


if __name__ == "__main__":
    main()
