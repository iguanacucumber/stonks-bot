"""Signal snapshot: data gates, marks and the researched target book.

The planner does not re-implement stock selection. It builds the same
``strategy.Market`` the research engine uses and calls ``backtest.target_book`` at a
month-end decision date. Everything here is pure with respect to the cache: no fetch,
no writes, no network.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pandas as pd

from bot import backtest, data, markets, strategy, universe

from . import calendar
from .broker import Bar
from .config import LiveConfig

TOL = 1e-9


class SnapshotError(RuntimeError):
    """The session cannot be planned safely (stale, holiday, missing benchmark)."""


@dataclass
class Snapshot:
    book: str
    market: str
    session: pd.Timestamp
    panel: pd.DataFrame
    mkt: strategy.Market
    cfg: strategy.Config
    close_px: pd.DataFrame
    open_px: pd.DataFrame
    volume_px: pd.DataFrame
    data_issues: list[str] = field(default_factory=list)

    @property
    def session_date(self) -> date:
        return self.session.date()

    @property
    def bench(self) -> str:
        return "SPY"

    def last_date(self, ticker: str) -> pd.Timestamp | None:
        if ticker not in self.close_px.columns:
            return None
        s = self.close_px[ticker].dropna()
        return s.index.max() if len(s) else None

    def close(self, ticker: str) -> float | None:
        if ticker not in self.close_px.columns:
            return None
        value = (
            self.close_px.at[self.session, ticker]
            if self.session in self.close_px.index
            else None
        )
        if value is None or pd.isna(value) or float(value) <= 0:
            return None
        return float(value)

    def closes(self) -> dict[str, float]:
        row = (
            self.close_px.loc[self.session]
            if self.session in self.close_px.index
            else pd.Series(dtype=float)
        )
        return {
            str(t): float(v) for t, v in row.items() if pd.notna(v) and float(v) > 0
        }

    def bar(self, ticker: str, session: str | date | pd.Timestamp) -> Bar | None:
        d = pd.Timestamp(session)
        if d not in self.close_px.index:
            return None
        close = self.close_px.at[d, ticker] if ticker in self.close_px.columns else None
        open_ = self.open_px.at[d, ticker] if ticker in self.open_px.columns else None
        volume = (
            self.volume_px.at[d, ticker] if ticker in self.volume_px.columns else None
        )
        if close is None or pd.isna(close) or float(close) <= 0:
            return None
        bar = Bar(
            open=float(open_)
            if open_ is not None and pd.notna(open_)
            else float("nan"),
            close=float(close),
            volume=float(volume) if volume is not None and pd.notna(volume) else 0.0,
        )
        return bar if bar.valid() else None

    def bars_on(
        self, session: str | date | pd.Timestamp, tickers: list[str]
    ) -> dict[str, Bar]:
        out = {}
        for ticker in tickers:
            bar = self.bar(ticker, session)
            if bar is not None:
                out[ticker] = bar
        return out

    def last_sessions(self, tickers: list[str]) -> dict[str, str | None]:
        return {
            t: (
                str(self.last_date(t).date()) if self.last_date(t) is not None else None
            )
            for t in tickers
        }

    def age_days(self, ticker: str = "SPY") -> int:
        last = self.last_date(ticker)
        if last is None:
            return 10**6
        return int((self.session - last).days)

    @property
    def bench_fresh(self) -> bool:
        return self.last_date(self.bench) == self.session

    def require_fresh(self, tickers: list[str], max_age_days: int) -> list[str]:
        """Return human-readable issues for names without a usable close today."""
        issues = []
        for ticker in sorted(set(tickers)):
            px = self.close(ticker)
            if px is None:
                last = self.last_date(ticker)
                if last is None:
                    issues.append(
                        f"{ticker}: no cached close on or before {self.session.date()}"
                    )
                else:
                    age = (self.session - last).days
                    if age > max_age_days:
                        issues.append(
                            f"{ticker}: stale close {last.date()} ({age}d old), "
                            f"limit {max_age_days}d"
                        )
                    else:
                        issues.append(
                            f"{ticker}: no close on {self.session.date()} "
                            f"(last {last.date()})"
                        )
        return issues

    def data_summary(self) -> dict[str, Any]:
        return {
            "session": str(self.session.date()),
            "bench": self.bench,
            "bench_last": (
                str(self.last_date(self.bench).date())
                if self.last_date(self.bench)
                else None
            ),
            "bench_fresh": self.bench_fresh,
            "sessions": int(len(self.mkt.dates)),
            "panel_last": str(self.panel["date"].max().date()),
        }


def build_snapshot(
    cfg: LiveConfig,
    session: str | date | pd.Timestamp,
    panel: pd.DataFrame | None = None,
    membership: pd.DataFrame | None = None,
) -> Snapshot:
    """Load the committed cache (or a supplied panel) and gate one session."""
    d = pd.Timestamp(session).normalize()
    if not calendar.is_session(d):
        raise SnapshotError(f"market closed: {d.date()} is not a US trading session")
    if panel is None or membership is None:
        built_panel, built_mem = markets.build(cfg.market)
        panel = built_panel if panel is None else panel
        membership = built_mem if membership is None else membership
    panel = panel[panel["date"] <= d].copy()
    if panel.empty:
        raise SnapshotError(f"no cached bars on or before {d.date()}")
    # Non-US symbols keep their Yahoo suffix; the S&P alias map would rewrite them.
    old_canonical = universe.canonical
    try:
        if cfg.market != "US":
            universe.canonical = lambda t: t
        scfg = cfg.strategy_config(end=str(d.date()))
        mkt = strategy.Market(panel, membership, scfg)
    finally:
        universe.canonical = old_canonical
    if d not in mkt.dates:
        last = mkt.dates[-1] if len(mkt.dates) else None
        raise SnapshotError(
            f"stale data: no benchmark session on {d.date()}"
            + (f" (last {last.date()})" if last is not None else "")
        )
    close_px = mkt.px
    open_px = pd.DataFrame(mkt._px_open, index=close_px.index, columns=close_px.columns)
    volume_px = panel.pivot(index="date", columns="ticker", values="volume").reindex(
        index=close_px.index, columns=close_px.columns
    )
    snap = Snapshot(
        book=cfg.book,
        market=cfg.market,
        session=d,
        panel=panel,
        mkt=mkt,
        cfg=scfg,
        close_px=close_px,
        open_px=open_px,
        volume_px=volume_px,
    )
    if not snap.bench_fresh:
        last = snap.last_date("SPY")
        raise SnapshotError(
            f"stale data: benchmark has no close on {d.date()}"
            + (f" (last {last.date()})" if last is not None else "")
        )
    return snap


def strategy_targets(snap: Snapshot, held: list[str] | None = None) -> dict[str, float]:
    """Unlevered selector target at ``snap.session``. Pure, deterministic."""
    base = backtest.target_book(snap.mkt, snap.session, held or [])
    return {t: float(w) for t, w in base.items() if float(w) > TOL}


def levered_targets(snap: Snapshot, held: list[str] | None = None) -> dict[str, float]:
    """Selector target with the research leverage, before live risk scaling."""
    base = strategy_targets(snap, held)
    cfg = snap.mkt.cfg
    if not base:
        return {}
    lev = float(cfg.leverage)
    if cfg.leverage_mode == "trend" and not bool(snap.mkt.regime.loc[snap.session]):
        lev = float(cfg.leverage_down)
    return {t: w * lev for t, w in base.items()}


def trend_breach(snap: Snapshot) -> tuple[bool, float | None, float | None]:
    """Core ETF below its trend MA at the signal close; (breach, price, ma)."""
    cfg = snap.mkt.cfg
    core = cfg.core
    if not cfg.trend_ma or not core or core not in snap.close_px.columns:
        return False, None, None
    if snap.mkt.core_ma is None or snap.session not in snap.mkt.core_ma.index:
        return False, None, None
    ma = snap.mkt.core_ma.at[snap.session]
    px = snap.close(core)
    if px is None or ma is None or pd.isna(ma):
        return False, None, None
    return bool(px < float(ma)), float(px), float(ma)


def coverage(snap: Snapshot, tickers: list[str]) -> dict[str, Any]:
    """Per-symbol freshness for the report; never raises."""
    out = {}
    for t in sorted(set(tickers)):
        last = snap.last_date(t)
        px = snap.close(t)
        out[t] = {
            "close": px,
            "last": str(last.date()) if last is not None else None,
            "fresh": last == snap.session and px is not None,
        }
    return out


def latest_quote(cfg: LiveConfig, tickers: list[str]) -> dict[str, float]:
    """Last cached close for status/reconcile reports (no fetch)."""
    out = {}
    for ticker in tickers:
        path = data.BARS / f"{ticker.replace('/', '_')}.csv"
        if not path.exists():
            continue
        try:
            df = pd.read_csv(path, usecols=["date", "adjclose"])
            if df.empty:
                continue
            out[ticker] = float(df["adjclose"].iloc[-1])
        except Exception:
            continue
    return out


def _close_on(
    ticker: str, session: pd.Timestamp
) -> tuple[float | None, pd.Timestamp | None]:
    path = data.BARS / f"{ticker.replace('/', '_')}.csv"
    if not path.exists():
        return None, None
    try:
        df = pd.read_csv(path, usecols=["date", "adjclose"])
        dates = pd.to_datetime(df["date"])
        df = df[dates <= session]
        if df.empty:
            return None, None
        row = df.iloc[-1]
        px = float(row["adjclose"])
        if px <= 0:
            return None, None
        return px, pd.Timestamp(row["date"])
    except Exception:
        return None, None


def eur_fx(cfg: LiveConfig, session: str | pd.Timestamp) -> dict[str, object]:
    """EUR-per-native-unit FX for the ledger.

    ``rate`` multiplies a native amount to get EUR. Missing/stale FX is reported,
    never invented; it does not block native paper trading because orders are sized
    in the native currency. The EUR columns exist so the overlay can reconcile later.
    """
    d = pd.Timestamp(session).normalize()
    native = cfg.native_currency
    if native == "EUR":
        return {
            "native": native,
            "rate": 1.0,
            "date": str(d.date()),
            "fresh": True,
            "pair": None,
            "age_days": 0,
        }
    eur_pair, _eur_usd_per = markets.FX["EUR"]
    eur_usd, eur_date = _close_on(eur_pair, d)
    if eur_usd is None or eur_usd <= 0:
        return {
            "native": native,
            "rate": None,
            "date": None,
            "fresh": False,
            "pair": eur_pair,
            "age_days": None,
        }
    if native == "USD":
        native_usd = 1.0
        fx_date = eur_date
        pair = eur_pair
    else:
        pair, usd_per_local = markets.FX[native]
        px, px_date = _close_on(pair, d)
        if px is None or px <= 0:
            return {
                "native": native,
                "rate": None,
                "date": None,
                "fresh": False,
                "pair": pair,
                "age_days": None,
            }
        native_usd = px if usd_per_local else 1.0 / px
        fx_date = min(x for x in (eur_date, px_date) if x is not None)
    rate = float(native_usd) / float(eur_usd) if native_usd is not None else None
    age = int((d - fx_date).days) if fx_date is not None else None
    return {
        "native": native,
        "rate": rate,
        "date": str(fx_date.date()) if fx_date is not None else None,
        "fresh": bool(age is not None and age <= 7),
        "pair": pair,
        "age_days": age,
    }
