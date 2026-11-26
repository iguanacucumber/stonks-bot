"""Approval and real-money preflight tests. No network, secrets or real ids.

All artifacts live in a temporary directory. The only account ids are fake
``DU1234567``/``U1234567`` strings; no broker is contacted.
"""

from __future__ import annotations

import contextlib
import csv
import io
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import real_money_preflight as cli  # noqa: E402
from bot.live import preflight  # noqa: E402
from bot.live.approval import (  # noqa: E402
    ApprovalError,
    approval_issues,
    check_approval,
    config_sha256,
    load_approval,
    parse_approval,
    plan_sha256,
)
from bot.live.config import LiveConfig  # noqa: E402
from bot.live.models import Plan  # noqa: E402
from bot.live.state import LiveState  # noqa: E402

NOW = "2026-11-26T18:00:00+00:00"
EXPIRES = "2026-12-03T00:00:00+00:00"


def _cfg(tmp: Path, **kw) -> LiveConfig:
    kw.setdefault("state_dir", str(tmp / "state" / "live"))
    kw.setdefault("book", "monthly")
    kw.setdefault("market", "US")
    kw.setdefault("capital", 10_000.0)
    return LiveConfig(**kw)


def _approval(cfg: LiveConfig, **kw) -> dict:
    raw = {
        "version": 1,
        "approver": "Test Human",
        "approved_at": "2026-11-26T12:00:00+00:00",
        "expires_at": EXPIRES,
        "max_capital": 10_000.0,
        "allowed_books": ["monthly"],
        "allowed_markets": ["US"],
        "config_sha256": config_sha256(cfg),
        "note": "synthetic test approval",
    }
    raw.update(kw)
    return raw


def _write_json(path: Path, raw: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(raw, indent=2, sort_keys=True) + "\n")


def _gate(path: Path, status: str = "PASS", verified: str = "5/5") -> None:
    path.write_text(
        "# Forward gate status\n\n"
        "Generated: 2026-11-26T17:24:00+00:00\n"
        "Session: 2026-11-25\n"
        f"Overall: **{status}**\n\n"
        "| book | market | status | verified | next required |\n"
        "|---|---|---|---:|---|\n"
        f"| monthly | US | {status} | {verified} | 2026-12-03 |\n"
    )


def _audit(path: Path, n: int = 5) -> None:
    rows = []
    day = date(2026, 11, 19)
    while len(rows) < n:
        if day.weekday() < 5:
            rows.append(
                {
                    "book": "monthly",
                    "market": "US",
                    "date": day.isoformat(),
                    "verified": "True",
                }
            )
        day += timedelta(days=1)
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["book", "market", "date", "verified"])
        writer.writeheader()
        writer.writerows(rows)


def _release(path: Path, status: str = "PASS") -> None:
    path.write_text(
        "# Release check\n\n"
        f"- Overall: **{status}**\n"
        "- Generated: 2026-11-26T18:14:27+00:00\n"
        "- Commit: `abcdef0`\n"
    )


def _minimal_plan(cfg: LiveConfig, qty: float = 1.0) -> Plan:
    return Plan(
        plan_id="plan-test",
        book=cfg.book,
        market=cfg.market,
        signal_session="2026-11-25",
        fill_session="2026-11-27",
        created_at=NOW,
        target_weights={"SMH": 1.0},
        current_weights={},
        orders=[],
    )


def test_config_and_plan_hash_stability() -> None:
    with TemporaryDirectory() as td:
        tmp = Path(td)
        a = _cfg(tmp)
        b = _cfg(tmp, state_dir=str(tmp / "other"))
        assert config_sha256(a) == config_sha256(b)
        assert len(config_sha256(a)) == 64
        assert config_sha256(a) != config_sha256(_cfg(tmp, capital=20_000.0))
        assert config_sha256(a) != config_sha256(_cfg(tmp, dd_stop=0.20))
        plan = _minimal_plan(a)
        digest = plan_sha256(plan)
        assert len(digest) == 64 and digest == plan_sha256(plan.to_dict())
        changed = _minimal_plan(a)
        changed.target_weights = {"SMH": 0.5}
        assert plan_sha256(changed) != digest
        path = tmp / "plan.json"
        _write_json(path, {"plan": plan.to_dict(), "extra": "report wrapper"})
        assert plan_sha256(path) == digest
        print("ok: config/plan hashes are stable, full-length and sensitive")


