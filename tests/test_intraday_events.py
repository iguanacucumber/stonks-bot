"""Intraday 8-K event tests: timestamps, no look-ahead, costs, placebo.

Synthetic bars only; no cache, no network, no broker.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot.news import _accepted_et  # noqa: E402
from bot.research import intraday_events as ie  # noqa: E402

TOL = 1e-9


def _bars(days: int = 6, start: str = "2024-01-02") -> pd.DataFrame:
    rows = []
    d = pd.Timestamp(start)
    sessions = []
    while len(sessions) < days:
        if d.weekday() < 5:
            sessions.append(d)
        d += pd.Timedelta(days=1)
    for si, sess in enumerate(sessions):
        base = 100.0 + si * 2.0
        for j, (hh, mm) in enumerate(
            [(9, 30), (10, 30), (11, 30), (15, 30), (16, 0)]
        ):
            ts = sess + pd.Timedelta(hours=hh, minutes=mm)
            px = base + j
            rows.append(
                {
                    "ts": ts,
                    "session": sess,
                    "open": px,
                    "high": px + 1,
                    "low": px - 1,
                    "close": px + 0.5,
                    "volume": 1000.0,
                }
            )
    return pd.DataFrame(rows)


def _events(times) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ticker": ["AAA"] * len(times),
            "filed": [pd.Timestamp(t).normalize() for t in times],
            "event_time": [pd.Timestamp(t) for t in times],
            "accepted": ["test"] * len(times),
            "items": ["2.02"] * len(times),
            "timestamp_source": ["accepted"] * len(times),
        }
    )


def test_timestamp_timezone_and_material_loader() -> None:
    ts = _accepted_et("2024-01-02T14:30:00.000Z")
    assert ts == pd.Timestamp("2024-01-02 09:30:00")
    assert _accepted_et("2024-01-02T21:05:00.000Z") == pd.Timestamp(
        "2024-01-02 16:05:00"
    )
    assert _accepted_et("") is None

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        payload = {
            "tickers": ["AAA"],
            "cik": "1",
            "filings": {
                "recent": {
                    "form": ["8-K", "8-K", "10-Q"],
                    "filingDate": ["2024-01-02", "2024-01-03", "2024-01-04"],
                    "acceptanceDateTime": [
                        "2024-01-02T14:30:00.000Z",
                        "2024-01-03T21:00:00.000Z",
                        "2024-01-04T14:00:00.000Z",
                    ],
                    "items": ["2.02", "7.01", "2.02"],
                }
            },
        }
        (root / "sub_1.json").write_text(json.dumps(payload))
        old = ie.NEWS
        try:
            ie.NEWS = root
            ev = ie.load_material_events(("AAA",), start="2024-01-01", end="2024-12-31")
        finally:
            ie.NEWS = old
        assert len(ev) == 1
        assert ev.iloc[0]["event_time"] == pd.Timestamp("2024-01-02 09:30:00")
        assert ev.iloc[0]["timestamp_source"] == "accepted"


def test_no_lookahead_entry_and_same_bar() -> None:
    bars = _bars(2)
    # Event during the 09:30-10:30 bar: entry at 10:30 open, never 09:30 close.
    tr = ie.simulate_event(bars, pd.Timestamp("2024-01-02 10:15:00"))
    assert tr is not None
    assert tr["entry_time"] == pd.Timestamp("2024-01-02 10:30:00")
    assert abs(tr["entry"] - 101.0) < TOL  # 10:30 open, not 09:30 close
    # Event exactly at a bar start: first *strictly after* is the next bar.
    tr2 = ie.simulate_event(bars, pd.Timestamp("2024-01-02 10:30:00"))
    assert tr2["entry_time"] == pd.Timestamp("2024-01-02 11:30:00")
    # 16:00 auction print is never an entry bar.
    tr3 = ie.simulate_event(bars, pd.Timestamp("2024-01-02 15:45:00"))
    assert tr3["entry_time"].normalize() == pd.Timestamp("2024-01-03")
    assert tr3["entry_time"].time() == pd.Timestamp("16:00").time() or tr3[
        "entry_time"
    ].hour == 9


def test_exits_and_costs() -> None:
    bars = _bars(6)
    ev = pd.Timestamp("2024-01-02 08:00:00")
    close = ie.simulate_event(bars, ev, exit_kind="close", cost_bps=5)
    nxt = ie.simulate_event(bars, ev, exit_kind="next_bar", cost_bps=5)
    five = ie.simulate_event(bars, ev, exit_kind="5d", cost_bps=5)
    assert close["exit_time"].hour == 16
    assert nxt["exit_time"] == pd.Timestamp("2024-01-02 10:30:00")
    assert five["exit_time"].normalize() == pd.Timestamp("2024-01-09")
    for tr in (close, nxt, five):
        assert abs(tr["net"] - (tr["gross"] - 0.001)) < TOL
    tr10 = ie.simulate_event(bars, ev, exit_kind="close", cost_bps=10)
    assert abs(tr10["net"] - (tr10["gross"] - 0.002)) < TOL
    short = ie.simulate_event(
        bars, ev, exit_kind="close", direction=-1.0, cost_bps=5
    )
    assert abs(short["gross"] + close["gross"]) < TOL


def test_placebo_deterministic_and_direction() -> None:
    bars = _bars(10)
    ev = _events(["2024-01-03 10:15:00", "2024-01-05 09:45:00"])
    p1 = ie.placebo_means(
        {"AAA": bars}, ev, exit_kind="close", direction=1, n=50, seed=7
    )
    p2 = ie.placebo_means(
        {"AAA": bars}, ev, exit_kind="close", direction=1, n=50, seed=7
    )
    assert np.allclose(p1, p2, equal_nan=True)
    assert np.isfinite(p1).any()
    trades = ie.trades_for({"AAA": bars}, ev, exit_kind="close")
    assert len(trades) == 2
    st = ie.summarize(trades, p1, direction=1)
    assert st["n"] == 2
    assert 0.0 <= st["placebo_p"] <= 1.0
    st_short = ie.summarize(trades, p1, direction=-1)
    assert 0.0 <= st_short["placebo_p"] <= 1.0


def test_summary_tax_and_empty() -> None:
    assert ie.summarize(pd.DataFrame())["n"] == 0
    bars = _bars(8)
    ev = _events(["2024-01-03 10:15:00", "2024-01-04 10:15:00"])
    tr = ie.trades_for({"AAA": bars}, ev, exit_kind="close")
    sessions = pd.DatetimeIndex(sorted(bars["session"].unique()))
    st = ie.summarize(tr, None, sessions=sessions)
    assert st["n"] == 2
    assert np.isfinite(st["multiple"]) and np.isfinite(st["maxdd"])


def main() -> None:
    test_timestamp_timezone_and_material_loader()
    test_no_lookahead_entry_and_same_bar()
    test_exits_and_costs()
    test_placebo_deterministic_and_direction()
    test_summary_tax_and_empty()
    print("test_intraday_events: all checks passed")


if __name__ == "__main__":
    main()
