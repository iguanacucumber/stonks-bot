"""Bearer-token authentication for the v12 production simulation API.

Static tokens only. There is no credential store, no OAuth and no call to a
real broker. Token values are never logged; only the reason a request was
rejected is recorded.
"""

from __future__ import annotations

import hmac
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass, field

LOG = logging.getLogger("bot.prodsim.auth")

API_TOKEN_ENV = "STONKS_PRODSIM_API_TOKEN"
ADMIN_TOKEN_ENV = "STONKS_PRODSIM_ADMIN_TOKEN"


class AuthError(Exception):
    """Authentication/authorization failure.

    ``reason`` is a low-cardinality label safe for logs and metrics.
    """

    def __init__(self, reason: str, message: str = "unauthorized") -> None:
        super().__init__(message)
        self.reason = reason


def parse_bearer(header: str | None) -> str | None:
    """Extract a bearer token from an ``Authorization`` header value.

    Returns ``None`` for a missing or malformed header. The token itself is
    never included in errors or logs.
    """

    if not header:
        return None
    parts = header.strip().split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    if not token or any(char.isspace() for char in token):
        return None
    return token


@dataclass(frozen=True)
class AuthConfig:
    """Static bearer tokens for the API and admin actions.

    An empty ``admin_token`` falls back to the API token. An empty API token is
    only accepted in explicit dev mode, where authentication is disabled and a
    warning is logged once at startup.
    """

    api_token: str = field(default="", repr=False)
    admin_token: str = field(default="", repr=False)
    dev: bool = False

    @property
    def effective_admin_token(self) -> str:
        return self.admin_token or self.api_token

    @property
    def auth_disabled(self) -> bool:
        return not self.api_token and self.dev

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
        *,
        dev: bool = False,
        api_token: str = "",
        admin_token: str = "",
    ) -> AuthConfig:
        source = os.environ if env is None else env
        return cls(
            api_token=api_token or source.get(API_TOKEN_ENV, ""),
            admin_token=admin_token or source.get(ADMIN_TOKEN_ENV, ""),
            dev=dev,
        )

    @classmethod
    def from_run_config(
        cls,
        run_config: object,
        env: Mapping[str, str] | None = None,
        *,
        dev: bool = False,
        admin_token: str = "",
    ) -> AuthConfig:
        token = str(getattr(run_config, "api_token", "") or "")
        return cls.from_env(env, dev=dev, api_token=token, admin_token=admin_token)

    def validate(self) -> None:
        """Fail closed outside dev; warn loudly when auth is disabled."""

        if not self.api_token and not self.dev:
            raise ValueError(
                "API token required outside dev mode; set "
                f"{API_TOKEN_ENV} or pass --dev"
            )
        if self.auth_disabled:
            LOG.warning(
                "prodsim API dev mode: bearer authentication is disabled "
                "(empty API token); never expose this listener"
            )
        if self.dev and not self.effective_admin_token:
            LOG.warning("prodsim API dev mode: admin actions are unauthenticated")

    def check(self, authorization: str | None, *, admin: bool = False) -> None:
        """Raise ``AuthError`` unless the header carries the required token."""

        expected = self.effective_admin_token if admin else self.api_token
        if not expected:
            if self.dev:
                return
            # validate() should have caught this; keep fail-closed anyway.
            raise AuthError("not_configured", "server token is not configured")
        supplied = parse_bearer(authorization)
        if supplied is None:
            raise AuthError("missing", "missing bearer token")
        if not hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8")):
            raise AuthError(
                "invalid_admin_token" if admin else "invalid_token",
                "invalid bearer token",
            )


__all__ = [
    "ADMIN_TOKEN_ENV",
    "API_TOKEN_ENV",
    "AuthConfig",
    "AuthError",
    "parse_bearer",
]
