"""Deterministic replay feed from cached daily or 5-minute bars.

The feed never fetches data. It reads the committed cache (or a caller-supplied
bar list), synthesises a reproducible top-of-book path inside each bar, and
yields ticks in strictly increasing timestamp order. A strategy only ever sees
the next tick; bars are not exposed to the strategy, so replay cannot leak a
future bar through this module.

Daily bars are used when no 5-minute file exists. The synthetic path is
intentionally boring and deterministic: a linear open-to-close bridge clipped to
the bar's low/high, with seeded noise and a U-shaped volume profile. It is a
liquidity model for execution tests, not a return model.
"""

from __future__ import annotations

import hashlib
import math
import os
import random
import time as wall_time
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable, Iterator, Mapping, Sequence
from zoneinfo import ZoneInfo

from .contract import Tick

__all__ = [
    "BarDataError",
    "LivePaperFeed",
    "ReplayBar",
    "ReplayFeed",
    "generate_ticks",
    "load_5m_bars",
    "load_bars",
    "load_daily_bars",
    "synthetic_bars",
]

ROOT = Path(__file__).resolve().parents[2]
NY = ZoneInfo("America/New_York")


class BarDataError(RuntimeError):
    """Cached bars are missing or malformed; fail closed, never fetch."""


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _as_date(value: str | date | datetime) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def session_bounds(session: date, close_hour: int = 16) -> tuple[datetime, datetime]:
    """NYSE regular-session open/close in UTC (13:00 close for half-days)."""
    start = datetime.combine(session, time(9, 30), tzinfo=NY)
    end = datetime.combine(session, time(close_hour, 0), tzinfo=NY)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


@dataclass(frozen=True)
class ReplayBar:
    """One daily or intraday OHLCV bar used to generate synthetic ticks."""

    symbol: str
    session: date
    start: datetime
    end: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    source: str = "daily"

    def __post_init__(self) -> None:
        if not self.symbol:
            raise ValueError("symbol is required")
        if self.end <= self.start:
            raise ValueError(f"{self.symbol} {self.session}: end must follow start")
        for name in ("open", "high", "low", "close"):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{self.symbol} {self.session}: invalid {name}")
        if self.high < max(self.open, self.close, self.low):
            raise ValueError(f"{self.symbol} {self.session}: high below OHLC")
        if self.low > min(self.open, self.close, self.high):
            raise ValueError(f"{self.symbol} {self.session}: low above OHLC")
        if self.volume < 0 or not math.isfinite(float(self.volume)):
            raise ValueError(f"{self.symbol} {self.session}: invalid volume")

    @classmethod
    def daily(
        cls,
        symbol: str,
        session: date | str,
        open_: float,
        high: float,
        low: float,
        close: float,
        volume: float = 0.0,
        *,
        close_hour: int = 16,
    ) -> "ReplayBar":
        d = _as_date(session)
        start, end = session_bounds(d, close_hour)
        return cls(
            symbol=symbol,
            session=d,
            start=start,
            end=end,
            open=float(open_),
            high=float(high),
            low=float(low),
            close=float(close),
            volume=float(volume),
            source="daily",
        )


