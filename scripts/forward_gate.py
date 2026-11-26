#!/usr/bin/env python
"""Read-only forward-gate report for the paper books.

The gate is intentionally conservative: only distinct, verified dates in
``results/paper_audit.csv`` count, and every required paper/audit/ledger check must
be explicit. Missing files, duplicate/conflicting dates, stale benchmark data or a
missed required session produce BLOCKED; a healthy but short track is WAIT.

No row is fabricated or modified. ``--write-status`` writes only the human status
file (and never the audit/ledgers). ``--check`` is the automation-safe exit-code
wrapper; ``--since`` reports newly verified dates.

Exit codes:
  0 PASS or WAIT, 2 BLOCKED, 1 unexpected error.

Run:
  uv run python scripts/forward_gate.py
  uv run python scripts/forward_gate.py --json
  uv run python scripts/forward_gate.py --check --write-status
  uv run python scripts/forward_gate.py --check --write-status --lock
  uv run python scripts/forward_gate.py --check --since 2026-11-25

``--lock`` appends newly verified dates to ``results/forward_proof.jsonl`` only
when the gate is WAIT/PASS. The lockbox is evidence, not a counter; the gate
still counts only distinct verified dates in ``paper_audit.csv``.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from bot import data  # noqa: E402
from bot.live import calendar, proof  # noqa: E402

ET = ZoneInfo("America/New_York")
DEFAULT_BOOKS = (("monthly", "US"), ("monthly", "TECH"), ("tax", "TECH"))
PASS = "PASS"
WAIT = "WAIT"
BLOCKED = "BLOCKED"
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_WAIT = 0  # automation-safe: a healthy short track is not an error
EXIT_BLOCKED = 2
TARGET_DEFAULT = 5


@dataclass
class BookGate:
    book: str
    market: str
    required: bool
    status: str = WAIT
    verified: int = 0
    target: int = TARGET_DEFAULT
    close_valid: int = 0
    rejected: int = 0
    paper_rows: int = 0
    duplicate_dates: int = 0
    last_verified: str | None = None
    last_paper: str | None = None
    verified_dates: list[str] = field(default_factory=list)
    close_valid_dates: list[str] = field(default_factory=list)
    next_required: str | None = None
    remaining_required: list[str] = field(default_factory=list)
    eta: str | None = None
    equity: float | None = None
    equity_source: str = "none"
    drawdown: float | None = None
    data_fresh: bool = False
    data_last: str | None = None
    reconcile: str = "no state"
    issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    new_verified: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "book": self.book,
            "market": self.market,
            "required": self.required,
            "status": self.status,
            "verified": self.verified,
            "target": self.target,
            "close_valid": self.close_valid,
            "rejected": self.rejected,
            "paper_rows": self.paper_rows,
            "duplicate_dates": self.duplicate_dates,
            "last_verified": self.last_verified,
            "last_paper": self.last_paper,
            "verified_dates": self.verified_dates,
            "close_valid_dates": self.close_valid_dates,
            "next_required": self.next_required,
            "remaining_required": self.remaining_required,
            "eta": self.eta,
            "equity": self.equity,
            "equity_source": self.equity_source,
            "drawdown": self.drawdown,
            "data_fresh": self.data_fresh,
            "data_last": self.data_last,
            "reconcile": self.reconcile,
            "issues": self.issues,
            "warnings": self.warnings,
            "new_verified": self.new_verified,
        }


@dataclass
class GateReport:
    now: str
    session: str
    status: str
    books: list[BookGate]
    issues: list[str] = field(default_factory=list)
    status_path: str | None = None
    lockbox: dict | None = None

    @property
    def ok(self) -> bool:
        return self.status == PASS

    @property
    def blocked(self) -> bool:
        return self.status == BLOCKED

    def to_dict(self) -> dict:
        return {
            "now": self.now,
            "session": self.session,
            "status": self.status,
            "ok": self.ok,
            "blocked": self.blocked,
            "issues": self.issues,
            "status_path": self.status_path,
            "lockbox": self.lockbox,
            "books": [b.to_dict() for b in self.books],
        }

    def lines(self) -> list[str]:
        out = [f"FORWARD GATE {self.status}  now={self.now}  session={self.session}"]
        for b in self.books:
            rest = (
                f"{b.book}/{b.market}: {b.status} {b.verified}/{b.target} verified, "
                f"next={b.next_required}, eta_5of5={b.eta}, "
                f"remaining={b.remaining_required}, equity={_fmt(b.equity)}, "
                f"dd={_fmt_pct(b.drawdown)}, data={'fresh' if b.data_fresh else 'STALE'}, "
                f"reconcile={b.reconcile}"
            )
            out.append("  " + rest)
            for issue in b.issues:
                out.append(f"    BLOCKED: {issue}")
            for warn in b.warnings:
                out.append(f"    WARN: {warn}")
            if b.new_verified:
                out.append(f"    NEW VERIFIED: {', '.join(b.new_verified)}")
        for issue in self.issues:
            out.append(f"  BLOCKED: {issue}")
        if self.lockbox:
            out.append(
                "  lockbox: "
                f"{self.lockbox.get('records', 0)} record(s), "
                f"last={self.lockbox.get('last') or 'none'}, "
                f"chain={self.lockbox.get('chain', '?')}"
            )
        return out


def _now_dt(now: str | datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if isinstance(now, datetime):
        return now.astimezone(timezone.utc)
    return pd.Timestamp(now).to_pydatetime().astimezone(timezone.utc)


def _fmt(x: float | None, spec: str = ",.2f") -> str:
    if x is None:
        return "n/a"
    try:
        v = float(x)
    except (TypeError, ValueError):
        return "n/a"
    return "n/a" if not pd.notna(v) else format(v, spec)


def _fmt_pct(x: float | None) -> str:
    if x is None:
        return "n/a"
    try:
        return f"{float(x) * 100:+.1f}%"
    except (TypeError, ValueError):
        return "n/a"


def _truthy(series: pd.Series) -> pd.Series:
    return series.astype(str).str.lower().isin(["true", "1", "yes"])


def _paper_path(book: str, results_dir: Path) -> Path:
    return results_dir / (
        "paper_daily.csv" if book == "monthly" else "paper_daily_tax.csv"
    )


def _state_dir(state_dir: Path | None = None) -> Path:
    return Path(state_dir) if state_dir is not None else ROOT / "state" / "live"


def _state_path(book: str, market: str, state_dir: Path | None = None) -> Path:
    return _state_dir(state_dir) / f"{book}_{market.lower()}.json"


def _read_audit(results_dir: Path) -> pd.DataFrame:
    path = results_dir / "paper_audit.csv"
    if not path.exists():
        raise FileNotFoundError(f"missing {path.name}")
    df = pd.read_csv(path)
    required = {"book", "market", "date", "verified"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"{path.name} missing columns {sorted(missing)}")
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    df["verified"] = _truthy(df["verified"])
    return df


def _read_paper(book: str, market: str, results_dir: Path) -> pd.DataFrame:
    path = _paper_path(book, results_dir)
    if not path.exists():
        raise FileNotFoundError(f"missing {path.name}")
    df = pd.read_csv(path)
    required = {"market", "quote_date"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"{path.name} missing columns {sorted(missing)}")
    df = df[df["market"].astype(str) == market].copy()
    df["quote_date"] = pd.to_datetime(df["quote_date"]).dt.normalize()
    for c in ("close_valid", "data_complete", "reconciled"):
        df[c] = _truthy(df[c]) if c in df.columns else False
    if "equity" in df.columns:
        df["equity"] = pd.to_numeric(df["equity"], errors="coerce")
    return df


def _reconcile_state(
    book: str, market: str, state_dir: Path | None = None
) -> tuple[str, list[str]]:
    """Read-only state/ledger reconcile; no state is 'no state', not a failure."""
    path = _state_path(book, market, state_dir)
    if not path.exists():
        return "no state", []
    try:
        from bot.live import reconcile
        from bot.live.config import LiveConfig
        from bot.live.state import StateStore

        cfg = LiveConfig(
            book=book,
            market=market,
            state_dir=str(_state_dir(state_dir)),
        )
        rep = reconcile.reconcile(
            cfg, StateStore(cfg).load(allow_config_change=True), StateStore(cfg)
        )
        if rep.ok:
            return "OK", []
        return "FAIL", list(rep.issues[:3])
    except Exception as exc:  # noqa: BLE001 - status must not crash
        return "FAIL", [str(exc)]


def _next_required(last_seen: date | None, today: date, after_close: bool) -> date:
    if last_seen is None:
        return (
            today
            if calendar.is_session(today) and after_close
            else calendar.next_session(calendar.prev_session(today))
        )
    return calendar.next_session(last_seen)


def _audit_conflicts(df: pd.DataFrame) -> list[str]:
    issues = []
    for (book, market, day), group in df.groupby(["book", "market", "date"]):
        if len(group) > 1:
            vals = set(group["verified"].tolist())
            if len(vals) > 1:
                issues.append(
                    f"duplicate audit date {book}/{market} {day.date()} has "
                    "conflicting verified values"
                )
            else:
                issues.append(
                    f"duplicate audit date {book}/{market} {day.date()} "
                    f"({len(group)} rows)"
                )
    return issues


def collect_gate(
    *,
    results_dir: Path | None = None,
    books: list[tuple[str, str]] | None = None,
    target: int = TARGET_DEFAULT,
    now: str | datetime | None = None,
    since: str | None = None,
    bench_last: str | date | None = None,
    state_dir: Path | None = None,
) -> GateReport:
    results_dir = Path(results_dir or (ROOT / "results"))
    books = books or list(DEFAULT_BOOKS)
    now_dt = _now_dt(now)
    et = now_dt.astimezone(ET)
    today = et.date()
    close = calendar.close_hour(today)
    after_close = et.hour > close or (et.hour == close and et.minute >= 30)
    session = calendar.target_session(now_dt)
    if not calendar.is_session(session):
        # The calendar helper rolls weekends/holidays; keep the gate on the last
        # completed session, never a holiday date.
        session = pd.Timestamp(calendar.prev_session(session))
    since_date = pd.Timestamp(since).date() if since else None

    report = GateReport(
        now=now_dt.isoformat(timespec="seconds"),
        session=str(session.date()),
        status=WAIT,
        books=[],
    )
    try:
        audit = _read_audit(results_dir)
    except Exception as exc:  # noqa: BLE001
        report.status = BLOCKED
        report.issues.append(str(exc))
        # Still emit one blocked row per requested book so automation has a table.
        for book, market in books:
            report.books.append(
                BookGate(
                    book=book,
                    market=market,
                    required=book == "monthly",
                    status=BLOCKED,
                    target=target,
                    issues=[str(exc)],
                )
            )
        return report

    audit_issues = _audit_conflicts(audit)
    if bench_last is not None:
        last = pd.Timestamp(bench_last).normalize()
    else:
        path = data.BARS / "SPY.csv"
        last = data._last_date(path, session) if path.exists() else None
    data_fresh = last is not None and last >= session

    for book, market in books:
        required = book == "monthly"  # tax is optional until its first run
        bg = BookGate(
            book=book,
            market=market,
            required=required,
            target=target,
            data_fresh=bool(data_fresh),
            data_last=str(last.date()) if last is not None else None,
        )
        if not data_fresh:
            bg.issues.append(
                f"stale data: SPY last={bg.data_last} session={session.date()}"
            )
        sub = audit[(audit["book"] == book) & (audit["market"] == market)].copy()
        for issue in audit_issues:
            if f"{book}/{market}" in issue:
                bg.issues.append(issue)
        if len(sub):
            dup = sub.groupby("date").size()
            bg.duplicate_dates = int((dup > 1).sum())
            # Conflicting duplicates are already a global issue; keep one row per
            # date for counting even when the duplicate is identical.
            # A conflicting duplicate is a blocker, but if any copy is verified
            # it still counts once for display; the issue prevents PASS.
            bad_dates = set(dup[dup > 1].index)
            # Duplicate dates never count, even when both copies agree. The
            # conflicting/duplicate issue already blocks the book; this keeps the
            # verified counter honest.
            sub = sub[~sub["date"].isin(bad_dates)]
            sub = sub.sort_values(["date", "verified"]).drop_duplicates(
                "date", keep="last"
            )
            # A verified audit row is only a valid forward date if it is a real
            # NYSE session on/before the completed session. This blocks a
            # fabricated weekend/holiday/future row from incrementing the gate.
            session_day = session.date()
            invalid_dates: list[str] = []
            for value in sub["date"]:
                if pd.isna(value):
                    invalid_dates.append("NaT")
                    continue
                day = value.date()
                if day > session_day or not calendar.is_session(day):
                    invalid_dates.append(str(day))
            if invalid_dates:
                bg.issues.append(
                    "audit contains non-session/future verified date(s): "
                    f"{sorted(set(invalid_dates))}"
                )
                sub = sub[~sub["date"].dt.date.astype(str).isin(set(invalid_dates))]
            bg.verified = int(sub["verified"].sum())
            bg.rejected = int((~sub["verified"]).sum())
            bg.verified_dates = sorted(
                {str(d) for d in sub.loc[sub["verified"], "date"].dt.date.astype(str)}
            )
            if bg.verified:
                bg.last_verified = str(sub.loc[sub["verified"], "date"].max().date())
        # New verified dates since a supplied baseline.
        if since_date is not None:
            bg.new_verified = sorted(
                {
                    str(d.date())
                    for d in sub.loc[
                        sub["verified"] & (sub["date"].dt.date > since_date), "date"
                    ]
                }
            )
        try:
            paper = _read_paper(book, market, results_dir)
            bg.paper_rows = len(paper)
            close = paper[paper["close_valid"]].copy()
            bg.close_valid = int(close["quote_date"].nunique())
            bg.close_valid_dates = sorted(
                {str(d) for d in close["quote_date"].dt.date.astype(str)}
            )
            if len(paper):
                bg.last_paper = str(paper["quote_date"].max().date())
            if len(close) and "equity" in close:
                close = close.sort_values("quote_date").drop_duplicates(
                    "quote_date", keep="last"
                )
                eq = close["equity"].dropna()
                if len(eq):
                    bg.equity = float(eq.iloc[-1])
                    bg.equity_source = "close-valid"
                    if len(eq) >= 2:
                        bg.drawdown = float((eq / eq.cummax() - 1.0).min())
                    else:
                        bg.drawdown = 0.0
            elif len(paper) and "equity" in paper:
                preview = paper.dropna(subset=["equity"]).sort_values("quote_date")
                if len(preview):
                    bg.equity = float(preview["equity"].iloc[-1])
                    bg.equity_source = "preview (not close-valid)"
            # paper_daily.equity is a single-day mark, not compounded wealth.
            # paper_report stores compounded live_equity in the audit; prefer it
            # for the gate's equity/drawdown display.
            if "live_equity" in sub.columns:
                audit_eq = sub.copy()
                audit_eq["_eq"] = pd.to_numeric(
                    audit_eq["live_equity"], errors="coerce"
                )
                audit_eq = (
                    audit_eq.dropna(subset=["_eq"])
                    .sort_values("date")
                    .drop_duplicates("date", keep="last")
                )
                if len(audit_eq):
                    vals = audit_eq["_eq"].astype(float).reset_index(drop=True)
                    bg.equity = float(vals.iloc[-1])
                    bg.equity_source = "audit live_equity (compounded)"
                    bg.drawdown = (
                        float((vals / vals.cummax() - 1.0).min())
                        if len(vals) > 1
                        else 0.0
                    )
            if bg.verified:
                close_dates = (
                    set(close["quote_date"].dt.date.astype(str))
                    if len(close)
                    else set()
                )
                missing = (
                    set(sub.loc[sub["verified"], "date"].dt.date.astype(str))
                    - close_dates
                )
                if missing:
                    bg.issues.append(
                        "audit verified date(s) missing from close-valid paper rows: "
                        f"{sorted(missing)}"
                    )
        except FileNotFoundError:
            if required:
                bg.issues.append("missing paper_daily file/rows")
            else:
                bg.warnings.append("tax book not started")
        except Exception as exc:  # noqa: BLE001
            bg.issues.append(f"paper read failed: {exc}")

        bg.reconcile, rec_issues = _reconcile_state(book, market, state_dir)
        if bg.reconcile == "FAIL":
            bg.issues.extend(f"reconcile: {x}" for x in rec_issues)

        # Only a verified date advances the required-session list. A preview row
        # with close_valid=False is not evidence and must never hide the session
        # it failed to verify; otherwise an unverified row on 11-27 would make
        # the gate expect 11-30 and silently skip the missed 11-27.
        last_verified = (
            date.fromisoformat(bg.last_verified) if bg.last_verified else None
        )
        bg.next_required = str(_next_required(last_verified, today, after_close))
        next_session = date.fromisoformat(bg.next_required)
        # The remaining sessions are the next real NYSE sessions needed to reach
        # the target. Do not skip a normal session (for example 11-30) just
        # because a manual schedule omitted it.
        need = max(0, target - bg.verified)
        cursor = next_session
        for _ in range(need):
            bg.remaining_required.append(str(cursor))
            cursor = calendar.next_session(cursor)
        bg.eta = (
            bg.remaining_required[-1]
            if bg.remaining_required
            else (bg.last_verified or "reached")
        )
        if bg.verified < target:
            if next_session < today or (next_session == today and after_close):
                if bg.last_verified is None or bg.last_verified < str(next_session):
                    bg.issues.append(
                        f"missed required session {next_session} "
                        f"(no verified row; now_et={et:%Y-%m-%d %H:%M})"
                    )
        if bg.issues:
            bg.status = BLOCKED
        elif bg.verified >= target:
            bg.status = PASS
        else:
            bg.status = WAIT
        if not required and bg.status == WAIT and bg.verified == 0:
            bg.warnings.append("optional book has no verified dates yet")
        report.books.append(bg)

    if report.issues or any(b.status == BLOCKED for b in report.books):
        report.status = BLOCKED
    elif any(b.status == WAIT for b in report.books if b.required):
        report.status = WAIT
    elif all(b.status == PASS for b in report.books if b.required):
        report.status = PASS
    else:
        report.status = WAIT
    return report


def _write_snapshot(report: GateReport, path: Path) -> None:
    """Write the machine-readable gate snapshot atomically for a second automation."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


