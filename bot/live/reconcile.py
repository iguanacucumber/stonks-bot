"""Reconcile paper fills vs intended orders vs state vs ledger.

The paper broker is deterministic, so the check is exact: rebuild the portfolio from
the fill journal, compare it with stored positions/cash, verify every fill belongs to
an order and never exceeds it, and verify the ledger has one current row per session.
A mismatch is an incident, not something to smooth over.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .config import LiveConfig
from .corporate import apply_event
from .models import Portfolio
from .state import LiveState, StateStore

TOL = 1e-6


@dataclass
class ReconcileReport:
    ok: bool = True
    issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    repaired_rows: int = 0
    repaired_corporate_rows: int = 0

    def fail(self, msg: str) -> None:
        self.ok = False
        self.issues.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    def lines(self) -> list[str]:
        out = [
            f"RECONCILE {'OK' if self.ok else 'FAIL'}  "
            f"state={self.metrics.get('state')}  ledger_rows={self.metrics.get('ledger_rows')}"
        ]
        for key, value in self.metrics.items():
            if key in ("state", "ledger_rows"):
                continue
            out.append(f"  {key}: {value}")
        if self.repaired_rows:
            out.append(f"  repaired ledger rows: {self.repaired_rows}")
        for w in self.warnings:
            out.append(f"  WARN: {w}")
        for i in self.issues:
            out.append(f"  FAIL: {i}")
        if self.ok:
            out.append("all fills, orders, positions, cash and ledger rows agree")
        return out


def _key(fill) -> tuple[str, str]:
    return (fill.order_id, fill.fill_id)


def reconcile(
    cfg: LiveConfig,
    state: LiveState | None,
    store: StateStore,
    repair_ledger: bool = False,
) -> ReconcileReport:
    report = ReconcileReport()
    rows = store.ledger_rows()
    report.metrics["state"] = str(store.path)
    report.metrics["ledger_rows"] = len(rows)
    if state is None:
        report.metrics["positions"] = 0
        report.metrics["cash"] = cfg.capital
        report.metrics["equity_hint"] = cfg.capital
        report.warn("no state file; nothing has traded yet")
        return report

    if repair_ledger:
        report.repaired_rows = store.sync_ledger(state)
        report.repaired_corporate_rows = store.sync_corporate_actions(state)
        rows = store.ledger_rows()
        report.metrics["ledger_rows"] = len(rows)

    orders = {}
    duplicate_orders = 0
    for plan in state.plans.values():
        for o in plan.orders:
            if o.order_id in orders:
                duplicate_orders += 1
            orders[o.order_id] = (plan, o)
    if duplicate_orders:
        report.fail(f"{duplicate_orders} duplicate order ids in state")

    fills = {}
    rebuilt = Portfolio(cash=float(state.initial_capital))
    fees = 0.0
    realized = 0.0
    for plan in sorted(state.plans.values(), key=lambda p: p.created_at):
        for fill in plan.fills:
            if _key(fill) in fills:
                report.fail(f"duplicate fill id {fill.fill_id}")
                continue
            fills[_key(fill)] = fill
            ref = orders.get(fill.order_id)
            if ref is None:
                report.fail(f"orphan fill {fill.fill_id}: order not in state")
            else:
                _, order = ref
                if fill.qty > order.qty + 1e-8:
                    report.fail(
                        f"{fill.ticker}: fill {fill.qty} > order {order.qty} "
                        f"({fill.fill_id})"
                    )
                if fill.side != order.side or fill.ticker != order.ticker:
                    report.fail(f"fill {fill.fill_id} does not match order side/ticker")
                if fill.session != plan.fill_session:
                    report.fail(
                        f"fill {fill.fill_id} session {fill.session} != plan fill "
                        f"{plan.fill_session}"
                    )

    # Rebuild in session order. A corporate action is an open-of-session event,
    # so it applies before that session's fills (priority 0 vs 1).
    ca_ids = [str(e.get("event_id", "")) for e in state.corporate_actions]
    if len(ca_ids) != len(set(ca_ids)):
        report.fail("duplicate corporate-action event_id in state")
    timeline = [
        (str(e.get("session", "")), 0, "action", e) for e in state.corporate_actions
    ]
    timeline += [(str(f.session), 1, "fill", f) for f in fills.values()]
    for _, _, kind, item in sorted(timeline, key=lambda x: (x[0], x[1])):
        if kind == "action":
            try:
                apply_event(rebuilt, item)
            except (ValueError, KeyError, TypeError) as exc:
                report.fail(f"corporate action {item.get('event_id')}: {exc}")
        else:
            try:
                realized += rebuilt.apply_fill(item)
            except ValueError as exc:
                report.fail(str(exc))
            fees += item.commission

    rebuilt.cash -= float(state.financing_paid)
    for ticker, pos in sorted(state.portfolio.positions.items()):
        got = rebuilt.positions.get(ticker)
        got_qty = got.qty if got else 0.0
        if abs(got_qty - pos.qty) > TOL:
            report.fail(f"{ticker}: rebuilt qty {got_qty} != state qty {pos.qty}")
        if (
            abs(pos.qty) > TOL
            and abs((got.avg_cost if got else 0.0) - pos.avg_cost) > 1e-4
        ):
            report.fail(
                f"{ticker}: rebuilt avg cost "
                f"{(got.avg_cost if got else 0.0):.6f} != state {pos.avg_cost:.6f}"
            )
        if pos.qty < -TOL:
            report.fail(f"{ticker}: negative position {pos.qty}")
    for ticker, pos in rebuilt.positions.items():
        if pos.active and ticker not in state.portfolio.positions:
            report.fail(f"{ticker}: fill journal has a position not in state")

    if abs(rebuilt.cash - state.portfolio.cash) > TOL:
        report.fail(
            f"cash: rebuilt {rebuilt.cash:,.6f} != state {state.portfolio.cash:,.6f}"
        )
    if abs(fees - state.fees_paid) > TOL:
        report.fail(f"fees: fills {fees:,.6f} != state {state.fees_paid:,.6f}")
    if abs(realized - state.realized_pnl) > 1e-4:
        report.fail(
            f"realized pnl: fills {realized:,.6f} != state {state.realized_pnl:,.6f}"
        )

    # Corporate-action journals: gross/withholding/net must agree with state and
    # each persisted row. This catches a state event whose cash amount was edited
    # or whose fields were truncated.
    gross_div = 0.0
    withholding_div = 0.0
    net_div = 0.0
    gross_eur = 0.0
    withholding_eur = 0.0
    for e in state.corporate_actions:
        if e.get("kind") != "dividend":
            continue
        try:
            gross = float(e.get("gross_cash", 0.0) or 0.0)
            withheld = float(e.get("withholding", 0.0) or 0.0)
            net = float(e.get("net_cash", 0.0) or 0.0)
            fx = e.get("fx_rate")
            if abs((gross - withheld) - net) > TOL:
                report.fail(
                    f"corporate action {e.get('event_id')}: net_cash {net} != "
                    f"gross {gross} - withholding {withheld}"
                )
            gross_div += gross
            withholding_div += withheld
            net_div += net
            if fx is not None:
                gross_eur += gross * float(fx)
                withholding_eur += withheld * float(fx)
        except (TypeError, ValueError, KeyError) as exc:
            report.fail(f"corporate action {e.get('event_id')}: bad dividend fields: {exc}")
    if abs(gross_div - state.dividend_income) > TOL:
        report.fail(
            f"dividend income: events {gross_div:,.6f} != state "
            f"{state.dividend_income:,.6f}"
        )
    if abs(withholding_div - state.dividend_withholding) > TOL:
        report.fail(
            f"dividend withholding: events {withholding_div:,.6f} != state "
            f"{state.dividend_withholding:,.6f}"
        )
    if abs(gross_eur - state.dividend_income_eur) > 1e-4:
        report.fail(
            f"EUR dividend income: events {gross_eur:,.6f} != state "
            f"{state.dividend_income_eur:,.6f}"
        )
    if abs(withholding_eur - state.dividend_withholding_eur) > 1e-4:
        report.fail(
            f"EUR dividend withholding: events {withholding_eur:,.6f} != state "
            f"{state.dividend_withholding_eur:,.6f}"
        )

    # EUR overlay: every fill carries the trade-date FX snapshot. Rebuild the EUR
    # journals from those fills; never re-translate at today's rate.
    fees_eur = 0.0
    realized_eur = 0.0
    for key, fill in fills.items():
        if fill.fx_rate is None:
            continue
        if (
            fill.notional_eur is None
            or abs(fill.notional_eur - fill.notional * fill.fx_rate) > 1e-4
        ):
            report.fail(f"{fill.fill_id}: notional_eur does not match notional*fx")
        if (
            fill.commission_eur is None
            or abs(fill.commission_eur - fill.commission * fill.fx_rate) > 1e-6
        ):
            report.fail(f"{fill.fill_id}: commission_eur does not match commission*fx")
            continue
        fees_eur += fill.commission_eur
        realized_eur += float(fill.realized_pnl_eur or 0.0)
    if abs(fees_eur - state.fees_paid_eur) > 1e-4:
        report.fail(
            f"EUR fees: fills {fees_eur:,.6f} != state {state.fees_paid_eur:,.6f}"
        )
    if abs(realized_eur - state.realized_pnl_eur) > 1e-4:
        report.fail(
            f"EUR realized pnl: fills {realized_eur:,.6f} != "
            f"state {state.realized_pnl_eur:,.6f}"
        )
    if state.financing_paid_eur < -1e-9:
        report.fail(f"EUR financing is negative: {state.financing_paid_eur}")

    # Plan/order lifecycle consistency.
    for plan in state.plans.values():
        for order in plan.orders:
            filled = sum(f.qty for f in plan.fills if f.order_id == order.order_id)
            if abs(filled - order.filled_qty) > TOL:
                report.fail(
                    f"{order.order_id}: journal filled {filled} != order "
                    f"{order.filled_qty}"
                )
            if order.status in ("FILLED", "PARTIAL") and filled <= TOL:
                report.fail(f"{order.order_id}: status {order.status} but no fills")
    pending = state.pending_plan
    if pending is not None:
        if pending.status == "SETTLED":
            report.fail("pending plan is marked SETTLED")
        if pending.fill_session < str(state.last_session or ""):
            report.fail(
                f"pending plan {pending.plan_id} for {pending.fill_session} is "
                f"behind last session {state.last_session}"
            )

    # Ledger: exactly one row per state run, same plan id and status.
    seen = set()
    by_key = {}
    for row in rows:
        key = (row.get("book"), row.get("market"), row.get("session"))
        if key in seen:
            report.fail(f"duplicate ledger row {key}")
        seen.add(key)
        by_key[key] = row
    for session, rec in state.runs.items():
        key = (cfg.book, cfg.market, session)
        row = by_key.get(key)
        if row is None:
            report.fail(f"ledger missing session {session}")
            continue
        if rec.get("ledger") and row.get("plan_id") != rec["ledger"].get("plan_id"):
            report.fail(
                f"ledger plan_id {row.get('plan_id')} != state "
                f"{rec['ledger'].get('plan_id')} for {session}"
            )
        if rec.get("ledger") and row.get("status") != rec["ledger"].get("status"):
            report.fail(
                f"ledger status {row.get('status')} != state "
                f"{rec['ledger'].get('status')} for {session}"
            )
    for key in by_key:
        if key[0] == cfg.book and key[1] == cfg.market and key[2] not in state.runs:
            report.warn(f"ledger has a session not present in state: {key[2]}")
    for key, row in by_key.items():
        if key[0] != cfg.book or key[1] != cfg.market:
            continue
        native = row.get("currency") or cfg.native_currency
        if native != "EUR" and not row.get("fx_rate"):
            report.warn(
                f"ledger row {key[2]} has no FX rate; EUR-ledger reconciliation "
                "cannot use it"
            )

    # Corporate-action ledger: every state event must have its persisted row.
    ca_rows = store.corporate_action_rows()
    ca_ids = {row.get("event_id") for row in ca_rows}
    for event in state.corporate_actions:
        eid = event.get("event_id")
        if eid not in ca_ids:
            report.fail(f"corporate action {eid} has no ledger row")
    for eid in sorted(ca_ids - {e.get("event_id") for e in state.corporate_actions}):
        report.warn(f"corporate-action ledger row not in state: {eid}")

    report.metrics["plans"] = len(state.plans)
    report.metrics["orders"] = len(orders)
    report.metrics["fills"] = len(fills)
    report.metrics["corporate_actions"] = len(state.corporate_actions)
    report.metrics["dividend_income"] = round(state.dividend_income, 6)
    report.metrics["dividend_withholding"] = round(state.dividend_withholding, 6)
    report.metrics["dividend_income_eur"] = round(state.dividend_income_eur, 6)
    report.metrics["dividend_withholding_eur"] = round(
        state.dividend_withholding_eur, 6
    )
    report.metrics["positions"] = sum(
        1 for p in state.portfolio.positions.values() if p.active
    )
    report.metrics["cash"] = round(state.portfolio.cash, 6)
    report.metrics["fees_paid"] = round(state.fees_paid, 6)
    report.metrics["realized_pnl"] = round(state.realized_pnl, 6)
    report.metrics["financing_paid"] = round(state.financing_paid, 6)
    report.metrics["fees_paid_eur"] = round(state.fees_paid_eur, 6)
    report.metrics["realized_pnl_eur"] = round(state.realized_pnl_eur, 6)
    report.metrics["financing_paid_eur"] = round(state.financing_paid_eur, 6)
    report.metrics["last_fx_rate"] = state.last_fx_rate
    report.metrics["pending"] = pending.plan_id if pending else None
    return report
