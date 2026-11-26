"""Yahoo Finance daily bars, cached per symbol, assembled into one panel."""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from . import universe

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "cache"
BARS = CACHE / "bars"
BARS.mkdir(parents=True, exist_ok=True)

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) stonks-bot"}
MISSING = CACHE / "missing.json"
_MISSING: dict[str, str] | None = None
_MISSING_LOCK = threading.Lock()
START = int(pd.Timestamp("2014-01-01", tz="UTC").timestamp())
END = int(pd.Timestamp("2100-01-01", tz="UTC").timestamp())


class DataFetchError(RuntimeError):
    """Yahoo was unreachable or rate-limited; do not mark the symbol dead."""


def _tmp_path(path: Path) -> Path:
    return path.with_name(f"{path.stem}.{os.getpid()}.tmp{path.suffix}")


def _write_csv(df: pd.DataFrame, path: Path) -> None:
    tmp = _tmp_path(path)
    df.to_csv(tmp, index=False)
    os.replace(tmp, path)


def _write_parquet(df: pd.DataFrame, path: Path) -> None:
    tmp = _tmp_path(path)
    df.to_parquet(tmp, index=False)
    os.replace(tmp, path)


def _write_text(path: Path, text: str) -> None:
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def _get(symbol: str, start: int = START) -> dict | None:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    params = {
        "period1": start,
        "period2": END,
        "interval": "1d",
        "events": "div,split",
        "includeAdjustedClose": "true",
    }
    for attempt in range(4):
        try:
            r = requests.get(url, params=params, headers=UA, timeout=40)
            if r.status_code == 404:
                return None  # Yahoo confirms there is no such history
            if r.status_code in (403, 408, 429) or r.status_code >= 500:
                time.sleep(2 + attempt * 3)
                continue
            if r.status_code != 200:
                return None
            data = r.json()
            res = data.get("chart", {}).get("result")
            return res[0] if res else None
        except Exception:
            time.sleep(1 + attempt * 2)
    raise DataFetchError(f"{symbol}: Yahoo unavailable after retries")


def _parse(res: dict) -> pd.DataFrame:
    ts = res.get("timestamp") or []
    quote = (res.get("indicators", {}).get("quote") or [{}])[0]
    adj = (res.get("indicators", {}).get("adjclose") or [{}])[0].get("adjclose")
    close = quote.get("close")
    if not ts or close is None:
        return pd.DataFrame()
    n = len(ts)
    if adj is None or len(adj) != n:
        adj = close
    df = pd.DataFrame(
        {
            "date": pd.to_datetime(ts, unit="s", utc=True)
            .tz_convert("America/New_York")
            .normalize()
            .tz_localize(None),
            "open": quote.get("open", [None] * n),
            "high": quote.get("high", [None] * n),
            "low": quote.get("low", [None] * n),
            "close": close,
            "adjclose": adj,
            "volume": quote.get("volume", [None] * n),
        }
    )
    df = df.dropna(subset=["adjclose", "close"])
    # Both the raw close and adjusted close must be positive. A zero raw close
    # with a positive adjusted close would make the adjusted OHLC factor infinite
    # in _panel_rows.
    df = df[(df["adjclose"] > 0) & (df["close"] > 0)]
    df = df.drop_duplicates("date").sort_values("date")
    return df.reset_index(drop=True)


def target_session(now: datetime | None = None) -> pd.Timestamp:
    """Last completed US session using the actual close and NYSE holidays.

    The 16:30 delay is for a normal session; a half-day is complete at 13:30 ET.
    Holidays never become a target date. This delegates to the shared live
    calendar at call time (avoiding an import cycle while ``bot.data`` initialises)
    and falls back to the old business-day rule only if that module is unavailable.
    """
    try:
        from .live import calendar

        return calendar.target_session(now)
    except Exception:  # pragma: no cover - defensive fallback for standalone data use
        et = (now or datetime.now(timezone.utc)).astimezone(
            ZoneInfo("America/New_York")
        )
        d = pd.Timestamp(et.date())
        if d.weekday() >= 5 or et.hour < 16 or (et.hour == 16 and et.minute < 30):
            d -= pd.offsets.BDay(1)
        return d


