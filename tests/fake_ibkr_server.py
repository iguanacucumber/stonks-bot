"""Local fake IBKR Client Portal server for tests (stdlib only).

It listens on 127.0.0.1:0 and speaks just enough of the Client Portal Web API for
the adapter's read-only surface. There is no external network, no credentials and
no real order path. POST/PUT/DELETE are recorded and rejected with 405 so a test
can prove zero writes.

Usage::

    with fake_server() as srv:
        cfg = IBKRPaperConfig(account_id=srv.account,
                              base_url=srv.base_url,
                              verify_ssl=False)
        broker = IBKRPaperBroker(ClientPortalTransport(cfg), cfg)
        snap = broker.read_snapshot()
"""

from __future__ import annotations

from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time
from typing import Any
from urllib.parse import parse_qs, urlparse


@dataclass
class FakeIBKRState:
    account: str = "DU1234567"
    authenticated: bool = True
    summary: dict[str, Any] = field(
        default_factory=lambda: {
            "accountcode": "DU1234567",
            "netliquidation": 10_000.0,
            "currency": "USD",
        }
    )
    positions: list[dict[str, Any]] = field(default_factory=list)
    cash: float = 10_000.0
    orders: list[dict[str, Any]] = field(default_factory=list)
    executions: list[dict[str, Any]] = field(default_factory=list)
    contracts: dict[int, dict[str, Any]] = field(default_factory=dict)
    conids: dict[str, int] = field(default_factory=dict)
    delay: float = 0.0
    fail_times: int = 0
    fail_status: int = 500
    malformed: bool = False
    write_calls: list[tuple[str, str]] = field(default_factory=list)
    requests: list[tuple[str, str]] = field(default_factory=list)
    _next_conid: int = 1000

    def conid(self, symbol: str) -> int:
        key = symbol.upper()
        if key not in self.conids:
            self.conids[key] = self._next_conid
            self.contracts[self._next_conid] = {
                "conid": self._next_conid,
                "symbol": key,
                "secType": "STK",
                "exchange": "SMART",
                "currency": "USD",
            }
            self._next_conid += 1
        return self.conids[key]

    def route(
        self, method: str, path: str, query: dict[str, list[str]]
    ) -> tuple[int, Any]:
        if method != "GET":
            self.write_calls.append((method, path))
            return 405, {"error": "read-only fake server"}
        if self.fail_times > 0:
            self.fail_times -= 1
            return self.fail_status, {"error": "simulated upstream failure"}
        if path == "/v1/api/iserver/auth/status":
            return 200, {"authenticated": self.authenticated, "competing": False}
        if path == "/v1/api/iserver/accounts":
            return 200, {"accounts": [self.account] if self.authenticated else []}
        if path.startswith("/v1/api/portfolio/") and path.endswith("/summary"):
            return 200, dict(self.summary)
        if path.startswith("/v1/api/portfolio/") and path.endswith("/positions"):
            return 200, list(self.positions)
        if path.startswith("/v1/api/portfolio/") and path.endswith("/ledger"):
            return 200, {
                "USD": {"cashbalance": float(self.cash)},
                "BASE": {"cashbalance": float(self.cash)},
            }
        if path == "/v1/api/iserver/account/orders":
            return 200, {"orders": list(self.orders)}
        if path == "/v1/api/iserver/account/trades":
            return 200, {"trades": list(self.executions)}
        if path == "/v1/api/iserver/secdef/search":
            symbol = (query.get("symbol") or [""])[0]
            conid = self.conid(symbol) if symbol else 1000
            return 200, [{"conid": conid, "symbol": symbol.upper()}]
        if path.startswith("/v1/api/iserver/contract/") and path.endswith("/info"):
            conid = int(path.split("/")[-2])
            return 200, dict(self.contracts.get(conid, {"conid": conid}))
        return 404, {"error": f"unknown path {path}"}


class _Handler(BaseHTTPRequestHandler):
    state: FakeIBKRState

    def log_message(self, fmt: str, *args: Any) -> None:  # silence tests
        return

    def _handle(self, method: str) -> None:
        parsed = urlparse(self.path)
        self.state.requests.append((method, parsed.path))
        if self.state.delay > 0:
            time.sleep(self.state.delay)
        query = parse_qs(parsed.query)
        status, payload = self.state.route(method, parsed.path, query)
        body = "not-json" if self.state.malformed else json.dumps(payload)
        raw = body.encode()
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError):
            # The client timed out on purpose in the timeout drill.
            pass

    def do_GET(self) -> None:  # noqa: N802
        self._handle("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._handle("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._handle("PUT")

    def do_DELETE(self) -> None:  # noqa: N802
        self._handle("DELETE")


@dataclass
class FakeServer:
    state: FakeIBKRState
    base_url: str
    account: str
    _httpd: ThreadingHTTPServer
    _thread: threading.Thread

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)


class _ServerContext:
    def __init__(self, state: FakeIBKRState | None = None):
        self.state = state or FakeIBKRState()
        self.server: FakeServer | None = None

    def __enter__(self) -> FakeServer:
        state = self.state
        handler = type("_BoundHandler", (_Handler,), {"state": state})
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        host, port = httpd.server_address[:2]
        self.server = FakeServer(
            state=state,
            base_url=f"http://{host}:{port}/v1/api",
            account=state.account,
            _httpd=httpd,
            _thread=thread,
        )
        return self.server

    def __exit__(self, *exc: Any) -> None:
        if self.server is not None:
            self.server.close()
            self.server = None


def fake_server(state: FakeIBKRState | None = None) -> _ServerContext:
    return _ServerContext(state)
