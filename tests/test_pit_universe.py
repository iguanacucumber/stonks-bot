"""Tests for the PIT Nasdaq-100 universe.

Run: uv run python tests/test_pit_universe.py
No network, no real cache required: history is committed; panel tests use a
temporary synthetic bar directory.
"""

from __future__ import annotations

import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot import universe  # noqa: E402
from bot.research.pit_universe import (  # noqa: E402
    build_pit_panel,
    coverage,
    load_history,
    membership_long,
    membership_on,
    pit_canonical,
)


def synthetic_bars(cache: Path, symbols: tuple[str, ...], days: int = 30) -> None:
    dates = pd.bdate_range("2020-01-01", periods=days)
    cache.mkdir(parents=True, exist_ok=True)
    for i, symbol in enumerate(symbols):
        px = 100.0 * np.exp(np.linspace(0.0, 0.05 + i * 0.01, days))
        df = pd.DataFrame(
            {
                "date": dates,
                "open": px,
                "high": px * 1.01,
                "low": px * 0.99,
                "close": px,
                "adjclose": px,
                "volume": 1_000_000,
            }
        )
        df.to_csv(cache / f"{symbol}.csv", index=False)


def synthetic_history(rows: list[tuple[str, str]]) -> pd.DataFrame:
    return pd.DataFrame(
        {"date": pd.to_datetime([d for d, _ in rows]), "tickers": [t for _, t in rows]}
    )


def test_history_is_sorted_and_unique():
    hist = load_history()
    assert len(hist) >= 100
    assert hist["date"].is_monotonic_increasing
    assert not hist["date"].duplicated().any()
    assert hist.iloc[0]["date"].year == 2007
    assert hist.iloc[-1]["date"] == pd.Timestamp("2026-05-18")


def test_membership_uses_latest_prior_snapshot_only():
    # TSLA entered 2013-07-15 and FB entered 2013-12-12; neither may appear
    # before its effective date.
    assert "TSLA" not in membership_on("2013-06-30")
    assert "TSLA" in membership_on("2013-07-16")
    assert "TSLA" not in membership_on("2013-01-01")
    assert "META" not in membership_on("2012-12-11")
    assert "META" in membership_on("2012-12-13")


def test_snapshot_equals_latest_row():
    hist = load_history()
    for _, row in hist.tail(20).iterrows():
        date = pd.Timestamp(row["date"])
        expected = {
            pit_canonical(t.strip())
            for t in str(row["tickers"]).split(",")
            if t.strip()
        }
        assert set(membership_on(date)) == expected
        # A day after the snapshot still uses it until the next change.
        after = date + pd.Timedelta(days=1)
        if after < hist["date"].max():
            nxt = hist.loc[hist["date"] > date, "date"].min()
            if after < nxt:
                assert set(membership_on(after)) == expected


def test_aliases_are_canonical_and_deduplicated():
    long = membership_long()
    # FB must become META, GOOG must become GOOGL, and neither date/ticker pair
    # may be duplicated after alias mapping.
    assert set(long["ticker"]) >= {"META", "GOOG", "GOOGL"}
    assert not long.duplicated(["date", "ticker"]).any()
    assert "FB" not in set(long["ticker"])


def test_coverage_reports_missing_bars():
    with TemporaryDirectory() as td:
        cache = Path(td)
        synthetic_bars(cache, ("AAPL", "MSFT"))
        hist = synthetic_history(
            [
                ("2020-01-01", "AAPL,MSFT,ZZZZ"),
                ("2020-02-01", "AAPL,MSFT,ZZZZ"),
            ]
        )
        cov = coverage(hist, cache)
        assert cov.unique_tickers == 3
        assert cov.available == 2
        assert cov.missing == 1
        assert cov.missing_tickers == ("ZZZZ",)


def test_panel_uses_qqq_as_benchmark_and_available_only():
    with TemporaryDirectory() as td:
        cache = Path(td)
        synthetic_bars(cache, ("AAPL", "MSFT", "QQQ", "SMH", "SOXX"))
        hist = synthetic_history(
            [
                ("2020-01-01", "AAPL,MSFT,ZZZZ"),
                ("2020-02-01", "AAPL,MSFT,ZZZZ"),
            ]
        )
        panel, mem, cov = build_pit_panel(hist, cache)
        tickers = set(panel["ticker"].unique())
        assert {"AAPL", "MSFT", "SPY", "SMH", "SOXX"} <= tickers
        assert "QQQ" not in tickers
        assert set(mem["ticker"].unique()) == {"AAPL", "MSFT"}
        assert cov.missing == 1
        # Membership dates are still the effective snapshot dates.
        assert set(pd.to_datetime(mem["date"])) == {
            pd.Timestamp("2020-01-01"),
            pd.Timestamp("2020-02-01"),
        }


def test_panel_alias_maps_fb_and_keeps_share_classes():
    with TemporaryDirectory() as td:
        cache = Path(td)
        synthetic_bars(cache, ("META", "GOOG", "GOOGL", "QQQ"))
        hist = synthetic_history([("2020-01-01", "FB,GOOG,GOOGL")])
        panel, mem, _ = build_pit_panel(hist, cache)
        assert set(mem["ticker"].unique()) == {"META", "GOOG", "GOOGL"}
        assert {"META", "GOOG", "GOOGL"} <= set(panel["ticker"].unique())


def main() -> None:
    checks = [
        test_history_is_sorted_and_unique,
        test_membership_uses_latest_prior_snapshot_only,
        test_snapshot_equals_latest_row,
        test_aliases_are_canonical_and_deduplicated,
        test_coverage_reports_missing_bars,
        test_panel_uses_qqq_as_benchmark_and_available_only,
        test_panel_alias_maps_fb_and_keeps_share_classes,
    ]
    orig = universe.canonical
    try:
        for fn in checks:
            fn()
            print(f"ok: {fn.__name__}")
    finally:
        universe.canonical = orig  # type: ignore[assignment]
    print(f"ok: {len(checks)} PIT checks")


if __name__ == "__main__":
    main()
