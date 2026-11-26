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
TECH monthly native  51.82x  terminal-FX  57.01x  EUR-ledger  52.98x
TECH tax     native  46.26x  terminal-FX  50.89x  EUR-ledger  48.80x
US   monthly native  64.41x  terminal-FX  70.86x  EUR-ledger  66.17x
US   tax     native  46.26x  terminal-FX  50.89x  EUR-ledger  48.80x
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


def test_parse_baseline_numbers() -> None:
    rows = rc.parse_baseline_numbers(SAMPLE)
    assert set(rows) == {
        ("TECH", "monthly"),
        ("TECH", "tax"),
        ("US", "monthly"),
        ("US", "tax"),
    }
    assert rows[("US", "monthly")]["eur_ledger"] == 66.17
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
