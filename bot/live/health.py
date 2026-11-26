"""Health checks for the live book. Read-only and explicit about blocking."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import calendar
from .config import LiveConfig
from .signal import Snapshot
from .state import LiveState, StateStore


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    blocking: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ok": self.ok,
            "detail": self.detail,
            "blocking": self.blocking,
        }


@dataclass
class Health:
    checks: list[Check] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks if c.blocking)

    @property
    def blocking_failures(self) -> list[Check]:
        return [c for c in self.checks if c.blocking and not c.ok]

    def add(self, name: str, ok: bool, detail: str, blocking: bool = True) -> None:
        self.checks.append(Check(name, bool(ok), detail, blocking))

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "checks": [c.to_dict() for c in self.checks],
        }

    def lines(self) -> list[str]:
        out = []
        for c in self.checks:
            tag = "OK  " if c.ok else ("FAIL" if c.blocking else "WARN")
            out.append(f"  [{tag}] {c.name}: {c.detail}")
        return out


def health(
    cfg: LiveConfig,
    state: LiveState | None,
    snap: Snapshot | None,
    store: StateStore,
    result=None,
) -> Health:
    h = Health()
    session = snap.session.date() if snap is not None else None
    if session is not None:
        h.add("market_session", calendar.is_session(session), str(session))
    else:
        h.add("market_session", False, "no session snapshot")

    if snap is not None:
        last = snap.last_date(snap.bench)
        h.add(
            "benchmark_fresh",
            snap.bench_fresh,
            f"SPY last={last.date() if last is not None else None} session={session}",
        )
        fx = getattr(result, "fx", None) if result is not None else None
        if fx:
            rate = fx.get("rate")
            h.add(
                "fx_eur",
                rate is not None,
                f"{cfg.native_currency}->EUR "
                + (
                    f"{float(rate):.6f} ({fx.get('date')})"
                    if rate is not None
                    else "missing; native book still marks, EUR columns blank"
                ),
                blocking=False,
            )
        tickers = []
        if state is not None:
            tickers += [t for t, p in state.portfolio.positions.items() if p.active]
        if result is not None and result.plan is not None:
            tickers += list(result.plan.target_weights)
        issues = snap.require_fresh(tickers, cfg.max_stale_days) if tickers else []
        h.add("symbols_fresh", not issues, "; ".join(issues) if issues else "all fresh")
    else:
        h.add("benchmark_fresh", False, "no snapshot")

    if state is None:
        h.add("state", True, "no state yet (fresh virtual book)", blocking=False)
        h.add("pending_plan", True, "none", blocking=False)
        h.add("duplicate_orders", True, "no orders", blocking=False)
        h.add("ledger", True, "no ledger yet", blocking=False)
        h.add("drawdown", True, "fresh book", blocking=False)
        return h

    h.add("state", True, f"updated {state.updated_at}")
    pending = state.pending_plan
    if pending is None:
        h.add("pending_plan", True, "none", blocking=False)
    else:
        overdue = session is not None and pending.fill_session < str(session)
        h.add(
            "pending_plan",
            not overdue,
            f"{pending.plan_id} for {pending.fill_session}"
            + (" (overdue)" if overdue else ""),
        )

    orders = state.all_order_ids()
    fills = state.all_fill_ids()
    n_orders = sum(len(p.orders) for p in state.plans.values())
    h.add(
        "duplicate_orders",
        len(orders) == n_orders,
        f"{len(orders)} unique ids / {n_orders} orders",
    )
    n_fills = sum(len(p.fills) for p in state.plans.values())
    h.add(
        "duplicate_fills",
        len(fills) == n_fills,
        f"{len(fills)} unique ids / {n_fills} fills",
    )

    rows = store.ledger_rows()
    missing = [
        s
        for s, rec in state.runs.items()
        if rec.get("ledger") and not any(r.get("session") == s for r in rows)
    ]
    h.add(
        "ledger",
        not missing,
        "in sync" if not missing else f"missing rows for {sorted(missing)}",
    )

    if result is not None and result.plan is not None:
        risk = result.plan.risk
        off = bool(risk.get("risk_off"))
        h.add(
            "drawdown_breaker",
            True,
            f"dd={float(risk.get('dd', 0.0)) * 100:+.2f}% "
            f"limit=-{cfg.dd_stop:.0%} off={off}",
            blocking=False,
        )
    return h
