"""Human approval artifact for a future real-money session.

This module only reads and verifies an approval file. It does not open a broker
connection, read credentials, submit orders or enable a live order path. In this
paper-only build the preflight command always fails closed at the execution-path
check; the approval artifact is a documented prerequisite, not a switch.

The artifact is deliberately strict: missing fields, unknown fields, a naive or
future timestamp, an expired window, a capital/book/market mismatch or a hash
mismatch are all reported as blockers. The default path is
``state/live/APPROVAL.json`` and ``state/`` is gitignored.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import ROOT, LiveConfig
from .models import Plan

APPROVAL_VERSION = 1
DEFAULT_APPROVAL_PATH = ROOT / "state" / "live" / "APPROVAL.json"
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_KEYS = {
    "version",
    "approver",
    "approved_at",
    "expires_at",
    "max_capital",
    "allowed_books",
    "allowed_markets",
    "config_sha256",
    "plan_sha256",
    "note",
}
_REQUIRED = {
    "version",
    "approver",
    "approved_at",
    "expires_at",
    "max_capital",
    "allowed_books",
    "allowed_markets",
    "config_sha256",
}


class ApprovalError(RuntimeError):
    """The approval artifact is missing, malformed or unsafe to apply."""


def _canonical(raw: Any) -> bytes:
    return json.dumps(
        raw,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode()


def config_sha256(cfg: LiveConfig) -> str:
    """Full SHA-256 of the exact live config, excluding only ``state_dir``.

    ``state_dir`` is a local runtime path and must not invalidate an approval;
    every other field (including capital, broker, read-only and rails) is pinned.
    """
    raw = asdict(cfg)
    raw.pop("state_dir", None)
    return hashlib.sha256(_canonical(raw)).hexdigest()


def plan_sha256(plan: Plan | dict[str, Any] | str | Path) -> str:
    """Full SHA-256 of one plan, as emitted by ``scripts/live.py ... --json``.

    A JSON file may be the plan object itself or a report wrapper containing a
    ``plan`` key; only the plan object is hashed.
    """
    if isinstance(plan, Plan):
        raw: Any = plan.to_dict()
    elif isinstance(plan, (str, Path)):
        try:
            raw = json.loads(Path(plan).read_text())
        except OSError as exc:
            raise ApprovalError(f"cannot read plan {plan}: {exc}") from exc
        except ValueError as exc:
            raise ApprovalError(f"plan {plan} is not valid JSON: {exc}") from exc
    else:
        raw = plan
    if not isinstance(raw, dict):
        raise ApprovalError("plan hash input must be a JSON object")
    if isinstance(raw.get("plan"), dict):
        raw = raw["plan"]
    return hashlib.sha256(_canonical(raw)).hexdigest()


def _parse_ts(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ApprovalError(f"{field} must be an ISO-8601 timestamp with an offset")
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ApprovalError(f"{field} is not an ISO-8601 timestamp: {text!r}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ApprovalError(f"{field} must include a timezone offset: {text!r}")
    return parsed.astimezone(timezone.utc)


def _string_list(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ApprovalError(f"{field} must be a non-empty list of strings")
    out: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ApprovalError(f"{field} contains a non-string/blank value")
        out.append(item.strip())
    return tuple(dict.fromkeys(out))


@dataclass(frozen=True)
class Approval:
    version: int
    approver: str
    approved_at: str
    expires_at: str
    max_capital: float
    allowed_books: tuple[str, ...]
    allowed_markets: tuple[str, ...]
    config_sha256: str
    plan_sha256: str | None = None
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "approver": self.approver,
            "approved_at": self.approved_at,
            "expires_at": self.expires_at,
            "max_capital": self.max_capital,
            "allowed_books": list(self.allowed_books),
            "allowed_markets": list(self.allowed_markets),
            "config_sha256": self.config_sha256,
            "plan_sha256": self.plan_sha256,
            "note": self.note,
        }


def parse_approval(raw: Any) -> Approval:
    """Parse one approval object, rejecting unknown/missing/unsafe fields."""
    if not isinstance(raw, dict):
        raise ApprovalError("approval must be a JSON object")
    unknown = sorted(set(raw) - _KEYS)
    if unknown:
        raise ApprovalError(f"approval has unknown fields: {unknown}")
    missing = sorted(_REQUIRED - set(raw))
    if missing:
        raise ApprovalError(f"approval is missing required fields: {missing}")

    version = raw["version"]
    if isinstance(version, bool) or not isinstance(version, int):
        raise ApprovalError("version must be an integer")
    if version != APPROVAL_VERSION:
        raise ApprovalError(
            f"unsupported approval version {version} (want {APPROVAL_VERSION})"
        )
    approver = raw["approver"]
    if not isinstance(approver, str) or len(approver.strip()) < 3:
        raise ApprovalError("approver must be a non-empty human name")
    approved_at = _parse_ts(raw["approved_at"], "approved_at")
    expires_at = _parse_ts(raw["expires_at"], "expires_at")
    capital = raw["max_capital"]
    if isinstance(capital, bool) or not isinstance(capital, (int, float)):
        raise ApprovalError("max_capital must be a number")
    capital = float(capital)
    if not math.isfinite(capital) or capital <= 0:
        raise ApprovalError("max_capital must be finite and > 0")
    books = _string_list(raw["allowed_books"], "allowed_books")
    markets = _string_list(raw["allowed_markets"], "allowed_markets")
    config_hash = raw["config_sha256"]
    if not isinstance(config_hash, str) or not _SHA256_RE.match(config_hash):
        raise ApprovalError("config_sha256 must be a 64-character hex SHA-256")
    plan_hash = raw.get("plan_sha256")
    if plan_hash is not None and (
        not isinstance(plan_hash, str) or not _SHA256_RE.match(plan_hash)
    ):
        raise ApprovalError("plan_sha256 must be a 64-character hex SHA-256")
    note = raw.get("note", "")
    if not isinstance(note, str):
        raise ApprovalError("note must be a string")

    return Approval(
        version=version,
        approver=approver.strip(),
        approved_at=approved_at.isoformat(timespec="seconds"),
        expires_at=expires_at.isoformat(timespec="seconds"),
        max_capital=capital,
        allowed_books=books,
        allowed_markets=markets,
        config_sha256=config_hash.lower(),
        plan_sha256=None if plan_hash is None else plan_hash.lower(),
        note=note.strip(),
    )


def load_approval(path: str | Path = DEFAULT_APPROVAL_PATH) -> Approval:
    path = Path(path)
    try:
        raw = json.loads(path.read_text())
    except FileNotFoundError as exc:
        raise ApprovalError(f"missing approval artifact: {path}") from exc
    except OSError as exc:
        raise ApprovalError(f"cannot read approval artifact {path}: {exc}") from exc
    except ValueError as exc:
        raise ApprovalError(
            f"approval artifact {path} is not valid JSON: {exc}"
        ) from exc
    return parse_approval(raw)


def check_approval(
    approval: Approval,
    cfg: LiveConfig,
    *,
    now: str | datetime | None = None,
    plan: Plan | dict[str, Any] | str | Path | None = None,
) -> list[str]:
    """Every reason the approval cannot authorise this exact config/plan."""
    issues: list[str] = []
    current = (
        datetime.now(timezone.utc)
        if now is None
        else now.astimezone(timezone.utc)
        if isinstance(now, datetime)
        else _parse_ts(now, "now")
    )
    approved = _parse_ts(approval.approved_at, "approved_at")
    expires = _parse_ts(approval.expires_at, "expires_at")
    if approved > current + timedelta(minutes=5):
        issues.append(f"approval is dated in the future ({approval.approved_at})")
    if expires <= approved:
        issues.append("expires_at must be later than approved_at")
    if expires <= current:
        issues.append(f"approval expired at {approval.expires_at}")
    if float(cfg.capital) > float(approval.max_capital) + 1e-9:
        issues.append(
            f"capital {cfg.capital:,.2f} > approved max_capital "
            f"{approval.max_capital:,.2f}"
        )
    if cfg.book not in approval.allowed_books:
        issues.append(
            f"book {cfg.book!r} is not in approved books {list(approval.allowed_books)}"
        )
    if cfg.market not in approval.allowed_markets:
        issues.append(
            f"market {cfg.market!r} is not in approved markets "
            f"{list(approval.allowed_markets)}"
        )
    actual_config = config_sha256(cfg)
    if actual_config != approval.config_sha256:
        issues.append(
            "config hash mismatch: approval pins "
            f"{approval.config_sha256[:16]}..., current config is "
            f"{actual_config[:16]}..."
        )
    if approval.plan_sha256 is not None:
        if plan is None:
            issues.append("approval pins a plan hash but no --plan was supplied")
        else:
            try:
                actual_plan = plan_sha256(plan)
            except ApprovalError as exc:
                issues.append(str(exc))
            else:
                if actual_plan != approval.plan_sha256:
                    issues.append(
                        "plan hash mismatch: approval pins "
                        f"{approval.plan_sha256[:16]}..., current plan is "
                        f"{actual_plan[:16]}..."
                    )
    return issues


def approval_issues(
    path: str | Path,
    cfg: LiveConfig,
    *,
    now: str | datetime | None = None,
    plan: Plan | dict[str, Any] | str | Path | None = None,
) -> list[str]:
    """Load and check an approval, collapsing any failure to a blocker string."""
    try:
        approval = load_approval(path)
    except ApprovalError as exc:
        return [str(exc)]
    try:
        return check_approval(approval, cfg, now=now, plan=plan)
    except ApprovalError as exc:
        return [str(exc)]
