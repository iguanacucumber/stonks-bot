"""Scenario, load, CLI and deployment checks for v12 sim-ops."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from bot.prodsim import scenarios  # noqa: E402


def test_required_scenarios() -> None:
    expected = {
        "partial_fill",
        "duplicate",
        "cancel_race",
        "buying_power",
        "data_outage",
        "restart",
        "kill",
        "replay_idempotency",
        "load",
    }
    assert set(scenarios.SCENARIOS) == expected
    for name in sorted(expected - {"load"}):
        result = scenarios.run(name)
        assert result["scenario"] == name, result
        assert result["ok"] is True, result
        assert result.get("errors", 0) == 0, result
    partial = scenarios.partial_fill()
    assert partial["fills"] == 2 and partial["filled_qty"] == 100.0
    duplicate = scenarios.duplicate_idempotency()
    assert duplicate["broker_orders"] == 1 and duplicate["duplicate_same_order"]
    race = scenarios.cancel_race()
    assert race["filled_qty"] == 30.0 and race["position_qty"] == 30.0
    assert "buying power" in scenarios.buying_power()["buying_power_reason"]
    assert scenarios.data_outage()["ready"] is False
    assert scenarios.restart()["store_ahead_resubmitted"] == 0
    assert scenarios.kill_switch()["new_orders_blocked"] is True
    replay = scenarios.replay_idempotency()
    assert replay["identical"] is True and replay["first"] == replay["second"]


def test_load_envelope() -> None:
    result = scenarios.load(ticks=10_000, orders=1_000)
    assert result["ok"] is True
    assert result["ticks"] >= 10_000
    assert result["orders"] >= 1_000
    assert result["fills"] >= 1_000
    assert result["errors"] == 0
    assert result["open_orders"] == 0
    assert result["peak_mb"] < 120
    assert result["events"] < 5_000 + 1_000
    assert result["elapsed_s"] < 30


def test_run_all_skips_load() -> None:
    results = scenarios.run_all(include_load=False)
    assert len(results) == len(scenarios.SCENARIOS) - 1
    assert all(r["ok"] for r in results)


def _mini_cache(root: Path) -> None:
    bars = root / "bars"
    bars.mkdir(parents=True)
    rows = []
    for i in range(12):
        d = date(2026, 1, 2) + timedelta(days=i)
        if d.weekday() >= 5:
            continue
        px = 100.0 + i
        rows.append(
            {
                "date": d.isoformat(),
                "open": px,
                "high": px + 1,
                "low": px - 1,
                "close": px + 0.5,
                "adjclose": px + 0.5,
                "volume": 1_000_000,
            }
        )
    pd.DataFrame(rows).to_csv(bars / "AAA.csv", index=False)


def test_cli_run_status_scenario_load() -> None:
    script = ROOT / "scripts" / "prod_sim.py"
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        cache = root / "cache"
        _mini_cache(cache)
        state = root / "state"
        cmd = [
            sys.executable,
            str(script),
            "run",
            "--dev",
            "--no-strategy",
            "--symbols",
            "AAA",
            "--cache-dir",
            str(cache),
            "--start",
            "2026-01-02",
            "--end",
            "2026-01-09",
            "--max-ticks",
            "20",
            "--state-dir",
            str(state),
        ]
        run = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        assert run.returncode == 0, run.stderr
        payload = json.loads(run.stdout)
        assert payload["status"]["processed_ticks"] == 20
        assert payload["status"]["state"] == "STOPPED"
        assert (state / "status.json").exists()
        status = subprocess.run(
            [
                sys.executable,
                str(script),
                "status",
                "--state-dir",
                str(state),
                "--json",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert status.returncode == 0, status.stderr
        status_payload = json.loads(status.stdout)
        assert status_payload["status"]["state"] == "STOPPED"
        assert status_payload["account"]["equity"] > 0
        scenario = subprocess.run(
            [sys.executable, str(script), "scenario", "partial_fill"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert scenario.returncode == 0, scenario.stderr
        assert json.loads(scenario.stdout)[0]["filled_qty"] == 100.0
        load = subprocess.run(
            [
                sys.executable,
                str(script),
                "load",
                "--ticks",
                "10000",
                "--orders",
                "1000",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert load.returncode == 0, load.stderr
        out = json.loads(load.stdout)
        assert out["ticks"] == 10_100 and out["orders"] == 1000


def test_cli_real_stack_uses_sqlite_exchange_oms() -> None:
    script = ROOT / "scripts" / "prod_sim.py"
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        cache = root / "cache"
        _mini_cache(cache)
        state = root / "state"
        cmd = [
            sys.executable,
            str(script),
            "run",
            "--no-strategy",
            "--symbols",
            "AAA",
            "--cache-dir",
            str(cache),
            "--start",
            "2026-01-02",
            "--end",
            "2026-01-09",
            "--max-ticks",
            "20",
            "--state-dir",
            str(state),
        ]
        run = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        assert run.returncode == 0, run.stderr
        payload = json.loads(run.stdout)
        components = payload["health"]["components"]
        assert components["store"] == "SQLiteStore", components
        assert components["exchange"] == "SimulatedExchange", components
        assert components["oms"] == "SimulatedOMS", components
        assert (state / "prodsim.sqlite3").exists()
        # Restarting on the same durable store must still come up.
        again = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        assert again.returncode == 0, again.stderr
        assert json.loads(again.stdout)["health"]["components"]["oms"] == "SimulatedOMS"


def test_deploy_artifacts_and_boundary_docs() -> None:
    deploy = ROOT / "deploy" / "prodsim"
    unit = (deploy / "stonks-prodsim.service").read_text()
    env = (deploy / "prodsim.env.example").read_text()
    runbook = (deploy / "RUNBOOK.md").read_text()
    assert "scripts/prod_sim.py run" in unit
    assert "--mode live-paper" in unit
    assert "127.0.0.1" in unit
    assert "Restart=on-failure" in unit
    assert "STONKS_PRODSIM_API_TOKEN=replace-with-a-long-random-token" in env
    assert "simulated real money only" in runbook.lower()
    assert "/api/v1/admin/kill" in runbook
    assert "Data outage" in runbook
    assert "STONKS_PRODSIM_API_TOKEN" in runbook
    for path in (ROOT / "README.md", ROOT / "README.en.md", ROOT / "RELEASE.md"):
        text = path.read_text()
        assert "v12" in text
        assert "no real broker" in text or "ni broker réel" in text
    contract = (ROOT / "docs" / "prod_sim_contract.md").read_text()
    assert "simulated real money" in contract.lower()


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok: {name}")
    print("ok: prodsim scenarios/cli/deploy")


if __name__ == "__main__":
    main()
