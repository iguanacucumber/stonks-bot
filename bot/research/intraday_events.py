"""Intraday 8-K event drift: actual first-bar entries, no same-bar fills.

The cache has 1h/2y and 5m/60d bars for a small set of liquid SEC filers. This
module joins material 8-K acceptance timestamps to the first *completed* bar
strictly after the timestamp, enters at that bar's open, and exits at the
session close, the next bar close, or five sessions later.

The event timestamp is SEC acceptance time in New York (``bot.news._accepted_et``).
A filing with no acceptance timestamp is treated as known after the filing-day
close, so entry is the next session open (never the filing close).
"""

from __future__ import annotations

import json
import time
from datetime import time as dtime
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from bot.news import MATERIAL, _accepted_et
from bot.research.short3.intraday import _tax_equity, load_bars

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "cache" / "intraday"
NEWS = ROOT / "cache" / "news"

# Liquid filers with enough material 8-Ks in the 2y window. Fetch is opt-in and
# limited to a handful; no full-market intraday download.
DEFAULT_SYMBOLS = (
    "AMD",
    "INTC",
    "FDX",
    "FANG",
    "APH",
    "TSLA",
    "AMAT",
    "OXY",
    "LUV",
    "ON",
    "CF",
    "ALB",
)
_UA = {"User-Agent": "Mozilla/5.0"}


def _intraday_path(symbol: str, interval: str, range_: str) -> Path:
    return CACHE / f"{symbol}_{interval}_{range_}.csv"


def fetch_intraday(symbol: str, interval: str, range_: str) -> Path:
    """Fetch one symbol/range from Yahoo and cache it atomically. Opt-in only."""
    path = _intraday_path(symbol, interval, range_)
    if path.exists() and path.stat().st_size > 1000:
        return path
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    params = {
        "range": range_,
        "interval": interval,
        "includePrePost": "false",
        "events": "div,splits",
    }
    r = requests.get(url, params=params, headers=_UA, timeout=30)
    r.raise_for_status()
    res = r.json().get("chart", {}).get("result")
    if not res:
        raise ValueError(f"no intraday data for {symbol} {range_}/{interval}")
    res = res[0]
    ts = res.get("timestamp") or []
    q = (res.get("indicators", {}).get("quote") or [{}])[0]
    n = len(ts)
    if n == 0:
        raise ValueError(f"empty intraday data for {symbol}")
    df = pd.DataFrame(
        {
            "ts": pd.to_datetime(ts, unit="s", utc=True),
            "open": q.get("open", [None] * n),
            "high": q.get("high", [None] * n),
            "low": q.get("low", [None] * n),
            "close": q.get("close", [None] * n),
            "volume": q.get("volume", [None] * n),
        }
    ).dropna(subset=["open", "close"])
    tmp = path.with_name(f"{path.name}.{time.time_ns()}.tmp")
    df.to_csv(tmp, index=False)
    tmp.replace(path)
    return path


def fetch_needed(symbols: tuple[str, ...] = DEFAULT_SYMBOLS) -> list[str]:
    paths = []
    for sym in symbols:
        for range_, interval in (("2y", "1h"), ("60d", "5m")):
            paths.append(str(fetch_intraday(sym, interval, range_)))
    return paths


