"""Tests for v11 PEAD event timing, no-lookahead, costs and duplicates.

Run: uv run python tests/test_pead.py
No network; all data is synthetic.
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

from bot.research.pead.events import (  # noqa: E402
    attach_timing,
    eligible,
    load_events,
)
from bot.research.pead.metrics import after_tax_metrics  # noqa: E402
from bot.research.pead.portfolio import buy_hold, event_returns, simulate  # noqa: E402


def bars(n: int = 80, seed: int = 7) -> dict[str, pd.DataFrame]:
    idx = pd.bdate_range("2020-01-01", periods=n)
    rng = np.random.default_rng(seed)
    out = {}
    for ticker, drift in (("TEST", 0.0005), ("BIL", 0.0001), ("QQQ", 0.0008)):
        close = 100.0 * np.exp(np.cumsum(rng.normal(drift, 0.01, n)))
        open_ = close * np.exp(rng.normal(0.0, 0.004, n))
        volume = rng.integers(1_000_000, 2_000_000, n).astype(float)
        out[ticker] = pd.DataFrame(
            {
                "open": open_,
                "high": np.maximum(open_, close) * 1.01,
                "low": np.minimum(open_, close) * 0.99,
                "close": close,
                "adjclose": close,
                "volume": volume,
            },
            index=idx,
        )
    return out


def news_file(root: Path, rows: list[tuple[str, str, str, str]]) -> None:
    """rows: (ticker, accession, filingDate, acceptanceDateTime)."""
    payload = {
        "cik": "0000000001",
        "tickers": [rows[0][0]],
        "filings": {
            "recent": {
                "form": ["8-K"] * len(rows),
                "items": ["2.02,9.01"] * len(rows),
                "filingDate": [r[2] for r in rows],
                "acceptanceDateTime": [r[3] for r in rows],
                "accessionNumber": [r[1] for r in rows],
            }
        },
    }
    (root / "sub_0000000001.json").write_text(json.dumps(payload))


def test_entry_timing_preopen_intraday_afterclose_unknown() -> None:
    with TemporaryDirectory() as td:
        root = Path(td)
        news_file(
            root,
            [
                ("TEST", "a1", "2020-01-06", "2020-01-06T13:00:00Z"),  # 08:00 ET
                ("TEST", "a2", "2020-01-07", "2020-01-07T17:00:00Z"),  # 12:00 ET
                ("TEST", "a3", "2020-01-08", "2020-01-08T22:00:00Z"),  # 17:00 ET
                ("TEST", "a4", "2020-01-12", ""),  # unknown -> next session
            ],
        )
        ev = load_events(root)
        assert len(ev) == 4
        b = bars()
        t = attach_timing(ev, b, start="2020-01-01")
        got = dict(zip(t["accession"], t["entry_date"], strict=True))
        assert got["a1"] == pd.Timestamp("2020-01-06")  # pre-open same session
        assert got["a2"] == pd.Timestamp("2020-01-08")  # intraday -> next session
        assert got["a3"] == pd.Timestamp("2020-01-09")  # after close -> next session
        assert got["a4"] == pd.Timestamp("2020-01-13")  # unknown -> next session


def test_duplicates_collapse_and_unique_accessions_survive() -> None:
    with TemporaryDirectory() as td:
        root = Path(td)
        news_file(
            root,
            [
                ("TEST", "dup", "2020-01-06", "2020-01-06T13:00:00Z"),
                ("TEST", "dup", "2020-01-06", "2020-01-06T13:00:00Z"),
                ("TEST", "companion", "2020-01-06", "2020-01-07T12:00:00Z"),
                ("TEST", "later", "2020-01-20", "2020-01-20T13:00:00Z"),
            ],
        )
        ev = load_events(root)
        assert len(ev) == 2
        assert list(ev["accession"]) == ["dup", "later"]


def test_reaction_and_entry_ignore_future_bars() -> None:
    with TemporaryDirectory() as td:
        root = Path(td)
        news_file(root, [("TEST", "a1", "2020-01-06", "2020-01-06T13:00:00Z")])
        ev = load_events(root)
        b = bars()
        full = attach_timing(ev, b, start="2020-01-01")
        cut = attach_timing(ev, {"TEST": b["TEST"].iloc[:20]}, start="2020-01-01")
        assert full.iloc[0]["entry_date"] == cut.iloc[0]["entry_date"]
        assert full.iloc[0]["gap"] == cut.iloc[0]["gap"]
        # Changing only the entry-day close and later prices cannot change the
        # entry open / prior close / reaction.
        b2 = {k: v.copy() for k, v in b.items()}
        b2["TEST"].loc[b2["TEST"].index >= pd.Timestamp("2020-01-06"), "close"] *= 1.5
        after = attach_timing(ev, b2, start="2020-01-01")
        assert after.iloc[0]["gap"] == full.iloc[0]["gap"]
        assert after.iloc[0]["entry_open"] == full.iloc[0]["entry_open"]


def test_volume_z_uses_only_prior_sessions() -> None:
    with TemporaryDirectory() as td:
        root = Path(td)
        news_file(root, [("TEST", "a1", "2020-02-10", "2020-02-10T13:00:00Z")])
        ev = load_events(root)
        b = bars()
        full = attach_timing(ev, b, start="2020-01-01")
        b2 = {k: v.copy() for k, v in b.items()}
        entry = full.iloc[0]["entry_date"]
        b2["TEST"].loc[b2["TEST"].index >= entry, "volume"] *= 100
        after = attach_timing(ev, b2, start="2020-01-01")
        assert np.isclose(full.iloc[0]["volume_z"], after.iloc[0]["volume_z"])


def test_costs_reduce_returns_and_event_net_matches_trade() -> None:
    with TemporaryDirectory() as td:
        root = Path(td)
        news_file(root, [("TEST", "a1", "2020-01-06", "2020-01-06T13:00:00Z")])
        ev = load_events(root)
        b = bars()
        t = attach_timing(ev, b, start="2020-01-01")
        t["gap"] = 0.01
        low = simulate(t, b, hold=5, cost_bps=0.0, cash_yield=False)
        high = simulate(t, b, hold=5, cost_bps=50.0, cash_yield=False)
        assert high.equity.iloc[-1] < low.equity.iloc[-1]
        er0 = event_returns(t, b, hold=5, cost_bps=0.0)
        er5 = event_returns(t, b, hold=5, cost_bps=5.0)
        assert np.isclose(er5.iloc[0]["net"], er0.iloc[0]["net"] - 10.0 / 1e4)
        tr = low.trades.iloc[0]
        row = t.iloc[0]
        expected = (
            b["TEST"]["close"].iloc[row["entry_idx"] + 4] / row["entry_open"] - 1.0
        )
        assert np.isclose(tr["return"], expected, atol=1e-12)


def test_exit_is_fifth_session_and_slots_are_capped() -> None:
    with TemporaryDirectory() as td:
        root = Path(td)
        rows = [
            ("TEST", f"a{i}", "2020-01-06", "2020-01-06T13:00:00Z") for i in range(15)
        ]
        news_file(root, rows)
        ev = load_events(root)
        b = bars()
        t = attach_timing(ev, b, start="2020-01-01")
        t["gap"] = 0.01
        # The duplicate filter keeps one event; build synthetic extra rows to
        # test the slot cap independently of event identity.
        extra = pd.concat([t] * 15, ignore_index=True)
        extra["ticker"] = [f"T{i:02d}" for i in range(len(extra))]
        for ticker in extra["ticker"]:
            b[ticker] = b["TEST"].copy()
        extra["entry_idx"] = int(t.iloc[0]["entry_idx"])
        extra["entry_date"] = t.iloc[0]["entry_date"]
        extra["entry_open"] = t.iloc[0]["entry_open"]
        extra["prev_close"] = t.iloc[0]["prev_close"]
        extra["gap"] = 0.01
        res = simulate(extra, b, hold=5, cost_bps=0.0, cash_yield=False)
        assert res.max_concurrent <= 10
        assert res.skipped_slots > 0
        for tr in res.trades.itertuples(index=False):
            assert (
                tr.exit_date
                == b[tr.ticker].index[b[tr.ticker].index.get_loc(tr.entry_date) + 4]
            )


def test_after_tax_lowers_positive_result_and_buy_hold_window() -> None:
    idx = pd.bdate_range("2020-01-01", periods=504)
    r = pd.Series(0.001, index=idx)
    m = after_tax_metrics(r)
    assert m["after_tax_multiple"] < m["pretax_multiple"]
    assert m["after_tax_tax"] > 0
    b = bars(600)
    q = buy_hold(b, "QQQ", b["QQQ"].index[10], b["QQQ"].index[-2], cost_bps=5.0)
    assert len(q) > 100
    assert np.isfinite(q.iloc[-1])


def test_eligible_requires_complete_hold_and_reaction() -> None:
    with TemporaryDirectory() as td:
        root = Path(td)
        news_file(root, [("TEST", "a1", "2020-01-06", "2020-01-06T13:00:00Z")])
        ev = load_events(root)
        b = bars(n=30)
        t = attach_timing(ev, b, start="2020-01-01")
        # Reaction is positive by construction only if the synthetic gap is;
        # test the complete-hold rule with a forced positive gap.
        t["gap"] = 0.10
        assert len(eligible(t, b, hold=5, reaction="gap")) == 1
        t["entry_idx"] = 28
        t["entry_date"] = b["TEST"].index[28]
        assert len(eligible(t, b, hold=5, reaction="gap")) == 0


def main() -> None:
    checks = [
        test_entry_timing_preopen_intraday_afterclose_unknown,
        test_duplicates_collapse_and_unique_accessions_survive,
        test_reaction_and_entry_ignore_future_bars,
        test_volume_z_uses_only_prior_sessions,
        test_costs_reduce_returns_and_event_net_matches_trade,
        test_exit_is_fifth_session_and_slots_are_capped,
        test_after_tax_lowers_positive_result_and_buy_hold_window,
        test_eligible_requires_complete_hold_and_reaction,
    ]
    for fn in checks:
        fn()
        print(f"ok: {fn.__name__}")
    print(f"ok: {len(checks)} PEAD checks")


if __name__ == "__main__":
    main()
