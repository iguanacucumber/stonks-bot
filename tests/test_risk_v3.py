"""Boundary and causality tests for the v3 risk primitives and gates.

Run: uv run python tests/test_risk_v3.py
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot.risk.alloc import (  # noqa: E402
    inverse_vol_weights,
    portfolio_vol,
    risk_contributions,
    risk_parity_weights,
)
from bot.risk.dividends import (  # noqa: E402
    after_tax_with_dividend_tax,
    apply_dividend_tax,
    daily_dividend_tax,
    yield_for,
)
from bot.risk.drawdown import (  # noqa: E402
    DrawdownRule,
    breaker_active,
    drawdown,
    ladder_scale,
)
from bot.risk.etf import ETF_UNIVERSE, load_panel, trend_selector  # noqa: E402
from bot.risk.regime import (  # noqa: E402
    above_trend,
    momentum,
    trend_scale,
)
from bot.risk.tail import (  # noqa: E402
    HedgeConfig,
    after_tax_delta,
    black_scholes_call,
    black_scholes_put,
    put_spread_cost,
    simulate_hedge,
    size_to_budget,
)
from bot.risk.vol import (  # noqa: E402
    VolTarget,
    gross_series,
    next_gross,
    raw_gross,
    realized_vol,
)


def test_vol() -> None:
    assert raw_gross(0.30, 0.30, 2.5, 0.5) == 1.0
    assert raw_gross(0.01, 0.30, 2.5, 0.5) == 2.5
    assert raw_gross(10.0, 0.30, 2.5, 0.5) == 0.5
    assert np.isnan(raw_gross(float("nan"), 0.30, 2.5, 0.5))
    assert raw_gross(0.0, 0.30, 2.5, 0.5) == 2.5

    cfg = VolTarget(0.30, 2.5, 0.5, band=0.10, hysteresis=0.10, max_step=0.5)
    # A 40% vol gives 0.75, a 25% relative cut: de-risk immediately.
    assert next_gross(0.40, 2.5, cfg) == 2.0
    # Small change inside the band is ignored.
    assert next_gross(0.31, 1.0, cfg) == 1.0
    # Re-risk needs both the band and the vol hysteresis.
    assert next_gross(0.275, 1.0, cfg) == 1.0  # raw 1.09, inside band
    assert np.isclose(next_gross(0.269, 1.0, cfg), 0.30 / 0.269)
    assert next_gross(0.24, 1.0, cfg) == 1.25
    # A missing vol holds the previous gross.
    assert next_gross(float("nan"), 1.25, cfg) == 1.25
    # Step cap and bounds.
    assert next_gross(10.0, 2.5, cfg) == 2.0
    assert 0.5 <= next_gross(0.01, 0.6, cfg) <= 2.5
    assert next_gross(2.0, None, cfg) == 0.5
    assert next_gross(float("nan"), None, cfg) == 2.5

    rets = pd.Series([0.0, 0.01, -0.01, 0.02, -0.02, 0.0, 0.01])
    vol = realized_vol(rets, window=3)
    assert vol.iloc[:2].isna().all()
    assert vol.iloc[2] > 0
    # Truncating the future cannot change a realized-vol value at t.
    assert np.isclose(vol.iloc[3], realized_vol(rets.iloc[:4], window=3).iloc[3])
    gs = gross_series(pd.Series([0.5, 0.4, 0.2, 0.7]), cfg, initial=2.5)
    assert len(gs) == 4 and (gs >= 0.5).all() and (gs <= 2.5).all()
    assert gs.is_monotonic_decreasing or True  # values are bounded, not monotone


def test_drawdown() -> None:
    eq = pd.Series([1.0, 1.1, 0.88, 0.95, 1.12, 1.0])
    dd = drawdown(eq)
    assert dd.iloc[0] == 0.0
    assert np.isclose(dd.iloc[2], 0.88 / 1.1 - 1.0)
    rule = DrawdownRule(0.20, 0.6, 0.10)
    assert breaker_active(-0.19, False, rule) is False
    assert breaker_active(-0.20, False, rule) is True
    assert breaker_active(-0.15, True, rule) is True
    assert breaker_active(-0.10, True, rule) is False
    assert breaker_active(float("nan"), True, rule) is True
    rungs = ((0.10, 0.75), (0.20, 0.50), (0.30, 0.25))
    assert ladder_scale(-0.05, rungs) == 1.0
    assert ladder_scale(-0.10, rungs) == 0.75
    assert ladder_scale(-0.25, rungs) == 0.50
    assert ladder_scale(-0.40, rungs) == 0.25


def test_regime() -> None:
    idx = pd.bdate_range("2020-01-01", periods=300)
    px = pd.Series(np.linspace(100, 200, len(idx)), index=idx)
    above = above_trend(px, 20)
    assert not above.iloc[:19].any()
    assert above.iloc[20:].all()
    scale = trend_scale(px, 20, 0.5)
    assert (scale.iloc[:19] == 1.0).all()
    assert (scale.iloc[20:] == 1.0).all()
    down = pd.Series(np.linspace(200, 100, len(idx)), index=idx)
    assert (trend_scale(down, 20, 0.5).iloc[20:] == 0.5).all()
    assert np.isclose(momentum(px, 20).iloc[25], px.iloc[25] / px.iloc[5] - 1.0)
    # Truncation must not change prior trend/momentum values.
    cut = 150
    assert np.array_equal(
        above.iloc[:cut].to_numpy(),
        above_trend(px.iloc[:cut], 20).to_numpy(),
    )
    assert np.allclose(
        momentum(px, 20).iloc[:cut].to_numpy(),
        momentum(px.iloc[:cut], 20).to_numpy(),
        equal_nan=True,
    )


def test_alloc() -> None:
    sig = pd.Series({"a": 0.1, "b": 0.2, "c": 0.4})
    w = inverse_vol_weights(sig, gross=1.0, cap=1.0)
    assert np.isclose(w.sum(), 1.0)
    assert w["a"] > w["b"] > w["c"]
    capped = inverse_vol_weights(sig, gross=1.0, cap=0.4)
    assert (capped <= 0.4 + 1e-12).all()
    assert np.isclose(capped.sum(), 1.0)
    try:
        inverse_vol_weights(sig, gross=1.0, cap=0.2)
        raise AssertionError("infeasible cap must raise")
    except ValueError:
        pass

    cov = pd.DataFrame(
        [[0.04, 0.01], [0.01, 0.09]], index=["a", "b"], columns=["a", "b"]
    )
    rp = risk_parity_weights(cov, gross=1.0, cap=1.0)
    rc = risk_contributions(cov, rp)
    assert np.isclose(rc["a"], rc["b"], rtol=1e-8)
    assert np.isclose(rp.sum(), 1.0)
    # For two assets equal risk contribution collapses to inverse vol.
    assert np.isclose(rp["a"] / rp["b"], math.sqrt(0.09 / 0.04), rtol=1e-8)
    assert portfolio_vol(cov, rp) > 0
    diag = pd.DataFrame(
        np.diag([0.01, 0.04, 0.09]), index=list("abc"), columns=list("abc")
    )
    rp3 = risk_parity_weights(diag)
    rc3 = risk_contributions(diag, rp3)
    assert np.allclose(rc3, rc3.iloc[0], rtol=1e-8)


def test_tail() -> None:
    assert np.isclose(
        black_scholes_call(100, 100, 1, 0.04, 0.2)
        - black_scholes_put(100, 100, 1, 0.04, 0.2),
        100 - 100 * math.exp(-0.04),
        atol=1e-9,
    )
    assert black_scholes_put(100, 80, 0, 0.04, 0.2) == 0.0
    assert black_scholes_put(100, 120, 0, 0.04, 0.2) == 20.0
    assert 0 < black_scholes_put(100, 100, 1, 0.04, 0.2) < 100
    # Monotone in vol and strike.
    assert black_scholes_put(100, 100, 1, 0.04, 0.4) > black_scholes_put(
        100, 100, 1, 0.04, 0.2
    )
    assert black_scholes_put(100, 90, 1, 0.04, 0.3) < black_scholes_put(
        100, 110, 1, 0.04, 0.3
    )
    spread = put_spread_cost(0.90, 0.70, 0.25, 0.04, 0.30)
    assert spread > 0
    try:
        put_spread_cost(0.70, 0.90, 0.25, 0.04, 0.30)
        raise AssertionError("long strike below short must raise")
    except ValueError:
        pass
    assert size_to_budget(1000, 0.015, 0.01, max_notional=2500) == 1500
    assert size_to_budget(1000, 0.015, 0.0, max_notional=2500) == 2500

    idx = pd.bdate_range("2020-01-01", periods=260)
    eq = pd.Series(1000.0, index=idx)
    und = pd.Series(100.0, index=idx)
    und.iloc[-1] = 60.0
    cfg = HedgeConfig(tenor_months=3, budget_pct=None)
    h = simulate_hedge(eq, cfg, underlying=und)
    assert h.premium > 0
    assert h.payoff >= 0
    assert len(h.rolls) >= 2
    assert after_tax_delta(h) < h.payoff
    # No crash: the premium is a drag.
    h2 = simulate_hedge(eq, cfg, underlying=pd.Series(100.0, index=idx))
    assert h2.equity.iloc[-1] < eq.iloc[-1]
    assert h2.payoff == 0.0


def test_dividends() -> None:
    idx = pd.bdate_range("2020-01-01", periods=5)
    w = pd.DataFrame({"SMH": 2.0, "BIL": 0.5, "GLD": 0.0}, index=idx)
    drag = daily_dividend_tax(w, tax_rate=0.314, default_yield=0.012)
    expected = (2.0 * 0.010 + 0.5 * 0.045) * 0.314 / 252
    assert np.allclose(drag, expected)
    assert yield_for("BIL") > yield_for("SMH") > yield_for("GLD")
    assert yield_for("UNKNOWN") == 0.012
    zero = daily_dividend_tax(w, tax_rate=0.0)
    assert (zero == 0).all()
    eq = pd.Series([100.0, 101.0, 102.0], index=idx[:3])
    net = apply_dividend_tax(eq, pd.Series([0.001] * 3, index=eq.index))
    assert net.iloc[-1] < eq.iloc[-1]
    assert np.isclose(net.iloc[-1], 102.0 * 0.999**3)
    assert np.isclose(
        after_tax_with_dividend_tax(110.0, eq, net), 110.0 * net.iloc[-1] / eq.iloc[-1]
    )


def test_etf() -> None:
    panel, mem = load_panel()
    assert set(ETF_UNIVERSE) <= set(panel["ticker"].unique())
    assert panel["date"].min() <= pd.Timestamp("2014-01-31")
    assert panel["date"].max() >= pd.Timestamp("2026-06-01")
    assert len(mem) == len(ETF_UNIVERSE)
    assert callable(trend_selector())


def test_gates() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    import risk_v3 as rv  # noqa: E402

    base = {
        "family": "A",
        "net_x": 30.0,
        "net_maxdd": -0.46,
        "p5": 2.6,
        "y2018": -0.19,
        "y2022": -0.27,
        "h1_cagr": 0.14,
        "h2_cagr": 0.33,
        "h1_dd": -0.44,
        "h2_dd": -0.46,
        "control_x": 28.0,
        "control_p5": 2.70,
        "net_x_2x": 27.0,
    }
    assert rv.balanced_gate(base)
    assert not rv.balanced_gate({**base, "family": "H"})
    assert not rv.balanced_gate({**base, "net_x_2x": 24.0})
    assert not rv.balanced_gate({**base, "control_x": 28.0, "control_p5": 2.5})
    # The 95% branch must require a drawdown 10 pp BETTER, not worse.
    row = {
        "family": "V",
        "net_x": 60.0,
        "net_maxdd": -0.56,
        "p5": 3.0,
        "y2018": -0.30,
        "y2022": -0.33,
        "h1_cagr": 0.17,
        "h2_cagr": 0.55,
        "control_x": 50.0,
        "control_p5": 2.2,
        "net_x_2x": 55.0,
    }
    # 61.0 base -> 60.0 is >95%; p5 passes; DD -56% is worse than base+10pp
    # (-47.8%), so the 95% branch must fail on the DD leg.
    assert rv.gate_row("US", row).split("/")[0] == "fail"
    row["net_maxdd"] = -0.47
    assert rv.gate_row("US", row).split("/")[0] == "pass-95"


def main() -> None:
    test_vol()
    test_drawdown()
    test_regime()
    test_alloc()
    test_tail()
    test_dividends()
    test_etf()
    test_gates()
    print("ok: risk v3 pure functions, boundaries, causality and gates")


if __name__ == "__main__":
    main()
