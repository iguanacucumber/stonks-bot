#!/usr/bin/env python
"""Render the stonks-bot monitoring dashboard.

The page is static: one HTML file and one Markdown file, no server, JavaScript or
external assets.  Inputs are read-only: `state/live/*.json/csv`,
`results/forward_status.md`, `results/paper_audit.csv` and `results/eval_*`.

Examples:
  uv run python scripts/dashboard.py
  uv run python scripts/dashboard.py --check
  uv run python scripts/dashboard.py --html results/dashboard.html --markdown results/dashboard.md
  uv run python scripts/dashboard.py --demo --html results/dashboard.html --markdown results/dashboard.md
  uv run python scripts/dashboard.py --alert              # dry-run by default
  uv run python scripts/dashboard.py --alert --send       # requires configured channels

Alerting is off unless the STONKS_ALERT_* variables are configured.  `--alert`
collects only WARN/FAIL findings; `--send` is explicit and never required for a
static page.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot.dashboard import collect_dashboard, demo_dashboard  # noqa: E402
from bot.dashboard.render import render_html, render_markdown  # noqa: E402
from bot.ops.alerts import AlertManager, AlertPayload, build_manager  # noqa: E402

EXIT_OK = 0
EXIT_ERROR = 1


def _write(path: str, text: str) -> None:
    if path == "-":
        print(text)
        return
    target = Path(path)
    if not target.is_absolute():
        target = ROOT / target
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    print(f"wrote {target}")


def _alert(data, *, send: bool) -> int:
    problems = [f for f in data.findings if f.level in {"WARN", "FAIL"}]
    if not problems:
        print("alert: no WARN/FAIL findings; nothing sent")
        return EXIT_OK
    severity = "critical" if any(f.level == "FAIL" for f in problems) else "warning"
    payload = AlertPayload(
        kind="dashboard",
        severity=severity,
        title=f"Stonks dashboard {data.overall}: {len(problems)} finding(s)",
        message="; ".join(f"{f.level} {f.code}: {f.message}" for f in problems[:8]),
        context={
            "session": data.session,
            "next_session": data.next_session,
            "overall": data.overall,
            "findings": [f.to_dict() for f in problems[:20]],
        },
    )
    manager: AlertManager = build_manager()
    deliveries = manager.send(payload, dry_run=not send)
    print(
        f"alert: {severity}; channels={manager.describe() or ['none (off by default)']}"
    )
    for d in deliveries:
        print(f"  {d.channel}: {'OK' if d.ok else 'FAIL'} - {d.detail}")
    return EXIT_OK if all(d.ok for d in deliveries) else EXIT_ERROR


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--state-dir", default=None)
    ap.add_argument("--results-dir", default=None)
    ap.add_argument("--html", default=None, help="HTML output path, or - for stdout")
    ap.add_argument(
        "--markdown", default=None, help="Markdown output path, or - for stdout"
    )
    ap.add_argument("--json", action="store_true", help="print the collected model")
    ap.add_argument("--check", action="store_true", help="render in memory and exit")
    ap.add_argument(
        "--demo", action="store_true", help="render the labelled synthetic example"
    )
    ap.add_argument("--alert", action="store_true", help="send only WARN/FAIL findings")
    ap.add_argument(
        "--send", action="store_true", help="actually deliver --alert (default dry-run)"
    )
    ap.add_argument("--now", default=None, help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    if args.send and not args.alert:
        ap.error("--send requires --alert")
    try:
        data = (
            demo_dashboard(now=args.now)
            if args.demo
            else collect_dashboard(
                ROOT,
                state_dir=args.state_dir,
                results_dir=args.results_dir,
                now=args.now,
            )
        )
        html = render_html(data)
        markdown = render_markdown(data)
    except Exception as exc:  # noqa: BLE001 - CLI should report, not traceback
        print(f"ERROR: dashboard render failed: {exc}", file=sys.stderr)
        return EXIT_ERROR

    if args.json:
        print(json.dumps(data.to_dict(), indent=2, sort_keys=True, default=str))
    if args.check:
        print(
            f"DASHBOARD CHECK OK  books={len(data.books)}  "
            f"warnings={len(data.warnings)}  failures={len(data.failures)}  "
            f"synthetic={data.synthetic}"
        )
        return EXIT_OK

    html_path = args.html
    md_path = args.markdown
    if html_path is None and md_path is None:
        html_path = "results/dashboard.html"
        md_path = "results/dashboard.md"
    if html_path is not None:
        _write(html_path, html)
    if md_path is not None:
        _write(md_path, markdown)
    if args.alert:
        return _alert(data, send=args.send)
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
