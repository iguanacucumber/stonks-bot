"""Read-only, paper-safe real-money readiness preflight.

The command answers one question: *may this exact live config be used with real
money?* It never connects to a broker, never reads a secret, never submits an order
and never writes state. In this paper-only build the execution-path check is a
permanent blocker, so the command must fail closed even if every evidence file is
hand-written.

Checks:
  * approval artifact present, unexpired, in scope and hash-matched;
  * forward gate PASS 5/5 for the selected book/market, cross-checked against the
    verified dates in ``results/paper_audit.csv``;
  * ``results/release_check.md`` overall PASS;
  * real-account environment names are present and the id is ``U...``, not ``DU``;
  * read-only is explicitly true;
  * live risk rails are present and no looser than the shipped defaults;
  * live state has no blocked/pending-cut/overdue plan or config mismatch;
  * no real-money execution path exists in this build.
"""

from __future__ import annotations

import csv
import json
import math
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .approval import approval_issues
from .config import ROOT, LiveConfig

DEFAULT_FORWARD_GATE = ROOT / "results" / "forward_gate.md"
DEFAULT_AUDIT = ROOT / "results" / "paper_audit.csv"
DEFAULT_RELEASE_CHECK = ROOT / "results" / "release_check.md"
FORWARD_TARGET = 5
_TRUTHY = {"true", "1", "yes"}
_EXIT_BLOCKED = 2


@dataclass(frozen=True)
class PreflightCheck:
    name: str
    ok: bool
    detail: str
    blocking: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ok": self.ok,
            "detail": self.detail,
            "blocking": self.blocking,
        }


@dataclass
class PreflightReport:
    generated_at: str
    book: str
    market: str
    capital: float
    approval_path: str
    checks: list[PreflightCheck] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks if c.blocking)

    @property
    def blockers(self) -> list[str]:
        return [f"{c.name}: {c.detail}" for c in self.checks if c.blocking and not c.ok]

    @property
    def exit_code(self) -> int:
        return 0 if self.ok else _EXIT_BLOCKED

    def add(self, name: str, ok: bool, detail: str, blocking: bool = True) -> None:
        self.checks.append(PreflightCheck(name, bool(ok), detail, blocking))

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "exit_code": self.exit_code,
            "generated_at": self.generated_at,
            "book": self.book,
            "market": self.market,
            "capital": self.capital,
            "approval_path": self.approval_path,
            "blockers": self.blockers,
            "checks": [c.to_dict() for c in self.checks],
        }

    def lines(self) -> list[str]:
        out = [
            f"REAL-MONEY PREFLIGHT {'PASS' if self.ok else 'FAIL'}  "
            f"book={self.book} market={self.market} capital={self.capital:,.2f}"
        ]
        for c in self.checks:
            tag = "OK  " if c.ok else ("FAIL" if c.blocking else "WARN")
            out.append(f"  [{tag}] {c.name}: {c.detail}")
        if self.blockers:
            out.append("")
            out.append(f"BLOCKED ({len(self.blockers)}):")
            out.extend(f"  - {x}" for x in self.blockers)
        else:
            out.append("")
            out.append("PASS: all real-money preflight checks passed.")
        return out


