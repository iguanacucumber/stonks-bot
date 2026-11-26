"""Release-check parser, gate and exit-code tests (no cache/network)."""

from __future__ import annotations

import sys
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import release_check as rc  # noqa: E402

SAMPLE = """\
TECH monthly native  51.82x  terminal-FX  57.01x  EUR-ledger  53.42x
TECH tax     native  46.26x  terminal-FX  50.89x  EUR-ledger  49.27x
US   monthly native  64.41x  terminal-FX  70.86x  EUR-ledger  66.69x
US   tax     native  46.26x  terminal-FX  50.89x  EUR-ledger  49.27x
"""

FORWARD = """\
# Forward paper status

Generated: 2026-11-26T16:03:36+00:00
Session: 2026-11-25
Mode: paper
Overall: **OK**

| book | market | live | orders | reconcile | paper dates | verified gate |
|---|---|---|---|---|---:|---|
| monthly | US | ok | 6 | OK | 0 | WAIT 0/5 verified |
| monthly | TECH | ok | 6 | OK | 1 | WAIT 1/5 verified |
"""

AUDIT = """\
book,market,date,verified
monthly,TECH,2026-11-25,true
monthly,US,2026-11-24,false
"""

GATE = """\
# Forward gate status

Generated: 2026-11-26T16:46:27+00:00
Session: 2026-11-25
Overall: **WAIT**

| book | market | status | verified | next required | equity | drawdown | data | reconcile |
|---|---|---|---|---|---:|---:|---|---|
| monthly | US | WAIT | 0/5 | 2026-11-27 | 10,000.00 | n/a | fresh | no state |
| monthly | TECH | WAIT | 1/5 | 2026-11-27 | 10,009.46 | +0.0% | fresh | OK |
| tax | TECH | WAIT | 0/5 | 2026-11-27 | n/a | n/a | fresh | no state |
"""


def test_parse_baseline_numbers() -> None:
    rows = rc.parse_baseline_numbers(SAMPLE)
    assert set(rows) == {
        ("TECH", "monthly"),
        ("TECH", "tax"),
        ("US", "monthly"),
        ("US", "tax"),
    }
    assert rows[("US", "monthly")]["eur_ledger"] == 66.69
    assert rows[("TECH", "monthly")]["terminal_fx"] == 57.01
    assert rc.parse_baseline_numbers("nothing here") == {}


def test_gate_parser_and_cross_checks() -> None:
    assert rc.parse_gate_text("WAIT 1/5 verified") == (1, 5)
    assert rc.parse_gate_text("PASS 5/5") == (5, 5)
    assert rc.parse_gate_text("garbage") is None

    with TemporaryDirectory() as td:
        tmp = Path(td)
        forward = tmp / "forward_status.md"
        audit = tmp / "paper_audit.csv"
        forward.write_text(FORWARD)
        audit.write_text(AUDIT)
        status, detail = rc.check_gate_results(
            forward, audit, {("monthly", "US"): 0, ("monthly", "TECH"): 1}
        )
        assert status == "WAIT", detail
        assert "monthly/US=0/5" in detail
        assert "next_session=2026-11-27" in detail
        # A changed honest count is still WAIT, with an annotation, not a fail.
        changed, changed_detail = rc.check_gate_results(
            forward, audit, {("monthly", "US"): 1, ("monthly", "TECH"): 1}
        )
        assert changed == "WAIT" and "release snapshot" in changed_detail
        # A positive status count without a matching audit row is a hard fail.
        forward.write_text(FORWARD.replace("WAIT 0/5", "WAIT 1/5"))
        bad, why = rc.check_gate_results(
            forward, audit, {("monthly", "US"): 0, ("monthly", "TECH"): 1}
        )
        assert bad == "FAIL" and "audit 0 != status 1" in why


def test_forward_gate_wait_and_blocked() -> None:
    with TemporaryDirectory() as td:
        tmp = Path(td)
        gate = tmp / "forward_gate.md"
        audit = tmp / "paper_audit.csv"
        gate.write_text(GATE)
        audit.write_text(AUDIT)
        expected = {
            ("monthly", "US"): 0,
            ("monthly", "TECH"): 1,
            ("tax", "TECH"): 0,
        }
        status, detail = rc.check_forward_gate(gate, audit, expected)
        assert status == "WAIT", detail
        assert "monthly/US=WAIT 0/5" in detail
        assert "next=2026-11-27" in detail

        # An explicit BLOCKED status is a release failure even with a 0/5 count.
        gate.write_text(
            GATE.replace("| monthly | US | WAIT |", "| monthly | US | BLOCKED |")
        )
        status, detail = rc.check_forward_gate(gate, audit, expected)
        assert status == "FAIL" and "BLOCKED" in detail

        # A positive count must be backed by the same audit dates.
        gate.write_text(
            GATE.replace(
                "| monthly | US | WAIT | 0/5 |", "| monthly | US | WAIT | 1/5 |"
            )
        )
        status, detail = rc.check_forward_gate(gate, audit, expected)
        assert status == "FAIL" and "audit 0 != status 1" in detail


def test_short3_verdict_missing_pending_and_reject() -> None:
    with TemporaryDirectory() as td:
        path = Path(td) / "short3.md"
        status, detail = rc.check_short3(path)
        assert status == "WAIT" and "not present" in detail
        path.write_text("# Short III\n\n**Verdict: reject.** No satellite.\n")
        status, detail = rc.check_short3(path)
        assert status == "PASS" and "reject" in detail
        path.write_text("# Short III\n\nNo conclusion.\n")
        status, detail = rc.check_short3(path)
        assert status == "FAIL" and "no explicit verdict" in detail
    assert rc.parse_short3_verdict("**Verdict:** paper") == "paper"
    assert rc.parse_short3_verdict("nothing") is None


def test_release_exit_codes_and_markdown() -> None:
    report = rc.ReleaseReport(
        snapshot="2026-11-25",
        commit="abcdef0",
        generated_at="2026-11-26T00:00:00+00:00",
        checks=[rc.Check("tests", "PASS", "all pass", 1.0, "python tests")],
    )
    assert report.ok and report.exit_code == 0
    assert report.counts == {"PASS": 1}
    report.checks.append(rc.Check("forward_gate", "WAIT", "US 0/5", 0.0))
    assert report.ok and report.exit_code == 0
    text = rc.render_markdown(report)
    assert "# Release check" in text
    assert "**PASS**" in text
    assert "1 WAIT" in text
    assert "Release checklist" in text
    assert "forward gate honesty" in text

    report.checks.append(rc.Check("broken", "FAIL", "bad", 0.0))
    assert not report.ok and report.exit_code == 2
    assert "FAIL" in rc.render_markdown(report)


def test_secret_scan_and_cache_detection() -> None:
    with TemporaryDirectory() as td:
        tmp = Path(td)
        clean = tmp / "clean.md"
        dirty = tmp / "dirty.env"
        clean.write_text("STONKS_ALERT_WEBHOOK_URL=\n")
        dirty.write_text("token=ghp_" + "a" * 36 + "\n")
        assert rc.scan_for_secrets([clean]) == []
        assert rc.scan_for_secrets([dirty]) == [str(dirty)]
        assert rc.cache_missing(tmp)


def main() -> None:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"ok: {fn.__name__}")
    print(f"ok: {len(tests)} release-check tests")


if __name__ == "__main__":
    main()
