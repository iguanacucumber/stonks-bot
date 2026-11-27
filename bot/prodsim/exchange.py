"""Deterministic simulated exchange -- never connects to a real venue.

The exchange implements :class:`bot.prodsim.contract.ExchangeProtocol` on top of
:class:`bot.prodsim.ledger.Ledger`.  It has no network, credential, persistence
or wall-clock dependency beyond timestamps copied from ticks.

Execution model
---------------
* An order is acknowledged on ``submit`` and can only fill on a **later**
  ``on_tick`` call (an internal tick sequence is recorded at admission).
* MARKET orders take the top of book: the ask for a buy, the bid for a sell.
* LIMIT orders fill only when the opposite side is marketable.  The fill price
  never violates the limit; slippage is capped by it.
* Slippage is applied against the taker in bps.
* Commission is ``max(min_commission, notional * commission_bps / 1e4)`` on
  every fill.
* A fill is capped by ``participation_cap * tick.volume`` and rounded down to
  the instrument lot size.  A binding cap leaves a PARTIAL order that remains
  open for DAY/GTC and expires for IOC.
* DAY orders expire on the first tick of a later session (the session is the
  date of the tick that was current when the order was submitted, so replaying
  historical ticks does not immediately expire a fresh order).
* GTC orders rest across sessions.  IOC orders expire after their first
  eligible tick even when only partially filled.
* Cancelling a partially filled order keeps the already filled quantity, its
  fills and its average price intact.

Risk checks happen at submit when a reference price exists: duplicate client
order id, unknown symbol, bad quantity, LIMIT without a price, halted symbol,
MAX order notional, shorting when disabled, the per-symbol position limit and
buying power.  A MARKET order submitted before the first tick has no price yet,
so it is accepted and its price-dependent checks are deferred to the first
fill; this is the only way to express "fill on a subsequent tick" from a cold
start.  A rejected order is stored with ``REJECTED`` status and
``reject_reason`` and never reaches the book.  Price gaps between submit and
fill are borne by the account, exactly as a market order would be.
"""

from __future__ import annotations

import copy
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import replace
from datetime import date, datetime, timezone

from .contract import (
    AccountSnapshot,
    Fill,
    Instrument,
    OrderRequest,
    OrderState,
    PositionSnapshot,
    RiskLimits,
    RunConfig,
    Tick,
    clamp_qty,
    utc_now,
    validate_request,
)
from .ledger import TOL, Ledger


