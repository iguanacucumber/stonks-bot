#!/usr/bin/env python
"""Fail-closed watchdog for the forward paper proof.

Reads the gate, the append-only proof lockbox and the last daily run, then
returns non-zero if any of these is true:

- the gate is BLOCKED (missed required session, stale data, duplicate/audit-only
  row, reconcile failure);
- a verified audit date has no lockbox record;
- the lockbox hash chain is broken, edited, duplicated or future-dated;
- `results/forward_status.md` is missing or says ATTENTION;
- the last daily run is behind the last completed session.

It never writes a row and never advances the gate. A healthy WAIT is exit 0
because a short track is an honest state, not a failure. It prints the exact
next session, half-day close and per-book ETA so a missed wall-clock window is
visible.

Run:
  uv run python scripts/forward_watchdog.py
  uv run python scripts/forward_watchdog.py --json
  uv run python scripts/forward_watchdog.py --require-daily

Exit codes: 0 WAIT/PASS, 2 BLOCKED/missed/unsafe, 1 unexpected error.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import pandas as pd  # noqa: E402

import forward_gate as fg  # noqa: E402
from bot.dashboard.collect import parse_forward_status  # noqa: E402
from bot.live import calendar, proof  # noqa: E402

ET = ZoneInfo("America/New_York")
WAIT = "WAIT"
PASS = "PASS"
BLOCKED = "BLOCKED"
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_BLOCKED = 2


def _now_dt(now: str | datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if isinstance(now, datetime):
        return now.astimezone(timezone.utc)
    return pd.Timestamp(now).to_pydatetime().astimezone(timezone.utc)


def _daily_issues(
    results_dir: Path, expected_session: str
) -> tuple[dict, list[str], list[str]]:
    """Check the last daily status; a missing/ATTENTION file is unsafe."""
    path = results_dir / "forward_status.md"
    daily, parse_warnings = parse_forward_status(path)
    issues: list[str] = []
    warnings: list[str] = list(parse_warnings)
    if not path.exists():
        issues.append(f"daily status missing: {path}")
        return daily, issues, warnings
    overall = str(daily.get("overall") or "").upper()
    if overall != "OK":
        issues.append(f"daily run is not OK: overall={overall or 'missing'}")
    session = str(daily.get("session") or "")
    try:
        session_day = pd.Timestamp(session).normalize()
    except Exception:  # noqa: BLE001
        issues.append(f"daily status has no readable session: {session or 'missing'}")
        session_day = None
    if session_day is not None:
        if session_day < pd.Timestamp(expected_session).normalize():
            issues.append(
                f"daily run is behind: status session={session_day.date()} "
                f"expected={expected_session}"
            )
        elif session_day > pd.Timestamp(expected_session).normalize():
            issues.append(
                f"daily status is ahead of the completed session: "
                f"{session_day.date()} > {expected_session}"
            )
    if not daily.get("generated"):
        warnings.append("daily status has no generated timestamp")
    return daily, issues, warnings


def collect_watchdog(
    *,
    results_dir: Path | None = None,
    state_dir: Path | None = None,
    lockbox: Path | None = None,
    now: str | datetime | None = None,
    bench_last: str | None = None,
    target: int = fg.TARGET_DEFAULT,
    require_daily: bool = True,
) -> dict:
    results_dir = Path(results_dir or (ROOT / "results"))
    lockbox = Path(lockbox) if lockbox else results_dir / proof.DEFAULT_NAME
    now_dt = _now_dt(now)
    stamp = now_dt.isoformat(timespec="seconds")
    issues: list[str] = []
    warnings: list[str] = []

    report = fg.collect_gate(
        results_dir=results_dir,
        target=target,
        now=now_dt,
        bench_last=bench_last,
        state_dir=state_dir,
    )
    if report.status == BLOCKED:
        issues.append("forward gate is BLOCKED")
        issues.extend(report.issues)
        for book in report.books:
            issues.extend(f"{book.book}/{book.market}: {x}" for x in book.issues)

    try:
        records = proof.load_records(lockbox, now=now_dt)
    except proof.ProofError as exc:
        records = []
        issues.append(f"lockbox invalid: {exc}")

    locked_keys = {(str(r["date"]), str(r["book"]), str(r["market"])) for r in records}
    if not records and any(b.verified_dates for b in report.books):
        issues.append(
            f"lockbox is empty but the gate has verified dates; run "
            f"`uv run python scripts/forward_gate.py --lock` ({lockbox})"
        )
    for book in report.books:
        unlocked = sorted(
            set(book.verified_dates)
            - {k[0] for k in locked_keys if k[1:] == (book.book, book.market)}
        )
        if unlocked:
            issues.append(
                f"unlocked verified date(s) for {book.book}/{book.market}: "
                f"{', '.join(unlocked)}"
            )
        if book.status == BLOCKED:
            continue
        next_required = str(book.next_required or "")
        if (
            next_required
            and next_required <= str(report.session)
            and next_required not in book.verified_dates
        ):
            issues.append(
                f"missed expected session {next_required} for "
                f"{book.book}/{book.market} (completed={report.session})"
            )
        if not book.verified_dates and book.required:
            warnings.append(f"{book.book}/{book.market} has no verified date yet")

    daily = {}
    if require_daily:
        daily, daily_issues, daily_warnings = _daily_issues(results_dir, report.session)
        issues.extend(daily_issues)
        warnings.extend(daily_warnings)
    else:
        daily, daily_warnings = parse_forward_status(results_dir / "forward_status.md")
        warnings.extend(daily_warnings)

    if records:
        last_proof = max(str(r["date"]) for r in records)
        if last_proof > str(report.session):
            issues.append(
                f"lockbox has a date after the completed session: "
                f"{last_proof} > {report.session}"
            )
        missing_state = [
            str(r["date"])
            for r in records
            if not (r.get("files", {}).get("live_state", {}).get("present"))
        ]
        if missing_state:
            warnings.append("live state hash absent for: " + ", ".join(missing_state))

    status = BLOCKED if issues else (PASS if report.status == PASS else WAIT)
    next_dates = [
        str(b.next_required)
        for b in report.books
        if b.next_required and b.status != PASS
    ]
    next_session = min(next_dates) if next_dates else None
    etas = [
        b.eta for b in report.books if b.status != PASS and b.eta and b.eta != "reached"
    ]
    all_eta = max(etas) if etas else None
    next_half = bool(next_session and calendar.is_half_day(next_session))
    next_close = calendar.close_hour(next_session) if next_session else None
    next_gate = None
    if next_session and next_close is not None:
        next_gate = (
            f"{next_session} "
            + ("half-day" if next_half else "session")
            + f"; close {next_close}:00 ET, data gate after {next_close}:30 ET"
        )
    return {
        "ok": status != BLOCKED,
        "status": status,
        "now": stamp,
        "session": report.session,
        "gate_status": report.status,
        "next_session": next_session,
        "next_session_half_day": next_half,
        "next_close_hour_et": next_close,
        "next_gate_time_et": next_gate,
        "eta_all_books": all_eta,
        "books": [
            {
                "book": b.book,
                "market": b.market,
                "status": b.status,
                "verified": b.verified,
                "target": b.target,
                "next": b.next_required,
                "eta": b.eta,
                "remaining": b.remaining_required,
                "issues": b.issues,
            }
            for b in report.books
        ],
        "lockbox": {
            "path": str(lockbox),
            "records": len(records),
            "last": max((str(r["date"]) for r in records), default=None),
            "chain": "OK" if not any("lockbox" in x for x in issues) else "FAIL",
        },
        "daily": {
            "path": str(results_dir / "forward_status.md"),
            "generated": daily.get("generated"),
            "session": daily.get("session"),
            "overall": daily.get("overall"),
        },
        "issues": issues,
        "warnings": warnings,
    }


def lines(payload: dict) -> list[str]:
    status = payload["status"]
    out = [
        f"FORWARD WATCHDOG {status}  now={payload['now']}  session={payload['session']}"
    ]
    gate = payload.get("gate_status")
    out.append(f"  gate: {gate}")
    for book in payload.get("books", []):
        out.append(
            f"  {book['book']}/{book['market']}: {book['status']} "
            f"{book['verified']}/{book['target']} next={book['next']} "
            f"eta_5of5={book['eta']} remaining={book['remaining']}"
        )
    if payload.get("next_session"):
        out.append(
            f"  next: {payload['next_gate_time_et']}"
            + (
                "  (early close: do not run the half-day before 13:30 ET)"
                if payload.get("next_session_half_day")
                else ""
            )
        )
    if payload.get("eta_all_books"):
        out.append(
            f"  eta: last book 5/5 on {payload['eta_all_books']} if no session is missed"
        )
    lock = payload.get("lockbox", {})
    out.append(
        f"  lockbox: {lock.get('records', 0)} record(s), "
        f"last={lock.get('last') or 'none'}, chain={lock.get('chain')}"
    )
    daily = payload.get("daily", {})
    out.append(
        f"  daily: {daily.get('overall') or 'missing'} "
        f"session={daily.get('session') or 'none'} "
        f"generated={daily.get('generated') or 'none'}"
    )
    for issue in payload.get("issues", []):
        out.append(f"  BLOCKED: {issue}")
    for warning in payload.get("warnings", []):
        out.append(f"  WARN: {warning}")
    if status == WAIT:
        out.append("  no missed session; gate stays WAIT until 5/5 verified")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--results-dir", default=None)
    ap.add_argument("--state-dir", default=None)
    ap.add_argument("--lockbox", default=None)
    ap.add_argument("--now", default=None, help=argparse.SUPPRESS)
    ap.add_argument("--bench-last", default=None, help=argparse.SUPPRESS)
    ap.add_argument("--target", type=int, default=fg.TARGET_DEFAULT)
    ap.add_argument(
        "--require-daily",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="fail when results/forward_status.md is missing or not OK",
    )
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        payload = collect_watchdog(
            results_dir=Path(args.results_dir) if args.results_dir else None,
            state_dir=Path(args.state_dir) if args.state_dir else None,
            lockbox=Path(args.lockbox) if args.lockbox else None,
            now=args.now,
            bench_last=args.bench_last,
            target=args.target,
            require_daily=args.require_daily,
        )
    except Exception as exc:  # noqa: BLE001 - watchdog must be automation-safe
        print(f"WATCHDOG ERROR: {exc}")
        return EXIT_ERROR
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print("\n".join(lines(payload)))
    return EXIT_OK if payload["ok"] else EXIT_BLOCKED


if __name__ == "__main__":
    raise SystemExit(main())
