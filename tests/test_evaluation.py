"""Analytic and synthetic checks for bot/evaluation.

No cache and no network: every fixture is generated here.  The tests are plain
asserts with a ``main()`` entry point, matching the rest of the repo (no pytest).
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from statistics import NormalDist
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot.evaluation import (  # noqa: E402
    attribution,
    currency,
    dataquality,
    metrics,
    survivorship,
)
from bot.evaluation import bootstrap as bootstrap_mod  # noqa: E402
from bot.evaluation import cscv, trials, walkforward, windows  # noqa: E402


def close(actual: float, expected: float, tol: float, label: str) -> None:
    assert abs(actual - expected) <= tol, f"{label}: {actual!r} != {expected!r}"


def test_metrics_constant() -> None:
    r = 0.001
    n = 252
    eq = pd.Series([100.0 * (1.0 + r) ** i for i in range(n + 1)])
    m = metrics.compute_metrics(eq)
    close(m["multiple"], (1.0 + r) ** n, 1e-10, "constant multiple")
    close(m["cagr"], (1.0 + r) ** n - 1.0, 1e-10, "constant CAGR")
    assert m["vol"] < 1e-12, "constant vol"
    assert math.isnan(metrics.sharpe_ratio(np.full(n, r))), (
        "exactly zero-vol Sharpe must be NaN"
    )
    assert abs(m["max_dd"]) < 1e-12, "constant growth cannot draw down"
    close(m["hit_rate"], 1.0, 1e-12, "constant hit rate")
    assert m["days"] == n


def test_metrics_drawdown_and_sortino() -> None:
    eq = pd.Series([100.0, 120.0, 90.0, 108.0, 130.0])
    close(metrics.max_drawdown(eq), -0.25, 1e-12, "max drawdown")
    close(metrics.total_return(eq), 0.30, 1e-12, "total return")
    close(metrics.multiple(eq), 1.30, 1e-12, "multiple")

    r = pd.Series([0.02, -0.01, 0.03, -0.02, 0.01])
    neg = np.array([-0.01, -0.02])
    expected = (r.mean() * 252) / (neg.std(ddof=1) * math.sqrt(252))
    close(metrics.sortino_ratio(r), expected, 1e-12, "Sortino")
    close(metrics.hit_rate(r), 0.6, 1e-12, "hit rate")


def test_metrics_iid_normal_sharpe() -> None:
    # Standardise the draw itself, so the sample mean and sample std are exact.
    z = np.random.default_rng(42).normal(size=2_000)
    z = (z - z.mean()) / z.std(ddof=1)
    mu, sigma = 0.0005, 0.01
    r = mu + sigma * z
    close(r.mean(), mu, 1e-18, "constructed mean")
    close(r.std(ddof=1), sigma, 1e-15, "constructed std")
    expected = mu / sigma * math.sqrt(252)
    close(metrics.sharpe_ratio(r), expected, 1e-12, "IID normal Sharpe")
    close(metrics.volatility(r), sigma * math.sqrt(252), 1e-12, "IID normal vol")


def test_after_tax_helpers() -> None:
    close(
        metrics.after_tax_multiple(2.0, 1.0, 0.1),
        1.9,
        1e-12,
        "after-tax multiple",
    )
    s = metrics.after_tax_summary(2.0, 1.0, 0.1, y=5.0)
    close(s["after_tax_multiple"], 1.9, 1e-12, "summary multiple")
    close(s["after_tax_cagr"], 1.9 ** (1 / 5) - 1, 1e-12, "summary CAGR")
    # Losses are not refunded by the terminal-gain approximation.
    close(metrics.apply_tax_to_multiple(0.5, 0.314), 0.5, 1e-12, "loss tax")


def test_currency_dimension_modes() -> None:
    idx = pd.date_range("2020-01-01", periods=3)
    res = SimpleNamespace(
        equity=pd.Series([1.0, 1.5, 2.0], index=idx),
        cash=pd.Series([0.0, 0.0, 0.0], index=idx),
        trades=[],
        fees_paid=0.05,
        ftt_paid=0.0,
        tax_paid=0.10,
        unrealized_tax=0.10,
    )
    ledger = SimpleNamespace(
        multiple=1.80,
        after_tax=180.0,
        cagr=0.20,
        maxdd=-0.30,
        fees=2.0,
        tax_paid=3.0,
        exit_tax=4.0,
        reconcile_gap=-5.0,
    )
    table = currency.dimension_table(
        res,
        pd.DataFrame(),
        "FR",
        capital=100.0,
        fx=1.25,
        ledger=ledger,
    ).set_index("accounting_mode")
    assert list(table.index) == ["native", "terminal_fx", "eur_ledger"]
    close(float(table.loc["native", "multiple"]), 1.90, 1e-12, "native multiple")
    close(
        float(table.loc["terminal_fx", "multiple"]),
        1.90 * 1.25,
        1e-12,
        "terminal-FX multiple",
    )
    close(float(table.loc["eur_ledger", "multiple"]), 1.80, 1e-12, "ledger multiple")
    assert list(table["is_real_life"]) == [False, False, True]
    assert "NOT real-life" in table.loc["terminal_fx", "label"]
    assert currency.is_real_life("eur_ledger")
    assert not currency.is_real_life("terminal_fx")
    try:
        currency.require_mode("spot")
        raise AssertionError("unknown accounting mode must be rejected")
    except ValueError:
        pass

    # A missing overlay must never fall back to the terminal-FX number.
    old = currency.eur_ledger_available
    currency.eur_ledger_available = lambda: False
    try:
        two = currency.dimension_table(res, pd.DataFrame(), "FR", capital=100.0)
        assert list(two["accounting_mode"]) == ["native", "terminal_fx"]
    finally:
        currency.eur_ledger_available = old


def test_bootstrap_constant_returns() -> None:
    r = 0.001
    n = 252
    res = bootstrap_mod.bootstrap(
        np.full(n, r), n_paths=300, method="stationary", expected_block=21, seed=1
    )
    expected = (1.0 + r) ** n
    close(float(res.terminal.min()), expected, 1e-10, "bootstrap terminal min")
    close(float(res.terminal.max()), expected, 1e-10, "bootstrap terminal max")
    close(float(res.max_dd.min()), 0.0, 1e-12, "bootstrap maxDD")
    tax = bootstrap_mod.bootstrap(
        np.full(n, r),
        n_paths=50,
        method="block",
        block=21,
        seed=1,
        exit_tax_rate=0.314,
    )
    close(
        float(tax.exit_tax_terminal.min()),
        1.0 + (expected - 1.0) * (1 - 0.314),
        1e-10,
        "bootstrap after-tax",
    )


def test_bootstrap_constant_loss() -> None:
    r = -0.001
    n = 252
    res = bootstrap_mod.bootstrap(
        np.full(n, r), n_paths=100, method="stationary", expected_block=5, seed=3
    )
    expected = (1.0 + r) ** n
    close(float(res.terminal.max()), expected, 1e-10, "loss terminal")
    close(float(res.max_dd.max()), expected - 1.0, 1e-10, "loss maxDD")
    taxed = bootstrap_mod.bootstrap(
        np.full(n, r), n_paths=20, method="block", block=5, seed=3, exit_tax_rate=0.314
    )
    close(float(taxed.exit_tax_terminal.max()), expected, 1e-10, "loss no refund")


def test_bootstrap_reproducible() -> None:
    r = np.random.default_rng(5).normal(0.0003, 0.01, 400)
    a = bootstrap_mod.bootstrap(r, n_paths=200, method="stationary", seed=11)
    b = bootstrap_mod.bootstrap(r, n_paths=200, method="stationary", seed=11)
    c = bootstrap_mod.bootstrap(r, n_paths=200, method="stationary", seed=12)
    assert np.array_equal(a.terminal, b.terminal)
    assert not np.array_equal(a.terminal, c.terminal)
    assert set(a.summary()) >= {
        "terminal_p5",
        "terminal_p50",
        "terminal_p95",
        "max_dd_p5",
    }
    taxed = bootstrap_mod.bootstrap(
        r, n_paths=20, method="stationary", seed=11, exit_tax_rate=0.314
    )
    assert "exit_tax_overlay_p5" in taxed.summary()
    table = bootstrap_mod.bootstrap_table(r, n_paths=20, seed=2)
    assert list(table["method"]) == ["stationary", "block"]
    assert (table["n_days"] == 400).all()


def test_cscv_same_distribution() -> None:
    rng = np.random.default_rng(7)
    x = pd.DataFrame(rng.normal(0.0002, 0.01, (1_200, 8)))
    res = cscv.cscv_pbo(x, n_splits=10)
    assert 0.20 < res.pbo < 0.80, f"identical trials PBO={res.pbo}"
    assert res.n_combinations == math.comb(10, 5)
    assert res.n_trials == 8
    assert res.n_obs == 1_200


def test_cscv_detects_one_real_edge() -> None:
    rng = np.random.default_rng(8)
    x = rng.normal(0.0, 0.01, (1_500, 8))
    x[:, 0] += 0.0015  # a genuinely stronger trial
    res = cscv.cscv_pbo(x, n_splits=10)
    assert res.pbo < 0.25, f"real edge should not look overfit: PBO={res.pbo}"


def test_cscv_requires_trials_and_splits() -> None:
    try:
        cscv.cscv_pbo(np.zeros((100, 1)), n_splits=10)
        raise AssertionError("one trial must be rejected")
    except ValueError:
        pass
    try:
        cscv.cscv_pbo(np.zeros((100, 3)), n_splits=5)
        raise AssertionError("odd n_splits must be rejected")
    except ValueError:
        pass


def test_expected_max_sharpe_formula() -> None:
    n, sd = 10, 0.08
    gamma = 0.5772156649015329
    z1 = NormalDist().inv_cdf(1 - 1 / n)
    z2 = NormalDist().inv_cdf(1 - 1 / (n * math.e))
    expected = sd * ((1 - gamma) * z1 + gamma * z2)
    close(cscv.expected_max_sharpe(n, sd), expected, 1e-15, "expected max SR")
    assert cscv.expected_max_sharpe(1, 0.1) == 0.0


def test_deflated_sharpe() -> None:
    rng = np.random.default_rng(9)
    strong = rng.normal(0.002, 0.01, 2_000)
    res = cscv.deflated_sharpe(strong, n_trials=50)
    assert res["deflated_sharpe"] > 0.95, res
    assert res["p_value"] < 0.05
    weak = rng.normal(0.0, 0.01, 2_000)
    weak_res = cscv.deflated_sharpe(weak, n_trials=100)
    assert weak_res["deflated_sharpe"] < 0.80, weak_res
    # Supplying cross-trial dispersion changes SR0 in the documented direction.
    with_trials = cscv.deflated_sharpe(
        strong, n_trials=50, trial_sharpes=[0.0] * 49 + [0.2]
    )
    assert with_trials["expected_max_sharpe_daily"] > 0
    assert with_trials["n_trials"] == 50


def test_rolling_windows_analytic() -> None:
    # Annual observations make a five-year window exactly five compounding
    # periods, so the expected multiple is analytic rather than calendar-noise.
    idx = pd.DatetimeIndex([pd.Timestamp(f"{y}-01-01") for y in range(2015, 2026)])
    eq = pd.Series(1.10 ** np.arange(len(idx)), index=idx)
    bm = pd.Series(1.05 ** np.arange(len(idx)), index=idx)
    table = windows.rolling_windows(
        eq, bm, years=5, freq="YS", min_obs=1, periods_per_year=1
    )
    assert len(table.rows) >= 6
    close(float(table.rows["multiple"].median()), 1.10**5, 1e-8, "5y multiple")
    close(float(table.rows["cagr"].median()), 0.10, 1e-8, "5y CAGR")
    assert abs(float(table.rows["max_dd"].min())) < 1e-12
    assert table.summary["share_beat"] == 1.0
    taxed = windows.rolling_windows(
        eq, bm, years=5, freq="YS", min_obs=1, tax_rate=0.314
    )
    expected = 1.0 + (1.10**5 - 1.0) * (1 - 0.314)
    close(float(taxed.rows["multiple"].median()), expected, 1e-8, "5y taxed")
    partial = windows.rolling_windows(
        eq, bm, years=5, freq="YS", min_obs=1, include_partial=True
    )
    assert len(partial.rows) >= len(table.rows)


def test_walkforward_rolling() -> None:
    dates = pd.bdate_range("2015-01-01", "2026-12-31")
    folds = walkforward.rolling_splits(
        dates, train_years=5, validate_years=2, test_years=1
    )
    assert len(folds) >= 5, len(folds)
    assert folds[0].train_start.year == 2015
    assert folds[0].test_start.year == 2022
    walkforward.assert_no_leakage(folds)
    for f in folds:
        assert f.train_end < f.validate_start <= f.validate_end < f.test_start
    labels = folds[0].label(dates)
    assert set(labels.unique()) == {"train", "validate", "test", "unused"}
    path = walkforward.oos_path(pd.Series(np.arange(len(dates)), index=dates), folds)
    assert path.index.is_monotonic_increasing
    assert not path.index.duplicated().any()
    # The protocol's single 2022-2026 holdout is available as a wider test.
    wide = walkforward.rolling_splits(
        dates, train_years=5, validate_years=2, test_years=4, include_partial=True
    )
    assert wide[0].test_start.year == 2022
    assert wide[0].test_end.year >= 2025


def test_walkforward_expanding_and_leakage_guard() -> None:
    dates = pd.bdate_range("2018-01-01", "2026-12-31")
    folds = walkforward.expanding_splits(dates, initial_train_years=3, test_years=1)
    assert len(folds) >= 3
    assert all(f.train_start == folds[0].train_start for f in folds)
    assert folds[-1].train_end > folds[0].train_end
    walkforward.assert_no_leakage(folds)
    bad = [
        folds[0],
        walkforward.Fold(
            **{
                **folds[1].__dict__,
                "test_start": folds[0].test_start,
                "test_end": folds[0].test_end,
            }
        ),
    ]
    try:
        walkforward.assert_no_leakage(bad)
        raise AssertionError("overlapping test windows must be rejected")
    except AssertionError:
        pass


def test_trial_registry_roundtrip() -> None:
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "trials.csv"
        reg = trials.TrialRegistry(path)
        reg.preregister(
            "T-001",
            "Momentum survives after tax",
            "after-tax multiple > QQQ and bootstrap p5 > 1",
            branch="D",
            universe="TECH static 49",
            params={"top_n": 6, "leverage": 2.5},
            n_trials=6,
        )
        df = reg.read()
        assert len(df) == 1
        assert df.loc[0, "verdict"] == "preregistered"
        assert df.loc[0, "status"] == "preregistered"
        assert df.loc[0, "accounting_mode"] == "eur_ledger"
        reg.complete(
            "T-001",
            value="52.89x",
            oos_value="?",
            verdict="paper",
            accounting_mode="native",
        )
        df = reg.read()
        assert df.loc[0, "verdict"] == "paper"
        assert df.loc[0, "accounting_mode"] == "native"
        assert reg.count() == 1
        assert reg.trial_count() == 6
        try:
            reg.preregister("bad-mode", "x", "y", accounting_mode="spot")
            raise AssertionError("invalid accounting mode must fail")
        except ValueError:
            pass
        try:
            reg.preregister("T-001", "dup", "x")
            raise AssertionError("duplicate id must fail")
        except ValueError:
            pass
        try:
            reg.complete("missing", value=1, verdict="accept")
            raise AssertionError("unknown id must fail")
        except KeyError:
            pass
        try:
            reg.record(id="T-002", verdict="winner")
            raise AssertionError("invalid verdict must fail")
        except ValueError:
            pass
        reg.record(
            id="T-002",
            date="2026-01-01",
            branch="D",
            hypothesis="historical import",
            verdict="reject",
            n_trials=3,
        )
        assert reg.count() == 2
        assert reg.trial_count() == 9
        summary = reg.summary()
        assert set(["branch", "verdict", "experiments", "trials"]) <= set(
            summary.columns
        )


def test_attribution_analytic() -> None:
    rng = np.random.default_rng(3)
    b = pd.Series(rng.normal(0.0004, 0.01, 1_000))
    r = 2.0 * b
    a = attribution.benchmark_attribution(r, b)
    close(a["beta"], 2.0, 1e-12, "beta 2")
    close(a["alpha_ann"], 0.0, 1e-15, "alpha 0")
    close(a["r2"], 1.0, 1e-12, "R2")
    close(
        a["tracking_error"],
        float(b.std(ddof=1) * math.sqrt(252)),
        1e-10,
        "TE of 2x vs 1x",
    )
    close(a["up_capture"], 2.0, 1e-9, "up capture")
    close(a["down_capture"], 2.0, 1e-9, "down capture")

    # A constant daily alpha leaves beta 1 and zero tracking error.
    r2 = b + 0.0002
    a2 = attribution.benchmark_attribution(r2, b)
    close(a2["beta"], 1.0, 1e-12, "beta with alpha")
    close(a2["alpha_ann"], 0.0002 * 252, 1e-10, "alpha annualised")
    close(a2["tracking_error"], 0.0, 1e-12, "constant-alpha TE")


def test_exposure_and_cost_decomposition() -> None:
    idx = pd.date_range("2020-01-01", periods=2)
    w = pd.DataFrame(
        {"SMH": [0.5, 0.5], "AAPL": [1.0, 1.0], "CASH": [-0.5, -0.5]}, index=idx
    )
    exp = attribution.exposure_breakdown(w)
    close(exp["avg_gross"], 1.5, 1e-12, "gross")
    close(exp["avg_core"], 0.5, 1e-12, "core")
    close(exp["avg_sleeve"], 1.0, 1e-12, "sleeve")
    close(exp["avg_cash"], -0.5, 1e-12, "cash")

    res = SimpleNamespace(
        equity=pd.Series([1.0, 1.1]),
        cash=pd.Series([0.0, -0.5]),
        fees_paid=0.01,
        ftt_paid=0.001,
        tax_paid=0.02,
        unrealized_tax=0.03,
    )
    d = attribution.cost_tax_decomposition(res, capital=100.0, financing_bps=500.0)
    close(d["pre_tax_multiple"], 1.1, 1e-12, "pre-tax")
    close(d["after_tax_multiple"], 1.07, 1e-12, "after-tax")
    close(d["fees"], 0.01, 1e-12, "fees")
    close(d["fees_ex_ftt"], 0.009, 1e-12, "fees ex FTT")
    close(d["exit_tax"], 0.03, 1e-12, "exit tax")
    assert d["gross_multiple"] > d["pre_tax_multiple"]
    assert d["cost_drag_pct"] > 0
    close(d["fees_amount"], 1.0, 1e-12, "fees amount")


def _bar_frame(dates, prices=None) -> pd.DataFrame:
    n = len(dates)
    p = np.asarray(
        prices if prices is not None else np.linspace(100, 110, n), dtype=float
    )
    return pd.DataFrame(
        {
            "date": dates,
            "open": p,
            "high": p * 1.01,
            "low": p * 0.99,
            "close": p,
            "adjclose": p,
            "volume": 1_000_000,
        }
    )


def test_dataquality_hard_failures() -> None:
    with TemporaryDirectory() as tmp:
        bars = Path(tmp) / "bars"
        bars.mkdir()
        dates = pd.bdate_range("2020-01-01", periods=30)
        clean = _bar_frame(dates)
        clean.to_csv(bars / "CLEAN.csv", index=False)

        bad = _bar_frame(dates)
        bad.loc[10, "close"] = -1.0
        bad.loc[11, "high"] = 1.0
        bad.loc[11, "low"] = 2.0
        bad = pd.concat([bad, bad.iloc[[12]]], ignore_index=True)
        bad.to_csv(bars / "BAD.csv", index=False)

        ohlc = _bar_frame(dates)
        ohlc.loc[5, "high"] = 1.0
        ohlc.loc[5, "low"] = 2.0
        ohlc.to_csv(bars / "OHLC.csv", index=False)

        report = dataquality.audit_bars(
            bars,
            snapshot_date=str(dates[-1].date()),
            require_fresh=["BAD"],
        )
        assert not report["ok"]
        checks = {x["check"] for x in report["hard_failures"]}
        assert {"duplicate_date", "nonpositive_price", "high_below_low"} <= checks
        assert report["symbols_checked"] == 3
        json.dumps(report, default=str)  # machine-readable

        # The long tail is warning-only: one foreign/delisted OHLC scar must not
        # block the whole cache, but it is still visible in the report.
        tail = dataquality.audit_bars(
            bars, symbols=["OHLC"], snapshot_date=str(dates[-1].date())
        )
        assert tail["ok"], "non-required OHLC scars must not block the whole cache"
        assert "high_below_low" in {x["check"] for x in tail["warnings"]}

        clean_report = dataquality.audit_bars(
            bars, symbols=["CLEAN"], snapshot_date=str(dates[-1].date())
        )
        assert clean_report["ok"], clean_report


def test_dataquality_alias_cycles_and_panel() -> None:
    report = dataquality.audit_aliases({"A": "B", "B": "C", "C": "A"})
    assert not report["ok"]
    assert report["cycles"]
    good = dataquality.audit_aliases({"FB": "META", "META": "META"})
    assert good["ok"]

    panel = pd.DataFrame(
        {
            "date": [pd.Timestamp("2020-01-01")] * 2,
            "ticker": ["A", "A"],
            "adjclose": [10.0, -1.0],
        }
    )
    p = dataquality.audit_panel(panel)
    assert not p["ok"]
    assert {x["check"] for x in p["hard_failures"]} >= {
        "panel_duplicate_rows",
        "panel_nonpositive_price",
    }

    with TemporaryDirectory() as tmp:
        missing = dataquality.audit_cache(Path(tmp) / "nope")
        assert not missing["ok"]


def test_survivorship_facts_synthetic() -> None:
    dates = pd.bdate_range("2014-01-01", "2016-12-31")
    rows = []
    for t in ("A", "B", "C", "SPY"):
        for d in dates:
            rows.append((d, t, 100.0))
    panel = pd.DataFrame(rows, columns=["date", "ticker", "adjclose"])
    membership = pd.DataFrame(
        {
            "date": [pd.Timestamp("2014-12-31")] * 2 + [pd.Timestamp("2015-06-30")] * 3,
            "ticker": ["A", "B", "A", "B", "C"],
        }
    )
    facts = survivorship.static_universe_facts(["A", "B", "C", "D"], membership, panel)
    assert facts["n_static"] == 4
    assert facts["n_in_sp500_at_start"] == 2
    assert facts["joined_sp500_later"] == {"C": "2015-06-30"}
    assert facts["never_in_sp500"] == ["D"]
    dated = survivorship.dated_sp500_membership(["A", "B", "C", "D"], membership)
    assert len(dated) == 3
    ew = survivorship.equal_weight_multiple(panel)
    assert ew["n_names"] == 3
    close(ew["equal_weight_multiple"], 1.0, 1e-12, "synthetic equal weight")


def main() -> None:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"ok: {fn.__name__}")
    print(f"ok: {len(tests)} evaluation tests")


if __name__ == "__main__":
    main()