def test_approval_parse_and_scope_checks() -> None:
    with TemporaryDirectory() as td:
        tmp = Path(td)
        cfg = _cfg(tmp)
        raw = _approval(cfg)
        approval = parse_approval(raw)
        assert approval.config_sha256 == config_sha256(cfg)
        assert check_approval(approval, cfg, now=NOW) == []

        path = tmp / "APPROVAL.json"
        _write_json(path, raw)
        assert load_approval(path).approver == "Test Human"
        assert approval_issues(path, cfg, now=NOW) == []

        missing = tmp / "missing.json"
        assert "missing approval" in approval_issues(missing, cfg, now=NOW)[0]

        bad = dict(raw)
        bad["unknown"] = True
        try:
            parse_approval(bad)
        except ApprovalError as exc:
            assert "unknown fields" in str(exc)
        else:
            raise AssertionError("unknown approval field was accepted")

        naive = dict(raw, approved_at="2026-11-26T12:00:00")
        try:
            parse_approval(naive)
        except ApprovalError as exc:
            assert "timezone offset" in str(exc)
        else:
            raise AssertionError("naive timestamp was accepted")

        assert any(
            "expired" in x
            for x in check_approval(
                parse_approval(raw), cfg, now="2026-12-04T00:00:00+00:00"
            )
        )
        future = dict(raw, approved_at="2026-11-27T00:00:00+00:00")
        assert any(
            "future" in x for x in check_approval(parse_approval(future), cfg, now=NOW)
        )
        wrong_scope = dict(
            raw,
            allowed_books=["tax"],
            allowed_markets=["TECH"],
            max_capital=5_000.0,
        )
        problems = check_approval(parse_approval(wrong_scope), cfg, now=NOW)
        assert any("book" in x for x in problems)
        assert any("market" in x for x in problems)
        assert any("max_capital" in x for x in problems)
        wrong_hash = dict(raw, config_sha256="0" * 64)
        assert any(
            "config hash mismatch" in x
            for x in check_approval(parse_approval(wrong_hash), cfg, now=NOW)
        )

        plan = _minimal_plan(cfg)
        pinned = dict(raw, plan_sha256=plan_sha256(plan))
        assert check_approval(parse_approval(pinned), cfg, now=NOW, plan=plan) == []
        assert any(
            "no --plan" in x
            for x in check_approval(parse_approval(pinned), cfg, now=NOW)
        )
        other = _minimal_plan(cfg)
        other.target_weights = {"SMH": 0.5}
        assert any(
            "plan hash mismatch" in x
            for x in check_approval(parse_approval(pinned), cfg, now=NOW, plan=other)
        )
        print("ok: approval parsing, expiry, scope, capital and hash checks")


def test_forward_gate_audit_and_release_parsers() -> None:
    with TemporaryDirectory() as td:
        tmp = Path(td)
        gate = tmp / "forward_gate.md"
        audit = tmp / "paper_audit.csv"
        release = tmp / "release_check.md"
        _gate(gate)
        _audit(audit)
        _release(release)
        ok, detail = preflight.forward_gate_issues(gate, "monthly", "US")
        assert ok and "PASS 5/5" in detail
        dates, issues = preflight.audit_verified_dates(audit, "monthly", "US")
        assert not issues and len(dates) == 5
        ok, detail = preflight.release_check_issues(release)
        assert ok and "PASS" in detail

        _gate(gate, "WAIT", "0/5")
        ok, detail = preflight.forward_gate_issues(gate, "monthly", "US")
        assert not ok and "WAIT" in detail and "need PASS 5/5" in detail
        _gate(gate, "PASS", "4/5")
        ok, _ = preflight.forward_gate_issues(gate, "monthly", "US")
        assert not ok
        _gate(gate, "BLOCKED", "0/5")
        ok, _ = preflight.forward_gate_issues(gate, "monthly", "US")
        assert not ok
        assert not preflight.forward_gate_issues(tmp / "missing.md", "monthly", "US")[0]

        _audit(audit, n=4)
        dates, issues = preflight.audit_verified_dates(audit, "monthly", "US")
        assert not issues and len(dates) == 4

        _release(release, "WAIT")
        assert not preflight.release_check_issues(release)[0]
        assert not preflight.release_check_issues(tmp / "missing.md")[0]
        print("ok: forward gate, audit and release-check parsers fail closed")


def test_env_risk_rails_and_state_blockers() -> None:
    with TemporaryDirectory() as td:
        tmp = Path(td)
        cfg = _cfg(tmp)

        ok, detail = preflight.credentials_issues({})
        assert not ok and "not set" in detail
        ok, detail = preflight.credentials_issues({"IBKR_REAL_ACCOUNT": "DU1234567"})
        assert not ok and "DU paper" in detail
        ok, detail = preflight.credentials_issues({"IBKR_REAL_ACCOUNT": "U1234567"})
        assert ok and "U1234567" not in detail
        ok, detail = preflight.credentials_issues({"IBKR_REAL_ACCOUNT": "U"})
        assert not ok and "U..." in detail
        assert not preflight.read_only_issues({})[0]
        assert not preflight.read_only_issues({"IBKR_REAL_READ_ONLY": "false"})[0]
        assert preflight.read_only_issues({"IBKR_REAL_READ_ONLY": "true"})[0]

        assert preflight.risk_rail_issues(cfg) == []
        loose = [
            _cfg(tmp, dd_stop=0.0),
            _cfg(tmp, max_gross=5.0),
            _cfg(tmp, max_turnover=5.0),
            _cfg(tmp, max_orders=100),
            _cfg(tmp, min_order_notional=1.0),
            _cfg(tmp, max_participation=0.05),
            _cfg(tmp, max_stale_days=10),
            _cfg(tmp, max_pending_sessions=10),
            _cfg(tmp, leverage=4.0),
        ]
        for bad in loose:
            assert preflight.risk_rail_issues(bad), bad

        assert preflight.state_issues(cfg) == []
        state = LiveState.new(cfg, now=NOW)
        _write_json(cfg.state_path, state.to_dict())
        assert preflight.state_issues(cfg) == []

        blocked = state.to_dict()
        blocked["blocked"] = {"session": "2026-11-25", "reason": "stale data"}
        _write_json(cfg.state_path, blocked)
        assert any("blocked plan" in x for x in preflight.state_issues(cfg))

        cut = state.to_dict()
        cut["pending_cut"] = {"reason": "risk_off", "scale": 0.5}
        _write_json(cfg.state_path, cut)
        assert any("pending risk cut" in x for x in preflight.state_issues(cfg))

        mismatch = state.to_dict()
        mismatch["config_signature"] = "deadbeef"
        _write_json(cfg.state_path, mismatch)
        assert any("signature" in x for x in preflight.state_issues(cfg))

        overdue = state.to_dict()
        overdue["pending_plan"] = {
            "plan_id": "old",
            "status": "OK",
            "fill_session": "2026-11-20",
        }
        overdue["last_session"] = "2026-11-25"
        _write_json(cfg.state_path, overdue)
        assert any("older than last session" in x for x in preflight.state_issues(cfg))

        cfg.state_path.write_text("{not json")
        assert any("unreadable" in x for x in preflight.state_issues(cfg))
        print("ok: env, read-only, rail and state blocker checks")