def _last_date(path: Path, target: pd.Timestamp | None = None) -> pd.Timestamp | None:
    try:
        df = pd.read_csv(path, usecols=["date"])
        dates = pd.to_datetime(df["date"])
        if target is not None:
            dates = dates[dates <= target]
        return dates.max().normalize() if len(dates) else None
    except Exception:
        return None


def _missing() -> dict[str, str]:
    global _MISSING
    if _MISSING is None:
        loaded = False
        if MISSING.exists():
            try:
                _MISSING = json.loads(MISSING.read_text())
                loaded = True
            except Exception:
                _MISSING = {}
        else:
            _MISSING = {}
        if not loaded:
            # Only import the legacy text file when the JSON state is absent or
            # unreadable. An intentionally empty JSON means "no known dead names"
            # and must not be refilled with old dates.
            txt = CACHE / "missing_symbols.txt"
            if txt.exists():
                today = target_session().date().isoformat()
                for sym in txt.read_text().splitlines():
                    if sym.strip():
                        _MISSING[sym.strip()] = today
                _save_missing()
    return _MISSING


def _save_missing() -> None:
    global _MISSING
    if _MISSING is None:
        return
    with _MISSING_LOCK:
        snapshot = dict(_MISSING)
        _write_text(MISSING, json.dumps(snapshot, indent=0, sort_keys=True))


def _recent_missing(symbol: str, days: int = 7) -> bool:
    last = _missing().get(symbol)
    if not last:
        return False
    try:
        return (target_session() - pd.Timestamp(last)).days < days
    except Exception:
        return False


def _panel_rows(symbol: str, df: pd.DataFrame) -> pd.DataFrame:
    d = df[["date", "adjclose", "close", "volume"]].copy()
    # adjust OHLC by the same split/dividend factor as adjclose
    if "open" in df.columns:
        d["open"] = df["open"] * df["adjclose"] / df["close"]
    else:
        d["open"] = df["adjclose"]
    for col in ("high", "low"):
        if col in df.columns:
            d[col] = df[col] * df["adjclose"] / df["close"]
        else:
            d[col] = df["adjclose"]
    d["ticker"] = symbol
    return d


def _since_start(since: pd.Timestamp) -> int:
    """Fetch from a few days before the cached date, in New York time."""
    ts = pd.Timestamp(since)
    if ts.tzinfo is None:
        ts = ts.tz_localize("America/New_York")
    return int((ts - pd.Timedelta(days=5)).timestamp())


def _overlap_changed(old: pd.DataFrame, new: pd.DataFrame, tol: float = 1e-5) -> bool:
    """True when Yahoo restated any cached price/volume field on the overlap.

    Adjusted close is the usual split/dividend signal, but a corrected open,
    high, low or volume outside the recent window is also a restatement. If we
    only compare close/adjclose, the incremental merge can leave old OHLCV values
    in place forever.
    """
    if old.empty or new.empty:
        return False
    common = set(old["date"]) & set(new["date"])
    if not common:
        return False
    a = old[old["date"].isin(common)].set_index("date")
    b = new[new["date"].isin(common)].set_index("date")
    for col in ("open", "high", "low", "close", "adjclose", "volume"):
        if col not in a.columns or col not in b.columns:
            continue
        av = pd.to_numeric(a[col], errors="coerce")
        bv = pd.to_numeric(b[col], errors="coerce")
        if col == "volume":
            if not np.allclose(av, bv, rtol=1e-9, atol=1.0, equal_nan=True):
                return True
        else:
            ratio = bv / av
            if ((ratio - 1.0).abs() > tol).any():
                return True
    return False


