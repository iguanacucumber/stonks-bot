"""Atomic live state and the append-only session ledger.

The JSON state is the source of truth. It is replaced with a single ``os.replace``
after an fsync, so a crash leaves either the old or the new file, never half a file.
The CSV ledger is a human/audit view; ``sync_ledger`` can rebuild its rows from state,
which is what makes a crash between the state write and the ledger write harmless.
"""

from __future__ import annotations

import csv
import fcntl
import json
import os
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .config import LiveConfig
from .models import Fill, Plan, Portfolio, _f

VERSION = 1

LEDGER_FIELDS = [
    "time",
    "book",
    "market",
    "session",
    "fill_session",
    "plan_id",
    "status",
    "equity",
    "cash",
    "gross",
    "gross_value",
    "target_gross",
    "currency",
    "fx_rate",
    "fx_date",
    "equity_eur",
    "cash_eur",
    "gross_eur",
    "target_gross_eur",
    "turnover",
    "fees",
    "fees_eur",
    "financing_eur",
    "realized_pnl",
    "realized_pnl_eur",
    "risk_off",
    "risk_reason",
    "drawdown",
    "orders",
    "fills",
    "targets",
    "blocked_reason",
    "data_fresh",
    "note",
]


class StateError(RuntimeError):
    pass


class StateLocked(StateError):
    pass


