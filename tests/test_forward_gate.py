"""Forward-gate tests: counting, duplicates, missing files, holidays, status.

All fixtures are temporary; no cache/network access is required because the
benchmark freshness is injected.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import forward_gate as fg  # noqa: E402


def _audit(rows):
    return pd.DataFrame(
        rows,
        columns=["book", "market", "date", "verified"],
    )


def _paper(rows, book="monthly"):
    df = pd.DataFrame(
        rows,
        columns=["market", "quote_date", "close_valid", "equity"],
    )
    path = Path("paper_daily.csv") if book == "monthly" else Path("paper_daily_tax.csv")
    return path, df


def _write(root: Path, audit: pd.DataFrame | None, paper: pd.DataFrame | None):
    if audit is not None:
        audit.to_csv(root / "paper_audit.csv", index=False)
    if paper is not None:
        paper.to_csv(root / "paper_daily.csv", index=False)


def _five_verified(start="2026-11-19", end="2026-11-25"):
    dates = pd.bdate_range(start, end)
    return [("monthly", "TECH", str(d.date()), True) for d in dates], [
        ("TECH", str(d.date()), True, 10000.0 + i) for i, d in enumerate(dates)
    ]


def test_pass_and_wait_counts():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        audit, paper = _five_verified()
        _write(root, _audit(audit), _paper(paper)[1])
        report = fg.collect_gate(
            results_dir=root,
            books=[("monthly", "TECH")],
            now="2026-11-26T22:00:00+00:00",  # Thanksgiving, next session 11-27
            bench_last="2026-11-25",
        )
        assert report.status == fg.PASS, report.lines()
        book = report.books[0]
        assert book.verified == 5 and book.status == fg.PASS
        assert book.next_required == "2026-11-27"
        assert book.equity == 10004.0
        assert book.drawdown is not None

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        audit, paper = _five_verified()
        _write(root, _audit(audit[-1:]), _paper(paper[-1:])[1])
        report = fg.collect_gate(
            results_dir=root,
            books=[("monthly", "TECH")],
            now="2026-11-26T22:00:00+00:00",
            bench_last="2026-11-25",
        )
        assert report.status == fg.WAIT
        assert report.books[0].verified == 1


def test_nonverified_and_duplicate_dates():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        audit = _audit(
            [
                ("monthly", "TECH", "2026-11-24", True),
                ("monthly", "TECH", "2026-11-24", False),  # conflict + duplicate
                ("monthly", "TECH", "2026-11-25", False),
            ]
        )
        paper = pd.DataFrame(
            [
                ("TECH", "2026-11-24", True, 10000.0),
                ("TECH", "2026-11-25", False, 9990.0),
            ],
            columns=["market", "quote_date", "close_valid", "equity"],
        )
        _write(root, audit, paper)
        report = fg.collect_gate(
            results_dir=root,
            books=[("monthly", "TECH")],
            now="2026-11-26T22:00:00+00:00",
            bench_last="2026-11-25",
        )
        assert report.status == fg.BLOCKED
        assert report.books[0].status == fg.BLOCKED
        assert any("duplicate" in x for x in report.books[0].issues)
        assert report.books[0].verified == 1
        assert report.books[0].rejected == 1


def test_missing_files_block_required_book():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        report = fg.collect_gate(
            results_dir=root,
            books=[("monthly", "US")],
            now="2026-11-26T22:00:00+00:00",
            bench_last="2026-11-25",
        )
        assert report.status == fg.BLOCKED
        assert "missing" in report.issues[0]

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        audit, paper = _five_verified()
        _write(root, _audit(audit), None)  # no paper daily
        report = fg.collect_gate(
            results_dir=root,
            books=[("monthly", "TECH")],
            now="2026-11-26T22:00:00+00:00",
            bench_last="2026-11-25",
        )
        assert report.status == fg.BLOCKED
        assert any("paper_daily" in x for x in report.books[0].issues)


def test_holiday_and_missed_session_logic():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write(
            root,
            _audit([]),
            pd.DataFrame(
                [("US", "2026-11-25", False, 10000.0)],
                columns=["market", "quote_date", "close_valid", "equity"],
            ),
        )
        # Thanksgiving: no completed session on 11-26; next is 11-27.
        report = fg.collect_gate(
            results_dir=root,
            books=[("monthly", "US")],
            now="2026-11-26T14:00:00+00:00",
            bench_last="2026-11-25",
        )
        assert report.books[0].next_required == "2026-11-27"
        assert report.status == fg.WAIT, report.lines()

        # 11-27 after the close with no verified row is a missed required session.
        missed = fg.collect_gate(
            results_dir=root,
            books=[("monthly", "US")],
            now="2026-11-27T22:00:00+00:00",
            bench_last="2026-11-27",
        )
        assert missed.status == fg.BLOCKED
        assert any("missed required" in x for x in missed.books[0].issues)

        # Before the 11-27 close it is still WAIT, not blocked.
        before = fg.collect_gate(
            results_dir=root,
            books=[("monthly", "US")],
            now="2026-11-27T15:00:00+00:00",
            bench_last="2026-11-25",
        )
        assert before.status == fg.WAIT


def test_stale_benchmark_blocks():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        audit, paper = _five_verified()
        _write(root, _audit(audit), _paper(paper)[1])
        report = fg.collect_gate(
            results_dir=root,
            books=[("monthly", "TECH")],
            now="2026-11-26T22:00:00+00:00",
            bench_last="2026-11-24",
        )
        assert report.status == fg.BLOCKED
        assert any("stale data" in x for x in report.books[0].issues)


def test_drawdown_equity_and_since():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        audit = _audit(
            [
                ("monthly", "TECH", "2026-11-20", True),
                ("monthly", "TECH", "2026-11-24", True),
                ("monthly", "TECH", "2026-11-25", False),
            ]
        )
        paper = pd.DataFrame(
            [
                ("TECH", "2026-11-20", True, 10000.0),
                ("TECH", "2026-11-24", True, 9000.0),
                ("TECH", "2026-11-25", True, 9500.0),
            ],
            columns=["market", "quote_date", "close_valid", "equity"],
        )
        _write(root, audit, paper)
        report = fg.collect_gate(
            results_dir=root,
            books=[("monthly", "TECH")],
            now="2026-11-26T22:00:00+00:00",
            bench_last="2026-11-25",
            since="2026-11-20",
        )
        b = report.books[0]
        assert b.verified == 2  # 11-25 is not verified
        assert abs(b.drawdown + 0.10) < 1e-9
        assert b.equity == 9500.0
        assert b.new_verified == ["2026-11-24"]


def test_status_write_is_report_only_and_idempotent():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        audit, paper = _five_verified()
        _write(root, _audit(audit), _paper(paper)[1])
        before = (
            (root / "paper_audit.csv").read_bytes(),
            (root / "paper_daily.csv").read_bytes(),
        )
        report = fg.collect_gate(
            results_dir=root,
            books=[("monthly", "TECH")],
            now="2026-11-26T22:00:00+00:00",
            bench_last="2026-11-25",
        )
        fg._write_status(report, root / "forward_gate.md")
        after = (
            (root / "paper_audit.csv").read_bytes(),
            (root / "paper_daily.csv").read_bytes(),
        )
        assert before == after
        text = (root / "forward_gate.md").read_text()
        assert "Forward gate status" in text and "PASS" in text
        first = text
        fg._write_status(report, root / "forward_gate.md")
        assert (root / "forward_gate.md").read_text() == first


def test_cli_json_and_exit_codes():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        audit, paper = _five_verified()
        _write(root, _audit(audit[-1:]), _paper(paper[-1:])[1])
        import contextlib
        import io

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = fg.main(
                [
                    "--results-dir",
                    str(root),
                    "--book",
                    "monthly/TECH",
                    "--now",
                    "2026-11-26T22:00:00+00:00",
                    "--bench-last",
                    "2026-11-25",
                    "--json",
                ]
            )
        assert rc == fg.EXIT_WAIT
        payload = json.loads(buf.getvalue())
        assert payload["status"] == "WAIT"
        assert payload["books"][0]["verified"] == 1


if __name__ == "__main__":
    test_pass_and_wait_counts()
    test_nonverified_and_duplicate_dates()
    test_missing_files_block_required_book()
    test_holiday_and_missed_session_logic()
    test_stale_benchmark_blocks()
    test_drawdown_equity_and_since()
    test_status_write_is_report_only_and_idempotent()
    test_cli_json_and_exit_codes()
    print("test_forward_gate: all checks passed")
