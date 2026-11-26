"""Point-in-time Nasdaq-100 membership and a cache-only panel builder.

Source: `thuningxu/sp500nq100` `nasdaq100_components_history.csv`, which
parses the Wikipedia Nasdaq-100 yearly changes tables into complete
constituent snapshots. The committed snapshot is in
`bot/research/data/nasdaq100_history.csv`; tests and reports never fetch it.

The history is a set of complete lists on effective change dates. A date uses
the latest snapshot on or before it, so no future member can enter an earlier
backtest. The last snapshot is 2026-05-18; later dates use that list, which is
a documented six-month staleness, not a look-ahead.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from bot import universe

from .data import BARS, read_bars

DATA = Path(__file__).resolve().parent / "data" / "nasdaq100_history.csv"
SOURCE_URL = (
    "https://raw.githubusercontent.com/thuningxu/sp500nq100/main/"
    "nasdaq100_components_history.csv"
)
SNAPSHOT_NOTE = (
    "thuningxu/sp500nq100 Nasdaq-100 complete snapshots; last committed "
    "snapshot 2026-05-18, applied until the data end 2026-11-25"
)


@dataclass(frozen=True)
class Coverage:
    unique_tickers: int
    available: int
    missing: int
    missing_tickers: tuple[str, ...]
    last_snapshot: str
    last_snapshot_count: int

    @property
    def available_share(self) -> float:
        return self.available / self.unique_tickers if self.unique_tickers else 0.0


def load_history(path: str | Path | None = None) -> pd.DataFrame:
    """Load and validate the committed complete-snapshot history."""
    path = Path(path) if path is not None else DATA
    df = pd.read_csv(path, parse_dates=["date"])
    need = {"date", "tickers"}
    missing = need - set(df.columns)
    if missing:
        raise ValueError(f"Nasdaq-100 history missing columns {sorted(missing)}")
    if df.empty:
        raise ValueError("Nasdaq-100 history is empty")
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    df = (
        df.dropna(subset=["date", "tickers"]).sort_values("date").reset_index(drop=True)
    )
    if df["date"].duplicated().any():
        dup = df.loc[df["date"].duplicated(), "date"].dt.date.unique()
        raise ValueError(f"duplicate Nasdaq-100 snapshot dates: {list(dup)[:5]}")
    if (df["tickers"].astype(str).str.strip() == "").any():
        raise ValueError("empty constituent list in Nasdaq-100 history")
    return df


def pit_canonical(raw: str) -> str:
    """Canonical Yahoo symbol for a Nasdaq-100 member.

    GOOG and GOOGL are separate index members and are kept separate; FB maps
    to META through the shared S&P alias map.
    """
    return universe.canonical(raw.strip())


def membership_long(history: pd.DataFrame | None = None) -> pd.DataFrame:
    """Long frame date,ticker with canonical Yahoo symbols, no duplicates."""
    hist = load_history() if history is None else history
    rows: list[tuple[pd.Timestamp, str, str]] = []
    for row in hist.itertuples(index=False):
        date = pd.Timestamp(row.date)
        for raw in str(row.tickers).split(","):
            raw = raw.strip()
            if not raw:
                continue
            rows.append((date, raw, pit_canonical(raw)))
    out = (
        pd.DataFrame(rows, columns=["date", "raw_ticker", "ticker"])
        .drop_duplicates(["date", "ticker"])
        .sort_values(["date", "ticker"])
        .reset_index(drop=True)
    )
    return out


def membership_on(
    date: str | pd.Timestamp,
    history: pd.DataFrame | None = None,
    available_only: bool = False,
    bars_dir: str | Path | None = None,
) -> list[str]:
    """Canonical constituents on `date`, using only the latest prior snapshot."""
    long = membership_long(history)
    ts = pd.Timestamp(date).normalize()
    prior = long[long["date"] <= ts]
    if prior.empty:
        return []
    last = prior["date"].max()
    tickers = sorted(prior.loc[prior["date"] == last, "ticker"].unique())
    if available_only:
        root = Path(bars_dir) if bars_dir is not None else BARS
        tickers = [t for t in tickers if (root / f"{t}.csv").exists()]
    return tickers


def coverage(
    history: pd.DataFrame | None = None, bars_dir: str | Path | None = None
) -> Coverage:
    """How many PIT tickers have cached bars; this is the delisting-bias audit."""
    hist = load_history() if history is None else history
    long = membership_long(hist)
    root = Path(bars_dir) if bars_dir is not None else BARS
    unique = sorted(long["ticker"].unique())
    have = [t for t in unique if (root / f"{t}.csv").exists()]
    missing = [t for t in unique if t not in set(have)]
    last = hist.iloc[-1]
    return Coverage(
        unique_tickers=len(unique),
        available=len(have),
        missing=len(missing),
        missing_tickers=tuple(missing),
        last_snapshot=pd.Timestamp(last["date"]).date().isoformat(),
        last_snapshot_count=len(str(last["tickers"]).split(",")),
    )


def build_pit_panel(
    history: pd.DataFrame | None = None,
    bars_dir: str | Path | None = None,
    benchmark: str = "QQQ",
    extras: tuple[str, ...] = ("SMH", "SOXX"),
) -> tuple[pd.DataFrame, pd.DataFrame, Coverage]:
    """Cache-only panel and membership for the PIT Nasdaq-100 book.

    `benchmark` is stored as the engine's `SPY` column, matching the shipped
    TECH book (QQQ benchmark). Constituents without cached bars are excluded
    from both panel and membership; the Coverage object records them.
    """
    hist = load_history() if history is None else history
    long = membership_long(hist)
    root = Path(bars_dir) if bars_dir is not None else BARS
    available = sorted(
        {t for t in long["ticker"].unique() if (root / f"{t}.csv").exists()}
    )
    if not available:
        raise ValueError(f"no cached bars for PIT universe under {root}")
    parts = [read_bars(t, cache_dir=root) for t in available]
    bench = read_bars(benchmark, cache_dir=root)
    bench = bench.copy()
    bench["ticker"] = "SPY"  # engine expects the benchmark in the SPY column
    parts.append(bench)
    for ticker in extras:
        path = root / f"{ticker}.csv"
        if path.exists() and ticker not in available:
            parts.append(read_bars(ticker, cache_dir=root))
    panel = (
        pd.concat(parts, ignore_index=True)
        .drop_duplicates(["date", "ticker"])
        .sort_values(["date", "ticker"])
    )
    mem = (
        long[long["ticker"].isin(available)][["date", "ticker"]]
        .drop_duplicates()
        .sort_values(["date", "ticker"])
        .reset_index(drop=True)
    )
    if mem.empty:
        raise ValueError("PIT membership is empty after cache coverage filter")
    return panel, mem, coverage(hist, root)
