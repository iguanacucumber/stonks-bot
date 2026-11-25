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

    # a missing bar (US holiday vs a local calendar) must carry the last price, not
    # the entry price: entry 50 -> last 100 would otherwise invent a -50% day
    n = 320
    dts = pd.bdate_range("2020-01-01", periods=n)
    a = np.r_[np.full(160, 50.0), np.full(n - 160, 100.0)]
    rows = [(d, "A", p, 5_000_000) for d, p in zip(dts, a)]
    rows += [(d, "SPY", 100.0, 9_000_000) for d in dts]
    gap = pd.DataFrame(rows, columns=["date", "ticker", "adjclose", "volume"])
    gap = gap[~((gap.ticker == "A") & (gap.date == dts[202]))]
    gmem = pd.DataFrame({"date": [dts[0]], "ticker": ["A"]})
    gcfg = strategy.Config(
        start=str(dts[0].date()),
        top_n=1,
        rebalance_days=5,
        lookback=20,
        skip=2,
        core=None,
        core_weight=0.0,
        min_dollar_vol=0.0,
        max_weight=1.0,
        sector_cap=1.0,
        stop=1.0,
        cost_bps=0.0,
    )
    gres = backtest.run(strategy.Market(gap, gmem, gcfg))
    step = gres.equity.loc[dts[202]] / gres.equity.loc[dts[201]] - 1
    assert abs(step) < 1e-9, f"missing bar marked at entry price: {step:.1%} jump"

    # leverage: gross exposure scales, borrowing costs money, no free lunch
    l1 = strategy.Config(start=str(dates[250].date()), top_n=5, rebalance_days=5)
    l2 = strategy.Config(
        start=str(dates[250].date()),
        top_n=5,
        rebalance_days=5,
        leverage=2.0,
        financing_bps=0.0,
    )
    l2f = strategy.Config(
        start=str(dates[250].date()),
        top_n=5,
        rebalance_days=5,
        leverage=2.0,
        financing_bps=1000.0,
    )
    r1 = backtest.run(strategy.Market(panel, mem, l1))
    r2 = backtest.run(strategy.Market(panel, mem, l2))
    r2f = backtest.run(strategy.Market(panel, mem, l2f))
    gross = r2.weights.drop(columns=["CASH"]).sum(axis=1)
    assert 1.8 < gross.mean() < 2.05, f"2x gross exposure is {gross.mean():.2f}"
    lev = (r2.cash / r2.equity).min()
    assert lev > -1.02, f"borrowed more than 1x equity: {lev:.2f}"
    assert r2.equity.iloc[-1] > 0, "2x wiped the book on a positive-drift panel"
    assert r2f.equity.iloc[-1] < r2.equity.iloc[-1], "financing is free"

    # P&L windows: 1/5/21/252 trading days, chained with the live day
    eq = pd.Series(1.01 ** np.arange(300))
    for live in (1.0, 1.02):
        h = backtest.horizon_returns(eq, live)
        for label, n in (("1 day", 1), ("1 week", 5), ("1 month", 21), ("1 year", 252)):
            steps = n - 1 if live != 1.0 else n
            want = 1.01 ** steps * live - 1
            assert abs(h[label] - want) < 1e-12, f"{label}: {h[label]} != {want}"

    print(
        f"ok: {len(res.equity)} days, final {res.equity.iloc[-1]:.3f}, "
        f"{len(res.trades)} trades, sharpe {backtest.metrics(res.equity, mkt.spy)['sharpe']:.2f}"
    )


if __name__ == "__main__":
    main()
