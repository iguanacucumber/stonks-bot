"""Forward daily loop tests: idempotency, gates, separation, no network.

Synthetic panel + temp state/results. The legacy paper logger and report are
injected so the test never invokes a subprocess, reads the real cache or writes
``results/``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import forward_daily as fd  # noqa: E402
from bot.live.config import LiveConfig  # noqa: E402
from bot.live.state import StateStore  # noqa: E402


def synthetic(days: int = 523, n: int = 12, seed: int = 5):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2020-01-01", periods=days)
    rows = []
    for k in range(n):
        ticker = f"T{k:02d}"
        px = 50 * np.exp(np.cumsum(rng.normal(0.0007, 0.014, days)))
        vol = rng.integers(2_000_000, 5_000_000, days)
        for d, p, v in zip(dates, px, vol):
            rows.append((d, ticker, p, p, 0.0, float(v)))
    for ticker, base, drift in (("SPY", 100.0, 0.0002), ("SMH", 80.0, 0.0001)):
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


def _fake_paper(book, market, session, now, results_dir):
    path = results_dir / (
        "paper_daily.csv" if book == "monthly" else "paper_daily_tax.csv"
    )
    results_dir.mkdir(parents=True, exist_ok=True)
    row = {
        "time": now,
        "market": market,
        "signal_date": session,
        "quote_date": session,
        "close_valid": True,
        "data_complete": True,
        "reconciled": False,
    }
    if path.exists():
        df = pd.read_csv(path)
        df = df[~((df["market"] == market) & (df["quote_date"].astype(str) == session))]
        out = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    else:
        out = pd.DataFrame([row])
    out.to_csv(path, index=False)


def _base(tmp: Path) -> LiveConfig:
    return LiveConfig(
        book="monthly",
        market="US",
        capital=10_000.0,
        state_dir=str(tmp / "state" / "live"),
    )


def test_dry_run_and_idempotent_paper() -> None:
    panel, mem, _ = synthetic()
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        cfg = _base(root)
        results = root / "results"
        calls = []

        def runner(book, market, session, now, results_dir):
            calls.append((book, market, session))
            _fake_paper(book, market, session, now, results_dir)

        report = fd.run_forward(
            cfg,
            [("monthly", "US"), ("monthly", "TECH")],
            paper=False,
            as_of="2021-11-30",
            now="2021-12-01T02:00:00+00:00",
            panel=panel,
            membership=mem,
            results_dir=results,
            paper_runner=runner,
            cache_check=lambda s: (True, "test"),
            write_status=False,
        )
        assert report.ok, report.errors
        assert calls == []
        assert not (root / "state").exists()
        assert not results.exists()

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        cfg = _base(root)
        results = root / "results"
        calls = []

        def runner(book, market, session, now, results_dir):
            calls.append((book, market, session))
            _fake_paper(book, market, session, now, results_dir)

        report = fd.run_forward(
            cfg,
            [("monthly", "US"), ("monthly", "TECH")],
            paper=True,
            now="2021-12-01T02:00:00+00:00",
            panel=panel,
            membership=mem,
            results_dir=results,
            paper_runner=runner,
            cache_check=lambda s: (True, "test"),
            write_status=True,
        )
        assert report.ok, report.errors
        assert len(calls) == 2
        us = StateStore(
            LiveConfig(
                book="monthly", market="US", state_dir=str(root / "state" / "live")
            )
        )
        tech = StateStore(
            LiveConfig(
                book="monthly", market="TECH", state_dir=str(root / "state" / "live")
            )
        )
        assert us.path != tech.path
        assert len(us.ledger_rows()) == 1
        assert len(tech.ledger_rows()) == 1
        assert fd.paper_dates("monthly", "US", results) == ["2021-11-30"]
        assert fd.paper_dates("monthly", "TECH", results) == ["2021-11-30"]
        status = (results / "forward_status.md").read_text()
        assert "monthly" in status and "TECH" in status and "US" in status

        # Exact rerun is a no-op: no new state row, no second legacy paper call.
        again = fd.run_forward(
            cfg,
            [("monthly", "US"), ("monthly", "TECH")],
            paper=True,
            now="2021-12-01T03:00:00+00:00",
            panel=panel,
            membership=mem,
            results_dir=results,
            paper_runner=runner,
            cache_check=lambda s: (True, "test"),
            write_status=True,
        )
        assert again.ok, again.errors
        assert len(calls) == 2
        assert len(us.ledger_rows()) == 1
        assert len(tech.ledger_rows()) == 1


def test_stale_holiday_and_health_fail() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        stale = fd.run_forward(
            _base(root),
            [("monthly", "US")],
            paper=False,
            now="2026-11-25T22:00:00+00:00",
            cache_check=lambda s: (False, "stale data: forced test"),
            write_status=False,
        )
        assert not stale.ok
        assert "stale data" in stale.errors[0]
        assert not (root / "state").exists()

        holiday = fd.run_forward(
            _base(root),
            [("monthly", "US")],
            paper=False,
            as_of="2021-12-25",
            now="2021-12-28T02:00:00+00:00",
            write_status=False,
        )
        assert not holiday.ok
        assert "market closed" in holiday.errors[0]

    panel, mem, _ = synthetic()
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        results = root / "results"

        def broken(book, market, session, now, results_dir):
            raise RuntimeError("legacy logger down")

        failed = fd.run_forward(
            _base(root),
            [("monthly", "US")],
            paper=True,
            now="2021-12-01T02:00:00+00:00",
            panel=panel,
            membership=mem,
            results_dir=results,
            paper_runner=broken,
            cache_check=lambda s: (True, "test"),
            write_status=False,
        )
        assert not failed.ok
        assert "legacy logger down" in (failed.books[0].error or "")


def test_gate_and_separate_books() -> None:
    with TemporaryDirectory() as tmp:
        results = Path(tmp) / "results"
        results.mkdir()
        pd.DataFrame(
            {
                "book": ["monthly", "monthly", "monthly"],
                "market": ["TECH", "TECH", "US"],
                "date": ["2026-11-24", "2026-11-25", "2026-11-25"],
                "verified": [True, False, True],
            }
        ).to_csv(results / "paper_audit.csv", index=False)
        assert fd.gate_text("monthly", "TECH", results) == "WAIT 1/5 verified"
        assert fd.gate_text("monthly", "US", results) == "WAIT 1/5 verified"
        assert fd.gate_text("monthly", "TECH", results, target=1) == "PASS 1/1 verified"
        assert fd.gate_text("tax", "TECH", results) == "WAIT 0/5 verified"


def test_refresh_is_opt_in_and_injected() -> None:
    panel, mem, _ = synthetic()
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        seen = []
        report = fd.run_forward(
            _base(root),
            [("monthly", "US"), ("monthly", "TECH")],
            paper=True,
            refresh=True,
            now="2021-12-01T02:00:00+00:00",
            panel=panel,
            membership=mem,
            results_dir=root / "results",
            paper_runner=lambda *a: _fake_paper(*a),
            report_runner=lambda *a: None,
            refresh_fn=lambda markets: seen.extend(markets),
            cache_check=lambda s: (True, "test"),
            write_status=False,
        )
        assert report.ok, report.errors
        assert seen == ["US", "TECH"]


def test_half_day_and_weekend_guard() -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from bot.live import calendar
    from paper_daily import is_close_valid

    session = pd.Timestamp("2026-11-27")  # day after Thanksgiving, 13:00 ET close
    assert calendar.is_half_day(session)
    assert calendar.close_hour(session) == 13

    def blocked(now: str) -> bool:
        try:
            fd._require_close_session(now, session, True, None)
            return False
        except fd.ForwardBlocked:
            return True

    # 12:00/13:29 ET are before the half-day close + 30m data delay.
    assert blocked("2026-11-27T17:00:00+00:00")  # 12:00 ET
    assert blocked("2026-11-27T18:29:00+00:00")  # 13:29 ET
    assert not blocked("2026-11-27T18:30:00+00:00")  # 13:30 ET
    assert not blocked("2026-11-27T21:00:00+00:00")  # 16:00 ET

    normal = pd.Timestamp("2026-12-01")
    assert not calendar.is_half_day(normal)

    def blocked_normal(now: str) -> bool:
        try:
            fd._require_close_session(now, normal, True, None)
            return False
        except fd.ForwardBlocked:
            return True

    assert blocked_normal("2026-12-01T21:00:00+00:00")  # 16:00 ET
    assert not blocked_normal("2026-12-01T21:30:00+00:00")  # 16:30 ET

    # A holiday cannot resolve to a writable session.
    try:
        fd.resolve_forward_session("2026-11-26T22:00:00+00:00", None)
    except fd.ForwardBlocked:
        pass
    else:
        raise AssertionError("Thanksgiving must not resolve to a writable session")

    # A Saturday run targets Friday's completed session but must not write a
    # close-valid row under a Saturday quote date.
    assert blocked("2026-11-28T22:00:00+00:00")

    et = ZoneInfo("America/New_York")
    assert not is_close_valid(
        session, session, datetime(2026, 11, 27, 13, 0, tzinfo=et)
    )
    assert is_close_valid(session, session, datetime(2026, 11, 27, 13, 30, tzinfo=et))
    assert not is_close_valid(
        session, session, datetime(2026, 11, 27, 12, 59, tzinfo=et)
    )


def test_cli_holiday_exit() -> None:
    import contextlib
    import io

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = fd.main(
            ["--as-of", "2021-12-25", "--now", "2021-12-28T02:00:00+00:00", "--json"]
        )
    assert rc == 2
    payload = json.loads(buf.getvalue())
    assert not payload["ok"]
    assert "market closed" in payload["errors"][0]


if __name__ == "__main__":
    test_dry_run_and_idempotent_paper()
    test_stale_holiday_and_health_fail()
    test_gate_and_separate_books()
    test_refresh_is_opt_in_and_injected()
    test_half_day_and_weekend_guard()
    test_cli_holiday_exit()
    print("test_forward: all checks passed")
