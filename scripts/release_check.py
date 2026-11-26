#!/usr/bin/env python
"""Release acceptance for the paper-only stonks-bot.

Runs, in order:

1. every ``tests/test_*.py`` script;
2. ``scripts/acceptance.py`` end-to-end;
3. ``scripts/reproduce.py baseline --no-write`` and the EUR-ledger numbers;
4. live dry-run plans for US and TECH;
5. the forward gate from ``results/forward_status.md`` + ``paper_audit.csv``;
6. ``scripts/dashboard.py --check``;
7. a lightweight tracked-file secret scan.

It writes ``results/release_check.md`` unless ``--no-write`` is used.  It does not
tag, publish a GitHub release, send orders, fetch data or use the network.

Examples::

    uv run python scripts/release_check.py
    uv run python scripts/release_check.py --json --no-write
    uv run python scripts/release_check.py --allow-missing-cache --no-write
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from bot.dashboard.collect import gates_from_audit, parse_forward_status  # noqa: E402
from bot.live import calendar  # noqa: E402
import reproduce as reproduce_cli  # noqa: E402

EXPECTED_SNAPSHOT = "2026-11-25"
EXPECTED_EUR = {
    ("US", "monthly"): 66.17,
    ("TECH", "monthly"): 52.98,
    ("US", "tax"): 48.80,
    ("TECH", "tax"): 48.80,
}
EXPECTED_GATES = {
    ("monthly", "US"): 0,
    ("monthly", "TECH"): 1,
    ("tax", "TECH"): 0,
}
TOL = 0.05
SECRET_PATTERNS = (
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    r"\bgh[pousr]_[A-Za-z0-9]{20,}\b",
    r"\bAKIA[0-9A-Z]{16}\b",
    r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b",
    r"(?:STONKS_SMTP_PASSWORD|STONKS_ALERT_TELEGRAM_BOT_TOKEN|IBKR_PAPER_PASSWORD)\s*=\s*[^\s#]+",
)


@dataclass
class Check:
    name: str
    status: str
    detail: str
    seconds: float = 0.0
    command: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "seconds": round(self.seconds, 3),
            "command": self.command,
        }


@dataclass
class ReleaseReport:
    snapshot: str
    commit: str
    generated_at: str
    checks: list[Check] = field(default_factory=list)
    cache_missing: list[str] = field(default_factory=list)
    allow_missing_cache: bool = False

    @property
    def ok(self) -> bool:
        # WAIT is an honest, non-blocking outcome for an incomplete forward
        # gate.  Only FAIL blocks a paper-only release.
        return all(c.status in {"PASS", "SKIP", "WAIT"} for c in self.checks)

    @property
    def exit_code(self) -> int:
        return 0 if self.ok else 2

    @property
    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for c in self.checks:
            out[c.status] = out.get(c.status, 0) + 1
        return out

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "exit_code": self.exit_code,
            "snapshot": self.snapshot,
            "commit": self.commit,
            "generated_at": self.generated_at,
            "cache_missing": self.cache_missing,
            "allow_missing_cache": self.allow_missing_cache,
            "counts": self.counts,
            "checks": [c.to_dict() for c in self.checks],
        }


def cache_missing(root: Path | None = None) -> list[str]:
    root = Path(root) if root is not None else ROOT / "cache"
    missing: list[str] = []
    try:
        symbols = reproduce_cli.required_symbols()
    except Exception:  # noqa: BLE001
        symbols = ["SPY"]
    for sym in symbols:
        path = root / "bars" / f"{sym.replace('/', '_')}.csv"
        if not path.exists() or path.stat().st_size == 0:
            missing.append(f"bars/{sym}")
    for name in ("panel.parquet", "membership.parquet"):
        path = root / name
        if not path.exists() or path.stat().st_size == 0:
            missing.append(name)
    return missing


def _cache_snapshot() -> str:
    path = ROOT / "cache" / "bars" / "QQQ.csv"
    try:
        import pandas as pd

        return str(
            pd.to_datetime(pd.read_csv(path, usecols=["date"])["date"]).max().date()
        )
    except Exception:  # noqa: BLE001
        return "unknown"


def _git_head() -> str:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return proc.stdout.strip() if proc.returncode == 0 else "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


def parse_baseline_numbers(text: str) -> dict[tuple[str, str], dict[str, float]]:
    """Parse the four lines printed by ``reproduce.py baseline``."""
    rows: dict[tuple[str, str], dict[str, float]] = {}
    pattern = re.compile(
        r"^\s*(TECH|US)\s+(monthly|tax)\s+native\s+([\d.]+)x\s+"
        r"terminal-FX\s+([\d.]+)x\s+EUR-ledger\s+([\d.]+)x",
        re.MULTILINE,
    )
    for match in pattern.finditer(text):
        market, book, native, terminal, ledger = match.groups()
        rows[(market, book)] = {
            "native": float(native),
            "terminal_fx": float(terminal),
            "eur_ledger": float(ledger),
        }
    return rows


def parse_gate_text(text: str | None) -> tuple[int, int] | None:
    match = re.search(r"(WAIT|PASS)\s+(\d+)\s*/\s*(\d+)", str(text or ""), re.I)
    if not match:
        return None
    return int(match.group(2)), int(match.group(3))


def check_gate_results(
    forward_path: Path, audit_path: Path, expected: dict[tuple[str, str], int]
) -> tuple[str, str]:
    """Validate gate integrity. An incomplete but honest gate is WAIT, not FAIL.

    Hard failures are limited to an unreadable/missing status table, an
    unparseable count, a denominator other than 5, a count outside 0..5, or a
    positive status count that is not backed by the same number of verified
    unique dates in ``paper_audit.csv``.  A zero count legitimately has no audit
    row: many books have never had a verified session.
    """
    status, status_warnings = parse_forward_status(forward_path)
    gates, audit_warnings = gates_from_audit(audit_path)
    hard = list(status_warnings)
    notes: list[str] = []
    rows = {
        (str(r.get("book", "")), str(r.get("market", ""))): r
        for r in status.get("books", [])
    }
    for key, expected_n in expected.items():
        row = rows.get(key)
        if row is None:
            hard.append(f"forward status missing {key[0]}/{key[1]}")
            continue
        parsed = parse_gate_text(row.get("verified_gate"))
        if parsed is None:
            hard.append(f"{key[0]}/{key[1]} gate unreadable")
            continue
        n, total = parsed
        if total != 5:
            hard.append(f"{key[0]}/{key[1]} gate denominator {total}")
        if n < 0 or n > total:
            hard.append(f"{key[0]}/{key[1]} gate count {n}/{total} out of range")
        audit_gate = gates.get(key)
        audit_n = 0 if audit_gate is None else audit_gate.verified
        if audit_gate is None and n > 0:
            hard.append(f"{key[0]}/{key[1]} positive gate but no paper_audit row")
        elif audit_gate is not None and audit_n != n:
            hard.append(f"{key[0]}/{key[1]} audit {audit_n} != status {n}")
        if n != expected_n:
            notes.append(
                f"{key[0]}/{key[1]} now {n}/{total}, release snapshot {expected_n}/5"
            )
    if audit_warnings:
        # A wholly missing audit file is only acceptable when every expected
        # gate is zero; otherwise the positive branch above already failed.
        if any(
            (parse_gate_text(rows.get(k, {}).get("verified_gate")) or (0, 0))[0] > 0
            for k in expected
        ):
            hard.extend(audit_warnings)
        else:
            notes.extend(audit_warnings)
    detail = (
        ", ".join(
            f"{b}/{m}={n}/{total}"
            for (b, m), r in sorted(rows.items())
            for n, total in [parse_gate_text(r.get("verified_gate")) or (0, 0)]
        )
        or "no gate rows"
    )
    session = status.get("session")
    try:
        next_session = str(calendar.next_session(session)) if session else "unknown"
    except Exception:  # noqa: BLE001
        next_session = "unknown"
    detail = f"next_session={next_session}; {detail}"
    if notes:
        detail += "; note: " + "; ".join(notes)
    if hard:
        return "FAIL", detail + "; " + "; ".join(hard)
    if all(
        (parse_gate_text(rows.get(k, {}).get("verified_gate")) or (0, 0))[0] >= 5
        for k in expected
    ):
        return "PASS", detail
    return "WAIT", detail


def parse_verified(text: str | None) -> tuple[int, int] | None:
    match = re.search(r"(\d+)\s*/\s*(\d+)", str(text or ""))
    return (int(match.group(1)), int(match.group(2))) if match else None


def parse_forward_gate(path: str | Path) -> tuple[dict, list[str]]:
    """Parse the table written by scripts/forward_gate.py.

    Unlike the older ``forward_status.md`` table this one carries an explicit
    PASS/WAIT/BLOCKED state per book, including the optional tax book.
    """
    path = Path(path)
    out: dict = {
        "generated": None,
        "session": None,
        "overall": None,
        "books": [],
        "path": str(path),
    }
    if not path.exists():
        return out, [f"{path.name} is missing"]
    try:
        lines = path.read_text().splitlines()
    except Exception as exc:  # noqa: BLE001
        return out, [f"{path.name} unreadable: {exc}"]
    headers: list[str] = []
    in_table = False
    for line in lines:
        clean = line.strip()
        if clean.startswith("Generated:"):
            out["generated"] = clean.split(":", 1)[1].strip()
        elif clean.startswith("Session:"):
            out["session"] = clean.split(":", 1)[1].strip()
        elif clean.startswith("Overall:"):
            out["overall"] = clean.split(":", 1)[1].strip().replace("*", "")
        elif clean.startswith("| book |") or clean.startswith("|book|"):
            headers = [_norm_header(x) for x in clean.strip("|").split("|")]
            in_table = True
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
    return out, []


def _norm_header(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def check_forward_gate(
    gate_path: Path,
    audit_path: Path,
    expected: dict[tuple[str, str], int] | None = None,
) -> tuple[str, str]:
    """Validate actual forward gate status. WAIT is allowed; BLOCKED is not."""
    expected = expected or {("monthly", "US"): 0, ("monthly", "TECH"): 1}
    gate, warnings = parse_forward_gate(gate_path)
    gates, audit_warnings = gates_from_audit(audit_path)
    rows = {
        (str(r.get("book", "")), str(r.get("market", ""))): r
        for r in gate.get("books", [])
    }
    hard = list(warnings)
    notes: list[str] = []
    overall = str(gate.get("overall", "")).upper()
    if overall in {"BLOCKED", "FAIL", "ERROR"}:
        hard.append(f"forward gate overall={overall}")
    for key in {("monthly", "US"), ("monthly", "TECH")} | set(expected):
        row = rows.get(key)
        if row is None:
            if key in {("monthly", "US"), ("monthly", "TECH")}:
                hard.append(f"forward gate missing {key[0]}/{key[1]}")
            continue
        status = str(row.get("status", "")).upper()
        if status in {"BLOCKED", "FAIL", "ERROR"}:
            hard.append(f"{key[0]}/{key[1]} status={status}")
        elif status not in {"PASS", "WAIT"}:
            hard.append(f"{key[0]}/{key[1]} unknown status={status or 'blank'}")
        parsed = parse_verified(row.get("verified"))
        if parsed is None:
            hard.append(f"{key[0]}/{key[1]} verified count unreadable")
            continue
        n, total = parsed
        if total != 5:
            hard.append(f"{key[0]}/{key[1]} target {total} != 5")
        if n < 0 or n > total:
            hard.append(f"{key[0]}/{key[1]} count {n}/{total} out of range")
        audit_gate = gates.get(key)
        audit_n = 0 if audit_gate is None else audit_gate.verified
        if n > 0 and audit_gate is None:
            hard.append(f"{key[0]}/{key[1]} positive gate but no paper_audit row")
        elif audit_gate is not None and audit_n != n:
            hard.append(f"{key[0]}/{key[1]} audit {audit_n} != status {n}")
        expected_n = expected.get(key)
        if expected_n is not None and n != expected_n:
            notes.append(
                f"{key[0]}/{key[1]} now {n}/{total}, release snapshot {expected_n}/5"
            )
    if audit_warnings:
        notes.extend(audit_warnings)
    detail = (
        ", ".join(
            f"{b}/{m}={row.get('status', '?')} {row.get('verified', '?')}"
            for (b, m), row in sorted(rows.items())
        )
        or "no gate rows"
    )
    next_sessions = sorted(
        {
            str(row.get("next_required"))
            for row in rows.values()
            if row.get("next_required")
        }
    )
    if next_sessions:
        detail += f"; next={','.join(next_sessions)}"
    if notes:
        detail += "; note: " + "; ".join(notes)
    if hard:
        return "FAIL", detail + "; " + "; ".join(hard)
    required_rows = [rows.get(k) for k in (("monthly", "US"), ("monthly", "TECH"))]
    if all(
        row is not None
        and str(row.get("status", "")).upper() == "PASS"
        and (parse_verified(row.get("verified")) or (0, 5))[0] >= 5
        for row in required_rows
    ):
        return "PASS", detail
    return "WAIT", detail


def parse_short3_verdict(text: str) -> str | None:
    """Extract a short-horizon III verdict from a markdown report."""
    match = re.search(
        r"(?im)^\s*(?:\*\*)?verdict(?:\*\*)?\s*[:=—-]\s*(?:\*\*)?([a-z_-]+)",
        text,
    )
    if match:
        return match.group(1).lower()
    match = re.search(
        r"(?i)\bverdict\b[^\n]{0,60}\b(reject|paper|accept|fail|blocked|pending)\b",
        text,
    )
    return match.group(1).lower() if match else None


def check_short3(path: Path) -> tuple[str, str]:
    if not path.exists():
        return "WAIT", f"{path.name} not present; v8 short3 workstream pending"
    try:
        text = path.read_text()
    except Exception as exc:  # noqa: BLE001
        return "FAIL", f"{path.name} unreadable: {exc}"
    verdict = parse_short3_verdict(text)
    if verdict is None:
        return "FAIL", "short3 report has no explicit verdict"
    if verdict in {"reject", "paper", "accept"}:
        return "PASS", f"short3 verdict={verdict}"
    if verdict == "pending":
        return "WAIT", "short3 verdict=pending"
    return "FAIL", f"short3 verdict={verdict}"


def scan_for_secrets(paths: list[Path]) -> list[str]:
    hits: list[str] = []
    for path in paths:
        try:
            text = path.read_text(errors="ignore")
        except Exception:  # noqa: BLE001
            continue
        for pattern in SECRET_PATTERNS:
            if re.search(pattern, text):
                hits.append(str(path))
                break
    return hits


def _tracked_text_files() -> list[Path]:
    try:
        proc = subprocess.run(
            ["git", "ls-files"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        if proc.returncode != 0:
            return []
        out = []
        for name in proc.stdout.splitlines():
            path = ROOT / name
            if path.is_file() and path.suffix.lower() in {
                ".py",
                ".md",
                ".txt",
                ".toml",
                ".yml",
                ".yaml",
                ".ini",
                ".cfg",
                ".json",
                ".csv",
                ".sh",
                ".service",
                ".timer",
                ".cron",
            }:
                out.append(path)
        return out
    except Exception:  # noqa: BLE001
        return []


def _run(
    name: str,
    fn: Callable[[], str | tuple[str, str]],
    *,
    command: str = "",
) -> Check:
    t0 = time.perf_counter()
    try:
        result = fn()
        if isinstance(result, tuple):
            status, detail = result
        else:
            status, detail = "PASS", result
        if status not in {"PASS", "SKIP", "WAIT", "FAIL"}:
            raise ValueError(f"invalid check status {status!r}")
    except Exception as exc:  # noqa: BLE001
        detail = f"{type(exc).__name__}: {exc}"
        status = "FAIL"
    return Check(name, status, detail, time.perf_counter() - t0, command)


def _cmd(argv: list[str]) -> list[str]:
    return [sys.executable, *argv]


def run_checks(
    *,
    allow_missing_cache: bool = False,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    tmp: Path | None = None,
) -> ReleaseReport:
    missing = cache_missing()
    snapshot = _cache_snapshot()
    report = ReleaseReport(
        snapshot=snapshot,
        commit=_git_head(),
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        cache_missing=missing,
        allow_missing_cache=allow_missing_cache,
    )
    skip_cache = bool(missing)
    if skip_cache and not allow_missing_cache:
        report.checks.append(
            Check(
                "cache",
                "FAIL",
                "missing cache: " + ", ".join(missing),
                0.0,
                "cache/bars + panel.parquet",
            )
        )
    elif skip_cache:
        report.checks.append(
            Check(
                "cache",
                "SKIP",
                "cache absent; cache-dependent checks skipped: " + ", ".join(missing),
                0.0,
                "cache/bars + panel.parquet",
            )
        )
    else:
        report.checks.append(
            Check("cache", "PASS", f"snapshot={snapshot}", 0.0, "cache/bars")
        )

    def run_tests() -> str:
        files = sorted((ROOT / "tests").glob("test_*.py"))
        failures = []
        for path in files:
            proc = runner(
                _cmd([str(path)]),
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=300,
            )
            if proc.returncode != 0:
                tail = (proc.stdout + proc.stderr).strip().splitlines()[-3:]
                failures.append(f"{path.name}: {' | '.join(tail)}")
        if failures:
            raise AssertionError("; ".join(failures))
        return f"{len(files)}/{len(files)} test scripts passed"

    report.checks.append(
        _run("tests", run_tests, command="for t in tests/test_*.py; do python $t; done")
    )

    def command_check(
        name: str,
        argv: list[str],
        validator: Callable[[subprocess.CompletedProcess], str],
        timeout: int = 300,
    ) -> None:
        def go() -> str:
            proc = runner(
                _cmd(argv),
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            if proc.returncode != 0:
                tail = (proc.stderr or proc.stdout).strip().splitlines()[-4:]
                raise AssertionError(f"exit {proc.returncode}: {' | '.join(tail)}")
            return validator(proc)

        report.checks.append(_run(name, go, command="python " + " ".join(argv)))

    if not skip_cache:

        def acceptance_validator(proc) -> str:
            payload = json.loads(proc.stdout)
            if not payload.get("ok"):
                raise AssertionError("acceptance payload ok=false")
            checks = payload.get("checks") or []
            failed = [c for c in checks if c.get("status") == "FAIL"]
            if failed:
                raise AssertionError(f"acceptance failures: {failed[:3]}")
            return f"{len(checks)}/{len(checks)} checks pass"

        command_check(
            "acceptance", ["scripts/acceptance.py", "--json"], acceptance_validator
        )

        reproduce_text: dict[str, str] = {}

        def reproduce_validator(proc) -> str:
            rows = parse_baseline_numbers(proc.stdout)
            if len(rows) != 4:
                raise AssertionError(f"parsed {len(rows)}/4 baseline rows")
            reproduce_text["stdout"] = proc.stdout
            return "4/4 baseline rows; exit 0"

        command_check(
            "reproduce_baseline",
            ["scripts/reproduce.py", "baseline", "--no-write"],
            reproduce_validator,
            timeout=180,
        )

        def ledger_validator(_proc) -> str:
            rows = parse_baseline_numbers(reproduce_text.get("stdout", ""))
            if len(rows) != 4:
                raise AssertionError("no parsed baseline output")
            bad = []
            for key, expected in EXPECTED_EUR.items():
                got = rows.get(key, {}).get("eur_ledger")
                if got is None or abs(got - expected) > TOL:
                    bad.append(f"{key[0]}/{key[1]}={got} expected {expected}")
            if bad:
                raise AssertionError("; ".join(bad))
            return ", ".join(
                f"{k[0]}/{k[1]}={v['eur_ledger']:.2f}x" for k, v in sorted(rows.items())
            )

        # The actual reproduce command has already run; this check is a parsed
        # assertion over its output, so use a trivial validator runner.
        report.checks.append(
            _run(
                "eur_ledger",
                lambda: ledger_validator(None),
                command="parsed from reproduce_baseline",
            )
        )

        state_dir = tmp or Path(
            tempfile.mkdtemp(prefix="stonks-release-", dir="/tmp/opencode")
        )

        def plan_validator(market: str):
            def validate(proc) -> str:
                payload = json.loads(proc.stdout)
                plan = payload.get("plan") or {}
                orders = plan.get("orders") or payload.get("orders") or []
                blocked = payload.get("blocked") or plan.get("blocked_reason")
                if blocked:
                    raise AssertionError(f"{market} blocked: {blocked}")
                if not orders:
                    raise AssertionError(f"{market} plan has no orders")
                return f"orders={len(orders)} status={plan.get('status', 'OK')}"

            return validate

        for market in ("US", "TECH"):
            command_check(
                f"live_plan_{market.lower()}",
                [
                    "scripts/live.py",
                    "plan",
                    "--book",
                    "monthly",
                    "--market",
                    market,
                    "--as-of",
                    EXPECTED_SNAPSHOT,
                    "--state-dir",
                    str(state_dir),
                    "--json",
                ],
                plan_validator(market),
                timeout=180,
            )

    def gate_check() -> tuple[str, str]:
        return check_forward_gate(
            ROOT / "results" / "forward_gate.md",
            ROOT / "results" / "paper_audit.csv",
            EXPECTED_GATES,
        )

    report.checks.append(
        _run(
            "forward_gate",
            gate_check,
            command="results/forward_gate.md + paper_audit.csv",
        )
    )

    def short3_check() -> tuple[str, str]:
        return check_short3(ROOT / "results" / "short3.md")

    report.checks.append(
        _run(
            "short3",
            short3_check,
            command="results/short3.md verdict",
        )
    )

    def dashboard_check() -> str:
        proc = runner(
            _cmd(["scripts/dashboard.py", "--check"]),
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if proc.returncode != 0:
            raise AssertionError((proc.stderr or proc.stdout).strip()[-500:])
        return (proc.stdout.strip().splitlines() or ["dashboard check OK"])[-1]

    report.checks.append(
        _run(
            "dashboard_check",
            dashboard_check,
            command="python scripts/dashboard.py --check",
        )
    )

    def secrets_check() -> str:
        hits = scan_for_secrets(_tracked_text_files())
        if hits:
            raise AssertionError("possible secret in: " + ", ".join(hits[:5]))
        return f"{len(_tracked_text_files())} tracked text files scanned; no obvious secrets"

    report.checks.append(
        _run("no_secrets", secrets_check, command="git ls-files + pattern scan")
    )
    return report


def render_markdown(report: ReleaseReport) -> str:
    status = "PASS" if report.ok else "FAIL"
    gate = next((c for c in report.checks if c.name == "forward_gate"), None)
    gate_mark = "x" if gate is not None and gate.status in {"PASS", "WAIT"} else " "
    short3 = next((c for c in report.checks if c.name == "short3"), None)
    short3_mark = (
        "x" if short3 is not None and short3.status in {"PASS", "WAIT"} else " "
    )
    lines = [
        "# Release check — v8 / v1.1",
        "",
        f"- Overall: **{status}**",
        f"- Generated: {report.generated_at}",
        f"- Commit: `{report.commit}`",
        f"- Data snapshot: **{report.snapshot}**",
        f"- Checks: {report.counts.get('PASS', 0)} PASS · "
        f"{report.counts.get('WAIT', 0)} WAIT · "
        f"{report.counts.get('SKIP', 0)} SKIP · {report.counts.get('FAIL', 0)} FAIL",
        "",
        "| check | status | detail | seconds | command |",
        "|---|---|---|---:|---|",
    ]
    for c in report.checks:
        detail = str(c.detail).replace("|", "\\|").replace("\n", " ")
        command = str(c.command).replace("|", "\\|")
        lines.append(
            f"| {c.name} | **{c.status}** | {detail} | {c.seconds:.1f} | `{command}` |"
        )
    lines += [
        "",
        "## Release checklist",
        "",
        f"- [{'x' if report.ok else ' '}] all local release checks pass",
        f"- [{'x' if not report.cache_missing else ' '}] committed data snapshot {report.snapshot} present",
        "- [ ] CI green on the merged commit (GitHub Actions)",
        "- [ ] no open PRs / release-blocking issues",
        f"- [{gate_mark}] forward gate honesty: US 0/5, TECH 1/5, tax 0/5 is WAIT, not an edge",
        f"- [{short3_mark}] short3 report/verdict reviewed (WAIT is allowed if the workstream is not merged)",
        "- [ ] paper-only; no real orders, broker keys or secrets",
        "- [ ] README/RELEASE links and headline numbers reviewed",
        "- [ ] main session tags/creates the GitHub release after merge",
        "",
        "## Known risks",
        "",
        "- Backtests are consistency checks, not forward evidence. US has 0/5 verified "
        "dates, TECH 1/5, tax 0/5.",
        "- Static-49 TECH is survivor-biased; US PIT drops 16.2% of 2014+ members "
        "without a Yahoo history.",
        "- The 2.5x growth book can lose more than half; balanced/conservative are "
        "post-hoc `paper` opt-ins.",
        "- The live paper ledger is pre-tax/average-cost, not the after-tax EUR ledger.",
        "- IBKR paper/read-only adapter is opt-in; no live-money order path exists here.",
        "- Short3 evidence may be pending in this release branch; never upgrade a "
        "missing/pending short study into a production allocation.",
        "",
        "## Commands",
        "",
        "```bash",
        "uv run python scripts/release_check.py",
        "uv run python scripts/acceptance.py",
        "uv run python scripts/reproduce.py baseline --no-write",
        "uv run python scripts/live.py plan --book monthly --market US --as-of 2026-11-25",
        "uv run python scripts/dashboard.py --check",
        'for t in tests/test_*.py; do uv run python "$t"; done',
        "```",
        "",
        "Generated by `scripts/release_check.py`. No network, no real orders.",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument(
        "--allow-missing-cache",
        action="store_true",
        help="mark cache-dependent checks SKIP and exit 0 without the 459 MB cache",
    )
    args = ap.parse_args(argv)
    report = run_checks(allow_missing_cache=args.allow_missing_cache)
    text = render_markdown(report)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        print(text)
    if not args.no_write:
        out = ROOT / "results" / "release_check.md"
        out.write_text(text)
        print(f"wrote {out}")
    return report.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