def _bars_equal(a: pd.DataFrame, b: pd.DataFrame) -> bool:
    """Full OHLCV/date comparison with float tolerance, for write decisions."""
    if len(a) != len(b):
        return False
    a = a.sort_values("date").reset_index(drop=True)
    b = b.sort_values("date").reset_index(drop=True)
    if not a["date"].equals(b["date"]):
        return False
    for col in ("open", "high", "low", "close", "adjclose", "volume"):
        if col not in a.columns or col not in b.columns:
            continue
        if not np.allclose(
            a[col].astype(float).to_numpy(),
            b[col].astype(float).to_numpy(),
            rtol=1e-9,
            atol=1e-9,
            equal_nan=True,
        ):
            return False
    return True


def _panel_equal(a: pd.DataFrame, b: pd.DataFrame) -> bool:
    """Compare panel slices for write decisions, including restatements."""
    if len(a) != len(b):
        return False
    a = a.sort_values(["date", "ticker"]).reset_index(drop=True)
    b = b.sort_values(["date", "ticker"]).reset_index(drop=True)
    if not a[["date", "ticker"]].equals(b[["date", "ticker"]]):
        return False
    for col in ("open", "high", "low", "close", "adjclose", "volume"):
        if col not in a.columns or col not in b.columns:
            continue
        if not np.allclose(
            a[col].astype(float).to_numpy(),
            b[col].astype(float).to_numpy(),
            rtol=1e-9,
            atol=1e-9,
            equal_nan=True,
        ):
            return False
    return True


