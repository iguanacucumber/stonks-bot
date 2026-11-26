"""Read-only operational doctor for cache, state, ledger, permissions and disk."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import os
from pathlib import Path
import shutil
from typing import Any

import pandas as pd

from bot import data, markets
from bot.live import reconcile
from bot.live.config import LiveConfig
from bot.live.state import StateError, StateStore


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    blocking: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ok": self.ok,
            "detail": self.detail,
            "blocking": self.blocking,
        }


@dataclass
class DoctorReport:
    checks: list[Check] = field(default_factory=list)
    session: str | None = None
    free_mb: float | None = None
    root: str = ""
    cache_dir: str = ""
    state_path: str = ""

    def add(self, name: str, ok: bool, detail: str, blocking: bool = True) -> None:
        self.checks.append(Check(name, bool(ok), detail, blocking))

    @property
    def blocking_failures(self) -> list[Check]:
        return [c for c in self.checks if c.blocking and not c.ok]

    @property
    def warnings(self) -> list[Check]:
        return [c for c in self.checks if not c.blocking and not c.ok]

    @property
    def ok(self) -> bool:
        return not self.blocking_failures

    @property
    def exit_code(self) -> int:
        return 0 if self.ok else 2

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "exit_code": self.exit_code,
            "session": self.session,
            "root": self.root,
            "cache_dir": self.cache_dir,
            "state_path": self.state_path,
            "free_mb": self.free_mb,
            "checks": [c.to_dict() for c in self.checks],
        }

    def lines(self) -> list[str]:
        out = [f"OPS DOCTOR {'PASS' if self.ok else 'FAIL'}  session={self.session}"]
        for c in self.checks:
            tag = "OK  " if c.ok else ("FAIL" if c.blocking else "WARN")
            out.append(f"  [{tag}] {c.name}: {c.detail}")
        if self.free_mb is not None:
            out.append(f"  disk free: {self.free_mb:,.0f} MB")
        return out


def _target_session(now: str | datetime | None) -> pd.Timestamp:
    if now is None:
        return data.target_session()
    if isinstance(now, datetime):
        return data.target_session(now)
    return data.target_session(pd.Timestamp(now).to_pydatetime())


def _last_bar(path: Path) -> pd.Timestamp | None:
    try:
        df = pd.read_csv(path, usecols=["date"])
        dates = pd.to_datetime(df["date"])
        return dates.max().normalize() if len(dates) else None
    except Exception:  # noqa: BLE001 - reported as an unreadable file
        return None


def _writable(path: Path) -> bool:
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    return os.access(probe, os.W_OK)


def _close_on(
    cache: Path, ticker: str, session: pd.Timestamp
) -> tuple[float | None, pd.Timestamp | None]:
    path = cache / "bars" / f"{ticker.replace('/', '_')}.csv"
    try:
        df = pd.read_csv(path, usecols=["date", "adjclose"])
        dates = pd.to_datetime(df["date"])
        df = df.loc[dates <= session]
        if df.empty:
            return None, None
        row = df.iloc[-1]
        px = float(row["adjclose"])
        return (px, pd.Timestamp(row["date"])) if px > 0 else (None, None)
    except Exception:  # noqa: BLE001
        return None, None


def _fx(cache: Path, cfg: LiveConfig, session: pd.Timestamp) -> dict[str, Any]:
    native = cfg.native_currency
    if native == "EUR":
        return {
            "rate": 1.0,
            "date": str(session.date()),
            "fresh": True,
            "pair": None,
            "age_days": 0,
        }
    eur_usd, eur_date = _close_on(cache, "EURUSD=X", session)
    if eur_usd is None or eur_usd <= 0:
        return {
            "rate": None,
            "date": None,
            "fresh": False,
            "pair": "EURUSD=X",
            "age_days": None,
        }
    if native == "USD":
        rate, pair, fx_date = 1.0 / eur_usd, "EURUSD=X", eur_date
    else:
        pair, usd_per_local = markets.FX[native]
        px, px_date = _close_on(cache, pair, session)
        if px is None or px <= 0:
            return {
                "rate": None,
                "date": None,
                "fresh": False,
                "pair": pair,
                "age_days": None,
            }
        native_usd = px if usd_per_local else 1.0 / px
        rate, fx_date = (
            native_usd / eur_usd,
            min(x for x in (eur_date, px_date) if x is not None),
        )
    age = int((session - fx_date).days) if fx_date is not None else None
    return {
        "rate": rate,
        "date": str(fx_date.date()) if fx_date is not None else None,
        "fresh": bool(age is not None and age <= 7),
        "pair": pair,
        "age_days": age,
    }


def run_doctor(
    root: str | Path,
    cfg: LiveConfig,
    *,
    cache_dir: str | Path | None = None,
    now: str | datetime | None = None,
    min_free_mb: float = 200.0,
) -> DoctorReport:
    """Run every check without writing cache/state and without network access."""
    root = Path(root).resolve()
    cache = Path(cache_dir).resolve() if cache_dir else root / "cache"
    store = StateStore(cfg)
    report = DoctorReport(
        root=str(root),
        cache_dir=str(cache),
        state_path=str(store.path),
    )
    report.session = str(_target_session(now).date())

    # --- cache ---------------------------------------------------------
    report.add("cache_dir", cache.is_dir(), str(cache))
    bars = cache / "bars"
    report.add("bars_dir", bars.is_dir(), str(bars))
    required = ["SPY.csv", "SMH.csv", "SOXX.csv"]
    bench = markets.MARKETS[cfg.market]["bench"]
    required.append(f"{bench}.csv")
    if cfg.native_currency != "EUR":
        required.append("EURUSD=X.csv")
        if cfg.native_currency != "USD":
            pair, _ = markets.FX[cfg.native_currency]
            required.append(f"{pair.replace('/', '_')}.csv")
    missing = [name for name in required if not (bars / name).is_file()]
    report.add(
        "required_bars",
        not missing,
        "missing: " + ", ".join(missing) if missing else "all present",
    )
    if cfg.market == "US":
        for name in ("panel.parquet", "membership.parquet"):
            path = cache / name
            report.add(
                f"cache_{name}",
                path.is_file() and path.stat().st_size > 0,
                str(path),
            )

    target = _target_session(now)
    for name in sorted(set(required)):
        path = bars / name
        if not path.is_file():
            continue
        last = _last_bar(path)
        if last is None:
            report.add(f"bar_readable_{name}", False, f"unreadable {path}")
            continue
        age = int((target - last).days)
        stale = age > int(cfg.max_stale_days)
        report.add(
            f"bar_fresh_{name}",
            not stale,
            f"last={last.date()} target={target.date()} age={age}d limit={cfg.max_stale_days}d",
            blocking=name in ("SPY.csv", f"{bench}.csv"),
        )

    fx = _fx(cache, cfg, target)
    if cfg.native_currency == "EUR":
        report.add("fx", True, "native EUR; no conversion required", blocking=False)
    else:
        report.add(
            "fx",
            fx.get("rate") is not None,
            (
                f"{fx.get('pair')} rate={fx.get('rate')} date={fx.get('date')} "
                f"fresh={fx.get('fresh')}"
            ),
        )
        if fx.get("rate") is not None and not fx.get("fresh"):
            report.add(
                "fx_fresh",
                False,
                f"FX last {fx.get('date')} for session {target.date()}",
                blocking=False,
            )

    missing_txt = cache / "missing_symbols.txt"
    if missing_txt.exists():
        names = [x.strip() for x in missing_txt.read_text().splitlines() if x.strip()]
        report.add(
            "missing_symbols",
            True,
            f"{len(names)} known missing symbols",
            blocking=False,
        )
    missing_json = cache / "missing.json"
    if missing_json.exists():
        try:
            import json

            known = json.loads(missing_json.read_text())
            report.add(
                "missing_cache", True, f"{len(known)} backoff entries", blocking=False
            )
        except Exception as exc:  # noqa: BLE001
            report.add(
                "missing_cache",
                False,
                f"unreadable {missing_json}: {exc}",
                blocking=False,
            )

    # --- permissions / disk -------------------------------------------
    report.add(
        "state_writable",
        _writable(store.path.parent),
        f"{store.path.parent} writable={_writable(store.path.parent)}",
    )
    try:
        usage = shutil.disk_usage(root)
        report.free_mb = float(usage.free) / (1024 * 1024)
        report.add(
            "disk_space",
            report.free_mb >= float(min_free_mb),
            f"{report.free_mb:,.0f} MB free (minimum {min_free_mb:,.0f} MB)",
        )
    except OSError as exc:
        report.add("disk_space", False, str(exc))

    # --- state / ledger ------------------------------------------------
    state = None
    if not store.path.exists():
        report.add(
            "state_file", True, "no state file (fresh paper book)", blocking=False
        )
        if store.ledger_path.exists():
            report.add(
                "ledger_without_state",
                False,
                f"ledger exists but state is missing: {store.ledger_path}",
            )
        return report
    try:
        state = store.load(allow_config_change=False)
        report.add("state_file", True, f"readable {store.path}")
    except StateError as exc:
        report.add("state_file", False, str(exc))
        return report

    if state is not None:
        report.add(
            "config_signature",
            state.config_signature == cfg.signature(),
            f"state={state.config_signature} config={cfg.signature()}",
        )
        rep = reconcile.reconcile(cfg, state, store, repair_ledger=False)
        report.add(
            "ledger_reconcile",
            rep.ok,
            "ok" if rep.ok else "; ".join(rep.issues[:5]),
        )
        for warning in rep.warnings[:3]:
            report.add("ledger_warning", True, warning, blocking=False)
        report.add(
            "leverage_ceiling",
            abs(sum(state.target_weights.values())) <= cfg.max_gross + 1e-9,
            f"target gross {sum(state.target_weights.values()):.4f} <= {cfg.max_gross:.2f}",
            blocking=False,
        )
    return report