def _append_log(report: GateReport, path: Path) -> None:
    """Append one compact JSONL gate record; never includes secrets or rows."""
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "now": report.now,
        "session": report.session,
        "status": report.status,
        "books": [
            {
                "book": b.book,
                "market": b.market,
                "status": b.status,
                "verified": b.verified,
                "target": b.target,
                "next": b.next_required,
                "remaining": b.remaining_required,
                "eta": b.eta,
            }
            for b in report.books
        ],
    }
    with path.open("a") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")


def _write_status(report: GateReport, path: Path) -> None:
    lines = [
        "# Forward gate status",
        "",
        f"Generated: {report.now}",
        f"Session: {report.session}",
        f"Overall: **{report.status}**",
        "",
        "| book | market | status | verified | next required | ETA 5/5 (no misses) | equity | drawdown | data | reconcile |",
        "|---|---|---|---:|---|---:|---:|---:|---|---|",
    ]
    for b in report.books:
        lines.append(
            f"| {b.book} | {b.market} | {b.status} | {b.verified}/{b.target} | "
            f"{b.next_required} | {b.eta or 'n/a'} | {_fmt(b.equity)} | "
            f"{_fmt_pct(b.drawdown)} | "
            f"{'fresh' if b.data_fresh else 'STALE'} | {b.reconcile} |"
        )
    lines += ["", "## Required sessions", ""]
    lines += [
        "| book | market | next | remaining | ETA 5/5 (no misses) |",
        "|---|---|---|---|---|",
    ]
    for b in report.books:
        lines.append(
            f"| {b.book} | {b.market} | {b.next_required} | "
            f"{', '.join(b.remaining_required) or 'none'} | {b.eta or 'n/a'} |"
        )
    lines += ["", "## Checks", ""]
    for b in report.books:
        lines.append(f"### {b.book}/{b.market}")
        lines.append(
            f"- verified: {b.verified}/{b.target}; close-valid paper dates: "
            f"{b.close_valid}; rejected audit rows: {b.rejected}; "
            f"last verified: {b.last_verified or 'none'}; last paper: {b.last_paper or 'none'}"
        )
        lines.append(
            f"- next required: {b.next_required}; ETA 5/5 (no misses): "
            f"{b.eta or 'n/a'}; remaining: "
            f"{', '.join(b.remaining_required) or 'none'}; data last: "
            f"{b.data_last or 'none'}; equity: {_fmt(b.equity)} "
            f"({b.equity_source}); drawdown: {_fmt_pct(b.drawdown)}"
        )
        for issue in b.issues:
            lines.append(f"- **BLOCKED:** {issue}")
        for warn in b.warnings:
            lines.append(f"- WARN: {warn}")
        if b.new_verified:
            lines.append(f"- NEW VERIFIED: {', '.join(b.new_verified)}")
    if report.issues:
        lines += ["", "## Global blockers", ""] + [f"- {x}" for x in report.issues]
    if report.lockbox:
        lines += ["", "## Proof lockbox", ""]
        lock_path = str(report.lockbox.get("path") or "")
        try:
            lock_path = str(Path(lock_path).resolve().relative_to(ROOT.resolve()))
        except ValueError:
            pass
        lines.append(f"- path: `{lock_path}`")
        lines.append(
            f"- records: {report.lockbox.get('records', 0)}; "
            f"last: {report.lockbox.get('last') or 'none'}; "
            f"chain: {report.lockbox.get('chain', '?')}"
        )
        new = report.lockbox.get("appended") or []
        skipped = report.lockbox.get("skipped") or []
        if new:
            lines.append(f"- appended this run: {', '.join(new)}")
        if skipped:
            lines.append(f"- already locked: {', '.join(skipped)}")
        lines.append(
            "- the lockbox records hashes and a hash chain; it never changes "
            "`paper_audit.csv` or a paper row."
        )
    lines += [
        "",
        "Only distinct verified dates in `results/paper_audit.csv` count. "
        "US, TECH and tax gates are separate. This file is a report, not a "
        "fabricated audit row.",
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text("\n".join(lines))
    tmp.replace(path)


def _verification_command(lockbox_path: Path) -> str:
    try:
        shown = lockbox_path.resolve().relative_to(ROOT.resolve())
    except ValueError:
        shown = lockbox_path
    return (
        f"uv run python scripts/forward_gate.py --check --write-status --lock {shown}"
    )


def _lock_report(
    report: GateReport,
    *,
    lockbox_path: Path,
    results_dir: Path,
    state_dir: Path | None,
    require_state: bool = False,
) -> proof.AppendResult:
    """Append verified dates from an already-computed gate report."""
    result = proof.append_from_report(
        report,
        results_dir=results_dir,
        state_dir=state_dir or (ROOT / "state" / "live"),
        lockbox=lockbox_path,
        repo_root=ROOT,
        now=report.now,
        command=_verification_command(lockbox_path),
        require_state=require_state,
    )
    report.lockbox = result.to_dict()
    try:
        report.lockbox["path"] = str(lockbox_path.resolve().relative_to(ROOT.resolve()))
    except ValueError:
        pass
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--check", action="store_true", help="automation exit-code mode")
    ap.add_argument("--write-status", action="store_true")
    ap.add_argument(
        "--snapshot",
        default=None,
        help="write an atomic JSON gate snapshot for a second automation",
    )
    ap.add_argument(
        "--log",
        default=None,
        help="append one compact JSONL gate record (no secrets)",
    )
    ap.add_argument(
        "--lock",
        action="store_true",
        help="append newly verified sessions to the proof lockbox",
    )
    ap.add_argument(
        "--lockbox",
        default=None,
        help="lockbox JSONL path (default results/forward_proof.jsonl)",
    )
    ap.add_argument(
        "--require-state",
        action="store_true",
        help="refuse a new lock record when the live state file is absent",
    )
    ap.add_argument("--status-file", default=None)
    ap.add_argument("--results-dir", default=None)
    ap.add_argument("--state-dir", default=None)
    ap.add_argument("--target", type=int, default=TARGET_DEFAULT)
    ap.add_argument("--now", default=None, help=argparse.SUPPRESS)
    ap.add_argument(
        "--since", default=None, help="report verified dates after YYYY-MM-DD"
    )
    ap.add_argument("--bench-last", default=None, help=argparse.SUPPRESS)
    ap.add_argument(
        "--book",
        action="append",
        default=None,
        help="only book/market, e.g. monthly/TECH (repeatable)",
    )
    args = ap.parse_args(argv)
    books = None
    if args.book:
        books = []
        for item in args.book:
            if "/" not in item:
                ap.error("--book must be book/market")
            b, m = item.split("/", 1)
            books.append((b, m))
    try:
        report = collect_gate(
            results_dir=Path(args.results_dir) if args.results_dir else None,
            books=books,
            target=args.target,
            now=args.now,
            since=args.since,
            bench_last=args.bench_last,
            state_dir=Path(args.state_dir) if args.state_dir else None,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}")
        return EXIT_ERROR
    results_dir = Path(args.results_dir) if args.results_dir else ROOT / "results"
    lockbox_path = (
        Path(args.lockbox) if args.lockbox else results_dir / proof.DEFAULT_NAME
    )
    if args.lock:
        try:
            _lock_report(
                report,
                lockbox_path=lockbox_path,
                results_dir=results_dir,
                state_dir=Path(args.state_dir) if args.state_dir else None,
                require_state=args.require_state,
            )
        except proof.ProofError as exc:
            print(f"LOCKBOX BLOCKED: {exc}")
            return EXIT_BLOCKED
    if args.write_status:
        path = (
            Path(args.status_file)
            if args.status_file
            else (
                Path(args.results_dir) / "forward_gate.md"
                if args.results_dir
                else ROOT / "results" / "forward_gate.md"
            )
        )
        _write_status(report, path)
        report.status_path = str(path)
    if args.snapshot:
        snapshot_path = Path(args.snapshot)
        _write_snapshot(report, snapshot_path)
        print(f"snapshot: {snapshot_path}", file=sys.stderr)
    if args.log:
        log_path = Path(args.log)
        _append_log(report, log_path)
        print(f"log: {log_path}", file=sys.stderr)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        print("\n".join(report.lines()))
        if report.status_path:
            print(f"status: {report.status_path}")
        if args.check:
            print(
                f"CHECK {report.status}: "
                + ("no blocked checks" if report.status != BLOCKED else "blocked")
            )
    if report.status == PASS:
        return EXIT_OK
    if report.status == BLOCKED:
        return EXIT_BLOCKED
    return EXIT_WAIT


if __name__ == "__main__":
    raise SystemExit(main())
