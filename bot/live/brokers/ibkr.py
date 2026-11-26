"""Interactive Brokers paper adapter (Client Portal Web API).

Why the Web API instead of ``ib_insync``:
  * it needs no extra Python package (``requests`` is already a project dependency);
  * the gateway owns login/2FA and the paper-account session, so no credentials or
    tokens are ever stored in this repository;
  * every call is plain JSON over HTTP, which makes the fake-transport failure drills
    exactly the same shape as the production path.

The adapter is paper-only by construction: the account id must start with ``DU`` and
there is no live-account switch. It implements the live ``Broker.execute`` contract
plus explicit submit/cancel/replace/status/reconcile methods.

Order identity
--------------
The bot's ``OrderIntent.order_id`` is deterministic and becomes the IBKR ``cOID``.
A retried submit first searches open orders for the same ``cOID`` and only then
posts. IBKR also rejects duplicate client order ids; that error is treated as an
idempotent hit, never as a reason to post a second order.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

import requests

from ..broker import Bar, BrokerError
from ..models import (
    CANCELLED,
    FILLED,
    PARTIAL,
    PENDING,
    REJECTED,
    Fill,
    OrderIntent,
    Plan,
    Portfolio,
)

DEFAULT_BASE_URL = "https://localhost:5000/v1/api"
PAPER_ACCOUNT_PREFIX = "DU"
TERMINAL_STATUSES = {
    "FILLED",
    "CANCELLED",
    "CANCELED",
    "REJECTED",
    "INACTIVE",
}


class IBKRError(BrokerError):
    """Base class for adapter/transport failures."""


class IBKRConfigError(IBKRError):
    """Missing or unsafe configuration."""


class IBKRAuthError(IBKRError):
    """Client Portal Gateway is not authenticated."""


class IBKRTransientError(IBKRError):
    """A retryable transport failure (connection, timeout, 429, 5xx)."""


class IBKRUnavailable(IBKRTransientError):
    """The broker did not answer after the configured retries."""


class IBKRRequestError(IBKRError):
    """A terminal broker/API error."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        payload: Any | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


class IBKRDuplicateOrder(IBKRRequestError):
    """IBKR rejected a duplicate ``cOID``; the caller should adopt the old order."""


class IBKRReadOnlyError(IBKRError):
    """A write was attempted on a broker explicitly opened read-only."""


@dataclass(frozen=True)
class IBKRPaperConfig:
    """Environment-only configuration. No secret is stored here."""

    account_id: str
    base_url: str = DEFAULT_BASE_URL
    timeout: float = 15.0
    poll_interval: float = 1.0
    max_poll_seconds: float = 30.0
    max_retries: int = 3
    backoff_base: float = 0.5
    verify_ssl: bool = True
    read_only: bool = False

    def __post_init__(self) -> None:
        account = str(self.account_id).strip()
        if not account:
            raise IBKRConfigError("IBKR_PAPER_ACCOUNT is required")
        if not account.upper().startswith(PAPER_ACCOUNT_PREFIX):
            raise IBKRConfigError(
                "refusing non-paper IBKR account id: "
                f"{account!r} must start with {PAPER_ACCOUNT_PREFIX}"
            )
        object.__setattr__(self, "account_id", account)
        base = str(self.base_url).strip().rstrip("/")
        if not base.startswith(("https://", "http://")):
            raise IBKRConfigError(
                f"IBKR_PAPER_BASE_URL must be an http(s) URL, got {base!r}"
            )
        object.__setattr__(self, "base_url", base)
        if self.timeout <= 0:
            raise IBKRConfigError("IBKR_PAPER_TIMEOUT must be > 0")
        if self.poll_interval < 0:
            raise IBKRConfigError("IBKR_PAPER_POLL_INTERVAL must be >= 0")
        if self.max_poll_seconds < 0:
            raise IBKRConfigError("IBKR_PAPER_MAX_POLL must be >= 0")
        if self.max_retries < 0:
            raise IBKRConfigError("IBKR_PAPER_MAX_RETRIES must be >= 0")
        if self.backoff_base < 0:
            raise IBKRConfigError("IBKR_PAPER_BACKOFF must be >= 0")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> IBKRPaperConfig:
        e = os.environ if env is None else env
        account = e.get("IBKR_PAPER_ACCOUNT", "").strip()
        if not account:
            raise IBKRConfigError(
                "IBKR_PAPER_ACCOUNT is not set; export the DU paper account id "
                "or use --broker paper"
            )
        return cls(
            account_id=account,
            base_url=e.get("IBKR_PAPER_BASE_URL", DEFAULT_BASE_URL),
            timeout=_env_float(e, "IBKR_PAPER_TIMEOUT", 15.0),
            poll_interval=_env_float(e, "IBKR_PAPER_POLL_INTERVAL", 1.0),
            max_poll_seconds=_env_float(e, "IBKR_PAPER_MAX_POLL", 30.0),
            max_retries=_env_int(e, "IBKR_PAPER_MAX_RETRIES", 3),
            backoff_base=_env_float(e, "IBKR_PAPER_BACKOFF", 0.5),
            verify_ssl=_env_bool(e, "IBKR_PAPER_VERIFY_SSL", True),
            read_only=_env_bool(e, "IBKR_PAPER_READ_ONLY", False),
        )


