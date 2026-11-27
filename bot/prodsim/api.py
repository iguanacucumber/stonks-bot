"""HTTP-facing API layer for the v12 production simulation.

The layer is deliberately framework-free: ``SimulationAPI.handle`` takes a
parsed method/path/query/headers/body and returns a ``Response``. ``server.py``
owns sockets, timeouts, body limits and graceful shutdown; this module owns
routing, auth policy, JSON validation, idempotency and error mapping.

Simulation only: the facade behind ``SimulationProtocol`` never talks to a real
broker.

Response shapes are intentionally small: a single order is returned as an
``OrderState`` object, list endpoints wrap their rows as
``{"orders"|"positions"|"events": [...], "count": N, ...}`` and errors are
``{"error": "message", "code": "stable_code"}``. The first accepted submission
returns 201; an ``Idempotency-Key`` replay returns 200 with
``Idempotency-Replayed: true``. Unknown paths are 404, killed/paused trading is
423 and a failed readiness check is 503.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import math
import os
import threading
import time
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, get_args
from urllib.parse import unquote

from .auth import (
    ADMIN_TOKEN_ENV,
    API_TOKEN_ENV,
    AuthConfig,
    AuthError,
)
from .contract import (
    OrderRequest,
    OrderState,
    OrderStatus,
    SimulationProtocol,
    SimulationStatus,
    iso,
    jsonable,
    utc_now,
)
from .metrics import Metrics

LOG = logging.getLogger("bot.prodsim.api")

JSON_CONTENT_TYPE = "application/json; charset=utf-8"
METRICS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"
PUBLIC_ROUTES = frozenset({"/healthz", "/readyz", "/metrics"})
VALID_ORDER_STATUSES = frozenset(get_args(OrderStatus))
MAX_IDEMPOTENCY_KEY = 255
MAX_QUERY_INT = 2**63 - 1


class ApiError(Exception):
    """An error that maps directly to an HTTP response."""

    def __init__(
        self,
        status: int,
        message: str,
        code: str = "error",
        headers: tuple[tuple[str, str], ...] = (),
    ) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.code = code
        self.headers = headers

    def response(self) -> Response:
        return json_response(
            {"error": self.message, "code": self.code},
            status=self.status,
            headers=self.headers,
        )


@dataclass(frozen=True)
class Response:
    status: int
    body: bytes
    content_type: str = JSON_CONTENT_TYPE
    headers: tuple[tuple[str, str], ...] = ()


def _encode_json(payload: Any) -> bytes:
    return json.dumps(
        jsonable(payload),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")


def json_response(
    payload: Any,
    status: int = 200,
    headers: Sequence[tuple[str, str]] = (),
) -> Response:
    return Response(status, _encode_json(payload), JSON_CONTENT_TYPE, tuple(headers))


def text_response(
    text: str,
    status: int = 200,
    content_type: str = METRICS_CONTENT_TYPE,
) -> Response:
    return Response(status, text.encode("utf-8"), content_type)


def is_loopback_host(host: str) -> bool:
    if host in {"localhost"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@dataclass(frozen=True)
class ApiConfig:
    """Server/API configuration. Tokens are excluded from ``repr``."""

    host: str = "127.0.0.1"
    port: int = 8080
    api_token: str = field(default="", repr=False)
    admin_token: str = field(default="", repr=False)
    dev: bool = False
    allow_remote: bool = False
    max_body_bytes: int = 64 * 1024
    request_timeout: float = 10.0
    max_limit: int = 1000
    max_idempotency_entries: int = 10_000

    def __post_init__(self) -> None:
        if not self.host:
            raise ValueError("host is required")
        if not 0 <= int(self.port) <= 65535:
            raise ValueError("port must be in 0..65535")
        if not self.allow_remote and not is_loopback_host(self.host):
            raise ValueError(
                f"refusing non-loopback bind {self.host!r}; "
                "pass allow_remote/--allow-remote only for an isolated host"
            )
        if self.max_body_bytes < 1:
            raise ValueError("max_body_bytes must be positive")
        if self.request_timeout <= 0:
            raise ValueError("request_timeout must be positive")
        if self.max_limit < 1:
            raise ValueError("max_limit must be positive")
        if self.max_idempotency_entries < 1:
            raise ValueError("max_idempotency_entries must be positive")
        AuthConfig(
            api_token=self.api_token,
            admin_token=self.admin_token,
            dev=self.dev,
        ).validate()

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
        *,
        host: str = "127.0.0.1",
        port: int = 8080,
        dev: bool = False,
        allow_remote: bool = False,
        run_config: object | None = None,
    ) -> ApiConfig:
        source = os.environ if env is None else env
        run_token = str(getattr(run_config, "api_token", "") or "")
        return cls(
            host=host,
            port=port,
            api_token=source.get(API_TOKEN_ENV, "") or run_token,
            admin_token=source.get(ADMIN_TOKEN_ENV, ""),
            dev=dev,
            allow_remote=allow_remote,
        )

    def auth(self) -> AuthConfig:
        return AuthConfig(
            api_token=self.api_token,
            admin_token=self.admin_token,
            dev=self.dev,
        )


def _header(headers: Mapping[str, str], name: str) -> str | None:
    value = headers.get(name)
    if value is not None:
        return str(value)
    lowered = name.lower()
    for key, candidate in headers.items():
        if str(key).lower() == lowered:
            return str(candidate)
    return None


def _header_values(headers: Mapping[str, str], name: str) -> list[str]:
    get_all = getattr(headers, "get_all", None)
    if callable(get_all):
        values = get_all(name)
        if values:
            return [str(value) for value in values]
    value = _header(headers, name)
    return [] if value is None else [value]


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "ready", "ok"}
    return bool(value)


@dataclass
class _IdempotencyEntry:
    fingerprint: str
    event: threading.Event = field(default_factory=threading.Event)
    order_id: str | None = None
    order_json: dict[str, Any] | None = None
    error: ApiError | None = None
    done: bool = False


# (method, path) -> (route label, handler method name, admin only)
_ROUTES: dict[tuple[str, str], tuple[str, str, bool]] = {
    ("GET", "/healthz"): ("/healthz", "_healthz", False),
    ("GET", "/readyz"): ("/readyz", "_readyz", False),
    ("GET", "/api/v1/status"): ("/api/v1/status", "_status", False),
    ("GET", "/api/v1/account"): ("/api/v1/account", "_account", False),
    ("GET", "/api/v1/positions"): ("/api/v1/positions", "_positions", False),
    ("GET", "/api/v1/orders"): ("/api/v1/orders", "_orders", False),
    ("POST", "/api/v1/orders"): ("/api/v1/orders", "_submit", False),
    ("POST", "/api/v1/admin/pause"): ("/api/v1/admin/pause", "_pause", True),
    ("POST", "/api/v1/admin/resume"): ("/api/v1/admin/resume", "_resume", True),
    ("POST", "/api/v1/admin/kill"): ("/api/v1/admin/kill", "_kill", True),
    ("GET", "/api/v1/events"): ("/api/v1/events", "_events", False),
    ("GET", "/metrics"): ("/metrics", "_metrics", False),
}
_EXACT_PATHS = frozenset(path for _, path in _ROUTES)


class SimulationAPI:
    """Routes and validates HTTP requests against a ``SimulationProtocol``."""

    def __init__(
        self,
        simulation: SimulationProtocol,
        config: ApiConfig | None = None,
        metrics: Metrics | None = None,
    ) -> None:
        self.sim = simulation
        self.config = config or ApiConfig.from_env()
        self.auth = self.config.auth()
        self.metrics = metrics or Metrics()
        self._lock = threading.Lock()
        self._idempotency: OrderedDict[str, _IdempotencyEntry] = OrderedDict()

    # -- request entry point -------------------------------------------------

    def handle(
        self,
        method: str,
        path: str,
        query: Mapping[str, Sequence[str]] | None = None,
        headers: Mapping[str, str] | None = None,
        body: bytes = b"",
        request_id: str = "",
    ) -> Response:
        method = method.upper()
        query = query or {}
        headers = headers or {}
        started = time.monotonic()
        route = "unmatched"
        status = 500
        self.metrics.add_gauge("prodsim_api_http_in_flight", 1.0)
        try:
            route, handler_name, admin, order_id = self._resolve(method, path)
            self._authorize(route, admin, headers)
            if order_id:
                headers = _attach_order_id(headers, order_id)
            response = getattr(self, handler_name)(query, headers, body)
            status = response.status
            return response
        except ApiError as exc:
            status = exc.status
            return exc.response()
        except AuthError as exc:  # defensive: _authorize normally maps this
            status = 401
            self.metrics.auth_failure(exc.reason)
            return json_response(
                {"error": "unauthorized", "code": "unauthorized"},
                401,
                (("WWW-Authenticate", 'Bearer realm="prodsim"'),),
            )
        except (BrokenPipeError, ConnectionResetError):
            status = 499
            raise
        except Exception as exc:  # noqa: BLE001 - boundary must not leak details
            # Never log exception text: downstream facades may include
            # sensitive values. Type-only logging keeps tokens out of logs.
            LOG.error(
                "unhandled API error method=%s route=%s type=%s",
                method,
                route,
                type(exc).__name__,
            )
            status = 500
            return json_response(
                {"error": "internal server error", "code": "internal_error"},
                500,
            )
        finally:
            self.metrics.add_gauge("prodsim_api_http_in_flight", -1.0)
            self.metrics.record_http(method, route, status, time.monotonic() - started)

    def _resolve(self, method: str, path: str) -> tuple[str, str, bool, str | None]:
        lookup = "GET" if method == "HEAD" else method
        route = _ROUTES.get((lookup, path))
        if route is not None:
            label, handler, admin = route
            return label, handler, admin, None
        if path in _EXACT_PATHS:
            raise ApiError(405, "method not allowed", "method_not_allowed")
        if path.startswith("/api/v1/orders/"):
            order_id = unquote(path[len("/api/v1/orders/") :])
            if not order_id or "/" in order_id:
                raise ApiError(404, "unknown resource", "not_found")
            if lookup == "GET":
                return "/api/v1/orders/{id}", "_get_order", False, order_id
            if method == "DELETE":
                return "/api/v1/orders/{id}", "_cancel", False, order_id
            raise ApiError(405, "method not allowed", "method_not_allowed")
        raise ApiError(404, "unknown resource", "not_found")

    def _authorize(self, route: str, admin: bool, headers: Mapping[str, str]) -> None:
        if route in PUBLIC_ROUTES:
            return
        authorization = _header_values(headers, "Authorization")
        if len(authorization) > 1:
            self.metrics.auth_failure("duplicate_authorization")
            raise ApiError(
                401,
                "unauthorized",
                "unauthorized",
                (("WWW-Authenticate", 'Bearer realm="prodsim"'),),
            )
        try:
            self.auth.check(authorization[0] if authorization else None, admin=admin)
        except AuthError as exc:
            self.metrics.auth_failure(exc.reason)
            raise ApiError(
                401,
                "unauthorized",
                "unauthorized",
                (("WWW-Authenticate", 'Bearer realm="prodsim"'),),
            ) from None

    # -- health/readiness ----------------------------------------------------

    def _healthz(
        self,
        query: Mapping[str, Sequence[str]],
        headers: Mapping[str, str],
        body: bytes,
    ) -> Response:
        return json_response(
            {
                "status": "ok",
                "component": "prodsim-api",
                "time": iso(utc_now()),
            }
        )

    def _readyz(
        self,
        query: Mapping[str, Sequence[str]],
        headers: Mapping[str, str],
        body: bytes,
    ) -> Response:
        try:
            health = jsonable(self.sim.health())
        except Exception as exc:  # noqa: BLE001
            LOG.warning("readiness check failed type=%s", type(exc).__name__)
            self.metrics.set("prodsim_api_ready", 0.0)
            return json_response(
                {"ready": False, "status": "error"},
                status=503,
            )
        if not isinstance(health, dict):
            health = {"detail": health}
        ready = self._is_ready(health)
        health["ready"] = ready
        self.metrics.set("prodsim_api_ready", 1.0 if ready else 0.0)
        return json_response(health, status=200 if ready else 503)

    def _is_ready(
        self, health: Mapping[str, Any], status: SimulationStatus | None = None
    ) -> bool:
        if status is None:
            status = self._status_optional()
        if status is not None and status.state == "KILLED":
            return False
        for key in ("ready", "ok", "healthy"):
            if key in health:
                return _as_bool(health[key])
        return status is not None and status.state == "RUNNING"

    def _status_optional(self) -> SimulationStatus | None:
        try:
            return self.sim.status()
        except Exception as exc:  # noqa: BLE001
            LOG.warning("simulation status unavailable type=%s", type(exc).__name__)
            return None

    def _require_running(self) -> SimulationStatus:
        try:
            status = self.sim.status()
        except Exception as exc:
            raise ApiError(503, "simulation status unavailable", "not_ready") from exc
        if status.state == "KILLED":
            raise ApiError(423, "simulation is killed", "killed")
        if status.state == "PAUSED":
            raise ApiError(423, "simulation is paused", "paused")
        if status.state != "RUNNING":
            raise ApiError(503, f"simulation is {status.state.lower()}", "not_ready")
        try:
            health = jsonable(self.sim.health())
        except Exception as exc:
            raise ApiError(503, "simulation health unavailable", "not_ready") from exc
        if not isinstance(health, dict) or not self._is_ready(health, status):
            raise ApiError(503, "simulation is not ready", "not_ready")
        return status

    # -- read endpoints ------------------------------------------------------

    def _status(
        self,
        query: Mapping[str, Sequence[str]],
        headers: Mapping[str, str],
        body: bytes,
    ) -> Response:
        return json_response(self._status_or_error())

    def _status_or_error(self) -> SimulationStatus:
        try:
            return self.sim.status()
        except Exception as exc:
            raise ApiError(503, "simulation status unavailable", "not_ready") from exc

    def _account(
        self,
        query: Mapping[str, Sequence[str]],
        headers: Mapping[str, str],
        body: bytes,
    ) -> Response:
        try:
            return json_response(self.sim.account())
        except Exception as exc:
            raise ApiError(503, "account unavailable", "not_ready") from exc

    def _positions(
        self,
        query: Mapping[str, Sequence[str]],
        headers: Mapping[str, str],
        body: bytes,
    ) -> Response:
        try:
            positions = list(self.sim.positions())
        except Exception as exc:
            raise ApiError(503, "positions unavailable", "not_ready") from exc
        return json_response(
            {"positions": jsonable(positions), "count": len(positions)}
        )

    def _orders(
        self,
        query: Mapping[str, Sequence[str]],
        headers: Mapping[str, str],
        body: bytes,
    ) -> Response:
        status = self._query_status(query)
        limit = self._query_int(
            query,
            "limit",
            default=min(100, self.config.max_limit),
            minimum=1,
            maximum=self.config.max_limit,
        )
        try:
            orders = list(self.sim.orders(status=status, limit=limit))
        except Exception as exc:
            raise ApiError(503, "orders unavailable", "not_ready") from exc
        return json_response(
            {
                "orders": jsonable(orders),
                "count": len(orders),
                "status": status,
                "limit": limit,
            }
        )

    def _get_order(
        self,
        query: Mapping[str, Sequence[str]],
        headers: Mapping[str, str],
        body: bytes,
    ) -> Response:
        order = self._lookup_order(self._order_id_from_path(headers))
        if order is None:
            raise ApiError(404, "order not found", "not_found")
        return json_response(order)

    def _lookup_order(self, order_id: str) -> OrderState | None:
        try:
            return self.sim.order(order_id)
        except Exception as exc:
            raise ApiError(503, "order lookup unavailable", "not_ready") from exc

    def _order_id_from_path(self, headers: Mapping[str, str]) -> str:
        # server.py injects the decoded path id as a synthetic header so the
        # handler signature stays uniform; direct callers may pass it too.
        value = _header(headers, "X-Prodsim-Order-Id")
        if not value:
            raise ApiError(404, "order not found", "not_found")
        return value

    def _events(
        self,
        query: Mapping[str, Sequence[str]],
        headers: Mapping[str, str],
        body: bytes,
    ) -> Response:
        since = self._query_int(
            query, "since", default=0, minimum=0, maximum=MAX_QUERY_INT
        )
        limit = self._query_int(
            query,
            "limit",
            default=min(100, self.config.max_limit),
            minimum=1,
            maximum=self.config.max_limit,
        )
        try:
            events = list(self.sim.events(since=since, limit=limit))
        except Exception as exc:
            raise ApiError(503, "events unavailable", "not_ready") from exc
        data = [jsonable(event) for event in events]
        next_cursor = since + len(data)
        for item in data:
            if not isinstance(item, Mapping):
                continue
            for key in ("id", "seq", "event_id"):
                value = item.get(key)
                if isinstance(value, int) and not isinstance(value, bool):
                    next_cursor = max(next_cursor, value + 1)
                    break
        return json_response(
            {
                "events": data,
                "count": len(data),
                "since": since,
                "next": next_cursor,
            }
        )

    # -- order submission ----------------------------------------------------

    def _submit(
        self,
        query: Mapping[str, Sequence[str]],
        headers: Mapping[str, str],
        body: bytes,
    ) -> Response:
        key = self._idempotency_key(headers)
        payload = self._json_object(body)
        request = self._order_request(payload, key)
        fingerprint = self._fingerprint(request)
        entry, owner = self._begin_idempotent(key, fingerprint)
        if not owner:
            if entry.error is not None:
                raise entry.error
            if not entry.order_id:
                raise ApiError(503, "idempotent request failed", "not_ready")
            self.metrics.inc("prodsim_api_idempotency_replays_total")
            order = self._cached_order(entry)
            return json_response(
                order,
                status=200,
                headers=(
                    ("Idempotency-Replayed", "true"),
                    ("Location", f"/api/v1/orders/{entry.order_id}"),
                ),
            )

        try:
            self._require_running()
            order = self.sim.submit(request)
            if order is None:
                raise RuntimeError("simulation returned no order")
        except ApiError as exc:
            self._finish_entry(key, entry, error=exc)
            outcome = "blocked" if exc.status == 423 else "unavailable"
            self.metrics.order(outcome)
            raise
        except Exception as exc:
            LOG.error("order submission failed type=%s", type(exc).__name__)
            self._finish_entry(
                key,
                entry,
                error=ApiError(500, "internal server error", "internal_error"),
            )
            self.metrics.order("error")
            raise

        data = self._order_json(order)
        self._finish_entry(
            key, entry, order_id=getattr(order, "order_id", None), order_json=data
        )
        outcome = (
            "rejected" if getattr(order, "status", None) == "REJECTED" else "accepted"
        )
        self.metrics.order(outcome)
        return json_response(
            data,
            status=201,
            headers=(("Location", f"/api/v1/orders/{order.order_id}"),),
        )

    def _cancel(
        self,
        query: Mapping[str, Sequence[str]],
        headers: Mapping[str, str],
        body: bytes,
    ) -> Response:
        self._require_running()
        order_id = self._order_id_from_path(headers)
        order = self._lookup_order(order_id)
        if order is None:
            raise ApiError(404, "order not found", "not_found")
        if not order.is_open:
            raise ApiError(409, "order is not open", "order_not_open")
        try:
            canceled = self.sim.cancel(order_id)
        except KeyError as exc:
            raise ApiError(404, "order not found", "not_found") from exc
        except ValueError as exc:
            raise ApiError(409, str(exc), "conflict") from exc
        except Exception as exc:
            raise ApiError(503, "cancel unavailable", "not_ready") from exc
        if canceled is None:
            raise ApiError(404, "order not found", "not_found")
        self.metrics.order("canceled")
        return json_response(canceled)

    def _begin_idempotent(
        self, key: str, fingerprint: str
    ) -> tuple[_IdempotencyEntry, bool]:
        with self._lock:
            entry = self._idempotency.get(key)
            if entry is None:
                entry = _IdempotencyEntry(fingerprint)
                self._idempotency[key] = entry
                self._prune_locked()
                return entry, True
            if entry.fingerprint != fingerprint:
                self.metrics.inc("prodsim_api_idempotency_conflicts_total")
                raise ApiError(
                    409,
                    "Idempotency-Key was reused with a different payload",
                    "idempotency_conflict",
                )
            if entry.done:
                return entry, False
        if not entry.event.wait(timeout=self.config.request_timeout):
            raise ApiError(
                503,
                "an identical request is still in flight",
                "idempotency_in_flight",
            )
        return entry, False

    def _finish_entry(
        self,
        key: str,
        entry: _IdempotencyEntry,
        *,
        order_id: str | None = None,
        order_json: dict[str, Any] | None = None,
        error: ApiError | None = None,
    ) -> None:
        with self._lock:
            entry.order_id = order_id
            entry.order_json = order_json
            entry.error = error
            entry.done = True
            if error is not None:
                if self._idempotency.get(key) is entry:
                    del self._idempotency[key]
            else:
                self._idempotency.move_to_end(key)
            entry.event.set()
            self._prune_locked()

    def _cached_order(self, entry: _IdempotencyEntry) -> Any:
        if entry.order_id:
            try:
                current = self.sim.order(entry.order_id)
            except Exception as exc:  # noqa: BLE001
                LOG.warning(
                    "idempotent replay lookup failed type=%s",
                    type(exc).__name__,
                )
                current = None
            if current is not None:
                return jsonable(current)
        if entry.order_json is not None:
            return entry.order_json
        raise ApiError(404, "order not found", "not_found")

    def _prune_locked(self) -> None:
        while len(self._idempotency) > self.config.max_idempotency_entries:
            victim = next(
                (key for key, entry in self._idempotency.items() if entry.done),
                None,
            )
            if victim is None:
                return
            del self._idempotency[victim]

    def _idempotency_key(self, headers: Mapping[str, str]) -> str:
        values = _header_values(headers, "Idempotency-Key")
        if not values:
            raise ApiError(
                400, "Idempotency-Key header is required", "missing_idempotency_key"
            )
        if len(values) > 1:
            raise ApiError(
                400,
                "Idempotency-Key header must be given once",
                "duplicate_idempotency_key",
            )
        value = values[0].strip()
        if (
            not value
            or len(value) > MAX_IDEMPOTENCY_KEY
            or not value.isprintable()
            or any(ord(char) > 126 for char in value)
        ):
            raise ApiError(
                400, "invalid Idempotency-Key header", "invalid_idempotency_key"
            )
        return value

    def _json_object(self, body: bytes) -> dict[str, Any]:
        if not body:
            raise ApiError(400, "request body is required", "invalid_body")
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ApiError(
                400, "request body is not valid JSON", "invalid_body"
            ) from exc
        if not isinstance(payload, dict):
            raise ApiError(400, "request body must be a JSON object", "invalid_body")
        return payload

    def _order_request(self, payload: Mapping[str, Any], key: str) -> OrderRequest:
        allowed = {
            "client_order_id",
            "symbol",
            "side",
            "qty",
            "order_type",
            "limit_price",
            "tif",
            "strategy",
            "created_at",
        }
        unknown = sorted(set(payload) - allowed)
        if unknown:
            raise ApiError(
                400, f"unknown field(s): {', '.join(unknown)}", "invalid_order"
            )

        client_order_id = payload.get("client_order_id", key)
        if not isinstance(client_order_id, str):
            raise ApiError(400, "client_order_id must be a string", "invalid_order")
        client_order_id = client_order_id.strip() or key
        if len(client_order_id) > MAX_IDEMPOTENCY_KEY:
            raise ApiError(400, "client_order_id is too long", "invalid_order")

        symbol = payload.get("symbol")
        if (
            not isinstance(symbol, str)
            or not symbol.strip()
            or len(symbol) > 32
            or any(char.isspace() for char in symbol.strip())
        ):
            raise ApiError(400, "symbol is required", "invalid_order")
        symbol = symbol.strip()

        side = self._literal(payload.get("side"), "side", ("BUY", "SELL"))
        order_type = self._literal(
            payload.get("order_type", "MARKET"), "order_type", ("MARKET", "LIMIT")
        )
        tif = self._literal(payload.get("tif", "DAY"), "tif", ("DAY", "IOC", "GTC"))
        qty = self._number(payload, "qty", required=True)
        assert qty is not None
        limit_price = self._number(payload, "limit_price", required=False)
        if order_type == "LIMIT" and limit_price is None:
            raise ApiError(
                400, "LIMIT orders need a positive limit_price", "invalid_order"
            )

        strategy = payload.get("strategy", "")
        if not isinstance(strategy, str) or len(strategy) > 200:
            raise ApiError(400, "strategy must be a short string", "invalid_order")

        created_at = self._created_at(payload.get("created_at"))
        try:
            return OrderRequest(
                client_order_id=client_order_id,
                symbol=symbol,
                side=side,  # type: ignore[arg-type]
                qty=qty,
                order_type=order_type,  # type: ignore[arg-type]
                limit_price=limit_price,
                tif=tif,  # type: ignore[arg-type]
                strategy=strategy,
                created_at=created_at,
            )
        except (TypeError, ValueError) as exc:
            raise ApiError(400, str(exc), "invalid_order") from exc

    def _literal(self, value: Any, name: str, allowed: tuple[str, ...]) -> str:
        if not isinstance(value, str):
            raise ApiError(400, f"{name} is required", "invalid_order")
        normalized = value.strip().upper()
        if normalized not in allowed:
            raise ApiError(
                400,
                f"{name} must be one of {', '.join(allowed)}",
                "invalid_order",
            )
        return normalized

    def _number(
        self, payload: Mapping[str, Any], name: str, *, required: bool
    ) -> float | None:
        if name not in payload or payload[name] is None:
            if required:
                raise ApiError(400, f"{name} is required", "invalid_order")
            return None
        value = payload[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ApiError(400, f"{name} must be a number", "invalid_order")
        value = float(value)
        if not math.isfinite(value) or value <= 0:
            raise ApiError(400, f"{name} must be positive", "invalid_order")
        return value

    def _created_at(self, value: Any) -> datetime:
        if value is None:
            return utc_now()
        if not isinstance(value, str) or not value.strip():
            raise ApiError(400, "created_at must be an ISO timestamp", "invalid_order")
        try:
            parsed = datetime.fromisoformat(value.strip())
        except ValueError as exc:
            raise ApiError(
                400, "created_at must be an ISO timestamp", "invalid_order"
            ) from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    def _fingerprint(self, request: OrderRequest) -> str:
        payload = {
            "client_order_id": request.client_order_id,
            "symbol": request.symbol,
            "side": request.side,
            "qty": request.qty,
            "order_type": request.order_type,
            "limit_price": request.limit_price,
            "tif": request.tif,
            "strategy": request.strategy,
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    # -- admin ---------------------------------------------------------------

    def _pause(
        self,
        query: Mapping[str, Sequence[str]],
        headers: Mapping[str, str],
        body: bytes,
    ) -> Response:
        return self._admin_action("pause", self._do_pause)

    def _resume(
        self,
        query: Mapping[str, Sequence[str]],
        headers: Mapping[str, str],
        body: bytes,
    ) -> Response:
        return self._admin_action("resume", self._do_resume)

    def _kill(
        self,
        query: Mapping[str, Sequence[str]],
        headers: Mapping[str, str],
        body: bytes,
    ) -> Response:
        return self._admin_action("kill", lambda: self._do_kill(body))

    def _do_pause(self) -> Response:
        status = self._status_or_error()
        if status.state == "KILLED":
            raise ApiError(423, "simulation is killed", "killed")
        if status.state == "PAUSED":
            raise ApiError(409, "simulation is already paused", "conflict")
        self.sim.pause()
        return json_response(self._status_or_error())

    def _do_resume(self) -> Response:
        status = self._status_or_error()
        if status.state == "KILLED":
            raise ApiError(423, "simulation is killed", "killed")
        if status.state != "PAUSED":
            raise ApiError(409, "simulation is not paused", "conflict")
        self.sim.resume()
        return json_response(self._status_or_error())

    def _do_kill(self, body: bytes) -> Response:
        payload = self._json_object(body) if body else {}
        unknown = sorted(set(payload) - {"reason"})
        if unknown:
            raise ApiError(
                400, f"unknown field(s): {', '.join(unknown)}", "invalid_request"
            )
        reason = payload.get("reason", "api kill switch")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 500:
            raise ApiError(400, "reason must be a short string", "invalid_request")
        status = self._status_or_error()
        if status.state == "KILLED":
            raise ApiError(409, "simulation is already killed", "conflict")
        result = self.sim.kill(reason.strip(), "api-admin")
        data = jsonable(result)
        if not isinstance(data, dict):
            data = {"result": data}
        status = self._status_or_error()
        data.setdefault("state", status.state)
        data.setdefault("kill_reason", status.kill_reason)
        return json_response(data)

    def _admin_action(self, action: str, operation: Any) -> Response:
        try:
            response = operation()
        except ApiError as exc:
            outcome = "conflict" if exc.status in (409, 423) else "error"
            self.metrics.admin_action(action, outcome)
            raise
        except Exception:
            self.metrics.admin_action(action, "error")
            raise
        self.metrics.admin_action(action, "ok")
        return response

    # -- metrics -------------------------------------------------------------

    def _metrics(
        self,
        query: Mapping[str, Sequence[str]],
        headers: Mapping[str, str],
        body: bytes,
    ) -> Response:
        extra = ""
        try:
            extra = str(self.sim.metrics() or "")
        except Exception as exc:  # noqa: BLE001
            LOG.warning("simulation metrics unavailable type=%s", type(exc).__name__)
            extra = "# simulation metrics unavailable"
        for secret in {
            self.config.api_token,
            self.config.admin_token,
            self.auth.effective_admin_token,
        }:
            if secret:
                extra = extra.replace(secret, "[redacted]")
        return text_response(self.metrics.render(extra))

    # -- query helpers -------------------------------------------------------

    def _query_one(self, query: Mapping[str, Sequence[str]], name: str) -> str | None:
        values = query.get(name)
        if values is None:
            return None
        if isinstance(values, str):
            values = [values]
        if len(values) != 1:
            raise ApiError(400, f"{name} must be given once", "invalid_query")
        return values[0]

    def _query_int(
        self,
        query: Mapping[str, Sequence[str]],
        name: str,
        *,
        default: int,
        minimum: int,
        maximum: int | None = None,
    ) -> int:
        raw = self._query_one(query, name)
        if raw is None:
            return default
        try:
            value = int(raw)
        except (TypeError, ValueError) as exc:
            raise ApiError(400, f"{name} must be an integer", "invalid_query") from exc
        if value < minimum or (maximum is not None and value > maximum):
            upper = f"..{maximum}" if maximum is not None else "+"
            raise ApiError(
                400,
                f"{name} must be in {minimum}{upper}",
                "invalid_query",
            )
        return value

    def _query_status(self, query: Mapping[str, Sequence[str]]) -> str | None:
        raw = self._query_one(query, "status")
        if raw is None or raw.strip() == "":
            return None
        status = raw.strip().upper()
        if status not in VALID_ORDER_STATUSES:
            raise ApiError(
                400,
                "status must be one of " + ", ".join(sorted(VALID_ORDER_STATUSES)),
                "invalid_query",
            )
        return status

    def _order_json(self, order: OrderState) -> dict[str, Any]:
        data = jsonable(order)
        if not isinstance(data, dict):
            raise TypeError("order serialization did not produce an object")
        return data


# The path id is passed to handlers via a synthetic header so every handler has
# the same (query, headers, body) signature. This is not an HTTP header.
def _attach_order_id(headers: Mapping[str, str], order_id: str) -> dict[str, str]:
    merged = {key: value for key, value in headers.items()}
    merged["X-Prodsim-Order-Id"] = order_id
    return merged


__all__ = [
    "JSON_CONTENT_TYPE",
    "METRICS_CONTENT_TYPE",
    "ApiConfig",
    "ApiError",
    "Response",
    "SimulationAPI",
    "is_loopback_host",
    "json_response",
    "text_response",
]
