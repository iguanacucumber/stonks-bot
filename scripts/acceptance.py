"""End-to-end acceptance harness: cache -> backtest -> live/paper -> ops -> PR evidence.

One command, temp state, no network, no real orders::

    uv run python scripts/acceptance.py
    uv run python scripts/acceptance.py --json
    uv run python scripts/acceptance.py --allow-missing-cache   # CI smoke

Exit 0 only when every check passes. With ``--allow-missing-cache`` a checkout
without the committed cache reports SKIP rows and exits 0; without the flag a
missing cache is a hard failure. The harness never writes the real
``state/`` or ``results/`` directories: live state, ledgers and backups all go
to a temporary directory.
"""

from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import pandas as pd  # noqa: E402

from bot import eur_ledger, france, markets, strategy, universe  # noqa: E402
from bot.evaluation import currency  # noqa: E402

import advise as advise_cli  # noqa: E402
import live as live_cli  # noqa: E402
import ops as ops_cli  # noqa: E402
import reproduce as reproduce_cli  # noqa: E402

CACHE = ROOT / "cache"
EXPECTED_SNAPSHOT = "2026-11-25"
EXPECTED_EUR = {"US": 66.17, "TECH": 52.98}
TOL = 0.05


class SkipCheck(RuntimeError):
    """A dependency (cache) is absent; the check is skipped, not failed."""


@dataclass
class Check:
    name: str
    status: str
    detail: str
    seconds: float

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "seconds": round(self.seconds, 3),
        }


def cache_missing(root: Path | None = None) -> list[str]:
    """Return missing required cache files for the acceptance run."""
    root = CACHE if root is None else Path(root)
    missing = []
    for sym in reproduce_cli.required_symbols():
        path = root / "bars" / f"{sym.replace('/', '_')}.csv"
        if not path.exists() or path.stat().st_size == 0:
            missing.append(f"bars/{sym}")
    for name in ("panel.parquet", "membership.parquet"):
        path = root / name
        if not path.exists() or path.stat().st_size == 0:
            missing.append(name)
    return missing


def _call_json(
    fn: Callable[[list[str]], int], argv: list[str]
) -> tuple[int, dict, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = int(fn(argv))
    text = out.getvalue()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"command did not emit JSON (rc={rc}): {text[:300]} {err.getvalue()[:300]}"
        ) from exc
    return rc, payload, text


def _run(name: str, fn: Callable[[], str]) -> Check:
    t0 = time.perf_counter()
    try:
        detail = fn()
        status = "PASS"
    except SkipCheck as exc:
        detail = str(exc)
        status = "SKIP"
    except Exception as exc:  # noqa: BLE001 - report, do not hide the stack in JSON
        detail = f"{type(exc).__name__}: {exc}"
        status = "FAIL"
    return Check(name, status, detail, time.perf_counter() - t0)


def _ledger_multiple(market: str) -> float:
    panel, mem = markets.build(market)
    old = universe.canonical
    universe.canonical = old if market == "US" else (lambda t: t)
    try:
        res = reproduce_cli.run_book(panel, mem, market, "monthly")
        dims = currency.dimension_table(res, panel, market, capital=10_000.0)
    finally:
        universe.canonical = old
    row = dims[dims["accounting_mode"] == "eur_ledger"]
    if row.empty:
        raise AssertionError("EUR ledger row missing")
    return float(row.iloc[0]["multiple"])


def _state_runs(path: Path) -> int:
    data = json.loads(path.read_text())
    return len(data.get("runs", {}))


def _ledger_rows(path: Path) -> int:
    if not path.exists():
        return 0
    with open(path) as fh:
        return max(0, sum(1 for _ in fh) - 1)


