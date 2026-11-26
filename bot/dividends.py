"""Dividend events from Yahoo, cached per symbol.

The backtest engine uses Yahoo adjusted closes, which reinvest gross dividends.
This module only supplies the event stream needed to charge dividend tax and
treat a distribution as cash instead of reinvestment. It does **not** rewrite
the price panel and it never fetches bars for the accounting replay.

Yahoo chart ``events=div`` amounts are already expressed on the current split
basis (the same basis as the adjusted-close share counts used by the engine).
Do not multiply them by historical split factors again.

Cache layout::

    cache/dividends/<safe-symbol>.csv      # ex_date, amount, currency
    cache/dividends/<safe-symbol>.json     # fetch metadata

Network is only touched by :func:`fetch_dividends`; :func:`load_dividends`
reads the cache when ``fetch_missing=False``. Tests use synthetic event dicts
and a temporary cache directory.
"""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from . import data

CACHE = data.CACHE / "dividends"
FIELDS = ("date", "amount", "currency")
DEFAULT_CURRENCY = "USD"


def safe_symbol(symbol: str) -> str:
    return str(symbol).replace("/", "_")


def dividend_path(symbol: str, cache: Path | None = None) -> Path:
    root = CACHE if cache is None else cache
    return root / f"{safe_symbol(symbol)}.csv"


def _metadata_path(symbol: str, cache: Path | None = None) -> Path:
    root = CACHE if cache is None else cache
    return root / f"{safe_symbol(symbol)}.json"


def _event_date(value) -> pd.Timestamp | None:
    if value is None:
        return None
    try:
        if isinstance(value, (int, float, np.integer, np.floating)):
            ts = pd.to_datetime(float(value), unit="s", utc=True)
            return ts.tz_convert("America/New_York").normalize().tz_localize(None)
        return pd.Timestamp(value).normalize()
    except (TypeError, ValueError, OverflowError):
        return None


def parse_events(result: dict | None, symbol: str | None = None) -> pd.DataFrame:
    """Pure parser for a Yahoo chart result's ``events.dividends`` map.

    Accepts both the normal ``{"<ts>": {"amount": ..., "date": ...}}`` map and
    a simple list of records. Zero/negative/non-finite amounts are ignored;
    duplicate ex-dates keep the last record. Currency comes from ``meta``.
    """
    empty = pd.DataFrame(columns=list(FIELDS))
    if not isinstance(result, dict):
        return empty
    events = result.get("events") or {}
    raw = events.get("dividends") or {}
    if not raw:
        return empty
    if isinstance(raw, dict):
        items = list(raw.items())
    elif isinstance(raw, list):
        items = [(None, row) for row in raw]
    else:
        return empty
    meta = result.get("meta") or {}
    default_currency = str(meta.get("currency") or DEFAULT_CURRENCY)
    rows: list[dict] = []
    for key, item in items:
        if isinstance(item, (int, float, np.integer, np.floating)):
            amount, stamp = float(item), key
        elif isinstance(item, dict):
            amount = item.get("amount")
            stamp = item.get("date", key)
        else:
            continue
        try:
            amount = float(amount)
        except (TypeError, ValueError):
            continue
        if not np.isfinite(amount) or amount <= 0:
            continue
        date = _event_date(stamp)
        if date is None:
            continue
        currency = (
            str(item.get("currency") or default_currency)
            if isinstance(item, dict)
            else default_currency
        )
        rows.append({"date": date, "amount": amount, "currency": currency})
    if not rows:
        return empty
    out = (
        pd.DataFrame(rows, columns=list(FIELDS))
        .drop_duplicates("date", keep="last")
        .sort_values("date")
        .reset_index(drop=True)
    )
    return out


def read_dividends(symbol: str, cache: Path | None = None) -> pd.DataFrame:
    """Read a cached symbol; a missing/empty cache returns an empty frame."""
    path = dividend_path(symbol, cache)
    empty = pd.DataFrame(columns=list(FIELDS))
    if not path.exists():
        return empty
    try:
        df = pd.read_csv(path, parse_dates=["date"])
    except Exception:
        return empty
    if df.empty or "amount" not in df.columns:
        return empty
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce")
    if "currency" not in df.columns:
        df["currency"] = DEFAULT_CURRENCY
    df = df.dropna(subset=["date", "amount"])
    df = df[np.isfinite(df["amount"]) & (df["amount"] > 0)]
    return df[list(FIELDS)].sort_values("date").reset_index(drop=True)