def test_preflight_is_blocked_by_missing_real_path() -> None:
    with TemporaryDirectory() as td:
        tmp = Path(td)
        cfg = _cfg(tmp)
        approval = tmp / "APPROVAL.json"
        gate = tmp / "forward_gate.md"
        audit = tmp / "paper_audit.csv"
        release = tmp / "release_check.md"
        _write_json(approval, _approval(cfg))
        _gate(gate)
        _audit(audit)
        _release(release)
        env = {"IBKR_REAL_ACCOUNT": "U1234567", "IBKR_REAL_READ_ONLY": "true"}
        report = preflight.run_preflight(
            cfg,
            approval_path=approval,
            forward_gate_path=gate,
            audit_path=audit,
            release_check_path=release,
            now=NOW,
            env=env,
        )
        checks = {c.name: c for c in report.checks}
        assert all(
            c.ok for name, c in checks.items() if name != "real_money_execution_path"
        )
        assert not checks["real_money_execution_path"].ok
        assert report.ok is False and report.exit_code == 2
        assert len(report.blockers) == 1
        assert "paper-only build" in report.blockers[0]

        empty = preflight.run_preflight(
            cfg,
            approval_path=tmp / "missing-approval.json",
            forward_gate_path=tmp / "missing-gate.md",
            audit_path=tmp / "missing-audit.csv",
            release_check_path=tmp / "missing-release.md",
            now=NOW,
            env={},
        )
        names = {x.split(":", 1)[0] for x in empty.blockers}
        assert {
            "approval",
            "forward_gate",
            "forward_audit",
            "release_check",
            "credentials_env",
            "read_only_default",
            "real_money_execution_path",
        } <= names
        print("ok: preflight fails closed on missing evidence and no live path")


def test_cli_json_and_exit_code() -> None:
    with TemporaryDirectory() as td:
        tmp = Path(td)
        cfg = _cfg(tmp)
        approval = tmp / "APPROVAL.json"
        gate = tmp / "forward_gate.md"
        audit = tmp / "paper_audit.csv"
        release = tmp / "release_check.md"
        _write_json(approval, _approval(cfg))
        _gate(gate)
        _audit(audit)
        _release(release)
        env = {"IBKR_REAL_ACCOUNT": "U1234567", "IBKR_REAL_READ_ONLY": "true"}
        buf = io.StringIO()
        with patch.dict(os.environ, env, clear=False), contextlib.redirect_stdout(buf):
            code = cli.main(
                [
                    "--book",
                    "monthly",
                    "--market",
                    "US",
                    "--capital",
                    "10000",
                    "--state-dir",
                    str(tmp / "state" / "live"),
                    "--approval",
                    str(approval),
                    "--forward-gate",
                    str(gate),
                    "--audit",
                    str(audit),
                    "--release-check",
                    str(release),
                    "--now",
                    NOW,
                    "--json",
                ]
            )
        payload = json.loads(buf.getvalue())
        assert code == 2 and payload["ok"] is False
        assert payload["blockers"][0].startswith("real_money_execution_path:")

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli.main(
                [
                    "--book",
                    "monthly",
                    "--market",
                    "US",
                    "--capital",
                    "10000",
                    "--state-dir",
                    str(tmp / "state" / "live"),
                    "--print-config-hash",
                ]
            )
        digest = buf.getvalue().strip()
        assert code == 0 and len(digest) == 64
        assert digest == config_sha256(cfg)
        print("ok: preflight CLI emits JSON, exits 2, and prints config hash")


def main() -> None:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"ok: {fn.__name__}")
    print(f"ok: {len(tests)} real-money approval/preflight tests")


if __name__ == "__main__":
    main()
