"""Point-in-time SEC 8-K item 2.02 events and no-lookahead timing.

The event is known at the SEC acceptance timestamp in New York. Entry is the
first session open strictly after that knowledge. A filing without an
acceptance timestamp is treated as known at 16:00 ET on the filing date, so
it can never be bought at the filing-day close.
"""

from __future__ import annotations

import json
from datetime import time as dtime
from pathlib import Path

import numpy as np
import pandas as pd

from bot.news import _accepted_et
from bot.research.data import read_bars

ROOT = Path(__file__).resolve().parents[3]
NEWS = ROOT / "cache" / "news"
BARS = ROOT / "cache" / "bars"

EVENT_COLUMNS = [
    "ticker",
    "cik",
    "accession",
    "filed",
    "accepted",
    "event_time",
    "items",
    "timestamp_source",
]

TIMING_COLUMNS = [
    "ticker",
    "accession",
    "filed",
    "event_time",
    "entry_date",
    "entry_idx",
    "entry_open",
    "prev_close",
    "gap",
    "close_close",
    "open_close",
    "volume_z",
    "timestamp_source",
]


def load_events(
    news_dir: str | Path | None = None,
    *,
    tickers: set[str] | None = None,
    start: str | pd.Timestamp = "2014-01-01",
    end: str | pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Load and de-duplicate item 2.02 events from the committed cache."""
    root = Path(news_dir) if news_dir is not None else NEWS
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end) if end is not None else None
    rows: list[dict] = []
    for path in sorted(root.glob("sub_*.json")):
        try:
            data = json.loads(path.read_text())
        except Exception:
            continue
        raw_tickers = data.get("tickers") or []
        ticker = str(raw_tickers[0]).upper() if raw_tickers else ""
        if not ticker or (tickers is not None and ticker not in tickers):
            continue
        recent = data.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        items = recent.get("items", [])
        filed = recent.get("filingDate", [])
        accepted = recent.get("acceptanceDateTime", [])
        accessions = recent.get("accessionNumber", [])
        for i, form in enumerate(forms):
            if form != "8-K":
                continue
            tags = {
                x.strip()
                for x in str(items[i] if i < len(items) else "").split(",")
                if x.strip()
            }
            if "2.02" not in tags:
                continue
            if i >= len(filed) or not filed[i]:
                continue
            filed_ts = pd.Timestamp(filed[i]).normalize()
            accepted_raw = str(accepted[i] if i < len(accepted) else "")
            accepted_et = _accepted_et(accepted_raw)
            event_time = (
                accepted_et
                if accepted_et is not None
                else filed_ts + pd.Timedelta(hours=16)
            )
            if event_time < start_ts:
                continue
            if end_ts is not None and event_time > end_ts:
                continue
            rows.append(
                {
                    "ticker": ticker,
                    "cik": str(data.get("cik", "")),
                    "accession": str(accessions[i] if i < len(accessions) else ""),
                    "filed": filed_ts,
                    "accepted": accepted_raw,
                    "event_time": pd.Timestamp(event_time),
                    "items": ",".join(sorted(tags)),
                    "timestamp_source": (
                        "accepted" if accepted_et is not None else "filed_close"
                    ),
                }
            )
    if not rows:
        return pd.DataFrame(columns=EVENT_COLUMNS)
    df = pd.DataFrame(rows)
    # Exact duplicate accessions are data errors. Distinct companion 2.02
    # filings within 24 hours are one earnings event.
    df = df.drop_duplicates(["ticker", "accession"]).sort_values(
        ["ticker", "event_time", "accession"]
    )
    keep: list[int] = []
    last: dict[str, pd.Timestamp] = {}
    for idx, row in df.iterrows():
        prev = last.get(row["ticker"])
        if prev is not None and row["event_time"] - prev < pd.Timedelta(hours=24):
            continue
        keep.append(idx)
        last[row["ticker"]] = row["event_time"]
    return (
        df.loc[keep]
        .sort_values(["event_time", "ticker"])
        .reset_index(drop=True)[EVENT_COLUMNS]
    )


def load_bars(
    tickers: list[str] | tuple[str, ...] | set[str],
    *,
    cache_dir: str | Path | None = None,
) -> dict[str, pd.DataFrame]:
    """Cached daily bars, indexed by date. Missing symbols are absent."""
    out: dict[str, pd.DataFrame] = {}
    for ticker in sorted(set(tickers)):
        path = Path(cache_dir or BARS) / f"{ticker}.csv"
        if not path.exists():
            continue
        try:
            df = read_bars(ticker, Path(cache_dir) if cache_dir else None)
        except Exception:
            continue
        if df.empty:
            continue
        frame = df.set_index("date").sort_index()
        frame["close"] = frame["adjclose"]
        # read_bars already adjusts open/high/low with the adjclose factor.
        out[ticker] = frame[["open", "high", "low", "close", "adjclose", "volume"]]
    return out


def entry_index(bars: pd.DataFrame, event_time: pd.Timestamp) -> int:
    """First session open strictly after the event is known.

    Before 09:30 ET on a session, that session's open is still in the future;
    at or after 09:30 the open has passed. Missing/non-session dates roll to
    the next cached session.
    """
    dates = bars.index
    event_time = pd.Timestamp(event_time)
    same = int(dates.get_indexer([event_time.normalize()])[0])
    if same >= 0 and event_time.time() < dtime(9, 30):
        return same
    return int(dates.searchsorted(event_time.normalize(), side="right"))


def attach_timing(
    events: pd.DataFrame,
    bars: dict[str, pd.DataFrame],
    *,
    start: str | pd.Timestamp = "2014-01-01",
    end: str | pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Attach entry index and reaction measures observable at entry."""
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end) if end is not None else None
    rows: list[dict] = []
    for row in events.itertuples(index=False):
        frame = bars.get(row.ticker)
        if frame is None or len(frame) < 3:
            continue
        idx = entry_index(frame, row.event_time)
        if idx <= 0 or idx >= len(frame):
            continue
        entry_date = frame.index[idx]
        if entry_date < start_ts:
            continue
        if end_ts is not None and entry_date > end_ts:
            continue
        p = idx - 1
        prev_close = float(frame["close"].iloc[p])
        entry_open = float(frame["open"].iloc[idx])
        gap = entry_open / prev_close - 1.0
        close_close = (
            float(frame["close"].iloc[p] / frame["close"].iloc[p - 1] - 1.0)
            if p >= 1
            else np.nan
        )
        open_close = (
            float(frame["close"].iloc[p] / frame["open"].iloc[p] - 1.0)
            if p >= 0
            else np.nan
        )
        volume_z = np.nan
        if p >= 21:
            hist = frame["volume"].iloc[p - 20 : p].astype(float)
            sd = float(hist.std(ddof=1))
            if sd > 0:
                volume_z = float((float(frame["volume"].iloc[p]) - hist.mean()) / sd)
        rows.append(
            {
                "ticker": row.ticker,
                "accession": row.accession,
                "filed": row.filed,
                "event_time": row.event_time,
                "entry_date": entry_date,
                "entry_idx": int(idx),
                "entry_open": entry_open,
                "prev_close": prev_close,
                "gap": gap,
                "close_close": close_close,
                "open_close": open_close,
                "volume_z": volume_z,
                "timestamp_source": row.timestamp_source,
            }
        )
    if not rows:
        return pd.DataFrame(columns=TIMING_COLUMNS)
    return (
        pd.DataFrame(rows).sort_values(["entry_date", "ticker"]).reset_index(drop=True)
    )