def run_checks(
    tmp: Path,
    *,
    cache_ok: bool,
    allow_missing_cache: bool = False,
) -> list[Check]:
    """Run all checks. `cache_ok=False` is the CI/no-cache path."""
    names = [
        "cache_freshness",
        "backtest_eur_ledger",
        "live_plan_us",
        "live_plan_tech",
        "paper_replay_us",
        "paper_replay_tech",
        "paper_idempotent_rerun",
        "reconcile_ledger",
        "ops_doctor",
        "ops_backup_restore",
        "advisor_order_list",
        "reproduce_baseline",
    ]
    if not cache_ok:
        status = "SKIP" if allow_missing_cache else "FAIL"
        reason = (
            "cache absent: full acceptance skipped (--allow-missing-cache)"
            if allow_missing_cache
            else "cache absent: restore cache/bars + panel/membership"
        )
        return [Check(name, status, reason, 0.0) for name in names]

    state_dir = tmp / "state"
    backups = tmp / "backups"
    restored = tmp / "restored"
    ledger_us = state_dir / "monthly_us.csv"
    ledger_tech = state_dir / "monthly_tech.csv"
    state_us = state_dir / "monthly_us.json"
    state_tech = state_dir / "monthly_tech.json"
    checks: list[Check] = []

    def fresh() -> str:
        snapshot, missing = reproduce_cli.preflight(require_us=True)
        if missing:
            raise AssertionError(f"missing cache files: {missing}")
        if snapshot.normalize() != pd.Timestamp(EXPECTED_SNAPSHOT):
            raise AssertionError(
                f"snapshot {snapshot.date()} != expected {EXPECTED_SNAPSHOT}"
            )
        return f"snapshot={snapshot.date()}"

    checks.append(_run("cache_freshness", fresh))

    def ledger() -> str:
        vals = {m: _ledger_multiple(m) for m in ("US", "TECH")}
        for market, expected in EXPECTED_EUR.items():
            if abs(vals[market] - expected) > TOL:
                raise AssertionError(
                    f"{market} EUR ledger {vals[market]:.3f}x != {expected:.2f}x"
                )
        return f"US={vals['US']:.2f}x TECH={vals['TECH']:.2f}x"

    checks.append(_run("backtest_eur_ledger", ledger))

    def plan(market: str) -> Callable[[], str]:
        def go() -> str:
            state = state_dir / f"monthly_{market.lower()}.json"
            if state.exists():
                state.unlink()
            rc, payload, _ = _call_json(
                live_cli.main,
                [
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
            )
            if rc != 0:
                raise AssertionError(f"plan exit {rc}: {payload.get('blocked_reason')}")
            if payload.get("blocked"):
                raise AssertionError(f"blocked: {payload.get('blocked_reason')}")
            plan = payload.get("plan") or {}
            orders = plan.get("orders") or payload.get("orders") or []
            if not orders:
                raise AssertionError("plan produced no orders")
            if state.exists():
                raise AssertionError("plan wrote state")
            return f"orders={len(orders)} no state"

        return go

    checks.append(_run("live_plan_us", plan("US")))
    checks.append(_run("live_plan_tech", plan("TECH")))

    def paper(market: str) -> Callable[[], str]:
        def go() -> str:
            rc, payload, _ = _call_json(
                live_cli.main,
                [
                    "run",
                    "--book",
                    "monthly",
                    "--market",
                    market,
                    "--as-of",
                    EXPECTED_SNAPSHOT,
                    "--state-dir",
                    str(state_dir),
                    "--paper",
                    "--json",
                ],
            )
            state = state_dir / f"monthly_{market.lower()}.json"
            ledger = state_dir / f"monthly_{market.lower()}.csv"
            if rc != 0:
                raise AssertionError(f"paper exit {rc}")
            if not state.exists() or not ledger.exists():
                raise AssertionError("paper did not write state+ledger")
            return f"state={state.name} ledger_rows={_ledger_rows(ledger)}"

        return go

    checks.append(_run("paper_replay_us", paper("US")))
    checks.append(_run("paper_replay_tech", paper("TECH")))

    def idempotent() -> str:
        before = {
            "us_rows": _ledger_rows(ledger_us),
            "tech_rows": _ledger_rows(ledger_tech),
            "us_runs": _state_runs(state_us),
            "tech_runs": _state_runs(state_tech),
        }
        for market in ("US", "TECH"):
            rc, _, _ = _call_json(
                live_cli.main,
                [
                    "run",
                    "--book",
                    "monthly",
                    "--market",
                    market,
                    "--as-of",
                    EXPECTED_SNAPSHOT,
                    "--state-dir",
                    str(state_dir),
                    "--paper",
                    "--json",
                ],
            )
            if rc != 0:
                raise AssertionError(f"rerun {market} exit {rc}")
        after = {
            "us_rows": _ledger_rows(ledger_us),
            "tech_rows": _ledger_rows(ledger_tech),
            "us_runs": _state_runs(state_us),
            "tech_runs": _state_runs(state_tech),
        }
        if before != after:
            raise AssertionError(f"rerun changed state/ledger: {before} -> {after}")
        return f"rows={after['us_rows']}/{after['tech_rows']} runs=1/1"

    checks.append(_run("paper_idempotent_rerun", idempotent))

    def reconcile() -> str:
        out = []
        for market in ("US", "TECH"):
            rc, payload, _ = _call_json(
                live_cli.main,
                [
                    "reconcile",
                    "--book",
                    "monthly",
                    "--market",
                    market,
                    "--state-dir",
                    str(state_dir),
                    "--json",
                ],
            )
            if rc != 0 or not payload.get("ok"):
                raise AssertionError(f"{market} reconcile failed: {payload}")
            out.append(f"{market}=ok")
        return " ".join(out)

    checks.append(_run("reconcile_ledger", reconcile))

    def doctor() -> str:
        rc, payload, _ = _call_json(
            ops_cli.main,
            [
                "doctor",
                "--book",
                "monthly",
                "--market",
                "US",
                "--state-dir",
                str(state_dir),
                "--json",
            ],
        )
        if rc != 0 or not payload.get("ok"):
            raise AssertionError(f"doctor exit {rc}: {payload.get('checks')}")
        return f"checks={len(payload.get('checks', []))}"

    checks.append(_run("ops_doctor", doctor))

    def backup_restore() -> str:
        original = state_us.read_bytes()
        rc, payload, _ = _call_json(
            ops_cli.main,
            [
                "backup",
                "--book",
                "monthly",
                "--market",
                "US",
                "--state-dir",
                str(state_dir),
                "--out",
                str(backups),
                "--results-dir",
                str(tmp / "backup_results"),
                "--json",
            ],
        )
        if rc != 0:
            raise AssertionError(f"backup exit {rc}")
        archive = payload.get("archive")
        if not archive or not Path(archive).exists():
            raise AssertionError("backup archive missing")
        state_us.unlink()
        rc, payload, _ = _call_json(
            ops_cli.main,
            [
                "restore",
                archive,
                "--state-dir",
                str(state_dir),
                "--results-dir",
                str(restored),
                "--force",
                "--json",
            ],
        )
        if rc != 0:
            raise AssertionError(f"restore exit {rc}")
        if not state_us.exists() or state_us.read_bytes() != original:
            raise AssertionError("restored state bytes differ")
        return f"entries={payload.get('restored')} bytes_match=true"

    checks.append(_run("ops_backup_restore", backup_restore))

    def advisor() -> str:
        rc, payload, _ = _call_json(
            advise_cli.main,
            [
                "--profile",
                "max_growth",
                "--horizon",
                "12",
                "--drawdown-tolerance",
                "0.60",
                "--capital",
                "10000",
                "--wrapper",
                "cto",
                "--as-of",
                EXPECTED_SNAPSHOT,
                "--json",
                "--no-lifecycle",
            ],
        )
        rec = payload.get("recommendation", {})
        orders = rec.get("order_list") or rec.get("orders") or []
        if rc != 0 or not orders:
            raise AssertionError(f"advisor rc={rc} orders={len(orders)}")
        return f"profile={rec.get('profile')} orders={len(orders)}"

    checks.append(_run("advisor_order_list", advisor))

    def reproduce() -> str:
        proc = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "reproduce.py"),
                "baseline",
                "--no-write",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if proc.returncode != 0:
            raise AssertionError(
                f"reproduce exit {proc.returncode}: {proc.stderr[-400:]}"
            )
        if "US   monthly" not in proc.stdout or "66.17x" not in proc.stdout:
            raise AssertionError("reproduce output missing US monthly 66.17x")
        if "TECH monthly" not in proc.stdout or "52.98x" not in proc.stdout:
            raise AssertionError("reproduce output missing TECH monthly 52.98x")
        return "US=66.17x TECH=52.98x (strict baseline, no write)"

    checks.append(_run("reproduce_baseline", reproduce))
    return checks


