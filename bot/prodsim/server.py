"""Stdlib HTTP server for the v12 production simulation API.

The listener is loopback-only by default, has a bounded request body, a socket
timeout and a graceful shutdown path. It never logs request headers, bodies or
bearer tokens.

The simulation lifecycle is owned by the caller (the v12 ops CLI); this module
only transports HTTP. ``main`` accepts a ``module:callable`` factory so the
same server can be run without importing ops code from the API branch.
"""

from __future__ import annotations

import argparse
import importlib
import logging
import os
import signal
import socket
import sys
import threading
import time
import uuid
from collections.abc import Sequence
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Self
from urllib.parse import parse_qs, urlsplit

from .api import (
    ApiConfig,
    Response,
    SimulationAPI,
    json_response,
)
from .contract import SimulationProtocol

LOG = logging.getLogger("bot.prodsim.server")

FACTORY_ENV = "STONKS_PRODSIM_FACTORY"
LOG_LEVEL_ENV = "STONKS_PRODSIM_LOG_LEVEL"


class ProdsimHTTPServer(ThreadingHTTPServer):
    """Threaded HTTP server carrying the API facade."""

    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 64

    def __init__(
        self,
        address: tuple[str, int],
        handler: type[BaseHTTPRequestHandler],
        api: SimulationAPI,
        config: ApiConfig,
    ) -> None:
        super().__init__(address, handler)
        self.api = api
        self.config = config

    def handle_error(self, request: Any, client_address: Any) -> None:
        # socketserver prints a traceback by default; keep logs free of
        # request data and exception text that could contain secrets.
        error = sys.exc_info()[1]
        LOG.error(
            "unhandled request error client=%s type=%s",
            client_address[0] if client_address else "unknown",
            type(error).__name__ if error is not None else "unknown",
        )