def _env_float(env: Mapping[str, str], key: str, default: float) -> float:
    raw = env.get(key)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise IBKRConfigError(f"{key} must be a number, got {raw!r}") from exc


def _env_int(env: Mapping[str, str], key: str, default: int) -> int:
    raw = env.get(key)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise IBKRConfigError(f"{key} must be an integer, got {raw!r}") from exc


def _env_bool(env: Mapping[str, str], key: str, default: bool) -> bool:
    raw = env.get(key)
    if raw is None or raw == "":
        return default
    value = raw.strip().lower()
    if value in ("1", "true", "yes", "on"):
        return True
    if value in ("0", "false", "no", "off"):
        return False
    raise IBKRConfigError(f"{key} must be a boolean, got {raw!r}")


class IBKRTransport(Protocol):
    """HTTP surface used by ``IBKRPaperBroker``. Tests provide a fake."""

    def authenticate(self) -> None: ...

    def accounts(self) -> list[str]: ...

    def resolve_conid(self, symbol: str) -> int: ...

    def place_order(self, payload: dict[str, Any]) -> Any: ...

    def reply(self, reply_id: str) -> Any: ...

    def order_status(self, broker_order_id: str) -> dict[str, Any]: ...

    def open_orders(self) -> list[dict[str, Any]]: ...

    def cancel_order(self, broker_order_id: str) -> Any: ...

    def replace_order(self, broker_order_id: str, payload: dict[str, Any]) -> Any: ...

    def positions(self) -> list[dict[str, Any]]: ...

    def cash(self) -> float: ...

    def account_summary(self) -> dict[str, Any]: ...

    def executions(self) -> list[dict[str, Any]]: ...