def fetch_one(
    symbol: str,
    force: bool = False,
    since: pd.Timestamp | None = None,
    target: pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Cached bars. With `since`, fetch only the recent window and merge.

    Dead tickers are retried at most once every 7 days. A daily bar is only
    kept through the last completed US session, so an intraday partial bar can
    never poison the cache. If a split or dividend restated the overlap, the
    symbol is refetched once from START so old and new rows stay comparable.
    `target` pins the session so one long update cannot mix two days.
    """
    path = BARS / f"{symbol.replace('/', '_')}.csv"
    target = pd.Timestamp(target) if target is not None else target_session()
    cached = None
    if path.exists():
        try:
            cached = pd.read_csv(path, parse_dates=["date"])
            cached = cached[cached["date"] <= target]
        except Exception:
            cached = None
    if cached is not None and not force and since is None:
        return cached
    if cached is None and not force and _recent_missing(symbol):
        return pd.DataFrame()
    incremental = since is not None and not force and cached is not None
    start = _since_start(since) if incremental else START
    try:
        res = _get(symbol, start=start)
    except DataFetchError:
        if cached is not None:
            return cached
        raise
    df = _parse(res) if res else pd.DataFrame()
    if df.empty:
        if cached is None:
            _missing()[symbol] = target.date().isoformat()
            _save_missing()
        return cached if cached is not None else df
    restated = False
    if incremental and _overlap_changed(cached, df):
        try:
            full = _get(symbol, start=START)
        except DataFetchError:
            return cached
        full_df = _parse(full) if full else pd.DataFrame()
        if (
            full_df.empty
            or full_df["date"].min() > cached["date"].min()
            or full_df["date"].max() < cached["date"].max()
        ):
            # Never splice restated recent bars onto old history, and never let
            # a truncated/partial "full" response delete cached history.
            return cached
        df = full_df
        restated = True
    _missing().pop(symbol, None)
    df = df[df["date"] <= target]
    if incremental and not restated:
        # New values win on overlapping dates; old cached rows fill only dates
        # the recent fetch did not return.
        df = (
            pd.concat([df, cached])
            .drop_duplicates("date", keep="first")
            .sort_values("date")
            .reset_index(drop=True)
        )
    changed = force or cached is None or restated or not _bars_equal(df, cached)
    if changed:
        _write_csv(df, path)
    return df


def _empty_missing(symbol: str) -> None:
    _missing()[symbol] = target_session().date().isoformat()


def fetch_all(
    symbols: list[str],
    force: bool = False,
    workers: int = 6,
    target: pd.Timestamp | None = None,
) -> dict[str, pd.DataFrame]:
    target = pd.Timestamp(target) if target is not None else target_session()
    # Do not resubmit names that are still inside their backoff window. That
    # used to reset the clock because fetch_one returned empty without trying.
    queue = [
        s
        for s in symbols
        if force
        or (BARS / f"{s.replace('/', '_')}.csv").exists()
        or not _recent_missing(s)
    ]
    out: dict[str, pd.DataFrame] = {}
    missing: list[str] = []
    failed: list[str] = []
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fetch_one, s, force, None, target): s for s in queue}
        for fut in as_completed(futs):
            s = futs[fut]
            try:
                df = fut.result()
            except DataFetchError:
                failed.append(s)
                continue
            except Exception:
                failed.append(s)
                continue
            if df.empty:
                missing.append(s)
                _empty_missing(s)
            else:
                out[s] = df
            done += 1
            if done % 100 == 0:
                print(
                    f"  bars {done}/{len(queue)} (missing {len(missing)}, "
                    f"failed {len(failed)})",
                    flush=True,
                )
    _save_missing()
    if missing:
        _write_text(CACHE / "missing_symbols.txt", "\n".join(sorted(missing)))
        print(f"  {len(missing)} symbols with no Yahoo data; backoff 7 days")
    if failed:
        print(f"  {len(failed)} transient Yahoo failures; retry next run")
    return out


def update_symbols(
    symbols: list[str], workers: int = 6, gate: str = "SPY"
) -> dict[str, pd.DataFrame]:
    """Incremental bars for a static universe; no panel write.

    `gate` is the market's benchmark. It is checked first: if it has no bar for
    the target session (holiday, early close, provider lag), the run stops after
    one request instead of probing every symbol.
    """
    target = target_session()
    symbols = list(dict.fromkeys(symbols))
    frames: dict[str, pd.DataFrame] = {}
    attempted = 0
    if gate in symbols:
        path = BARS / f"{gate.replace('/', '_')}.csv"
        last = _last_date(path, target) if path.exists() else None
        if last is None or last < target:
            if last is not None or not _recent_missing(gate):
                attempted += 1
                failed = False
                try:
                    df = fetch_one(gate, since=last, target=target)
                except DataFetchError:
                    df, failed = pd.DataFrame(), True
                if not df.empty:
                    frames[gate] = df
                elif not failed:
                    _empty_missing(gate)
            last = _last_date(path, target) if path.exists() else None
        if last is None or last < target:
            _save_missing()
            print(
                f"data: {gate} has no bar for {target.date()} "
                f"(closed or not published); no other fetch"
            )
            return frames
    for sym in symbols:
        if sym == gate:
            continue
        path = BARS / f"{sym.replace('/', '_')}.csv"
        last = _last_date(path, target) if path.exists() else None
        if last is not None and last >= target:
            continue
        if last is None and _recent_missing(sym):
            continue
        attempted += 1
        try:
            df = fetch_one(sym, since=last, target=target)
        except DataFetchError:
            continue
        if not df.empty:
            frames[sym] = df
        else:
            _empty_missing(sym)
    _save_missing()
    print(
        f"data: {len(symbols)} symbols, {attempted} fetched, "
        f"{len(symbols) - attempted} cached/skipped @ {target.date()}"
    )
    return frames


def update_panel(membership: pd.DataFrame, workers: int = 12) -> pd.DataFrame:
    """Incremental membership panel: only stale symbols, keep old history."""
    path = CACHE / "panel.parquet"
    target = target_session()
    if not path.exists():
        return build_panel(membership, force=True, workers=workers)
    raw = pd.read_parquet(path)
    existing = raw[raw["date"] <= target] if not raw.empty else raw
    trimmed = len(existing) != len(raw)
    panel_last = existing["date"].max() if not existing.empty else None
    tickers = sorted(membership["ticker"].unique())
    canonical = {t: universe.canonical(t) for t in tickers}
    symbols = sorted(set(canonical.values()) | {"SPY", "QQQ", "SOXX", "SMH"})
    # A removed member only needs bars through its last membership date. Without
    # this, every delisted ticker is retried every day forever.
    snapshot = membership["date"].max()
    current = set(membership.loc[membership["date"] == snapshot, "ticker"])
    end = membership.groupby("ticker")["date"].max()
    need: dict[str, pd.Timestamp] = {}
    for ticker, last_member in end.items():
        sym = canonical[ticker]
        wanted = target if ticker in current else min(target, pd.Timestamp(last_member))
        if sym not in need or wanted > need[sym]:
            need[sym] = wanted
    known = set(existing["ticker"].unique())
    ticker_panel_last = (
        existing.groupby("ticker")["date"].max()
        if not existing.empty
        else pd.Series(dtype="datetime64[ns]")
    )
    frames: dict[str, pd.DataFrame] = {}
    attempted = 0
    # One benchmark check decides whether the session exists at all. Holidays
    # and unpublished closes stop here instead of probing the full member list.
    gate = BARS / "SPY.csv"
    last = _last_date(gate, target) if gate.exists() else None
    if last is None or last < target:
        if last is not None or not _recent_missing("SPY"):
            attempted += 1
            failed = False
            try:
                df = fetch_one("SPY", since=last, target=target)
            except DataFetchError:
                df, failed = pd.DataFrame(), True
            if not df.empty:
                frames["SPY"] = df
            elif not failed:
                _empty_missing("SPY")
        last = _last_date(gate, target) if gate.exists() else None
    if last is None or last < target:
        _save_missing()
        print(
            f"data: SPY has no bar for {target.date()} "
            f"(closed or not published); panel held at "
            f"{panel_last.date() if panel_last is not None else 'empty'}"
        )
        return existing
    spy_panel_last = ticker_panel_last.get("SPY")
    if "SPY" not in frames and (
        "SPY" not in known or spy_panel_last is None or last > spy_panel_last
    ):
        frames["SPY"] = fetch_one("SPY", target=target)  # cached, no network
    for sym in symbols:
        if sym == "SPY":
            continue
        wanted = need.get(sym, target)
        p = BARS / f"{sym.replace('/', '_')}.csv"
        last = _last_date(p, target) if p.exists() else None
        panel_ticker_last = ticker_panel_last.get(sym)
        if last is not None and last >= wanted:
            if (
                sym not in known
                or panel_ticker_last is None
                or last > panel_ticker_last
            ):
                frames[sym] = fetch_one(sym, target=target)  # cached, no network
            continue
        if last is not None and wanted < target and _recent_missing(sym):
            continue  # stale delisted name still in backoff
        if last is None and wanted < target and (target - wanted).days > 30:
            continue  # delisted long ago and Yahoo has no history: not retryable
        if last is None and _recent_missing(sym):
            continue
        attempted += 1
        failed = False
        try:
            df = fetch_one(sym, since=last, target=target)
        except DataFetchError:
            df, failed = pd.DataFrame(), True
        if not df.empty:
            frames[sym] = df
            if wanted < target and _last_date(p, target) < wanted:
                _empty_missing(sym)  # delisted and no bar through removal
        elif not failed:
            _empty_missing(sym)
    _save_missing()
    if not frames:
        if trimmed:
            _write_parquet(existing, path)
        print(
            f"data: panel already at {target.date()}; {attempted} requested, "
            f"{'trimmed' if trimmed else 'no write'}"
        )
        return existing
    new = pd.concat([_panel_rows(s, df) for s, df in frames.items()], ignore_index=True)
    keep = existing[~existing["ticker"].isin(frames)]
    panel = (
        pd.concat([keep, new], ignore_index=True)
        .drop_duplicates(["date", "ticker"])
        .sort_values(["date", "ticker"])
    )
    same_shape = (
        len(panel) == len(existing) and panel["date"].max() == existing["date"].max()
    )
    if same_shape:
        old_f = existing[existing["ticker"].isin(frames)]
        new_f = panel[panel["ticker"].isin(frames)]
        if _panel_equal(old_f, new_f):
            print(
                f"data: {len(frames)} symbols checked ({attempted} requested), "
                "no new bars; panel unchanged"
            )
            return existing
    _write_parquet(panel, path)
    print(
        f"data: updated {len(frames)} symbols ({attempted} requested); panel "
        f"{panel['ticker'].nunique()} tickers, {panel['date'].nunique()} dates, "
        f"last {panel['date'].max().date()}"
    )
    return panel


def build_panel(
    membership: pd.DataFrame, force: bool = False, workers: int = 6
) -> pd.DataFrame:
    """Long panel: date, ticker (canonical Yahoo symbol), adjclose, close, volume."""
    path = CACHE / "panel.parquet"
    target = target_session()
    needed = {"SPY", "QQQ", "SOXX", "SMH"}
    if path.exists() and not force:
        cached = pd.read_parquet(path)
        if "open" in cached.columns and needed <= set(cached.ticker.unique()):
            return cached[cached["date"] <= target]
    tickers = sorted(membership["ticker"].unique())
    canonical = {t: universe.canonical(t) for t in tickers}
    symbols = sorted(set(canonical.values()) | needed)
    print(f"fetching {len(symbols)} symbols from Yahoo...", flush=True)
    frames = fetch_all(symbols, force=force, workers=workers, target=target)
    parts = [_panel_rows(sym, df) for sym, df in frames.items()]
    panel = pd.concat(parts, ignore_index=True)
    panel = panel[panel["date"] <= target]
    panel = panel.drop_duplicates(["date", "ticker"]).sort_values(["date", "ticker"])
    _write_parquet(panel, path)
    print(
        f"panel: {panel['ticker'].nunique()} tickers, {panel['date'].nunique()} dates"
    )
    return panel


def wide(panel: pd.DataFrame, field: str) -> pd.DataFrame:
    return panel.pivot(index="date", columns="ticker", values=field).sort_index()


def intraday(
    symbols: list[str], interval: str = "1m", period: str = "5d", workers: int = 6
) -> dict[str, pd.DataFrame]:
    """Minute bars in New York time, keyed by symbol. Empty frame on failure."""

    def one(s: str) -> tuple[str, pd.DataFrame]:
        try:
            r = requests.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{s}",
                params={
                    "range": period,
                    "interval": interval,
                    "includePrePost": "false",
                    "events": "div,split",
                },
                headers=UA,
                timeout=30,
            )
            if r.status_code != 200:
                return s, pd.DataFrame()
            res = r.json().get("chart", {}).get("result")
            if not res:
                return s, pd.DataFrame()
            ts = pd.to_datetime(res[0]["timestamp"], unit="s", utc=True).tz_convert(
                "America/New_York"
            )
            close = (res[0].get("indicators", {}).get("quote") or [{}])[0].get("close")
            if close is None:
                return s, pd.DataFrame()
            df = pd.DataFrame({"ts": ts, "close": close}).dropna()
            return s, df.sort_values("ts").reset_index(drop=True)
        except Exception:
            return s, pd.DataFrame()

    out: dict[str, pd.DataFrame] = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for s, df in ex.map(one, symbols):
            out[s] = df
    return out


def latest_prices(symbols: list[str], workers: int = 4) -> dict[str, float]:
    """Last intraday/minute price per symbol, for the paper monitor only."""

    def one(s: str) -> tuple[str, float | None]:
        try:
            r = requests.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{s}",
                params={"range": "1d", "interval": "1m"},
                headers=UA,
                timeout=15,
            )
            if r.status_code != 200:
                return s, None
            res = r.json().get("chart", {}).get("result")
            if not res:
                return s, None
            q = (res[0].get("indicators", {}).get("quote") or [{}])[0]
            closes = [x for x in (q.get("close") or []) if x is not None]
            return s, float(closes[-1]) if closes else None
        except Exception:
            return s, None

    out = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for s, p in ex.map(one, symbols):
            if p is not None:
                out[s] = p
    return out
