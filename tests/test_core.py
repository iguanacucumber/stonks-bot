"""One runnable check: accounting invariants and no look-ahead."""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from bot import backtest, france, markets, strategy, universe  # noqa: E402
from paper_daily import book_config, book_paths  # noqa: E402


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

    # French CTO: annual netting can never tax more than the per-trade US model
    us_tax = backtest.run(
        strategy.Market(panel, mem, replace(cfg, tax_rate=0.30, state_tax_rate=0.0))
    )
    fr_tax = backtest.run(
        strategy.Market(panel, mem, replace(cfg, tax_country="fr", tax_rate=0.30))
    )
    assert fr_tax.tax_paid <= us_tax.tax_paid + 1e-9, "French annual tax > US per-trade"
    # a full run from the first date crosses a year end, so the 2020 tax is paid
    fr_long = backtest.run(
        strategy.Market(
            panel,
            mem,
            replace(
                cfg,
                start=str(dates[0].date()),
                tax_country="fr",
                tax_rate=0.30,
            ),
        )
    )
    assert fr_long.tax_paid > 0, "expected French annual settlement to pay tax"

    # Bourse Direct-style fixed ticket fee: one EUR per order, 10k account
    fee_res = backtest.run(
        strategy.Market(
            panel,
            mem,
            replace(
                cfg,
                cost_bps=0.0,
                broker_grid=((1e9, 1.0),),
                initial_capital=10000.0,
            ),
        )
    )
    assert abs(fee_res.fees_paid - len(fee_res.trades) * 1.0 / 10000.0) < 1e-12, (
        "fixed ticket fee not charged once per trade"
    )

    # French FTT: 0.30% on buys only, for the listed French shares
    ftt_res = backtest.run(
        strategy.Market(
            panel,
            mem,
            replace(
                cfg,
                cost_bps=0.0,
                ftt_tickers=frozenset(panel.ticker.unique()),
            ),
        )
    )
    buys = ftt_res.trades[ftt_res.trades.side == "BUY"]
    expected_ftt = float((buys["notional"] * 0.003).sum())
    assert abs(ftt_res.ftt_paid - expected_ftt) < 1e-9, "FTT should be 30 bps on buys"

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

    # a missing open must be marked at the prior close, never at today's close:
    # change only the close on the masked day and the open equity must not move
    cut_open = dates[330]
    p1 = panel.copy()
    p1["open"] = p1["adjclose"]  # the synthetic panel has no open column
    ocfg = strategy.Config(
        start=str(dates[250].date()),
        end=str(cut_open.date()),
        top_n=5,
        rebalance_days=5,
        exec_price="open",
    )
    base = backtest.run(strategy.Market(p1, mem, ocfg))
    held = base.weights.loc[cut_open].drop("CASH").idxmax()
    mask = (p1.ticker == held) & (p1.date == cut_open)
    p1.loc[mask, "open"] = np.nan
    p2 = p1.copy()
    p2.loc[mask, "adjclose"] *= 1.5
    o1 = backtest.run(strategy.Market(p1, mem, ocfg))
    o2 = backtest.run(strategy.Market(p2, mem, ocfg))
    assert o1.weights.loc[cut_open, held] > 0.01, "test name not held on the cut"
    assert abs(o1.eq_open.loc[cut_open] - o2.eq_open.loc[cut_open]) < 1e-9, (
        "missing open leaked today's close into open equity"
    )

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

    # SALT: state tax is deductible against federal up to the cap
    t1, d1 = backtest.tax_on_gain(1000, 0.37, 0.10, 10000, 10000)
    assert abs(t1 - 433.0) < 1e-9 and abs(d1 - 100.0) < 1e-9, (t1, d1)
    t2, d2 = backtest.tax_on_gain(1000, 0.37, 0.10, 40, 10000)
    assert abs(t2 - 455.2) < 1e-9 and abs(d2 - 40.0) < 1e-9, (t2, d2)
    assert backtest.tax_on_gain(0, 0.37, 0.10, 10000, 10000) == (0.0, 0.0)

    # HIFO: sell the 200-basis lot before the 100-basis lot
    lot_list = [[1.0, 100.0], [1.0, 200.0]]
    gain = backtest.consume_lots(lot_list, 1.0, 250.0)
    assert abs(gain - 50.0) < 1e-9, f"HIFO gain {gain} != 50"
    assert lot_list == [[1.0, 100.0]], f"wrong lot left: {lot_list}"

    # realized-gains tax: with the same trades, a positive rate must reduce wealth
    tax0 = strategy.Config(
        start=str(dates[250].date()), top_n=5, rebalance_days=5, tax_rate=0.0
    )
    tax1 = strategy.Config(
        start=str(dates[250].date()), top_n=5, rebalance_days=5, tax_rate=0.5
    )
    w0 = backtest.run(strategy.Market(panel, mem, tax0)).equity.iloc[-1]
    w1 = backtest.run(strategy.Market(panel, mem, tax1)).equity.iloc[-1]
    assert w1 < w0, f"tax did not bite: {w1:.3f} vs {w0:.3f}"

    # a full cash drawdown exit cannot recover; refuse it instead of dying silently
    try:
        backtest.run(
            strategy.Market(
                panel,
                mem,
                strategy.Config(
                    start=str(dates[250].date()), dd_stop=0.2, dd_scale=0.0
                ),
            )
        )
        raise AssertionError("dd_scale=0 should be rejected")
    except ValueError:
        pass

    # wash sales can only add tax, never remove it
    wash_cfg = strategy.Config(
        start=str(dates[250].date()),
        top_n=5,
        rebalance_days=5,
        tax_rate=0.5,
        wash_sale_days=30,
    )
    w_wash = backtest.run(strategy.Market(panel, mem, wash_cfg)).equity.iloc[-1]
    assert w_wash <= w0 + 1e-9, f"wash sale credited money: {w_wash:.3f} vs {w0:.3f}"

    # next-open execution fills at the next day's open, not its close
    n2 = 300
    dts2 = pd.bdate_range("2020-01-01", periods=n2)
    rows2 = []
    for k in range(2):
        t = f"O{k}"
        for i, d in enumerate(dts2):
            px = 100.0 + (i % 2) * 0.5
            rows2.append((d, t, px, px, 5_000_000))
    for d in dts2:
        rows2.append((d, "SPY", 100.0, 100.0, 9_000_000))
    op = pd.DataFrame(rows2, columns=["date", "ticker", "adjclose", "open", "volume"])
    op.loc[(op.ticker == "O0") & (op.date == dts2[26]), "open"] = 150.0
    omem = pd.DataFrame({"date": [dts2[0]] * 2, "ticker": ["O0", "O1"]})
    for mode, want in (("open", 150.0), ("close", 100.0)):
        ocfg = strategy.Config(
            start=str(dts2[25].date()),
            top_n=1,
            rebalance_days=50,
            lookback=20,
            skip=2,
            core=None,
            core_weight=0.0,
            min_dollar_vol=0.0,
            max_weight=1.0,
            sector_cap=1.0,
            stop=1.0,
            cost_bps=0.0,
            exec_price=mode,
        )
        ores = backtest.run(strategy.Market(op, omem, ocfg))
        first = ores.trades[ores.trades.side == "BUY"].iloc[0]
        assert abs(first.price - want) < 1e-9, f"{mode} fill {first.price} != {want}"

    # limit-at-open fills at the limit on a touch, else at the close
    fp, hit = backtest.limit_fill(100.0, 101.0, 98.0, 100.5, True, 10)
    assert hit and abs(fp - 99.9) < 1e-12, (fp, hit)
    fp, hit = backtest.limit_fill(100.0, 101.0, 99.95, 100.5, True, 10)
    assert not hit and abs(fp - 100.5) < 1e-12, (fp, hit)
    fp, hit = backtest.limit_fill(100.0, 100.5, 99.0, 99.5, False, 10)
    assert hit and abs(fp - 100.1) < 1e-12, (fp, hit)
    fp, hit = backtest.limit_fill(100.0, 100.05, 99.0, 99.5, False, 10)
    assert not hit and abs(fp - 99.5) < 1e-12, (fp, hit)
    assert backtest.limit_fill(100.0, 101.0, 98.0, 100.5, True, 0) == (
        100.0,
        False,
    ), "0 bps must stay on the next-open path"

    # limit_bps=0 with high/low present reproduces the baseline exactly
    op_range = op.copy()
    op_range["high"] = np.maximum(op_range["open"], op_range["adjclose"]) * 1.01
    op_range["low"] = np.minimum(op_range["open"], op_range["adjclose"]) * 0.99
    lim_cfg = strategy.Config(
        start=str(dts2[25].date()),
        top_n=1,
        rebalance_days=50,
        lookback=20,
        skip=2,
        core=None,
        core_weight=0.0,
        min_dollar_vol=0.0,
        max_weight=1.0,
        sector_cap=1.0,
        stop=1.0,
        cost_bps=0.0,
        exec_price="open",
        limit_bps=0.0,
    )
    base_res = backtest.run(strategy.Market(op, omem, lim_cfg))
    zero_res = backtest.run(strategy.Market(op_range, omem, lim_cfg))
    assert np.allclose(base_res.equity.values, zero_res.equity.values), (
        "limit_bps=0 changed equity"
    )
    pd.testing.assert_frame_equal(base_res.trades, zero_res.trades, check_exact=True)
    lim_res = backtest.run(
        strategy.Market(op_range, omem, replace(lim_cfg, limit_bps=10.0))
    )
    first = lim_res.trades[lim_res.trades.side == "BUY"].iloc[0]
    assert abs(first.price - 150.0 * (1 - 10 / 1e4)) < 1e-9, (
        f"limit engine fill {first.price}"
    )
    # no look-ahead in limit mode: truncating future bars must not change the past
    cut_l = dts2[200]
    lcfg = replace(lim_cfg, limit_bps=10.0)
    lfull = backtest.run(strategy.Market(op_range, omem, lcfg))
    lcut = backtest.run(
        strategy.Market(
            op_range[op_range["date"] <= cut_l],
            omem,
            replace(lcfg, end=str(cut_l.date())),
        )
    )
    assert np.allclose(
        lfull.equity.loc[:cut_l].iloc[:-1].values,
        lcut.equity.loc[:cut_l].iloc[:-1].values,
    ), "limit mode look-ahead detected"
    try:
        strategy.Market(
            op_range, omem, replace(lim_cfg, limit_bps=10.0, exec_price="close")
        )
        raise AssertionError("limit_bps with exec_price=close should be rejected")
    except ValueError:
        pass

    # default TECH uses real core opens; core_close_fill restores the old path
    with TemporaryDirectory() as tmp:
        bars_dir = Path(tmp)
        bdates = pd.bdate_range("2020-01-01", periods=12)
        for sym, base in (("SMH", 100.0), ("SOXX", 200.0)):
            px = base + np.arange(len(bdates), dtype=float)
            pd.DataFrame(
                {
                    "date": bdates,
                    "open": px + 0.5,
                    "high": px + 1.0,
                    "low": px - 0.5,
                    "close": px,
                    "adjclose": px,
                    "volume": 1_000_000,
                }
            ).to_csv(bars_dir / f"{sym}.csv", index=False)

        def fake_core(sym, currency, calendar=None):
            px = 100.0 + np.arange(len(bdates), dtype=float)
            return pd.Series(px, index=bdates)

        def fake_fetch(symbols, force=False, workers=6):
            px = 100.0 + np.arange(len(bdates), dtype=float)
            frame = pd.DataFrame(
                {
                    "date": bdates,
                    "open": px,
                    "high": px + 1.0,
                    "low": px - 1.0,
                    "close": px,
                    "adjclose": px,
                    "volume": 1_000_000,
                }
            )
            return {"QQQ": frame, "AAPL": frame.copy()}

        old_bars = markets.data.BARS
        old_fetch = markets.data.fetch_all
        old_core = markets.local_core
        try:
            markets.data.BARS = bars_dir
            markets.data.fetch_all = fake_fetch
            markets.local_core = fake_core
            markets._CACHE.clear()
            markets._CORE_CACHE.clear()
            real, _ = markets.build("TECH")
            old, _ = markets.build("TECH", core_close_fill=True)
        finally:
            markets.data.BARS = old_bars
            markets.data.fetch_all = old_fetch
            markets.local_core = old_core
            markets._CACHE.clear()
            markets._CORE_CACHE.clear()

    real_smh = real[real["ticker"] == "SMH"].set_index("date")
    old_smh = old[old["ticker"] == "SMH"].set_index("date")
    want_open = 100.0 + np.arange(len(bdates), dtype=float) + 0.5
    assert np.allclose(real_smh["open"].values, want_open), "default core not real-open"
    assert not np.allclose(real_smh["open"].values, real_smh["close"].values), (
        "default still close-filled"
    )
    assert np.allclose(old_smh["open"].values, old_smh["close"].values), (
        "opt-out did not restore close-fill"
    )
    assert strategy.Config().limit_bps == 0.0

    # P&L windows: 1/5/21/252 trading days, chained with the live day
    eq = pd.Series(1.01 ** np.arange(300))
    for live in (1.0, 1.02):
        h = backtest.horizon_returns(eq, live)
        for label, n in (("1 day", 1), ("1 week", 5), ("1 month", 21), ("1 year", 252)):
            steps = n - 1 if live != 1.0 else n
            want = 1.01**steps * live - 1
            assert abs(h[label] - want) < 1e-12, f"{label}: {h[label]} != {want}"

    # open-trade costs cannot increase equity; the forward tracker relies on this
    assert (res.eq_open <= res.eq_open_pre + 1e-9).all(), "open equity grew"
    assert (res.eq_open >= res.eq_open_pre * 0.98).all(), "open cost >2% of equity"

    # French rate schedule and broker tickets used by the CLI
    assert backtest.fr_rate(2017) == 0.345
    assert backtest.fr_rate(2020) == 0.30
    assert backtest.fr_rate(2026) == 0.314
    assert backtest.fr_rate(2026, pea=True) == 0.186
    assert backtest.ftt_rate(pd.Timestamp("2025-04-01")) == 0.004
    assert france.ticket_fee("Bourse Direct", 1000, False) == 1.90
    assert abs(france.ticket_fee("Bourse Direct", 5000, False) - 4.5) < 1e-12
    assert france.ticket_fee("Bourse Direct", 10000, True) == 8.50
    assert france.ticket_fee("Trade Republic", 1_000_000, True) == 1.0
    fr = france.after_tax(
        backtest.Result(
            equity=pd.Series([1.0, 2.0], index=pd.bdate_range("2026-01-02", periods=2)),
            cash=pd.Series([1.0, 1.0], index=pd.bdate_range("2026-01-02", periods=2)),
            trades=pd.DataFrame(),
            weights=pd.DataFrame(),
            nav=pd.DataFrame(),
            tax_paid=0.10,
            fees_paid=0.05,
            ftt_paid=0.01,
            unrealized_tax=0.20,
        ),
        10000,
        "FR",
    )
    assert fr["currency"] == "EUR"
    assert abs(fr["after_tax_native"] - 18000.0) < 1e-6
    assert abs(fr["after_tax_eur"] - 18000.0) < 1e-6

    # forward books: monthly stays the shipped default, tax is the core-only shape
    assert book_config("monthly") == {}
    tax_book = book_config("tax")
    assert tax_book == {
        "core": "SMH",
        "core_mode": "always",
        "leverage_band": 0.30,
        "tax_lots": "hifo",
    }
    tax_cfg = strategy.Config(leverage=2.5, **tax_book)
    assert tax_cfg.core == "SMH"
    assert tax_cfg.core_mode == "always"
    assert tax_cfg.leverage_band == 0.30
    assert tax_cfg.tax_lots == "hifo"
    assert tax_cfg.tax_country == "us", "paper tax marks must stay pre-tax"
    assert book_paths("monthly")[0].name == "paper_daily.csv"
    assert book_paths("monthly")[1].name == "paper_ledger.csv"
    assert book_paths("tax")[0].name == "paper_daily_tax.csv"
    assert book_paths("tax")[1].name == "paper_ledger_tax.csv"

    print(
        f"ok: {len(res.equity)} days, final {res.equity.iloc[-1]:.3f}, "
        f"{len(res.trades)} trades, sharpe {backtest.metrics(res.equity, mkt.spy)['sharpe']:.2f}"
    )


if __name__ == "__main__":
    main()