class ClientPortalTransport:
    """Thin Client Portal Web API client.

    The Client Portal Gateway must already be running and logged in. This class does
    not store or transmit credentials; it only reuses the gateway's local session.
    """

    def __init__(
        self,
        config: IBKRPaperConfig,
        *,
        session: requests.Session | None = None,
    ):
        self.config = config
        self.session = session or requests.Session()
        self.session.verify = config.verify_ssl
        self.session.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": "stonks-bot/ibkr-paper",
            }
        )
        self._conids: dict[str, int] = {}

    def _url(self, path: str) -> str:
        return f"{self.config.base_url}{path}"

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            resp = self.session.request(
                method,
                self._url(path),
                timeout=self.config.timeout,
                **kwargs,
            )
        except requests.RequestException as exc:
            raise IBKRTransientError(f"{method} {path}: {exc}") from exc
        if resp.status_code in (401, 403):
            raise IBKRAuthError(
                f"{method} {path}: gateway not authenticated ({resp.status_code})"
            )
        payload: Any
        try:
            payload = resp.json() if resp.content else {}
        except ValueError:
            payload = {"text": resp.text[:1000]}
        if resp.status_code in (408, 425, 429) or resp.status_code >= 500:
            raise IBKRTransientError(
                f"{method} {path}: HTTP {resp.status_code}: {payload}"
            )
        if resp.status_code >= 400:
            message = _message_of(payload) or f"HTTP {resp.status_code}"
            if "duplicate" in message.lower() or "clientorderid" in message.lower():
                raise IBKRDuplicateOrder(message, status_code=resp.status_code)
            raise IBKRRequestError(
                f"{method} {path}: {message}",
                status_code=resp.status_code,
                payload=payload,
            )
        return payload

    def authenticate(self) -> None:
        payload = self._request("GET", "/iserver/auth/status")
        if isinstance(payload, dict) and payload.get("authenticated") is False:
            raise IBKRAuthError(
                "Client Portal Gateway is reachable but not authenticated; "
                "open the gateway and complete paper login/2FA"
            )

    def accounts(self) -> list[str]:
        payload = self._request("GET", "/iserver/accounts")
        raw = payload.get("accounts", []) if isinstance(payload, dict) else payload
        out: list[str] = []
        for item in raw or []:
            if isinstance(item, str):
                out.append(item)
            elif isinstance(item, dict):
                value = item.get("accountId") or item.get("account")
                if value:
                    out.append(str(value))
        return out

    def resolve_conid(self, symbol: str) -> int:
        key = symbol.upper()
        if key in self._conids:
            return self._conids[key]
        payload = self._request(
            "GET",
            "/iserver/secdef/search",
            params={"symbol": symbol, "secType": "STK"},
        )
        rows = payload if isinstance(payload, list) else payload.get("data", [])
        for row in rows:
            if not isinstance(row, dict) or row.get("conid") is None:
                continue
            conid = int(row["conid"])
            self._conids[key] = conid
            return conid
        raise IBKRRequestError(f"no contract found for {symbol!r}")

    def place_order(self, payload: dict[str, Any]) -> Any:
        return self._request(
            "POST",
            f"/iserver/account/{self.config.account_id}/orders",
            json={"orders": [payload]},
        )

    def reply(self, reply_id: str) -> Any:
        return self._request(
            "POST",
            f"/iserver/reply/{reply_id}",
            json={"confirmed": True},
        )

    def order_status(self, broker_order_id: str) -> dict[str, Any]:
        payload = self._request(
            "GET", f"/iserver/account/order/status/{broker_order_id}"
        )
        return payload if isinstance(payload, dict) else {"data": payload}

    def open_orders(self) -> list[dict[str, Any]]:
        payload = self._request("GET", "/iserver/account/orders")
        if isinstance(payload, list):
            return [x for x in payload if isinstance(x, dict)]
        rows = payload.get("orders", []) if isinstance(payload, dict) else []
        return [x for x in rows if isinstance(x, dict)]

    def cancel_order(self, broker_order_id: str) -> Any:
        return self._request(
            "DELETE",
            f"/iserver/account/{self.config.account_id}/order/{broker_order_id}",
        )

    def replace_order(self, broker_order_id: str, payload: dict[str, Any]) -> Any:
        return self._request(
            "POST",
            f"/iserver/account/{self.config.account_id}/order/{broker_order_id}",
            json=payload,
        )

    def positions(self) -> list[dict[str, Any]]:
        payload = self._request("GET", f"/portfolio/{self.config.account_id}/positions")
        if isinstance(payload, list):
            return [x for x in payload if isinstance(x, dict)]
        rows = payload.get("positions", []) if isinstance(payload, dict) else []
        return [x for x in rows if isinstance(x, dict)]

    def cash(self) -> float:
        payload = self._request("GET", f"/portfolio/{self.config.account_id}/ledger")
        if not isinstance(payload, dict):
            raise IBKRRequestError("unexpected ledger payload")
        for section in ("BASE", "USD", "EUR"):
            row = payload.get(section)
            if not isinstance(row, dict):
                continue
            for key in ("cashbalance", "cashBalance", "totalcashvalue"):
                if row.get(key) is not None:
                    return float(row[key])
        raise IBKRRequestError("ledger has no cash balance")

    def account_summary(self) -> dict[str, Any]:
        payload = self._request("GET", f"/portfolio/{self.config.account_id}/summary")
        if not isinstance(payload, dict):
            raise IBKRRequestError("unexpected account-summary payload")
        return payload

    def executions(self) -> list[dict[str, Any]]:
        payload = self._request("GET", "/iserver/account/trades")
        if isinstance(payload, list):
            return [x for x in payload if isinstance(x, dict)]
        rows = (
            payload.get("trades", payload.get("executions", []))
            if isinstance(payload, dict)
            else []
        )
        return [x for x in rows if isinstance(x, dict)]


@dataclass
class BrokerReconcile:
    """Result of comparing bot state with broker positions/cash."""

    ok: bool = True
    issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    broker_cash: float | None = None
    broker_positions: dict[str, float] = field(default_factory=dict)
    bot_cash: float = 0.0
    bot_positions: dict[str, float] = field(default_factory=dict)
    missing_positions: list[str] = field(default_factory=list)
    extra_positions: list[str] = field(default_factory=list)
    position_diffs: dict[str, tuple[float, float]] = field(default_factory=dict)
    unknown_executions: list[str] = field(default_factory=list)
    stale: bool = False
    account_id: str | None = None

    def fail(self, message: str) -> None:
        self.ok = False
        self.issues.append(message)

    def lines(self) -> list[str]:
        out = [
            (
                f"IBKR RECONCILE {'OK' if self.ok else 'FAIL'}  "
                f"account={self.account_id or '?'}  "
                f"bot_cash={self.bot_cash:,.2f}  broker_cash="
                f"{'?' if self.broker_cash is None else f'{self.broker_cash:,.2f}'}"
            )
        ]
        for ticker in sorted(set(self.broker_positions) | set(self.bot_positions)):
            if ticker in self.missing_positions:
                out.append(f"  FAIL: {ticker} missing at broker")
            elif ticker in self.extra_positions:
                out.append(f"  FAIL: {ticker} extra at broker")
            elif ticker in self.position_diffs:
                b, s = self.position_diffs[ticker]
                out.append(
                    f"  FAIL: {ticker} broker {b:g} vs bot {s:g} (diff {b - s:+g})"
                )
        for issue in self.issues:
            out.append(f"  FAIL: {issue}")
        for unknown in self.unknown_executions:
            out.append(f"  FAIL: unknown execution {unknown}")
        for warning in self.warnings:
            out.append(f"  WARN: {warning}")
        if self.ok:
            out.append("broker positions, cash, open orders and executions agree")
        return out


