#!/usr/bin/env python
"""One idempotent forward-paper command for US + TECH (and optional tax/TECH).

This is the operational wrapper around the merged live autopilot. It does not
invent a second strategy or accounting engine:

- refresh only stale bars through the existing incremental update helpers;
- resolve the last completed US session and fail closed on holidays/stale data;
- run the live planner/paper broker separately for monthly/US, monthly/TECH and
  optionally tax/TECH (separate state/ledger files);
- run the existing paper logger/report for the legacy forward rows only when the
  session is genuinely close-valid;
- reconcile each live book, write ``results/forward_status.md`` and exit non-zero
  on any health failure.

There is no real broker path here. ``--paper`` writes the local paper ledger and
the existing forward files; dry-run writes nothing.

Examples:
  uv run python scripts/forward_daily.py                    # dry-run
  uv run python scripts/forward_daily.py --paper            # daily paper loop
  uv run python scripts/forward_daily.py --paper --tax
  uv run python scripts/forward_daily.py --paper --as-of 2026-11-25  # replay live
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import pandas as pd  # noqa: E402

from bot import data  # noqa: E402
from bot.live import calendar, reconcile  # noqa: E402
from bot.live.config import LiveConfig  # noqa: E402
from bot.live.engine import EngineError, LiveEngine  # noqa: E402
from bot.live.signal import SnapshotError  # noqa: E402
from bot.live.state import LiveState, StateStore  # noqa: E402

ET = ZoneInfo("America/New_York")
DEFAULT_BOOKS = (("monthly", "US"), ("monthly", "TECH"))
TAX_BOOK = ("tax", "TECH")
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_BLOCKED = 2


class ForwardBlocked(RuntimeError):
    """Fail closed: no session, stale data, market closed or a bad run."""


@dataclass
class BookResult:
    book: str
    market: str
    ok: bool
    session: str
    state_path: str
    live_status: str
    orders: int = 0
    equity: float = 0.0
    blocked: str | None = None
    reconcile: str = "skipped"
    paper_dates: int = 0
    paper_logged: bool = False
    gate: str = "WAIT 0/5"
    error: str | None = None


@dataclass
class ForwardReport:
    session: str
    now: str
    paper: bool
    books: list[BookResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    status_path: str | None = None

    @property
    def ok(self) -> bool:
        return not self.errors and all(b.ok for b in self.books)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "session": self.session,
            "now": self.now,
            "paper": self.paper,
            "status_path": self.status_path,
            "errors": self.errors,
            "books": [b.__dict__ for b in self.books],
        }

    def lines(self) -> list[str]:
        mode = "PAPER" if self.paper else "DRY-RUN"
        out = [
            f"FORWARD DAILY {mode} {'OK' if self.ok else 'ATTENTION'}  "
            f"session={self.session}  now={self.now}"
        ]
        for b in self.books:
            out.append(
                f"  {b.book}/{b.market}: {'OK' if b.ok else 'FAIL'}  "
                f"state={b.state_path}  live={b.live_status}  orders={b.orders}  "
                f"reconcile={b.reconcile}  paper_dates={b.paper_dates}  gate={b.gate}"
            )
            if b.blocked:
                out.append(f"    BLOCKED: {b.blocked}")
            if b.error:
                out.append(f"    ERROR: {b.error}")
        for err in self.errors:
            out.append(f"  ERROR: {err}")
        return out


def _now_dt(now: str | datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if isinstance(now, datetime):
        return now.astimezone(timezone.utc)
    return pd.Timestamp(now).to_pydatetime().astimezone(timezone.utc)


def resolve_forward_session(
    now: str | datetime | None = None, as_of: str | None = None
) -> pd.Timestamp:
    """Last completed US session; reject holidays/future replays."""
    latest = calendar.target_session(_now_dt(now))
    if as_of:
        session = pd.Timestamp(as_of).normalize()
        if session > latest:
            raise ForwardBlocked(
                f"--as-of {session.date()} is after the last completed session "
                f"{latest.date()}"
            )
    else:
        # A weekday holiday must fail closed, not silently reuse yesterday's
        # completed session as a new writable run. Weekends still target Friday
        # and are rejected by the close-date guard below.
        et = _now_dt(now).astimezone(ET)
        if et.date().weekday() < 5 and not calendar.is_session(et.date()):
            raise ForwardBlocked(
                f"market closed: {et.date()} is not a US trading session"
            )
        session = latest
    if not calendar.is_session(session):
        raise ForwardBlocked(
            f"market closed: {session.date()} is not a US trading session"
        )
    return session


def _require_close_session(
    now: str | datetime | None,
    session: pd.Timestamp,
    paper: bool,
    as_of: str | None,
    require_close: bool = True,
) -> None:
    """Paper rows may only be written after the actual session close.

    Half-days close at 13:00 ET, not 16:00. The data-publication delay is 30
    minutes, so the guard is 13:30 on a half-day and 16:30 on a normal day.
    Holidays/weekends are rejected earlier by ``resolve_forward_session``.
    """
    if not require_close or as_of:
        return
    et = _now_dt(now).astimezone(ET)
    close = calendar.close_hour(session)
    deadline_hour = close
    after_close = et.hour > deadline_hour or (
        et.hour == deadline_hour and et.minute >= 30
    )
    if not after_close or pd.Timestamp(et.date()) != session:
        kind = "half-day" if close == 13 else "session"
        raise ForwardBlocked(
            f"paper mode requires a completed {kind}: now_et={et:%Y-%m-%d %H:%M} "
            f"session={session.date()} close={close}:00 ET (+30m data delay); "
            "refusing a close-valid row"
        )


def _cache_fresh(session: pd.Timestamp) -> tuple[bool, str]:
    """The benchmark is the gate for a real session; no symbol-by-symbol fetch."""
    path = data.BARS / "SPY.csv"
    last = data._last_date(path, session) if path.exists() else None
    if last is None:
        return False, f"stale data: no SPY bar at or before {session.date()}"
    if last < session:
        return False, (
            f"stale data: SPY last={last.date()} session={session.date()} "
            "(holiday or unpublished close); no orders"
        )
    return True, f"SPY close={last.date()}"


def _refresh_stale(markets: list[str]) -> list[str]:
    """Incremental refresh only; paper_daily.refresh respects backoff."""
    import paper_daily

    done = []
    for market in dict.fromkeys(markets):
        paper_daily.refresh(market)
        done.append(market)
    return done


def _paper_path(book: str, results_dir: Path) -> Path:
    name = "paper_daily.csv" if book == "monthly" else "paper_daily_tax.csv"
    return results_dir / name


def paper_dates(book: str, market: str, results_dir: Path) -> list[str]:
    path = _paper_path(book, results_dir)
    if not path.exists():
        return []
    try:
        df = pd.read_csv(path)
        if "market" in df.columns:
            df = df[df["market"] == market]
        if "close_valid" in df.columns:
            df = df[df["close_valid"].astype(str).str.lower().isin(["true", "1"])]
        if "quote_date" not in df.columns:
            return []
        dates = pd.to_datetime(df["quote_date"]).dt.date.astype(str)
        return sorted(set(dates.tolist()))
    except Exception:
        return []


def gate_text(book: str, market: str, results_dir: Path, target: int = 5) -> str:
    path = results_dir / "paper_audit.csv"
    if not path.exists():
        return f"WAIT 0/{target} verified"
    try:
        df = pd.read_csv(path)
        df = df[(df.get("book") == book) & (df.get("market") == market)]
        if df.empty:
            return f"WAIT 0/{target} verified"
        verified = df["verified"].astype(str).str.lower().isin(["true", "1"])
        n = int(df.loc[verified, "date"].astype(str).nunique())
        return f"{'PASS' if n >= target else 'WAIT'} {n}/{target} verified"
    except Exception as exc:  # noqa: BLE001 - status must not crash
        return f"WAIT 0/{target} verified (audit unreadable: {exc})"


def _run_legacy_paper(
    book: str, market: str, session: str, now: str, results_dir: Path
) -> None:
    """Invoke the existing paper logger for a real after-close session."""
    if results_dir.resolve() != (ROOT / "results").resolve():
        raise ForwardBlocked(
            "legacy paper logger only supports the repository results dir"
        )
    if session in paper_dates(book, market, results_dir):
        return
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "paper_daily.py"),
        "--book",
        book,
        "--market",
        market,
    ]
    proc = subprocess.run(
        cmd, cwd=ROOT, text=True, capture_output=True, timeout=240, check=False
    )
    if proc.returncode != 0:
        raise ForwardBlocked(
            f"paper_daily {book}/{market} failed ({proc.returncode}): "
            f"{(proc.stderr or proc.stdout).strip()[-500:]}"
        )
    if session not in paper_dates(book, market, results_dir):
        raise ForwardBlocked(
            f"paper_daily {book}/{market} did not produce a close-valid row for "
            f"{session}; not counting it"
        )


def _run_legacy_report(book: str, market: str, results_dir: Path) -> None:
    if results_dir.resolve() != (ROOT / "results").resolve():
        return
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "paper_report.py"),
        "--book",
        book,
        "--market",
        market,
    ]
    proc = subprocess.run(
        cmd, cwd=ROOT, text=True, capture_output=True, timeout=240, check=False
    )
    if proc.returncode != 0:
        raise ForwardBlocked(
            f"paper_report {book}/{market} failed ({proc.returncode}): "
            f"{(proc.stderr or proc.stdout).strip()[-500:]}"
        )


def _write_status(report: ForwardReport, path: Path) -> None:
    lines = [
        "# Forward paper status",
        "",
        f"Generated: {report.now}",
        f"Session: {report.session}",
        f"Mode: {'paper' if report.paper else 'dry-run'}",
        f"Overall: **{'OK' if report.ok else 'ATTENTION'}**",
        "",
        "| book | market | live | orders | reconcile | paper dates | verified gate |",
        "|---|---|---|---:|---|---:|---|",
    ]
    for b in report.books:
        lines.append(
            f"| {b.book} | {b.market} | {b.live_status} | {b.orders} | "
            f"{b.reconcile} | {b.paper_dates} | {b.gate} |"
        )
    lines += ["", "## State", ""]
    for b in report.books:
        try:
            shown = str(Path(b.state_path).relative_to(ROOT))
        except ValueError:
            shown = b.state_path
        lines.append(f"- `{b.book}/{b.market}`: `{shown}`")
        if b.blocked:
            lines.append(f"  - blocked: {b.blocked}")
        if b.error:
            lines.append(f"  - error: {b.error}")
    if report.errors:
        lines += ["", "## Errors", ""] + [f"- {e}" for e in report.errors]
    lines += ["", "## Next action", ""]
    if not report.ok:
        lines.append("- Fix the errors/blockers above; do not backfill a stale open.")
    elif not report.paper:
        lines.append("- Dry-run only. Add `--paper` after the next US close.")
    else:
        if any(not b.paper_logged for b in report.books):
            lines.append(
                "- Legacy paper row was already present or this was a replay; no row "
                "was duplicated or fabricated. Live paper state is the separate ledger."
            )
        if any(b.gate.startswith("WAIT") for b in report.books):
            lines.append(
                "- Keep the weekday job running; one verified date is added per "
                "close-valid session. Do not quote a forward edge before 5/5."
            )
        else:
            lines.append(
                "- Gate passed on the recorded dates; review the audit before use."
            )
    lines += [
        "",
        "No real orders were sent. The live ledger is separate from the legacy "
        "`paper_daily.csv` track; US and TECH rows are never mixed.",
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{Path(str(path)).stem}.tmp")
    tmp.write_text("\n".join(lines))
    tmp.replace(path)


def run_forward(
    base: LiveConfig,
    books: list[tuple[str, str]],
    *,
    paper: bool = False,
    refresh: bool = False,
    as_of: str | None = None,
    now: str | datetime | None = None,
    state_dir: str | None = None,
    results_dir: Path | None = None,
    panel: pd.DataFrame | None = None,
    membership: pd.DataFrame | None = None,
    paper_runner=None,
    report_runner=None,
    refresh_fn=None,
    cache_check=None,
    require_close: bool | None = None,
    write_status: bool | None = None,
) -> ForwardReport:
    """Run the daily loop. All runners are injectable for synthetic tests."""
    if require_close is None:
        require_close = bool(paper)
    stamp = _now_dt(now).isoformat(timespec="seconds")
    results_dir = Path(results_dir or (ROOT / "results"))
    paper_runner = paper_runner or _run_legacy_paper
    report_runner = report_runner or _run_legacy_report
    refresh_fn = refresh_fn or _refresh_stale
    cache_check = cache_check or _cache_fresh
    try:
        session = resolve_forward_session(now, as_of)
        _require_close_session(now, session, paper, as_of, require_close)
    except ForwardBlocked as exc:
        report = ForwardReport(
            session=str(as_of or ""),
            now=stamp,
            paper=paper,
            errors=[str(exc)],
        )
        if write_status if write_status is not None else paper:
            path = results_dir / "forward_status.md"
            _write_status(report, path)
            report.status_path = str(path)
        return report

    if refresh:
        try:
            refresh_fn([m for _, m in books])
        except Exception as exc:  # noqa: BLE001 - data fetch is an operational gate
            report = ForwardReport(
                session=str(session.date()),
                now=stamp,
                paper=paper,
                errors=[f"refresh failed: {exc}"],
            )
            if write_status if write_status is not None else paper:
                path = results_dir / "forward_status.md"
                _write_status(report, path)
                report.status_path = str(path)
            return report

    fresh, detail = cache_check(session)
    if not fresh:
        report = ForwardReport(
            session=str(session.date()), now=stamp, paper=paper, errors=[detail]
        )
        if write_status if write_status is not None else paper:
            path = results_dir / "forward_status.md"
            _write_status(report, path)
            report.status_path = str(path)
        return report

    report = ForwardReport(
        session=str(session.date()), now=stamp, paper=paper, errors=[]
    )

    for book, market in books:
        cfg = LiveConfig(
            book=book,
            market=market,
            broker="paper",
            capital=base.capital,
            leverage=base.leverage,
            state_dir=state_dir or base.state_dir,
        )
        engine = LiveEngine(cfg)
        item = BookResult(
            book=book,
            market=market,
            ok=True,
            session=str(session.date()),
            state_path=str(cfg.state_path),
            live_status="not-run",
            gate=gate_text(book, market, results_dir, cfg.forward_gate),
            paper_dates=len(paper_dates(book, market, results_dir)),
        )
        try:
            if paper:
                result = engine.run(
                    now=stamp,
                    as_of=as_of,
                    panel=panel,
                    membership=membership,
                )
            else:
                result = engine.plan(
                    now=stamp,
                    as_of=as_of,
                    panel=panel,
                    membership=membership,
                )
            item.live_status = "blocked" if result.blocked else "ok"
            item.orders = len(result.orders)
            item.equity = float(result.equity)
            item.blocked = result.blocked_reason
            if result.blocked:
                item.ok = False
                item.error = result.blocked_reason
                report.errors.append(f"{book}/{market}: {result.blocked_reason}")
            state = engine.store.load(allow_config_change=True) if paper else None
            if paper:
                rep = reconcile.reconcile(cfg, state, engine.store)
                item.reconcile = "OK" if rep.ok else "FAIL"
                if not rep.ok:
                    item.ok = False
                    item.error = "; ".join(rep.issues[:3])
                    report.errors.append(f"{book}/{market}: reconcile: {item.error}")
                if state is None or state.last_session != str(session.date()):
                    item.ok = False
                    item.error = item.error or "state did not record the session"
                    report.errors.append(f"{book}/{market}: {item.error}")
                if paper and not as_of and not result.blocked:
                    try:
                        if str(session.date()) not in paper_dates(
                            book, market, results_dir
                        ):
                            paper_runner(
                                book, market, str(session.date()), stamp, results_dir
                            )
                            item.paper_logged = True
                        item.paper_dates = len(paper_dates(book, market, results_dir))
                    except Exception as exc:  # noqa: BLE001 - forward health gate
                        item.ok = False
                        item.error = f"paper logger: {exc}"
                        report.errors.append(f"{book}/{market}: {item.error}")
                if paper and not as_of:
                    try:
                        report_runner(book, market, results_dir)
                    except Exception as exc:  # noqa: BLE001
                        item.ok = False
                        item.error = f"paper report: {exc}"
                        report.errors.append(f"{book}/{market}: {item.error}")
        except (SnapshotError, EngineError, Exception) as exc:  # noqa: BLE001
            item.ok = False
            item.live_status = "error"
            item.error = str(exc)
            report.errors.append(f"{book}/{market}: {exc}")
        item.gate = gate_text(book, market, results_dir, cfg.forward_gate)
        report.books.append(item)

    if not report.books:
        report.errors.append("no books requested")
    if write_status if write_status is not None else paper:
        path = results_dir / "forward_status.md"
        _write_status(report, path)
        report.status_path = str(path)
    return report


def _base_config(args: argparse.Namespace) -> LiveConfig:
    return LiveConfig(
        capital=args.capital,
        leverage=args.leverage,
        state_dir=args.state_dir,
        broker="paper",
    )


def _parse_books(args: argparse.Namespace) -> list[tuple[str, str]]:
    books = list(DEFAULT_BOOKS)
    if args.tax:
        books.append(TAX_BOOK)
    return books


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--paper", action="store_true", help="write paper state/ledger")
    ap.add_argument(
        "--require-close",
        action="store_true",
        help="refuse to write before the actual close (half-day aware); implied by --paper",
    )
    ap.add_argument(
        "--refresh",
        action="store_true",
        help="incrementally refresh stale needed bars before the run",
    )
    ap.add_argument(
        "--no-refresh",
        action="store_true",
        help="never fetch (used by replays/tests; cache must already be current)",
    )
    ap.add_argument("--tax", action="store_true", help="also run tax/TECH")
    ap.add_argument("--capital", type=float, default=10_000.0)
    ap.add_argument("--leverage", type=float, default=2.5)
    ap.add_argument("--state-dir", default=None)
    ap.add_argument("--as-of", default=None)
    ap.add_argument("--now", default=None, help=argparse.SUPPRESS)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    refresh = (
        (bool(args.refresh) or bool(args.paper))
        and not args.no_refresh
        and not args.as_of
    )
    report = run_forward(
        _base_config(args),
        _parse_books(args),
        paper=args.paper,
        refresh=refresh,
        as_of=args.as_of,
        now=args.now,
        state_dir=args.state_dir,
        require_close=bool(args.require_close or args.paper),
    )
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        print("\n".join(report.lines()))
        if report.status_path:
            print(f"status: {report.status_path}")
        if report.paper and report.ok:
            print("paper only: no real orders")
    return EXIT_OK if report.ok else EXIT_BLOCKED


if __name__ == "__main__":
    raise SystemExit(main())
