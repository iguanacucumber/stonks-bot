"""Panel builders for Alpha Lab experiments.

All readers are cache-only. A missing symbol raises instead of silently
fetching, so a report never mixes a data snapshot with a live download.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from bot import data, france, markets, universe

ROOT = Path(__file__).resolve().parent.parent.parent
BARS = ROOT / "cache" / "bars"

# The pre-registered cross-asset universe. BIL is the defensive asset.
RISK_ETFS = ("SMH", "QQQ", "SPY", "TLT", "IEF", "GLD")
DEFENSIVE_ETF = "BIL"
CROSS_ASSET = RISK_ETFS + (DEFENSIVE_ETF,)

# A wider liquid-ETF set already present in the cache. Used only as an
# explicitly separate variant, never as the primary cross-asset test.
SECTOR_ETFS = ("XLK", "XLE", "XLF", "XLV", "XLI", "XLP", "XLU", "XLB", "XLY")
EXTENDED_ETFS = CROSS_ASSET + SECTOR_ETFS + ("IWM", "SHY")


def read_bars(symbol: str, cache_dir: Path | None = None) -> pd.DataFrame:
    """Adjusted OHLCV for one symbol from the committed cache."""
    path = (cache_dir or BARS) / f"{symbol.replace('/', '_')}.csv"
    if not path.exists():
        raise FileNotFoundError(f"no cached bars for {symbol}: {path}")
    df = pd.read_csv(path, parse_dates=["date"])
    if df.empty:
        raise ValueError(f"empty cached bars for {symbol}")
    need = {"date", "adjclose", "close", "volume"}
    missing = need - set(df.columns)
    if missing:
        raise ValueError(f"{symbol}: missing columns {sorted(missing)}")
    factor = df["adjclose"] / df["close"]
    for col in ("open", "high", "low"):
        if col in df.columns:
            df[col] = df[col] * factor
        else:
            df[col] = df["adjclose"]
    df["ticker"] = symbol
    return df[
        ["date", "ticker", "open", "high", "low", "close", "adjclose", "volume"]
    ].sort_values("date")


def make_panel(
    symbols: tuple[str, ...] | list[str], benchmark: str = "SPY"
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Long ETF panel plus an all-always membership frame.

    `bot.strategy.Market` uses the benchmark column for its trading calendar.
    The membership frame is static because an ETF universe has no index
    membership problem.
    """
    symbols = tuple(dict.fromkeys(symbols))
    if benchmark not in symbols:
        raise ValueError(f"benchmark {benchmark} must be in the universe")
    parts = [read_bars(s) for s in symbols]
    panel = (
        pd.concat(parts, ignore_index=True)
        .drop_duplicates(["date", "ticker"])
        .sort_values(["date", "ticker"])
    )
    start = panel["date"].min()
    mem = pd.DataFrame({"date": [start] * len(symbols), "ticker": list(symbols)})
    return panel, mem


def load_market(name: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Existing stock-market panel (US/TECH/...), with Yahoo aliases fixed."""
    panel, mem = markets.build(name)
    if name != "US":
        # Yahoo Europe/Asia keep the dot in symbols; the S&P alias map does not.
        universe.canonical = lambda t: t  # type: ignore[assignment]
    return panel, mem


def tech_sector_membership(
    membership: pd.DataFrame,
    sectors: tuple[str, ...] = ("Information Technology",),
) -> pd.DataFrame:
    """PIT S&P membership filtered to a set of *current* GICS sectors.

    Membership is point-in-time; the sector label is not. That residual bias is
    documented in `docs/research.md` and is why the ETF-only results are the
    preferred evidence.
    """
    meta = universe.current_meta()
    allowed = set(meta.loc[meta["sector"].isin(sectors), "ticker"])
    # Keep the original membership ticker spelling; canonical() happens later.
    out = membership[membership["ticker"].isin(allowed)].copy()
    if out.empty:
        raise ValueError(f"no membership rows for sectors {sectors}")
    return out.sort_values(["date", "ticker"]).reset_index(drop=True)


def pit_membership_filter(
    membership: pd.DataFrame, tickers: tuple[str, ...] | list[str]
) -> pd.DataFrame:
    """PIT membership rows restricted to a fixed name list.

    This removes the "future index member" part of survivorship bias from a
    static list, but it still selects names that survived to today.
    """
    keep = set(tickers)
    out = membership[membership["ticker"].isin(keep)].copy()
    return out.sort_values(["date", "ticker"]).reset_index(drop=True)


def etf_returns(symbols: tuple[str, ...] | list[str]) -> pd.DataFrame:
    """Adjusted close-to-close returns, aligned on the benchmark calendar."""
    closes = {}
    for sym in symbols:
        df = read_bars(sym).set_index("date")["adjclose"]
        closes[sym] = df
    px = pd.DataFrame(closes).sort_index()
    return px.pct_change(fill_method=None)


def close_open(
    symbols: tuple[str, ...] | list[str],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Aligned adjusted close and next-session open matrices.

    `open` is already dividend/split-adjusted with the same factor as
    `adjclose`, so `open.shift(-1) / close - 1` is a total-return overnight
    return (ignoring the dividend accrual inside the overnight gap, which is
    second order for this study).
    """
    closes, opens = {}, {}
    for sym in symbols:
        df = read_bars(sym)
        closes[sym] = df.set_index("date")["adjclose"]
        opens[sym] = df.set_index("date")["open"]
    c = pd.DataFrame(closes).sort_index()
    o = pd.DataFrame(opens).sort_index().reindex(c.index)
    return c, o


def fx_eur_per_usd(dates: pd.DatetimeIndex) -> pd.Series:
    """EUR value of one USD, from the cached EURUSD series."""
    eurusd = data.fetch_one("EURUSD=X").set_index("date")["adjclose"].sort_index()
    return (1.0 / eurusd).reindex(dates).ffill()


def after_tax_multiple(result, market: str = "TECH", capital: float = 10000.0) -> dict:
    """Convenience wrapper around `bot.france.after_tax` with both currencies."""
    f = france.after_tax(result, capital, market)
    return {
        **f,
        "after_tax_multiple_eur": f["after_tax_eur"] / capital,
        "after_tax_multiple_usd": f["after_tax_native"] / capital,
    }
