"""Tests for v8 intraday and daily short-horizon research.

Run: uv run python tests/test_short3.py
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

from bot.research.short3 import intraday as intraday_mod  # noqa: E402
from bot.research.short3.daily import (  # noqa: E402
    open_close_stats,
    volatility_gap_fade,
)
from bot.research.short3.intraday import (  # noqa: E402
    gap_fade,
    load_bars,
    summarize_trades,
)


def session_bars() -> pd.DataFrame:
    ts = pd.to_datetime(
        [
            "2024-01-02 09:30", "2024-01-02 10:30", "2024-01-02 15:30",
            "2024-01-03 09:30", "2024-01-03 10:30", "2024-01-03 15:30",
        ]
    )
    return pd.DataFrame(
        {
            "ts": ts,
            "session": ts.normalize(),
            "open": [100, 100, 100, 102, 102.1, 102],
            "high": [101, 101, 101, 103, 103, 103],
            "low": [99, 99, 99, 101, 101, 99],
            "close": [100, 100, 100, 102, 102, 101],
            "volume": [1000] * 6,
        }
    )


def test_load_bars_timezone_and_session_filter():
    with TemporaryDirectory() as td:
        root = Path(td)
        # 14:30 UTC = 09:30 ET, 21:00 UTC = 16:00 ET, 12:00 UTC = 07:00 ET.
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
        old = intraday_mod.CACHE
        intraday_mod.CACHE = root
        try:
            out = load_bars("SPY", "1h", "2y")
        finally:
            intraday_mod.CACHE = old
        assert len(out) == 2
        assert out["ts"].dt.tz is None
        assert out["ts"].dt.hour.tolist() == [9, 16]
        assert out["session"].nunique() == 1


def test_gap_fade_uses_next_bar_open_and_no_future_entry():
    bars = session_bars()
    t = gap_fade(bars, gap_bps=50, cost_bps=0)
    assert len(t) == 1
    assert t.iloc[0]["direction"] == -1.0
    assert abs(t.iloc[0]["entry"] - 102.1) < 1e-9
    assert abs(t.iloc[0]["exit"] - 101.0) < 1e-9
    bars2 = bars.copy()
    bars2.loc[bars2.index[-1], "close"] = 90.0
    t2 = gap_fade(bars2, gap_bps=50, cost_bps=0)
    assert abs(t2.iloc[0]["entry"] - t.iloc[0]["entry"]) < 1e-9
    assert t2.iloc[0]["net"] != t.iloc[0]["net"]


def test_costs_reduce_intraday_wealth():
    bars = session_bars()
    free = gap_fade(bars, gap_bps=50, cost_bps=0)
    costly = gap_fade(bars, gap_bps=50, cost_bps=100)
    assert costly.iloc[0]["net"] < free.iloc[0]["net"]
    s = summarize_trades(costly, 2)
    assert s["n"] == 1
    assert 0 <= s["hit"] <= 1


def test_daily_gap_z_no_lookahead():
    idx = pd.bdate_range("2020-01-01", periods=120)
    rng = np.random.default_rng(3)
    gap = rng.normal(0, 0.01, len(idx))
    open_ = np.empty(len(idx))
    close = np.empty(len(idx))
    prev = 100.0
    for i in range(len(idx)):
        open_[i] = prev * (1.0 + gap[i])
        close[i] = open_[i] * (1.0 + rng.normal(0, 0.01))
        prev = close[i]
    df = pd.DataFrame({"open": open_, "close": close}, index=idx)
    f1 = volatility_gap_fade(df, entry_z=0.0, cost_bps=5)
    df2 = df.copy()
    df2.iloc[-1, df2.columns.get_loc("close")] *= 1.10
    f2 = volatility_gap_fade(df2, entry_z=0.0, cost_bps=5)
    assert np.allclose(f1["z"].iloc[:-2], f2["z"].iloc[:-2], equal_nan=True)
    assert np.allclose(f1["net"].iloc[:-2], f2["net"].iloc[:-2], equal_nan=True)


def test_open_close_stats_are_fractions():
    idx = pd.bdate_range("2020-01-01", periods=50)
    df = pd.DataFrame({"open": 100.0, "close": 101.0}, index=idx)
    s = open_close_stats(df, start="2020-01-01")
    assert abs(s["o2c_mean"] - 0.01) < 1e-12
    assert s["o2c_hit"] == 1.0


def main() -> None:
    checks = [
        test_load_bars_timezone_and_session_filter,
        test_gap_fade_uses_next_bar_open_and_no_future_entry,
        test_costs_reduce_intraday_wealth,
        test_daily_gap_z_no_lookahead,
        test_open_close_stats_are_fractions,
    ]
    for fn in checks:
        fn()
        print(f"ok: {fn.__name__}")
    print(f"ok: {len(checks)} short3 checks")


if __name__ == "__main__":
    main()
