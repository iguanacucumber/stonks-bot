"""Forward proof lockbox and watchdog tests.

Everything is synthetic and temporary: no cache, network, real state or real
``results/`` file is touched. The fake clock is explicit so the 2026-11-27
half-day boundary is proven without writing a real row.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import forward_gate as fg  # noqa: E402
import forward_watchdog as fw  # noqa: E402
from bot.live import calendar, proof  # noqa: E402

AUDIT_COLUMNS = ["book", "market", "date", "verified"]
PAPER_COLUMNS = ["market", "quote_date", "close_valid", "equity"]


def _audit(rows):
    return pd.DataFrame(rows, columns=AUDIT_COLUMNS)


def _paper(rows):
    return pd.DataFrame(rows, columns=PAPER_COLUMNS)


def _write(
    root: Path,
    audit_rows,
    paper_rows,
    *,
    tax_rows=None,
    daily_session="2026-11-25",
    daily_overall="OK",
):
    root.mkdir(parents=True, exist_ok=True)
    _audit(audit_rows).to_csv(root / "paper_audit.csv", index=False)
    _paper(paper_rows).to_csv(root / "paper_daily.csv", index=False)
    (root / "paper_daily_tax.csv").write_text(
        _paper(tax_rows or paper_rows).to_csv(index=False)
    )
    (root / "forward_status.md").write_text(
        "# Forward paper status\n\n"
        "Generated: 2026-11-26T22:00:00+00:00\n"
        f"Session: {daily_session}\n"
        "Mode: paper\n"
        f"Overall: **{daily_overall}**\n\n"
        "| book | market | live | orders | reconcile | paper dates | verified gate |\n"
        "|---|---|---|---:|---|---:|---|\n"
        "| monthly | US | ok | 1 | OK | 1 | WAIT 1/5 verified |\n"
        "| monthly | TECH | ok | 1 | OK | 1 | WAIT 1/5 verified |\n"
        "| tax | TECH | ok | 1 | OK | 1 | WAIT 1/5 verified |\n"
    )


def _gate(root: Path, now: str, bench: str = "2026-11-25", books=None):
    return fg.collect_gate(
        results_dir=root,
        now=now,
        bench_last=bench,
        state_dir=root / "state",
        books=books,
    )


def _book(report, book="monthly", market="TECH"):
    return next(b for b in report.books if (b.book, b.market) == (book, market))


def _lock(root: Path, report, command="uv run python scripts/forward_gate.py --lock"):
    return proof.append_from_report(
        report,
        results_dir=root,
        state_dir=root / "state",
        lockbox=root / "forward_proof.jsonl",
        repo_root=root,
        command=command,
        now=report.now,
    )


@dataclass
class _FakeBook:
    book: str
    market: str
    status: str = "WAIT"
    verified: int = 1
    target: int = 5
    verified_dates: list[str] = field(default_factory=list)


@dataclass
class _FakeReport:
    status: str
    session: str
    now: str
    books: list[_FakeBook]


def test_lockbox_records_real_verified_session_and_hashes():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        rows = [("monthly", "TECH", "2026-11-25", True)]
        paper = [("TECH", "2026-11-25", True, 10000.0)]
        _write(root, rows, paper)
        report = _gate(root, "2026-11-26T22:00:00+00:00", "2026-11-25")
        assert report.status == fg.WAIT
        result = _lock(
            root, report, command="uv run python scripts/forward_gate.py --lock"
        )
        assert len(result.appended) == 1
        assert result.records == 1 and result.last == "2026-11-25"
        rec = proof.load_records(root / "forward_proof.jsonl")[0]
        assert rec["date"] == "2026-11-25"
        assert (rec["book"], rec["market"]) == ("monthly", "TECH")
        assert rec["gate"] == {
            "verified": 1,
            "observed": 1,
            "target": 5,
            "status": "WAIT",
        }
        assert rec["files"]["paper_audit"]["sha256"] == proof.sha256_file(
            root / "paper_audit.csv"
        )
        assert rec["files"]["paper_daily"]["sha256"] == proof.sha256_file(
            root / "paper_daily.csv"
        )
        assert rec["files"]["live_state"] == {
            "path": "state/monthly_tech.json",
            "sha256": None,
            "present": False,
        }
        assert rec["verification"]["session"] == "2026-11-25"
        assert rec["verification"]["close_valid"] is True
        assert rec["verification"]["command"].endswith("--lock")
        assert rec["prev_hash"] == proof.GENESIS
        assert rec["record_hash"] == proof.compute_record_hash(rec)
        # Idempotent: a second lock never writes a duplicate row.
        again = _lock(root, report)
        assert again.appended == []
        assert len(again.skipped) == 1
        assert again.records == 1
        assert len((root / "forward_proof.jsonl").read_text().splitlines()) == 1
        # Re-hashing the current evidence is clean.
        assert proof.verify_files(rec, root) == []


def test_lockbox_rejects_audit_only_future_holiday_and_duplicate():
    # Audit-only: a verified audit row with no close-valid paper row.
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write(
            root,
            [("monthly", "TECH", "2026-11-25", True)],
            [("TECH", "2026-11-25", False, 9990.0)],
        )
        report = _gate(root, "2026-11-26T22:00:00+00:00", "2026-11-25")
        assert report.status == fg.BLOCKED
        assert "close-valid" in " ".join(_book(report).issues)
        try:
            _lock(root, report)
        except proof.ProofError:
            pass
        else:
            raise AssertionError("audit-only report must not lock")
        assert not (root / "forward_proof.jsonl").exists()

        # The proof module itself rejects an audit-only row even if a caller
        # supplies a synthetic WAIT report.
        fake = _FakeReport(
            status="WAIT",
            session="2026-11-25",
            now="2026-11-26T22:00:00+00:00",
            books=[_FakeBook("monthly", "TECH", verified_dates=["2026-11-25"])],
        )
        try:
            _lock(root, fake)
        except proof.ProofError as exc:
            assert "close-valid" in str(exc)
        else:
            raise AssertionError("proof must independently reject audit-only rows")

    # Future: 11-27 cannot count on 11-26; the gate excludes it and blocks.
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write(
            root,
            [("monthly", "TECH", "2026-11-27", True)],
            [("TECH", "2026-11-27", True, 10100.0)],
        )
        report = _gate(root, "2026-11-26T22:00:00+00:00", "2026-11-25")
        assert report.status == fg.BLOCKED
        assert any("non-session/future" in x for x in _book(report).issues)
        try:
            _lock(root, report)
        except proof.ProofError:
            pass
        else:
            raise AssertionError("future date must not lock")
        assert not (root / "forward_proof.jsonl").exists()

    # Holiday: Thanksgiving is not a session even with close_valid=True.
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write(
            root,
            [("monthly", "TECH", "2026-11-26", True)],
            [("TECH", "2026-11-26", True, 10000.0)],
        )
        report = _gate(root, "2026-11-26T22:00:00+00:00", "2026-11-25")
        assert report.status == fg.BLOCKED
        assert any("non-session" in x for x in _book(report).issues)
        try:
            _lock(root, report)
        except proof.ProofError:
            pass
        else:
            raise AssertionError("holiday must not lock")
        assert not (root / "forward_proof.jsonl").exists()

    # Duplicate audit dates are excluded and rejected, not averaged.
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write(
            root,
            [
                ("monthly", "TECH", "2026-11-25", True),
                ("monthly", "TECH", "2026-11-25", True),
            ],
            [("TECH", "2026-11-25", True, 10000.0)],
        )
        report = _gate(root, "2026-11-26T22:00:00+00:00", "2026-11-25")
        assert report.status == fg.BLOCKED
        assert _book(report).verified == 0
        try:
            proof.audit_verified_dates(root, "monthly", "TECH")
        except proof.ProofError as exc:
            assert "duplicate" in str(exc)
        else:
            raise AssertionError("duplicate audit date must be rejected")
        try:
            _lock(root, report)
        except proof.ProofError:
            pass
        else:
            raise AssertionError("duplicate report must not lock")
        assert not (root / "forward_proof.jsonl").exists()


def test_half_day_2026_11_27_only_locks_after_1330_et():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write(
            root,
            [("monthly", "TECH", "2026-11-27", True)],
            [("TECH", "2026-11-27", True, 10010.0)],
        )
        # 13:29 ET: the half-day has not completed; 11-27 is still future.
        before = _gate(
            root,
            "2026-11-27T18:29:00+00:00",
            "2026-11-25",
            books=[("monthly", "TECH")],
        )
        assert before.session == "2026-11-25"
        assert before.status == fg.BLOCKED
        try:
            _lock(root, before)
        except proof.ProofError:
            pass
        else:
            raise AssertionError("half-day must not lock before 13:30 ET")
        assert not (root / "forward_proof.jsonl").exists()

        # Even a synthetic report that falsely claims 11-27 is complete at
        # 13:29 ET is rejected by the lockbox's own close-time check.
        fake = _FakeReport(
            status="WAIT",
            session="2026-11-27",
            now="2026-11-27T18:29:00+00:00",
            books=[_FakeBook("monthly", "TECH", verified_dates=["2026-11-27"])],
        )
        try:
            _lock(root, fake)
        except proof.ProofError as exc:
            assert "before" in str(exc) and "completed" in str(exc)
        else:
            raise AssertionError("proof must reject a pre-close half-day record")
        assert not (root / "forward_proof.jsonl").exists()

        # 13:30 ET: the half-day plus 30-minute data delay is complete.
        after = _gate(
            root,
            "2026-11-27T18:30:00+00:00",
            "2026-11-27",
            books=[("monthly", "TECH")],
        )
        assert after.session == "2026-11-27"
        assert after.status == fg.WAIT
        result = _lock(root, after)
        assert [r["date"] for r in result.appended] == ["2026-11-27"]
        rec = proof.load_records(root / "forward_proof.jsonl")[0]
        assert rec["verification"]["session"] == "2026-11-27"
        assert rec["recorded_at"] == "2026-11-27T18:30:00+00:00"
        assert calendar.is_half_day("2026-11-27")

        # The same record cannot be loaded at 13:29 ET as if it existed then.
        try:
            proof.load_records(
                root / "forward_proof.jsonl", now="2026-11-27T18:29:00+00:00"
            )
        except proof.ProofError as exc:
            assert "future" in str(exc)
        else:
            raise AssertionError("future-dated proof must not load")


def test_lockbox_chain_tamper_and_truncation_are_detected():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write(
            root,
            [
                ("monthly", "TECH", "2026-11-24", True),
                ("monthly", "TECH", "2026-11-25", True),
            ],
            [
                ("TECH", "2026-11-24", True, 10000.0),
                ("TECH", "2026-11-25", True, 10010.0),
            ],
        )
        report = _gate(root, "2026-11-26T22:00:00+00:00", "2026-11-25")
        result = _lock(root, report)
        assert [r["date"] for r in result.appended] == ["2026-11-24", "2026-11-25"]
        assert [r["gate"]["verified"] for r in result.appended] == [1, 2]
        path = root / "forward_proof.jsonl"
        text = path.read_text()
        assert len(text.splitlines()) == 2

        # Edit any byte in the first record: the record hash no longer matches.
        tampered = text.replace('"2026-11-24"', '"2026-11-23"', 1)
        path.write_text(tampered)
        try:
            proof.load_records(path)
        except proof.ProofError as exc:
            assert "hash mismatch" in str(exc) or "not an NYSE session" in str(exc)
        else:
            raise AssertionError("tampered record must fail")

        # Truncated final line is a failed append, not a valid record.
        path.write_text(text.rstrip("\n"))
        try:
            proof.load_records(path)
        except proof.ProofError as exc:
            assert "partial line" in str(exc)
        else:
            raise AssertionError("truncated lockbox must fail")

        # Rebuilt valid chain, then a duplicate key inserted with a valid hash:
        # the duplicate guard, not just the chain, must reject it.
        path.write_text(text)
        records = proof.load_records(path)
        dup = dict(records[0])
        dup["prev_hash"] = records[-1]["record_hash"]
        dup["record_hash"] = proof.compute_record_hash(dup)
        path.write_text(text + json.dumps(dup, sort_keys=True) + "\n")
        try:
            proof.load_records(path)
        except proof.ProofError as exc:
            assert "duplicate" in str(exc) or "chain break" in str(exc)
        else:
            raise AssertionError("duplicate proof record must fail")


def test_gate_does_not_skip_session_with_unverified_preview():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        # 11-27 preview exists but is not close-valid. It must not advance the
        # required list to 11-30 and hide the missed 11-27 session.
        _write(
            root,
            [("monthly", "TECH", "2026-11-25", True)],
            [
                ("TECH", "2026-11-25", True, 10000.0),
                ("TECH", "2026-11-27", False, 9990.0),
            ],
        )
        report = _gate(root, "2026-11-27T22:00:00+00:00", "2026-11-27")
        book = _book(report)
        assert book.verified_dates == ["2026-11-25"]
        assert book.last_paper == "2026-11-27"
        assert book.next_required == "2026-11-27"
        assert report.status == fg.BLOCKED
        assert any("missed required" in x for x in book.issues)


def test_watchdog_wait_blocked_daily_and_missed_session():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        rows = [
            ("monthly", "US", "2026-11-25", True),
            ("monthly", "TECH", "2026-11-25", True),
            ("tax", "TECH", "2026-11-25", True),
        ]
        paper = [
            ("US", "2026-11-25", True, 10000.0),
            ("TECH", "2026-11-25", True, 10010.0),
        ]
        _write(root, rows, paper)
        report = _gate(root, "2026-11-26T22:00:00+00:00", "2026-11-25")
        assert report.status == fg.WAIT
        _lock(root, report)
        ok = fw.collect_watchdog(
            results_dir=root,
            state_dir=root / "state",
            now="2026-11-26T22:00:00+00:00",
            bench_last="2026-11-25",
        )
        assert ok["ok"] and ok["status"] == fw.WAIT, ok
        assert ok["next_session"] == "2026-11-27"
        assert ok["next_session_half_day"] is True
        assert ok["next_close_hour_et"] == 13
        assert ok["eta_all_books"] == "2026-12-02"
        assert ok["lockbox"]["records"] == 3
        assert "13:30 ET" in ok["next_gate_time_et"]

        # The CLI exit code matters for the 21:45 cron: 0 on a healthy WAIT,
        # 2 on a missed expected session.
        import contextlib
        import io

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = fw.main(
                [
                    "--results-dir",
                    str(root),
                    "--state-dir",
                    str(root / "state"),
                    "--now",
                    "2026-11-26T22:00:00+00:00",
                    "--bench-last",
                    "2026-11-25",
                    "--json",
                ]
            )
        assert rc == 0
        with contextlib.redirect_stdout(io.StringIO()):
            missed_rc = fw.main(
                [
                    "--results-dir",
                    str(root),
                    "--state-dir",
                    str(root / "state"),
                    "--now",
                    "2026-11-30T22:00:00+00:00",
                    "--bench-last",
                    "2026-11-30",
                    "--json",
                ]
            )
        assert missed_rc == fw.EXIT_BLOCKED

        # Before the half-day close the watchdog remains a healthy WAIT.
        before = fw.collect_watchdog(
            results_dir=root,
            state_dir=root / "state",
            now="2026-11-27T18:29:00+00:00",
            bench_last="2026-11-25",
        )
        assert before["ok"] and before["status"] == fw.WAIT, before

        # At 13:30 ET the completed half-day has no verified row: missed.
        after = fw.collect_watchdog(
            results_dir=root,
            state_dir=root / "state",
            now="2026-11-27T18:30:00+00:00",
            bench_last="2026-11-27",
        )
        assert not after["ok"] and after["status"] == fw.BLOCKED, after
        assert any("missed" in x for x in after["issues"]), after

        # A later Monday with no run is a missed expected session.
        monday = fw.collect_watchdog(
            results_dir=root,
            state_dir=root / "state",
            now="2026-11-30T22:00:00+00:00",
            bench_last="2026-11-30",
        )
        assert not monday["ok"]
        assert any("missed" in x for x in monday["issues"]), monday

        # A blocked/ATTENTION daily status is non-zero even if the gate is WAIT.
        (root / "forward_status.md").write_text(
            (root / "forward_status.md").read_text().replace("**OK**", "**ATTENTION**")
        )
        blocked_daily = fw.collect_watchdog(
            results_dir=root,
            state_dir=root / "state",
            now="2026-11-26T22:00:00+00:00",
            bench_last="2026-11-25",
        )
        assert not blocked_daily["ok"]
        assert any("not OK" in x for x in blocked_daily["issues"])
        (root / "forward_status.md").write_text(
            (root / "forward_status.md").read_text().replace("**ATTENTION**", "**OK**")
        )

        # An unlocked verified date (deleted lockbox) is not allowed.
        (root / "forward_proof.jsonl").unlink()
        unlocked = fw.collect_watchdog(
            results_dir=root,
            state_dir=root / "state",
            now="2026-11-26T22:00:00+00:00",
            bench_last="2026-11-25",
        )
        assert not unlocked["ok"]
        assert any("lockbox" in x for x in unlocked["issues"])
        _lock(root, report)

        # Tampering with a lockbox line is non-zero.
        path = root / "forward_proof.jsonl"
        path.write_text(path.read_text().replace('"monthly"', '"monthly "', 1))
        tampered = fw.collect_watchdog(
            results_dir=root,
            state_dir=root / "state",
            now="2026-11-26T22:00:00+00:00",
            bench_last="2026-11-25",
        )
        assert not tampered["ok"]
        assert any("lockbox invalid" in x for x in tampered["issues"])


if __name__ == "__main__":
    test_lockbox_records_real_verified_session_and_hashes()
    test_lockbox_rejects_audit_only_future_holiday_and_duplicate()
    test_half_day_2026_11_27_only_locks_after_1330_et()
    test_lockbox_chain_tamper_and_truncation_are_detected()
    test_gate_does_not_skip_session_with_unverified_preview()
    test_watchdog_wait_blocked_daily_and_missed_session()
    print("test_forward_proof: all checks passed")