def _now(now: str | datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if isinstance(now, datetime):
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must include a timezone offset")
        return now.astimezone(timezone.utc)
    parsed = datetime.fromisoformat(str(now).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("now must include a timezone offset")
    return parsed.astimezone(timezone.utc)


def _cells(line: str) -> list[str]:
    return [
        x.strip().replace("**", "").replace("`", "")
        for x in line.strip().strip("|").split("|")
    ]


def _norm_header(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def forward_gate_issues(
    path: str | Path,
    book: str,
    market: str,
    *,
    target: int = FORWARD_TARGET,
) -> tuple[bool, str]:
    """Require an explicit PASS n/target row for exactly this book/market."""
    path = Path(path)
    try:
        lines = path.read_text().splitlines()
    except FileNotFoundError:
        return False, f"missing forward gate report: {path}"
    except OSError as exc:
        return False, f"cannot read forward gate report {path}: {exc}"

    headers: list[str] = []
    rows: list[dict[str, str]] = []
    in_table = False
    for line in lines:
        clean = line.strip()
        if clean.startswith("| book |") or clean.startswith("|book|"):
            headers = [_norm_header(x) for x in _cells(clean)]
            in_table = True
            continue
        if in_table and clean.startswith("|"):
            cells = _cells(clean)
            if not cells or all(set(x) <= {"-", ":"} for x in cells):
                continue
            row = dict(zip(headers, cells))
            if row.get("book") and row.get("market"):
                rows.append(row)
        elif in_table and clean and not clean.startswith("|"):
            in_table = False

    matches = [
        row for row in rows if row.get("book") == book and row.get("market") == market
    ]
    if not matches:
        return False, f"no {book}/{market} row in {path.name}"
    row = next((r for r in matches if r.get("status") or r.get("verified")), matches[0])
    status = str(row.get("status", "")).strip().upper()
    match = re.search(r"(\d+)\s*/\s*(\d+)", str(row.get("verified", "")))
    if match is None:
        return False, f"{book}/{market} verified count is unreadable"
    verified, total = int(match.group(1)), int(match.group(2))
    if status != "PASS":
        return (
            False,
            f"{book}/{market} forward gate is {status or 'blank'} "
            f"{verified}/{total}; need PASS {target}/{target}",
        )
    if total != target or verified != target:
        return (
            False,
            f"{book}/{market} forward gate is {verified}/{total}; "
            f"need PASS {target}/{target}",
        )
    return True, f"{book}/{market} forward gate PASS {verified}/{total}"


def audit_verified_dates(
    path: str | Path,
    book: str,
    market: str,
) -> tuple[set[str], list[str]]:
    """Count distinct verified dates in the audit CSV; invalid rows block."""
    path = Path(path)
    try:
        handle = path.open(newline="")
    except FileNotFoundError:
        return set(), [f"missing forward audit: {path}"]
    except OSError as exc:
        return set(), [f"cannot read forward audit {path}: {exc}"]
    dates: set[str] = set()
    issues: list[str] = []
    with handle:
        reader = csv.DictReader(handle)
        required = {"book", "market", "date", "verified"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            return set(), [f"audit is missing columns {sorted(missing)}"]
        for line_no, row in enumerate(reader, start=2):
            if str(row.get("book", "")).strip() != book:
                continue
            if str(row.get("market", "")).strip() != market:
                continue
            if str(row.get("verified", "")).strip().lower() not in _TRUTHY:
                continue
            raw = str(row.get("date", "")).strip()
            try:
                day = date.fromisoformat(raw[:10]).isoformat()
            except ValueError:
                issues.append(f"audit line {line_no} has invalid date {raw!r}")
                continue
            dates.add(day)
    if issues:
        return dates, issues
    return dates, []


def release_check_issues(path: str | Path) -> tuple[bool, str]:
    """Require the recorded release check to be an explicit overall PASS."""
    path = Path(path)
    try:
        text = path.read_text()
    except FileNotFoundError:
        return False, f"missing release check report: {path}"
    except OSError as exc:
        return False, f"cannot read release check {path}: {exc}"
    match = re.search(
        r"(?im)^\s*-\s*Overall:\s*\*\*(PASS|FAIL|WAIT|BLOCKED|ERROR)\*\*",
        text,
    )
    if match is None:
        return False, "release check has no readable overall status"
    status = match.group(1).upper()
    commit = re.search(r"(?im)^\s*-\s*Commit:\s*`?([0-9a-f]+)`?", text)
    generated = re.search(r"(?im)^\s*-\s*Generated:\s*(.+)$", text)
    detail = f"release check {status}"
    if commit:
        detail += f" at {commit.group(1)}"
    if generated:
        detail += f" generated {generated.group(1).strip()}"
    if status != "PASS":
        return False, f"release check overall {status}, need PASS"
    return True, detail


def credentials_issues(env: Mapping[str, str] | None = None) -> tuple[bool, str]:
    """Check only a non-secret real-account id shape; never read a password."""
    e = os.environ if env is None else env
    account = str(e.get("IBKR_REAL_ACCOUNT", "")).strip()
    if not account:
        return (
            False,
            "IBKR_REAL_ACCOUNT is not set; no real-account access is approved",
        )
    upper = account.upper()
    if upper.startswith("DU"):
        return False, "IBKR_REAL_ACCOUNT is a DU paper account, not a real U account"
    if not re.fullmatch(r"U[A-Z0-9]{2,}", upper):
        return False, "IBKR_REAL_ACCOUNT must be a U... real account id"
    return True, "real-account id present and not DU (value not printed)"


def read_only_issues(env: Mapping[str, str] | None = None) -> tuple[bool, str]:
    e = os.environ if env is None else env
    raw = str(e.get("IBKR_REAL_READ_ONLY", "")).strip().lower()
    if raw not in _TRUTHY:
        return (
            False,
            "IBKR_REAL_READ_ONLY must be explicitly true; absence is not read-only",
        )
    return True, "IBKR_REAL_READ_ONLY=true (GET-only inspection mode)"


def risk_rail_issues(cfg: LiveConfig) -> list[str]:
    """The shipped rails, or tighter. A looser/disabled rail is a blocker."""
    issues: list[str] = []
    if not (0 < float(cfg.dd_stop) <= 0.35):
        issues.append(f"dd_stop={cfg.dd_stop!r} must be in (0, 0.35]")
    if not (0 < float(cfg.dd_scale) <= 1.0):
        issues.append(f"dd_scale={cfg.dd_scale!r} must be in (0, 1]")
    if not (0 < float(cfg.max_gross) <= 3.0):
        issues.append(f"max_gross={cfg.max_gross!r} must be in (0, 3]")
    if float(cfg.leverage) > float(cfg.max_gross) + 1e-9:
        issues.append(f"leverage={cfg.leverage!r} > max_gross={cfg.max_gross!r}")
    if not (0 < float(cfg.max_turnover) <= 3.0):
        issues.append(f"max_turnover={cfg.max_turnover!r} must be in (0, 3]")
    if not (0 < int(cfg.max_orders) <= 50):
        issues.append(f"max_orders={cfg.max_orders!r} must be in (0, 50]")
    if float(cfg.min_order_notional) < 25.0:
        issues.append(f"min_order_notional={cfg.min_order_notional!r} must be >= 25")
    if not (0 < float(cfg.max_participation) <= 0.02):
        issues.append(
            f"max_participation={cfg.max_participation!r} must be in (0, 0.02]"
        )
    if not (0 <= int(cfg.max_stale_days) <= 3):
        issues.append(f"max_stale_days={cfg.max_stale_days!r} must be in [0, 3]")
    if not (0 <= int(cfg.max_pending_sessions) <= 5):
        issues.append(
            f"max_pending_sessions={cfg.max_pending_sessions!r} must be in [0, 5]"
        )
    if int(cfg.rebalance_months) < 1:
        issues.append("rebalance_months must be >= 1")
    if float(cfg.trade_band) < 0 or not math.isfinite(float(cfg.trade_band)):
        issues.append(f"trade_band={cfg.trade_band!r} must be finite and >= 0")
    return issues


def state_issues(cfg: LiveConfig, state_path: str | Path | None = None) -> list[str]:
    path = Path(state_path) if state_path is not None else cfg.state_path
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        return [f"live state {path} is unreadable: {exc}"]
    if not isinstance(raw, dict):
        return [f"live state {path} is not a JSON object"]
    issues: list[str] = []
    if str(raw.get("book", cfg.book)) != cfg.book:
        issues.append(f"state book {raw.get('book')!r} != config book {cfg.book!r}")
    if str(raw.get("market", cfg.market)) != cfg.market:
        issues.append(
            f"state market {raw.get('market')!r} != config market {cfg.market!r}"
        )
    signature = raw.get("config_signature")
    if signature is not None and str(signature) != cfg.signature():
        issues.append("state config signature differs from the current config")
    blocked = raw.get("blocked")
    if blocked:
        reason = blocked.get("reason") if isinstance(blocked, dict) else blocked
        session = blocked.get("session") if isinstance(blocked, dict) else "?"
        issues.append(f"state has a blocked plan at {session}: {reason}")
    pending_cut = raw.get("pending_cut")
    if pending_cut:
        reason = (
            pending_cut.get("reason") if isinstance(pending_cut, dict) else pending_cut
        )
        issues.append(f"state has a pending risk cut: {reason}")
    pending = raw.get("pending_plan")
    if isinstance(pending, dict):
        if str(pending.get("status", "")).upper() == "BLOCKED":
            issues.append(
                f"pending plan {pending.get('plan_id')} is BLOCKED: "
                f"{pending.get('blocked_reason')}"
            )
        fill = pending.get("fill_session")
        last = raw.get("last_session")
        if fill and last and str(fill) < str(last):
            issues.append(
                f"pending plan {pending.get('plan_id')} fill session {fill} "
                f"is older than last session {last}"
            )
    return issues


def execution_path_issues() -> tuple[bool, str]:
    """This build has no real-money adapter. Keep the boundary explicit."""
    return (
        False,
        "paper-only build: no real-money broker adapter or order path exists; "
        "the user did not approve live access",
    )


def run_preflight(
    cfg: LiveConfig,
    *,
    approval_path: str | Path,
    forward_gate_path: str | Path = DEFAULT_FORWARD_GATE,
    audit_path: str | Path = DEFAULT_AUDIT,
    release_check_path: str | Path = DEFAULT_RELEASE_CHECK,
    state_path: str | Path | None = None,
    plan: str | Path | dict[str, Any] | None = None,
    now: str | datetime | None = None,
    env: Mapping[str, str] | None = None,
) -> PreflightReport:
    current = _now(now)
    report = PreflightReport(
        generated_at=current.isoformat(timespec="seconds"),
        book=cfg.book,
        market=cfg.market,
        capital=float(cfg.capital),
        approval_path=str(approval_path),
    )

    issues = approval_issues(approval_path, cfg, now=current, plan=plan)
    report.add(
        "approval",
        not issues,
        "valid approval artifact" if not issues else "; ".join(issues),
    )

    ok, detail = forward_gate_issues(forward_gate_path, cfg.book, cfg.market)
    report.add("forward_gate", ok, detail)

    dates, audit_problems = audit_verified_dates(audit_path, cfg.book, cfg.market)
    verified = len(dates)
    if audit_problems:
        report.add("forward_audit", False, "; ".join(audit_problems))
    elif verified < FORWARD_TARGET:
        report.add(
            "forward_audit",
            False,
            f"{cfg.book}/{cfg.market} has {verified}/{FORWARD_TARGET} "
            "distinct verified dates",
        )
    else:
        report.add(
            "forward_audit",
            True,
            f"{cfg.book}/{cfg.market} has {verified} distinct verified dates",
        )

    ok, detail = release_check_issues(release_check_path)
    report.add("release_check", ok, detail)

    ok, detail = credentials_issues(env)
    report.add("credentials_env", ok, detail)

    ok, detail = read_only_issues(env)
    report.add("read_only_default", ok, detail)

    rail_problems = risk_rail_issues(cfg)
    report.add(
        "risk_rails",
        not rail_problems,
        "shipped rails or tighter" if not rail_problems else "; ".join(rail_problems),
    )

    blockers = state_issues(cfg, state_path)
    report.add(
        "pending_blocker",
        not blockers,
        "no blocked/pending/overdue state" if not blockers else "; ".join(blockers),
    )

    ok, detail = execution_path_issues()
    report.add("real_money_execution_path", ok, detail)
    return report