class StateConfigMismatch(StateError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    try:
        dfd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except OSError:
        pass


@dataclass
class LiveState:
    book: str
    market: str
    currency: str
    created_at: str
    updated_at: str
    initial_capital: float
    config_signature: str
    portfolio: Portfolio
    peak_equity: float
    target_weights: dict[str, float] = field(default_factory=dict)
    risk_off: bool = False
    risk_reason: str | None = None
    last_session: str | None = None
    last_mark_session: str | None = None
    last_plan_id: str | None = None
    pending_plan: Plan | None = None
    pending_cut: dict[str, Any] | None = None
    plans: dict[str, Plan] = field(default_factory=dict)
    runs: dict[str, dict[str, Any]] = field(default_factory=dict)
    fees_paid: float = 0.0
    financing_paid: float = 0.0
    realized_pnl: float = 0.0
    fees_paid_eur: float = 0.0
    financing_paid_eur: float = 0.0
    realized_pnl_eur: float = 0.0
    dividend_income: float = 0.0
    dividend_withholding: float = 0.0
    dividend_income_eur: float = 0.0
    dividend_withholding_eur: float = 0.0
    corporate_actions: list[dict[str, Any]] = field(default_factory=list)
    last_fx_rate: float | None = None
    last_fx_date: str | None = None
    last_equity_eur: float | None = None
    last_cash_eur: float | None = None
    last_gross_eur: float | None = None
    initial_capital_eur: float | None = None
    blocked: dict[str, Any] | None = None
    version: int = VERSION

    @classmethod
    def new(cls, cfg: LiveConfig, now: str | None = None) -> "LiveState":
        stamp = now or utc_now()
        return cls(
            book=cfg.book,
            market=cfg.market,
            currency=cfg.native_currency,
            created_at=stamp,
            updated_at=stamp,
            initial_capital=float(cfg.capital),
            config_signature=cfg.signature(),
            portfolio=Portfolio(cash=float(cfg.capital)),
            peak_equity=float(cfg.capital),
            initial_capital_eur=(
                float(cfg.capital) if cfg.native_currency == "EUR" else None
            ),
        )

    @property
    def equity_hint(self) -> float:
        return float(self.peak_equity)

    def all_order_ids(self) -> set[str]:
        ids: set[str] = set()
        for plan in self.plans.values():
            ids.update(o.order_id for o in plan.orders)
        if self.pending_plan:
            ids.update(o.order_id for o in self.pending_plan.orders)
        return ids

    def all_fill_ids(self) -> set[str]:
        ids: set[str] = set()
        for plan in self.plans.values():
            ids.update(f.fill_id for f in plan.fills)
        return ids

    def all_fills(self) -> list[Fill]:
        out: list[Fill] = []
        for plan in self.plans.values():
            out.extend(plan.fills)
        return out

    def add_plan(self, plan: Plan, keep: int = 240) -> None:
        self.plans[plan.plan_id] = plan
        if len(self.plans) > keep:
            ordered = sorted(self.plans.values(), key=lambda p: p.created_at)
            for old in ordered[: len(self.plans) - keep]:
                if self.pending_plan and old.plan_id == self.pending_plan.plan_id:
                    continue
                if old.plan_id == self.last_plan_id:
                    continue
                self.plans.pop(old.plan_id, None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "book": self.book,
            "market": self.market,
            "currency": self.currency,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "initial_capital": _f(self.initial_capital),
            "config_signature": self.config_signature,
            "portfolio": self.portfolio.to_dict(),
            "peak_equity": _f(self.peak_equity),
            "target_weights": {
                t: _f(w) for t, w in sorted(self.target_weights.items())
            },
            "risk_off": bool(self.risk_off),
            "risk_reason": self.risk_reason,
            "last_session": self.last_session,
            "last_mark_session": self.last_mark_session,
            "last_plan_id": self.last_plan_id,
            "pending_plan": self.pending_plan.to_dict() if self.pending_plan else None,
            "pending_cut": self.pending_cut,
            "plans": {pid: p.to_dict() for pid, p in sorted(self.plans.items())},
            "runs": self.runs,
            "fees_paid": _f(self.fees_paid),
            "financing_paid": _f(self.financing_paid),
            "realized_pnl": _f(self.realized_pnl),
            "fees_paid_eur": _f(self.fees_paid_eur),
            "financing_paid_eur": _f(self.financing_paid_eur),
            "realized_pnl_eur": _f(self.realized_pnl_eur),
            "dividend_income": _f(self.dividend_income),
            "dividend_withholding": _f(self.dividend_withholding),
            "dividend_income_eur": _f(self.dividend_income_eur),
            "dividend_withholding_eur": _f(self.dividend_withholding_eur),
            "corporate_actions": [dict(x) for x in self.corporate_actions],
            "last_fx_rate": _f(self.last_fx_rate, 8),
            "last_fx_date": self.last_fx_date,
            "last_equity_eur": _f(self.last_equity_eur),
            "last_cash_eur": _f(self.last_cash_eur),
            "last_gross_eur": _f(self.last_gross_eur),
            "initial_capital_eur": _f(self.initial_capital_eur),
            "blocked": self.blocked,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "LiveState":
        version = int(raw.get("version", 0))
        if version != VERSION:
            raise StateError(
                f"unsupported live state version {version} (want {VERSION})"
            )
        pending_raw = raw.get("pending_plan")
        plans = {
            str(pid): Plan.from_dict(p) for pid, p in (raw.get("plans") or {}).items()
        }
        pending = Plan.from_dict(pending_raw) if pending_raw else None
        if pending is not None and pending.plan_id in plans:
            # One object, not two copies: settling fills must update the archive too.
            pending = plans[pending.plan_id]
        return cls(
            version=version,
            book=str(raw["book"]),
            market=str(raw["market"]),
            currency=str(raw.get("currency", "USD")),
            created_at=str(raw.get("created_at", "")),
            updated_at=str(raw.get("updated_at", "")),
            initial_capital=float(raw["initial_capital"]),
            config_signature=str(raw.get("config_signature", "")),
            portfolio=Portfolio.from_dict(raw.get("portfolio") or {"cash": 0.0}),
            peak_equity=float(raw.get("peak_equity", raw["initial_capital"])),
            target_weights={
                str(t): float(w) for t, w in (raw.get("target_weights") or {}).items()
            },
            risk_off=bool(raw.get("risk_off", False)),
            risk_reason=raw.get("risk_reason"),
            last_session=raw.get("last_session"),
            last_mark_session=raw.get("last_mark_session"),
            last_plan_id=raw.get("last_plan_id"),
            pending_plan=pending,
            pending_cut=raw.get("pending_cut"),
            plans=plans,
            runs={str(k): dict(v) for k, v in (raw.get("runs") or {}).items()},
            fees_paid=float(raw.get("fees_paid", 0.0)),
            financing_paid=float(raw.get("financing_paid", 0.0)),
            realized_pnl=float(raw.get("realized_pnl", 0.0)),
            fees_paid_eur=float(raw.get("fees_paid_eur", 0.0)),
            financing_paid_eur=float(raw.get("financing_paid_eur", 0.0)),
            realized_pnl_eur=float(raw.get("realized_pnl_eur", 0.0)),
            dividend_income=float(raw.get("dividend_income", 0.0)),
            dividend_withholding=float(raw.get("dividend_withholding", 0.0)),
            dividend_income_eur=float(raw.get("dividend_income_eur", 0.0)),
            dividend_withholding_eur=float(raw.get("dividend_withholding_eur", 0.0)),
            corporate_actions=[dict(x) for x in (raw.get("corporate_actions") or [])],
            last_fx_rate=(
                None if raw.get("last_fx_rate") is None else float(raw["last_fx_rate"])
            ),
            last_fx_date=raw.get("last_fx_date"),
            last_equity_eur=(
                None
                if raw.get("last_equity_eur") is None
                else float(raw["last_equity_eur"])
            ),
            last_cash_eur=(
                None
                if raw.get("last_cash_eur") is None
                else float(raw["last_cash_eur"])
            ),
            last_gross_eur=(
                None
                if raw.get("last_gross_eur") is None
                else float(raw["last_gross_eur"])
            ),
            initial_capital_eur=(
                None
                if raw.get("initial_capital_eur") is None
                else float(raw["initial_capital_eur"])
            ),
            blocked=raw.get("blocked"),
        )


class StateStore:
    """One state file + one ledger per (book, market)."""

    def __init__(self, cfg: LiveConfig):
        self.cfg = cfg
        self.path = cfg.state_path
        self.ledger_path = cfg.ledger_path
        self.corporate_actions_path = cfg.ledger_path.with_name(
            f"{cfg.ledger_path.stem}_corporate_actions.csv"
        )
        self.lock_path = cfg.lock_path

    def exists(self) -> bool:
        return self.path.exists()

    def load(self, allow_config_change: bool = False) -> LiveState | None:
        if not self.path.exists():
            return None
        try:
            raw = json.loads(self.path.read_text())
        except Exception as exc:  # corrupt file: fail closed, never "start over"
            raise StateError(f"cannot read live state {self.path}: {exc}") from exc
        state = LiveState.from_dict(raw)
        if state.book != self.cfg.book or state.market != self.cfg.market:
            raise StateConfigMismatch(
                f"state {self.path.name} is for {state.book}/{state.market}, "
                f"config is {self.cfg.book}/{self.cfg.market}"
            )
        if state.config_signature != self.cfg.signature() and not allow_config_change:
            raise StateConfigMismatch(
                "live config changed since the state was created; use a new "
                "--state-dir or explicitly --allow-config-change after reconciling"
            )
        return state

    def save(self, state: LiveState) -> None:
        state.updated_at = utc_now()
        blob = json.dumps(state.to_dict(), sort_keys=True, indent=1).encode()
        _atomic_write(self.path, blob)

    @contextmanager
    def lock(self) -> Iterator[None]:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise StateLocked(f"another live run holds {self.lock_path}") from exc
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    def ledger_rows(self) -> list[dict[str, str]]:
        if not self.ledger_path.exists():
            return []
        with open(self.ledger_path, newline="") as fh:
            return list(csv.DictReader(fh))

    def upsert_ledger(self, row: dict[str, Any]) -> bool:
        """Insert or replace one row keyed by (book, market, session).

        Returns True when the file changed. A rerun of the same session therefore
        leaves exactly one row, even across a crash after the state write.
        """
        key = (str(row["book"]), str(row["market"]), str(row["session"]))
        rows = self.ledger_rows()
        clean = {
            k: ("" if row.get(k) is None else str(row.get(k))) for k in LEDGER_FIELDS
        }
        clean.update({k: str(v) for k, v in row.items() if k not in clean})
        out: list[dict[str, str]] = []
        found = 0
        identical = False
        for old in rows:
            if (old.get("book"), old.get("market"), old.get("session")) == key:
                found += 1
                if found == 1:
                    identical = old == clean
                    out.append(clean)
                # duplicate rows are dropped, never repeated
            else:
                out.append(old)
        if found == 0:
            out.append(clean)
        elif found == 1 and identical:
            return False
        out.sort(
            key=lambda r: (r.get("book", ""), r.get("market", ""), r.get("session", ""))
        )
        fields = list(LEDGER_FIELDS)
        for r in out:
            for k in r:
                if k not in fields:
                    fields.append(k)
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.ledger_path.with_name(f"{self.ledger_path.name}.{os.getpid()}.tmp")
        with open(tmp, "w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            for r in out:
                writer.writerow(r)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.ledger_path)
        return True

    def sync_ledger(self, state: LiveState) -> int:
        """Rebuild missing/changed ledger rows from state. Returns rows written."""
        changed = 0
        for session in sorted(state.runs):
            rec = state.runs[session]
            row = rec.get("ledger")
            if not row:
                continue
            if self.upsert_ledger(row):
                changed += 1
        return changed

    def corporate_action_rows(self) -> list[dict[str, str]]:
        if not self.corporate_actions_path.exists():
            return []
        with open(self.corporate_actions_path, newline="") as fh:
            return list(csv.DictReader(fh))

    def upsert_corporate_action(self, event: dict[str, Any]) -> bool:
        """Insert/replace one corporate-action row keyed by event_id."""
        event_id = str(event.get("event_id", ""))
        if not event_id:
            raise StateError("corporate action needs an event_id")
        rows = self.corporate_action_rows()
        clean = {str(k): ("" if v is None else str(v)) for k, v in event.items()}
        out: list[dict[str, str]] = []
        found = False
        for old in rows:
            if old.get("event_id") == event_id:
                if not found:
                    out.append(clean)
                found = True
            else:
                out.append(old)
        if not found:
            out.append(clean)
        out.sort(key=lambda r: (r.get("session", ""), r.get("event_id", "")))
        fields: list[str] = []
        for r in out:
            for k in r:
                if k not in fields:
                    fields.append(k)
        self.corporate_actions_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.corporate_actions_path.with_name(
            f"{self.corporate_actions_path.name}.{os.getpid()}.tmp"
        )
        with open(tmp, "w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            for r in out:
                writer.writerow(r)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.corporate_actions_path)
        return True

    def sync_corporate_actions(self, state: LiveState) -> int:
        changed = 0
        for event in state.corporate_actions:
            if self.upsert_corporate_action(event):
                changed += 1
        return changed
