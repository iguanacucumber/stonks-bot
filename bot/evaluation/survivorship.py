"""Survivorship facts and sensitivity runs for the static TECH universe.

The TECH book does not use point-in-time membership.  `bot.markets.UNIVERSES`
is a hand-picked 49-name NASDAQ list chosen in 2026; `markets.build("TECH")`
turns it into one membership snapshot dated 2014-01-01.  Every name is therefore
eligible for the whole backtest as soon as it has enough price history, which is
a look-ahead selection of today's winners and IPO survivors.

This module does not pretend to reconstruct the true NASDAQ universe.  It
measures what can be measured from the committed cache:

* how many static names were S&P 500 members at the 2015 start, joined later, or
  never (a dated-membership sensitivity);
* how many static names have price history at the start (public-at-start
  sensitivity);
* how much of the point-in-time S&P 500 membership has usable prices (the US
  book's residual survivorship bias);
* the equal-weight multiple of the static basket versus QQQ (selection premium).
"""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd

START = "2015-01-01"


def _first_prices(panel: pd.DataFrame) -> pd.Series:
    return panel.groupby("ticker")["date"].min()


def _last_prices(panel: pd.DataFrame) -> pd.Series:
    return panel.groupby("ticker")["date"].max()


def static_universe_facts(
    tech: list[str], membership: pd.DataFrame, panel: pd.DataFrame, start: str = START
) -> dict:
    """Counts and names that explain the static-49 selection."""
    start_ts = pd.Timestamp(start)
    snap = membership.loc[membership["date"] <= start_ts, "date"].max()
    at_start = set(membership.loc[membership["date"] == snap, "ticker"])
    ever = set(membership["ticker"])
    first_member = membership.groupby("ticker")["date"].min()
    first_px = _first_prices(panel)
    have = set(panel["ticker"].unique())
    in_sp500 = [t for t in tech if t in at_start]
    joined_later = {
        t: first_member[t].date().isoformat()
        for t in tech
        if t not in at_start and t in ever
    }
    never = [t for t in tech if t not in ever]
    missing_price = [t for t in tech if t not in have]
    late_price = {
        t: first_px[t].date().isoformat()
        for t in tech
        if t in first_px.index and first_px[t] > start_ts
    }
    return {
        "n_static": len(tech),
        "start": start,
        "sp500_snapshot": snap.date().isoformat() if pd.notna(snap) else None,
        "in_sp500_at_start": sorted(in_sp500),
        "n_in_sp500_at_start": len(in_sp500),
        "joined_sp500_later": joined_later,
        "n_joined_later": len(joined_later),
        "never_in_sp500": sorted(never),
        "n_never_in_sp500": len(never),
        "missing_price": sorted(missing_price),
        "n_missing_price": len(missing_price),
        "first_price_after_start": late_price,
        "n_first_price_after_start": len(late_price),
        "tickers": list(tech),
    }


def dated_sp500_membership(tech: list[str], membership: pd.DataFrame) -> pd.DataFrame:
    """Membership rows dated at each TECH name's first S&P 500 membership.

    Names that never joined the S&P 500 are absent.  This is still not a true
    NASDAQ point-in-time universe (only 28 of the 49 names were in the S&P at the
    2015 start), but it removes the look-ahead for the later index additions.
    """
    first = membership.groupby("ticker")["date"].min()
    rows = [{"date": first[t], "ticker": t} for t in tech if t in first.index]
    if not rows:
        return pd.DataFrame(columns=["date", "ticker"])
    return pd.DataFrame(rows).sort_values(["date", "ticker"]).reset_index(drop=True)


def public_at_start_membership(
    tech: list[str], panel: pd.DataFrame, start: str = START
) -> pd.DataFrame:
    """Static membership for names with at least one bar on/before the start."""
    start_ts = pd.Timestamp(start)
    first = _first_prices(panel)
    keep = [t for t in tech if t in first.index and first[t] <= start_ts]
    return pd.DataFrame(
        {"date": [pd.Timestamp("2014-01-01")] * len(keep), "ticker": keep}
    )


def membership_coverage(
    membership: pd.DataFrame, panel: pd.DataFrame, freq: str = "YS"
) -> pd.DataFrame:
    """Share of each S&P 500 snapshot with a usable price that day.

    A member is covered when its ticker appears in the panel on the most recent
    trading day on/before the snapshot date.  Missing tickers are the delisted /
    renamed names Yahoo no longer serves; they are the residual survivorship bias
    in the US book even though its membership is point-in-time.
    """
    panel_dates = pd.DatetimeIndex(sorted(panel["date"].unique()))
    if len(panel_dates) == 0:
        return pd.DataFrame()
    dates = pd.DatetimeIndex(sorted(membership["date"].unique()))
    if freq:
        dates = pd.DatetimeIndex(pd.Series(dates).groupby(dates.to_period("Y")).max())
    rows = []
    for d in dates:
        pos = panel_dates.searchsorted(d, side="right") - 1
        if pos < 0:
            continue
        px_date = panel_dates[pos]
        raw_members = set(membership.loc[membership["date"] == d, "ticker"])
        if not raw_members:
            continue
        from bot import universe

        members = {universe.canonical(t) for t in raw_members}
        available = set(panel.loc[panel["date"] == px_date, "ticker"])
        covered = members & available
        rows.append(
            {
                "snapshot": d.date().isoformat(),
                "price_date": px_date.date().isoformat(),
                "members": len(members),
                "covered": len(covered),
                "coverage": len(covered) / len(members),
                "missing": len(members) - len(covered),
            }
        )
    return pd.DataFrame(rows)


