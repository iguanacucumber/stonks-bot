"""UCITS semiconductor funds, currency conversion and vehicle comparison.

Cache-only. The three UCITS lines are read from committed-style bars under
`cache/bars`; their metadata is static (documented in `results/ucits_core.md`).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from .data import BARS, read_bars

# Metadata checked 2026-11-26. The UCITS lines are Irish-domiciled,
# accumulating, physically replicated and UCITS-compliant.
FUNDS: dict[str, dict] = {
    "SMH": {
        "name": "VanEck Semiconductor ETF (US)",
        "isin": "US92189F6767",
        "domicile": "US",
        "currency": "USD",
        "accumulating": False,
        "ucits": False,
        "ter": 0.0035,
        "index": "MVIS US Listed Semiconductor 25% Capped",
        "listing": "Nasdaq",
        "source": "https://www.vaneck.com/us/en/investments/semiconductor-etf-smh/",
    },
    "SMH.L": {
        "name": "VanEck Semiconductor UCITS ETF",
        "isin": "IE00BMC38736",
        "domicile": "IE",
        "currency": "USD",
        "accumulating": True,
        "ucits": True,
        "ter": 0.0035,
        "index": "MVIS US Listed Semiconductor 10% Capped",
        "listing": "London Stock Exchange (USD line)",
        "source": "https://www.vaneck.com/uk/en/investments/semiconductor-etf/",
    },
    "SEMI.AS": {
        "name": "iShares MSCI Global Semiconductors UCITS ETF USD Acc",
        "isin": "IE000I8KRLL9",
        "domicile": "IE",
        "currency": "USD",
        "accumulating": True,
        "ucits": True,
        "ter": 0.0035,
        "index": (
            "MSCI ACWI IMI Semiconductors & Semiconductor Equipment "
            "ESG Screened Select Capped"
        ),
        "listing": "Euronext Amsterdam (USD line)",
        "source": (
            "https://www.ishares.com/uk/individual/en/products/319084/"
            "ishares-msci-global-semiconductors-ucits-etf"
        ),
    },
    "SEMI.L": {
        "name": "iShares MSCI Global Semiconductors UCITS ETF USD Acc",
        "isin": "IE000I8KRLL9",
        "domicile": "IE",
        "currency": "GBP",
        "accumulating": True,
        "ucits": True,
        "ter": 0.0035,
        "index": (
            "MSCI ACWI IMI Semiconductors & Semiconductor Equipment "
            "ESG Screened Select Capped"
        ),
        "listing": "London Stock Exchange (GBP line)",
        "source": (
            "https://www.ishares.com/uk/individual/en/products/319084/"
            "ishares-msci-global-semiconductors-ucits-etf"
        ),
    },
}


@dataclass(frozen=True)
class FundStats:
    symbol: str
    start: str
    end: str
    total_return: float
    cagr: float
    vol: float
    maxdd: float
    tracking_diff: float = float("nan")
    tracking_error: float = float("nan")
    beta: float = float("nan")
    corr: float = float("nan")


def get_fund(symbol: str) -> dict:
    if symbol not in FUNDS:
        raise KeyError(f"unknown fund {symbol}; known: {sorted(FUNDS)}")
    return dict(FUNDS[symbol])


def _bars(symbol: str, cache_dir: str | Path | None = None) -> pd.DataFrame:
    return read_bars(symbol, cache_dir=cache_dir)


def fx_to_usd(
    currency: str, dates: pd.DatetimeIndex, cache_dir: str | Path | None = None
) -> pd.Series:
    """USD value of one unit of `currency` on each date, cache-only.

    Same-date FX is used; no future rate. GBPUSD/EURUSD quote USD per unit.
    """
    currency = currency.upper()
    if currency == "USD":
        return pd.Series(1.0, index=pd.DatetimeIndex(dates), name="fx_usd")
    pair = {"GBP": "GBPUSD=X", "EUR": "EURUSD=X"}.get(currency)
    if pair is None:
        raise ValueError(f"unsupported currency {currency}")
    df = _bars(pair, cache_dir)
    fx = df.set_index("date")["adjclose"].sort_index()
    out = fx.reindex(fx.index.union(pd.DatetimeIndex(dates))).ffill().reindex(dates)
    if out.isna().any():
        first = out.index[out.isna()][0]
        raise ValueError(f"missing {pair} for {first.date()}")
    return out.rename("fx_usd")


def to_usd_bars(symbol: str, cache_dir: str | Path | None = None) -> pd.DataFrame:
    """Adjusted OHLC bars converted to USD with same-date FX."""
    df = _bars(symbol, cache_dir).copy()
    currency = get_fund(symbol)["currency"]
    fx = fx_to_usd(currency, pd.DatetimeIndex(df["date"]), cache_dir)
    f = fx.to_numpy(dtype=float)
    for col in ("open", "high", "low", "close", "adjclose"):
        if col in df.columns:
            df[col] = df[col].to_numpy(dtype=float) * f
    df["ticker"] = symbol
    return df


def to_usd_series(symbol: str, cache_dir: str | Path | None = None) -> pd.Series:
    df = to_usd_bars(symbol, cache_dir)
    return df.set_index("date")["adjclose"].sort_index().rename(symbol)


def common_start(
    symbols: list[str] | tuple[str, ...], cache_dir: str | Path | None = None
) -> pd.Timestamp:
    starts = [to_usd_series(s, cache_dir).index.min() for s in symbols]
    return max(starts)


def add_ucits_to_panel(
    panel: pd.DataFrame,
    symbols: tuple[str, ...] = ("SMH.L", "SEMI.AS", "SEMI.L"),
    cache_dir: str | Path | None = None,
) -> pd.DataFrame:
    """Append USD-converted UCITS core rows to an existing US panel."""
    # The engine computes momentum with row shifts. A UCITS line on an LSE
    # holiday would add a union-calendar row where US names are NaN and shift
    # every lookback. Project each UCITS series onto the US sessions first.
    us_dates = set(
        pd.to_datetime(panel.loc[panel["ticker"] == "SPY", "date"]).dt.normalize()
    )
    parts = [panel]
    for symbol in symbols:
        df = to_usd_bars(symbol, cache_dir)
        df = df[df["date"].isin(us_dates)]
        parts.append(df)
    return (
        pd.concat(parts, ignore_index=True)
        .drop_duplicates(["date", "ticker"])
        .sort_values(["date", "ticker"])
    )


def fund_stats(series: pd.Series, benchmark: pd.Series | None = None) -> FundStats:
    """Total return, CAGR, vol, maxDD, and tracking vs a benchmark."""
    s = series.dropna()
    if len(s) < 2:
        raise ValueError("not enough observations")
    r = s.pct_change().dropna()
    years = len(s) / 252.0
    total = float(s.iloc[-1] / s.iloc[0] - 1.0)
    cagr = float((1.0 + total) ** (1.0 / years) - 1.0)
    vol = float(r.std() * np.sqrt(252.0))
    maxdd = float((s / s.cummax() - 1.0).min())
    out = FundStats(
        symbol=str(s.name),
        start=s.index[0].date().isoformat(),
        end=s.index[-1].date().isoformat(),
        total_return=total,
        cagr=cagr,
        vol=vol,
        maxdd=maxdd,
    )
    if benchmark is not None:
        b = benchmark.reindex(s.index).ffill()
        rb = b.pct_change().dropna()
        rr = r.reindex(rb.index).dropna()
        rb = rb.reindex(rr.index)
        if len(rr) > 2:
            diff = rr - rb
            var = float(rb.var())
            out = FundStats(
                **{
                    **out.__dict__,
                    "tracking_diff": float(diff.mean() * 252.0),
                    "tracking_error": float(diff.std() * np.sqrt(252.0)),
                    "beta": float(np.cov(rr, rb)[0, 1] / var)
                    if var > 0
                    else float("nan"),
                    "corr": float(rr.corr(rb)),
                }
            )
    return out
