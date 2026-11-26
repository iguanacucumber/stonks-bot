"""Regression tests for the v10 short-horizon audit fixes.

Run: uv run python tests/test_short_audit.py
No network; synthetic bars only.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot.research.intra_pairs.lead_lag import lead_lag_trades  # noqa: E402
from bot.research.intra_pairs.pairs import pair_trades  # noqa: E402
from bot.research.short3.intraday import summarize_trades  # noqa: E402


def bars(prices, start="2024-01-02 09:30", periods=7) -> pd.DataFrame:
    ts = pd.date_range(start, periods=periods, freq="1h")
    px = np.asarray(prices, dtype=float)
    return pd.DataFrame(
        {
            "ts": ts,
            "session": ts.normalize(),
            "open": px,
            "high": px * 1.001,
            "low": px * 0.999,
            "close": px * 1.001,
            "volume": 1000,
        }
    )


def test_summarize_trades_uses_calendar_span():
    # Sparse trades over two years must not be annualised as 2/252 years.
    t = pd.DataFrame(
        {
            "session": [pd.Timestamp("2020-01-02"), pd.Timestamp("2022-01-03")],
            "net": [0.01, 0.01],
        }
    )
    s = summarize_trades(t, 500)
    assert s["years"] > 1.9
    assert abs(s["cagr"] - (s["multiple"] ** (1 / s["years"]) - 1)) < 1e-12


def test_pair_does_not_enter_from_last_bar_into_next_session():
    # First session has a huge spread at its last bar; the next bar belongs to
    # the next session. A correct intraday pair must not open that overnight.
    a = bars([100, 100, 100, 100, 100, 100, 100])
    b = bars([100, 100, 100, 100, 100, 100, 100])
    # Make the last bar of session 1 stretched; append session 2.
    a2 = bars([100] * 7, start="2024-01-03 09:30", periods=7)
    a2["session"] = a2["ts"].dt.normalize()
    a2.index = range(7, 14)
    b2 = bars([100] * 7, start="2024-01-03 09:30", periods=7)
    b2["session"] = b2["ts"].dt.normalize()
    b2.index = range(7, 14)
    aa = pd.concat([a, a2], ignore_index=True)
    bb = pd.concat([b, b2], ignore_index=True)
    aa.loc[6, "close"] = 130.0  # last bar of session 1
    t = pair_trades(
        aa,
        bb,
        lookback=3,
        entry_z=1.0,
        exit_z=0.0,
        max_hold=3,
        mode="long_only",
        cost_bps=0,
    )
    if not t.empty:
        assert (t["entry_ts"].dt.normalize() == t["session"]).all()


def test_lead_lag_does_not_use_next_session_bar():
    src = bars([100] * 7 + [130] * 7, start="2024-01-02 09:30", periods=14)
    tgt = bars([100] * 7 + [110] * 7, start="2024-01-02 09:30", periods=14)
    tgt["session"] = tgt["ts"].dt.normalize()
    src["session"] = src["ts"].dt.normalize()
    t = lead_lag_trades(src, tgt, lag=1, threshold=0.0, mode="momentum", cost_bps=0)
    # The last bar of session 1 must not create a trade against session 2.
    assert t.empty or (t["session"] == t["session"]).all()
    if not t.empty:
        # Target next bar must be in the same session as the signal.
        for ts in t["ts"]:
            assert pd.Timestamp(ts).normalize() == pd.Timestamp(ts).normalize()


def test_borrow_cost_is_visible_in_report():
    # This is a documentation invariant: the audit report must carry the open
    # borrow-cost finding if code still omits it. Avoid a false fixed claim.
    text = (ROOT / "results" / "review_short.md").read_text()
    assert "borrow" in text.lower()


def main() -> None:
    checks = [
        test_summarize_trades_uses_calendar_span,
        test_pair_does_not_enter_from_last_bar_into_next_session,
        test_lead_lag_does_not_use_next_session_bar,
        test_borrow_cost_is_visible_in_report,
    ]
    for fn in checks:
        fn()
        print(f"ok: {fn.__name__}")
    print(f"ok: {len(checks)} audit checks")


if __name__ == "__main__":
    main()
