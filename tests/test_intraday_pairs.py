"""Tests for v9 intraday lead-lag, pairs and cross-sectional research.

Run: uv run python tests/test_intraday_pairs.py
No network; synthetic bars only.
"""

from __future__ import annotations

import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot.research.intra_pairs.lead_lag import lead_lag_trades  # noqa: E402
from bot.research.intra_pairs.pairs import pair_trades  # noqa: E402
from bot.research.intra_pairs.xsec import xsec_trades  # noqa: E402
from bot.research.short3 import intraday as short3_intraday  # noqa: E402
from bot.research.short3.intraday import load_bars, summarize_trades  # noqa: E402


def make_bars(prices, start="2024-01-02 09:30", freq="1h") -> pd.DataFrame:
    ts = pd.date_range(start, periods=len(prices), freq=freq)
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


def test_load_bars_session_and_tz():
    with TemporaryDirectory() as td:
        root = Path(td)
        df = pd.DataFrame(
            {
                "ts": [
                    "2024-01-02 12:00:00+00:00",
                    "2024-01-02 14:30:00+00:00",
                    "2024-01-02 21:00:00+00:00",
                ],
                "open": [1, 2, 3],
                "close": [1, 2, 3],
                "high": [1, 2, 3],
                "low": [1, 2, 3],
                "volume": [1, 1, 1],
            }
        )
        df.to_csv(root / "SPY_1h_2y.csv", index=False)
        old = short3_intraday.CACHE
        short3_intraday.CACHE = root
        try:
            out = load_bars("SPY", "1h", "2y")
        finally:
            short3_intraday.CACHE = old
        assert len(out) == 2
        assert out["ts"].dt.tz is None
        assert out["ts"].dt.hour.tolist() == [9, 16]


def test_lead_lag_uses_next_bar_target():
    src = make_bars([100, 101, 102, 103])
    tgt = make_bars([100, 100, 100, 100])
    # Target bars: close = open*1.001; bar after source signal is bar 1.
    t = lead_lag_trades(src, tgt, lag=1, threshold=0.0, mode="momentum", cost_bps=0)
    assert not t.empty
    assert abs(t.iloc[0]["gross"] - (100 * 1.001 / 100 - 1.0)) < 1e-12
    # Changing target bar 3 cannot change the bar-1 trade.
    tgt2 = tgt.copy()
    tgt2.loc[3, "open"] = 50.0
    t2 = lead_lag_trades(src, tgt2, lag=1, threshold=0.0, mode="momentum", cost_bps=0)
    assert abs(t2.iloc[0]["gross"] - t.iloc[0]["gross"]) < 1e-12


def test_lead_lag_costs_reduce_net():
    src = make_bars([100, 101, 102, 103])
    tgt = make_bars([100, 100, 100, 100])
    free = lead_lag_trades(src, tgt, lag=1, threshold=0.0, cost_bps=0)
    cost = lead_lag_trades(src, tgt, lag=1, threshold=0.0, cost_bps=100)
    assert cost.iloc[0]["net"] < free.iloc[0]["net"]
    s = summarize_trades(cost, 1)
    assert s["n"] == len(cost)


def test_pair_and_xsec_costs_and_no_future():
    a = make_bars(np.linspace(100, 110, 30))
    b = make_bars(np.linspace(100, 101, 30))
    t = pair_trades(a, b, lookback=10, entry_z=0.5, exit_z=0.0, max_hold=2, mode="long_only", cost_bps=5)
    if not t.empty:
        cheap = pair_trades(a, b, lookback=10, entry_z=0.5, exit_z=0.0, max_hold=2, mode="long_only", cost_bps=0)
        assert t["net"].sum() < cheap["net"].sum()
        assert (t["entry_ts"] > t["session"]).all()
    bars = {
        "A": make_bars(np.linspace(100, 120, 30)),
        "B": make_bars(np.linspace(100, 101, 30)),
        "C": make_bars(np.linspace(100, 99, 30)),
        "D": make_bars(np.linspace(100, 105, 30)),
    }
    x1 = xsec_trades(bars, lookback=1, top_n=1, mode="long_only", cost_bps=5)
    assert not x1.empty
    bars2 = {k: v.copy() for k, v in bars.items()}
    bars2["A"].loc[bars2["A"].index[-1], "open"] *= 2.0
    x2 = xsec_trades(bars2, lookback=1, top_n=1, mode="long_only", cost_bps=5)
    assert np.allclose(x1["net"].iloc[:-1], x2["net"].iloc[:-1])


def main() -> None:
    checks = [
        test_load_bars_session_and_tz,
        test_lead_lag_uses_next_bar_target,
        test_lead_lag_costs_reduce_net,
        test_pair_and_xsec_costs_and_no_future,
    ]
    for fn in checks:
        fn()
        print(f"ok: {fn.__name__}")
    print(f"ok: {len(checks)} intraday-pairs checks")


if __name__ == "__main__":
    main()
