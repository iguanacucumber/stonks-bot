"""One runnable check: accounting invariants and no look-ahead."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot import backtest, strategy, universe  # noqa: E402


def synthetic(days: int = 420, n: int = 30, seed: int = 3):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2020-01-01", periods=days)
    rows = []
    for k in range(n):
        t = f"T{k:02d}"
        ret = rng.normal(0.0004, 0.018, days)
        px = 50 * np.exp(np.cumsum(ret))
        vol = rng.integers(1_000_000, 5_000_000, days)
        for d, p, v in zip(dates, px, vol):
            rows.append((d, t, p, v))
    spy = 100 * np.exp(np.cumsum(rng.normal(0.0003, 0.01, days)))
    for d, p in zip(dates, spy):
        rows.append((d, "SPY", p, 10_000_000))
    panel = pd.DataFrame(rows, columns=["date", "ticker", "adjclose", "volume"])
    mem = pd.DataFrame(
        {"date": [dates[0]] * n, "ticker": [f"T{k:02d}" for k in range(n)]}
    )
    return panel, mem, dates


def main():
    panel, mem, dates = synthetic()
    cfg = strategy.Config(start=str(dates[250].date()), top_n=5, rebalance_days=5)
    mkt = strategy.Market(panel, mem, cfg)
    res = backtest.run(mkt)

    assert (res.equity > 0).all(), "equity went non-positive"
    assert (res.cash >= -1e-6).all(), "negative cash"
    wsum = res.weights.drop(columns=["CASH"], errors="ignore").sum(axis=1)
    assert (wsum <= 1.001).all(), f"weights exceed 1: {wsum.max()}"
    if len(res.trades):
        assert res.trades["date"].min() >= dates[250], "trade before start"

    again = backtest.run(mkt)
    assert np.allclose(res.equity.values, again.equity.values), "not deterministic"

    # no look-ahead: truncating future prices must not change earlier equity
    cut = dates[330]
    panel2 = panel[panel["date"] <= cut]
    cfg2 = strategy.Config(
        start=str(dates[250].date()), end=str(cut.date()), top_n=5, rebalance_days=5
    )
    mkt2 = strategy.Market(panel2, mem, cfg2)
    res2 = backtest.run(mkt2)
    a = res.equity.loc[:cut].iloc[:-1]
    b = res2.equity.loc[:cut].iloc[:-1]
    assert np.allclose(a.values, b.values), "look-ahead detected: truncated run differs"

    print(
        f"ok: {len(res.equity)} days, final {res.equity.iloc[-1]:.3f}, "
        f"{len(res.trades)} trades, sharpe {backtest.metrics(res.equity, mkt.spy)['sharpe']:.2f}"
    )


if __name__ == "__main__":
    main()
