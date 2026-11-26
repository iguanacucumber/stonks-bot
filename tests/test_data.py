"""Incremental cache invariants: no refetch when current, backoff, no rewrite."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot import data, universe  # noqa: E402


def bars(last: str, start: str = "2026-11-20") -> pd.DataFrame:
    dates = pd.bdate_range(start, last)
    px = 100.0 + np.arange(len(dates), dtype=float)
    return pd.DataFrame(
        {
            "date": dates,
            "open": px + 0.25,
            "high": px + 1.0,
            "low": px - 1.0,
            "close": px,
            "adjclose": px,
            "volume": 1_000_000,
        }
    )


def main() -> None:
    # 16:30 ET cutoff: before the close is still the prior session.
    utc = timezone.utc
    assert data.target_session(
        datetime(2026, 11, 25, 20, 0, tzinfo=utc)
    ) == pd.Timestamp("2026-11-24")
    assert data.target_session(
        datetime(2026, 11, 25, 21, 30, tzinfo=utc)
    ) == pd.Timestamp("2026-11-25")
    assert data.target_session(
        datetime(2026, 11, 28, 15, 0, tzinfo=utc)
    ) == pd.Timestamp("2026-11-27")

    with TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        cache, bars_dir = tmp / "cache", tmp / "bars"
        cache.mkdir()
        bars_dir.mkdir()
        target = pd.Timestamp("2026-11-25")

        old = (
            data.CACHE,
            data.BARS,
            data.MISSING,
            data._MISSING,
            data.target_session,
            data.fetch_one,
            data._get,
            data._parse,
            universe.canonical,
        )
        real_fetch = data.fetch_one
        data.CACHE, data.BARS = cache, bars_dir
        data.MISSING = cache / "missing.json"
        data._MISSING = {}
        data.target_session = lambda now=None: target
        universe.canonical = lambda t: t
        calls: list[tuple[str, pd.Timestamp | None]] = []

        def fetch_at(last: str):
            def one(symbol: str, force: bool = False, since=None) -> pd.DataFrame:
                calls.append((symbol, since))
                df = bars(last)
                df.to_csv(bars_dir / f"{symbol.replace('/', '_')}.csv", index=False)
                return df

            return one

        current = fetch_at(str(target.date()))

        data.fetch_one = current
        try:
            for sym in ("SPY", "QQQ"):
                bars(str(target.date())).to_csv(
                    bars_dir / f"{sym.replace('/', '_')}.csv", index=False
                )
            out = data.update_symbols(["SPY", "QQQ"], gate="SPY")
            assert out == {}, "current cache fetched"
            assert calls == [], f"current cache made network calls: {calls}"

            # holiday / unpublished close: one benchmark probe, no universe sweep
            for sym in ("SPY", "QQQ"):
                bars("2026-11-25").to_csv(
                    bars_dir / f"{sym.replace('/', '_')}.csv", index=False
                )
            data.target_session = lambda now=None: pd.Timestamp("2026-11-26")
            calls.clear()
            data.update_symbols(["SPY", "QQQ"], gate="SPY")
            assert calls == [("SPY", pd.Timestamp("2026-11-25"))], (
                f"closed market swept the universe: {calls}"
            )

            # dead ticker: no request inside the 7-day backoff
            data.target_session = lambda now=None: target
            data._MISSING = {"DEAD": target.date().isoformat()}
            calls.clear()
            data.update_symbols(["SPY", "DEAD"], gate="SPY")
            assert calls == [], f"dead ticker retried inside backoff: {calls}"

            # stale symbol: fetch only that symbol, from its cached date
            bars("2026-11-24").to_csv(bars_dir / "AAPL.csv", index=False)
            calls.clear()
            out = data.update_symbols(["SPY", "AAPL"], gate="SPY")
            assert calls == [("AAPL", pd.Timestamp("2026-11-24"))], calls
            assert set(out) == {"AAPL"}

            # corporate action: incremental overlap restates history -> full refetch
            data._MISSING = {}
            old_bars = bars(str(target.date()))
            old_bars.to_csv(bars_dir / "MSFT.csv", index=False)
            restated = old_bars.copy()
            restated["adjclose"] = restated["adjclose"] * 0.99
            restated["close"] = restated["close"] * 0.99
            starts: list[int] = []

            def fake_get(symbol, start=data.START):
                starts.append(start)
                return {"call": len(starts)}

            def fake_parse(res):
                return restated if res["call"] == 1 else restated

            data._get, data._parse = fake_get, fake_parse
            df = real_fetch("MSFT", since=target)
            assert starts[:2] == [data._since_start(target), data.START], (
                f"restated history was not refetched: {starts}"
            )
            assert abs(df["adjclose"].iloc[0] - restated["adjclose"].iloc[0]) < 1e-9
            first_mtime = (bars_dir / "MSFT.csv").stat().st_mtime_ns
            starts.clear()
            again = real_fetch("MSFT", since=target)
            assert starts == [data._since_start(target)], "unchanged bar refetched full"
            assert (bars_dir / "MSFT.csv").stat().st_mtime_ns == first_mtime, (
                "unchanged bars were rewritten"
            )
            assert len(again) == len(df)

            # panel: hold on a closed market, write once on a new bar, then no fetch
            for sym in ("AAPL", "SPY", "QQQ", "SOXX", "SMH"):
                bars("2026-11-24").to_csv(
                    bars_dir / f"{sym.replace('/', '_')}.csv", index=False
                )
            panel = pd.concat(
                [data._panel_rows(s, bars("2026-11-24")) for s in ("AAPL", "SPY")],
                ignore_index=True,
            )
            panel.to_parquet(cache / "panel.parquet", index=False)
            mem = pd.DataFrame({"date": [target], "ticker": ["AAPL"]})
            held_mtime = (cache / "panel.parquet").stat().st_mtime_ns
            calls.clear()
            data.fetch_one = fetch_at("2026-11-24")
            held = data.update_panel(mem)
            assert held["date"].max() == pd.Timestamp("2026-11-24")
            assert calls == [("SPY", pd.Timestamp("2026-11-24"))], (
                f"closed panel swept members: {calls}"
            )
            assert (cache / "panel.parquet").stat().st_mtime_ns == held_mtime, (
                "closed panel was rewritten"
            )

            data.fetch_one = fetch_at(str(target.date()))
            calls.clear()
            fresh = data.update_panel(mem)
            assert fresh["date"].max() == target, "panel did not advance"
            assert {c[0] for c in calls} == {"AAPL", "SPY", "QQQ", "SOXX", "SMH"}
            fresh_mtime = (cache / "panel.parquet").stat().st_mtime_ns
            calls.clear()
            again = data.update_panel(mem)
            assert calls == [], f"current panel fetched: {calls}"
            assert (cache / "panel.parquet").stat().st_mtime_ns == fresh_mtime, (
                "current panel was rewritten"
            )
            assert len(again) == len(fresh)

            # a closed session after a current panel: one probe, no rewrite
            data.target_session = lambda now=None: pd.Timestamp("2026-11-26")
            data.fetch_one = fetch_at("2026-11-25")
            calls.clear()
            data.update_panel(mem)
            assert calls == [("SPY", pd.Timestamp("2026-11-25"))], calls
            assert (cache / "panel.parquet").stat().st_mtime_ns == fresh_mtime, (
                "closed day rewrote panel"
            )
        finally:
            (
                data.CACHE,
                data.BARS,
                data.MISSING,
                data._MISSING,
                data.target_session,
                data.fetch_one,
                data._get,
                data._parse,
                universe.canonical,
            ) = old

    print("ok: data cache incremental, backoff, closed-market gate, no rewrite")


if __name__ == "__main__":
    main()
