"""Ops tests: doctor, backup/restore, alert payloads and failure drill.

Every fixture is a temporary directory.  No test reads the real cache, writes the
real state or opens a network connection.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot.live.config import LiveConfig  # noqa: E402
from bot.live.state import LiveState, StateStore  # noqa: E402
from bot.ops.alerts import build_manager, sample_payload  # noqa: E402
from bot.ops.backup import (  # noqa: E402
    checksum_path,
    create_backup,
    restore_backup,
    verify_backup,
)
from bot.ops.doctor import run_doctor  # noqa: E402
from bot.ops.drill import run_drill  # noqa: E402
from bot.ops.status import format_status, forward_gate, missed_session_payload  # noqa: E402


def _bar_frame(day: str = "2026-11-25") -> pd.DataFrame:
    dates = pd.bdate_range(end=day, periods=5)
    return pd.DataFrame(
        {
            "date": dates,
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "adjclose": 100.0,
            "volume": 1_000_000,
        }
    )


def _mini_cache(root: Path) -> None:
    cache = root / "cache"
    bars = cache / "bars"
    bars.mkdir(parents=True)
    for name in ("SPY", "SMH", "SOXX", "QQQ", "EURUSD=X"):
        _bar_frame().to_csv(bars / f"{name}.csv", index=False)
    pd.DataFrame(
        {"date": [pd.Timestamp("2026-11-25")], "ticker": ["SPY"], "adjclose": [100.0]}
    ).to_parquet(cache / "panel.parquet", index=False)
    pd.DataFrame({"date": [pd.Timestamp("2014-01-01")], "ticker": ["SPY"]}).to_parquet(
        cache / "membership.parquet", index=False
    )
    (cache / "missing_symbols.txt").write_text("DEAD\n")


def _cfg(root: Path, **kw) -> LiveConfig:
    return LiveConfig(
        book="monthly",
        market="US",
        start="2015-01-01",
        state_dir=str(root / "state" / "live"),
        **kw,
    )


def test_doctor_healthy_then_broken() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _mini_cache(root)
        cfg = _cfg(root)
        StateStore(cfg).save(LiveState.new(cfg))
        report = run_doctor(
            root,
            cfg,
            now="2026-11-25T22:00:00+00:00",
            min_free_mb=1.0,
        )
        assert report.ok, report.lines()
        assert report.exit_code == 0
        assert report.session == "2026-11-25"
        names = {c.name for c in report.checks}
        assert {"cache_dir", "required_bars", "state_file", "ledger_reconcile"} <= names

        # A corrupt state is a hard failure.
        cfg.state_path.write_text("{broken")
        bad = run_doctor(root, cfg, now="2026-11-25T22:00:00+00:00")
        assert not bad.ok and bad.exit_code == 2
        assert any("state" in c.name for c in bad.blocking_failures)

        # A missing panel/cache is also a hard failure.
        cfg.state_path.unlink()
        (root / "cache" / "panel.parquet").unlink()
        missing = run_doctor(root, cfg, now="2026-11-25T22:00:00+00:00")
        assert not missing.ok and missing.exit_code == 2
        assert any(c.name == "cache_panel.parquet" for c in missing.blocking_failures)


def test_backup_restore_exact_and_checksum() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        state = root / "state" / "live"
        results = root / "results"
        state.mkdir(parents=True)
        results.mkdir(parents=True)
        (state / "monthly_us.json").write_text('{"state": "exact"}\n')
        (state / "monthly_us.csv").write_text(
            "book,market,session\nmonthly,US,2026-11-25\n"
        )
        (results / "paper_ledger.csv").write_text(
            "date,book,market,equity\n2026-11-25,monthly,US,10000\n"
        )
        (results / "paper_daily.csv").write_text("date,equity\n2026-11-25,10000\n")
        config = root / "live.toml"
        config.write_text('book = "monthly"\nmarket = "US"\n')

        info = create_backup(
            root,
            out_dir=root / "backups",
            state_dir=state,
            results_dir=results,
            config_paths=[config],
        )
        verification = verify_backup(info.path)
        assert verification.ok, verification.error
        assert verification.digest == info.digest
        assert len(verification.entries) == 5
        assert checksum_path(info.path).exists()

        originals = {
            p: p.read_bytes()
            for p in [
                state / "monthly_us.json",
                state / "monthly_us.csv",
                results / "paper_ledger.csv",
                results / "paper_daily.csv",
                config,
            ]
        }
        (state / "monthly_us.json").write_text("{corrupt")
        (state / "monthly_us.csv").unlink()
        (results / "paper_ledger.csv").write_text("lost\n")
        config.write_text("lost\n")

        restored = restore_backup(
            info.path,
            root,
            state_dir=state,
            results_dir=results,
            force=True,
        )
        assert restored.restored == 5
        for path, blob in originals.items():
            assert path.read_bytes() == blob, path

        # A tampered archive must fail verification and restore.
        bad = root / "bad.tar.gz"
        bad.write_bytes(info.path.read_bytes() + b"tamper")
        checksum_path(bad).write_text(f"{info.digest}  {bad.name}\n")
        assert not verify_backup(bad).ok


def test_alert_payloads_are_off_and_dry_run() -> None:
    payload = sample_payload("missed_session")
    assert payload.kind == "missed_session"
    assert "missed" in payload.title.lower()
    assert payload.to_dict()["context"]["current_session"]

    off = build_manager({})
    assert not off.enabled
    deliveries = off.send(payload, dry_run=True)
    assert deliveries[0].channel == "none" and deliveries[0].ok

    configured = build_manager(
        {"STONKS_ALERT_WEBHOOK_URL": "https://example.invalid/hook"}
    )
    assert configured.enabled
    dry = configured.send(payload, dry_run=True)
    assert dry[0].dry_run and "no network" in dry[0].detail
    # The actual send path is never exercised in tests; dry-run must not touch it.


def test_status_missed_session_and_gate() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        cfg = _cfg(root)
        state = LiveState.new(cfg)
        state.last_session = "2026-11-20"
        alert = missed_session_payload(cfg, state, "2026-11-25")
        assert alert is not None
        assert alert.kind == "missed_session"
        assert alert.context["last_session"] == "2026-11-20"
        assert alert.context["current_session"] == "2026-11-25"
        assert alert.severity in {"warning", "critical"}

        (root / "results").mkdir()
        (root / "results" / "paper_audit.csv").write_text(
            "book,market,date,verified\nmonthly,US,2026-11-20,true\n"
        )
        assert forward_gate(root, cfg).startswith("WAIT 1/5")
        text = format_status(
            {
                "book": "monthly",
                "market": "US",
                "session": "2026-11-25",
                "alert": alert.to_dict(),
                "state_ledger": {"ok": True, "issues": []},
            }
        )
        assert "ALERT [warning]" in text or "ALERT [critical]" in text


def test_failure_drill_all_scenarios() -> None:
    results = run_drill()
    assert len(results) == 5
    assert all(r["ok"] for r in results), results
    assert {r["scenario"] for r in results} == {
        "stale_data",
        "duplicate_order",
        "broker_down",
        "state_corrupt",
        "missed_session",
    }
    for row in results:
        assert row["alert"]["kind"]
        assert row["expected_exit"] == row["actual_exit"]


def main() -> None:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"ok: {fn.__name__}")
    print(f"ok: {len(tests)} ops tests")


if __name__ == "__main__":
    main()
