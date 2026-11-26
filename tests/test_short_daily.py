"""Tests for daily sector reversal, calendar and breakout signals.

Run: uv run python tests/test_short_daily.py
No cache/network: synthetic prices only.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot.research.short3.daily import after_tax_series  # noqa: E402
from bot.research.short4.daily import (  # noqa: E402
    breakout_returns,
    calendar_mask,
    calendar_placebo,
    calendar_returns,
    sector_reversal_returns,
)


def _prices(seed: int = 3, n: int = 260) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2020-01-01", periods=n)
    closes = {}
    for i in range(5):
        closes[f"T{i}"] = pd.Series(
            100 * np.exp(np.cumsum(rng.normal(0.0002, 0.01, n))), index=idx
        )
    return pd.DataFrame(closes)


def test_sector_no_lookahead() -> None:
    px = _prices()
    closes = {c: px[c] for c in px.columns}
    full, _, active = sector_reversal_returns(
        closes, lookback=5, hold=5, top_n=2, mode="long_only", cost_bps=5
    )
    cut = 200
    part_closes = {c: s.iloc[:cut] for c, s in closes.items()}
    part, _, _ = sector_reversal_returns(
        part_closes, lookback=5, hold=5, top_n=2, mode="long_only", cost_bps=5
    )
    assert np.allclose(full.iloc[:cut].to_numpy(), part.to_numpy())
    assert active.iloc[:cut].any()


def test_calendar_masks_and_timing() -> None:
    idx = pd.bdate_range("2020-01-01", "2020-02-10")
    tom = calendar_mask(idx, "tom", k=2)
    jan = tom.loc["2020-01"]
    assert jan.iloc[:2].all() and jan.iloc[-2:].all()
    assert not jan.iloc[2:-2].any()
    mon = calendar_mask(idx, "dow", weekday=0)
    assert set(idx[mon].day_of_week) == {0}
    # Missing Wednesday = holiday; pre/post masks identify the adjacent sessions.
    holiday = pd.Timestamp("2020-01-15")
    idx2 = idx[idx != holiday]
    pre = calendar_mask(idx2, "pre_holiday")
    post = calendar_mask(idx2, "post_holiday")
    assert pre.loc[pd.Timestamp("2020-01-14")]
    assert post.loc[pd.Timestamp("2020-01-16")]

    returns = pd.Series(0.001, index=idx)
    mask = pd.Series(False, index=idx)
    mask.loc[pd.Timestamp("2020-01-06")] = True
    net, turnover, active = calendar_returns(returns, mask, cost_bps=5)
    day = pd.Timestamp("2020-01-06")
    assert abs(net.loc[day] - (0.001 - 5 / 1e4)) < 1e-12
    assert active.sum() == 1


def test_calendar_placebo_deterministic() -> None:
    idx = pd.bdate_range("2020-01-01", periods=250)
    r = pd.Series(0.0005, index=idx)
    a = calendar_placebo(r, 30, n=50, seed=11)
    b = calendar_placebo(r, 30, n=50, seed=11)
    assert a["n"] == b["n"] == 50
    assert np.allclose(a["means"], b["means"])
    assert a["p5"] <= a["p95"]


def _bars(n: int = 60) -> pd.DataFrame:
    idx = pd.bdate_range("2020-01-01", periods=n)
    close = 100.0 + np.arange(n) * 0.1
    open_ = close - 0.05
    high = close + 0.2
    low = close - 0.2
    df = pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "adjclose": close},
        index=idx,
    )
    # A clean breakout at session 20's close: next open entry, next close exit.
    df.iloc[20, df.columns.get_loc("close")] = 110.0
    df.iloc[20, df.columns.get_loc("adjclose")] = 110.0
    df.iloc[20, df.columns.get_loc("high")] = 110.5
    df.iloc[21, df.columns.get_loc("open")] = 111.0
    df.iloc[21, df.columns.get_loc("close")] = 112.0
    df.iloc[21, df.columns.get_loc("adjclose")] = 112.0
    return df


def test_breakout_entry_is_next_open_and_costs() -> None:
    df = _bars()
    net, active, trades = breakout_returns(
        df, window=10, hold=1, vol_filter=False, cost_bps=0.0
    )
    assert trades >= 1
    assert net.loc[df.index[20]] == 0.0  # signal day: no same-close trade
    assert net.loc[df.index[21]] > 0.0  # entered at 21 open, exited 21 close
    net10, _, _ = breakout_returns(
        df, window=10, hold=1, vol_filter=False, cost_bps=10.0
    )
    assert net10.loc[df.index[21]] <= net.loc[df.index[21]]


def test_breakout_no_lookahead() -> None:
    df = _bars()
    full, _, _ = breakout_returns(df, window=10, hold=3, vol_filter=False, cost_bps=5)
    cut = 35
    part, _, _ = breakout_returns(
        df.iloc[:cut], window=10, hold=3, vol_filter=False, cost_bps=5
    )
    assert np.allclose(full.iloc[:cut].to_numpy(), part.to_numpy())


def test_after_tax_positive() -> None:
    idx = pd.bdate_range("2020-01-01", periods=252)
    net = pd.Series(0.001, index=idx)
    eq, tax = after_tax_series(net)
    assert eq.iloc[-1] > 1.0
    assert tax > 0
    assert eq.iloc[-1] < (1.001 ** len(net))


def main() -> None:
    test_sector_no_lookahead()
    test_calendar_masks_and_timing()
    test_calendar_placebo_deterministic()
    test_breakout_entry_is_next_open_and_costs()
    test_breakout_no_lookahead()
    test_after_tax_positive()
    print("ok: sector/calendar/breakout no-lookahead, timing, costs and tax")


if __name__ == "__main__":
    main()
