"""Read-only operational status built on the existing live engine/state."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from bot.live import calendar, reconcile
from bot.live.config import LiveConfig
from bot.live.engine import EngineError, LiveEngine, resolve_session
from bot.live.health import health
from bot.live.signal import SnapshotError, build_snapshot
from bot.live.state import StateError

from .alerts import AlertPayload


def forward_gate(root: str | Path, cfg: LiveConfig) -> str:
    """Read the committed forward audit; never write/fabricate a verified date."""
    path = Path(root) / "results" / "paper_audit.csv"
    if not path.exists():
        return f"WAIT 0/{cfg.forward_gate} verified dates (no audit file)"
    try:
        df = pd.read_csv(path)
        df = df[(df.get("book") == cfg.book) & (df.get("market") == cfg.market)]
        if df.empty:
            return (
                f"WAIT 0/{cfg.forward_gate} verified dates "
                f"(no {cfg.book}/{cfg.market} rows)"
            )
        verified = df["verified"].astype(str).str.lower().isin(["true", "1"])
        n = int(df.loc[verified, "date"].astype(str).nunique())
        status = "PASS" if n >= cfg.forward_gate else "WAIT"
        return (
            f"{status} {n}/{cfg.forward_gate} verified dates "
            f"({cfg.book}/{cfg.market}, paper_audit.csv)"
        )
    except Exception as exc:  # noqa: BLE001 - status must never crash
        return f"WAIT 0/{cfg.forward_gate} verified dates (audit unreadable: {exc})"


def _last_run(state) -> dict[str, Any] | None:
    if state is None:
        return None
    if not state.runs:
        if not state.last_session:
            return None
        return {
            "session": state.last_session,
            "plan_id": state.last_plan_id,
            "settled_plan_id": None,
            "complete": False,
            "blocked": bool((state.blocked or {}).get("reason")),
            "updated_at": state.updated_at,
        }
    session = max(state.runs)
    rec = state.runs[session]
    return {
        "session": session,
        "plan_id": rec.get("plan_id"),
        "settled_plan_id": rec.get("settled_plan_id"),
        "complete": bool(rec.get("complete")),
        "blocked": bool(rec.get("blocked")),
        "updated_at": state.updated_at,
    }


def missed_session_payload(
    cfg: LiveConfig, state, session: str | pd.Timestamp
) -> AlertPayload | None:
    """Return an alert when the latest session has no run and one was missed."""
    if state is None:
        return None
    last = state.last_session or (max(state.runs) if state.runs else None)
    if not last:
        return None
    current = str(pd.Timestamp(session).date())
    pending = state.pending_plan
    overdue = pending is not None and pending.fill_session < current
    gap = calendar.count_sessions(last, current)
    if gap <= 1 and not overdue:
        return None
    if overdue:
        title = "Stonks live plan is overdue"
        message = (
            f"Pending plan {pending.plan_id} was due at {pending.fill_session} "
            f"but the latest completed session is {current}. The loop must not "
            "fabricate a historical open; reconcile and replan."
        )
        severity = "critical"
    else:
        title = "Stonks live session missed"
        message = (
            f"Last recorded session is {last}; latest completed session is "
            f"{current} ({gap - 1} missed session(s)). The paper loop is "
            "idempotent: restore/advance the cache and rerun the daily command."
        )
        severity = "warning"
    return AlertPayload(
        kind="missed_session",
        severity=severity,
        title=title,
        message=message,
        context={
            "book": cfg.book,
            "market": cfg.market,
            "last_session": last,
            "current_session": current,
            "missed_sessions": max(0, gap - 1),
            "pending_fill_session": pending.fill_session if pending else None,
        },
    )


def collect_status(
    cfg: LiveConfig,
    root: str | Path,
    *,
    now: str | datetime | None = None,
    as_of: str | None = None,
    forward_gate_enabled: bool = True,
) -> dict[str, Any]:
    """Collect status without writing state or calling the network."""
    root = Path(root)
    engine = LiveEngine(cfg)
    error = None
    result = None
    try:
        result = engine.status(
            now=now,
            as_of=as_of,
            allow_config_change=True,
        )
    except (StateError, EngineError, SnapshotError) as exc:
        error = str(exc)
        try:
            session = resolve_session(now, as_of)
        except Exception:  # noqa: BLE001
            session = None
    else:
        session = pd.Timestamp(result.session)

    try:
        state = engine.store.load(allow_config_change=True)
    except StateError as exc:
        state = None
        error = error or str(exc)

    snap = None
    if session is not None:
        try:
            snap = build_snapshot(cfg, session)
        except SnapshotError:
            snap = None

    rep = reconcile.reconcile(cfg, state, engine.store, repair_ledger=False)
    h = health(cfg, state, snap, engine.store, result) if snap is not None else None

    current = str(pd.Timestamp(session).date()) if session is not None else None
    last = _last_run(state)
    alert = missed_session_payload(cfg, state, session) if session is not None else None
    pending = state.pending_plan if state is not None else None
    open_orders = (
        [
            {
                "order_id": o.order_id,
                "ticker": o.ticker,
                "side": o.side,
                "qty": o.qty,
                "ref_price": o.ref_price,
                "fill_session": o.fill_session,
                "status": o.status,
            }
            for o in pending.orders
            if o.status == "PENDING"
        ]
        if pending is not None
        else []
    )
    risk = (
        result.risk.to_dict()
        if result is not None and result.risk is not None
        else ({"dd": 0.0, "risk_off": False} if state is None else None)
    )
    if result is not None:
        blocked = result.blocked_reason
    elif state is not None:
        blocked = (state.blocked or {}).get("reason") or error
    else:
        blocked = error
    data = dict(result.data) if result is not None else {}
    if snap is not None:
        data.update(snap.data_summary())
    payload = {
        "book": cfg.book,
        "market": cfg.market,
        "currency": cfg.native_currency,
        "reporting_currency": cfg.reporting_currency,
        "session": current,
        "next_session": (
            str(calendar.next_session(session)) if session is not None else None
        ),
        "last_run": last,
        "state_path": str(engine.store.path),
        "ledger_path": str(engine.store.ledger_path),
        "state_exists": state is not None,
        "state_updated_at": state.updated_at if state is not None else None,
        "data": data,
        "fresh": bool(data.get("bench_fresh")),
        "fx": (result.fx if result is not None else None) or {},
        "drawdown": risk,
        "blocked_reason": blocked,
        "pending_plan": (
            {
                "plan_id": pending.plan_id,
                "fill_session": pending.fill_session,
                "status": pending.status,
                "orders": len(pending.orders),
            }
            if pending is not None
            else None
        ),
        "open_orders": open_orders,
        "pending_cut": state.pending_cut if state is not None else None,
        "state_ledger": {
            "ok": rep.ok,
            "issues": rep.issues,
            "warnings": rep.warnings,
            "metrics": rep.metrics,
        },
        "health": h.to_dict() if h is not None else None,
        "forward_gate": forward_gate(root, cfg) if forward_gate_enabled else None,
        "alert": alert.to_dict() if alert is not None else None,
        "error": error,
    }
    healthy = (
        error is None
        and rep.ok
        and not bool(payload["blocked_reason"])
        and (h.ok if h is not None else True)
        and alert is None
    )
    payload["ok"] = healthy
    payload["exit_code"] = 0 if healthy else 2
    return payload


def format_status(payload: dict[str, Any]) -> str:
    """Short human report; no secrets and no side effects."""
    lines = [
        f"OPS STATUS {'OK' if payload.get('ok') else 'ATTENTION'}  "
        f"book={payload.get('book')}/{payload.get('market')}",
        f"  session={payload.get('session')}  next={payload.get('next_session')}",
        f"  last_run={payload.get('last_run')}",
        f"  state={payload.get('state_path')} exists={payload.get('state_exists')}",
        f"  data_fresh={payload.get('fresh')}  fx={payload.get('fx')}",
        f"  drawdown={payload.get('drawdown')}",
        f"  blocked={payload.get('blocked_reason')}",
        f"  forward_gate={payload.get('forward_gate')}",
        f"  state_ledger_ok={payload.get('state_ledger', {}).get('ok')}",
        f"  open_orders={len(payload.get('open_orders') or [])}",
    ]
    alert = payload.get("alert")
    if alert:
        lines += [
            f"  ALERT [{alert.get('severity')}] {alert.get('title')}: "
            f"{alert.get('message')}",
            f"  alert_context={alert.get('context')}",
        ]
    for issue in (payload.get("state_ledger") or {}).get("issues", [])[:5]:
        lines.append(f"  RECONCILE FAIL: {issue}")
    if payload.get("error"):
        lines.append(f"  ERROR: {payload['error']}")
    return "\n".join(lines)
