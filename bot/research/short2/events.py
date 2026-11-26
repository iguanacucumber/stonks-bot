"""SEC 8-K event study with date-shuffle placebo.

The cache contains recent SEC submissions per CIK. Entry is the first close
after the filing date; the event is never traded on the filing-day close.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from bot.news import MATERIAL

NEWS = Path(__file__).resolve().parents[3] / "cache" / "news"


def load_events(cache_dir: str | Path | None = None) -> pd.DataFrame:
    root = Path(cache_dir) if cache_dir is not None else NEWS
    rows = []
    for path in sorted(root.glob("sub_*.json")):
        try:
            data = json.loads(path.read_text())
        except Exception:
            continue
        tickers = data.get("tickers") or []
        ticker = str(tickers[0]).upper() if tickers else ""
        if not ticker:
            continue
        recent = data.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        items = recent.get("items", [])
        filed = recent.get("filingDate", [])
        accepted = recent.get("acceptanceDateTime", [])
        for i, form in enumerate(forms):
            if form != "8-K":
                continue
            item = str(items[i] if i < len(items) else "")
            tags = {x.strip() for x in item.split(",") if x.strip()}
            if not (tags & MATERIAL):
                continue
            if not filed[i]:
                continue
            rows.append(
                {
                    "ticker": ticker,
                    "filed": pd.Timestamp(filed[i]).normalize(),
                    "accepted": str(accepted[i] if i < len(accepted) else ""),
                    "items": ",".join(sorted(tags & MATERIAL)),
                    "cik": str(data.get("cik", "")),
                }
            )
    if not rows:
        return pd.DataFrame(columns=["ticker", "filed", "accepted", "items", "cik"])
    return (
        pd.DataFrame(rows)
        .drop_duplicates(["ticker", "filed", "items"])
        .sort_values(["ticker", "filed"])
        .reset_index(drop=True)
    )


def event_returns(
    prices: dict[str, pd.Series],
    events: pd.DataFrame,
    *,
    horizons: tuple[int, ...] = (1, 5, 20),
    cost_bps: float = 5.0,
    start: str = "2015-01-01",
) -> pd.DataFrame:
    """Net event returns; entry at the first close strictly after filing."""
    out = []
    for row in events.itertuples(index=False):
        px = prices.get(row.ticker)
        if px is None or len(px) < 30:
            continue
        px = px.dropna().sort_index()
        filed = pd.Timestamp(row.filed)
        if filed < pd.Timestamp(start):
            continue
        pos = int(px.index.searchsorted(filed, side="right"))
        if pos <= 0 or pos >= len(px):
            continue
        entry_date = px.index[pos]
        entry = float(px.iloc[pos])
        for h in horizons:
            j = pos + h
            if j >= len(px):
                continue
            ret = float(px.iloc[j] / entry - 1.0)
            net = ret - 2.0 * cost_bps / 1e4
            out.append(
                {
                    "ticker": row.ticker,
                    "filed": filed,
                    "entry": entry_date,
                    "horizon": h,
                    "gross": ret,
                    "net": net,
                }
            )
    if not out:
        return pd.DataFrame(columns=["ticker", "filed", "entry", "horizon", "gross", "net"])
    return pd.DataFrame(out)


def placebo_means(
    prices: dict[str, pd.Series],
    events: pd.DataFrame,
    horizon: int,
    *,
    n: int = 500,
    seed: int = 7,
    cost_bps: float = 5.0,
    start: str = "2015-01-01",
) -> np.ndarray:
    """Date-shuffled mean event return per ticker, same horizon/costs."""
    rng = np.random.default_rng(seed)
    by_ticker = {t: g for t, g in events.groupby("ticker")}
    means = np.empty(n)
    for k in range(n):
        vals = []
        for ticker, g in by_ticker.items():
            px = prices.get(ticker)
            if px is None or len(px) < horizon + 30:
                continue
            px = px.dropna().sort_index()
            px = px.loc[px.index >= pd.Timestamp(start)]
            if len(px) < horizon + 5:
                continue
            picks = rng.integers(0, len(px) - horizon, size=len(g))
            for i in picks:
                ret = float(px.iloc[i + horizon] / px.iloc[i] - 1.0)
                vals.append(ret - 2.0 * cost_bps / 1e4)
        means[k] = float(np.mean(vals)) if vals else np.nan
    return means


def event_summary(frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for h, g in frame.groupby("horizon"):
        r = g["net"].to_numpy()
        rows.append(
            {
                "horizon": int(h),
                "n": int(len(r)),
                "mean": float(np.mean(r)),
                "median": float(np.median(r)),
                "hit": float((r > 0).mean()),
                "t": float(np.mean(r) / np.std(r, ddof=1) * np.sqrt(len(r)))
                if len(r) > 2 and np.std(r, ddof=1) > 0
                else float("nan"),
                "gross_mean": float(g["gross"].mean()),
            }
        )
    return pd.DataFrame(rows)
