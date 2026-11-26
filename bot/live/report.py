"""Human-readable plan/status report. No side effects, no data access."""

from __future__ import annotations

import json
from typing import Any

from .config import LiveConfig
from .engine import LiveResult
from .models import Fill, OrderIntent, Plan
from .rails import turnover_of


def _money(x: float) -> str:
    return f"{x:,.2f}"


def _pct(x: float) -> str:
    return f"{x * 100:,.2f}%"


def _order_costs(order: OrderIntent, cfg: LiveConfig) -> tuple[float, float, float]:
    notional = order.notional
    commission = (
        max(cfg.min_commission, notional * cfg.commission_bps / 1e4)
        if notional > 0
        else 0.0
    )
    slippage = notional * cfg.slippage_bps / 1e4
    return commission, slippage, commission + slippage


def _order_lines(plan: Plan, cfg: LiveConfig) -> list[str]:
    lines = [
        f"{'ticker':10s} {'side':4s} {'qty':>12s} {'ref':>10s} "
        f"{'notional':>12s} {'comm':>8s} {'slip':>8s} {'status':>9s}"
    ]
    for o in plan.orders:
        commission, slippage, _ = _order_costs(o, cfg)
        lines.append(
            f"{o.ticker:10s} {o.side:4s} {o.qty:12.4f} {o.ref_price:10.2f} "
            f"{o.notional:12.2f} {commission:8.2f} {slippage:8.2f} {o.status:>9s}"
        )
    if not plan.orders:
        lines.append("  (no orders)")
    return lines


def _weight_lines(plan: Plan, cfg: LiveConfig) -> list[str]:
    tickers = sorted(set(plan.target_weights) | set(plan.current_weights))
    orders = {o.ticker: o for o in plan.orders}
    lines = [
        f"{'ticker':10s} {'target':>9s} {'current':>9s} {'drift%':>9s} "
        f"{'target$':>12s} {'order':>18s}"
    ]
    for t in tickers:
        tw = float(plan.target_weights.get(t, 0.0))
        cw = float(plan.current_weights.get(t, 0.0))
        o = orders.get(t)
        action = f"{o.side} {o.qty:.4f}" if o else "-"
        lines.append(
            f"{t:10s} {_pct(tw):>9s} {_pct(cw):>9s} "
            f"{(tw - cw) * 100:+9.2f} {tw * plan.equity:12.2f} {action:>18s}"
        )
    if not tickers:
        lines.append("  (empty book)")
    return lines


def _fill_lines(fills: list[Fill]) -> list[str]:
    if not fills:
        return ["  (none)"]
    lines = [
        f"{'ticker':10s} {'side':4s} {'qty':>12s} {'price':>10s} "
        f"{'notional':>12s} {'comm':>8s} {'pnl':>10s} {'session':>10s}"
    ]
    for f in fills:
        lines.append(
            f"{f.ticker:10s} {f.side:4s} {f.qty:12.4f} {f.price:10.2f} "
            f"{f.notional:12.2f} {f.commission:8.2f} {f.realized_pnl:10.2f} "
            f"{f.session:>10s}"
        )
    return lines