def _seed_for(bar: ReplayBar, seed: int) -> int:
    raw = f"{seed}|{bar.symbol}|{bar.session.isoformat()}|{bar.start.isoformat()}"
    digest = hashlib.blake2b(raw.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big")


def generate_ticks(
    bar: ReplayBar,
    *,
    ticks_per_bar: int = 12,
    seed: int = 7,
    spread_bps: float = 2.0,
) -> list[Tick]:
    """Generate ``ticks_per_bar`` deterministic top-of-book ticks for one bar.

    The first and last tick print the bar's open and close. Intermediate ticks
    stay inside ``[low, high]`` and volumes sum (approximately) to the bar's
    volume. The same bar, seed and count always produce identical ticks.
    """
    n = int(ticks_per_bar)
    if n < 1:
        raise ValueError("ticks_per_bar must be >= 1")
    rng = random.Random(_seed_for(bar, seed))
    span = max(1e-12, bar.end.timestamp() - bar.start.timestamp())
    rng_span = max(1e-9, (bar.high - bar.low) / max(bar.close, 1e-9))
    path: list[float] = []
    noise: list[float] = []
    for i in range(n):
        u = 0.0 if n == 1 else i / (n - 1)
        base = bar.open + (bar.close - bar.open) * u
        if i == 0:
            px = bar.open
        elif i == n - 1:
            px = bar.close
        else:
            # A bounded bridge perturbation; never uses information beyond the
            # bar, and the realised high/low bound is applied below.
            bridge = math.sin(math.pi * u) * bar.close * rng_span * 0.12
            px = base + bridge * rng.uniform(-1.0, 1.0)
        px = min(bar.high, max(bar.low, px))
        path.append(max(1e-9, px))
        noise.append(rng.random())
    # U-shaped participation: heavier at the open and close, lighter midday.
    weights = []
    for i in range(n):
        u = 0.0 if n == 1 else i / (n - 1)
        weights.append(0.6 + 1.4 * abs(u - 0.5) * 2.0 + 0.25 * noise[i])
    total_w = sum(weights) or 1.0
    total_volume = max(0.0, float(bar.volume))
    out: list[Tick] = []
    for i, px in enumerate(path):
        ts = bar.start + timedelta(seconds=span * (i / max(1, n - 1)))
        spread = max(px * max(0.0, spread_bps) / 1e4, 0.0001)
        bid = max(1e-9, px - spread / 2.0)
        ask = px + spread / 2.0
        if ask < bid:
            ask = bid
        volume = total_volume * weights[i] / total_w
        out.append(
            Tick(
                ts=_utc(ts),
                symbol=bar.symbol,
                bid=round(bid, 6),
                ask=round(ask, 6),
                last=round(px, 6),
                volume=round(volume, 6),
            )
        )
    return out


class ReplayFeed:
    """A finite, deterministic iterator of ticks.

    ``ReplayFeed`` is replay-only. The live-paper loop can still use it with a
    realtime clock; the clock, not the feed, decides pacing. Data gaps are
    reported through :meth:`outages` so health can fail closed.
    """

    def __init__(
        self,
        bars: Iterable[ReplayBar],
        *,
        seed: int = 7,
        ticks_per_bar: int | Mapping[str, int] = 12,
        spread_bps: float = 2.0,
    ) -> None:
        self._bars = sorted(list(bars), key=lambda b: (b.start, b.symbol))
        if not self._bars:
            raise BarDataError("replay feed has no bars")
        self.seed = int(seed)
        self.ticks_per_bar = ticks_per_bar
        self.spread_bps = float(spread_bps)
        self._by_session: dict[date, list[ReplayBar]] = {}
        for bar in self._bars:
            self._by_session.setdefault(bar.session, []).append(bar)
        self._symbols = sorted({b.symbol for b in self._bars})

    @property
    def bars(self) -> tuple[ReplayBar, ...]:
        return tuple(self._bars)

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(self._symbols)

    @property
    def sessions(self) -> tuple[date, ...]:
        return tuple(sorted(self._by_session))

    def _count(self, bar: ReplayBar) -> int:
        if isinstance(self.ticks_per_bar, Mapping):
            return int(self.ticks_per_bar.get(bar.symbol, 12))
        return int(self.ticks_per_bar)

    def outages(self, required: Sequence[str] | None = None) -> list[dict]:
        """Sessions where at least one required symbol has no bar."""
        need = list(required or self._symbols)
        out = []
        for session in self.sessions:
            present = {b.symbol for b in self._by_session[session]}
            missing = [s for s in need if s not in present]
            if missing:
                out.append({"session": session.isoformat(), "missing": missing})
        return out

    def iter_ticks(self, symbols: Sequence[str] | None = None) -> Iterator[Tick]:
        keep = set(symbols) if symbols else None
        for session in self.sessions:
            ticks: list[Tick] = []
            for bar in self._by_session[session]:
                if keep is not None and bar.symbol not in keep:
                    continue
                ticks.extend(
                    generate_ticks(
                        bar,
                        ticks_per_bar=self._count(bar),
                        seed=self.seed,
                        spread_bps=self.spread_bps,
                    )
                )
            ticks.sort(key=lambda t: (t.ts, t.symbol))
            # Different symbols can share a nominal timestamp inside one bar.
            # A tick is a distinct observation, so nudge duplicates by a
            # microsecond instead of handing the clock an invalid sequence.
            previous: datetime | None = None
            for tick in ticks:
                if previous is not None and tick.ts <= previous:
                    tick = replace(tick, ts=previous + timedelta(microseconds=1))
                previous = tick.ts
                yield tick

    def __iter__(self) -> Iterator[Tick]:
        return self.iter_ticks()


# -- cache loaders -----------------------------------------------------
def _cache_dir(cache_dir: str | Path | None) -> Path:
    if cache_dir is not None:
        return Path(cache_dir)
    env = os.environ.get("PRODSIM_CACHE_DIR")
    return Path(env) if env else ROOT / "cache"


def _bar_rows(rows: Iterable[Mapping], symbol: str, source: str) -> list[ReplayBar]:
    out = []
    for row in rows:
        try:
            session = _as_date(str(row["date"]))
            open_ = float(row.get("open", row["close"]))
            high = float(row.get("high", max(open_, float(row["close"]))))
            low = float(row.get("low", min(open_, float(row["close"]))))
            close = float(row["close"])
            volume = float(row.get("volume", 0.0) or 0.0)
        except (KeyError, TypeError, ValueError) as exc:
            raise BarDataError(f"{symbol}: malformed cached bar: {exc}") from exc
        if min(open_, high, low, close) <= 0:
            raise BarDataError(f"{symbol} {session}: non-positive OHLC")
        out.append(
            ReplayBar.daily(
                symbol,
                session,
                open_=open_,
                high=max(high, open_, close),
                low=min(low, open_, close),
                close=close,
                volume=max(0.0, volume),
            )
        )
    return out


def load_daily_bars(
    symbols: Sequence[str],
    start: str | date,
    end: str | date,
    *,
    cache_dir: str | Path | None = None,
    panel: object | None = None,
) -> list[ReplayBar]:
    """Load daily bars from the committed panel or per-symbol CSV cache.

    No network call is possible. Missing data raises :class:`BarDataError`;
    callers that want a partial universe should filter ``symbols`` first.
    """
    import pandas as pd  # lazy: replay tests can use synthetic bars only

    wanted = [str(s) for s in symbols]
    if not wanted:
        raise BarDataError("at least one symbol is required")
    a, b = _as_date(start), _as_date(end)
    if b < a:
        raise BarDataError("end precedes start")
    root = _cache_dir(cache_dir)
    frame = panel
    if frame is None:
        path = root / "panel.parquet"
        if path.exists():
            try:
                frame = pd.read_parquet(path)
            except Exception as exc:  # noqa: BLE001
                raise BarDataError(f"unreadable panel cache: {exc}") from exc
    bars: list[ReplayBar] = []
    have: set[str] = set()
    if frame is not None:
        df = frame
        if "ticker" in df.columns and "date" in df.columns:
            dates = pd.to_datetime(df["date"])
            mask = (
                df["ticker"].astype(str).isin(wanted)
                & (dates >= pd.Timestamp(a))
                & (dates <= pd.Timestamp(b))
            )
            sub = df.loc[mask]
            for symbol, rows in sub.groupby("ticker", sort=True):
                rows = rows.sort_values("date")
                bars.extend(_bar_rows(rows.to_dict("records"), str(symbol), "panel"))
                have.add(str(symbol))
    # Per-symbol files fill symbols absent from the panel (and work without it).
    for symbol in wanted:
        if symbol in have:
            continue
        path = root / "bars" / f"{symbol.replace('/', '_')}.csv"
        if not path.exists():
            continue
        try:
            df = pd.read_csv(path)
        except Exception as exc:  # noqa: BLE001
            raise BarDataError(f"{symbol}: unreadable bar cache {path}: {exc}") from exc
        if "date" not in df.columns:
            raise BarDataError(f"{symbol}: bar cache has no date column")
        dates = pd.to_datetime(df["date"])
        sub = df[(dates >= pd.Timestamp(a)) & (dates <= pd.Timestamp(b))]
        bars.extend(_bar_rows(sub.to_dict("records"), symbol, "csv"))
        have.add(symbol)
    missing = [s for s in wanted if s not in have]
    if missing:
        raise BarDataError(
            f"no cached bars for {missing} between {a} and {b}; "
            "refresh the cache first (replay never fetches)"
        )
    bars.sort(key=lambda x: (x.start, x.symbol))
    return bars


def load_5m_bars(
    symbols: Sequence[str],
    start: str | date,
    end: str | date,
    *,
    cache_dir: str | Path | None = None,
    directory: str | Path | None = None,
    ticks_per_bar: int = 4,
) -> list[ReplayBar]:
    """Load cached 5-minute bars if present.

    Accepted directories are ``cache/bars5m`` and ``cache/intraday``; accepted
    time columns are ``ts``, ``timestamp``, ``datetime`` or ``date``. Missing
    files are simply not returned; :func:`load_bars` falls back to daily.
    """
    import pandas as pd

    root = _cache_dir(cache_dir)
    bases = [Path(directory)] if directory else [root / "bars5m", root / "intraday"]
    a, b = _as_date(start), _as_date(end)
    out: list[ReplayBar] = []
    for symbol in [str(s) for s in symbols]:
        for base in bases:
            path = base / f"{symbol.replace('/', '_')}.csv"
            if not path.exists():
                continue
            try:
                df = pd.read_csv(path)
            except Exception as exc:  # noqa: BLE001
                raise BarDataError(
                    f"{symbol}: unreadable 5m cache {path}: {exc}"
                ) from exc
            time_col = next(
                (c for c in ("ts", "timestamp", "datetime", "date") if c in df.columns),
                None,
            )
            if time_col is None:
                raise BarDataError(f"{symbol}: 5m cache has no timestamp column")
            stamps = pd.to_datetime(df[time_col], utc=True)
            mask = (stamps.dt.date >= a) & (stamps.dt.date <= b)
            sub = df.loc[mask]
            for row in sub.to_dict("records"):
                ts = pd.Timestamp(row[time_col])
                ts = (
                    ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
                )
                start_dt = ts.to_pydatetime()
                span = timedelta(minutes=5)
                close = float(row["close"])
                open_ = float(row.get("open", close))
                high = float(row.get("high", max(open_, close)))
                low = float(row.get("low", min(open_, close)))
                out.append(
                    ReplayBar(
                        symbol=symbol,
                        session=start_dt.date(),
                        start=start_dt,
                        end=start_dt + span,
                        open=open_,
                        high=max(high, open_, close),
                        low=min(low, open_, close),
                        close=close,
                        volume=float(row.get("volume", 0.0) or 0.0),
                        source="5m",
                    )
                )
            break
    out.sort(key=lambda x: (x.start, x.symbol))
    return out


def load_bars(
    symbols: Sequence[str],
    start: str | date,
    end: str | date,
    *,
    cache_dir: str | Path | None = None,
    prefer: str = "auto",
    ticks_per_bar: int = 12,
) -> list[ReplayBar]:
    """Prefer cached 5-minute bars, otherwise daily; never fetch."""
    if prefer not in ("auto", "5m", "daily"):
        raise ValueError("prefer must be auto, 5m or daily")
    if prefer in ("auto", "5m"):
        intraday = load_5m_bars(symbols, start, end, cache_dir=cache_dir)
        if intraday:
            return intraday
        if prefer == "5m":
            raise BarDataError("no cached 5-minute bars for the requested range")
    return load_daily_bars(symbols, start, end, cache_dir=cache_dir)


def synthetic_bars(
    symbols: Sequence[str],
    sessions: Sequence[date | str],
    *,
    start_price: float = 100.0,
    seed: int = 7,
    volume: float = 1_000_000.0,
) -> list[ReplayBar]:
    """In-memory bars for tests/demos; deterministic, no cache required."""
    rng = random.Random(seed)
    prices = {str(s): float(start_price) for s in symbols}
    out: list[ReplayBar] = []
    for session in sessions:
        for symbol in symbols:
            prev = prices[str(symbol)]
            drift = rng.uniform(-0.015, 0.015)
            close = max(0.01, prev * (1.0 + drift))
            high = max(prev, close) * (1.0 + abs(rng.uniform(0.0, 0.01)))
            low = min(prev, close) * (1.0 - abs(rng.uniform(0.0, 0.01)))
            out.append(
                ReplayBar.daily(
                    str(symbol),
                    session,
                    open_=prev,
                    high=high,
                    low=low,
                    close=close,
                    volume=volume,
                )
            )
            prices[str(symbol)] = close
    return out


class LivePaperFeed:
    """Long-running live-paper feed over the committed cache.

    It polls the cache for newly completed daily sessions, emits their
    deterministic ticks, then sleeps. No network access happens here; an
    external refresh job must advance the cache. If the newest completed cached
    session is older than ``max_stale_days`` the feed raises
    :class:`BarDataError`, which the simulation surfaces as ``ERROR`` and
    ``READY=false`` rather than inventing a tick.
    """

    def __init__(
        self,
        symbols: Sequence[str],
        *,
        cache_dir: str | Path | None = None,
        start: str | date | None = None,
        poll_seconds: float = 30.0,
        ticks_per_bar: int = 12,
        seed: int = 7,
        spread_bps: float = 2.0,
        max_stale_days: int = 3,
        max_idle_polls: int | None = None,
        now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        if not symbols:
            raise ValueError("at least one symbol is required")
        if poll_seconds < 0:
            raise ValueError("poll_seconds must be >= 0")
        self.symbols = tuple(str(s) for s in symbols)
        self.cache_dir = _cache_dir(cache_dir)
        self.start = _as_date(start) if start is not None else None
        self.poll_seconds = float(poll_seconds)
        self.ticks_per_bar = int(ticks_per_bar)
        self.seed = int(seed)
        self.spread_bps = float(spread_bps)
        self.max_stale_days = int(max_stale_days)
        self.max_idle_polls = max_idle_polls
        self.now_fn = now_fn
        self._yielded: set[tuple[str, str]] = set()

    def _completed_session(self) -> date:
        now = self.now_fn()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        et = now.astimezone(NY)
        d = et.date()
        close = time(13, 30) if _is_half_day(d) else time(16, 30)
        if et.time() < close:
            d -= timedelta(days=1)
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        return d

    def _load(self, end: date) -> list[ReplayBar]:
        start = self.start
        if start is None:
            start = end - timedelta(days=max(7, self.max_stale_days + 4))
        return load_daily_bars(
            self.symbols,
            start,
            end,
            cache_dir=self.cache_dir,
        )

    def __iter__(self) -> Iterator[Tick]:
        idle = 0
        while True:
            end = self._completed_session()
            bars = self._load(end)
            if not bars:
                raise BarDataError(
                    "live-paper cache is stale: no completed bars in the "
                    "lookback window"
                )
            latest = max(b.session for b in bars)
            age = (end - latest).days
            if age > self.max_stale_days:
                raise BarDataError(
                    f"live-paper data is stale: newest cached session "
                    f"{latest.isoformat()} is {age}d old "
                    f"(limit {self.max_stale_days}d)"
                )
            fresh = [b for b in bars if (b.symbol, b.start.isoformat()) not in self._yielded]
            fresh.sort(key=lambda b: (b.start, b.symbol))
            if fresh:
                idle = 0
                by_session: dict[date, list[ReplayBar]] = {}
                for bar in fresh:
                    by_session.setdefault(bar.session, []).append(bar)
                for session in sorted(by_session):
                    ticks: list[Tick] = []
                    for bar in by_session[session]:
                        self._yielded.add((bar.symbol, bar.start.isoformat()))
                        ticks.extend(
                            generate_ticks(
                                bar,
                                ticks_per_bar=self.ticks_per_bar,
                                seed=self.seed,
                                spread_bps=self.spread_bps,
                            )
                        )
                    ticks.sort(key=lambda t: (t.ts, t.symbol))
                    previous: datetime | None = None
                    for tick in ticks:
                        if previous is not None and tick.ts <= previous:
                            tick = replace(tick, ts=previous + timedelta(microseconds=1))
                        previous = tick.ts
                        yield tick
                continue
            idle += 1
            if self.max_idle_polls is not None and idle >= self.max_idle_polls:
                return
            if self.poll_seconds > 0:
                wall_time.sleep(self.poll_seconds)


def _is_half_day(day: date) -> bool:
    """Local half-day rule; avoids importing the pandas-backed live calendar."""
    if (day.month, day.day) in {(7, 3), (12, 24)}:
        return True
    if (day.month, day.day) == (11, 27) and day.weekday() == 3:  # Thanksgiving Fri
        return True
    if day.month == 12 and day.day == 26 and day.weekday() == 0:
        return True
    return False
