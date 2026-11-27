"""HTTP API/auth/metrics tests for the v12 production simulation.

Standalone script (the repository test runner does not use pytest). Starts the
real stdlib server on an ephemeral 127.0.0.1 port and drives it over HTTP with
stdlib clients. No external network, no real broker, no credentials.
"""

from __future__ import annotations

import http.client
import json
import logging
import socket
import sys
import threading
import time
from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot.prodsim.api import ApiConfig
from bot.prodsim.contract import (
    AccountSnapshot,
    OrderRequest,
    OrderState,
    PositionSnapshot,
    SimulationStatus,
    order_event,
    utc_now,
)
from bot.prodsim.server import create_server

TOKEN = "tkn_0123456789abcdef"
ADMIN = "adm_fedcba9876543210"


class FakeSimulation:
    """In-memory SimulationProtocol with controllable failures."""

    def __init__(self) -> None:
        self.state = "RUNNING"
        self.ready = True
        self.fail_health = False
        self.fail_submit: Exception | None = None
        self.delay = 0.0
        self.started_at = utc_now()
        self.kill_reason: str | None = None
        self.submit_calls = 0
        self.cancel_calls = 0
        self.kill_calls = 0
        self.pause_calls = 0
        self.resume_calls = 0
        self.start_calls = 0
        self.stop_calls = 0
        self._orders: OrderedDict[str, OrderState] = OrderedDict()
        self._by_client: dict[str, OrderState] = {}
        self._events: list[dict[str, Any]] = []
        self._next_order = 0
        self.metrics_text = "facade_simulation_ticks_total 42\n"
        self._lock = threading.Lock()

    # SimulationProtocol ---------------------------------------------------

    def start(self) -> None:
        self.start_calls += 1

    def stop(self) -> None:
        self.stop_calls += 1
        self.state = "STOPPED"

    def pause(self) -> None:
        self.pause_calls += 1
        self.state = "PAUSED"

    def resume(self) -> None:
        self.resume_calls += 1
        self.state = "RUNNING"

    def status(self) -> SimulationStatus:
        return SimulationStatus(
            state=self.state,  # type: ignore[arg-type]
            mode="replay",
            clock=utc_now(),
            session="2026-11-25",
            speed=1.0,
            started_at=self.started_at,
            last_tick=None,
            processed_ticks=len(self._events),
            orders=len(self._orders),
            fills=0,
            errors=0,
            kill_reason=self.kill_reason,
        )

    def health(self) -> dict[str, Any]:
        if self.fail_health:
            raise RuntimeError("health backend unavailable")
        return {
            "ready": self.ready,
            "checks": {
                "store": True,
                "clock": self.state != "KILLED",
                "data": self.ready,
            },
            "state": self.state,
        }

    def account(self) -> AccountSnapshot:
        return AccountSnapshot(
            ts=utc_now(),
            currency="USD",
            cash=100_000.0,
            buying_power=100_000.0,
            equity=100_500.0,
            gross_exposure=10_000.0,
            net_exposure=10_000.0,
            margin_used=0.0,
            realized_pnl=250.0,
            unrealized_pnl=250.0,
            fees_paid=5.0,
            positions=self.positions(),
        )

    def positions(self) -> list[PositionSnapshot]:
        return [
            PositionSnapshot(
                symbol="SPY",
                qty=10.0,
                avg_cost=100.0,
                last=101.0,
                market_value=1010.0,
                unrealized_pnl=10.0,
                realized_pnl=0.0,
            )
        ]

    def orders(self, status: str | None = None, limit: int = 100) -> list[OrderState]:
        values = list(self._orders.values())
        if status is not None:
            values = [order for order in values if order.status == status]
        return values[:limit]

    def order(self, order_id: str) -> OrderState | None:
        return self._orders.get(order_id)

    def submit(self, request: OrderRequest) -> OrderState:
        with self._lock:
            self.submit_calls += 1
        if self.fail_submit is not None:
            raise self.fail_submit
        if self.delay:
            time.sleep(self.delay)
        with self._lock:
            existing = self._by_client.get(request.client_order_id)
            if existing is not None:
                return existing
            self._next_order += 1
            rejected = request.symbol == "NOPOWER"
            order = OrderState(
                order_id=f"ord-{self._next_order:04d}",
                client_order_id=request.client_order_id,
                symbol=request.symbol,
                side=request.side,
                qty=request.qty,
                order_type=request.order_type,
                limit_price=request.limit_price,
                tif=request.tif,
                strategy=request.strategy,
                status="REJECTED" if rejected else "NEW",
                reject_reason="insufficient buying power" if rejected else None,
                created_at=request.created_at,
                updated_at=utc_now(),
            )
            self._orders[order.order_id] = order
            self._by_client[order.client_order_id] = order
            self._append_event(order)
            return order

    def cancel(self, order_id: str) -> OrderState:
        self.cancel_calls += 1
        order = self._orders[order_id]
        if not order.is_open:
            raise ValueError("order is not open")
        order.status = "CANCELED"
        order.updated_at = utc_now()
        self._append_event(order)
        return order

    def kill(self, reason: str, actor: str) -> dict[str, Any]:
        self.kill_calls += 1
        self.state = "KILLED"
        self.kill_reason = reason
        canceled = 0
        for order in list(self._orders.values()):
            if order.is_open:
                order.status = "CANCELED"
                order.updated_at = utc_now()
                self._append_event(order)
                canceled += 1
        return {
            "killed": True,
            "canceled": canceled,
            "reason": reason,
            "actor": actor,
        }

    def events(self, since: int = 0, limit: int = 100) -> list[dict[str, Any]]:
        return [event for event in self._events if int(event.get("id", 0)) > since][
            :limit
        ]

    def metrics(self) -> str:
        return self.metrics_text

    # helpers -------------------------------------------------------------

    def _append_event(self, order: OrderState) -> None:
        event = order_event(order)
        event["id"] = len(self._events) + 1
        event["seq"] = event["id"]
        self._events.append(event)


