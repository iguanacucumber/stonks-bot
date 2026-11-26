"""Dashboard parsing, degradation, gate counts and escaping tests.

Every fixture is temporary; no cache, state directory or network is touched.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import dashboard as dashboard_cli  # noqa: E402
from bot.dashboard.collect import (  # noqa: E402
    Finding,
    collect_dashboard,
    demo_dashboard,
    gates_from_audit,
    parse_forward_status,
)
from bot.dashboard.render import render_html, render_markdown  # noqa: E402


FORWARD = """# Forward paper status

Generated: 2026-11-26T16:03:36+00:00
Session: 2026-11-25
Mode: paper
Overall: **OK**

| book | market | live | orders | reconcile | paper dates | verified gate |
|---|---|---|---:|---|---:|---|
| monthly | US | ok | 6 | OK | 0 | WAIT 0/5 verified |
| monthly | TECH | ok | 6 | OK | 1 | WAIT 1/5 verified |
"""


def _fixture(tmp: Path, *, audit: str | None = None, state: str | None = None) -> None:
    results = tmp / "results"
    results.mkdir(parents=True)
    (results / "forward_status.md").write_text(FORWARD)
    if audit is not None:
        (results / "paper_audit.csv").write_text(audit)
    if state is not None:
        live = tmp / "state" / "live"
        live.mkdir(parents=True)
        (live / "monthly_us.json").write_text(state)


def test_forward_status_and_gate_counts() -> None:
    with TemporaryDirectory() as td:
        tmp = Path(td)
        path = tmp / "forward.md"
        path.write_text(FORWARD)
        parsed, warnings = parse_forward_status(path)
        assert not warnings
        assert parsed["session"] == "2026-11-25"
        assert parsed["mode"] == "paper"
        assert [r["book"] for r in parsed["books"]] == ["monthly", "monthly"]
        assert parsed["books"][1]["verified_gate"] == "WAIT 1/5 verified"

        audit = tmp / "paper_audit.csv"
        audit.write_text(
            "book,market,date,verified\n"
            "monthly,US,2026-11-20,true\n"
            "monthly,US,2026-11-21,true\n"
            "monthly,US,2026-11-21,true\n"
            "monthly,US,2026-11-24,false\n"
            "monthly,TECH,2026-11-20,true\n"
        )
        gates, gate_warnings = gates_from_audit(audit)
        assert not gate_warnings
        assert gates[("monthly", "US")].verified == 2
        assert "WAIT 2/5" in gates[("monthly", "US")].text
        assert gates[("monthly", "TECH")].verified == 1


def test_collect_degrades_for_missing_and_corrupt_files() -> None:
    with TemporaryDirectory() as td:
        tmp = Path(td)
        (tmp / "results").mkdir()
        empty = collect_dashboard(tmp, now="2026-11-26T22:00:00+00:00")
        assert empty.overall == "WARN"
        assert empty.books == []
        assert any(f.code == "forward_status" for f in empty.findings)
        assert "DASHBOARD" not in render_html(empty)  # no exception, real page below
        assert "No live books" in render_html(empty)

    with TemporaryDirectory() as td:
        tmp = Path(td)
        (tmp / "results").mkdir()
        (tmp / "results" / "forward_status.md").write_text("not a status table")
        (tmp / "state" / "live").mkdir(parents=True)
        (tmp / "state" / "live" / "bad.json").write_text("{broken")
        broken = collect_dashboard(tmp)
        assert broken.overall == "WARN"
        assert any(
            f.code in {"state_corrupt", "forward_status"} for f in broken.findings
        )
        assert any(b.status == "corrupt" for b in broken.books)
        assert "corrupt" in render_html(broken)


def test_collect_reads_forward_fallback_and_eval() -> None:
    with TemporaryDirectory() as td:
        tmp = Path(td)
        _fixture(
            tmp,
            audit="book,market,date,verified\nmonthly,TECH,2026-11-25,true\n",
        )
        (tmp / "results" / "eval_baseline.json").write_text(
            json.dumps(
                {
                    "rows": [
                        {
                            "market": "TECH",
                            "book": "monthly",
                            "native": 51.82,
                            "terminal_fx": 57.01,
                            "eur_ledger": 53.42,
                        }
                    ]
                }
            )
        )
        (tmp / "results" / "eval_data_quality.json").write_text(
            json.dumps({"ok": True, "hard_failures": [], "warning_counts": {}})
        )
        data = collect_dashboard(tmp, now="2026-11-26T22:00:00+00:00")
        assert {b.book for b in data.books} == {"monthly"}
        assert len(data.books) == 2
        assert data.books[0].source == "forward_status"
        assert data.books[0].gate.verified == 0
        assert any(f.code == "eval" for f in data.findings)
        text = render_markdown(data)
        assert "Books, risk, data and gate" in text
        assert "WAIT 0/5 verified" in text
        assert "EUR ledger (real-life)" in text


def test_html_escaping_and_synthetic_banner() -> None:
    data = demo_dashboard(now="2026-11-26T22:00:00+00:00")
    data.books[0].book = "<script>alert('x')</script>"
    data.findings.append(
        Finding("WARN", "xss", "<img src=x onerror=alert(1)>", "<src>")
    )
    html = render_html(data)
    assert "<script>alert('x')</script>" not in html
    assert "&lt;script&gt;alert(&#x27;x&#x27;)&lt;/script&gt;" in html
    assert "<img src=x" not in html
    assert "SYNTHETIC DEMO" in html
    assert "http://" not in html and "https://" not in html
    markdown = render_markdown(data)
    assert "SYNTHETIC DEMO" in markdown
    assert "|" in markdown


def test_alert_filters_warn_and_fail_only() -> None:
    data = demo_dashboard(now="2026-11-26T22:00:00+00:00")
    data.findings = [
        Finding("INFO", "quiet", "not an incident"),
        Finding("WARN", "warn", "needs attention"),
        Finding("FAIL", "fail", "broken"),
    ]

    class FakeManager:
        def __init__(self):
            self.payload = None
            self.dry_run = None

        def describe(self):
            return ["fake"]

        def send(self, payload, *, dry_run=True):
            self.payload = payload
            self.dry_run = dry_run
            return [SimpleNamespace(channel="fake", ok=True, detail="dry-run")]

    manager = FakeManager()
    with patch.object(dashboard_cli, "build_manager", return_value=manager):
        assert dashboard_cli._alert(data, send=False) == 0
    assert manager.payload is not None
    assert manager.payload.severity == "critical"
    kinds = {f["level"] for f in manager.payload.context["findings"]}
    assert kinds == {"WARN", "FAIL"}
    assert manager.dry_run is True


def test_check_and_demo_cli_are_static() -> None:
    assert dashboard_cli.main(["--check", "--demo"]) == 0
    with TemporaryDirectory() as td:
        tmp = Path(td)
        html = tmp / "dash.html"
        md = tmp / "dash.md"
        assert (
            dashboard_cli.main(["--demo", "--html", str(html), "--markdown", str(md)])
            == 0
        )
        assert html.exists() and md.exists()
        assert "SYNTHETIC DEMO" in html.read_text()
        assert "SYNTHETIC DEMO" in md.read_text()


def main() -> None:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"ok: {fn.__name__}")
    print(f"ok: {len(tests)} dashboard tests")


if __name__ == "__main__":
    main()
