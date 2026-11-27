#!/usr/bin/env python
"""End-to-end demo of the v12 simulated production service.

Starts the real (non-dev) SQLite store + simulated exchange + OMS + HTTP API,
submits an order over localhost HTTP, feeds ticks, then reads account, positions,
orders and metrics and fires the kill switch.

Simulated real money only. No real broker, no credentials, no live order path.

Run:
  uv run python scripts/prodsim_demo.py --port 8099
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot.prodsim.clock import SimulationClock  # noqa: E402
from bot.prodsim.contract import OrderRequest, RunConfig, Tick  # noqa: E402
from bot.prodsim.exchange import SimulatedExchange  # noqa: E402
from bot.prodsim.oms import SimulatedOMS  # noqa: E402
from bot.prodsim.simulation import Simulation  # noqa: E402
from bot.prodsim.store import SQLiteStore  # noqa: E402


def _request(
    base: str,
    method: str,
    path: str,
    *,
    token: str | None = None,
    body: dict | None = None,
    idempotency_key: str | None = None,
    as_json: bool = True,
) -> tuple[int, dict | str]:
    data = None if body is None else json.dumps(body).encode()
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            raw = response.read()
            if not as_json:
                return response.status, raw.decode(errors="replace")
            return response.status, json.loads(raw or b"{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        if not as_json:
            return exc.code, raw.decode(errors="replace")
        return exc.code, json.loads(raw or b"{}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8099)
    ap.add_argument("--state-dir", default="/tmp/opencode/prodsim-demo")
    ap.add_argument("--capital", type=float, default=100_000.0)
    args = ap.parse_args()

    state_dir = Path(args.state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    db = state_dir / "prodsim.sqlite3"
    if db.exists():
        db.unlink()

    token = "demo-api-token"
    admin = "demo-admin-token"
    os.environ["STONKS_PRODSIM_ADMIN_TOKEN"] = admin
    config = RunConfig(
        capital=args.capital,
        symbols=("SPY",),
        state_dir=str(state_dir),
        api_token=token,
    )
    exchange = SimulatedExchange(config, ["SPY"])
    store = SQLiteStore(db)
    oms = SimulatedOMS(exchange, store, config)
    sim = Simulation(
        config,
        exchange=exchange,
        store=store,
        oms=oms,
        clock=SimulationClock(mode="replay", speed=1, realtime=False),
    )
    sim.start()
    server = sim.start_api(port=args.port, token=token, dev=False)
    if server is None:
        raise SystemExit("API failed to start")
    base = server.base_url
    out: dict = {"base_url": base}

    try:
        out["health"] = _request(base, "GET", "/healthz")

        ts = datetime(2026, 11, 25, 14, 30, tzinfo=timezone.utc)
        first_tick = Tick(
            ts=ts,
            symbol="SPY",
            bid=100.00,
            ask=100.02,
            last=100.01,
            volume=500,
        )
        sim.on_tick(first_tick)
        out["ready"] = _request(base, "GET", "/readyz")

        order_body = {
            "client_order_id": "demo-order-1",
            "symbol": "SPY",
            "side": "BUY",
            "qty": 100,
            "order_type": "MARKET",
            "tif": "DAY",
            "strategy": "demo",
        }
        first_status, first = _request(
            base,
            "POST",
            "/api/v1/orders",
            token=token,
            body=order_body,
            idempotency_key="demo-key-1",
        )
        replay_status, replay = _request(
            base,
            "POST",
            "/api/v1/orders",
            token=token,
            body=order_body,
            idempotency_key="demo-key-1",
        )
        out["order_submit"] = (first_status, first)
        out["order_replay"] = (replay_status, replay)

        ticks = [first_tick]
        for i in range(1, 3):
            tick = Tick(
                ts=ts + timedelta(seconds=i),
                symbol="SPY",
                bid=100.00 + i * 0.5,
                ask=100.02 + i * 0.5,
                last=100.01 + i * 0.5,
                volume=500,
            )
            ticks.append(tick)
            sim.on_tick(tick)
            time.sleep(0.05)

        out["ticks"] = [
            {"ts": t.ts.isoformat(), "bid": t.bid, "ask": t.ask, "volume": t.volume}
            for t in ticks
        ]
        out["orders"] = _request(base, "GET", "/api/v1/orders", token=token)
        out["account"] = _request(base, "GET", "/api/v1/account", token=token)
        out["positions"] = _request(base, "GET", "/api/v1/positions", token=token)
        metrics_status, metrics_text = _request(base, "GET", "/metrics", as_json=False)
        out["metrics"] = {
            "status": metrics_status,
            "head": str(metrics_text).splitlines()[:12],
        }
        out["kill"] = _request(
            base,
            "POST",
            "/api/v1/admin/kill",
            token=admin,
            body={"reason": "demo complete"},
        )
        out["ready_after_kill"] = _request(base, "GET", "/readyz")
        out["final_status"] = sim.status_dict()

        assert out["health"][0] == 200
        assert out["ready"][0] == 200 and out["ready"][1]["ready"] is True
        assert first_status == 201 and replay_status == 200
        assert first["order_id"] == replay["order_id"]
        assert out["orders"][1]["orders"][0]["status"] == "FILLED"
        assert out["orders"][1]["orders"][0]["filled_qty"] == 100
        assert out["positions"][1]["positions"][0]["qty"] == 100
        assert out["kill"][0] == 200 and out["kill"][1]["state"] == "KILLED"
        assert out["ready_after_kill"][0] == 503
    finally:
        sim.stop()
        store.close()

    print(json.dumps(out, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
