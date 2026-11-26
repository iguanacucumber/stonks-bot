"""Read dashboard inputs without running the engine or touching the network."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import csv
import json
import math
from pathlib import Path
import re
from typing import Any

from bot import data, markets
from bot.live import calendar
from bot.live.state import LiveState

LEVELS = {"OK": 0, "INFO": 1, "WARN": 2, "FAIL": 3}


def _f(value: Any, default: float | None = None) -> float | None:
    try:
        out = float(value)
        return out if math.isfinite(out) else default
    except (TypeError, ValueError):
        return default


def _bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "ok"}


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text())


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as fh:
        return list(csv.DictReader(fh))


def _last_bar(root: Path, ticker: str) -> str | None:
    path = root / "cache" / "bars" / f"{ticker.replace('/', '_')}.csv"
    if not path.exists():
        return None
    try:
        rows = _read_csv(path)
        dates = [str(r.get("date", "")) for r in rows if r.get("date")]
        return max(dates) if dates else None
    except Exception:  # noqa: BLE001
        return None


@dataclass
class Finding:
    level: str
    code: str
    message: str
    source: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "code": self.code,
            "message": self.message,
            "source": self.source,
        }


@dataclass
class Gate:
    verified: int = 0
    required: int = 5
    text: str = "WAIT 0/5 verified"
    source: str = "none"

    @property
    def passed(self) -> bool:
        return self.verified >= self.required

    def to_dict(self) -> dict[str, Any]:
        return {
            "verified": self.verified,
            "required": self.required,
            "text": self.text,
            "passed": self.passed,
            "source": self.source,
        }


@dataclass
class BookSnapshot:
    book: str
    market: str
    state_file: str = ""
    status: str = "unknown"
    last_session: str | None = None
    next_session: str | None = None
    updated_at: str | None = None
    last_run: dict[str, Any] | None = None
    equity: float | None = None
    cash: float | None = None
    gross: float | None = None
    currency: str = ""
    equity_eur: float | None = None
    cash_eur: float | None = None
    gross_eur: float | None = None
    drawdown: float | None = None
    peak: float | None = None
    risk_off: bool = False
    risk_reason: str | None = None
    targets: dict[str, float] = field(default_factory=dict)
    positions: list[dict[str, Any]] = field(default_factory=list)
    open_orders: list[dict[str, Any]] = field(default_factory=list)
    pending_plan: dict[str, Any] | None = None
    gate: Gate = field(default_factory=Gate)
    data_fresh: bool | None = None
    last_bar: str | None = None
    last_mark: str | None = None
    fx_rate: float | None = None
    fx_date: str | None = None
    ledger_rows: int = 0
    reconcile: str = "unknown"
    warnings: list[str] = field(default_factory=list)
    source: str = "state"

    def to_dict(self) -> dict[str, Any]:
        return {
            "book": self.book,
            "market": self.market,
            "state_file": self.state_file,
            "status": self.status,
            "last_session": self.last_session,
            "next_session": self.next_session,
            "updated_at": self.updated_at,
            "last_run": self.last_run,
            "equity": self.equity,
            "cash": self.cash,
            "gross": self.gross,
            "currency": self.currency,
            "equity_eur": self.equity_eur,
            "cash_eur": self.cash_eur,
            "gross_eur": self.gross_eur,
            "drawdown": self.drawdown,
            "peak": self.peak,
            "risk_off": self.risk_off,
            "risk_reason": self.risk_reason,
            "targets": self.targets,
            "positions": self.positions,
            "open_orders": self.open_orders,
            "pending_plan": self.pending_plan,
            "gate": self.gate.to_dict(),
            "data_fresh": self.data_fresh,
            "last_bar": self.last_bar,
            "last_mark": self.last_mark,
            "fx_rate": self.fx_rate,
            "fx_date": self.fx_date,
            "ledger_rows": self.ledger_rows,
            "reconcile": self.reconcile,
            "warnings": self.warnings,
            "source": self.source,
        }


@dataclass
class EvalSummary:
    baseline: list[dict[str, Any]] = field(default_factory=list)
    data_quality: dict[str, Any] = field(default_factory=dict)
    survivorship: dict[str, Any] = field(default_factory=dict)
    headline: dict[str, Any] = field(default_factory=dict)
    trials: int | None = None
    files: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "baseline": self.baseline,
            "data_quality": self.data_quality,
            "survivorship": self.survivorship,
            "headline": self.headline,
            "trials": self.trials,
            "files": self.files,
            "warnings": self.warnings,
        }


@dataclass
class DashboardData:
    generated_at: str
    session: str | None
    next_session: str | None
    mode: str
    books: list[BookSnapshot] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    eval: EvalSummary = field(default_factory=EvalSummary)
    forward: dict[str, Any] = field(default_factory=dict)
    sources: list[str] = field(default_factory=list)
    synthetic: bool = False

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.level in {"WARN", "FAIL"}]

    @property
    def failures(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "FAIL"]

    @property
    def overall(self) -> str:
        if self.failures:
            return "FAIL"
        if self.warnings:
            return "WARN"
        return "OK"

    @property
    def exit_code(self) -> int:
        return 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "session": self.session,
            "next_session": self.next_session,
            "mode": self.mode,
            "overall": self.overall,
            "synthetic": self.synthetic,
            "books": [b.to_dict() for b in self.books],
            "findings": [f.to_dict() for f in self.findings],
            "eval": self.eval.to_dict(),
            "forward": self.forward,
            "sources": self.sources,
        }


def _norm_header(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def parse_forward_status(path: str | Path) -> tuple[dict[str, Any], list[str]]:
    """Parse the human table written by scripts/forward_daily.py.

    Returns ``(data, warnings)``.  A malformed file is a warning, never a crash.
    """
    path = Path(path)
    out: dict[str, Any] = {
        "generated": None,
        "session": None,
        "mode": None,
        "overall": None,
        "books": [],
        "path": str(path),
    }
    warnings: list[str] = []
    if not path.exists():
        return out, ["forward_status.md is missing"]
    try:
        lines = path.read_text().splitlines()
    except Exception as exc:  # noqa: BLE001
        return out, [f"forward_status.md unreadable: {exc}"]
    in_table = False
    headers: list[str] = []
    for line in lines:
        clean = line.strip()
        if clean.startswith("Generated:"):
            out["generated"] = clean.split(":", 1)[1].strip()
        elif clean.startswith("Session:"):
            out["session"] = clean.split(":", 1)[1].strip()
        elif clean.startswith("Mode:"):
            out["mode"] = clean.split(":", 1)[1].strip()
        elif clean.startswith("Overall:"):
            out["overall"] = clean.split(":", 1)[1].strip().replace("*", "")
        elif clean.startswith("| book |") or clean.startswith("|book|"):
            in_table = True
            headers = [_norm_header(x) for x in clean.strip("|").split("|")]
        elif in_table and clean.startswith("|"):
            cells = [
                x.strip().replace("**", "").replace("`", "")
                for x in clean.strip("|").split("|")
            ]
            if not cells or all(set(x) <= {"-", ":"} for x in cells):
                continue
            row = dict(zip(headers, cells))
            if row.get("book") and row.get("market"):
                out["books"].append(row)
        elif in_table and clean and not clean.startswith("|"):
            in_table = False
    if not out["books"]:
        warnings.append("forward_status.md has no book table")
    return out, warnings


def _gate_from_text(text: str | None, source: str = "forward_status.md") -> Gate:
    if not text:
        return Gate(source=source)
    cleaned = str(text).replace("*", "").replace("`", "")
    match = re.search(r"(WAIT|PASS)\s+(\d+)\s*/\s*(\d+)", cleaned, re.I)
    if match:
        n, total = int(match.group(2)), int(match.group(3))
        return Gate(n, total, cleaned, source)
    if cleaned.upper().startswith("OK"):
        return Gate(5, 5, cleaned, source)
    return Gate(0, 5, cleaned, source)


def gates_from_audit(
    path: str | Path, required: int = 5
) -> tuple[dict[tuple[str, str], Gate], list[str]]:
    path = Path(path)
    if not path.exists():
        return {}, ["paper_audit.csv is missing"]
    try:
        rows = _read_csv(path)
    except Exception as exc:  # noqa: BLE001
        return {}, [f"paper_audit.csv unreadable: {exc}"]
    by_key: dict[tuple[str, str], set[str]] = {}
    for i, row in enumerate(rows):
        book = str(row.get("book", "")).strip()
        market = str(row.get("market", "")).strip()
        if not book or not market:
            continue
        verified = _bool(row.get("verified"))
        if not verified:
            continue
        date = str(row.get("date", "")).strip() or f"row-{i}"
        by_key.setdefault((book, market), set()).add(date)
    gates = {
        key: Gate(
            len(dates),
            required,
            f"PASS {len(dates)}/{required} verified",
            "paper_audit.csv",
        )
        if len(dates) >= required
        else Gate(
            len(dates),
            required,
            f"WAIT {len(dates)}/{required} verified",
            "paper_audit.csv",
        )
        for key, dates in by_key.items()
    }
    # Books with no verified rows still have a visible WAIT gate.
    for row in rows:
        key = (str(row.get("book", "")).strip(), str(row.get("market", "")).strip())
        if key[0] and key[1] and key not in gates:
            gates[key] = Gate(
                0, required, f"WAIT 0/{required} verified", "paper_audit.csv"
            )
    return gates, []


def _eval_summary(root: Path) -> tuple[EvalSummary, list[str]]:
    results = root / "results"
    ev = EvalSummary()
    warnings: list[str] = []
    files = {
        "baseline": results / "eval_baseline.json",
        "headline": results / "eval_headline.json",
        "data_quality": results / "eval_data_quality.json",
        "survivorship": results / "eval_survivorship.json",
        "trials": results / "eval_trials.csv",
    }
    for key, path in files.items():
        ev.files[key] = str(path)
        if not path.exists():
            warnings.append(f"eval {key} is missing")
    try:
        if files["baseline"].exists():
            raw = _read_json(files["baseline"])
            ev.baseline = list(raw.get("rows") or [])
            if raw.get("failures"):
                warnings.append(f"eval baseline failures: {raw['failures']}")
        if files["headline"].exists():
            ev.headline = _read_json(files["headline"])
        if files["data_quality"].exists():
            ev.data_quality = _read_json(files["data_quality"])
            if ev.data_quality.get("ok") is False:
                warnings.append("eval data-quality audit is not OK")
        if files["survivorship"].exists():
            ev.survivorship = _read_json(files["survivorship"])
        if files["trials"].exists():
            ev.trials = len(_read_csv(files["trials"]))
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"eval JSON unreadable: {exc}")
    ev.warnings = warnings
    return ev, warnings


def _state_book(
    path: Path,
    root: Path,
    session: str | None,
    gates: dict[tuple[str, str], Gate],
    forward_rows: dict[tuple[str, str], dict[str, Any]],
) -> BookSnapshot:
    raw = _read_json(path)
    state = LiveState.from_dict(raw)
    book, market = state.book, state.market
    ledger_path = path.with_suffix(".csv")
    rows: list[dict[str, str]] = []
    warnings: list[str] = []
    if ledger_path.exists():
        try:
            rows = _read_csv(ledger_path)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"ledger unreadable: {exc}")
    else:
        warnings.append("ledger CSV is missing")
    last = rows[-1] if rows else {}
    last_session = (
        state.last_session
        or last.get("session")
        or (max(state.runs) if state.runs else None)
    )
    try:
        next_session = (
            str(calendar.next_session(last_session)) if last_session else None
        )
    except Exception:  # noqa: BLE001
        next_session = None
    pending = state.pending_plan
    open_orders = [
        {
            "order_id": o.order_id,
            "ticker": o.ticker,
            "side": o.side,
            "qty": o.qty,
            "ref_price": o.ref_price,
            "status": o.status,
        }
        for o in (pending.orders if pending is not None else [])
        if o.status == "PENDING"
    ]
    targets = state.target_weights
    if last.get("targets"):
        try:
            parsed = json.loads(last["targets"])
            if isinstance(parsed, dict) and parsed:
                targets = {str(k): float(v) for k, v in parsed.items()}
        except Exception:  # noqa: BLE001
            warnings.append("last ledger targets are not valid JSON")
    marks = {
        t: float(p.last_price)
        for t, p in state.portfolio.positions.items()
        if p.last_price is not None
    }
    equity = _f(last.get("equity"))
    if equity is None:
        equity = (
            state.portfolio.equity(marks)
            if state.portfolio.positions
            else state.portfolio.cash
        )
    cash = _f(last.get("cash"), state.portfolio.cash)
    gross = _f(last.get("gross"))
    if gross is None and equity and equity > 0:
        gross = (
            sum(
                abs(p.qty) * (p.last_price or 0.0)
                for p in state.portfolio.positions.values()
                if p.active
            )
            / equity
        )
    drawdown = _f(last.get("drawdown"))
    if drawdown is None and equity and state.peak_equity:
        drawdown = equity / state.peak_equity - 1.0
    last_bar = None
    bench = None
    try:
        bench = markets.MARKETS[market]["bench"]
    except Exception:  # noqa: BLE001
        bench = "SPY"
    last_bar = _last_bar(root, str(bench)) if bench else None
    data_fresh: bool | None = None
    if last.get("data_fresh") not in (None, ""):
        data_fresh = _bool(last.get("data_fresh"))
    elif last_session and last_bar:
        data_fresh = last_bar == last_session
    fx_rate = state.last_fx_rate
    fx_date = state.last_fx_date
    if state.currency != "EUR" and fx_rate is None:
        warnings.append("FX rate is missing; EUR columns are unavailable")
    if last_session and last_bar and last_bar != last_session:
        warnings.append(f"benchmark stale: last {last_bar}, session {last_session}")
    if state.blocked:
        warnings.append(f"state blocked: {state.blocked.get('reason')}")
    if pending is not None and last_session and pending.fill_session < last_session:
        warnings.append(f"pending plan overdue: {pending.fill_session}")
    if not rows:
        warnings.append("no ledger rows; state has not been reconciled")
    last_run = None
    if state.runs:
        run_session = max(state.runs)
        rec = state.runs[run_session]
        last_run = {
            "session": run_session,
            "plan_id": rec.get("plan_id"),
            "complete": bool(rec.get("complete")),
            "blocked": bool(rec.get("blocked")),
        }
    elif last_session:
        last_run = {
            "session": last_session,
            "plan_id": state.last_plan_id,
            "complete": False,
            "blocked": bool(state.blocked),
        }
    key = (book, market)
    gate = gates.get(key)
    if gate is None and key in forward_rows:
        gate = _gate_from_text(
            forward_rows[key].get("verified_gate"), "forward_status.md"
        )
    if gate is None:
        gate = Gate(source="none")
    status = "blocked" if state.blocked else ("risk_off" if state.risk_off else "ok")
    if not rows:
        status = "degraded"
    return BookSnapshot(
        book=book,
        market=market,
        state_file=str(path),
        status=status,
        last_session=last_session,
        next_session=next_session,
        updated_at=state.updated_at,
        last_run=last_run,
        equity=equity,
        cash=cash,
        gross=gross,
        currency=state.currency,
        equity_eur=state.last_equity_eur,
        cash_eur=state.last_cash_eur,
        gross_eur=state.last_gross_eur,
        drawdown=drawdown,
        peak=state.peak_equity,
        risk_off=state.risk_off,
        risk_reason=state.risk_reason,
        targets={str(k): float(v) for k, v in targets.items()},
        positions=[
            {
                "ticker": t,
                "qty": p.qty,
                "avg_cost": p.avg_cost,
                "last_price": p.last_price,
                "value": p.qty * (p.last_price or 0.0),
            }
            for t, p in sorted(state.portfolio.positions.items())
            if p.active
        ],
        open_orders=open_orders,
        pending_plan=pending.to_dict() if pending is not None else None,
        gate=gate,
        data_fresh=data_fresh,
        last_bar=last_bar,
        last_mark=state.last_mark_session,
        fx_rate=fx_rate,
        fx_date=fx_date,
        ledger_rows=len(rows),
        reconcile=str(forward_rows.get(key, {}).get("reconcile", "unchecked")),
        warnings=warnings,
        source="state",
    )


def _placeholder_book(
    book: str,
    market: str,
    session: str | None,
    row: dict[str, Any],
    gate: Gate,
) -> BookSnapshot:
    warnings = []
    if str(row.get("live", "")).lower() not in {"ok", ""}:
        warnings.append(f"forward status: {row.get('live')}")
    if str(row.get("reconcile", "")).upper() not in {"OK", ""}:
        warnings.append(f"reconcile: {row.get('reconcile')}")
    return BookSnapshot(
        book=book,
        market=market,
        status=str(row.get("live", "unknown")),
        last_session=session,
        next_session=str(calendar.next_session(session)) if session else None,
        last_run={"session": session, "complete": None, "blocked": None}
        if session
        else None,
        gate=gate,
        reconcile=str(row.get("reconcile", "unknown")),
        data_fresh=None,
        warnings=warnings,
        source="forward_status",
    )


def collect_dashboard(
    root: str | Path,
    *,
    state_dir: str | Path | None = None,
    results_dir: str | Path | None = None,
    now: str | datetime | None = None,
) -> DashboardData:
    """Collect all dashboard inputs; an absent/bad file becomes a warning."""
    root = Path(root).resolve()
    results = Path(results_dir).resolve() if results_dir else root / "results"
    state = Path(state_dir).resolve() if state_dir else root / "state" / "live"
    if now is None:
        now_dt = datetime.now(timezone.utc)
    elif isinstance(now, datetime):
        now_dt = now.astimezone(timezone.utc)
    else:
        now_dt = datetime.fromisoformat(str(now).replace("Z", "+00:00"))
    try:
        session = str(data.target_session(now_dt).date())
    except Exception:  # noqa: BLE001
        session = None
    try:
        next_session = str(calendar.next_session(session)) if session else None
    except Exception:  # noqa: BLE001
        next_session = None
    findings: list[Finding] = []
    forward, fwarn = parse_forward_status(results / "forward_status.md")
    findings += [
        Finding("WARN", "forward_status", w, str(results / "forward_status.md"))
        for w in fwarn
    ]
    if str(forward.get("overall", "")).upper() == "ATTENTION":
        findings.append(
            Finding(
                "WARN",
                "forward_overall",
                "forward_status.md reports ATTENTION",
                str(results / "forward_status.md"),
            )
        )
    gates, awarn = gates_from_audit(results / "paper_audit.csv")
    findings += [
        Finding("WARN", "paper_audit", w, str(results / "paper_audit.csv"))
        for w in awarn
    ]
    ev, ewarn = _eval_summary(root)
    findings += [Finding("WARN", "eval", w, "results/eval_*") for w in ewarn]
    if not state.exists():
        findings.append(
            Finding(
                "WARN", "state_dir", f"no live state directory: {state}", str(state)
            )
        )
    forward_rows = {
        (str(r.get("book", "")), str(r.get("market", ""))): r
        for r in forward.get("books", [])
    }
    books: list[BookSnapshot] = []
    seen: set[tuple[str, str]] = set()
    if state.exists():
        for path in sorted(state.glob("*.json")):
            try:
                book = _state_book(path, root, session, gates, forward_rows)
            except Exception as exc:  # noqa: BLE001
                findings.append(
                    Finding("WARN", "state_corrupt", f"{path.name}: {exc}", str(path))
                )
                books.append(
                    BookSnapshot(
                        book="?",
                        market="?",
                        state_file=str(path),
                        status="corrupt",
                        warnings=[f"state unreadable: {exc}"],
                        source="corrupt",
                    )
                )
                continue
            books.append(book)
            seen.add((book.book, book.market))
            for warning in book.warnings:
                findings.append(
                    Finding(
                        "WARN",
                        "book",
                        f"{book.book}/{book.market}: {warning}",
                        book.state_file,
                    )
                )
            findings.append(
                Finding(
                    "INFO",
                    "gate",
                    f"{book.book}/{book.market}: {book.gate.text}",
                    book.gate.source,
                )
            )
    for key, row in forward_rows.items():
        if key in seen or not key[0]:
            continue
        gate = _gate_from_text(row.get("verified_gate"), "forward_status.md")
        books.append(
            _placeholder_book(key[0], key[1], forward.get("session"), row, gate)
        )
        findings.append(
            Finding(
                "INFO", "gate", f"{key[0]}/{key[1]}: {gate.text}", "forward_status.md"
            )
        )
    if not books:
        findings.append(
            Finding(
                "WARN",
                "no_books",
                "no live state files and no forward-status books; dashboard is empty",
                str(state),
            )
        )
    # Global cache freshness is visible even when no state exists.
    last_spy = _last_bar(root, "SPY")
    if last_spy is None:
        findings.append(
            Finding("WARN", "cache", "SPY bar is missing from cache/bars", "cache/bars")
        )
    elif session and last_spy != session:
        findings.append(
            Finding(
                "WARN",
                "stale_data",
                f"SPY last bar {last_spy} != target session {session}",
                "cache/bars/SPY.csv",
            )
        )
    for book in books:
        if book.source == "forward_status":
            book.last_bar = last_spy
            book.data_fresh = (
                bool(session and last_spy == session) if last_spy else None
            )
        for warning in book.warnings:
            # State warnings were already added above; forward placeholders are new.
            if book.source != "state":
                findings.append(
                    Finding(
                        "WARN",
                        "book",
                        f"{book.book}/{book.market}: {warning}",
                        book.source,
                    )
                )
    sources = [
        str(state),
        str(results / "forward_status.md"),
        str(results / "paper_audit.csv"),
        "results/eval_*.json",
    ]
    return DashboardData(
        generated_at=now_dt.isoformat(timespec="seconds"),
        session=session,
        next_session=next_session,
        mode=str(forward.get("mode") or "unknown"),
        books=books,
        findings=findings,
        eval=ev,
        forward=forward,
        sources=sources,
    )


def _demo_gate(n: int, required: int = 5) -> Gate:
    return Gate(n, required, f"WAIT {n}/{required} verified", "synthetic demo")


def demo_dashboard(now: str | datetime | None = None) -> DashboardData:
    """A clearly labelled synthetic page used for the committed example."""
    if now is None:
        stamp = datetime.now(timezone.utc)
    elif isinstance(now, datetime):
        stamp = now.astimezone(timezone.utc)
    else:
        stamp = datetime.fromisoformat(str(now).replace("Z", "+00:00"))
    session = "2026-11-25"
    books = [
        BookSnapshot(
            book="monthly",
            market="US",
            state_file="synthetic/monthly_us.json",
            status="ok",
            last_session=session,
            next_session="2026-11-27",
            updated_at="2026-11-25T22:00:00+00:00",
            last_run={
                "session": session,
                "plan_id": "demo-plan",
                "complete": True,
                "blocked": False,
            },
            equity=12_480.0,
            cash=-4_200.0,
            gross=1.66,
            currency="USD",
            equity_eur=11_356.8,
            cash_eur=-3_822.0,
            gross_eur=18_875.0,
            drawdown=-0.124,
            peak=14_246.0,
            risk_off=False,
            targets={"SMH": 1.45, "NVDA": 0.32, "MSFT": 0.18},
            positions=[
                {
                    "ticker": "SMH",
                    "qty": 18.0,
                    "avg_cost": 410.0,
                    "last_price": 466.3,
                    "value": 8_393.4,
                },
                {
                    "ticker": "NVDA",
                    "qty": 11.0,
                    "avg_cost": 145.0,
                    "last_price": 176.0,
                    "value": 1_936.0,
                },
                {
                    "ticker": "MSFT",
                    "qty": 3.0,
                    "avg_cost": 420.0,
                    "last_price": 432.0,
                    "value": 1_296.0,
                },
            ],
            open_orders=[
                {"ticker": "SMH", "side": "BUY", "qty": 1.2, "status": "PENDING"},
            ],
            gate=_demo_gate(3),
            data_fresh=True,
            last_bar=session,
            last_mark=session,
            fx_rate=0.91,
            fx_date="2026-11-24",
            ledger_rows=42,
            reconcile="OK",
            source="synthetic",
        ),
        BookSnapshot(
            book="monthly",
            market="TECH",
            state_file="synthetic/monthly_tech.json",
            status="risk_off",
            last_session=session,
            next_session="2026-11-27",
            updated_at="2026-11-25T22:00:00+00:00",
            last_run={
                "session": session,
                "plan_id": "demo-tech",
                "complete": True,
                "blocked": False,
            },
            equity=9_120.0,
            cash=-2_900.0,
            gross=1.32,
            currency="USD",
            equity_eur=8_299.2,
            drawdown=-0.216,
            peak=11_630.0,
            risk_off=True,
            risk_reason="trend",
            targets={"SMH": 0.66},
            positions=[
                {
                    "ticker": "SMH",
                    "qty": 12.0,
                    "avg_cost": 405.0,
                    "last_price": 466.3,
                    "value": 5_595.6,
                }
            ],
            gate=_demo_gate(1),
            data_fresh=True,
            last_bar=session,
            last_mark=session,
            fx_rate=0.91,
            fx_date="2026-11-24",
            ledger_rows=39,
            reconcile="OK",
            source="synthetic",
        ),
    ]
    ev = EvalSummary(
        baseline=[
            {
                "market": "TECH",
                "book": "monthly",
                "native": 51.82,
                "terminal_fx": 57.01,
                "eur_ledger": 52.98,
            },
            {
                "market": "US",
                "book": "monthly",
                "native": 64.41,
                "terminal_fx": 70.86,
                "eur_ledger": 66.17,
            },
            {
                "market": "TECH",
                "book": "tax",
                "native": 46.26,
                "terminal_fx": 50.89,
                "eur_ledger": 48.80,
            },
        ],
        data_quality={
            "ok": True,
            "hard_failures": [],
            "warning_counts": {"ohlc_inconsistent": 122},
        },
        survivorship={
            "facts": {"n_static": 49, "n_in_sp500_at_start": 28},
            "us_missing": {
                "missing": 128,
                "unique_members": 789,
                "missing_share": 0.162,
            },
        },
        headline={
            "accounting_modes": [{"accounting_mode": "eur_ledger", "multiple": 52.98}]
        },
        trials=14,
        files={"demo": "synthetic"},
    )
    findings = [
        Finding(
            "WARN",
            "synthetic",
            "SYNTHETIC DEMO — not live state or a forward result",
            "demo",
        ),
        Finding("INFO", "gate", "monthly/US: WAIT 3/5 verified", "demo"),
        Finding("INFO", "gate", "monthly/TECH: WAIT 1/5 verified", "demo"),
    ]
    return DashboardData(
        generated_at=stamp.isoformat(timespec="seconds"),
        session=session,
        next_session="2026-11-27",
        mode="paper (synthetic example)",
        books=books,
        findings=findings,
        eval=ev,
        forward={
            "generated": stamp.isoformat(timespec="seconds"),
            "session": session,
            "mode": "paper",
            "overall": "OK",
            "books": [],
        },
        sources=["synthetic demo (no files read)"],
        synthetic=True,
    )
