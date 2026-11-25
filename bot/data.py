"""Yahoo Finance daily bars, cached per symbol, assembled into one panel."""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
import requests

from . import universe

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "cache"
BARS = CACHE / "bars"
BARS.mkdir(parents=True, exist_ok=True)

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) stonks-bot"}
START = int(pd.Timestamp("2014-01-01", tz="UTC").timestamp())
END = int(pd.Timestamp("2100-01-01", tz="UTC").timestamp())


def _get(symbol: str) -> dict | None:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    params = {
        "period1": START,
        "period2": END,
        "interval": "1d",
        "events": "div,split",
        "includeAdjustedClose": "true",
    }
    for attempt in range(4):
        try:
            r = requests.get(url, params=params, headers=UA, timeout=40)
            if r.status_code == 429:
                time.sleep(2 + attempt * 3)
                continue
            if r.status_code != 200:
                return None
            data = r.json()
            res = data.get("chart", {}).get("result")
            return res[0] if res else None
        except Exception:
            time.sleep(1 + attempt * 2)
    return None


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
    df = df[df["adjclose"] > 0].drop_duplicates("date").sort_values("date")
    return df.reset_index(drop=True)


def fetch_one(symbol: str, force: bool = False) -> pd.DataFrame:
    path = BARS / f"{symbol.replace('/', '_')}.csv"
    if path.exists() and not force:
        try:
            return pd.read_csv(path, parse_dates=["date"])
        except Exception:
            pass
    res = _get(symbol)
    df = _parse(res) if res else pd.DataFrame()
    if not df.empty:
        df.to_csv(path, index=False)
    return df


def fetch_all(
    symbols: list[str], force: bool = False, workers: int = 6
) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    missing: list[str] = []
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fetch_one, s, force): s for s in symbols}
        for fut in as_completed(futs):
            s = futs[fut]
            try:
                df = fut.result()
            except Exception:
                df = pd.DataFrame()
            if df.empty:
                missing.append(s)
            else:
                out[s] = df
            done += 1
            if done % 50 == 0:
                print(
                    f"  bars {done}/{len(symbols)} (missing {len(missing)})", flush=True
                )
    if missing:
        (CACHE / "missing_symbols.txt").write_text("\n".join(sorted(missing)))
        print(
            f"  {len(missing)} symbols with no Yahoo data -> cache/missing_symbols.txt"
        )
    return out


def build_panel(membership: pd.DataFrame, force: bool = False) -> pd.DataFrame:
    """Long panel: date, ticker (canonical Yahoo symbol), adjclose, close, volume."""
    path = CACHE / "panel.parquet"
    if path.exists() and not force:
        return pd.read_parquet(path)
    tickers = sorted(membership["ticker"].unique())
    canonical = {t: universe.canonical(t) for t in tickers}
    symbols = sorted(set(canonical.values()) | {"SPY", "QQQ"})
    print(f"fetching {len(symbols)} symbols from Yahoo...", flush=True)
    frames = fetch_all(symbols, force=force)
    parts = []
    for sym, df in frames.items():
        d = df[["date", "adjclose", "close", "volume"]].copy()
        d["ticker"] = sym
        parts.append(d)
    panel = pd.concat(parts, ignore_index=True)
    panel = panel.drop_duplicates(["date", "ticker"]).sort_values(["date", "ticker"])
    panel.to_parquet(path, index=False)
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
