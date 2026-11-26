"""Safety rails and the live risk overlay.

All checks are pure and return explicit violations. The engine turns any violation
into a BLOCKED plan with zero orders: there is no "trade the part that fits" path.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from .config import LiveConfig
from .models import BUY, SELL, OrderIntent, Portfolio
from .signal import Snapshot, trend_breach

TOL = 1e-9


@dataclass
class RiskState:
    dd: float = 0.0
    peak: float = 0.0
    risk_off: bool = False
    risk_reason: str | None = None
    entered: bool = False
    cleared: bool = False
    scale: float = 1.0
    dd_breach: bool = False
    trend_breach: bool = False
    trend_price: float | None = None
    trend_ma: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "dd": round(self.dd, 6),
            "peak": round(self.peak, 2),
            "risk_off": self.risk_off,
            "risk_reason": self.risk_reason,
            "entered": self.entered,
            "cleared": self.cleared,
            "scale": self.scale,
            "dd_breach": self.dd_breach,
            "trend_breach": self.trend_breach,
            "trend_price": None
            if self.trend_price is None
            else round(self.trend_price, 4),
            "trend_ma": None if self.trend_ma is None else round(self.trend_ma, 4),
        }


def evaluate_risk(cfg: LiveConfig, state, snap: Snapshot, equity: float) -> RiskState:
    """Update the circuit breaker/trend state at the signal close.

    Hysteresis matches the backtest: a breach cuts exposure; a drawdown exit needs
    recovery to ``-dd_recover``, a trend exit needs the core back above its MA.
    """
    peak = max(float(state.peak_equity), float(equity))
    dd = (float(equity) / peak - 1.0) if peak > 0 else 0.0
    breached_trend, px, ma = trend_breach(snap)
    dd_breach = bool(cfg.dd_stop) and dd <= -float(cfg.dd_stop)
    risk = RiskState(
        dd=dd,
        peak=peak,
        risk_off=bool(state.risk_off),
        risk_reason=state.risk_reason,
        dd_breach=dd_breach,
        trend_breach=breached_trend,
        trend_price=px,
        trend_ma=ma,
    )
    if not risk.risk_off and (dd_breach or breached_trend):
        risk.risk_off = True
        risk.risk_reason = "dd" if dd_breach else "trend"
        risk.entered = True
    elif risk.risk_off:
        if risk.risk_reason == "trend" and not breached_trend:
            risk.risk_off = False
            risk.risk_reason = None
            risk.cleared = True
        elif risk.risk_reason == "dd" and dd >= -float(cfg.dd_recover):
            risk.risk_off = False
            risk.risk_reason = None
            risk.cleared = True
    risk.scale = (
        float(cfg.dd_scale)
        if risk.risk_reason == "dd"
        else float(cfg.trend_scale)
        if risk.risk_reason == "trend"
        else 1.0
    )
    return risk


def desired_targets(
    cfg: LiveConfig,
    state,
    snap: Snapshot,
    risk: RiskState,
    strategy_target: dict[str, float],
    current_weights: dict[str, float],
    due: bool,
    initialized: bool,
) -> tuple[dict[str, float], str]:
    """Strategic target for this session, before order generation."""
    if due or initialized:
        target = dict(strategy_target)
        if risk.risk_off:
            target = {t: w * risk.scale for t, w in target.items()}
        reason = "rebalance" if due else "initial"
    elif risk.entered:
        target = {t: w * risk.scale for t, w in current_weights.items()}
        reason = "risk_off"
    else:
        target = dict(state.target_weights)
        reason = "hold" if not risk.cleared else "hold_until_rebalance"
    return {t: float(w) for t, w in target.items() if float(w) > TOL}, reason


def make_orders(
    plan_id: str,
    target: dict[str, float],
    portfolio: Portfolio,
    marks: dict[str, float],
    equity: float,
    cfg: LiveConfig,
    fill_session: str,
    reason: str,
) -> list[OrderIntent]:
    """Turn target weights into whole/fractional share deltas at the signal close."""
    if equity <= 0:
        return []
    orders: list[OrderIntent] = []
    tickers = sorted(
        set(target) | {t for t, p in portfolio.positions.items() if p.active}
    )
    for i, ticker in enumerate(tickers):
        px = marks.get(ticker)
        if px is None or not math.isfinite(float(px)) or float(px) <= 0:
            continue
        px = float(px)
        pos = portfolio.positions.get(ticker)
        qty_now = pos.qty if pos is not None and pos.active else 0.0
        target_notional = float(target.get(ticker, 0.0)) * equity
        delta = target_notional - qty_now * px
        threshold = max(float(cfg.min_order_notional), float(cfg.trade_band) * equity)
        exiting = target.get(ticker, 0.0) <= TOL and qty_now > 0
        if abs(delta) < threshold and not exiting:
            continue
        if exiting:
            qty = qty_now
        else:
            qty = abs(delta) / px
        if not cfg.fractional_shares:
            qty = float(math.floor(qty))
        else:
            qty = float(math.floor(qty * 1e8) / 1e8)
        if qty <= TOL:
            continue
        if exiting:
            qty = min(qty, qty_now)
        elif qty_now > 0 and delta < 0:
            qty = min(qty, qty_now)
        if qty <= TOL:
            continue
        side = SELL if delta < 0 or exiting else BUY
        orders.append(
            OrderIntent(
                order_id=f"{plan_id[:12]}-{i:02d}-{ticker}",
                ticker=ticker,
                side=side,
                qty=qty,
                ref_price=px,
                reason=("exit" if exiting else reason),
                fill_session=fill_session,
            )
        )
    return orders


def check_rails(
    cfg: LiveConfig,
    target: dict[str, float],
    orders: list[OrderIntent],
    portfolio: Portfolio,
    marks: dict[str, float],
    equity: float,
    existing_order_ids: set[str],
    existing_keys: set[tuple[str, str, str]],
    fill_session: str,
) -> list[str]:
    """Every reason this plan must fail closed, in human-readable form."""
    issues: list[str] = []
    if equity <= 0 or not math.isfinite(equity):
        issues.append(f"equity is not positive ({equity:,.2f})")
    for ticker, w in target.items():
        if not math.isfinite(float(w)) or float(w) < 0:
            issues.append(f"invalid target weight {ticker}={w!r}")
    gross = sum(abs(float(w)) for w in target.values())
    if gross > float(cfg.max_gross) + TOL:
        issues.append(f"target gross {gross:.3f} > max_gross {cfg.max_gross:.3f}")
    if not target:
        issues.append("selector returned an empty target book")
    if not target and any(p.active for p in portfolio.positions.values()):
        issues.append("empty target book with open positions")
    buys = sum(o.notional for o in orders if o.side == BUY)
    sells = sum(o.notional for o in orders if o.side == SELL)
    turnover = max(buys, sells) / equity if equity > 0 else 0.0
    if turnover > float(cfg.max_turnover) + TOL:
        issues.append(f"turnover {turnover:.3f} > max_turnover {cfg.max_turnover:.3f}")
    if len(orders) > int(cfg.max_orders):
        issues.append(f"{len(orders)} orders > max_orders {cfg.max_orders}")
    seen: set[str] = set()
    for o in orders:
        if o.order_id in seen:
            issues.append(f"duplicate order id in plan: {o.order_id}")
        seen.add(o.order_id)
        if o.order_id in existing_order_ids:
            issues.append(f"duplicate order id already in state: {o.order_id}")
        key = (o.ticker, o.side, o.fill_session or fill_session)
        if key in existing_keys:
            issues.append(f"duplicate {o.side} {o.ticker} for {key[2]}")
        if o.qty <= TOL or not math.isfinite(o.qty):
            issues.append(f"{o.ticker}: invalid quantity {o.qty!r}")
        if o.ref_price <= 0 or not math.isfinite(o.ref_price):
            issues.append(f"{o.ticker}: invalid reference price {o.ref_price!r}")
        if o.side == SELL:
            pos = portfolio.positions.get(o.ticker)
            held = pos.qty if pos is not None else 0.0
            if o.qty > held + 1e-9:
                issues.append(f"{o.ticker}: sell {o.qty} exceeds position {held}")
    # A target name without a mark cannot be traded; the planner skipped it. That is
    # a data issue, not a silent partial rebalance.
    for ticker in target:
        if ticker not in marks:
            issues.append(f"{ticker}: target has no usable close")
    return issues


def turnover_of(orders: list[OrderIntent], equity: float) -> float:
    if equity <= 0:
        return 0.0
    buys = sum(o.notional for o in orders if o.side == BUY)
    sells = sum(o.notional for o in orders if o.side == SELL)
    return max(buys, sells) / equity
