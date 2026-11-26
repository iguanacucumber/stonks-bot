"""The idempotent live loop.

One call = one completed session. It settles any pending prior plan at that session's
open, marks the book at the close, updates the risk breaker, and decides the next
session's orders. The state file is written once, atomically, after every gate has
passed; a crash before that write leaves the previous state and the same deterministic
plan can be recomputed.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from bot import data

from . import calendar
from .broker import Broker, BrokerError, PaperBroker, estimate_cost
from .brokers import make_broker
from .config import LiveConfig
from .models import (
    PLAN_BLOCKED,
    PLAN_OK,
    PLAN_SETTLED,
    Fill,
    Plan,
)
from .rails import (
    RiskState,
    check_rails,
    evaluate_risk,
    make_orders,
    turnover_of,
)
from .signal import (
    Snapshot,
    SnapshotError,
    build_snapshot,
    coverage,
    eur_fx,
    levered_targets,
)
from .state import LiveState, StateStore, utc_now

TOL = 1e-9


@dataclass
class LiveResult:
    session: str
    plan: Plan | None
    settled_plan: Plan | None = None
    fills: list[Fill] = field(default_factory=list)
    equity: float = 0.0
    cash: float = 0.0
    gross: float = 0.0
    risk: RiskState | None = None
    data: dict[str, Any] = field(default_factory=dict)
    fx: dict[str, Any] = field(default_factory=dict)
    blocked_reason: str | None = None
    persisted: bool = False
    created: bool = False
    ledger_written: bool = False

    @property
    def blocked(self) -> bool:
        return bool(self.blocked_reason) or (
            self.plan is not None and self.plan.status == PLAN_BLOCKED
        )

    @property
    def orders(self):
        return self.plan.orders if self.plan else []


class EngineError(RuntimeError):
    pass


def _now_iso(now: str | datetime | None) -> str:
    if now is None:
        return utc_now()
    if isinstance(now, datetime):
        return now.astimezone(timezone.utc).isoformat(timespec="seconds")
    return str(now)


def _clone(state: LiveState) -> LiveState:
    return LiveState.from_dict(state.to_dict())


def _plan_id(
    cfg: LiveConfig,
    session: str,
    fill_session: str,
    target: dict[str, float],
    equity: float,
    reason: str,
    trade: bool,
) -> str:
    payload = {
        "book": cfg.book,
        "market": cfg.market,
        "session": session,
        "fill": fill_session,
        "targets": {t: round(float(w), 8) for t, w in sorted(target.items())},
        "equity": round(float(equity), 6),
        "reason": reason,
        "trade": bool(trade),
        "config": cfg.signature(),
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()[:20]


def resolve_session(now: str | datetime | None, as_of: str | None) -> pd.Timestamp:
    if as_of:
        d = pd.Timestamp(as_of).normalize()
        latest = data.target_session(
            None if now is None else pd.Timestamp(now).to_pydatetime()
        )
        if d > latest:
            raise EngineError(
                f"--as-of {d.date()} is after the last completed session {latest.date()}"
            )
        return d
    return data.target_session(
        None if now is None else pd.Timestamp(now).to_pydatetime()
    )


def _fx_rate(fx: dict[str, Any] | None) -> float | None:
    if not fx:
        return None
    rate = fx.get("rate")
    return None if rate is None else float(rate)


def _accrue_financing(
    cfg: LiveConfig,
    state: LiveState,
    start: str | None,
    end: str,
    fx_rate: float | None = None,
) -> float:
    if not start or start >= end or cfg.financing_bps <= 0:
        return 0.0
    if state.portfolio.cash >= 0:
        return 0.0
    n = calendar.count_sessions(start, end)
    if n <= 0:
        return 0.0
    rate = float(cfg.financing_bps) / 1e4 / 252.0
    interest = abs(float(state.portfolio.cash)) * ((1.0 + rate) ** n - 1.0)
    state.portfolio.cash -= interest
    state.financing_paid += interest
    if fx_rate is not None:
        state.financing_paid_eur += interest * fx_rate
    return float(interest)


def _mark(
    cfg: LiveConfig, state: LiveState, snap: Snapshot
) -> tuple[dict[str, float], float, float]:
    held = [t for t, p in state.portfolio.positions.items() if p.active]
    issues = snap.require_fresh(held, cfg.max_stale_days)
    if issues:
        raise EngineError("cannot mark book: " + "; ".join(issues))
    marks: dict[str, float] = {}
    for ticker in held:
        px = snap.close(ticker)
        assert px is not None
        marks[ticker] = px
        pos = state.portfolio.positions[ticker]
        pos.last_price = px
        pos.last_session = str(snap.session.date())
    equity = state.portfolio.equity(marks)
    gross = state.portfolio.gross(marks)
    if equity <= 0:
        raise EngineError(f"marked equity is not positive ({equity:.2f})")
    return marks, float(equity), float(gross)


def _settle_pending(
    cfg: LiveConfig,
    state: LiveState,
    snap: Snapshot,
    now: str,
    broker: Broker,
) -> tuple[Plan | None, list[Fill]]:
    plan = state.pending_plan
    if plan is None:
        return None, []
    session = str(snap.session.date())
    if plan.fill_session > session:
        return None, []  # still waiting for the next open
    if plan.fill_session < session:
        raise EngineError(
            f"overdue pending plan {plan.plan_id}: fill session "
            f"{plan.fill_session} is older than today {session}; refusing to "
            "fabricate a historical open. Run reconcile, then cancel/replan."
        )
    if plan.status == PLAN_BLOCKED:
        state.pending_plan = None
        return plan, []
    age = calendar.count_sessions(plan.signal_session, snap.session.date())
    if age - 1 > int(cfg.max_pending_sessions):
        raise EngineError(
            f"pending plan {plan.plan_id} from {plan.signal_session} is "
            f"{age - 1} sessions old (limit {cfg.max_pending_sessions}); "
            "run reconcile and cancel/replan manually"
        )
    bars = snap.bars_on(plan.fill_session, [o.ticker for o in plan.orders])
    missing = [o.ticker for o in plan.orders if o.ticker not in bars]
    if missing:
        raise EngineError(
            f"cannot settle {plan.plan_id}: no usable open on "
            f"{plan.fill_session} for {sorted(missing)}"
        )
    try:
        fills = broker.execute(plan, bars, now, state.portfolio)
    except BrokerError as exc:
        raise EngineError(f"broker execution failed for {plan.plan_id}: {exc}") from exc
    # FX at the fill session, not at today's signal session: the EUR ledger needs
    # the trade-date rate to rebuild cost basis and annual EUR gains.
    fill_fx = eur_fx(cfg, plan.fill_session)
    fill_rate = _fx_rate(fill_fx)
    for fill in fills:
        fill.currency = cfg.native_currency
        fill.fx_rate = fill_rate
        fill.fx_date = (
            str(fill_fx.get("date")) if fill_fx.get("date") is not None else None
        )
        if fill_rate is not None:
            fill.notional_eur = fill.notional * fill_rate
            fill.commission_eur = fill.commission * fill_rate
            fill.realized_pnl_eur = fill.realized_pnl * fill_rate
            state.fees_paid_eur += fill.commission_eur
            state.realized_pnl_eur += fill.realized_pnl_eur
    state.fees_paid += sum(f.commission for f in fills)
    state.realized_pnl += sum(f.realized_pnl for f in fills)
    plan.fills.extend(fills)
    plan.status = PLAN_SETTLED
    state.pending_plan = None
    if fills:
        state.last_plan_id = plan.plan_id
    return plan, fills


def _ledger_row(
    cfg: LiveConfig,
    state: LiveState,
    plan: Plan | None,
    settled: Plan | None,
    fills: list[Fill],
    equity: float,
    cash: float,
    gross_value: float,
    risk: RiskState | None,
    data_summary: dict[str, Any],
    fx: dict[str, Any],
    now: str,
    blocked_reason: str | None,
    note: str,
) -> dict[str, Any]:
    orders = plan.orders if plan else []
    target = plan.target_weights if plan else state.target_weights
    rate = fx.get("rate")
    rate_f = float(rate) if rate is not None else None
    target_gross = float(sum(target.values()))
    gross_weight = float(gross_value) / float(equity) if equity > 0 else 0.0

    def eur_amount(amount: float, native: float) -> float | str:
        # No current FX and no earlier conversion: leave the cell blank rather
        # than printing a fabricated zero for a non-zero native amount.
        if rate_f is None and float(amount) == 0.0 and float(native) != 0.0:
            return ""
        return round(float(amount), 6)

    return {
        "time": now,
        "book": cfg.book,
        "market": cfg.market,
        "session": str(plan.signal_session if plan else state.last_session or ""),
        "fill_session": str(plan.fill_session if plan else ""),
        "plan_id": plan.plan_id if plan else "",
        "status": PLAN_BLOCKED
        if blocked_reason
        else (plan.status if plan else PLAN_OK),
        "equity": round(float(equity), 6),
        "cash": round(float(cash), 6),
        "gross": round(gross_weight, 6),
        "gross_value": round(float(gross_value), 6),
        "target_gross": round(target_gross, 6),
        "currency": cfg.native_currency,
        "fx_rate": "" if rate_f is None else round(rate_f, 8),
        "fx_date": fx.get("date") or "",
        "equity_eur": "" if rate_f is None else round(float(equity) * rate_f, 6),
        "cash_eur": "" if rate_f is None else round(float(cash) * rate_f, 6),
        "gross_eur": "" if rate_f is None else round(float(gross_value) * rate_f, 6),
        "target_gross_eur": (
            "" if rate_f is None else round(target_gross * float(equity) * rate_f, 6)
        ),
        "turnover": round(float(plan.turnover if plan else 0.0), 6),
        "fees": round(float(state.fees_paid), 6),
        "fees_eur": eur_amount(state.fees_paid_eur, state.fees_paid),
        "financing_eur": eur_amount(state.financing_paid_eur, state.financing_paid),
        "realized_pnl": round(float(state.realized_pnl), 6),
        "realized_pnl_eur": eur_amount(state.realized_pnl_eur, state.realized_pnl),
        "risk_off": bool(risk.risk_off if risk else state.risk_off),
        "risk_reason": (risk.risk_reason if risk else state.risk_reason) or "",
        "drawdown": round(float(risk.dd if risk else 0.0), 6),
        "orders": json.dumps([o.to_dict() for o in orders], sort_keys=True),
        "fills": json.dumps([f.to_dict() for f in fills], sort_keys=True),
        "targets": json.dumps(
            {t: round(float(w), 8) for t, w in sorted(target.items())}, sort_keys=True
        ),
        "blocked_reason": blocked_reason or "",
        "data_fresh": "true" if data_summary.get("bench_fresh") else "false",
        "note": note,
    }


def _simulate(
    cfg: LiveConfig,
    state: LiveState,
    snap: Snapshot,
    now: str,
    broker: Broker,
    persist: bool,
) -> LiveResult:
    session = str(snap.session.date())
    fx = eur_fx(cfg, snap.session)
    fx_rate = _fx_rate(fx)
    pending = state.pending_plan
    fill_session = pending.fill_session if pending is not None else None
    if (
        pending is not None
        and fill_session
        and fill_session <= session
        and pending.status != PLAN_BLOCKED
    ):
        fill_fx = eur_fx(cfg, fill_session)
        _accrue_financing(
            cfg, state, state.last_mark_session, fill_session, _fx_rate(fill_fx)
        )
        settle_plan, fills = _settle_pending(cfg, state, snap, now, broker)
        _accrue_financing(cfg, state, fill_session, session, fx_rate)
    else:
        settle_plan, fills = _settle_pending(cfg, state, snap, now, broker)
        _accrue_financing(cfg, state, state.last_mark_session, session, fx_rate)
    marks, equity, gross_native = _mark(cfg, state, snap)
    state.peak_equity = max(float(state.peak_equity), float(equity))
    risk = evaluate_risk(cfg, state, snap, equity)
    due = calendar.is_rebalance_session(
        snap.session, cfg.rebalance_months, cfg.rebalance_offset
    )
    initialized = not state.target_weights
    current_weights = state.portfolio.weights(marks, equity)
    strategy_target: dict[str, float] = {}
    if due or initialized:
        strategy_target = levered_targets(
            snap, [t for t, p in state.portfolio.positions.items() if p.active]
        )
    # Gap-down deleverage: the researched leverage is a target, not just a signal-
    # close check. If a gap leaves gross above leverage*(1+band), plan a sale for
    # the next open instead of silently running hotter than the book.
    cap = cfg.leverage * equity * (1.0 + cfg.leverage_band)
    deleverage = cfg.leverage > 0 and gross_native > cap + 1e-6
    cut = state.pending_cut
    retry_cut = bool(cut) and not (due or initialized)
    target: dict[str, float]
    reason: str
    if due or initialized:
        target = dict(strategy_target)
        if risk.risk_off:
            target = {t: w * risk.scale for t, w in target.items()}
        reason = "rebalance" if due else "initial"
        retry_cut = False
    elif retry_cut:
        scale = float(cut.get("scale", 1.0))
        if risk.risk_off:
            scale = min(scale, float(risk.scale))
        if deleverage:
            scale = min(scale, cap / gross_native)
        target = {t: w * scale for t, w in current_weights.items()}
        reason = str(cut.get("reason") or "risk_retry")
        if not target:
            retry_cut = False
            target = dict(state.target_weights)
            reason = "hold"
    elif risk.entered:
        target = {t: w * risk.scale for t, w in current_weights.items()}
        reason = "risk_off"
    elif deleverage:
        scale = cap / gross_native
        if risk.risk_off:
            scale = min(scale, float(risk.scale))
        target = {t: w * scale for t, w in current_weights.items()}
        reason = "deleverage"
    else:
        target = dict(state.target_weights)
        reason = "hold_until_rebalance" if risk.cleared else "hold"
    target = {t: float(w) for t, w in target.items() if float(w) > TOL}
    # Every target needs a mark for sizing and for the coverage check.
    for ticker in set(target) | set(strategy_target):
        px = snap.close(ticker)
        if px is not None:
            marks[ticker] = float(px)
    fill_session = str(calendar.next_session(snap.session))
    trade = bool(due or initialized or risk.entered or retry_cut or deleverage)
    plan_id = _plan_id(cfg, session, fill_session, target, equity, reason, trade)
    orders = []
    if trade:
        orders = make_orders(
            plan_id,
            target,
            state.portfolio,
            marks,
            equity,
            cfg,
            fill_session,
            reason,
        )
    existing_order_ids = state.all_order_ids()
    existing_keys = {
        (o.ticker, o.side, o.fill_session)
        for p in state.plans.values()
        for o in p.orders
        if o.fill_session
    }
    issues = check_rails(
        cfg,
        target,
        orders,
        state.portfolio,
        marks,
        equity,
        existing_order_ids,
        existing_keys,
        fill_session,
    )
    status = PLAN_BLOCKED if issues else PLAN_OK
    expected_cost = estimate_cost(orders, cfg) if not issues else 0.0
    target_gross = sum(target.values())
    plan = Plan(
        plan_id=plan_id,
        book=cfg.book,
        market=cfg.market,
        signal_session=session,
        fill_session=fill_session,
        created_at=now,
        target_weights=target,
        current_weights=current_weights,
        orders=orders if not issues else [],
        status=status,
        blocked_reason="; ".join(issues) if issues else None,
        expected_cost=expected_cost,
        turnover=turnover_of(orders, equity) if not issues else 0.0,
        gross=target_gross,
        equity=equity,
        cash=state.portfolio.cash,
        risk=risk.to_dict(),
        data={
            **snap.data_summary(),
            "fx": fx,
            "coverage": coverage(
                snap, list(target) + list(current_weights) + [snap.bench]
            ),
        },
        currency=cfg.native_currency,
        fx_rate=fx_rate,
        fx_date=str(fx.get("date")) if fx.get("date") is not None else None,
        equity_eur=None if fx_rate is None else equity * fx_rate,
        cash_eur=(None if fx_rate is None else float(state.portfolio.cash) * fx_rate),
        gross_eur=None if fx_rate is None else target_gross * equity * fx_rate,
        expected_cost_eur=(None if fx_rate is None else expected_cost * fx_rate),
    )
    if not issues:
        state.target_weights = target
        state.risk_off = risk.risk_off
        state.risk_reason = risk.risk_reason
        state.pending_plan = plan if orders else None
        state.pending_cut = None
        state.last_plan_id = plan.plan_id
        state.last_session = session
        state.last_mark_session = session
        state.blocked = None
    else:
        # Keep the old target/risk book; record the block for the next human.
        state.risk_off = risk.risk_off
        state.risk_reason = risk.risk_reason
        state.last_session = session
        state.last_mark_session = session
        state.blocked = {
            "session": session,
            "time": now,
            "reason": plan.blocked_reason,
        }
        if target and (risk.risk_off or deleverage or retry_cut):
            ratios = [
                target[t] / current_weights[t]
                for t in target
                if current_weights.get(t, 0.0) > TOL
            ]
            state.pending_cut = {
                "target": {t: round(float(w), 8) for t, w in target.items()},
                "reason": reason,
                "scale": round(min(ratios), 8) if ratios else 1.0,
                "session": session,
                "plan_id": plan.plan_id,
            }
        elif not (risk.risk_off or deleverage):
            state.pending_cut = None
    rate = fx.get("rate")
    if rate is not None:
        state.last_fx_rate = float(rate)
        state.last_fx_date = str(fx.get("date") or "")
        state.last_equity_eur = float(equity) * float(rate)
        state.last_cash_eur = float(state.portfolio.cash) * float(rate)
        state.last_gross_eur = float(gross_native) * float(rate)
        if state.initial_capital_eur is None:
            state.initial_capital_eur = float(state.initial_capital) * float(rate)
    note_bits = []
    if settle_plan is not None:
        note_bits.append(f"settled {settle_plan.plan_id}")
    if issues:
        note_bits.append("BLOCKED")
    elif orders:
        note_bits.append(f"plan {plan.plan_id}")
    else:
        note_bits.append("hold, no trades")
    data_summary = snap.data_summary()
    row = _ledger_row(
        cfg,
        state,
        plan,
        settle_plan,
        fills,
        equity,
        state.portfolio.cash,
        gross_native,
        risk,
        data_summary,
        fx,
        now,
        plan.blocked_reason,
        "; ".join(note_bits),
    )
    if persist:
        state.add_plan(plan)
        state.runs[session] = {
            "plan_id": plan.plan_id,
            "settled_plan_id": settle_plan.plan_id if settle_plan else None,
            "ledger": row,
            "complete": True,
            "blocked": bool(issues),
        }
    return LiveResult(
        session=session,
        plan=plan,
        settled_plan=settle_plan,
        fills=fills,
        equity=equity,
        cash=state.portfolio.cash,
        gross=(float(gross_native) / float(equity) if equity > 0 else 0.0),
        risk=risk,
        data=data_summary,
        fx=fx,
        blocked_reason=plan.blocked_reason,
        persisted=persist,
    )


class LiveEngine:
    def __init__(
        self,
        cfg: LiveConfig,
        store: StateStore | None = None,
        *,
        broker: Broker | None = None,
        env: dict[str, str] | None = None,
    ):
        self.cfg = cfg
        self.store = store or StateStore(cfg)
        self._broker = broker
        self._env = env
        # Dry-run/plan always uses the deterministic local preview, even when the
        # configured execution broker is IBKR. Planning must never touch a network.
        self.preview_broker = PaperBroker(
            slippage_bps=cfg.slippage_bps,
            commission_bps=cfg.commission_bps,
            min_commission=cfg.min_commission,
            max_participation=cfg.max_participation,
            fractional_shares=cfg.fractional_shares,
        )

    @property
    def broker(self) -> Broker:
        if self._broker is None:
            self._broker = make_broker(self.cfg, env=self._env)
        return self._broker

    def _load(self, allow_config_change: bool = False) -> LiveState:
        state = self.store.load(allow_config_change=allow_config_change)
        return state if state is not None else LiveState.new(self.cfg)

    def _snapshot(
        self,
        session: pd.Timestamp,
        panel: pd.DataFrame | None = None,
        membership: pd.DataFrame | None = None,
    ) -> Snapshot:
        return build_snapshot(self.cfg, session, panel=panel, membership=membership)

    def _blocked(
        self,
        session: pd.Timestamp,
        now: str,
        reason: str,
        state: LiveState | None,
        data_summary: dict[str, Any] | None = None,
    ) -> LiveResult:
        equity = state.portfolio.equity({}) if state else self.cfg.capital
        gross_value = state.portfolio.gross({}) if state else 0.0
        return LiveResult(
            session=str(pd.Timestamp(session).date()),
            plan=None,
            equity=equity,
            cash=(state.portfolio.cash if state else self.cfg.capital),
            gross=(gross_value / equity if equity > 0 else 0.0),
            fx=(
                {
                    "rate": state.last_fx_rate,
                    "date": state.last_fx_date,
                    "native": state.currency,
                }
                if state is not None and state.last_fx_rate is not None
                else {}
            ),
            data=data_summary or {"session": str(pd.Timestamp(session).date())},
            blocked_reason=reason,
        )

    def plan(
        self,
        *,
        now: str | datetime | None = None,
        as_of: str | None = None,
        panel: pd.DataFrame | None = None,
        membership: pd.DataFrame | None = None,
        allow_config_change: bool = False,
    ) -> LiveResult:
        """Read-only plan. Uses a cloned state so a dry run cannot move the book."""
        stamp = _now_iso(now)
        session = resolve_session(now, as_of)
        state = self._load(allow_config_change=allow_config_change)
        try:
            snap = self._snapshot(session, panel, membership)
        except SnapshotError as exc:
            return self._blocked(session, stamp, str(exc), state)
        session_str = str(snap.session.date())
        existing = state.runs.get(session_str)
        if existing and existing.get("plan_id"):
            plan = state.plans.get(existing["plan_id"])
            if plan is not None:
                return LiveResult(
                    session=session_str,
                    plan=plan,
                    settled_plan=(
                        state.plans.get(existing.get("settled_plan_id") or "")
                        if existing.get("settled_plan_id")
                        else None
                    ),
                    equity=plan.equity,
                    cash=plan.cash,
                    gross=plan.gross,
                    data=plan.data,
                    fx=(plan.data or {}).get("fx") or {},
                    blocked_reason=plan.blocked_reason,
                    persisted=False,
                )
        try:
            result = _simulate(
                self.cfg, _clone(state), snap, stamp, self.preview_broker, persist=False
            )
        except EngineError as exc:
            return self._blocked(
                snap.session, stamp, str(exc), state, snap.data_summary()
            )
        return result

    def run(
        self,
        *,
        now: str | datetime | None = None,
        as_of: str | None = None,
        panel: pd.DataFrame | None = None,
        membership: pd.DataFrame | None = None,
        allow_config_change: bool = False,
    ) -> LiveResult:
        """Paper run: settle, mark, plan, persist state and ledger exactly once."""
        stamp = _now_iso(now)
        session = resolve_session(now, as_of)
        with self.store.lock():
            try:
                snap = self._snapshot(session, panel, membership)
            except SnapshotError as exc:
                state = self.store.load(allow_config_change=allow_config_change)
                return self._blocked(session, stamp, str(exc), state)
            state = self._load(allow_config_change=allow_config_change)
            session_str = str(snap.session.date())
            existing = state.runs.get(session_str)
            if existing and existing.get("complete"):
                self.store.sync_ledger(state)
                plan = state.plans.get(existing.get("plan_id") or "")
                settled = (
                    state.plans.get(existing.get("settled_plan_id") or "")
                    if existing.get("settled_plan_id")
                    else None
                )
                return LiveResult(
                    session=session_str,
                    plan=plan,
                    settled_plan=settled,
                    fills=list(settled.fills) if settled else [],
                    equity=plan.equity if plan else state.peak_equity,
                    cash=plan.cash if plan else state.portfolio.cash,
                    gross=plan.gross if plan else 0.0,
                    data=(plan.data if plan else snap.data_summary()),
                    fx=(
                        (plan.data or {}).get("fx")
                        if plan
                        else eur_fx(self.cfg, snap.session)
                    )
                    or {},
                    blocked_reason=(plan.blocked_reason if plan else None),
                    persisted=True,
                    created=False,
                )
            working = _clone(state)
            if self.cfg.broker != "paper":
                try:
                    ensure = getattr(self.broker, "ensure_ready", None)
                    if ensure is not None:
                        ensure()
                except BrokerError as exc:
                    state.blocked = {
                        "session": session_str,
                        "time": stamp,
                        "reason": f"broker unavailable: {exc}",
                    }
                    self.store.save(state)
                    return self._blocked(
                        snap.session,
                        stamp,
                        f"broker unavailable: {exc}",
                        state,
                        snap.data_summary(),
                    )
            try:
                result = _simulate(
                    self.cfg, working, snap, stamp, self.broker, persist=True
                )
            except EngineError as exc:
                state.blocked = {
                    "session": session_str,
                    "time": stamp,
                    "reason": str(exc),
                }
                self.store.save(state)
                return self._blocked(
                    snap.session, stamp, str(exc), state, snap.data_summary()
                )
            if result.plan is not None and result.plan.status == PLAN_BLOCKED:
                # A blocked plan is a real, auditable session: persist the block but
                # never a pending order. A later valid session retries.
                working.blocked = {
                    "session": session_str,
                    "time": stamp,
                    "reason": result.plan.blocked_reason,
                }
                self.store.save(working)
                self.store.sync_ledger(working)
                result.persisted = True
                return result
            self.store.save(working)
            written = self.store.upsert_ledger(working.runs[session_str]["ledger"])
            result.persisted = True
            result.created = True
            result.ledger_written = bool(written)
            return result

    def status(
        self,
        *,
        now: str | datetime | None = None,
        as_of: str | None = None,
        allow_config_change: bool = False,
    ) -> LiveResult:
        """Read-only status: latest state, pending plan, health of the next session."""
        stamp = _now_iso(now)
        session = resolve_session(now, as_of)
        state = self._load(allow_config_change=allow_config_change)
        try:
            snap = self._snapshot(session)
        except SnapshotError as exc:
            return self._blocked(session, stamp, str(exc), state)
        marks = {}
        for ticker, pos in state.portfolio.positions.items():
            px = snap.close(ticker) or pos.last_price
            if px is not None:
                marks[ticker] = float(px)
        equity = (
            state.portfolio.equity(marks)
            if state.portfolio.positions
            else state.portfolio.cash
        )
        weights = state.portfolio.weights(marks, float(equity))
        risk = evaluate_risk(self.cfg, state, snap, float(equity))
        return LiveResult(
            session=str(snap.session.date()),
            plan=state.pending_plan,
            equity=float(equity),
            cash=float(state.portfolio.cash),
            gross=float(sum(weights.values())),
            risk=risk,
            data=snap.data_summary(),
            fx=eur_fx(self.cfg, snap.session),
            blocked_reason=(state.blocked or {}).get("reason"),
            persisted=False,
        )
