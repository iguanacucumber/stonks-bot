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
    assert data.target_session(
        datetime(2026, 11, 28, 23, 0, tzinfo=utc)
    ) == pd.Timestamp("2026-11-27")
    assert data.target_session(
        datetime(2026, 11, 29, 23, 0, tzinfo=utc)
    ) == pd.Timestamp("2026-11-27")
    # Thanksgiving 11-26 is never a target; half-day 11-27 closes at 13:00 ET.
    assert data.target_session(
        datetime(2026, 11, 26, 22, 0, tzinfo=utc)
    ) == pd.Timestamp("2026-11-25")
    assert data.target_session(
        datetime(2026, 11, 27, 18, 29, tzinfo=utc)
    ) == pd.Timestamp("2026-11-25")
    assert data.target_session(
        datetime(2026, 11, 27, 18, 30, tzinfo=utc)
    ) == pd.Timestamp("2026-11-27")

    parsed = data._parse(
        {
            "timestamp": [1_764_086_400, 1_764_172_800],
            "indicators": {
                "quote": [
                    {
                        "open": [100.0, 100.0],
                        "high": [101.0, 101.0],
                        "low": [99.0, 99.0],
                        "close": [100.0, 0.0],
                        "volume": [1_000_000, 1_000_000],
                    }
                ],
                "adjclose": [{"adjclose": [100.0, 100.0]}],
            },
        }
    )
    assert len(parsed) == 1 and float(parsed["close"].iloc[0]) == 100.0

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

        # an intentionally empty JSON must not resurrect stale text entries
        (cache / "missing.json").write_text("{}")
        (cache / "missing_symbols.txt").write_text("ANCIENT\n")
        data._MISSING = None
        assert "ANCIENT" not in data._missing()
        data._MISSING = {}

        def fetch_at(last: str):
            def one(
                symbol: str, force: bool = False, since=None, target=None
            ) -> pd.DataFrame:
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

            # a failed full refetch must keep the old consistent cache, not
            # splice restated recent bars onto unadjusted history
            old_bars.to_csv(bars_dir / "MSFT2.csv", index=False)
            starts.clear()

            def fake_get2(symbol, start=data.START):
                starts.append(start)
                return {"call": len(starts)}

            def fake_parse2(res):
                return restated if res["call"] == 1 else pd.DataFrame()

            data._get, data._parse = fake_get2, fake_parse2
            kept = real_fetch("MSFT2", since=target)
            assert starts[:2] == [data._since_start(target), data.START]
            assert abs(kept["adjclose"].iloc[0] - old_bars["adjclose"].iloc[0]) < 1e-9
            saved = pd.read_csv(bars_dir / "MSFT2.csv")
            assert abs(saved["adjclose"].iloc[0] - old_bars["adjclose"].iloc[0]) < 1e-9

            # a truncated "full" response must not delete cached history
            long_bars = bars(str(target.date()), start="2026-11-01")
            long_bars.to_csv(bars_dir / "MSFT3.csv", index=False)
            long_restated = long_bars.copy()
            long_restated["adjclose"] *= 0.99
            long_restated["close"] *= 0.99
            starts.clear()

            def fake_get3(symbol, start=data.START):
                starts.append(start)
                return {"call": len(starts)}

            def fake_parse3(res):
                return long_restated if res["call"] == 1 else long_restated.tail(2)

            data._get, data._parse = fake_get3, fake_parse3
            kept = real_fetch("MSFT3", since=target)
            assert len(kept) == len(long_bars), "truncated full refetch deleted history"

            # volume/OHLC-only corrections merge and rewrite, even when the
            # adjusted close is unchanged
            old_bars.to_csv(bars_dir / "MSFT4.csv", index=False)
            corrected = old_bars.copy()
            corrected["volume"] = 9_000_000
            corrected["high"] = corrected["high"] + 50.0
            data._get = lambda symbol, start=data.START: {"ok": 1}
            data._parse = lambda res: corrected
            fixed = real_fetch("MSFT4", since=target)
            assert float(fixed["volume"].iloc[-1]) == 9_000_000
            assert float(fixed["high"].iloc[-1]) == corrected["high"].iloc[-1]
            saved = pd.read_csv(bars_dir / "MSFT4.csv")
            assert float(saved["volume"].iloc[-1]) == 9_000_000
            assert data._overlap_changed(old_bars, corrected), (
                "OHLC/volume-only restatement was not detected"
            )

            # panel slices with the same shape but restated values are not equal
            pa = data._panel_rows("A", old_bars)
            pb = pa.copy()
            pb["adjclose"] *= 0.99
            assert not data._panel_equal(pa, pb), "panel restatement treated as equal"

            # panel: hold on a closed market, write once on a new bar, then no fetch
            for sym in ("AAPL", "SPY", "QQQ", "SOXX", "SMH"):
                bars("2026-11-24").to_csv(
                    bars_dir / f"{sym.replace('/', '_')}.csv", index=False
                )
            bars("2020-01-02", start="2019-12-20").to_csv(
                bars_dir / "OLD.csv", index=False
            )
            panel = pd.concat(
                [data._panel_rows(s, bars("2026-11-24")) for s in ("AAPL", "SPY")]
                + [data._panel_rows("OLD", bars("2020-01-02", start="2019-12-20"))],
                ignore_index=True,
            )
            panel.to_parquet(cache / "panel.parquet", index=False)
            mem = pd.DataFrame(
                {
                    "date": [target, *([pd.Timestamp("2020-01-02")] * 2)],
                    "ticker": ["AAPL", "OLD", "GONE"],
                }
            )
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
            assert "OLD" not in {c[0] for c in calls}, "delisted member was fetched"
            assert "GONE" not in {c[0] for c in calls}, "dead delisted symbol retried"
            fresh_mtime = (cache / "panel.parquet").stat().st_mtime_ns
            calls.clear()
            again = data.update_panel(mem)
            assert calls == [], f"current panel fetched: {calls}"
            assert (cache / "panel.parquet").stat().st_mtime_ns == fresh_mtime, (
                "current panel was rewritten"
            )
            assert len(again) == len(fresh)

            # a per-ticker hole is repaired from cache even when the panel's
            # global last date is already the target
            holed = fresh[~((fresh["ticker"] == "AAPL") & (fresh["date"] == target))]
            holed.to_parquet(cache / "panel.parquet", index=False)
            calls.clear()
            data.fetch_one = fetch_at(str(target.date()))
            repaired = data.update_panel(mem)
            assert (
                (repaired["ticker"] == "AAPL") & (repaired["date"] == target)
            ).any(), "per-ticker panel hole was not repaired"
            assert "AAPL" in {c[0] for c in calls}, "hole repair did not use cache"
            fresh = repaired
            fresh_mtime = (cache / "panel.parquet").stat().st_mtime_ns

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
