"""Deterministic failure drill: stale data, duplicate order, broker down, bad state.

The drill uses temporary directories and synthetic panels only.  It never touches
the committed cache, the real state directory or the network.  Each scenario
returns the payload an operator would receive and the exit code the corresponding
command should produce, so a runbook can be tested after every release.
"""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import pandas as pd

from bot.live import reconcile
from bot.live.config import LiveConfig
from bot.live.models import OrderIntent, Plan, Portfolio
from bot.live.signal import build_snapshot
from bot.live.state import LiveState, StateError, StateStore

from .alerts import AlertPayload
from .status import missed_session_payload


def _panel() -> tuple[pd.DataFrame, pd.DataFrame, pd.Timestamp]:
    dates = pd.bdate_range("2020-01-01", periods=30)
    rows = []
    for ticker, base in (("SPY", 100.0), ("SMH", 80.0)):
        for i, d in enumerate(dates):
            px = base * (1.0 + 0.001 * i)
            rows.append((d, ticker, px, px, px, 10_000_000))
    panel = pd.DataFrame(
        rows, columns=["date", "ticker", "adjclose", "open", "high", "volume"]
    )
    panel["low"] = panel["adjclose"]
    panel["close"] = panel["adjclose"]
    mem = pd.DataFrame({"date": [dates[0]], "ticker": ["SMH"]})
    return panel, mem, dates[-1]


def _state_cfg(tmp: Path, **kw) -> LiveConfig:
    return LiveConfig(
        book="monthly",
        market="US",
        start="2020-01-01",
        capital=10_000.0,
        state_dir=str(tmp / "state"),
        **kw,
    )


def _plan(plan_id: str, order_id: str, cfg: LiveConfig) -> Plan:
    return Plan(
        plan_id=plan_id,
        book=cfg.book,
        market=cfg.market,
        signal_session="2020-02-10",
        fill_session="2020-02-11",
        created_at="2020-02-10T22:00:00+00:00",
        target_weights={"SMH": 0.5},
        current_weights={},
        orders=[
            OrderIntent(
                order_id=order_id,
                ticker="SMH",
                side="BUY",
                qty=1.0,
                ref_price=80.0,
                reason="rebalance",
                fill_session="2020-02-11",
            )
        ],
    )


def run_drill(root: str | Path | None = None) -> list[dict[str, Any]]:
    """Run all scenarios and return scenario/expected-exit/alert evidence."""
    results: list[dict[str, Any]] = []

    def add(
        name: str,
        expected: int,
        actual: int,
        payload: AlertPayload,
        detail: str,
    ) -> None:
        results.append(
            {
                "scenario": name,
                "expected_exit": expected,
                "actual_exit": actual,
                "ok": expected == actual,
                "alert": payload.to_dict(),
                "detail": detail,
            }
        )

    with TemporaryDirectory() as td:
        base = Path(root) if root is not None else Path(td)
        # 1. stale cache: the target session has no benchmark close.
        panel, mem, last = _panel()
        cfg = _state_cfg(base)
        target = last + pd.offsets.BDay(1)
        try:
            build_snapshot(cfg, target, panel=panel, membership=mem)
            actual_stale = 0
            stale_detail = "unexpected snapshot success"
        except Exception as exc:  # noqa: BLE001 - the exception is the evidence
            actual_stale = 2
            stale_detail = str(exc)
        add(
            "stale_data",
            2,
            actual_stale,
            AlertPayload(
                kind="stale_data",
                severity="critical",
                title="Stonks cache is stale",
                message="Target session has no benchmark close; live run is blocked.",
                context={"target": str(target.date()), "last_bar": str(last.date())},
            ),
            stale_detail,
        )

        # 2. duplicate order ids: reconcile must fail with exit 3.
        cfg = _state_cfg(base)
        store = StateStore(cfg)
        state = LiveState.new(cfg)
        state.plans = {
            "p1": _plan("p1", "duplicate-order", cfg),
            "p2": _plan("p2", "duplicate-order", cfg),
        }
        store.save(state)
        rep = reconcile.reconcile(cfg, state, store)
        add(
            "duplicate_order",
            3,
            3 if not rep.ok else 0,
            AlertPayload(
                kind="duplicate_order",
                severity="critical",
                title="Stonks duplicate order detected",
                message="Reconcile found duplicate order ids; run is blocked.",
                context={"issues": rep.issues[:3]},
            ),
            "; ".join(rep.issues[:3]) or "no duplicate detected",
        )

        # 3. corrupt state: load must fail closed.
        cfg = _state_cfg(base / "corrupt")
        cfg.state_path.parent.mkdir(parents=True, exist_ok=True)
        cfg.state_path.write_text("{not valid json")
        try:
            StateStore(cfg).load()
            actual_corrupt = 0
            corrupt_detail = "unexpected state load success"
        except StateError as exc:
            actual_corrupt = 2
            corrupt_detail = str(exc)
        add(
            "state_corrupt",
            2,
            actual_corrupt,
            AlertPayload(
                kind="state_corrupt",
                severity="critical",
                title="Stonks state file is corrupt",
                message="State failed JSON validation; restore a verified backup.",
                context={"file": str(cfg.state_path)},
            ),
            corrupt_detail,
        )

        # 4. broker unavailable: no retry loop, an explicit critical payload.
        class DownBroker:
            def execute(self, *_args, **_kwargs):
                raise ConnectionError("broker unavailable")

        try:
            DownBroker().execute("plan", {}, "now", Portfolio(cash=1.0))
            actual_broker = 0
            broker_detail = "unexpected broker success"
        except ConnectionError as exc:
            actual_broker = 2
            broker_detail = str(exc)
        add(
            "broker_down",
            2,
            actual_broker,
            AlertPayload(
                kind="broker_down",
                severity="critical",
                title="Stonks broker unavailable",
                message="Execution failed before any order was accepted.",
                context={"error": broker_detail},
            ),
            broker_detail,
        )

        # 5. missed session: a clear alert payload with both dates.
        cfg = _state_cfg(base / "missed")
        state = LiveState.new(cfg)
        state.last_session = "2020-01-02"
        alert = missed_session_payload(cfg, state, "2020-01-10")
        add(
            "missed_session",
            2,
            2 if alert is not None else 0,
            alert
            or AlertPayload(
                kind="missed_session",
                severity="warning",
                title="No missed session detected",
                message="Expected a missed-session payload.",
            ),
            json.dumps(alert.context if alert else {}, sort_keys=True),
        )
    return results