def us_missing_share(
    membership: pd.DataFrame, panel: pd.DataFrame, start: str = "2014-01-01"
) -> dict:
    """Unique S&P members with no usable price at all since the panel start.

    This is the residual survivorship bias in the US book: membership is
    point-in-time but the price panel drops delisted/renamed names Yahoo no
    longer serves.  Row-weighted coverage at each snapshot is in
    :func:`membership_coverage`; this is the unique-name version.
    """
    from bot import universe

    mem = membership[membership["date"] >= pd.Timestamp(start)]
    tickers = set(mem["ticker"])
    have = set(panel["ticker"].unique())
    missing = [t for t in tickers if universe.canonical(t) not in have]
    return {
        "start": start,
        "unique_members": len(tickers),
        "missing": len(missing),
        "missing_share": len(missing) / len(tickers) if tickers else float("nan"),
        "missing_sample": sorted(missing)[:40],
    }


def equal_weight_multiple(
    panel: pd.DataFrame, start: str = START, tickers: list[str] | None = None
) -> dict:
    """Static equal-weight buy-and-hold multiple and QQQ over the same dates."""
    start_ts = pd.Timestamp(start)
    wanted = set(tickers) if tickers is not None else None
    rows = []
    for ticker, g in panel.groupby("ticker"):
        if wanted is not None and ticker not in wanted and ticker != "SPY":
            continue
        g = g.sort_values("date")
        g = g[g["date"] >= start_ts]
        if len(g) < 2 or g["adjclose"].iloc[0] <= 0:
            continue
        rows.append((ticker, float(g["adjclose"].iloc[-1] / g["adjclose"].iloc[0])))
    if not rows:
        return {}
    df = pd.DataFrame(rows, columns=["ticker", "multiple"])
    bench = df[df["ticker"] == "SPY"]["multiple"]
    names = df[df["ticker"] != "SPY"]
    return {
        "equal_weight_multiple": float(names["multiple"].mean()),
        "median_name_multiple": float(names["multiple"].median()),
        "best_name": str(names.loc[names["multiple"].idxmax(), "ticker"]),
        "best_multiple": float(names["multiple"].max()),
        "worst_name": str(names.loc[names["multiple"].idxmin(), "ticker"]),
        "worst_multiple": float(names["multiple"].min()),
        "benchmark_multiple": float(bench.iloc[0]) if len(bench) else float("nan"),
        "n_names": int(len(names)),
    }


def run_tech_variant(
    panel: pd.DataFrame, mem: pd.DataFrame, capital: float = 10_000.0
) -> dict:
    """Run the shipped TECH monthly 2.5x CTO book with a supplied membership.

    This mirrors `scripts/france.py:run_book("TECH", "CTO", "Interactive
    Brokers", 10_000, "monthly", "native")`; keeping it local avoids changing
    the shared `bot/france.py` API.
    """
    from bot import backtest, france, strategy, universe

    old_canonical = universe.canonical
    universe.canonical = lambda t: t
    try:
        kw = dict(
            start=START,
            leverage=2.5,
            financing_bps=500.0,
            trend_ma=200,
            trend_scale=0.5,
            rebalance_mode="calendar",
            exec_price="open",
            cost_bps=5.0,
            tax_lots="hifo",
        )
        kw.update(france.kwargs("TECH", "Interactive Brokers", "cto", capital, panel))
        cfg = strategy.Config(**kw)
        res = backtest.run(strategy.Market(panel, mem, cfg))
    finally:
        universe.canonical = old_canonical
    fx = france.fx_factor("TECH", res.equity.index[0], res.equity.index[-1])
    pre = float(res.equity.iloc[-1]) * capital * fx
    exit_tax = float(res.unrealized_tax) * capital * fx
    after = pre - exit_tax
    years = len(res.equity) / 252.0
    return {
        "after_tax": after,
        "after_tax_multiple": after / capital,
        "cagr": (after / capital) ** (1.0 / years) - 1.0,
        "maxdd": float((res.equity / res.equity.cummax() - 1.0).min()),
        "pre_tax_multiple": float(res.equity.iloc[-1] * fx),
        "fees": float(res.fees_paid) * capital * fx,
        "tax_paid": float(res.tax_paid) * capital * fx,
        "exit_tax": exit_tax,
        "trades": len(res.trades),
        "years": years,
        "fx": fx,
    }


def build_report(
    tech: list[str],
    panel: pd.DataFrame,
    membership: pd.DataFrame,
    baseline: dict,
    sp500_dated: dict,
    public_start: dict,
    coverage: pd.DataFrame,
    equal_weight: dict,
    missing_symbols: list[str],
    us_missing: dict | None = None,
) -> dict:
    facts = static_universe_facts(tech, membership, panel)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "facts": facts,
        "us_missing": us_missing or {},
        "baseline": baseline,
        "sp500_dated": sp500_dated,
        "public_at_start": public_start,
        "membership_coverage": coverage.to_dict(orient="records"),
        "equal_weight": equal_weight,
        "missing_symbols_count": len(missing_symbols),
        "missing_symbols_sample": missing_symbols[:40],
        "verdict": {
            "tech_static_biased": True,
            "safe_claims": [
                "The monthly rotation logic is reproducible on the committed cache.",
                "The after-tax multiple is an upper bound with today's winners in the universe.",
                "ETF-only benchmarks (QQQ, SMH) are survivorship-free at the fund level.",
            ],
            "unsafe_claims": [
                "That the TECH stock sleeve's alpha is investable without a point-in-time universe.",
                "That the 57x after-tax multiple is an expected forward return.",
                "That the static-49 momentum result generalises to the NASDAQ universe of 2015.",
            ],
        },
    }
