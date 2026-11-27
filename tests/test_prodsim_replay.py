"""Replay tests: deterministic ticks, cached bar loaders, gaps, live-paper feed."""

from __future__ import annotations

import math
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot.prodsim.replay import (  # noqa: E402
    BarDataError,
    LivePaperFeed,
    ReplayBar,
    ReplayFeed,
    generate_ticks,
    load_5m_bars,
    load_bars,
    load_daily_bars,
    synthetic_bars,
)


def _bar(symbol: str = "AAA", session: date = date(2026, 1, 2)) -> ReplayBar:
    return ReplayBar.daily(
        symbol,
        session,
        open_=100.0,
        high=103.0,
        low=99.0,
        close=102.0,
        volume=120_000.0,
    )


def _write_daily_cache(root: Path, symbol: str = "AAA", days: int = 10) -> date:
    bars = root / "bars"
    bars.mkdir(parents=True, exist_ok=True)
    rows = []
    last = None
    for i in range(days):
        d = date(2026, 1, 2) + timedelta(days=i)
        if d.weekday() >= 5:
            continue
        px = 100 + i
        rows.append(
            {
                "date": d.isoformat(),
                "open": px,
                "high": px + 2,
                "low": px - 2,
                "close": px + 1,
                "adjclose": px + 1,
                "volume": 1_000_000,
            }
        )
        last = d
    pd.DataFrame(rows).to_csv(bars / f"{symbol}.csv", index=False)
    return last or date(2026, 1, 2)


def test_generate_ticks_deterministic_and_bounded() -> None:
    bar = _bar()
    first = generate_ticks(bar, ticks_per_bar=12, seed=7)
    second = generate_ticks(bar, ticks_per_bar=12, seed=7)
    assert first == second
    assert len(first) == 12
    assert first[0].last == bar.open
    assert first[-1].last == bar.close
    stamps = [t.ts for t in first]
    assert stamps == sorted(stamps) and len(set(stamps)) == len(stamps)
    for tick in first:
        assert bar.low - 1e-9 <= tick.last <= bar.high + 1e-9
        assert tick.bid <= tick.ask
        assert tick.bid > 0 and tick.ask > 0
    assert math.isclose(sum(t.volume for t in first), bar.volume, rel_tol=1e-4)
    # Different seed changes the intermediate path, not the endpoints.
    other = generate_ticks(bar, ticks_per_bar=12, seed=8)
    assert other[0].last == first[0].last and other[-1].last == first[-1].last
    assert any(a.last != b.last for a, b in zip(other[1:-1], first[1:-1]))


def test_generate_ticks_single_and_invalid() -> None:
    one = generate_ticks(_bar(), ticks_per_bar=1)
    assert len(one) == 1 and one[0].last == _bar().open
    try:
        generate_ticks(_bar(), ticks_per_bar=0)
    except ValueError:
        pass
    else:
        raise AssertionError("accepted zero ticks per bar")
    try:
        ReplayBar.daily("BAD", date(2026, 1, 2), 100, 99, 98, 100)
    except ValueError:
        pass
    else:
        raise AssertionError("accepted high below open/close")


def test_replay_feed_monotonic_and_gaps() -> None:
    a = _bar("AAA")
    b = _bar("BBB")
    feed = ReplayFeed([b, a], seed=3, ticks_per_bar=4)
    ticks = list(feed)
    assert len(ticks) == 8
    stamps = [t.ts for t in ticks]
    assert stamps == sorted(stamps) and len(set(stamps)) == 8
    assert feed.sessions == (date(2026, 1, 2),)
    assert feed.outages(["AAA", "BBB"]) == []
    out = feed.outages(["AAA", "CCC"])
    assert out and out[0]["missing"] == ["CCC"]
    # Selecting a subset keeps ordering and drops the other symbol.
    only = list(feed.iter_ticks(["AAA"]))
    assert {t.symbol for t in only} == {"AAA"}


def test_synthetic_bars() -> None:
    bars = synthetic_bars(
        ["AAA", "BBB"], [date(2026, 1, 2), date(2026, 1, 5)], seed=4, volume=10_000
    )
    assert len(bars) == 4
    assert {b.symbol for b in bars} == {"AAA", "BBB"}
    again = synthetic_bars(
        ["AAA", "BBB"], [date(2026, 1, 2), date(2026, 1, 5)], seed=4, volume=10_000
    )
    assert bars == again


def test_load_daily_bars_csv_without_panel() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        last = _write_daily_cache(root)
        bars = load_daily_bars(["AAA"], date(2026, 1, 2), last, cache_dir=root)
        assert bars and all(b.symbol == "AAA" for b in bars)
        assert bars[-1].session == last
        feed = ReplayFeed(bars, ticks_per_bar=2)
        assert len(list(feed)) == len(bars) * 2
        try:
            load_daily_bars(["MISSING"], date(2026, 1, 2), last, cache_dir=root)
        except BarDataError as exc:
            assert "no cached bars" in str(exc)
        else:
            raise AssertionError("missing cache did not raise")
        try:
            load_daily_bars(["AAA"], last, date(2026, 1, 2), cache_dir=root)
        except BarDataError:
            pass
        else:
            raise AssertionError("reversed range did not raise")


def test_load_5m_bars_preferred() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_daily_cache(root)
        intraday = root / "bars5m"
        intraday.mkdir()
        rows = []
        base = datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc)
        for i in range(6):
            ts = base + timedelta(minutes=5 * i)
            rows.append(
                {
                    "ts": ts.isoformat(),
                    "open": 100 + i,
                    "high": 101 + i,
                    "low": 99 + i,
                    "close": 100.5 + i,
                    "volume": 10_000,
                }
            )
        pd.DataFrame(rows).to_csv(intraday / "AAA.csv", index=False)
        bars = load_bars(["AAA"], date(2026, 1, 2), date(2026, 1, 2), cache_dir=root)
        assert bars and all(b.source == "5m" for b in bars)
        feed = ReplayFeed(bars, ticks_per_bar=2)
        ticks = list(feed)
        assert len(ticks) == 12
        assert [t.ts for t in ticks] == sorted(t.ts for t in ticks)
        assert len(set(t.ts for t in ticks)) == 12
        assert (
            len(
                load_5m_bars(
                    ["AAA"], date(2026, 1, 2), date(2026, 1, 2), cache_dir=root
                )
            )
            == 6
        )


def test_live_paper_feed_polls_and_fails_closed() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        last = _write_daily_cache(root)
        now = datetime(last.year, last.month, last.day, 22, 0, tzinfo=timezone.utc)
        feed = LivePaperFeed(
            ["AAA"],
            cache_dir=root,
            poll_seconds=0.01,
            max_idle_polls=1,
            now_fn=lambda: now,
        )
        ticks = list(feed)
        assert ticks and all(t.symbol == "AAA" for t in ticks)
        assert [t.ts for t in ticks] == sorted(t.ts for t in ticks)
        assert len(set(t.ts for t in ticks)) == len(ticks)
        # The same feed object does not duplicate already-yielded bars.
        assert list(feed) == []
        stale_now = now + timedelta(days=30)
        stale = LivePaperFeed(
            ["AAA"],
            cache_dir=root,
            poll_seconds=0.01,
            max_idle_polls=1,
            max_stale_days=3,
            now_fn=lambda: stale_now,
        )
        try:
            list(stale)
        except BarDataError as exc:
            assert "stale" in str(exc)
        else:
            raise AssertionError("stale live-paper data did not fail closed")


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok: {name}")
    print("ok: prodsim replay")


if __name__ == "__main__":
    main()