def load_material_events(
    symbols: tuple[str, ...] = DEFAULT_SYMBOLS,
    start: pd.Timestamp | None = None,
    end: pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Material 8-K events with acceptance time in naive New York time."""
    allowed = {s.upper(): s.upper() for s in symbols}
    rows = []
    for path in sorted(NEWS.glob("sub_*.json")):
        try:
            data = json.loads(path.read_text())
        except Exception:
            continue
        tickers = data.get("tickers") or []
        if not tickers:
            continue
        ticker = str(tickers[0]).upper()
        if ticker not in allowed:
            continue
        recent = data.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        items = recent.get("items", [])
        filed = recent.get("filingDate", [])
        accepted = recent.get("acceptanceDateTime", [])
        for i, form in enumerate(forms):
            if form != "8-K":
                continue
            tags = {
                x.strip()
                for x in str(items[i] if i < len(items) else "").split(",")
                if x.strip()
            }
            if not (tags & MATERIAL):
                continue
            if not filed[i]:
                continue
            filed_ts = pd.Timestamp(filed[i]).normalize()
            if start is not None and filed_ts < pd.Timestamp(start).normalize():
                continue
            if end is not None and filed_ts > pd.Timestamp(end).normalize():
                continue
            accepted_raw = str(accepted[i] if i < len(accepted) else "")
            accepted_et = _accepted_et(accepted_raw)
            # No timestamp: known only after the filing-day close.
            event_time = (
                accepted_et
                if accepted_et is not None
                else filed_ts + pd.Timedelta(hours=16)
            )
            rows.append(
                {
                    "ticker": ticker,
                    "filed": filed_ts,
                    "event_time": pd.Timestamp(event_time),
                    "accepted": accepted_raw,
                    "items": ",".join(sorted(tags & MATERIAL)),
                    "timestamp_source": "accepted"
                    if accepted_et is not None
                    else "filed_close",
                }
            )
    if not rows:
        return pd.DataFrame(
            columns=[
                "ticker",
                "filed",
                "event_time",
                "accepted",
                "items",
                "timestamp_source",
            ]
        )
    df = (
        pd.DataFrame(rows)
        .drop_duplicates(["ticker", "event_time", "items"])
        .sort_values(["event_time", "ticker"])
        .reset_index(drop=True)
    )
    return df


def load_event_bars(
    symbols: tuple[str, ...],
    interval: str,
    range_: str,
    *,
    allow_fetch: bool = False,
) -> dict[str, pd.DataFrame]:
    out = {}
    for sym in symbols:
        path = _intraday_path(sym, interval, range_)
        if not path.exists() and allow_fetch:
            try:
                fetch_intraday(sym, interval, range_)
                time.sleep(0.2)
            except Exception:
                continue
        if not path.exists():
            continue
        try:
            out[sym] = load_bars(sym, interval, range_)
        except Exception:
            continue
    return out


def _session_volumes(bars: pd.DataFrame) -> pd.Series:
    return bars.groupby("session")["volume"].sum().sort_index()


def _daily_closes(bars: pd.DataFrame) -> pd.Series:
    return bars.groupby("session")["close"].last().sort_index()


def _prior_features(bars: pd.DataFrame) -> pd.DataFrame:
    """Ex-ante prior-session volatility and relative volume, shifted by one day."""
    closes = _daily_closes(bars)
    vol = closes.pct_change(fill_method=None).rolling(20, min_periods=10).std()
    vols = _session_volumes(bars)
    rel_vol = vols / vols.rolling(20, min_periods=10).median().shift(1)
    return pd.DataFrame(
        {"prior_vol": vol.shift(1), "prior_rel_volume": rel_vol.shift(1)}
    )


def _entry_bar(bars: pd.DataFrame, event_time: pd.Timestamp) -> int | None:
    """First tradable bar start strictly after the event timestamp.

    The 16:00 print is a closing/auction row, not an entry bar. If the event
    happens in the last hour, entry is the next session's 09:30 bar.
    """
    stamps = pd.DatetimeIndex(bars["ts"])
    pos = int(stamps.searchsorted(pd.Timestamp(event_time), side="right"))
    while pos < len(bars):
        t = bars.iloc[pos]["ts"]
        if pd.Timestamp(t).time() < dtime(16, 0):
            return pos
        pos += 1
    return None


def simulate_event(
    bars: pd.DataFrame,
    event_time: pd.Timestamp,
    *,
    exit_kind: str = "close",
    direction: float = 1.0,
    cost_bps: float = 5.0,
    features: pd.DataFrame | None = None,
) -> dict | None:
    """One event trade. Entry at the first bar open after the timestamp."""
    if exit_kind not in ("close", "next_bar", "5d"):
        raise ValueError(exit_kind)
    pos = _entry_bar(bars, event_time)
    if pos is None:
        return None
    entry = float(bars.iloc[pos]["open"])
    entry_time = pd.Timestamp(bars.iloc[pos]["ts"])
    session = pd.Timestamp(bars.iloc[pos]["session"])
    if entry <= 0 or not np.isfinite(entry):
        return None
    sessions = pd.DatetimeIndex(sorted(bars["session"].unique()))
    sidx = int(sessions.searchsorted(session))
    if exit_kind == "close":
        exit_row = bars[bars["session"] == session].iloc[-1]
        exit_time = pd.Timestamp(exit_row["ts"])
        exit_px = float(exit_row["close"])
    elif exit_kind == "next_bar":
        if pos + 1 >= len(bars):
            return None
        exit_row = bars.iloc[pos + 1]
        exit_time = pd.Timestamp(exit_row["ts"])
        exit_px = float(exit_row["close"])
    else:
        target = sidx + 5
        if target >= len(sessions):
            return None
        exit_row = bars[bars["session"] == sessions[target]].iloc[-1]
        exit_time = pd.Timestamp(exit_row["ts"])
        exit_px = float(exit_row["close"])
    if exit_px <= 0 or not np.isfinite(exit_px) or exit_time <= entry_time:
        return None
    gross = direction * (exit_px / entry - 1.0)
    net = gross - 2.0 * cost_bps / 1e4
    row = {
        "entry_time": entry_time,
        "entry_session": session,
        "exit_time": exit_time,
        "entry": entry,
        "exit": exit_px,
        "gross": float(gross),
        "net": float(net),
        "direction": float(direction),
        "cost_bps": float(cost_bps),
    }
    if features is not None and session in features.index:
        row["prior_vol"] = float(features.at[session, "prior_vol"])
        row["prior_rel_volume"] = float(features.at[session, "prior_rel_volume"])
    return row


def trades_for(
    bars_by_symbol: dict[str, pd.DataFrame],
    events: pd.DataFrame,
    *,
    exit_kind: str = "close",
    direction: float = 1.0,
    cost_bps: float = 5.0,
    vol_filter: str = "none",
) -> pd.DataFrame:
    """All event trades with ex-ante filters; one row per usable event."""
    rows = []
    feature_cache: dict[str, pd.DataFrame] = {}
    for ev in events.itertuples(index=False):
        bars = bars_by_symbol.get(ev.ticker)
        if bars is None or len(bars) == 0:
            continue
        feats = feature_cache.setdefault(ev.ticker, _prior_features(bars))
        tr = simulate_event(
            bars,
            pd.Timestamp(ev.event_time),
            exit_kind=exit_kind,
            direction=direction,
            cost_bps=cost_bps,
            features=feats,
        )
        if tr is None:
            continue
        if vol_filter == "high_vol":
            if not np.isfinite(tr.get("prior_vol", np.nan)) or tr["prior_vol"] < 0.02:
                continue
        elif vol_filter == "high_volume":
            if (
                not np.isfinite(tr.get("prior_rel_volume", np.nan))
                or tr["prior_rel_volume"] < 1.0
            ):
                continue
        elif vol_filter != "none":
            raise ValueError(vol_filter)
        tr.update(
            {
                "ticker": ev.ticker,
                "filed": ev.filed,
                "event_time": ev.event_time,
                "items": ev.items,
                "timestamp_source": ev.timestamp_source,
            }
        )
        rows.append(tr)
    if not rows:
        return pd.DataFrame(
            columns=[
                "ticker",
                "filed",
                "event_time",
                "entry_time",
                "entry_session",
                "exit_time",
                "entry",
                "exit",
                "gross",
                "net",
                "direction",
                "cost_bps",
                "prior_vol",
                "prior_rel_volume",
            ]
        )
    return pd.DataFrame(rows).sort_values("entry_time").reset_index(drop=True)


def placebo_means(
    bars_by_symbol: dict[str, pd.DataFrame],
    events: pd.DataFrame,
    *,
    exit_kind: str = "close",
    direction: float = 1.0,
    cost_bps: float = 5.0,
    vol_filter: str = "none",
    n: int = 200,
    seed: int = 11,
) -> np.ndarray:
    """Fast date-shuffle placebo: random tradable bars, same horizon/costs.

    Per ticker, random entry bars are drawn vectorized; the exit is looked up
    from the session calendar. This is much faster than filtering a DataFrame
    once per draw and keeps the same no-16:00-entry rule.
    """
    if exit_kind not in ("close", "next_bar", "5d"):
        raise ValueError(exit_kind)
    rng = np.random.default_rng(seed)
    per_ticker = []
    for ticker, g in events.groupby("ticker"):
        bars = bars_by_symbol.get(ticker)
        if bars is None or len(bars) < 10:
            continue
        b = bars.sort_values("ts").reset_index(drop=True)
        sessions = pd.DatetimeIndex(sorted(b["session"].unique()))
        sess_idx = sessions.get_indexer(pd.DatetimeIndex(b["session"]))
        opens = b["open"].to_numpy(dtype=float)
        closes = b["close"].to_numpy(dtype=float)
        ts = b["ts"]
        valid = np.array(
            [pd.Timestamp(x).time() < dtime(16, 0) for x in ts], dtype=bool
        )
        valid &= np.isfinite(opens) & (opens > 0)
        valid_pos = np.flatnonzero(valid)
        if len(valid_pos) == 0:
            continue
        last_idx = np.full(len(sessions), -1, dtype=int)
        for i, si in enumerate(sess_idx):
            if si >= 0:
                last_idx[si] = i
        if exit_kind == "close":
            exit_idx = last_idx[sess_idx]
        elif exit_kind == "next_bar":
            exit_idx = np.arange(len(b)) + 1
            exit_idx = np.where(exit_idx < len(b), exit_idx, -1)
        else:
            target = sess_idx + 5
            exit_idx = np.where(
                target < len(sessions), last_idx[np.clip(target, 0, len(sessions) - 1)], -1
            )
        feats = _prior_features(b)
        pv = feats["prior_vol"].reindex(sessions).to_numpy(dtype=float)
        rv = feats["prior_rel_volume"].reindex(sessions).to_numpy(dtype=float)
        k = len(g)
        draws = rng.integers(0, len(valid_pos), size=(n, k))
        pos = valid_pos[draws]
        entry = opens[pos]
        si = sess_idx[pos]
        ex = exit_idx[pos]
        ok = (ex >= 0) & np.isfinite(entry) & (entry > 0)
        ex_safe = np.clip(ex, 0, len(closes) - 1)
        exit_px = closes[ex_safe]
        ok &= np.isfinite(exit_px) & (exit_px > 0)
        if vol_filter == "high_vol":
            ok &= np.isfinite(pv[si]) & (pv[si] >= 0.02)
        elif vol_filter == "high_volume":
            ok &= np.isfinite(rv[si]) & (rv[si] >= 1.0)
        elif vol_filter != "none":
            raise ValueError(vol_filter)
        gross = direction * (exit_px / entry - 1.0)
        net = gross - 2.0 * cost_bps / 1e4
        net = np.where(ok, net, np.nan)
        per_ticker.append(net)
    out = np.full(n, np.nan)
    if not per_ticker:
        return out
    for i in range(n):
        vals = [x[i, :] for x in per_ticker]
        flat = np.concatenate(vals)
        good = flat[np.isfinite(flat)]
        if len(good):
            out[i] = float(np.mean(good))
    return out

def daily_equity(
    trades: pd.DataFrame, sessions: pd.DatetimeIndex | None = None
) -> pd.Series:
    """Equal-weight daily event return, then annual French PFU.

    Idle days are zero returns when a full session calendar is supplied; this
    keeps CAGR annualized over the actual sample rather than only event days.
    """
    if trades.empty:
        return pd.Series(dtype=float)
    daily = trades.groupby("entry_session")["net"].mean().sort_index()
    if sessions is not None and len(sessions):
        daily = daily.reindex(pd.DatetimeIndex(sessions)).fillna(0.0)
    eq, _ = _tax_equity(daily)
    return eq


def summarize(
    trades: pd.DataFrame,
    placebo: np.ndarray | None = None,
    *,
    sessions: pd.DatetimeIndex | None = None,
    direction: float = 1.0,
) -> dict:
    if trades.empty:
        return {
            "n": 0,
            "hit": float("nan"),
            "mean": float("nan"),
            "median": float("nan"),
            "gross_mean": float("nan"),
            "multiple": float("nan"),
            "cagr": float("nan"),
            "maxdd": float("nan"),
            "placebo_p": float("nan"),
        }
    net = trades["net"].to_numpy(dtype=float)
    eq = daily_equity(trades, sessions)
    years = (
        (eq.index[-1] - eq.index[0]).days / 365.25
        if len(eq) > 1
        else (len(eq) / 252.0 if len(eq) else 0.0)
    )
    p = float("nan")
    if placebo is not None and len(placebo):
        good = placebo[np.isfinite(placebo)]
        if len(good):
            observed = float(np.mean(net))
            if direction >= 0:
                p = float((1.0 + np.sum(good >= observed)) / (1.0 + len(good)))
            else:
                p = float((1.0 + np.sum(good <= observed)) / (1.0 + len(good)))
    return {
        "n": int(len(trades)),
        "hit": float((net > 0).mean()),
        "mean": float(np.mean(net)),
        "median": float(np.median(net)),
        "gross_mean": float(trades["gross"].mean()),
        "multiple": float(eq.iloc[-1]) if len(eq) else float("nan"),
        "cagr": float(eq.iloc[-1] ** (1.0 / years) - 1.0)
        if years > 0
        else float("nan"),
        "maxdd": float((eq / eq.cummax() - 1.0).min()) if len(eq) else float("nan"),
        "placebo_p": p,
    }