def reaction_mask(frame: pd.DataFrame, kind: str) -> pd.Series:
    """Boolean mask for a pre-registered reaction definition."""
    if kind == "gap":
        return frame["gap"] > 0
    if kind == "close_close":
        return frame["close_close"] > 0
    if kind == "open_close":
        return frame["open_close"] > 0
    if kind == "volume_z":
        return (frame["gap"] > 0) & (frame["volume_z"] > 1.0)
    if kind == "all_gap":
        return np.isfinite(frame["gap"])
    raise ValueError(f"unknown reaction: {kind}")


def eligible(
    timing: pd.DataFrame,
    bars: dict[str, pd.DataFrame],
    *,
    hold: int,
    reaction: str,
) -> pd.DataFrame:
    """Rows with the reaction, enough history and a complete holding period."""
    if hold < 1:
        raise ValueError("hold must be >= 1")
    mask = reaction_mask(timing, reaction)
    out = []
    for row in timing.loc[mask].itertuples(index=False):
        frame = bars.get(row.ticker)
        if frame is None:
            continue
        if row.entry_idx + hold - 1 >= len(frame):
            continue
        if not np.isfinite(row.entry_open) or not np.isfinite(row.prev_close):
            continue
        out.append(row)
    if not out:
        return timing.iloc[0:0].copy()
    return pd.DataFrame(out).reset_index(drop=True)
