"""Alert delivery behind one small interface.

Alerting is **off by default**.  Channels are enabled only when their environment
variables are present, and every test / `alert-test` call is a dry run unless the
operator explicitly asks to send.  Secrets never enter the repository or a report:
configuration is read from the process environment (or an injected mapping).

Supported channels:

* webhook    ``STONKS_ALERT_WEBHOOK_URL``
* Telegram   ``STONKS_ALERT_TELEGRAM_BOT_TOKEN`` + ``STONKS_ALERT_TELEGRAM_CHAT_ID``
* email      ``STONKS_ALERT_EMAIL_TO``, ``STONKS_ALERT_EMAIL_FROM``,
             ``STONKS_SMTP_HOST`` (optional port/user/password, TLS switch)

No channel is created when its required variables are absent, so importing this
module or running ``ops alert-test`` without configuration has no network side
effect.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
import smtplib
from email.message import EmailMessage
from typing import Any, Mapping, Protocol

import requests


@dataclass(frozen=True)
class AlertPayload:
    """A stable, JSON-friendly incident payload."""

    kind: str
    severity: str
    title: str
    message: str
    context: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "severity": self.severity,
            "title": self.title,
            "message": self.message,
            "context": dict(self.context),
            "created_at": self.created_at,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)


@dataclass(frozen=True)
class Delivery:
    channel: str
    ok: bool
    detail: str
    dry_run: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "ok": self.ok,
            "detail": self.detail,
            "dry_run": self.dry_run,
        }


class AlertChannel(Protocol):
    name: str

    def send(self, payload: AlertPayload) -> Delivery: ...


class WebhookChannel:
    name = "webhook"

    def __init__(self, url: str, timeout: float = 5.0):
        self.url = url
        self.timeout = timeout

    def send(self, payload: AlertPayload) -> Delivery:
        try:
            response = requests.post(
                self.url,
                json=payload.to_dict(),
                timeout=self.timeout,
                headers={"User-Agent": "stonks-bot-ops/1"},
            )
            ok = 200 <= int(response.status_code) < 300
            return Delivery(self.name, ok, f"HTTP {response.status_code}")
        except Exception as exc:  # noqa: BLE001 - report, never take down the loop
            return Delivery(self.name, False, f"{type(exc).__name__}: {exc}")


class TelegramChannel:
    name = "telegram"

    def __init__(self, token: str, chat_id: str, timeout: float = 5.0):
        self.token = token
        self.chat_id = chat_id
        self.timeout = timeout

    def send(self, payload: AlertPayload) -> Delivery:
        text = f"[{payload.severity.upper()}] {payload.title}\n{payload.message}"
        try:
            response = requests.post(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                json={"chat_id": self.chat_id, "text": text},
                timeout=self.timeout,
            )
            ok = 200 <= int(response.status_code) < 300
            return Delivery(self.name, ok, f"HTTP {response.status_code}")
        except Exception as exc:  # noqa: BLE001
            return Delivery(self.name, False, f"{type(exc).__name__}: {exc}")


class EmailChannel:
    name = "email"

    def __init__(
        self,
        host: str,
        port: int,
        sender: str,
        recipients: list[str],
        user: str | None = None,
        password: str | None = None,
        use_tls: bool = True,
        timeout: float = 10.0,
    ):
        self.host = host
        self.port = port
        self.sender = sender
        self.recipients = recipients
        self.user = user
        self.password = password
        self.use_tls = use_tls
        self.timeout = timeout

    def send(self, payload: AlertPayload) -> Delivery:
        msg = EmailMessage()
        msg["Subject"] = f"[stonks-bot {payload.severity}] {payload.title}"
        msg["From"] = self.sender
        msg["To"] = ", ".join(self.recipients)
        msg.set_content(f"{payload.message}\n\n{payload.to_json()}\n")
        try:
            with smtplib.SMTP(self.host, self.port, timeout=self.timeout) as smtp:
                if self.use_tls:
                    smtp.starttls()
                if self.user:
                    smtp.login(self.user, self.password or "")
                smtp.send_message(msg)
            return Delivery(
                self.name, True, f"sent to {len(self.recipients)} recipient(s)"
            )
        except Exception as exc:  # noqa: BLE001
            return Delivery(self.name, False, f"{type(exc).__name__}: {exc}")


class AlertManager:
    """One router for every channel; dry-run is the default."""

    def __init__(self, channels: list[AlertChannel] | None = None):
        self.channels = list(channels or [])

    @property
    def enabled(self) -> bool:
        return bool(self.channels)

    def describe(self) -> list[str]:
        return [c.name for c in self.channels]

    def send(self, payload: AlertPayload, *, dry_run: bool = True) -> list[Delivery]:
        if not self.channels:
            return [
                Delivery(
                    "none",
                    True,
                    "alerting off: no STONKS_ALERT_* channels configured",
                    dry_run=dry_run,
                )
            ]
        if dry_run:
            return [
                Delivery(
                    c.name,
                    True,
                    "dry-run: payload validated, no network call",
                    dry_run=True,
                )
                for c in self.channels
            ]
        return [c.send(payload) for c in self.channels]


def _env_bool(value: str | None, default: bool = True) -> bool:
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def build_manager(
    environ: Mapping[str, str] | None = None,
) -> AlertManager:
    """Build configured channels from environment variables.

    Intentionally returns no channels when nothing is configured.  A malformed
    optional value is ignored rather than blocking the live loop; the operator
    sees the missing channel in ``ops alert-test`` output.
    """
    env = dict(os.environ if environ is None else environ)
    channels: list[AlertChannel] = []

    webhook = env.get("STONKS_ALERT_WEBHOOK_URL", "").strip()
    if webhook:
        channels.append(WebhookChannel(webhook))

    token = env.get("STONKS_ALERT_TELEGRAM_BOT_TOKEN", "").strip()
    chat = env.get("STONKS_ALERT_TELEGRAM_CHAT_ID", "").strip()
    if token and chat:
        channels.append(TelegramChannel(token, chat))

    to = [
        x.strip() for x in env.get("STONKS_ALERT_EMAIL_TO", "").split(",") if x.strip()
    ]
    sender = env.get("STONKS_ALERT_EMAIL_FROM", "").strip()
    host = env.get("STONKS_SMTP_HOST", "").strip()
    if to and sender and host:
        try:
            port = int(env.get("STONKS_SMTP_PORT", "587"))
        except ValueError:
            port = 587
        channels.append(
            EmailChannel(
                host=host,
                port=port,
                sender=sender,
                recipients=to,
                user=env.get("STONKS_SMTP_USER") or None,
                password=env.get("STONKS_SMTP_PASSWORD") or None,
                use_tls=_env_bool(env.get("STONKS_SMTP_TLS"), True),
            )
        )
    return AlertManager(channels)


def sample_payload(kind: str = "missed_session") -> AlertPayload:
    """A clear payload for `ops alert-test`; also used by the failure drill."""
    if kind == "missed_session":
        return AlertPayload(
            kind="missed_session",
            severity="warning",
            title="Stonks live session missed",
            message=(
                "The daily live loop has not recorded the latest completed session. "
                "Check cron/systemd, cache freshness, then rerun the idempotent paper run."
            ),
            context={
                "last_session": "2026-11-24",
                "current_session": "2026-11-25",
                "expected_action": "run scripts/live.py run --paper, then reconcile",
            },
        )
    if kind == "broker_down":
        return AlertPayload(
            kind="broker_down",
            severity="critical",
            title="Stonks broker unavailable",
            message=(
                "The execution adapter failed before any order was accepted. "
                "No retry loop is started automatically; reconcile and replan."
            ),
            context={"adapter": "paper", "error": "ConnectionError"},
        )
    if kind == "stale_data":
        return AlertPayload(
            kind="stale_data",
            severity="critical",
            title="Stonks cache is stale",
            message=(
                "The benchmark close is missing for the target session. The live "
                "loop is blocked; do not fabricate a bar or fill."
            ),
            context={"ticker": "SPY", "last_bar": "2026-11-21"},
        )
    if kind == "duplicate_order":
        return AlertPayload(
            kind="duplicate_order",
            severity="critical",
            title="Stonks duplicate order detected",
            message=(
                "An order id appears more than once. The run is blocked; reconcile "
                "the state/ledger before retrying."
            ),
            context={"order_id": "example-order"},
        )
    if kind == "state_corrupt":
        return AlertPayload(
            kind="state_corrupt",
            severity="critical",
            title="Stonks state file is corrupt",
            message=(
                "The live state failed checksum/JSON validation. Restore the last "
                "verified backup or use a fresh state directory after review."
            ),
            context={"file": "state/live/monthly_us.json"},
        )
    return AlertPayload(
        kind=kind,
        severity="info",
        title=f"Stonks test alert: {kind}",
        message="Dry-run alert payload; no external message was sent.",
        context={"test": True},
    )