@dataclass
class IBKRReadOnlySnapshot:
    """A read-only account view with an explicit fetch timestamp."""

    account_id: str
    fetched_at: str
    summary: dict[str, Any]
    positions: list[dict[str, Any]]
    cash: float
    open_orders: list[dict[str, Any]]
    executions: list[dict[str, Any]]


class IBKRPaperBroker:
    """Paper execution through an IBKR Client Portal transport."""

    name = "ibkr-paper"

    def __init__(
        self,
        transport: IBKRTransport,
        config: IBKRPaperConfig,
        *,
        sleeper: Callable[[float], None] | None = None,
        clock: Callable[[], float] | None = None,
        read_only: bool | None = None,
    ):
        self.transport = transport
        self.config = config
        self.read_only = bool(config.read_only if read_only is None else read_only)
        self._sleep = sleeper or time.sleep
        self._clock = clock or time.monotonic
        self._ready = False
        self._conids: dict[str, int] = {}
        self._orders: dict[str, dict[str, Any]] = {}
        self._fill_seq: dict[str, int] = {}

    @classmethod
    def from_env(
        cls,
        *,
        env: Mapping[str, str] | None = None,
        transport: IBKRTransport | None = None,
        sleeper: Callable[[float], None] | None = None,
        read_only: bool | None = None,
    ) -> IBKRPaperBroker:
        config = IBKRPaperConfig.from_env(env)
        return cls(
            transport or ClientPortalTransport(config),
            config,
            sleeper=sleeper,
            read_only=read_only,
        )

    def _guard_write(self, action: str) -> None:
        if self.read_only:
            raise IBKRReadOnlyError(
                f"read-only IBKR session: refusing {action}; "
                "no order, cancel or replace was sent"
            )

    def ensure_ready(self) -> None:
        if self._ready:
            return
        self.transport.authenticate()
        accounts = self.transport.accounts()
        if self.config.account_id not in accounts:
            raise IBKRConfigError(
                f"account {self.config.account_id} is not in the gateway's "
                f"account list {accounts!r}"
            )
        self._ready = True

    def _retry(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        last: Exception | None = None
        for attempt in range(self.config.max_retries + 1):
            try:
                return fn(*args, **kwargs)
            except IBKRTransientError as exc:
                last = exc
                if attempt >= self.config.max_retries:
                    break
                delay = self.config.backoff_base * (2**attempt)
                if delay > 0:
                    self._sleep(delay)
        raise IBKRUnavailable(
            f"broker call failed after {self.config.max_retries + 1} attempts: {last}"
        )

    @staticmethod
    def client_order_id(order: OrderIntent) -> str:
        raw = str(order.order_id)
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", raw)[:30]
        digest = hashlib.sha256(raw.encode()).hexdigest()[:8]
        return f"{safe}-{digest}"[:40]

    def _conid(self, ticker: str) -> int:
        key = ticker.upper()
        if key not in self._conids:
            self._conids[key] = self._retry(self.transport.resolve_conid, ticker)
        return self._conids[key]

    # --- read-only account surface -------------------------------------
    def account_summary(self) -> dict[str, Any]:
        self.ensure_ready()
        return self._retry(self.transport.account_summary)

    def positions(self) -> list[dict[str, Any]]:
        self.ensure_ready()
        return self._retry(self.transport.positions)

    def cash(self) -> float:
        self.ensure_ready()
        return float(self._retry(self.transport.cash))

    def open_orders(self) -> list[dict[str, Any]]:
        self.ensure_ready()
        return self._retry(self.transport.open_orders)

    def executions(self) -> list[dict[str, Any]]:
        self.ensure_ready()
        return self._retry(self.transport.executions)

    def read_snapshot(self, now: str | None = None) -> IBKRReadOnlySnapshot:
        """Fetch one coherent read-only account view; never writes."""
        self.ensure_ready()
        summary = self._retry(self.transport.account_summary)
        positions = self._retry(self.transport.positions)
        cash = float(self._retry(self.transport.cash))
        orders = self._retry(self.transport.open_orders)
        executions = self._retry(self.transport.executions)
        return IBKRReadOnlySnapshot(
            account_id=self.config.account_id,
            fetched_at=now or _utc_now(),
            summary=summary if isinstance(summary, dict) else {},
            positions=[x for x in positions if isinstance(x, dict)],
            cash=cash,
            open_orders=[x for x in orders if isinstance(x, dict)],
            executions=[x for x in executions if isinstance(x, dict)],
        )

    def _payload(self, order: OrderIntent, coid: str) -> dict[str, Any]:
        return {
            "conid": self._conid(order.ticker),
            "orderType": "MKT",
            "side": order.side.upper(),
            "quantity": float(order.qty),
            "tif": "DAY",
            "cOID": coid,
            "outsideRth": False,
            "referrer": "stonks-bot",
        }

    @staticmethod
    def _ack(payload: Any) -> dict[str, Any]:
        if isinstance(payload, list):
            payload = payload[0] if payload else {}
        if not isinstance(payload, dict):
            raise IBKRRequestError(f"unexpected order acknowledgement: {payload!r}")
        error = payload.get("error")
        if error:
            message = str(error)
            if "duplicate" in message.lower() or "clientorderid" in message.lower():
                raise IBKRDuplicateOrder(message, payload=payload)
            raise IBKRRequestError(message, payload=payload)
        return payload

    def _confirm(self, payload: Any) -> dict[str, Any]:
        ack = self._ack(payload)
        if (
            "order_id" not in ack
            and "orderId" not in ack
            and "order_id" not in ack
            and ack.get("id") is not None
            and ("message" in ack or "isSuppressed" in ack)
        ):
            reply = self._retry(self.transport.reply, str(ack["id"]))
            return self._confirm(reply)
        return ack

    @staticmethod
    def _broker_order_id(ack: Mapping[str, Any]) -> str:
        for key in ("order_id", "orderId", "order_id"):
            if ack.get(key) is not None:
                return str(ack[key])
        raise IBKRRequestError(f"ack has no order id: {ack!r}")

    @staticmethod
    def _status_text(payload: Mapping[str, Any]) -> str:
        for key in ("order_status", "status", "orderStatus", "orderState"):
            value = payload.get(key)
            if value is not None:
                return str(value)
        return "Submitted"

    def _record(
        self,
        *,
        coid: str,
        broker_order_id: str,
        ticker: str,
        status: str,
        filled_qty: float = 0.0,
        avg_price: float = 0.0,
        commission: float = 0.0,
    ) -> dict[str, Any]:
        rec = {
            "coid": coid,
            "broker_order_id": broker_order_id,
            "ticker": ticker,
            "status": status,
            "filled_qty": float(filled_qty),
            "avg_price": float(avg_price),
            "commission": float(commission),
        }
        self._orders[coid] = rec
        return rec

    def _find_by_coid(self, coid: str) -> dict[str, Any] | None:
        for row in self._retry(self.transport.open_orders):
            value = row.get("cOID") or row.get("coid") or row.get("clientOrderId")
            if value != coid:
                continue
            broker_id = row.get("order_id") or row.get("orderId") or row.get("orderId")
            if broker_id is None:
                continue
            return self._record(
                coid=coid,
                broker_order_id=str(broker_id),
                ticker=str(row.get("ticker") or row.get("symbol") or ""),
                status=self._status_text(row),
                filled_qty=_number(row, "filledQuantity", "filled_qty", default=0.0),
                avg_price=_number(row, "avgPrice", "averagePrice", default=0.0),
                commission=_number(row, "commission", "commissionAmount", default=0.0),
            )
        return None

    def submit(
        self,
        order: OrderIntent,
        fill_session: str,
        now: str | None = None,
    ) -> dict[str, Any]:
        """Submit (or adopt) one order exactly once. Returns the broker record."""
        self._guard_write("submit")
        self.ensure_ready()
        coid = self.client_order_id(order)
        if coid in self._orders:
            return self._orders[coid]
        existing = self._find_by_coid(coid)
        if existing is not None:
            return existing
        payload = self._payload(order, coid)
        try:
            ack = self._confirm(self._retry(self.transport.place_order, payload))
        except IBKRDuplicateOrder:
            existing = self._find_by_coid(coid)
            if existing is None:
                raise
            return existing
        broker_id = self._broker_order_id(ack)
        return self._record(
            coid=coid,
            broker_order_id=broker_id,
            ticker=order.ticker,
            status=self._status_text(ack),
            filled_qty=_number(ack, "filledQuantity", default=0.0),
            avg_price=_number(ack, "avgPrice", "averagePrice", default=0.0),
            commission=_number(ack, "commission", "commissionAmount", default=0.0),
        )

    def _apply_status(
        self,
        order: OrderIntent,
        rec: dict[str, Any],
        status: Mapping[str, Any],
        plan: Plan,
        portfolio: Portfolio,
        now: str,
    ) -> list[Fill]:
        cum_qty = _number(
            status, "filledQuantity", "filled_qty", "filled", default=rec["filled_qty"]
        )
        if cum_qty <= rec["filled_qty"] + 1e-12:
            return []
        if cum_qty > float(order.qty) + 1e-8:
            raise IBKRRequestError(
                f"{order.order_id}: broker filled {cum_qty} > order {order.qty}"
            )
        delta = cum_qty - float(rec["filled_qty"])
        avg = _number(
            status,
            "avgPrice",
            "averagePrice",
            "avg_price",
            default=float(rec["avg_price"]),
        )
        last = _number(status, "lastFillPrice", "last_fill_price", default=0.0)
        if last > 0:
            price = last
        elif avg > 0 and cum_qty > 0:
            price = (
                avg * cum_qty - float(rec["avg_price"]) * rec["filled_qty"]
            ) / delta
        else:
            price = float(order.ref_price)
        commission_total = _number(
            status,
            "commission",
            "commissionAmount",
            "commission_amount",
            default=float(rec["commission"]),
        )
        commission = max(0.0, commission_total - float(rec["commission"]))
        seq = self._fill_seq.get(rec["coid"], 0)
        self._fill_seq[rec["coid"]] = seq + 1
        fill = Fill(
            fill_id=f"{rec['coid']}:{seq}",
            order_id=order.order_id,
            ticker=order.ticker,
            side=order.side,
            qty=float(delta),
            price=float(price),
            commission=float(commission),
            session=plan.fill_session,
            created_at=now or _utc_now(),
        )
        fill.realized_pnl = portfolio.apply_fill(fill)
        rec["filled_qty"] = float(cum_qty)
        rec["avg_price"] = float(avg)
        rec["commission"] = float(commission_total)
        rec["status"] = self._status_text(status)
        order.filled_qty = float(cum_qty)
        return [fill]

    def _terminal(self, status: str, order: OrderIntent) -> str | None:
        key = status.upper().replace(" ", "")
        if "FILLED" in key and "PARTIALLY" not in key:
            return FILLED
        if "CANCEL" in key:
            return CANCELLED
        if "REJECT" in key or "INACTIVE" in key:
            return REJECTED
        return None

    def _poll_order(
        self,
        order: OrderIntent,
        rec: dict[str, Any],
        plan: Plan,
        portfolio: Portfolio,
        now: str,
    ) -> list[Fill]:
        fills: list[Fill] = []
        deadline = self._clock() + max(0.0, float(self.config.max_poll_seconds))
        while True:
            status = self._retry(self.transport.order_status, rec["broker_order_id"])
            fills.extend(self._apply_status(order, rec, status, plan, portfolio, now))
            text = self._status_text(status)
            terminal = self._terminal(text, order)
            if terminal is not None:
                order.status = terminal
                return fills
            if self._clock() >= deadline:
                break
            if self.config.poll_interval > 0:
                self._sleep(self.config.poll_interval)
        # A day order that has not terminalised within the poll window is not left
        # orphaned. Cancel the remainder; the engine records the actual fills.
        try:
            self._retry(self.transport.cancel_order, rec["broker_order_id"])
            final = self._retry(self.transport.order_status, rec["broker_order_id"])
            fills.extend(self._apply_status(order, rec, final, plan, portfolio, now))
            terminal = self._terminal(self._status_text(final), order)
            if terminal is not None:
                order.status = terminal
                return fills
        except IBKRTransientError:
            pass
        order.status = PARTIAL if order.filled_qty > 1e-12 else CANCELLED
        return fills

    def execute(
        self,
        plan: Plan,
        bars: dict[str, Bar],
        now: str,
        portfolio: Portfolio,
    ) -> list[Fill]:
        """Submit the plan's pending orders and collect fills.

        ``bars`` is the engine's session-open gate. IBKR supplies the actual fill;
        the adapter never fabricates a price from the cached bar.
        """
        self._guard_write("execute")
        self.ensure_ready()
        fills: list[Fill] = []
        orders = sorted(
            plan.orders, key=lambda o: (0 if o.side == "SELL" else 1, o.ticker)
        )
        for order in orders:
            if order.status not in (PENDING, PARTIAL):
                continue
            rec = self.submit(order, plan.fill_session, now)
            fills.extend(self._poll_order(order, rec, plan, portfolio, now))
        return fills

    def status(self, broker_order_id: str) -> dict[str, Any]:
        self.ensure_ready()
        return self._retry(self.transport.order_status, broker_order_id)

    def cancel(self, broker_order_id: str) -> Any:
        self._guard_write("cancel")
        self.ensure_ready()
        return self._retry(self.transport.cancel_order, broker_order_id)

    def replace(
        self,
        broker_order_id: str,
        *,
        qty: float | None = None,
        limit_price: float | None = None,
    ) -> Any:
        self._guard_write("replace")
        self.ensure_ready()
        status = self._retry(self.transport.order_status, broker_order_id)
        conid = status.get("conid") or status.get("contract_id")
        if conid is None:
            raise IBKRRequestError(
                f"cannot replace {broker_order_id}: status has no conid"
            )
        payload: dict[str, Any] = {
            "conid": int(conid),
            "orderType": "LMT" if limit_price is not None else "MKT",
            "quantity": (
                float(qty)
                if qty is not None
                else float(status.get("remainingQuantity") or 0.0)
            ),
            "tif": "DAY",
            "cOID": status.get("cOID") or status.get("coid") or broker_order_id,
            "outsideRth": False,
        }
        if not payload["quantity"]:
            raise IBKRRequestError(
                f"cannot replace {broker_order_id}: quantity is required"
            )
        if limit_price is not None:
            payload["price"] = float(limit_price)
        return self._retry(self.transport.replace_order, broker_order_id, payload)

    def reconcile(self, portfolio: Portfolio) -> BrokerReconcile:
        """Compare bot state with the paper account's positions and cash."""
        self.ensure_ready()
        try:
            rows = self._retry(self.transport.positions)
            cash = self._retry(self.transport.cash)
        except IBKRError as exc:
            report = BrokerReconcile(ok=False, bot_cash=float(portfolio.cash))
            report.fail(f"broker unreachable: {exc}")
            return report
        broker: dict[str, float] = {}
        for row in rows:
            ticker = (
                row.get("ticker")
                or row.get("contractDesc")
                or row.get("symbol")
                or row.get("description")
            )
            if not ticker:
                continue
            qty = _number(row, "position", "qty", default=0.0)
            if abs(qty) <= 1e-9:
                continue
            broker[str(ticker).split()[0].upper()] = qty
        bot = {
            ticker: float(pos.qty)
            for ticker, pos in portfolio.positions.items()
            if abs(pos.qty) > 1e-9
        }
        report = BrokerReconcile(
            broker_cash=float(cash),
            broker_positions=broker,
            bot_cash=float(portfolio.cash),
            bot_positions=bot,
        )
        for ticker in sorted(set(broker) | set(bot)):
            b = broker.get(ticker, 0.0)
            s = bot.get(ticker, 0.0)
            if abs(b - s) > 1e-4:
                report.fail(f"{ticker}: broker qty {b} != bot qty {s}")
        cash_tol = max(1.0, abs(float(portfolio.cash)) * 1e-6)
        if abs(float(cash) - float(portfolio.cash)) > cash_tol:
            report.fail(
                f"cash: broker {float(cash):,.2f} != bot "
                f"{float(portfolio.cash):,.2f} (tol {cash_tol:,.2f})"
            )
        return report

    def reconcile_strict(
        self,
        portfolio: Portfolio,
        *,
        state: Any | None = None,
        snapshot: IBKRReadOnlySnapshot | None = None,
        now: str | None = None,
        max_snapshot_age: float = 300.0,
    ) -> BrokerReconcile:
        """Read-only reconciliation with positions, cash, orders and executions.

        `state` is a LiveState-like object. It is used to detect unknown broker
        open orders and executions; without it, any broker execution/open order
        fails the reconciliation. A snapshot older than `max_snapshot_age`
        seconds also fails closed.
        """
        snap = snapshot or self.read_snapshot()
        report = BrokerReconcile(
            broker_cash=float(snap.cash),
            broker_positions=_position_map(snap.positions),
            bot_cash=float(portfolio.cash),
            bot_positions={
                ticker: float(pos.qty)
                for ticker, pos in portfolio.positions.items()
                if abs(pos.qty) > 1e-9
            },
            account_id=snap.account_id,
        )
        # Snapshot age: a read taken for a different session is not evidence.
        current = _parse_ts(now) if now else datetime.now(UTC)
        fetched = _parse_ts(snap.fetched_at)
        if current is None or fetched is None:
            report.fail("broker snapshot timestamp is missing or unparseable")
        elif abs((current - fetched).total_seconds()) > max(1.0, max_snapshot_age):
            report.stale = True
            report.fail(
                f"stale broker snapshot: fetched {snap.fetched_at}, "
                f"now {current.isoformat(timespec='seconds')}"
            )

        for ticker in sorted(set(report.broker_positions) | set(report.bot_positions)):
            b = report.broker_positions.get(ticker, 0.0)
            s = report.bot_positions.get(ticker, 0.0)
            if abs(b - s) > 1e-4:
                report.position_diffs[ticker] = (b, s)
                if ticker not in report.bot_positions:
                    report.extra_positions.append(ticker)
                elif ticker not in report.broker_positions:
                    report.missing_positions.append(ticker)
                report.fail(f"{ticker}: broker {b:g} != bot {s:g} (diff {b - s:+g})")

        cash_tol = max(1.0, abs(float(portfolio.cash)) * 1e-6)
        if abs(float(snap.cash) - float(portfolio.cash)) > cash_tol:
            report.fail(
                f"cash: broker {float(snap.cash):,.2f} != bot "
                f"{float(portfolio.cash):,.2f} (tol {cash_tol:,.2f})"
            )

        bot_orders = []
        if state is not None:
            for plan in getattr(state, "plans", {}).values():
                for order in plan.orders:
                    if order.status in (PENDING, PARTIAL):
                        bot_orders.append(order)
        for row in snap.open_orders:
            ticker = _ticker_of(row)
            side = str(row.get("side") or row.get("buySell") or "").upper()
            qty = abs(_number(row, "quantity", "qty", "remainingQuantity"))
            matched = any(
                o.ticker.upper() == ticker
                and o.side.upper() == side
                and abs(abs(float(o.remaining)) - qty) <= 1e-4
                for o in bot_orders
            )
            if ticker and not matched:
                report.fail(f"unknown open order at broker: {ticker} {side} {qty:g}")

        if snap.executions:
            if state is None:
                report.unknown_executions.append("all executions (no bot state)")
                report.fail("broker executions present but no bot state to match")
            else:
                fills = list(state.all_fills())
                for row in snap.executions:
                    ticker = _ticker_of(row)
                    side = _side_of(row)
                    qty = abs(
                        _number(row, "quantity", "size", "shares", "filledQuantity")
                    )
                    session = _execution_session(row)
                    matched = any(
                        f.ticker.upper() == ticker
                        and f.side.upper() == side
                        and abs(abs(float(f.qty)) - qty) <= 1e-4
                        and (not session or str(f.session).startswith(session))
                        for f in fills
                    )
                    if ticker and not matched:
                        label = f"{ticker} {side} {qty:g}"
                        if session:
                            label += f" {session}"
                        report.unknown_executions.append(label)
                if report.unknown_executions:
                    report.fail(
                        f"{len(report.unknown_executions)} unknown execution(s) at broker"
                    )
        return report


def _ticker_of(row: Mapping[str, Any]) -> str:
    value = (
        row.get("ticker")
        or row.get("symbol")
        or row.get("contractDesc")
        or row.get("description")
        or ""
    )
    return str(value).split()[0].upper() if str(value).strip() else ""


def _side_of(row: Mapping[str, Any]) -> str:
    raw = str(row.get("side") or row.get("buySell") or "").upper()
    if raw in ("BOT", "BUY", "B"):
        return "BUY"
    if raw in ("SLD", "SELL", "S"):
        return "SELL"
    return raw


def _execution_session(row: Mapping[str, Any]) -> str:
    raw = (
        row.get("trade_time")
        or row.get("tradeTime")
        or row.get("date")
        or row.get("execution_time")
        or ""
    )
    return str(raw)[:10] if raw else ""


def _position_map(rows: list[Mapping[str, Any]]) -> dict[str, float]:
    out: dict[str, float] = {}
    for row in rows:
        ticker = _ticker_of(row)
        if not ticker:
            continue
        qty = _number(row, "position", "qty", "quantity")
        if abs(qty) > 1e-9:
            out[ticker] = float(qty)
    return out


def _parse_ts(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _number(
    payload: Mapping[str, Any],
    *keys: str,
    default: float = 0.0,
) -> float:
    for key in keys:
        value = payload.get(key)
        if value is None or value == "":
            continue
        try:
            out = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(out):
            return out
    return float(default)


def _message_of(payload: Any) -> str:
    if isinstance(payload, dict):
        for key in ("error", "message", "error_description"):
            value = payload.get(key)
            if value:
                return str(value)
        return str(payload)[:500]
    return str(payload)[:500]
