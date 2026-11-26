"""Checks for the balanced profile selector. Run: uv run python tests/test_profiles.py."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot import profiles, strategy  # noqa: E402


def synthetic(days: int = 80, n: int = 6, seed: int = 4):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2020-01-01", periods=days)
    rows = []
    for ticker, drift, base in (
        ("SPY", 0.0003, 100.0),
        ("SMH", 0.0004, 50.0),
        *[(f"T{k:02d}", 0.0002, 20.0 + k) for k in range(n)],
    ):
        px = base * np.exp(np.cumsum(rng.normal(drift, 0.01, days)))
        for d, p in zip(dates, px):
            rows.append((d, ticker, p, p, p, 1_000_000))
    panel = pd.DataFrame(
        rows, columns=["date", "ticker", "adjclose", "open", "close", "volume"]
    )
    mem = pd.DataFrame(
        {
            "date": [dates[0]] * (n + 2),
            "ticker": ["SPY", "SMH"] + [f"T{k:02d}" for k in range(n)],
        }
    )
    return panel, mem, dates


def main() -> None:
    panel, mem, dates = synthetic()
    cfg = strategy.Config(
        start="2020-01-01",
        core="SMH",
        core_mode="always",
        leverage=2.5,
        trend_ma=0,
        cost_bps=0.0,
    )
    mkt = strategy.Market(panel, mem, cfg)
    date = mkt.dates[20]
    w = profiles.balanced_selector(mkt, date, [], set())
    assert abs(sum(w.values()) - 0.82) < 1e-12, sum(w.values())
    assert abs(w["SMH"] - 0.70) < 1e-12, w
    assert abs(w["SPY"] - 0.30 / 2.5) < 1e-12, w
    # The engine multiplies these unscaled weights by cfg.leverage=2.5:
    # 70% core * 2.5 + 30% SPY = 2.05 target gross before the daily trend cut.
    scaled = {t: x * cfg.leverage for t, x in w.items()}
    assert abs(sum(scaled.values()) - 2.05) < 1e-12, scaled
    assert abs(scaled["SPY"] - 0.30) < 1e-12, scaled
    assert abs(scaled["SMH"] - 1.75) < 1e-12, scaled
    assert profiles.selector_for("balanced") is profiles.balanced_selector
    assert profiles.selector_for("monthly") is None
    try:
        profiles.blend_selector(profiles._spy_selector, 1.0)
    except ValueError:
        pass
    else:
        raise AssertionError("core_weight=1.0 should be rejected")
    print("ok: balanced selector 70/30, SPY sleeve, profile lookup")


if __name__ == "__main__":
    main()