def render_text(checks: list[Check]) -> str:
    ok = all(c.status in ("PASS", "SKIP") for c in checks)
    head = "ACCEPTANCE PASS" if ok else "ACCEPTANCE FAIL"
    lines = [f"{head}  checks={len(checks)}"]
    for c in checks:
        lines.append(f"  {c.status:4s} {c.name:22s} {c.seconds:6.2f}s  {c.detail}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true")
    ap.add_argument(
        "--allow-missing-cache",
        action="store_true",
        help="treat a missing cache as SKIP and exit 0 (CI smoke)",
    )
    ap.add_argument("--keep", action="store_true", help="keep the temp state dir")
    args = ap.parse_args(argv)

    base = Path("/tmp/opencode")
    if not base.exists():
        base = Path(tempfile.gettempdir())
    tmp = Path(tempfile.mkdtemp(prefix="stonks-acceptance-", dir=base))
    missing = cache_missing()
    try:
        checks = run_checks(
            tmp,
            cache_ok=not missing,
            allow_missing_cache=args.allow_missing_cache,
        )
        ok = all(c.status in ("PASS", "SKIP") for c in checks) and not (
            missing and not args.allow_missing_cache
        )
        payload = {
            "ok": ok,
            "snapshot_expected": EXPECTED_SNAPSHOT,
            "cache_missing": missing,
            "allow_missing_cache": args.allow_missing_cache,
            "checks": [c.to_dict() for c in checks],
        }
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print(render_text(checks))
            if missing:
                print(f"missing cache: {', '.join(missing)}")
            if args.keep:
                print(f"temp state: {tmp}")
        return 0 if ok else 1
    finally:
        if not args.keep:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
