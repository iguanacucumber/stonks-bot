"""Acceptance harness tests: no-cache skip/fail, table and JSON shape.

Run: uv run python tests/test_acceptance.py
"""

from __future__ import annotations

import json
import sys
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import acceptance as acc  # noqa: E402


def test_cache_missing_on_empty_dir() -> None:
    with TemporaryDirectory() as tmp:
        missing = acc.cache_missing(Path(tmp))
    assert missing
    assert any("panel.parquet" in x for x in missing)
    assert any("membership.parquet" in x for x in missing)
    assert any(x.startswith("bars/") for x in missing)


def test_no_cache_skip_and_fail() -> None:
    with TemporaryDirectory() as tmp:
        skipped = acc.run_checks(Path(tmp), cache_ok=False, allow_missing_cache=True)
        failed = acc.run_checks(Path(tmp), cache_ok=False, allow_missing_cache=False)
    assert len(skipped) == 12
    assert all(c.status == "SKIP" for c in skipped)
    assert len(failed) == 12
    assert all(c.status == "FAIL" for c in failed)
    assert "SKIP" in acc.render_text(skipped)
    assert "FAIL" in acc.render_text(failed)
    assert acc.render_text(skipped).startswith("ACCEPTANCE PASS")
    assert acc.render_text(failed).startswith("ACCEPTANCE FAIL")


def test_json_shape_and_exit_codes() -> None:
    old = acc.CACHE
    with TemporaryDirectory() as tmp:
        acc.CACHE = Path(tmp)  # simulate a cache-less checkout
        try:
            out = StringIO()
            with redirect_stdout(out):
                rc_ok = acc.main(["--allow-missing-cache", "--json"])
            payload = json.loads(out.getvalue())
            assert rc_ok == 0
            assert payload["ok"] is True
            assert payload["cache_missing"]
            assert all(c["status"] == "SKIP" for c in payload["checks"])
            assert all(
                {"name", "status", "detail", "seconds"} <= set(c)
                for c in payload["checks"]
            )
        finally:
            acc.CACHE = old


def test_cache_status_rejects_partial_and_stale() -> None:
    with TemporaryDirectory() as td:
        root = Path(td)
        bars = root / "bars"
        bars.mkdir()
        (bars / "SPY.csv").write_text("date\n2026-11-25\n")
        missing, snapshot = acc.cache_status(root)
        assert snapshot is None
        assert any(x.startswith("bars/") for x in missing)
        # Fill the exact required set but leave QQQ stale.
        for sym in acc.reproduce_cli.required_symbols():
            (bars / f"{sym.replace('/', '_')}.csv").write_text("date\n2026-11-24\n")
        (root / "panel.parquet").write_bytes(b"x")
        (root / "membership.parquet").write_bytes(b"x")
        missing, snapshot = acc.cache_status(root)
        assert snapshot == "2026-11-24"
        assert any(x.startswith("snapshot ") for x in missing)
        # Fresh snapshot is ready.
        (bars / "QQQ.csv").write_text("date\n2026-11-25\n")
        missing, snapshot = acc.cache_status(root)
        assert missing == [] and snapshot == "2026-11-25"


def test_no_cache_skip_writes_nothing() -> None:
    with TemporaryDirectory() as td:
        tmp = Path(td)
        checks = acc.run_checks(tmp, cache_ok=False, allow_missing_cache=True)
        assert all(c.status == "SKIP" for c in checks)
        assert list(tmp.iterdir()) == []


def test_check_serialization() -> None:
    c = acc.Check("name", "PASS", "detail", 0.1234)
    assert c.to_dict() == {
        "name": "name",
        "status": "PASS",
        "detail": "detail",
        "seconds": 0.123,
    }


def main() -> None:
    test_cache_missing_on_empty_dir()
    test_no_cache_skip_and_fail()
    test_json_shape_and_exit_codes()
    test_cache_status_rejects_partial_and_stale()
    test_no_cache_skip_writes_nothing()
    test_check_serialization()
    print("ok: acceptance no-cache skip/fail, JSON and exit codes")


if __name__ == "__main__":
    main()
