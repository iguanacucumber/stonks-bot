"""Small stdlib Prometheus registry for the v12 simulation API.

Only aggregate counters, gauges and histograms are stored. Labels are chosen by
the server (HTTP method, normalized route, status code); request headers, query
strings and tokens are never used as labels.
"""

from __future__ import annotations

import math
import re
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field

BUCKETS: tuple[float, ...] = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
)

_NAME_RE = re.compile(r"^[a-zA-Z_:][a-zA-Z0-9_:]*$")
_LABEL_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")

_HELP: dict[str, str] = {
    "prodsim_api_http_requests_total": "HTTP requests handled, by route and status.",
    "prodsim_api_http_request_duration_seconds": "HTTP request latency in seconds.",
    "prodsim_api_http_in_flight": "HTTP requests currently being handled.",
    "prodsim_api_http_request_bytes_total": "Request body bytes accepted.",
    "prodsim_api_auth_failures_total": "Rejected bearer-token attempts.",
    "prodsim_api_idempotency_replays_total": "Idempotent order submissions replayed.",
    "prodsim_api_idempotency_conflicts_total": "Idempotency-Key payload conflicts.",
    "prodsim_api_orders_total": "Order submissions by outcome.",
    "prodsim_api_admin_actions_total": "Pause/resume/kill actions by outcome.",
    "prodsim_api_ready": "1 when the simulation last reported ready.",
    "prodsim_api_start_time_seconds": "Unix time when the API started.",
}

Labels = Mapping[str, str] | None


def _key(labels: Labels) -> tuple[tuple[str, str], ...]:
    if not labels:
        return ()
    items: list[tuple[str, str]] = []
    for name, value in labels.items():
        if not _LABEL_RE.match(name):
            raise ValueError(f"invalid Prometheus label name {name!r}")
        items.append((name, str(value)))
    return tuple(sorted(items))


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _format_labels(labels: tuple[tuple[str, str], ...]) -> str:
    if not labels:
        return ""
    body = ",".join(f'{name}="{_escape(value)}"' for name, value in labels)
    return "{" + body + "}"


def _format_value(value: float) -> str:
    if math.isnan(value):
        return "NaN"
    if math.isinf(value):
        return "+Inf" if value > 0 else "-Inf"
    if value == int(value):
        return str(int(value))
    return format(value, ".12g")


def _le(bucket: float) -> str:
    return format(bucket, ".12g")


@dataclass
class _Histogram:
    buckets: list[float] = field(default_factory=lambda: [0.0] * len(BUCKETS))
    count: float = 0.0
    total: float = 0.0

    def observe(self, value: float) -> None:
        if value < 0:
            raise ValueError("histogram observations must be non-negative")
        self.count += 1
        self.total += value
        for index, upper in enumerate(BUCKETS):
            if value <= upper:
                self.buckets[index] += 1
                break


class Metrics:
    """Thread-safe registry with Prometheus text exposition."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
        self._gauges: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
        self._histograms: dict[tuple[str, tuple[tuple[str, str], ...]], _Histogram] = {}
        self._names: dict[str, str] = {}
        self.set("prodsim_api_start_time_seconds", time.time())
        self.set("prodsim_api_http_in_flight", 0.0)
        self.set("prodsim_api_ready", 0.0)

    def _register(self, name: str, kind: str) -> None:
        if not _NAME_RE.match(name):
            raise ValueError(f"invalid Prometheus metric name {name!r}")
        previous = self._names.get(name)
        if previous is not None and previous != kind:
            raise ValueError(
                f"metric {name!r} already registered as {previous}, not {kind}"
            )
        self._names[name] = kind

    def inc(self, name: str, labels: Labels = None, amount: float = 1.0) -> None:
        if amount < 0:
            raise ValueError("counters cannot decrease")
        key = (name, _key(labels))
        with self._lock:
            self._register(name, "counter")
            self._counters[key] = self._counters.get(key, 0.0) + amount

    def set(self, name: str, value: float, labels: Labels = None) -> None:
        key = (name, _key(labels))
        with self._lock:
            self._register(name, "gauge")
            self._gauges[key] = float(value)

    def add_gauge(self, name: str, amount: float, labels: Labels = None) -> None:
        key = (name, _key(labels))
        with self._lock:
            self._register(name, "gauge")
            self._gauges[key] = self._gauges.get(key, 0.0) + float(amount)

    def observe(self, name: str, value: float, labels: Labels = None) -> None:
        key = (name, _key(labels))
        with self._lock:
            self._register(name, "histogram")
            self._histograms.setdefault(key, _Histogram()).observe(float(value))

    def record_http(self, method: str, route: str, status: int, seconds: float) -> None:
        labels = {"method": method, "route": route, "status": str(status)}
        self.inc("prodsim_api_http_requests_total", labels)
        self.observe(
            "prodsim_api_http_request_duration_seconds",
            seconds,
            {"method": method, "route": route},
        )

    def auth_failure(self, reason: str) -> None:
        self.inc("prodsim_api_auth_failures_total", {"reason": reason})

    def order(self, outcome: str) -> None:
        self.inc("prodsim_api_orders_total", {"outcome": outcome})

    def admin_action(self, action: str, outcome: str) -> None:
        self.inc(
            "prodsim_api_admin_actions_total",
            {"action": action, "outcome": outcome},
        )

    def render(self, extra: str = "") -> str:
        """Render a Prometheus text snapshot, followed by facade metrics."""

        with self._lock:
            counters = dict(self._counters)
            gauges = dict(self._gauges)
            histograms = {
                key: _Histogram(
                    buckets=list(value.buckets),
                    count=value.count,
                    total=value.total,
                )
                for key, value in self._histograms.items()
            }
            names = dict(self._names)

        lines: list[str] = []
        emitted: set[str] = set()

        def header(name: str, kind: str) -> None:
            if name in emitted:
                return
            emitted.add(name)
            help_text = _HELP.get(name)
            if help_text:
                lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} {kind}")

        families = {
            name: kind for name, kind in names.items() if kind in ("counter", "gauge")
        }
        for name in sorted(families):
            kind = families[name]
            rows = {
                key: value
                for key, value in (counters if kind == "counter" else gauges).items()
                if key[0] == name
            }
            for (_, labels), value in sorted(rows.items()):
                header(name, kind)
                lines.append(f"{name}{_format_labels(labels)} {_format_value(value)}")

        for name in sorted(
            {key[0] for key in histograms if names.get(key[0]) == "histogram"}
        ):
            rows = {key: value for key, value in histograms.items() if key[0] == name}
            for (_, labels), histogram in sorted(rows.items()):
                header(name, "histogram")
                cumulative = 0.0
                for index, upper in enumerate(BUCKETS):
                    cumulative += histogram.buckets[index]
                    bucket_labels = labels + (("le", _le(upper)),)
                    lines.append(
                        f"{name}_bucket{_format_labels(bucket_labels)} "
                        f"{_format_value(cumulative)}"
                    )
                bucket_labels = labels + (("le", "+Inf"),)
                lines.append(
                    f"{name}_bucket{_format_labels(bucket_labels)} "
                    f"{_format_value(histogram.count)}"
                )
                lines.append(
                    f"{name}_sum{_format_labels(labels)} "
                    f"{_format_value(histogram.total)}"
                )
                lines.append(
                    f"{name}_count{_format_labels(labels)} "
                    f"{_format_value(histogram.count)}"
                )

        own = "\n".join(lines)
        if own:
            own += "\n"
        extra = extra.strip("\n")
        if extra:
            own += extra + "\n"
        return own


__all__ = ["BUCKETS", "Metrics"]