def _atomic_write(path: Path, data_bytes: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    with open(tmp, "wb") as fh:
        fh.write(data_bytes)
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


def write_dividends(
    symbol: str, frame: pd.DataFrame, cache: Path | None = None
) -> Path:
    """Atomically write a dividend cache file (header-only for no dividends)."""
    out = frame.copy()
    for col in FIELDS:
        if col not in out.columns:
            out[col] = DEFAULT_CURRENCY if col == "currency" else np.nan
    out = out[list(FIELDS)]
    path = dividend_path(symbol, cache)
    _atomic_write(path, out.to_csv(index=False).encode())
    meta = {
        "symbol": symbol,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "rows": int(len(out)),
        "currency": (str(out["currency"].iloc[0]) if len(out) else DEFAULT_CURRENCY),
    }
    _atomic_write(
        _metadata_path(symbol, cache),
        json.dumps(meta, sort_keys=True, indent=1).encode(),
    )
    return path


def fetch_dividends(
    symbol: str,
    force: bool = False,
    cache: Path | None = None,
) -> pd.DataFrame:
    """Return cached events, fetching Yahoo only when the cache is absent.

    ``force=True`` bypasses the cache. A Yahoo failure with a cached file falls
    back to that file. Failures without a cache propagate as ``DataFetchError``
    so the report can list the symbol instead of silently understating tax.
    """
    path = dividend_path(symbol, cache)
    if path.exists() and not force:
        return read_dividends(symbol, cache)
    try:
        result = data._get(symbol)
    except data.DataFetchError:
        if path.exists():
            return read_dividends(symbol, cache)
        raise
    frame = parse_events(result, symbol)
    write_dividends(symbol, frame, cache)
    return frame


def load_dividends(
    symbols: list[str] | tuple[str, ...],
    fetch_missing: bool = True,
    force: bool = False,
    workers: int = 6,
    cache: Path | None = None,
) -> pd.DataFrame:
    """Load a long dividend frame ``date,ticker,amount,currency``.

    Cached symbols are always preferred. ``fetch_missing=False`` is the
    cache-only path used by tests and offline reports; it never touches the
    network. Failed fetches are omitted and reported through ``frame.attrs``.
    """
    empty = pd.DataFrame(columns=["date", "ticker", "amount", "currency"])
    unique = [s for s in dict.fromkeys(map(str, symbols)) if s]
    if not unique:
        return empty
    root = CACHE if cache is None else cache
    frames: dict[str, pd.DataFrame] = {}
    errors: dict[str, str] = {}
    to_fetch: list[str] = []
    for symbol in unique:
        path = dividend_path(symbol, root)
        if path.exists() and not force:
            frames[symbol] = read_dividends(symbol, root)
        elif fetch_missing:
            to_fetch.append(symbol)
    if to_fetch:
        with ThreadPoolExecutor(max_workers=max(1, int(workers))) as pool:
            futures = {
                pool.submit(fetch_dividends, symbol, force, root): symbol
                for symbol in to_fetch
            }
            for fut in as_completed(futures):
                symbol = futures[fut]
                try:
                    frames[symbol] = fut.result()
                except Exception as exc:  # noqa: BLE001 - report, do not abort
                    errors[symbol] = f"{type(exc).__name__}: {exc}"
    parts = []
    for symbol, frame in frames.items():
        if frame is None or frame.empty:
            continue
        part = frame.copy()
        part["ticker"] = symbol
        parts.append(part[["date", "ticker", "amount", "currency"]])
    if not parts:
        empty.attrs["errors"] = errors
        return empty
    out = (
        pd.concat(parts, ignore_index=True)
        .drop_duplicates(["date", "ticker"], keep="last")
        .sort_values(["date", "ticker"])
        .reset_index(drop=True)
    )
    out.attrs["errors"] = errors
    return out


__all__ = [
    "CACHE",
    "FIELDS",
    "dividend_path",
    "fetch_dividends",
    "load_dividends",
    "parse_events",
    "read_dividends",
    "safe_symbol",
    "write_dividends",
]
