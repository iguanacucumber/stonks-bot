"""Point-in-time S&P 500 membership, ticker aliases, sectors."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "cache"

HIST_URL = (
    "https://raw.githubusercontent.com/fja05680/sp500/master/"
    "S%26P%20500%20Historical%20Components%20%26%20Changes%20(Updated).csv"
)
CURRENT_URL = "https://raw.githubusercontent.com/fja05680/sp500/master/sp500.csv"
UA = {"User-Agent": "stonks-bot research contact@example.com"}

# Tickers that Yahoo no longer serves under their index-era symbol.
# Value is the symbol whose price history is the same security.
ALIASES = {
    "FB": "META",
    "BRK.B": "BRK-B",
    "BF.B": "BF-B",
    "ANTM": "ELV",
    "PKI": "RVTY",
    "WLTW": "WTW",
    "FISV": "FI",
    "FBHS": "FBIN",
    "RE": "EG",
    "ABC": "COR",
    "GOOG": "GOOG",
    "GOOGL": "GOOGL",
    "BK": "BNY",
    "MMC": "MRSH",
    "FI": "FISV",
    "RTN": "RTX",
    "UTX": "RTX",
    "SYMC": "GEN",
    "NLOK": "GEN",
    "BHGE": "BKR",
    "DWDP": "DD",
    "WRK": "SW",
    "BLL": "BALL",
    "GPS": "GAP",
    "CTL": "LUMN",
    "JEC": "J",
    "FLT": "CPAY",
    "HFC": "DINO",
    "PEAK": "DOC",
    "ADS": "BFH",
    "KORS": "CPRI",
    "WYND": "TNL",
    "COG": "CTRA",
    "CMCSK": "CMCSA",
}

# Index changes after the frozen fja05680 snapshot (2026-08-18).
RECENT_CHANGES = [
    ("2026-09-21", ["BE", "P", "ILMN"], ["TAP", "TTD", "BLDR"]),
    ("2026-10-01", ["VYLR"], []),
    ("2026-10-06", ["TWLO"], ["WBD", "CTVA"]),
    ("2026-10-30", ["OKTA"], ["KVUE"]),
]


def _download(url: str, path: Path) -> Path:
    if not path.exists():
        r = requests.get(url, headers=UA, timeout=60)
        r.raise_for_status()
        path.write_bytes(r.content)
    return path


def load_membership() -> pd.DataFrame:
    """Long frame: one row per (date, ticker), the index as of that date."""
    path = _download(HIST_URL, CACHE / "sp500_history.csv")
    df = pd.read_csv(path)
    df["date"] = pd.to_datetime(df["date"])
    rows = []
    for d, tickers in zip(df["date"], df["tickers"]):
        for t in str(tickers).split(","):
            t = t.strip()
            if t:
                rows.append((d, t))
    out = pd.DataFrame(rows, columns=["date", "ticker"])
    # apply changes that post-date the snapshot
    current = set(out.loc[out["date"] == out["date"].max(), "ticker"])
    for day, adds, removes in RECENT_CHANGES:
        current = (current - set(removes)) | set(adds)
        d = pd.Timestamp(day)
        for t in sorted(current):
            rows.append((d, t))
    out = pd.DataFrame(rows, columns=["date", "ticker"])
    return out.drop_duplicates().sort_values(["date", "ticker"]).reset_index(drop=True)


def members_on(membership: pd.DataFrame, date: pd.Timestamp) -> list[str]:
    sub = membership[membership["date"] <= date]
    if sub.empty:
        return []
    last = sub["date"].max()
    return sorted(sub.loc[sub["date"] == last, "ticker"].unique())


def current_meta() -> pd.DataFrame:
    """Current constituents with sector and CIK. Used for mapping only."""
    path = _download(CURRENT_URL, CACHE / "sp500_current.csv")
    df = pd.read_csv(path)
    df = df.rename(columns={"Symbol": "ticker", "GICS Sector": "sector", "CIK": "cik"})
    return df[["ticker", "sector", "cik"]].drop_duplicates("ticker")


def sector_map() -> dict[str, str]:
    meta = current_meta()
    out = {str(t): str(s) for t, s in zip(meta["ticker"], meta["sector"])}
    # carry a sector across renames so old symbols don't become "Unknown"
    for old, new in ALIASES.items():
        if new in out and old not in out:
            out[old] = out[new]
    return out


def yahoo_symbol(ticker: str) -> str:
    t = ALIASES.get(ticker, ticker)
    return t.replace(".", "-")


def canonical(ticker: str) -> str:
    """Symbol used as the price-panel column."""
    return yahoo_symbol(ticker)