@dataclass
class Reply:
    status: int
    body: bytes
    headers: dict[str, str]
    json: Any


class Client:
    def __init__(self, server: Any) -> None:
        host, port = server.httpd.server_address[:2]
        self.host = str(host)
        self.port = int(port)

    def call(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
        raw: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> Reply:
        payload: bytes | None
        if raw is not None:
            payload = raw
        elif body is not None:
            payload = json.dumps(body).encode("utf-8")
        else:
            payload = None
        request_headers = dict(headers or {})
        if body is not None and "Content-Type" not in request_headers:
            request_headers["Content-Type"] = "application/json"
        connection = http.client.HTTPConnection(self.host, self.port, timeout=5)
        try:
            connection.request(method, path, body=payload, headers=request_headers)
            response = connection.getresponse()
            data = response.read()
            response_headers = {
                key.lower(): value for key, value in response.getheaders()
            }
            status = response.status
        finally:
            connection.close()
        try:
            parsed = json.loads(data) if data else None
        except json.JSONDecodeError:
            parsed = None
        return Reply(status, data, response_headers, parsed)


def bearer(token: str = TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@contextmanager
def running(
    simulation: FakeSimulation | None = None, **overrides: Any
) -> Iterator[tuple[Client, FakeSimulation, Any]]:
    sim = simulation or FakeSimulation()
    options: dict[str, Any] = {
        "host": "127.0.0.1",
        "port": 0,
        "api_token": TOKEN,
        "admin_token": ADMIN,
    }
    options.update(overrides)
    config = ApiConfig(**options)
    server = create_server(sim, config)
    thread = server.start_background()
    assert server.wait_started(2.0), "server did not start"
    try:
        yield Client(server), sim, server
    finally:
        server.shutdown()
        thread.join(timeout=3.0)
        assert not thread.is_alive(), "server thread did not stop"


def expect(reply: Reply, status: int, code: str | None = None) -> Reply:
    assert reply.status == status, (reply.status, reply.body)
    if code is not None:
        assert reply.json is not None, reply.body
        assert reply.json.get("code") == code, reply.json
    return reply


def submit(
    client: Client,
    key: str,
    *,
    token: str = TOKEN,
    **fields: Any,
) -> Reply:
    payload = {"symbol": "SPY", "side": "BUY", "qty": 10.0}
    payload.update(fields)
    headers = bearer(token)
    headers["Idempotency-Key"] = key
    return client.call("POST", "/api/v1/orders", body=payload, headers=headers)


def test_health_auth_and_public_endpoints() -> None:
    with running() as (client, _, _):
        health = expect(client.call("GET", "/healthz"), 200)
        assert health.json["status"] == "ok"
        assert health.json["component"] == "prodsim-api"

        ready = expect(client.call("GET", "/readyz"), 200)
        assert ready.json["ready"] is True

        expect(client.call("GET", "/api/v1/status"), 401, "unauthorized")
        unauthorized = client.call("GET", "/api/v1/status")
        assert unauthorized.headers["www-authenticate"].startswith("Bearer")

        expect(
            client.call("GET", "/api/v1/status", headers=bearer("wrong-token")),
            401,
            "unauthorized",
        )
        expect(
            client.call(
                "GET",
                "/api/v1/status",
                headers={"Authorization": "Basic dXNlcjpwYXNz"},
            ),
            401,
            "unauthorized",
        )
        status = expect(client.call("GET", "/api/v1/status", headers=bearer()), 200)
        assert status.json["state"] == "RUNNING"

        metrics = expect(client.call("GET", "/metrics"), 200)
        text = metrics.body.decode()
        assert metrics.headers["content-type"].startswith("text/plain")
        assert TOKEN not in text and ADMIN not in text
        assert "prodsim_api_auth_failures_total" in text


def test_read_endpoints_filters_and_events() -> None:
    with running() as (client, _, _):
        account = expect(client.call("GET", "/api/v1/account", headers=bearer()), 200)
        assert account.json["cash"] == 100_000.0
        assert account.json["currency"] == "USD"

        positions = expect(
            client.call("GET", "/api/v1/positions", headers=bearer()), 200
        )
        assert positions.json["count"] == 1
        assert positions.json["positions"][0]["symbol"] == "SPY"

        first = expect(submit(client, "read-a"), 201)
        second = expect(submit(client, "read-b", symbol="QQQ", qty=5.0), 201)
        assert first.json["order_id"] != second.json["order_id"]

        listed = expect(
            client.call("GET", "/api/v1/orders?status=new&limit=1", headers=bearer()),
            200,
        )
        assert listed.json["count"] == 1
        assert listed.json["status"] == "NEW"
        assert listed.json["limit"] == 1

        fetched = expect(
            client.call(
                "GET",
                f"/api/v1/orders/{first.json['order_id']}",
                headers=bearer(),
            ),
            200,
        )
        assert fetched.json["order_id"] == first.json["order_id"]

        expect(
            client.call("GET", "/api/v1/orders/missing", headers=bearer()),
            404,
            "not_found",
        )
        expect(
            client.call("GET", "/api/v1/orders?status=BOGUS", headers=bearer()),
            400,
            "invalid_query",
        )
        expect(
            client.call("GET", "/api/v1/orders?limit=0", headers=bearer()),
            400,
            "invalid_query",
        )

        events = expect(
            client.call("GET", "/api/v1/events?since=1&limit=1", headers=bearer()),
            200,
        )
        assert events.json["count"] == 1
        assert events.json["events"][0]["id"] == 2
        assert events.json["next"] >= 3
        expect(
            client.call("GET", "/api/v1/events?since=-1", headers=bearer()),
            400,
            "invalid_query",
        )


def test_submit_validation_and_idempotency() -> None:
    with running() as (client, sim, _):
        headers = bearer()
        expect(
            client.call(
                "POST",
                "/api/v1/orders",
                body={"symbol": "SPY", "side": "BUY", "qty": 1},
                headers=headers,
            ),
            400,
            "missing_idempotency_key",
        )
        bad = dict(headers)
        bad["Idempotency-Key"] = "bad-json"
        expect(
            client.call("POST", "/api/v1/orders", raw=b"{", headers=bad),
            400,
            "invalid_body",
        )
        expect(
            client.call("POST", "/api/v1/orders", raw=b"[]", headers=bad),
            400,
            "invalid_body",
        )

        expect(
            submit(client, "bad-field", unexpected=1),
            400,
            "invalid_order",
        )
        expect(submit(client, "bad-qty", qty=0), 400, "invalid_order")
        expect(submit(client, "bad-bool", qty=True), 400, "invalid_order")
        expect(
            submit(client, "bad-limit", order_type="LIMIT"),
            400,
            "invalid_order",
        )
        expect(
            submit(
                client,
                "bad-side",
                side="HOLD",
            ),
            400,
            "invalid_order",
        )
        assert sim.submit_calls == 0

        created = expect(submit(client, "idem-1", qty=10), 201)
        assert created.json["client_order_id"] == "idem-1"
        assert created.json["status"] == "NEW"
        order_id = created.json["order_id"]
        assert created.headers["location"] == f"/api/v1/orders/{order_id}"
        assert sim.submit_calls == 1

        replayed = expect(submit(client, "idem-1", qty=10), 200)
        assert replayed.json["order_id"] == order_id
        assert replayed.headers["idempotency-replayed"] == "true"
        assert sim.submit_calls == 1

        conflict = expect(submit(client, "idem-1", qty=11), 409)
        assert conflict.json["code"] == "idempotency_conflict"
        assert sim.submit_calls == 1

        explicit = expect(submit(client, "idem-2", client_order_id="client-abc"), 201)
        assert explicit.json["client_order_id"] == "client-abc"

        limit = expect(
            submit(
                client,
                "idem-3",
                order_type="LIMIT",
                limit_price=99.5,
                tif="GTC",
            ),
            201,
        )
        assert limit.json["limit_price"] == 99.5
        assert limit.json["tif"] == "GTC"

        rejected = expect(submit(client, "idem-4", symbol="NOPOWER"), 201)
        assert rejected.json["status"] == "REJECTED"
        assert rejected.json["reject_reason"]
        assert (
            expect(submit(client, "idem-4", symbol="NOPOWER"), 200).json["order_id"]
            == rejected.json["order_id"]
        )


def test_concurrent_idempotency_submits_once() -> None:
    sim = FakeSimulation()
    sim.delay = 0.25
    with running(sim) as (client, sim, _):
        results: list[Reply] = []
        lock = threading.Lock()

        def call() -> None:
            reply = submit(client, "race-key", qty=10)
            with lock:
                results.append(reply)

        threads = [threading.Thread(target=call) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
        assert len(results) == 2
        assert sorted(reply.status for reply in results) == [200, 201]
        assert len({reply.json["order_id"] for reply in results}) == 1
        assert sim.submit_calls == 1


def test_cancel_pause_resume_and_kill_switch() -> None:
    with running() as (client, sim, _):
        first = expect(submit(client, "cancel-a"), 201)
        second = expect(submit(client, "cancel-b"), 201)

        canceled = expect(
            client.call(
                "DELETE",
                f"/api/v1/orders/{first.json['order_id']}",
                headers=bearer(),
            ),
            200,
        )
        assert canceled.json["status"] == "CANCELED"
        expect(
            client.call(
                "DELETE",
                f"/api/v1/orders/{first.json['order_id']}",
                headers=bearer(),
            ),
            409,
            "order_not_open",
        )
        expect(
            client.call("DELETE", "/api/v1/orders/missing", headers=bearer()),
            404,
            "not_found",
        )

        expect(
            client.call("POST", "/api/v1/admin/kill", headers=bearer()),
            401,
            "unauthorized",
        )
        assert sim.state == "RUNNING"

        killed = expect(
            client.call(
                "POST",
                "/api/v1/admin/kill",
                body={"reason": "test kill"},
                headers=bearer(ADMIN),
            ),
            200,
        )
        assert killed.json["killed"] is True
        assert killed.json["state"] == "KILLED"
        assert killed.json["canceled"] >= 1
        assert sim.kill_calls == 1
        assert sim.kill_reason == "test kill"

        before = sim.submit_calls
        expect(submit(client, "after-kill"), 423, "killed")
        assert sim.submit_calls == before
        expect(
            client.call(
                "DELETE",
                f"/api/v1/orders/{second.json['order_id']}",
                headers=bearer(),
            ),
            423,
            "killed",
        )
        expect(client.call("GET", "/healthz"), 200)
        expect(client.call("GET", "/readyz"), 503)
        expect(
            client.call("POST", "/api/v1/admin/pause", headers=bearer(ADMIN)),
            423,
            "killed",
        )
        expect(
            client.call("POST", "/api/v1/admin/resume", headers=bearer(ADMIN)),
            423,
            "killed",
        )
        expect(
            client.call("POST", "/api/v1/admin/kill", headers=bearer(ADMIN)),
            409,
            "conflict",
        )


def test_pause_resume_and_not_ready() -> None:
    with running() as (client, sim, _):
        order = expect(submit(client, "pause-order"), 201)
        paused = expect(
            client.call("POST", "/api/v1/admin/pause", headers=bearer(ADMIN)),
            200,
        )
        assert paused.json["state"] == "PAUSED"
        assert sim.pause_calls == 1
        expect(
            client.call("POST", "/api/v1/admin/pause", headers=bearer(ADMIN)),
            409,
            "conflict",
        )
        before = sim.submit_calls
        expect(submit(client, "paused-submit"), 423, "paused")
        assert sim.submit_calls == before
        expect(
            client.call(
                "DELETE",
                f"/api/v1/orders/{order.json['order_id']}",
                headers=bearer(),
            ),
            423,
            "paused",
        )
        resumed = expect(
            client.call("POST", "/api/v1/admin/resume", headers=bearer(ADMIN)),
            200,
        )
        assert resumed.json["state"] == "RUNNING"
        assert sim.resume_calls == 1
        expect(
            client.call("POST", "/api/v1/admin/resume", headers=bearer(ADMIN)),
            409,
            "conflict",
        )

        sim.ready = False
        not_ready = expect(client.call("GET", "/readyz"), 503)
        assert not_ready.json["ready"] is False
        expect(submit(client, "not-ready"), 503, "not_ready")
        expect(
            client.call(
                "DELETE",
                f"/api/v1/orders/{order.json['order_id']}",
                headers=bearer(),
            ),
            503,
            "not_ready",
        )
        expect(client.call("GET", "/api/v1/account", headers=bearer()), 200)

        sim.ready = True
        sim.fail_health = True
        expect(client.call("GET", "/readyz"), 503)
        expect(submit(client, "health-down"), 503, "not_ready")
        sim.fail_health = False

        sim.state = "STOPPED"
        assert (
            expect(client.call("GET", "/api/v1/status", headers=bearer()), 200).json[
                "state"
            ]
            == "STOPPED"
        )
        expect(submit(client, "stopped"), 503, "not_ready")


def test_unexpected_error_is_generic_and_key_recovers() -> None:
    with running() as (client, sim, _):
        sim.fail_submit = RuntimeError(f"broker exploded token={TOKEN}")
        error = expect(submit(client, "error-key"), 500, "internal_error")
        assert TOKEN.encode() not in error.body
        assert b"broker exploded" not in error.body
        assert b"RuntimeError" not in error.body

        sim.fail_submit = None
        recovered = expect(submit(client, "error-key"), 201)
        assert recovered.json["status"] == "NEW"
        replayed = expect(submit(client, "error-key"), 200)
        assert replayed.json["order_id"] == recovered.json["order_id"]


def test_body_limit_methods_and_metrics_format() -> None:
    with running() as (client, _, _):
        huge = expect(
            submit(client, "huge", strategy="x" * 70_000),
            413,
            "payload_too_large",
        )
        assert huge.json["max_bytes"] == 64 * 1024

        expect(
            client.call("PUT", "/api/v1/status", headers=bearer()),
            405,
            "method_not_allowed",
        )
        expect(
            client.call("GET", "/api/v1/unknown", headers=bearer()),
            404,
            "not_found",
        )
        expect(
            client.call("GET", "/api/v1/orders/", headers=bearer()),
            404,
            "not_found",
        )

        head = client.call("HEAD", "/healthz")
        assert head.status == 200
        assert head.body == b""
        assert int(head.headers["content-length"]) > 0

        expect(submit(client, "metric-order"), 201)
        expect(client.call("GET", "/api/v1/status"), 401)
        text = expect(client.call("GET", "/metrics"), 200).body.decode()
        assert "# TYPE prodsim_api_http_requests_total counter" in text
        assert "# TYPE prodsim_api_http_request_duration_seconds histogram" in text
        assert "prodsim_api_http_request_duration_seconds_bucket{" in text
        assert "prodsim_api_http_request_duration_seconds_sum{" in text
        assert "prodsim_api_http_request_duration_seconds_count{" in text
        assert "prodsim_api_orders_total{" in text
        assert "prodsim_api_http_in_flight 1" in text
        assert "facade_simulation_ticks_total 42" in text
        assert TOKEN not in text and ADMIN not in text
        for line in text.splitlines():
            if not line or line.startswith("#"):
                continue
            name, value = line.rsplit(" ", 1)
            assert name
            float(value)


def test_dev_mode_auth_and_config_guards() -> None:
    records: list[logging.LogRecord] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    logger = logging.getLogger("bot.prodsim.auth")
    handler = Capture()
    old_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        config = ApiConfig(api_token="", admin_token="", dev=True, port=0)
        assert config.dev
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)
    messages = [record.getMessage() for record in records]
    assert any("authentication is disabled" in message for message in messages)
    assert all(TOKEN not in message and ADMIN not in message for message in messages)

    try:
        ApiConfig(api_token="", dev=False, port=0)
    except ValueError:
        pass
    else:
        raise AssertionError("empty token outside dev must fail closed")

    try:
        ApiConfig(api_token=TOKEN, host="0.0.0.0", port=0)
    except ValueError:
        pass
    else:
        raise AssertionError("remote bind must require allow_remote")
    ApiConfig(api_token=TOKEN, host="0.0.0.0", port=0, allow_remote=True)

    hidden = ApiConfig(api_token=TOKEN, admin_token=ADMIN, port=0)
    assert TOKEN not in repr(hidden)
    assert ADMIN not in repr(hidden)

    with running(api_token="", admin_token="", dev=True) as (client, sim, _):
        assert (
            expect(client.call("GET", "/api/v1/status"), 200).json["state"] == "RUNNING"
        )
        assert (
            expect(client.call("POST", "/api/v1/admin/pause"), 200).json["state"]
            == "PAUSED"
        )
        assert sim.pause_calls == 1


def test_tokens_never_logged_or_exposed_in_metrics() -> None:
    records: list[logging.LogRecord] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    logger = logging.getLogger("bot.prodsim")
    handler = Capture()
    logger.addHandler(handler)
    try:
        with running() as (client, sim, _):
            sim.metrics_text = f'leaked_token{{value="{TOKEN}"}} 1\n'
            text = expect(client.call("GET", "/metrics"), 200).body.decode()
            assert TOKEN not in text
            assert ADMIN not in text
            assert "[redacted]" in text

            sim.fail_submit = RuntimeError(
                f"broker exploded token={TOKEN} admin={ADMIN}"
            )
            expect(submit(client, "log-key"), 500, "internal_error")
            sim.fail_submit = None

            sim.fail_health = True
            expect(client.call("GET", "/readyz"), 503)
            sim.fail_health = False
    finally:
        logger.removeHandler(handler)

    assert records, "expected at least one prodsim log record"
    formatter = logging.Formatter("%(message)s")
    for record in records:
        rendered = formatter.format(record)
        assert TOKEN not in rendered
        assert ADMIN not in rendered


def test_request_timeout_closes_slow_client() -> None:
    with running(request_timeout=0.2) as (client, _, _):
        sock = socket.create_connection((client.host, client.port), timeout=2.0)
        try:
            sock.sendall(b"GET /healthz HTTP/1.1\r\nHost: localhost\r\n")
            time.sleep(0.45)
            data = sock.recv(4096)
        finally:
            sock.close()
        # ``http.server`` closes an idle connection on timeout; an explicit
        # 408 is also acceptable. The guarantee is a bounded read, not a hang.
        assert data == b"" or b"408" in data, data

        # A timeout while reading a declared body is mapped to JSON 408.
        sock = socket.create_connection((client.host, client.port), timeout=2.0)
        try:
            sock.sendall(
                b"POST /api/v1/orders HTTP/1.1\r\n"
                b"Host: localhost\r\n"
                b"Content-Length: 10\r\n\r\n{"
            )
            time.sleep(0.45)
            data = sock.recv(4096)
        finally:
            sock.close()
        assert b"408" in data, data


def test_graceful_shutdown_closes_listener() -> None:
    sim = FakeSimulation()
    config = ApiConfig(
        host="127.0.0.1",
        port=0,
        api_token=TOKEN,
        admin_token=ADMIN,
        request_timeout=2.0,
    )
    server = create_server(sim, config)
    thread = server.start_background()
    assert server.wait_started(2.0)
    port = int(server.httpd.server_address[1])
    assert server.base_url.startswith("http://127.0.0.1:")
    server.shutdown()
    thread.join(timeout=3.0)
    assert not thread.is_alive()
    assert server.wait_closed(1.0)
    server.shutdown()  # idempotent

    try:
        sock = socket.create_connection(("127.0.0.1", port), timeout=1.0)
    except OSError:
        pass
    else:
        sock.close()
        raise AssertionError("listener still accepts connections after shutdown")


def main() -> None:
    tests = [
        value for name, value in sorted(globals().items()) if name.startswith("test_")
    ]
    for test in tests:
        test()
        print(f"ok: {test.__name__}")
    print(f"ok: {len(tests)} prodsim API tests")


if __name__ == "__main__":
    main()
