"""Tests for the v7 short-horizon research: pairs, events, intraday.

Run: uv run python tests/test_short2.py
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

from bot.research.short2.events import (  # noqa: E402
    event_returns,
    event_summary,
    load_events,
    placebo_means,
)
from bot.research.short2.intraday import gap_fade_daily, summary  # noqa: E402
from bot.research.short2.pairs import (  # noqa: E402
    PairConfig,
    backtest_pair,
    pair_positions,
    spread_z,
)


def prices(n: int = 400, seed: int = 7) -> tuple[pd.Series, pd.Series]:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2020-01-01", periods=n)
    common = np.exp(np.cumsum(rng.normal(0.0003, 0.01, n)))
    p1 = pd.Series(100 * common * np.exp(rng.normal(0, 0.01, n)), index=idx)
    p2 = pd.Series(100 * common * np.exp(rng.normal(0, 0.01, n)), index=idx)
    return p1, p2


def test_spread_z_has_no_future_data():
    p1, p2 = prices()
    z1 = spread_z(p1, p2, 60)
    p1b = p1.copy()
    p1b.iloc[-1] *= 2.0
    z2 = spread_z(p1b, p2, 60)
    assert np.allclose(z1.iloc[:-1], z2.iloc[:-1], equal_nan=True)
    assert z1.iloc[-1] != z2.iloc[-1]


def test_positions_and_backtest_no_lookahead_shift():
    p1, p2 = prices()
    cfg = PairConfig(lookback=60, entry_z=1.0, max_hold=5, mode="long_only", cost_bps=0, tax=False)
    w = pair_positions(p1, p2, cfg)
    p1b = p1.copy()
    p1b.iloc[-1] *= 1.2
    w2 = pair_positions(p1b, p2, cfg)
    assert np.allclose(w.iloc[:-1], w2.iloc[:-1])
    r = backtest_pair(p1, p2, cfg)
    assert r["multiple"] > 0
    assert 0 <= r["hit_rate"] <= 1
    assert r["maxdd"] <= 0


def test_pair_costs_reduce_wealth_and_config_bounds():
    p1, p2 = prices()
    low = backtest_pair(
        p1, p2, PairConfig(lookback=60, entry_z=1.0, max_hold=5, mode="neutral", cost_bps=0, tax=False)
    )
    high = backtest_pair(
        p1, p2, PairConfig(lookback=60, entry_z=1.0, max_hold=5, mode="neutral", cost_bps=100, tax=False)
    )
    assert high["multiple"] < low["multiple"]
    for kwargs in (
        {"lookback": 5},
        {"entry_z": 0},
        {"max_hold": 0},
        {"mode": "bad"},
    ):
        try:
            PairConfig(**kwargs)
        except ValueError:
            continue
        raise AssertionError(f"accepted invalid PairConfig {kwargs}")


def test_event_loader_and_no_same_day_entry():
    with TemporaryDirectory() as td:
        root = Path(td)
        sub = {
            "cik": "0000000001",
            "tickers": ["TEST"],
            "filings": {
                "recent": {
                    "form": ["8-K", "10-Q"],
                    "items": ["2.02,9.01", ""],
                    "filingDate": ["2020-01-03", "2020-01-06"],
                    "acceptanceDateTime": ["2020-01-03T21:00:00Z", ""],
                }
            },
        }
        (root / "sub_0000000001.json").write_text(json.dumps(sub))
        ev = load_events(root)
        assert len(ev) == 1
        assert ev.iloc[0]["ticker"] == "TEST"
        # Filing Friday after close; entry is the next trading close, not Friday.
        idx = pd.bdate_range("2020-01-01", periods=40)
        px = pd.Series(np.linspace(100, 140, len(idx)), index=idx)
        er = event_returns({"TEST": px}, ev, horizons=(1, 5), cost_bps=0, start="2020-01-01")
        assert not er.empty
        assert (er["entry"] > pd.Timestamp("2020-01-03")).all()
        assert (er["entry"] == pd.Timestamp("2020-01-06")).all()
        assert set(event_summary(er)["horizon"]) == {1, 5}


def test_event_placebo_is_finite():
    p1, _ = prices()
    events = pd.DataFrame(
        {
            "ticker": ["TEST"] * 20,
            "filed": p1.index[100:120],
            "accepted": [""] * 20,
            "items": ["2.02"] * 20,
            "cik": ["1"] * 20,
        }
    )
    pm = placebo_means({"TEST": p1}, events, 5, n=20, seed=1, cost_bps=0)
    assert len(pm) == 20
    assert np.isfinite(pm).all()


def test_intraday_gap_fade_signs_and_costs():
    idx = pd.DatetimeIndex(
        [
            "2024-01-02 09:30", "2024-01-02 09:35", "2024-01-02 15:55",
            "2024-01-03 09:30", "2024-01-03 09:35", "2024-01-03 15:55",
        ]
    )
    bars = pd.DataFrame(
        {
            "open": [100, 100, 100, 102, 102, 102],
            "close": [100, 100, 100, 103, 102, 101],
        },
        index=idx,
    )
    out = gap_fade_daily(bars, gap_bps=50, cost_bps=5)
    assert len(out) == 1
    assert out.iloc[0]["direction"] == -1.0  # gap up -> short fade
    assert out.iloc[0]["net"] < out.iloc[0]["gross"]
    assert summary(out)["n"] == 1


def main() -> None:
    checks = [
        test_spread_z_has_no_future_data,
        test_positions_and_backtest_no_lookahead_shift,
        test_pair_costs_reduce_wealth_and_config_bounds,
        test_event_loader_and_no_same_day_entry,
        test_event_placebo_is_finite,
        test_intraday_gap_fade_signs_and_costs,
    ]
    for fn in checks:
        fn()
        print(f"ok: {fn.__name__}")
    print(f"ok: {len(checks)} short2 checks")


if __name__ == "__main__":
    main()
