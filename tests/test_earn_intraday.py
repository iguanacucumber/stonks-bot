"""Earnings 2.02 intraday tests: event timing, no look-ahead, costs, cap.

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
from bot.research import earn_intraday as ei  # noqa: E402

TOL = 1e-9


def _bars(days: int = 7, start: str = "2024-01-02") -> pd.DataFrame:
    """Prior flat day, one event day, then flat higher sessions.

    The 16:00 row is an auction print and must never be an entry bar.
    """
    sessions = pd.bdate_range(start, periods=days)
    rows = []
    times = [
        (9, 30),
        (10, 30),
        (11, 30),
        (12, 30),
        (13, 30),
        (14, 30),
        (15, 30),
        (16, 0),
    ]
    for si, sess in enumerate(sessions):
        if si == 0:
            opens = [100.0] * 8
            closes = [100.0] * 8
        elif si == 1:
            opens = [101.0, 103.0, 105.0, 107.0, 109.0, 111.0, 113.0, 114.0]
            closes = [102.0, 104.0, 106.0, 108.0, 110.0, 112.0, 114.0, 114.0]
        else:
            base = 120.0 + (si - 2) * 10.0
            opens = [base] * 8
            closes = [base + 1.0] * 7 + [base + 1.0]
        for (hh, mm), op, cl in zip(times, opens, closes):
            rows.append(
                {
                    "ts": sess + pd.Timedelta(hours=hh, minutes=mm),
                    "session": sess,
                    "open": op,
                    "high": max(op, cl) + 0.5,
                    "low": min(op, cl) - 0.5,
                    "close": cl,
                    "volume": 0.0 if (hh, mm) == (16, 0) else 1000.0,
                }
            )
    return pd.DataFrame(rows)


def _events(times, ticker: str = "AAA") -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ticker": [ticker] * len(times),
            "filed": [pd.Timestamp(t).normalize() for t in times],
            "event_time": [pd.Timestamp(t) for t in times],
            "accepted": ["test"] * len(times),
            "items": ["2.02"] * len(times),
            "timestamp_source": ["accepted"] * len(times),
        }
    )


def test_event_loader_item_and_timestamp() -> None:
    assert _accepted_et("2024-01-02T15:15:00.000Z") == pd.Timestamp(
        "2024-01-02 10:15:00"
    )
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        payload = {
            "tickers": ["AAA"],
            "cik": "1",
            "filings": {
                "recent": {
                    "form": ["8-K", "8-K", "10-Q", "8-K"],
                    "filingDate": [
                        "2024-01-02",
                        "2024-01-02",
                        "2024-01-03",
                        "2024-01-04",
                    ],
                    "acceptanceDateTime": [
                        "2024-01-02T15:15:00.000Z",
                        "2024-01-02T15:16:00.000Z",
                        "2024-01-03T14:00:00.000Z",
                        "",
                    ],
                    "items": ["2.02", "2.02,9.01", "2.02", "2.02"],
                    "accessionNumber": [
                        "a-1",
                        "a-2",
                        "a-3",
                        "a-4",
                    ],
                }
            },
        }
        (root / "sub_1.json").write_text(json.dumps(payload))
        ev = ei.load_earnings_events(("AAA",), news_dir=root)
        assert len(ev) == 2
        first = ev[ev["filed"] == pd.Timestamp("2024-01-02")].iloc[0]
        assert first["event_time"] == pd.Timestamp("2024-01-02 10:15:00")
        assert first["timestamp_source"] == "accepted"
        assert "2.02" in first["items"]
        fallback = ev[ev["filed"] == pd.Timestamp("2024-01-04")].iloc[0]
        assert fallback["event_time"] == pd.Timestamp("2024-01-04 16:00:00")
        assert fallback["timestamp_source"] == "filed_close"


def test_reaction_bar_and_no_same_bar_entry() -> None:
    bars = _bars()
    # Event inside the 09:30-10:30 bar: reaction is the completed 10:30 bar,
    # entry is the 11:30 open.
    tr = ei.simulate_event(
        bars,
        pd.Timestamp("2024-01-03 10:15:00"),
        signal="gap",
        mode="continuation",
        exit_kind="close",
    )
    assert tr is not None
    assert tr["reaction_time"] == pd.Timestamp("2024-01-03 10:30:00")
    assert tr["entry_time"] == pd.Timestamp("2024-01-03 11:30:00")
    assert abs(tr["entry"] - 105.0) < TOL

    # Event exactly at a bar start: the first strictly-after bar is 11:30,
    # so entry is 12:30.
    tr2 = ei.simulate_event(
        bars,
        pd.Timestamp("2024-01-03 10:30:00"),
        signal="gap",
        mode="continuation",
        exit_kind="close",
    )
    assert tr2["reaction_time"] == pd.Timestamp("2024-01-03 11:30:00")
    assert tr2["entry_time"] == pd.Timestamp("2024-01-03 12:30:00")
    assert abs(tr2["entry"] - 107.0) < TOL

    # A 16:00 auction print is never the reaction or entry bar. If the event
    # is in the last hour, reaction is next session 09:30 and entry 10:30.
    tr3 = ei.simulate_event(
        bars,
        pd.Timestamp("2024-01-03 15:45:00"),
        signal="gap",
        mode="continuation",
        exit_kind="close",
    )
    assert tr3["reaction_time"] == pd.Timestamp("2024-01-04 09:30:00")
    assert tr3["entry_time"] == pd.Timestamp("2024-01-04 10:30:00")
    assert tr3["entry_time"].time() < pd.Timestamp("16:00").time()

    # After-close release: next session 09:30 reaction, 10:30 entry.
    tr4 = ei.simulate_event(
        bars,
        pd.Timestamp("2024-01-03 16:10:00"),
        signal="gap",
        mode="continuation",
        exit_kind="close",
    )
    assert tr4["reaction_time"] == pd.Timestamp("2024-01-04 09:30:00")
    assert tr4["entry_time"] == pd.Timestamp("2024-01-04 10:30:00")

    # A 15:30 reaction bar whose next row is the 16:00 auction must fill at
    # the next regular-session open, not at the auction print.
    tr5 = ei.simulate_event(
        bars,
        pd.Timestamp("2024-01-03 14:45:00"),
        signal="gap",
        mode="continuation",
        exit_kind="close",
    )
    assert tr5["reaction_time"] == pd.Timestamp("2024-01-03 15:30:00")
    assert tr5["entry_time"] == pd.Timestamp("2024-01-04 09:30:00")
    assert tr5["entry_time"].time() == pd.Timestamp("09:30").time()


def test_no_lookahead_future_bars_do_not_change_entry_or_signal() -> None:
    bars = _bars()
    event = pd.Timestamp("2024-01-03 10:15:00")
    tr = ei.simulate_event(
        bars, event, signal="move", mode="continuation", exit_kind="close"
    )
    assert tr is not None
    altered = bars.copy()
    future = altered["ts"] > tr["entry_time"]
    altered.loc[future, ["open", "close"]] *= 3.0
    tr2 = ei.simulate_event(
        altered, event, signal="move", mode="continuation", exit_kind="close"
    )
    assert tr2["reaction_time"] == tr["reaction_time"]
    assert tr2["entry_time"] == tr["entry_time"]
    assert abs(tr2["entry"] - tr["entry"]) < TOL
    assert abs(tr2["signal_value"] - tr["signal_value"]) < TOL
    assert tr2["direction"] == tr["direction"]


def test_exits_costs_borrow_and_conditions() -> None:
    bars = _bars()
    event = pd.Timestamp("2024-01-03 08:00:00")
    close = ei.simulate_event(
        bars,
        event,
        signal="gap",
        mode="continuation",
        exit_kind="close",
        cost_bps=5.0,
        borrow_bps=500.0,
    )
    nxt = ei.simulate_event(
        bars,
        event,
        signal="gap",
        mode="continuation",
        exit_kind="next_session",
        cost_bps=5.0,
        borrow_bps=500.0,
    )
    five = ei.simulate_event(
        bars,
        event,
        signal="gap",
        mode="continuation",
        exit_kind="5d",
        cost_bps=5.0,
        borrow_bps=500.0,
    )
    assert close["exit_time"] == pd.Timestamp("2024-01-03 16:00:00")
    assert nxt["exit_time"] == pd.Timestamp("2024-01-04 16:00:00")
    assert five["exit_time"] == pd.Timestamp("2024-01-10 16:00:00")
    assert abs(close["net"] - (close["gross"] - 0.001)) < TOL
    assert close["borrow"] == 0.0
    tr10 = ei.simulate_event(
        bars,
        event,
        signal="gap",
        mode="continuation",
        exit_kind="close",
        cost_bps=10.0,
        borrow_bps=1000.0,
    )
    assert abs(tr10["net"] - (tr10["gross"] - 0.002)) < TOL
    short = ei.simulate_event(
        bars,
        event,
        signal="gap",
        mode="fade",
        exit_kind="close",
        cost_bps=5.0,
        borrow_bps=500.0,
    )
    assert short["direction"] == -1.0
    assert short["borrow"] > 0.0
    assert abs(short["net"] - (-close["gross"] - 0.001 - short["borrow"])) < TOL

    # Threshold is applied to the chosen condition. The move is +2% here.
    move = ei.simulate_event(
        bars,
        event,
        signal="move",
        mode="continuation",
        exit_kind="close",
        threshold=0.02,
    )
    assert move is not None
    blocked = ei.simulate_event(
        bars,
        event,
        signal="move",
        mode="continuation",
        exit_kind="close",
        threshold=0.03,
    )
    assert blocked is None
    gap_blocked = ei.simulate_event(
        bars,
        event,
        signal="gap",
        mode="continuation",
        exit_kind="close",
        threshold=0.02,
    )
    assert gap_blocked is None


def test_concurrency_cap_and_same_bar_slot() -> None:
    t = pd.Timestamp("2024-01-03 10:30:00")
    trades = pd.DataFrame(
        {
            "entry_time": [t] * 6,
            "exit_fill_time": [t + pd.Timedelta(hours=1)] * 6,
        }
    )
    accepted, skipped = ei.select_concurrent(trades, max_concurrent=5)
    assert len(accepted) == 5
    assert skipped == 1

    # A position that exits at the 15:30 close still occupies the slot at the
    # 15:30 open; a new entry there must wait.
    trades2 = pd.DataFrame(
        {
            "entry_time": [t, pd.Timestamp("2024-01-03 15:30:00")],
            "exit_fill_time": [
                pd.Timestamp("2024-01-03 16:00:00"),
                pd.Timestamp("2024-01-03 16:00:00"),
            ],
        }
    )
    accepted2, skipped2 = ei.select_concurrent(trades2, max_concurrent=1)
    assert len(accepted2) == 1
    assert skipped2 == 1


def test_portfolio_single_trade_and_tax() -> None:
    bars = _bars()
    events = _events(["2024-01-03 08:00:00"])
    sessions = pd.DatetimeIndex(sorted(bars["session"].unique()))
    tr = ei.trades_for(
        {"AAA": bars},
        events,
        signal="gap",
        mode="continuation",
        exit_kind="close",
        cost_bps=5.0,
        borrow_bps=500.0,
    )
    assert len(tr) == 1
    port = ei.portfolio_daily(
        tr,
        {"AAA": bars},
        sessions,
        cost_bps=5.0,
        borrow_bps=500.0,
        max_concurrent=1,
        cash_returns=None,
    )
    s = ei.summarize(tr, portfolio=port, sessions=sessions)
    assert s["n"] == 1 and s["n_traded"] == 1
    # One trade, one slot, one session: the pre-tax path is exactly the trade
    # return; the after-tax path applies the annual French PFU.
    from bot.backtest import fr_rate

    expected = 1.0 + tr.iloc[0]["net"] * (1.0 - fr_rate(2024))
    assert abs(s["multiple"] - expected) < 1e-9
    assert np.isfinite(s["cagr"]) and np.isfinite(s["maxdd"])


def test_equal_active_sizing_uses_more_capital() -> None:
    bars = _bars()
    events = pd.DataFrame(
        {
            "ticker": ["AAA", "BBB"],
            "filed": [pd.Timestamp("2024-01-03")] * 2,
            "event_time": [pd.Timestamp("2024-01-03 08:00:00")] * 2,
            "accepted": ["test", "test"],
            "items": ["2.02", "2.02"],
            "timestamp_source": ["accepted", "accepted"],
        }
    )
    sessions = pd.DatetimeIndex(sorted(bars["session"].unique()))
    tr = ei.trades_for(
        {"AAA": bars, "BBB": bars},
        events,
        signal="gap",
        mode="continuation",
        exit_kind="close",
        cost_bps=5.0,
        borrow_bps=500.0,
    )
    fixed = ei.portfolio_daily(
        tr,
        {"AAA": bars, "BBB": bars},
        sessions,
        cost_bps=5.0,
        borrow_bps=500.0,
        max_concurrent=5,
        cash_returns=None,
        sizing="fixed_slots",
    )
    full = ei.portfolio_daily(
        tr,
        {"AAA": bars, "BBB": bars},
        sessions,
        cost_bps=5.0,
        borrow_bps=500.0,
        max_concurrent=5,
        cash_returns=None,
        sizing="equal_active",
    )
    s_fixed = ei.summarize(tr, portfolio=fixed, sessions=sessions)
    s_full = ei.summarize(tr, portfolio=full, sessions=sessions)
    assert s_fixed["n"] == 2 and s_full["n"] == 2
    # Both trades are profitable in the fixture; full capital compounds more.
    assert s_full["multiple"] > s_fixed["multiple"]


def test_placebo_deterministic_and_direction() -> None:
    bars = _bars(days=30)
    events = _events(
        ["2024-01-10 10:15:00", "2024-01-17 10:15:00", "2024-01-24 10:15:00"]
    )
    p1 = ei.placebo_means(
        {"AAA": bars},
        events,
        signal="gap",
        mode="continuation",
        threshold=0.0,
        exit_kind="close",
        cost_bps=5.0,
        borrow_bps=500.0,
        n=20,
        seed=7,
    )
    p2 = ei.placebo_means(
        {"AAA": bars},
        events,
        signal="gap",
        mode="continuation",
        threshold=0.0,
        exit_kind="close",
        cost_bps=5.0,
        borrow_bps=500.0,
        n=20,
        seed=7,
    )
    assert np.allclose(p1, p2, equal_nan=True)
    assert np.isfinite(p1).any()
    assert 0.0 <= ei.placebo_p(0.01, p1) <= 1.0


def test_summary_empty() -> None:
    empty = pd.DataFrame()
    sessions = pd.DatetimeIndex(pd.bdate_range("2024-01-02", periods=5))
    port = ei.portfolio_daily(empty, {}, sessions)
    s = ei.summarize(empty, portfolio=port, sessions=sessions)
    assert s["n"] == 0
    assert ei.year_folds(empty) == {}
    assert ei.max_concurrent_uncapped(empty) == 0


def main() -> None:
    test_event_loader_item_and_timestamp()
    test_reaction_bar_and_no_same_bar_entry()
    test_no_lookahead_future_bars_do_not_change_entry_or_signal()
    test_exits_costs_borrow_and_conditions()
    test_concurrency_cap_and_same_bar_slot()
    test_portfolio_single_trade_and_tax()
    test_equal_active_sizing_uses_more_capital()
    test_placebo_deterministic_and_direction()
    test_summary_empty()
    print("test_earn_intraday: all checks passed")


if __name__ == "__main__":
    main()