class ProdsimRequestHandler(BaseHTTPRequestHandler):
    """One request per call, bounded body and no header/token logging."""

    protocol_version = "HTTP/1.1"
    server_version = "stonks-prodsim/0.1"
    sys_version = ""

    def setup(self) -> None:
        super().setup()
        self.request_id = uuid.uuid4().hex
        timeout = float(self.server.config.request_timeout)
        self.connection.settimeout(timeout)

    # BaseHTTPRequestHandler logs to stderr with the raw request line; keep it
    # quiet and emit a structured line without headers/body below instead.
    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
        return

    def do_GET(self) -> None:
        self._handle()

    def do_HEAD(self) -> None:
        self._handle()

    def do_POST(self) -> None:
        self._handle()

    def do_DELETE(self) -> None:
        self._handle()

    def do_PUT(self) -> None:
        self._handle()

    def do_PATCH(self) -> None:
        self._handle()

    def handle_one_request(self) -> None:
        try:
            super().handle_one_request()
        except TimeoutError:
            self.close_connection = True
            LOG.warning("request header timeout rid=%s", self.request_id)
            self._send(
                json_response(
                    {"error": "request timeout", "code": "timeout"},
                    status=408,
                    headers=(("Connection", "close"),),
                )
            )
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True

    def _handle(self) -> None:
        started = time.monotonic()
        self.request_id = uuid.uuid4().hex
        method = self.command.upper()
        parsed = urlsplit(self.path)
        path = parsed.path or "/"
        query = parse_qs(parsed.query, keep_blank_values=True)
        status = 500
        response: Response | None = None
        api_handled = False
        try:
            body, rejection = self._read_body()
            if rejection is not None:
                response = rejection
            else:
                response = self.server.api.handle(
                    method,
                    path,
                    query=query,
                    headers=self.headers,
                    body=body,
                    request_id=self.request_id,
                )
                api_handled = True
        except TimeoutError:
            LOG.warning(
                "request timeout method=%s path=%s rid=%s",
                method,
                path,
                self.request_id,
            )
            self.close_connection = True
            response = json_response(
                {"error": "request timeout", "code": "timeout"},
                status=408,
                headers=(("Connection", "close"),),
            )
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as exc:  # noqa: BLE001
            LOG.error(
                "request handler failed method=%s path=%s type=%s rid=%s",
                method,
                path,
                type(exc).__name__,
                self.request_id,
            )
            self.close_connection = True
            response = json_response(
                {"error": "internal server error", "code": "internal_error"},
                status=500,
                headers=(("Connection", "close"),),
            )
        if response is None:
            response = json_response(
                {"error": "internal server error", "code": "internal_error"},
                status=500,
            )
        status = response.status
        if not api_handled and status >= 400:
            # API-level metrics only see requests that reach the API layer.
            self.server.api.metrics.record_http(
                method, "request", status, time.monotonic() - started
            )
        self._send(response)
        LOG.info(
            "%s %s -> %s %.1fms rid=%s",
            method,
            path,
            status,
            (time.monotonic() - started) * 1000.0,
            self.request_id,
        )

    def _read_body(self) -> tuple[bytes, Response | None]:
        transfer = self.headers.get("Transfer-Encoding")
        if transfer:
            self.close_connection = True
            return b"", json_response(
                {
                    "error": "Transfer-Encoding is not supported",
                    "code": "length_required",
                },
                status=411,
                headers=(("Connection", "close"),),
            )
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            return b"", None
        try:
            length = int(raw_length)
        except (TypeError, ValueError):
            self.close_connection = True
            return b"", json_response(
                {"error": "invalid Content-Length", "code": "invalid_request"},
                status=400,
                headers=(("Connection", "close"),),
            )
        if length < 0:
            self.close_connection = True
            return b"", json_response(
                {"error": "invalid Content-Length", "code": "invalid_request"},
                status=400,
                headers=(("Connection", "close"),),
            )
        if length > self.server.config.max_body_bytes:
            self.close_connection = True
            return b"", json_response(
                {
                    "error": "request body too large",
                    "code": "payload_too_large",
                    "max_bytes": self.server.config.max_body_bytes,
                },
                status=413,
                headers=(("Connection", "close"),),
            )
        if length == 0:
            return b"", None
        body = self.rfile.read(length)
        if len(body) != length:
            self.close_connection = True
            return b"", json_response(
                {"error": "incomplete request body", "code": "invalid_request"},
                status=400,
                headers=(("Connection", "close"),),
            )
        return body, None

    def _send(self, response: Response) -> None:
        try:
            self.send_response(response.status)
            self.send_header("Content-Type", response.content_type)
            self.send_header("Content-Length", str(len(response.body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Request-ID", self.request_id)
            for name, value in response.headers:
                self.send_header(name, value)
            if self.close_connection and not any(
                name.lower() == "connection" for name, _ in response.headers
            ):
                self.send_header("Connection", "close")
            self.end_headers()
            if getattr(self, "command", None) != "HEAD":
                self.wfile.write(response.body)
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            self.close_connection = True


class ProdsimServer:
    """Lifecycle wrapper around the threaded HTTP server."""

    def __init__(
        self, httpd: ProdsimHTTPServer, api: SimulationAPI, config: ApiConfig
    ) -> None:
        self.httpd = httpd
        self.api = api
        self.config = config
        self._started = threading.Event()
        self._closed = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    @property
    def base_url(self) -> str:
        host, port = self.httpd.server_address[:2]
        host = str(host)
        if host in {"0.0.0.0", ""}:
            host = "127.0.0.1"
        elif host in {"::", "::0"}:
            host = "::1"
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        return f"http://{host}:{port}"

    def wait_started(self, timeout: float | None = None) -> bool:
        return self._started.wait(timeout=timeout)

    def serve_forever(self, poll_interval: float = 0.25) -> None:
        self._started.set()
        try:
            self.httpd.serve_forever(poll_interval=poll_interval)
        finally:
            try:
                self.httpd.server_close()
            finally:
                self._closed.set()

    def start_background(self, poll_interval: float = 0.25) -> threading.Thread:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise RuntimeError("server is already running")
            thread = threading.Thread(
                target=self.serve_forever,
                kwargs={"poll_interval": poll_interval},
                name="prodsim-http",
                daemon=True,
            )
            self._thread = thread
            self._started.set()
            thread.start()
            return thread

    def shutdown(self, timeout: float = 5.0) -> None:
        """Stop accepting connections and wait for the serve loop to close."""

        if self._closed.is_set():
            return
        if not self._started.is_set():
            self._closed.set()
            self.httpd.server_close()
            return
        self.httpd.shutdown()
        self._closed.wait(timeout=timeout)

    def wait_closed(self, timeout: float | None = None) -> bool:
        return self._closed.wait(timeout=timeout)

    def __enter__(self) -> Self:
        self.start_background()
        return self

    def __exit__(self, *exc: object) -> None:
        self.shutdown()


def create_server(
    simulation: SimulationProtocol, config: ApiConfig | None = None
) -> ProdsimServer:
    """Bind the API to ``config.host:config.port`` (port 0 is ephemeral)."""

    config = config or ApiConfig.from_env()
    api = SimulationAPI(simulation, config)
    handler = type("_BoundProdsimRequestHandler", (ProdsimRequestHandler,), {})
    server_class: type[ProdsimHTTPServer] = ProdsimHTTPServer
    if ":" in config.host:
        server_class = type(
            "_V6ProdsimHTTPServer",
            (ProdsimHTTPServer,),
            {"address_family": socket.AF_INET6},
        )
    address: tuple[str, int] = (config.host, int(config.port))
    httpd = server_class(address, handler, api, config)
    server = ProdsimServer(httpd, api, config)
    LOG.info("prodsim API bound to %s", server.base_url)
    return server


def serve(
    simulation: SimulationProtocol,
    config: ApiConfig | None = None,
    *,
    start_simulation: bool = False,
    stop_simulation: bool = False,
    install_signal_handlers: bool = True,
    poll_interval: float = 0.25,
) -> int:
    """Run until SIGINT/SIGTERM, then shut the listener down gracefully."""

    server = create_server(simulation, config)
    if start_simulation:
        try:
            simulation.start()
        except Exception:
            server.shutdown()
            raise

    previous: dict[int, Any] = {}
    stopping = threading.Event()

    def request_stop(signum: int, frame: Any) -> None:
        if stopping.is_set():
            return
        stopping.set()
        LOG.info("received signal %s; shutting down", signum)
        threading.Thread(
            target=server.shutdown,
            name="prodsim-shutdown",
            daemon=True,
        ).start()

    if (
        install_signal_handlers
        and threading.current_thread() is threading.main_thread()
    ):
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                previous[sig] = signal.signal(sig, request_stop)
            except (ValueError, OSError):  # not the main thread / restricted env
                pass

    LOG.info("prodsim API listening on %s", server.base_url)
    try:
        server.serve_forever(poll_interval=poll_interval)
    except KeyboardInterrupt:
        LOG.info("keyboard interrupt; shutting down")
    finally:
        server.shutdown()
        for sig, handler in previous.items():
            try:
                signal.signal(sig, handler)
            except (ValueError, OSError):
                pass
        if stop_simulation:
            try:
                simulation.stop()
            except Exception as exc:  # noqa: BLE001
                LOG.error("simulation stop failed type=%s", type(exc).__name__)
    return 0


def _load_factory(spec: str) -> Any:
    module_name, separator, attribute = spec.partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError("factory must be 'module:callable'")
    module = importlib.import_module(module_name)
    factory = getattr(module, attribute)
    if not callable(factory):
        raise TypeError(f"factory {spec!r} is not callable")
    return factory


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m bot.prodsim.server",
        description=(
            "Serve the v12 production-simulation HTTP API. Simulation only; "
            "loopback by default and no real broker."
        ),
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument(
        "--dev",
        action="store_true",
        help="allow an empty bearer token; logs a warning (never expose)",
    )
    parser.add_argument(
        "--allow-remote",
        action="store_true",
        help="allow a non-loopback bind (only on an isolated host)",
    )
    parser.add_argument(
        "--factory",
        default=os.environ.get(FACTORY_ENV, ""),
        help=(
            "simulation factory as 'module:callable' (no arguments); "
            f"defaults to ${FACTORY_ENV}"
        ),
    )
    parser.add_argument(
        "--no-start",
        action="store_true",
        help="do not call simulation.start() before serving",
    )
    parser.add_argument(
        "--no-stop",
        action="store_true",
        help="do not call simulation.stop() on shutdown",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=os.environ.get(LOG_LEVEL_ENV, "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    if not args.factory:
        parser.error(
            "--factory is required: simulation construction belongs to the "
            "v12 ops CLI (or set " + FACTORY_ENV + ")"
        )
    try:
        config = ApiConfig.from_env(
            host=args.host,
            port=args.port,
            dev=args.dev,
            allow_remote=args.allow_remote,
        )
        factory = _load_factory(args.factory)
        simulation = factory()
        return serve(
            simulation,
            config,
            start_simulation=not args.no_start,
            stop_simulation=not args.no_stop,
        )
    except (ValueError, OSError) as exc:
        LOG.error("server configuration/startup failed type=%s", type(exc).__name__)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ProdsimHTTPServer",
    "ProdsimRequestHandler",
    "ProdsimServer",
    "create_server",
    "main",
    "serve",
]
