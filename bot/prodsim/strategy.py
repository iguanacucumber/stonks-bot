"""Monthly book strategy adapter for the production simulation.

The researched book is a month-end, next-open rotation. This adapter keeps that
shape:

1. at the last tick of a session, ask the existing selector for target weights;
2. on the first tick of each symbol in the next session, turn the weight delta
   into a market order.

The selector is injected. Production uses :class:`CachedMonthlyTargets`, which
lazily calls ``bot.live.signal`` against the committed cache; tests use a pure
callable and never touch pandas or the network.
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Callable, Mapping, Protocol, Sequence

from .contract import (
    AccountSnapshot,
    OrderRequest,
    PositionSnapshot,
    RiskLimits,
    Tick,
    clamp_qty,
)

__all__ = [
    "CachedMonthlyTargets",
    "MonthlyBookStrategy",
    "StrategyProtocol",
    "TargetProvider",
]

TargetProvider = Callable[[date, Mapping[str, float]], Mapping[str, float]]


class StrategyProtocol(Protocol):
    """Narrow strategy surface consumed by :class:`Simulation`."""

    def on_session_close(
        self,
        session: date,
        closes: Mapping[str, float],
        account: AccountSnapshot,
    ) -> None: ...

    def orders_for_tick(
        self,
        tick: Tick,
        session: date,
        account: AccountSnapshot,
        positions: Sequence[PositionSnapshot],
    ) -> list[OrderRequest]: ...

    def status(self) -> dict[str, Any]: ...


def _session(value: date | datetime | str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _clean_symbol(symbol: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in symbol)[:24]


@dataclass
class _PendingTargets:
    session: date
    weights: dict[str, float]
    generated_at: datetime
    source: str = "monthly"


@dataclass
class StrategyStats:
    closes: int = 0
    target_sessions: int = 0
    orders: int = 0
    skipped: int = 0
    errors: int = 0


class MonthlyBookStrategy:
    """Weight-delta execution for the shipped monthly book.

    ``rebalance_check`` decides whether a session close is a rebalance. The
    default is the real month-end rule from ``bot.live.calendar`` and is imported
    lazily so this module remains importable without pandas.
    """

    def __init__(
        self,
        target_provider: TargetProvider,
        *,
        rebalance_check: Callable[[date], bool] | None = None,
        always_rebalance: bool = False,
        min_order_notional: float = 25.0,
        max_orders: int = 50,
        lot_size: int = 1,
        limits: RiskLimits | None = None,
        name: str = "monthly_book",
    ) -> None:
        if min_order_notional < 0:
            raise ValueError("min_order_notional must be >= 0")
        if max_orders < 0:
            raise ValueError("max_orders must be >= 0")
        self.target_provider = target_provider
        self._rebalance_check = rebalance_check
        self.always_rebalance = bool(always_rebalance)
        self.min_order_notional = float(min_order_notional)
        self.max_orders = int(max_orders)
        self.lot_size = max(1, int(lot_size))
        self.limits = limits or RiskLimits()
        self.name = name
        self._lock = threading.RLock()
        self._pending: _PendingTargets | None = None
        self._emitted: set[tuple[date, str, str]] = set()
        self._orders_this_target = 0
        self._last_close: date | None = None
        self._last_targets: dict[str, float] = {}
        self.stats = StrategyStats()

    # -- rebalance timing ---------------------------------------------
    def _is_rebalance(self, session: date) -> bool:
        if self.always_rebalance:
            return True
        if self._rebalance_check is not None:
            return bool(self._rebalance_check(session))
        try:
            from bot.live import calendar  # lazy; imports pandas

            return bool(calendar.is_rebalance_session(session))
        except Exception:  # noqa: BLE001 - a missing calendar must not trade
            self.stats.errors += 1
            return False

    def on_session_close(
        self,
        session: date,
        closes: Mapping[str, float],
        account: AccountSnapshot,
    ) -> None:
        """Compute and remember target weights at a completed session close."""
        d = _session(session)
        with self._lock:
            self._last_close = d
            self.stats.closes += 1
            if not self._is_rebalance(d):
                return
            try:
                raw = self.target_provider(d, dict(closes))
            except Exception:
                self.stats.errors += 1
                raise
            weights: dict[str, float] = {}
            for symbol, weight in (raw or {}).items():
                try:
                    w = float(weight)
                except (TypeError, ValueError):
                    continue
                if not math.isfinite(w) or w <= 1e-12:
                    continue
                if self.limits.max_position_pct and w > self.limits.max_position_pct:
                    w = float(self.limits.max_position_pct)
                weights[str(symbol)] = w
            if sum(weights.values()) > self.limits.max_gross + 1e-9:
                scale = self.limits.max_gross / sum(weights.values())
                weights = {s: w * scale for s, w in weights.items()}
            self._pending = _PendingTargets(
                session=d,
                weights=weights,
                generated_at=account.ts,
            )
            self._last_targets = dict(weights)
            self._orders_this_target = 0
            self.stats.target_sessions += 1

    # -- execution -----------------------------------------------------
    def orders_for_tick(
        self,
        tick: Tick,
        session: date,
        account: AccountSnapshot,
        positions: Sequence[PositionSnapshot],
    ) -> list[OrderRequest]:
        """Emit at most one order for ``tick.symbol`` on its first tick."""
        d = _session(session)
        with self._lock:
            pending = self._pending
            if pending is None or d <= pending.session:
                return []
            held = {p.symbol: float(p.qty) for p in positions if abs(p.qty) > 1e-12}
            symbol = tick.symbol
            # A held name absent from the target is a full liquidation, not a
            # short: the monthly book has no short leg.
            target_weight = float(pending.weights.get(symbol, 0.0))
            current = float(held.get(symbol, 0.0))
            if current <= 1e-12 and target_weight <= 1e-12:
                # Nothing to trade; do not let a large no-target universe
                # exhaust the per-rebalance order budget.
                return []
            key_side = (
                "BUY"
                if target_weight * account.equity > current * tick.last
                else "SELL"
            )
            key = (pending.session, symbol, key_side)
            if key in self._emitted:
                return []
            price = float(tick.ask if key_side == "BUY" else tick.bid)
            if price <= 0:
                return []
            target_qty = clamp_qty(
                target_weight * max(0.0, float(account.equity)) / price,
                self.lot_size,
            )
            target_qty = max(0.0, target_qty)
            delta = target_qty - current
            if abs(delta) * price < self.min_order_notional:
                self._emitted.add(key)
                self.stats.skipped += 1
                return []
            # Never submit a sell larger than the held position.
            if delta < 0:
                delta = -min(abs(delta), max(0.0, current))
            qty = clamp_qty(abs(delta), self.lot_size)
            if qty <= 0:
                self._emitted.add(key)
                self.stats.skipped += 1
                return []
            if self._orders_this_target >= self.max_orders:
                self.stats.skipped += 1
                return []
            side = "BUY" if delta > 0 else "SELL"
            client_id = f"mb-{pending.session:%Y%m%d}-{_clean_symbol(symbol)}-{side}"
            request = OrderRequest(
                client_order_id=client_id,
                symbol=symbol,
                side=side,
                qty=qty,
                order_type="MARKET",
                tif="DAY",
                strategy=self.name,
                created_at=tick.ts,
            )
            self._emitted.add(key)
            self._orders_this_target += 1
            self.stats.orders += 1
            return [request]

    # -- introspection -------------------------------------------------
    def pending_session(self) -> date | None:
        with self._lock:
            return None if self._pending is None else self._pending.session

    def pending_weights(self) -> dict[str, float]:
        with self._lock:
            return {} if self._pending is None else dict(self._pending.weights)

    def last_targets(self) -> dict[str, float]:
        with self._lock:
            return dict(self._last_targets)

    def reset(self) -> None:
        with self._lock:
            self._pending = None
            self._emitted.clear()
            self._orders_this_target = 0
            self._last_close = None
            self._last_targets = {}
            self.stats = StrategyStats()

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "name": self.name,
                "pending_session": (
                    self._pending.session.isoformat() if self._pending else None
                ),
                "last_close": self._last_close.isoformat()
                if self._last_close
                else None,
                "targets": len(self._pending.weights) if self._pending else 0,
                "emitted": len(self._emitted),
                "closes": self.stats.closes,
                "target_sessions": self.stats.target_sessions,
                "orders": self.stats.orders,
                "skipped": self.stats.skipped,
                "errors": self.stats.errors,
            }


class CachedMonthlyTargets:
    """Production target provider backed by the committed cache.

    The selector is imported lazily. It uses exactly the live planner's
    ``build_snapshot`` + ``levered_targets`` path, so the simulated book cannot
    drift from the researched monthly book. No data is fetched.
    """

    def __init__(
        self,
        *,
        market: str = "US",
        book: str = "monthly",
        start: str = "2015-01-01",
        capital: float = 100_000.0,
        panel: Any | None = None,
        membership: Any | None = None,
        config: Any | None = None,
    ) -> None:
        self.market = market
        self.book = book
        self.start = start
        self.capital = capital
        self._panel = panel
        self._membership = membership
        self._config = config
        self._lock = threading.Lock()
        self.last_error: str | None = None

    def _live_config(self) -> Any:
        if self._config is not None:
            return self._config
        from bot.live.config import LiveConfig  # lazy

        self._config = LiveConfig(
            book=self.book,
            market=self.market,
            start=self.start,
            capital=self.capital,
        )
        return self._config

    def _data(self) -> tuple[Any, Any]:
        if self._panel is not None and self._membership is not None:
            return self._panel, self._membership
        from bot import markets  # lazy; pandas/cache only

        self._panel, self._membership = markets.build(self.market)
        return self._panel, self._membership

    def __call__(
        self, session: date, closes: Mapping[str, float]
    ) -> Mapping[str, float]:
        from bot.live.signal import build_snapshot, levered_targets  # lazy

        cfg = self._live_config()
        panel, membership = self._data()
        try:
            snap = build_snapshot(
                cfg,
                session,
                panel=panel,
                membership=membership,
            )
            held = [s for s, px in closes.items() if px and px > 0]
            target = levered_targets(snap, held=held)
            return {s: float(w) for s, w in target.items() if float(w) > 1e-12}
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"{type(exc).__name__}: {exc}"
            raise
