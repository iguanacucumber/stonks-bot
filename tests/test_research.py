"""Tests for the Alpha Lab research harness.

Run: uv run pytest tests/test_research.py -q
     uv run python -m pytest tests/test_research.py -q
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import SkipTest

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot import data as bot_data  # noqa: E402
from bot import markets, strategy  # noqa: E402
from bot.research.costs import CostModel, after_tax  # noqa: E402
from bot.research.data import CROSS_ASSET, make_panel  # noqa: E402
from bot.research.dividends import apply_dividend_tax  # noqa: E402
from bot.research.ledger import replay as ledger_replay  # noqa: E402
from bot.research.metrics import (  # noqa: E402
    bootstrap_p5,
    deflated_sharpe,
    five_year_windows,
    max_drawdown,
    summarize_equity,
)
from bot.research.overnight import (  # noqa: E402
    lag_positions,
    normalize_positions,
    overnight_backtest,
)
from bot.research.registry import TrialRegistry  # noqa: E402
from bot.research.spec import StrategySpec  # noqa: E402
from bot.research.strategies import (  # noqa: E402
    cross_asset_selector,
    short_signal_selector,
)
from bot.research.walkforward import (  # noqa: E402
    expanding_folds,
    run_full,
    walk_forward,
)
from scripts.research_common import tech_baseline, us_baseline  # noqa: E402


def _bars_dir() -> Path:
    """Read the live module path so a cache-less simulation can redirect it."""
    return Path(bot_data.BARS)


def _cache_dir() -> Path:
    return Path(bot_data.CACHE)


def require_bars(*symbols: str) -> None:
    """Skip cleanly when the committed cache is absent or incomplete."""
    missing = [s for s in symbols if not (_bars_dir() / f"{s}.csv").exists()]
    if missing:
        raise SkipTest(f"cache bars missing: {', '.join(missing[:5])}")


def require_cache_files(*names: str) -> None:
    missing = [n for n in names if not (_cache_dir() / n).exists()]
    if missing:
        raise SkipTest(f"cache files missing: {', '.join(missing)}")


def test_baseline_reproduces_after_tax():
    require_bars(*markets.UNIVERSES["TECH"], "QQQ", "SMH", "SOXX")
    b = tech_baseline()
    assert abs(b["after_tax_multiple"] - 57.01) < 0.10
    assert abs(b["maxdd"] + 0.678) < 0.005
    assert abs(b["ledger_multiple"] - 53.42) < 0.10
    assert b["after_tax_multiple_divadj"] < b["after_tax_multiple"]
    assert b["ledger_multiple_divadj"] < b["ledger_multiple"]


def test_us_baseline_and_ledger_order():
    require_cache_files(
        "panel.parquet", "membership.parquet", "membership.stamp", "sp500_history.csv"
    )
    require_bars("SPY", "SMH", "SOXX")
    u = us_baseline()
    assert u["after_tax_multiple"] > 65.0
    assert u["ledger_multiple"] < u["after_tax_multiple"]
    assert u["ledger_maxdd"] > -0.70


def test_expanding_folds_are_disjoint_and_cover_years():
    folds = expanding_folds("2015-01-01", "2026-11-25", "2018-12-31")
    assert len(folds) == 8
    assert folds[0].test_start == "2019-01-01"
    assert folds[-1].test_end == "2026-11-25"
    for a, b in zip(folds, folds[1:]):
        assert a.test_end < b.test_start
        assert b.train_start == folds[0].train_start


def test_cross_asset_selector_weights_sum_to_one():
    require_bars(*CROSS_ASSET)
    panel, mem = make_panel(CROSS_ASSET)
    cfg = strategy.Config(
        start="2015-01-01", end="2026-11-25", trend_ma=0, cost_bps=0.0
    )
    mkt = strategy.Market(panel, mem, cfg)
    sel = cross_asset_selector(
        {
            "universe": CROSS_ASSET,
            "defensive": "BIL",
            "lookback": 252,
            "skip": 21,
            "top_n": 2,
            "trend_ma": 200,
            "abs_mom": "bil",
            "weighting": "invvol",
            "vol_window": 60,
            "max_weight": 0.6,
        }
    )
    date = mkt.dates[300]
    w = sel(mkt, date, [], set())
    assert w
    assert abs(sum(w.values()) - 1.0) < 1e-9
    assert all(v > 0 for v in w.values())


def test_walk_forward_smoke():
    require_bars(*CROSS_ASSET)
    panel, mem = make_panel(CROSS_ASSET)
    spec = StrategySpec(
        name="smoke",
        family="cross_asset",
        universe=CROSS_ASSET,
        params={"test": 1},
        hypothesis="smoke",
        pass_rule="smoke",
    )
    grid = [
        {
            "universe": CROSS_ASSET,
            "lookback": 252,
            "skip": 21,
            "top_n": 1,
            "trend_ma": 0,
            "abs_mom": "bil",
            "weighting": "equal",
        },
        {
            "universe": CROSS_ASSET,
            "lookback": 252,
            "skip": 21,
            "top_n": 2,
            "trend_ma": 200,
            "abs_mom": "bil",
            "weighting": "equal",
        },
    ]
    folds = expanding_folds("2015-01-01", "2020-12-31", "2018-12-31")
    reg = TrialRegistry()
    res = walk_forward(
        panel,
        mem,
        spec=spec,
        param_grid=grid,
        selector_factory=cross_asset_selector,
        folds=folds,
        cost_model=CostModel(),
        market="TECH",
        leverage=1.0,
        score="calmar",
        registry=reg,
        bootstrap_n=500,
    )
    assert len(res.folds) == 2
    assert res.n_trials == 4
    assert res.after_tax_multiple > 0
    assert res.after_tax_multiple_divadj > 0
    assert res.ledger_multiple > 0
    assert reg.n_trials >= 4


def test_overnight_synthetic_compounds_and_costs():
    dates = pd.bdate_range("2020-01-01", periods=30)
    close = pd.DataFrame({"SPY": np.full(len(dates), 100.0)}, index=dates)
    open_ = close.shift(-1).copy()
    open_["SPY"] = close["SPY"] * 1.01
    open_.iloc[-1] = 100.0
    pos = pd.DataFrame(1.0, index=dates, columns=["SPY"])
    r = overnight_backtest(close, open_, pos, cost_bps_per_side=0.0, tax=False)
    expected = 1.01 ** int((r["gross"] > 0).sum())
    assert abs(r["equity"].iloc[-1] - expected) < 1e-8
    assert r["maxdd"] > -1e-9
    # A 5 bps/side round trip must reduce the terminal value.
    r2 = overnight_backtest(close, open_, pos, cost_bps_per_side=5.0, tax=False)
    assert r2["equity"].iloc[-1] < r["equity"].iloc[-1]


def test_overnight_lag_has_no_lookahead():
    dates = pd.bdate_range("2020-01-01", periods=5)
    trig = pd.DataFrame({"SPY": [0, 1, 1, 0, 0]}, index=dates)
    pos = lag_positions(trig, 1)
    assert pos["SPY"].tolist() == [0.0, 0.0, 1.0, 1.0, 0.0]


def test_normalize_positions_gross_cap():
    pos = pd.DataFrame(
        {"A": [1.0, 2.0], "B": [1.0, 0.0]},
        index=pd.bdate_range("2020-01-01", periods=2),
    )
    w = normalize_positions(pos)
    assert np.allclose(w.sum(axis=1).to_numpy(), 1.0)


def test_metric_bootstrap_and_drawdown():
    rng = np.random.default_rng(1)
    idx = pd.bdate_range("2018-01-01", periods=1500)
    r = pd.Series(rng.normal(0.0005, 0.01, 1500), index=idx)
    eq = (1 + r).cumprod()
    assert max_drawdown(eq) <= 0
    b = bootstrap_p5(r, n_paths=500, block=21)
    assert b["p5_multiple"] < b["median_multiple"] < b["p95_multiple"]
    s = summarize_equity(eq)
    assert np.isfinite(s["cagr"])
    assert s["days"] == len(eq)
    assert s["five_year"]["n"] >= 0


def test_deflated_sharpe_bounds():
    rng = np.random.default_rng(2)
    r = pd.Series(rng.normal(0.0008, 0.01, 1000))
    d = deflated_sharpe(r, [0.1, 0.2, 0.3, 0.4, 0.5])
    assert 0.0 <= d["dsr"] <= 1.0
    assert d["sharpe"] > 0


def test_five_year_windows_use_last_observation():
    idx = pd.bdate_range("2015-01-01", "2026-01-01")
    eq = pd.Series(np.linspace(1.0, 3.0, len(idx)), index=idx)
    w = five_year_windows(eq)
    assert w
    assert w[0]["return"] > 0


def test_registry_writes_every_trial():
    reg = TrialRegistry()
    reg.add(
        family="x",
        hypothesis="h",
        universe="SPY",
        start="2015-01-01",
        end="2020-01-01",
        params={"a": 1},
        metric="sharpe",
        value=0.5,
        oos_value=0.4,
        cost_model="test",
        verdict="reject",
        n_trials=3,
    )
    with TemporaryDirectory() as d:
        p = reg.write(Path(d) / "trials.csv")
        with p.open() as fh:
            rows = list(csv.DictReader(fh))
        assert len(rows) == 1
        assert rows[0]["family"] == "x"
        assert rows[0]["n_trials"] == "3"
        assert rows[0]["verdict"] == "reject"


def test_dividend_adjustment_reduces_equity():
    dates = pd.bdate_range("2020-01-01", periods=252)
    eq = pd.Series(np.linspace(1.0, 1.2, len(dates)), index=dates)
    weights = pd.DataFrame({"A": 1.0}, index=dates)
    yields = pd.DataFrame({"A": 0.0005}, index=dates)
    adj, daily = apply_dividend_tax(eq, weights, yields)
    assert adj.iloc[-1] < eq.iloc[-1]
    assert daily.iloc[0] == 0.0005


def test_short_selector_returns_cash_or_one():
    require_bars("SPY", "BIL")
    panel, mem = make_panel(("SPY", "BIL"))
    cfg = strategy.Config(
        start="2015-01-01",
        end="2026-11-25",
        trend_ma=0,
        cost_bps=0.0,
        rebalance_mode="days",
    )
    mkt = strategy.Market(panel, mem, cfg)
    sel = short_signal_selector(
        {
            "assets": ("SPY",),
            "lookback": 1,
            "threshold": 0.01,
            "mode": "reversal",
            "holding_days": 1,
            "pick": "all",
            "defensive": "BIL",
        }
    )
    w = sel(mkt, mkt.dates[300], [], set())
    assert w == {"BIL": 1.0} or w == {"SPY": 1.0}


def main() -> None:
    checks = [
        test_baseline_reproduces_after_tax,
        test_us_baseline_and_ledger_order,
        test_expanding_folds_are_disjoint_and_cover_years,
        test_cross_asset_selector_weights_sum_to_one,
        test_walk_forward_smoke,
        test_overnight_synthetic_compounds_and_costs,
        test_overnight_lag_has_no_lookahead,
        test_normalize_positions_gross_cap,
        test_metric_bootstrap_and_drawdown,
        test_deflated_sharpe_bounds,
        test_five_year_windows_use_last_observation,
        test_registry_writes_every_trial,
        test_dividend_adjustment_reduces_equity,
        test_short_selector_returns_cash_or_one,
    ]
    failures = 0
    skipped = 0
    for fn in checks:
        try:
            fn()
        except SkipTest as exc:
            skipped += 1
            print(f"skip: {fn.__name__}: {exc}")
        except Exception:
            failures += 1
            raise
        else:
            print(f"ok: {fn.__name__}")
    print(f"ok: {len(checks) - skipped} research checks, {skipped} skipped")
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
