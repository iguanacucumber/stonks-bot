"""Shared contract for the v12 production simulation.

This package is a *simulation* of a real-money trading service. It has no real
broker adapter, no credentials and no order path to a live account. The contract
is deliberately stdlib-only so the exchange, OMS, server and replay loop can be
built and tested independently.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal, Mapping, Protocol, Sequence

Side = Literal["BUY", "SELL"]
OrderType = Literal["MARKET", "LIMIT"]
TimeInForce = Literal["DAY", "IOC", "GTC"]
OrderStatus = Literal[
    "NEW",
    "ACKNOWLEDGED",
    "PARTIAL",
    "FILLED",
    "CANCELED",
    "REJECTED",
    "EXPIRED",
]
RunState = Literal["STARTING", "RUNNING", "PAUSED", "KILLED", "STOPPED", "ERROR"]
RunMode = Literal["replay", "live-paper"]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).isoformat(timespec="milliseconds")


def jsonable(value: Any) -> Any:
    """Recursively convert dataclasses/datetimes to JSON-safe primitives."""
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if hasattr(value, "__dataclass_fields__"):
        return {k: jsonable(v) for k, v in asdict(value).items()}
    if isinstance(value, datetime):
        return iso(value)
    if isinstance(value, Mapping):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    return value


@dataclass(frozen=True)
class Instrument:
    symbol: str
    currency: str = "USD"
    lot_size: int = 1
    tick_size: float = 0.01


@dataclass(frozen=True)
class Tick:
    """One top-of-book observation from the simulated market data feed."""

    ts: datetime
    symbol: str
    bid: float
    ask: float
    last: float
    volume: float = 0.0

    def __post_init__(self) -> None:
        if self.bid <= 0 or self.ask <= 0 or self.last <= 0:
            raise ValueError("prices must be positive")
        if self.bid > self.ask:
            raise ValueError("bid cannot exceed ask")


@dataclass(frozen=True)
class OrderRequest:
    client_order_id: str
    symbol: str
    side: Side
    qty: float
    order_type: OrderType = "MARKET"
    limit_price: float | None = None
    tif: TimeInForce = "DAY"
    strategy: str = ""
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not self.client_order_id:
            raise ValueError("client_order_id is required")
        if self.qty <= 0:
            raise ValueError("qty must be positive")
        if self.side not in ("BUY", "SELL"):
            raise ValueError(f"invalid side {self.side!r}")
        if self.order_type not in ("MARKET", "LIMIT"):
            raise ValueError(f"invalid order_type {self.order_type!r}")
        if self.order_type == "LIMIT" and (
            self.limit_price is None or self.limit_price <= 0
        ):
            raise ValueError("LIMIT orders need a positive limit_price")
        if self.tif not in ("DAY", "IOC", "GTC"):
            raise ValueError(f"invalid tif {self.tif!r}")


@dataclass(frozen=True)
class Fill:
    fill_id: str
    order_id: str
    ts: datetime
    symbol: str
    side: Side
    qty: float
    price: float
    commission: float = 0.0
    slippage: float = 0.0
    liquidity: str = "TAKER"

    @property
    def notional(self) -> float:
        return self.qty * self.price


@dataclass
class OrderState:
    order_id: str
    client_order_id: str
    symbol: str
    side: Side
    qty: float
    order_type: OrderType = "MARKET"
    limit_price: float | None = None
    tif: TimeInForce = "DAY"
    strategy: str = ""
    status: OrderStatus = "NEW"
    filled_qty: float = 0.0
    avg_price: float = 0.0
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    reject_reason: str | None = None
    fills: list[Fill] = field(default_factory=list)

    @property
    def remaining_qty(self) -> float:
        return max(0.0, self.qty - self.filled_qty)

    @property
    def is_open(self) -> bool:
        return self.status in ("NEW", "ACKNOWLEDGED", "PARTIAL")

    def to_dict(self) -> dict[str, Any]:
        return jsonable(self)


@dataclass
class PositionSnapshot:
    symbol: str
    qty: float
    avg_cost: float
    last: float
    market_value: float
    unrealized_pnl: float
    realized_pnl: float = 0.0
    currency: str = "USD"


@dataclass
class AccountSnapshot:
    ts: datetime
    currency: str
    cash: float
    buying_power: float
    equity: float
    gross_exposure: float
    net_exposure: float
    margin_used: float
    realized_pnl: float
    unrealized_pnl: float
    fees_paid: float
    positions: list[PositionSnapshot] = field(default_factory=list)


@dataclass(frozen=True)
class RiskLimits:
    max_gross: float = 3.0
    max_order_notional: float = 100_000.0
    max_daily_loss: float = 0.10
    max_position_pct: float = 0.50
    max_orders_per_min: int = 60
    allow_short: bool = False


@dataclass(frozen=True)
class RunConfig:
    capital: float = 100_000.0
    currency: str = "USD"
    mode: RunMode = "replay"
    speed: float = 1.0
    start: str = "2026-01-02"
    end: str = "2026-11-25"
    symbols: tuple[str, ...] = ()
    seed: int = 7
    latency_ms: float = 0.0
    commission_bps: float = 1.0
    min_commission: float = 0.0
    slippage_bps: float = 2.0
    participation_cap: float = 0.10
    limits: RiskLimits = field(default_factory=RiskLimits)
    state_dir: str = "state/prodsim"
    api_token: str = ""

    def __post_init__(self) -> None:
        if self.capital <= 0:
            raise ValueError("capital must be positive")
        if self.speed <= 0:
            raise ValueError("speed must be positive")
        if self.mode not in ("replay", "live-paper"):
            raise ValueError(f"invalid mode {self.mode!r}")


@dataclass
class SimulationStatus:
    state: RunState
    mode: RunMode
    clock: datetime
    session: str | None
    speed: float
    started_at: datetime
    last_tick: datetime | None = None
    processed_ticks: int = 0
    orders: int = 0
    fills: int = 0
    errors: int = 0
    kill_reason: str | None = None


class ExchangeProtocol(Protocol):
    """Simulated broker/exchange. Never connects to a real venue."""

    def submit(self, request: OrderRequest) -> OrderState: ...

    def cancel(self, order_id: str) -> OrderState: ...

    def on_tick(self, tick: Tick) -> list[Fill]: ...

    def mark(self, prices: Mapping[str, float]) -> None: ...

    def account(self) -> AccountSnapshot: ...

    def positions(self) -> list[PositionSnapshot]: ...

    def open_orders(self) -> list[OrderState]: ...

    def order(self, order_id: str) -> OrderState | None: ...


class StoreProtocol(Protocol):
    """Durable event + order store. SQLite/JSONL implementation is local only."""

    def append(self, kind: str, payload: Mapping[str, Any]) -> int: ...

    def events(self, since: int = 0, limit: int = 100) -> list[dict[str, Any]]: ...

    def save_order(self, order: OrderState) -> None: ...

    def load_order(self, order_id: str) -> OrderState | None: ...

    def list_orders(
        self, status: str | None = None, limit: int = 100
    ) -> list[OrderState]: ...

    def save_snapshot(self, account: AccountSnapshot) -> None: ...

    def last_snapshot(self) -> AccountSnapshot | None: ...

    def close(self) -> None: ...


class SimulationProtocol(Protocol):
    """Facade used by the HTTP server and the scenario runner."""

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def pause(self) -> None: ...

    def resume(self) -> None: ...

    def status(self) -> SimulationStatus: ...

    def health(self) -> dict[str, Any]: ...

    def account(self) -> AccountSnapshot: ...

    def positions(self) -> list[PositionSnapshot]: ...

    def orders(
        self, status: str | None = None, limit: int = 100
    ) -> list[OrderState]: ...

    def order(self, order_id: str) -> OrderState | None: ...

    def submit(self, request: OrderRequest) -> OrderState: ...

    def cancel(self, order_id: str) -> OrderState: ...

    def kill(self, reason: str, actor: str) -> dict[str, Any]: ...

    def events(self, since: int = 0, limit: int = 100) -> list[dict[str, Any]]: ...

    def metrics(self) -> str: ...


def order_event(order: OrderState, kind: str = "order") -> dict[str, Any]:
    """Stable event payload shared by OMS, store and API."""
    return {
        "kind": kind,
        "order_id": order.order_id,
        "client_order_id": order.client_order_id,
        "symbol": order.symbol,
        "side": order.side,
        "qty": order.qty,
        "status": order.status,
        "filled_qty": order.filled_qty,
        "avg_price": order.avg_price,
        "reject_reason": order.reject_reason,
        "updated_at": iso(order.updated_at),
    }


def validate_request(request: OrderRequest, limits: RiskLimits) -> str | None:
    """Return a rejection reason, or None. Shared by exchange and OMS."""
    if request.side == "SELL" and not limits.allow_short:
        return "short selling is disabled"
    if request.qty <= 0:
        return "qty must be positive"
    if request.order_type == "LIMIT":
        if request.limit_price is None or request.limit_price <= 0:
            return "limit price required"
    return None


def notional_for(qty: float, price: float) -> float:
    return float(qty) * float(price)


def clamp_qty(qty: float, lot_size: int = 1) -> float:
    """Round down to a whole lot; fractional shares are not assumed."""
    lot = max(1, int(lot_size))
    return float(int(qty // lot) * lot)


__all__ = [
    "AccountSnapshot",
    "ExchangeProtocol",
    "Fill",
    "Instrument",
    "OrderRequest",
    "OrderState",
    "OrderStatus",
    "PositionSnapshot",
    "RiskLimits",
    "RunConfig",
    "RunMode",
    "RunState",
    "SimulationProtocol",
    "SimulationStatus",
    "StoreProtocol",
    "Tick",
    "clamp_qty",
    "iso",
    "jsonable",
    "notional_for",
    "order_event",
    "utc_now",
    "validate_request",
]