def format_report(
    result: LiveResult,
    cfg: LiveConfig,
    mode: str = "plan",
    forward_gate: str | None = None,
) -> str:
    plan = result.plan
    lines: list[str] = []
    title = (
        "LIVE PLAN (dry-run)"
        if mode == "plan"
        else "LIVE PAPER RUN"
        if mode == "run"
        else "LIVE STATUS"
    )
    lines.append(
        f"{title}  book={cfg.book} ({cfg.book_label})  "
        f"market={cfg.market} ({cfg.market_label})  "
        f"native={cfg.native_currency}  reporting={cfg.reporting_currency}  "
        "broker=paper"
    )
    lines.append(f"recommendation: {cfg.recommendation}")
    fx = (plan.data.get("fx") if plan else None) or result.fx or {}
    rate = fx.get("rate")
    eur_bit = ""
    if rate is not None:
        eur_bit = (
            f"  equity_eur=€{float(result.equity) * float(rate):,.2f}"
            f" @ {float(rate):.6f} {cfg.reporting_currency}/{cfg.native_currency}"
            f" ({fx.get('date')})"
        )
    lines.append(
        f"session={result.session}  equity={_money(result.equity)} "
        f"{cfg.native_currency}  cash={_money(result.cash)}  "
        f"gross={_pct(result.gross)}{eur_bit}"
    )
    if plan is not None:
        lines.append(
            f"signal={plan.signal_session}  fill={plan.fill_session}  "
            f"plan={plan.plan_id}  status={plan.status}"
        )
    data = result.data or {}
    lines.append(
        f"data: bench last={data.get('bench_last')}  fresh={data.get('bench_fresh')}  "
        f"panel={data.get('panel_last')}"
    )
    if result.blocked_reason:
        lines.append(f"BLOCKED: {result.blocked_reason}")
        lines.append("no orders will be sent")
        if forward_gate:
            lines.append(f"forward gate: {forward_gate}")
        return "\n".join(lines)

    risk = plan.risk if plan else (result.risk.to_dict() if result.risk else {})
    lines.append(
        f"risk: drawdown={float(risk.get('dd', 0.0)) * 100:+.2f}%  "
        f"breaker_off={bool(risk.get('risk_off'))}  "
        f"reason={risk.get('risk_reason') or '-'}  "
        f"trend_breach={bool(risk.get('trend_breach'))}  "
        f"scale={float(risk.get('scale', 1.0)):.2f}"
    )
    if result.settled_plan is not None:
        lines.append(
            f"settled prior plan {result.settled_plan.plan_id} "
            f"({len(result.fills)} fills, "
            f"{sum(f.commission for f in result.fills):.2f} commissions)"
        )
    if result.fills:
        lines.append("")
        lines.append("fills at the open")
        lines.extend(_fill_lines(result.fills))

    if plan is not None:
        lines.append("")
        lines.append("target vs current")
        lines.extend(_weight_lines(plan, cfg))
        lines.append("")
        lines.append(
            f"next orders for {plan.fill_session} "
            f"({len(plan.orders)} intents, estimated cost "
            f"{_money(plan.expected_cost)} "
            f"= {plan.expected_cost / plan.equity * 1e4 if plan.equity else 0:.1f} bps)"
        )
        lines.extend(_order_lines(plan, cfg))
        turnover = plan.turnover or turnover_of(plan.orders, plan.equity)
        lines.append("")
        lines.append(
            f"rails: gross {sum(plan.target_weights.values()):.3f} <= "
            f"{cfg.max_gross:.2f}  turnover {turnover:.3f} <= "
            f"{cfg.max_turnover:.2f}  orders {len(plan.orders)} <= {cfg.max_orders}"
        )
        lines.append(
            f"execution model: next open + {cfg.slippage_bps:.1f} bps slippage, "
            f"{cfg.commission_bps:.1f} bps commission "
            f"(min {cfg.min_commission:.2f}), "
            f"max {cfg.max_participation:.1%} of open volume"
        )
        stale = [
            t
            for t, row in (plan.data.get("coverage") or {}).items()
            if not row.get("fresh")
        ]
        lines.append(
            "coverage: "
            + ("all required symbols fresh" if not stale else f"stale {stale}")
        )
    lines.append("")
    if mode == "status":
        lines.append("read-only status; no state written")
    elif mode == "run":
        lines.append(
            "PAPER ONLY — fills and orders are recorded in the paper ledger; no real broker"
        )
    else:
        lines.append("NO ORDERS SENT — paper/dry-run only")
    if forward_gate:
        lines.append(f"forward gate: {forward_gate}")
    return "\n".join(lines)


def plan_json(result: LiveResult, cfg: LiveConfig) -> str:
    fx = (result.plan.data.get("fx") if result.plan else None) or result.fx or {}
    rate = fx.get("rate")
    rate_f = None if rate is None else float(rate)
    payload: dict[str, Any] = {
        "mode": "paper" if result.persisted else "dry-run",
        "session": result.session,
        "blocked": result.blocked,
        "blocked_reason": result.blocked_reason,
        "equity": result.equity,
        "cash": result.cash,
        "gross": result.gross,
        "currency": cfg.native_currency,
        "reporting_currency": cfg.reporting_currency,
        "equity_eur": None if rate_f is None else result.equity * rate_f,
        "cash_eur": None if rate_f is None else result.cash * rate_f,
        "gross_eur": (
            None if rate_f is None else result.gross * result.equity * rate_f
        ),
        "fx": fx,
        "recommendation": cfg.recommendation,
        "config": {
            "book": cfg.book,
            "book_label": cfg.book_label,
            "market": cfg.market,
            "market_label": cfg.market_label,
            "native_currency": cfg.native_currency,
            "reporting_currency": cfg.reporting_currency,
            "recommended_market": cfg.recommended_market,
            "capital": cfg.capital,
            "leverage": cfg.leverage,
            "max_gross": cfg.max_gross,
            "max_turnover": cfg.max_turnover,
        },
        "plan": result.plan.to_dict() if result.plan else None,
        "fills": [f.to_dict() for f in result.fills],
        "settled_plan": result.settled_plan.plan_id if result.settled_plan else None,
    }
    return json.dumps(payload, sort_keys=True, indent=2)