class SimulatedExchange:
    """In-process simulated exchange with cash, margin and fills."""

    def __init__(
        self,
        config: RunConfig | None = None,
        instruments: Mapping[str, Instrument]
        | Sequence[str | Instrument]
        | None = None,
        *,
        halted: Iterable[str] = (),
    ) -> None:
        self.config = config if config is not None else RunConfig()
        self.limits = self.config.limits
        self.ledger = Ledger(
            self.config.capital,
            currency=self.config.currency,
            limits=self.limits,
        )

        self.commission_bps = float(self.config.commission_bps)
        self.min_commission = float(self.config.min_commission)
        self.slippage_bps = float(self.config.slippage_bps)
        self.participation_cap = float(self.config.participation_cap)
        if not math.isfinite(self.commission_bps) or self.commission_bps < 0:
            raise ValueError("commission_bps must be non-negative and finite")
        if not math.isfinite(self.min_commission) or self.min_commission < 0:
            raise ValueError("min_commission must be non-negative and finite")
        if not math.isfinite(self.slippage_bps) or not (
            0 <= self.slippage_bps < 10_000
        ):
            raise ValueError("slippage_bps must be in [0, 10000)")
        if not math.isfinite(self.participation_cap) or not (
            0 < self.participation_cap <= 1
        ):
            raise ValueError("participation_cap must be in (0, 1]")
        if not math.isfinite(float(self.limits.max_order_notional)) or (
            float(self.limits.max_order_notional) < 0
        ):
            raise ValueError("max_order_notional must be non-negative and finite")

        # An explicit mapping/sequence is a closed universe.  With no argument,
        # RunConfig.symbols closes the universe when non-empty; an empty
        # universe accepts any symbol with default instrument terms.
        self._instruments: dict[str, Instrument] = {}
        if instruments is not None:
            self._closed_universe = True
            if isinstance(instruments, Mapping):
                for symbol, value in instruments.items():
                    self._instruments[str(symbol)] = self._to_instrument(
                        str(symbol), value
                    )
            else:
                for value in instruments:
                    instrument = self._to_instrument(None, value)
                    if not instrument.symbol:
                        raise ValueError("instrument symbol is required")
                    self._instruments[instrument.symbol] = instrument
        else:
            symbols = tuple(str(s) for s in self.config.symbols)
            self._closed_universe = bool(symbols)
            for symbol in symbols:
                self._instruments[symbol] = Instrument(symbol=symbol)

        self._halted: set[str] = set()
        for symbol in halted:
            self.halt_symbol(str(symbol))

        self._orders: dict[str, OrderState] = {}
        self._open: dict[str, OrderState] = {}
        self._client_index: dict[str, str] = {}
        self._admitted: dict[str, int] = {}
        self._submitted_session: dict[str, date] = {}
        self._deferred_risk: set[str] = set()
        self._last_ticks: dict[str, Tick] = {}
        self._last_prices: dict[str, float] = {}
        self._tick_seq = 0
        self._last_tick_ts: datetime | None = None
        self._created_at = self._config_start()
        self._order_seq = 0
        self._fill_seq = 0

    def _config_start(self) -> datetime:
        """Deterministic fallback timestamp before the first tick arrives."""
        try:
            value = datetime.fromisoformat(str(self.config.start))
        except ValueError:
            return utc_now()
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    # -------------------------------------------------------------- helpers
    @staticmethod
    def _to_instrument(symbol: str | None, value: str | Instrument) -> Instrument:
        if isinstance(value, Instrument):
            if symbol is not None and value.symbol != symbol:
                return Instrument(
                    symbol=symbol,
                    currency=value.currency,
                    lot_size=value.lot_size,
                    tick_size=value.tick_size,
                )
            return value
        return Instrument(symbol=str(value if symbol is None else symbol))

    def _known(self, symbol: str) -> bool:
        return (not self._closed_universe) or symbol in self._instruments

    def _instrument(self, symbol: str) -> Instrument:
        return self._instruments.get(symbol) or Instrument(symbol=symbol)

    def _stamp(self) -> datetime:
        return self._last_tick_ts or self._created_at

    def _next_order_id(self) -> str:
        self._order_seq += 1
        return f"SIM-{self._order_seq:08d}"

    def _next_fill_id(self) -> str:
        self._fill_seq += 1
        return f"FILL-{self._fill_seq:08d}"

    def _commission(self, notional: float) -> float:
        if notional <= 0:
            return 0.0
        return max(self.min_commission, notional * self.commission_bps / 1e4)

    def _reference_price(self, request: OrderRequest) -> float | None:
        if request.order_type == "LIMIT":
            price = request.limit_price
            return None if price is None else float(price)
        tick = self._last_ticks.get(request.symbol)
        if tick is not None:
            return tick.ask if request.side == "BUY" else tick.bid
        return self._last_prices.get(request.symbol)

    def _record_reject(
        self,
        request: OrderRequest,
        qty: float,
        reason: str,
        order_id: str,
        now: datetime,
    ) -> OrderState:
        order = OrderState(
            order_id=order_id,
            client_order_id=request.client_order_id,
            symbol=request.symbol,
            side=request.side,
            qty=float(qty) if math.isfinite(float(qty)) else 0.0,
            order_type=request.order_type,
            limit_price=request.limit_price,
            tif=request.tif,
            strategy=request.strategy,
            status="REJECTED",
            reject_reason=reason,
            created_at=request.created_at,
            updated_at=now,
        )
        self._orders[order_id] = order
        self._client_index.setdefault(request.client_order_id, order_id)
        return copy.deepcopy(order)

    # ------------------------------------------------------------ Exchange API
    def submit(self, request: OrderRequest) -> OrderState:
        """Validate and acknowledge an order.  Never fills on submit."""
        order_id = self._next_order_id()
        now = self._stamp()

        if request.client_order_id in self._client_index:
            return self._record_reject(
                request, request.qty, "duplicate client_order_id", order_id, now
            )
        if not self._known(request.symbol):
            return self._record_reject(
                request,
                request.qty,
                f"unknown symbol {request.symbol!r}",
                order_id,
                now,
            )
        if request.symbol in self._halted:
            return self._record_reject(
                request, request.qty, "symbol is halted", order_id, now
            )
        if not math.isfinite(float(request.qty)) or request.qty <= 0:
            return self._record_reject(
                request, request.qty, "qty must be positive and finite", order_id, now
            )
        if request.order_type == "LIMIT":
            limit = request.limit_price
            if limit is None or not math.isfinite(float(limit)) or float(limit) <= 0:
                return self._record_reject(
                    request, request.qty, "limit price required", order_id, now
                )

        # Shared contract validation with the blunt all-sells rule relaxed; the
        # precise short check (sell only what is held) is applied by the ledger.
        shared = validate_request(request, replace(self.limits, allow_short=True))
        if shared is not None:
            return self._record_reject(request, request.qty, shared, order_id, now)

        instrument = self._instrument(request.symbol)
        qty = clamp_qty(request.qty, instrument.lot_size)
        if qty <= 0 or not math.isfinite(qty):
            return self._record_reject(
                request, qty, "qty below instrument lot size", order_id, now
            )

        # Price-independent short check works even before the first tick.
        if (
            request.side == "SELL"
            and not self.limits.allow_short
            and qty > max(0.0, self.ledger.position_qty(request.symbol)) + TOL
        ):
            return self._record_reject(
                request, qty, "short selling is disabled", order_id, now
            )

        reference = self._reference_price(request)
        if reference is not None:
            if not math.isfinite(reference) or reference <= 0:
                return self._record_reject(
                    request, qty, "invalid reference price", order_id, now
                )

            max_notional = float(self.limits.max_order_notional)
            if max_notional > 0 and qty * reference > max_notional + TOL:
                return self._record_reject(
                    request, qty, "max order notional exceeded", order_id, now
                )

            commission = self._commission(qty * reference)
            risk = self.ledger.check_order(
                symbol=request.symbol,
                side=request.side,
                qty=qty,
                price=reference,
                commission=commission,
                limits=self.limits,
            )
            if risk is not None:
                return self._record_reject(request, qty, risk, order_id, now)

        # A MARKET order with no top-of-book yet is accepted and first checked
        # against the first fill's price; rejecting it here would make a cold
        # start unable to place the order that the next tick is meant to fill.

        order = OrderState(
            order_id=order_id,
            client_order_id=request.client_order_id,
            symbol=request.symbol,
            side=request.side,
            qty=qty,
            order_type=request.order_type,
            limit_price=request.limit_price,
            tif=request.tif,
            strategy=request.strategy,
            status="ACKNOWLEDGED",
            created_at=request.created_at,
            updated_at=now,
        )
        self._orders[order_id] = order
        self._open[order_id] = order
        self._client_index[request.client_order_id] = order_id
        self._admitted[order_id] = self._tick_seq
        if reference is None:
            self._deferred_risk.add(order_id)
        self._submitted_session[order_id] = (
            self._last_tick_ts.date()
            if self._last_tick_ts is not None
            else request.created_at.date()
        )
        return copy.deepcopy(order)

    def cancel(self, order_id: str) -> OrderState:
        order = self._orders.get(order_id)
        if order is None:
            raise KeyError(f"unknown order {order_id!r}")
        if not order.is_open:
            return copy.deepcopy(order)
        order.status = "CANCELED"
        order.updated_at = self._stamp()
        self._open.pop(order_id, None)
        self._deferred_risk.discard(order_id)
        return copy.deepcopy(order)

    def on_tick(self, tick: Tick) -> list[Fill]:
        """Mark the market and fill eligible resting orders for this symbol."""
        if not self._known(tick.symbol):
            return []
        if self._last_tick_ts is not None and tick.ts < self._last_tick_ts:
            raise ValueError("out-of-order tick")
        previous = self._last_ticks.get(tick.symbol)
        if previous is not None and tick.ts == previous.ts:
            raise ValueError("duplicate tick")

        volume = float(tick.volume)
        if not math.isfinite(volume) or volume < 0:
            raise ValueError("tick volume must be non-negative and finite")

        self._tick_seq += 1
        self._last_tick_ts = tick.ts
        self._last_ticks[tick.symbol] = tick
        self._last_prices[tick.symbol] = tick.last
        self.ledger.mark({tick.symbol: tick.last})

        self._expire_day_orders(tick.ts.date())
        if tick.symbol in self._halted:
            return []

        fills: list[Fill] = []
        for order_id, order in list(self._open.items()):
            if order.symbol != tick.symbol:
                continue
            if self._admitted.get(order_id, self._tick_seq) >= self._tick_seq:
                continue
            fill = self._try_fill(order, tick, volume)
            if fill is not None:
                fills.append(fill)
        # New positions are marked at the top-of-book last, not at the fill
        # price, so the account snapshot after the tick is mark-to-market.
        self.ledger.mark({tick.symbol: tick.last})
        return fills

    def _expire_day_orders(self, current_date: date) -> None:
        for order_id, order in list(self._open.items()):
            if order.tif != "DAY":
                continue
            session = self._submitted_session.get(order_id)
            if session is not None and current_date > session:
                order.status = "EXPIRED"
                order.updated_at = self._stamp()
                self._open.pop(order_id, None)
                self._deferred_risk.discard(order_id)

    def _try_fill(self, order: OrderState, tick: Tick, volume: float) -> Fill | None:
        remaining = order.remaining_qty
        if remaining <= TOL:
            order.status = "FILLED"
            order.updated_at = tick.ts
            self._open.pop(order.order_id, None)
            self._deferred_risk.discard(order.order_id)
            return None

        if order.order_type == "LIMIT":
            limit = float(order.limit_price) if order.limit_price is not None else 0.0
            if not math.isfinite(limit) or limit <= 0:
                order.status = "REJECTED"
                order.reject_reason = "limit price required"
                order.updated_at = tick.ts
                self._open.pop(order.order_id, None)
                self._deferred_risk.discard(order.order_id)
                return None
            if order.side == "BUY" and tick.ask > limit + TOL:
                self._expire_ioc(order, tick)
                return None
            if order.side == "SELL" and tick.bid < limit - TOL:
                self._expire_ioc(order, tick)
                return None

        base = tick.ask if order.side == "BUY" else tick.bid
        slip = self.slippage_bps / 1e4
        if order.order_type == "MARKET":
            price = base * (1.0 + slip) if order.side == "BUY" else base * (1.0 - slip)
        else:
            limit = float(order.limit_price)  # checked above
            raw = base * (1.0 + slip) if order.side == "BUY" else base * (1.0 - slip)
            price = min(raw, limit) if order.side == "BUY" else max(raw, limit)
        if not math.isfinite(price) or price <= 0:
            self._expire_ioc(order, tick)
            return None

        cap = self.participation_cap * volume
        fill_qty = clamp_qty(
            min(remaining, cap), self._instrument(order.symbol).lot_size
        )
        if fill_qty <= TOL:
            self._expire_ioc(order, tick)
            return None

        # Defensive: don't let two resting sells create an accidental short.
        if order.side == "SELL" and not self.limits.allow_short:
            held = max(0.0, self.ledger.position_qty(order.symbol))
            if fill_qty > held + TOL:
                fill_qty = held
            if fill_qty <= TOL:
                order.status = "CANCELED"
                order.reject_reason = "short selling is disabled"
                order.updated_at = tick.ts
                self._open.pop(order.order_id, None)
                self._deferred_risk.discard(order.order_id)
                return None

        # Orders accepted before the first tick had no price for the max
        # notional / buying-power / position checks.  Apply them once, against
        # the first fill's price and the whole remaining quantity.
        if order.order_id in self._deferred_risk:
            max_notional = float(self.limits.max_order_notional)
            if max_notional > 0 and order.qty * price > max_notional + TOL:
                order.status = "REJECTED"
                order.reject_reason = "max order notional exceeded"
                order.updated_at = tick.ts
                self._open.pop(order.order_id, None)
                self._deferred_risk.discard(order.order_id)
                return None
            risk = self.ledger.check_order(
                symbol=order.symbol,
                side=order.side,
                qty=order.remaining_qty,
                price=price,
                commission=self._commission(order.remaining_qty * price),
                limits=self.limits,
            )
            if risk is not None:
                order.status = "REJECTED"
                order.reject_reason = risk
                order.updated_at = tick.ts
                self._open.pop(order.order_id, None)
                self._deferred_risk.discard(order.order_id)
                return None
            self._deferred_risk.discard(order.order_id)

        notional = fill_qty * price
        commission = self._commission(notional)
        fill = Fill(
            fill_id=self._next_fill_id(),
            order_id=order.order_id,
            ts=tick.ts,
            symbol=order.symbol,
            side=order.side,
            qty=fill_qty,
            price=price,
            commission=commission,
            slippage=abs(price - base) * fill_qty,
            liquidity="TAKER",
        )
        self.ledger.apply_fill(fill)

        previous_filled = order.filled_qty
        order.filled_qty = previous_filled + fill_qty
        order.avg_price = (
            order.avg_price * previous_filled + price * fill_qty
        ) / order.filled_qty
        order.fills.append(fill)
        order.updated_at = tick.ts

        if order.remaining_qty <= TOL:
            order.status = "FILLED"
            self._open.pop(order.order_id, None)
            self._deferred_risk.discard(order.order_id)
        elif order.tif == "IOC":
            order.status = "EXPIRED"
            self._open.pop(order.order_id, None)
            self._deferred_risk.discard(order.order_id)
        else:
            order.status = "PARTIAL"
            if order.side == "SELL" and not self.limits.allow_short:
                held = max(0.0, self.ledger.position_qty(order.symbol))
                if order.remaining_qty > held + TOL:
                    order.status = "CANCELED"
                    order.reject_reason = "short selling is disabled"
                    self._open.pop(order.order_id, None)
                    self._deferred_risk.discard(order.order_id)
        return fill

    def _expire_ioc(self, order: OrderState, tick: Tick) -> None:
        if order.tif != "IOC":
            return
        order.status = "EXPIRED"
        order.updated_at = tick.ts
        self._open.pop(order.order_id, None)
        self._deferred_risk.discard(order.order_id)

    def mark(self, prices: Mapping[str, float]) -> None:
        """Update marks without generating fills."""
        known = {
            str(symbol): float(price)
            for symbol, price in prices.items()
            if self._known(str(symbol))
        }
        if not known:
            return
        self.ledger.mark(known)
        for symbol, price in known.items():
            if math.isfinite(price) and price > 0:
                self._last_prices[symbol] = price

    # ------------------------------------------------------------- reporting
    def account(self) -> AccountSnapshot:
        return self.ledger.snapshot(self._stamp())

    def positions(self) -> list[PositionSnapshot]:
        return self.ledger.positions()

    def open_orders(self) -> list[OrderState]:
        return [copy.deepcopy(order) for order in self._open.values()]

    def order(self, order_id: str) -> OrderState | None:
        order = self._orders.get(order_id)
        return None if order is None else copy.deepcopy(order)

    def order_by_client(self, client_order_id: str) -> OrderState | None:
        """Additive reconciliation helper for OMS timeout/duplicate handling."""
        order_id = self._client_index.get(client_order_id)
        return None if order_id is None else self.order(order_id)

    def all_orders(self) -> list[OrderState]:
        return [copy.deepcopy(order) for order in self._orders.values()]

    # --------------------------------------------------------------- halts
    def halt_symbol(self, symbol: str) -> None:
        if not self._known(symbol):
            raise ValueError(f"unknown symbol {symbol!r}")
        self._halted.add(symbol)

    def resume_symbol(self, symbol: str) -> None:
        self._halted.discard(symbol)

    # Short aliases for callers that model an exchange halt event.
    def halt(self, symbol: str) -> None:
        self.halt_symbol(symbol)

    def resume(self, symbol: str) -> None:
        self.resume_symbol(symbol)

    def is_halted(self, symbol: str) -> bool:
        return symbol in self._halted

    @property
    def halted_symbols(self) -> tuple[str, ...]:
        return tuple(sorted(self._halted))

    # ------------------------------------------------------ corporate actions
    def split(self, symbol: str, ratio: float) -> None:
        """Apply a split to the ledger (open orders are not re-priced)."""
        self.ledger.apply_split(symbol, ratio)
        ratio = float(ratio)
        if symbol in self._last_prices:
            self._last_prices[symbol] /= ratio
        # The previous tick is pre-split; require a fresh tick for fills.
        self._last_ticks.pop(symbol, None)

    def dividend(self, symbol: str, per_share: float) -> float:
        """Credit a cash dividend to the ledger and return the amount."""
        return self.ledger.apply_dividend(symbol, per_share)


__all__ = ["SimulatedExchange"]
