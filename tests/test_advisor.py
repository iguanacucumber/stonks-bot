"""Advisor: profile validation, tax estimate, orders and withdrawal math."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot import advisor  # noqa: E402


def investor(**kw):
    base = dict(
        horizon_years=12.0,
        drawdown_tolerance=0.60,
        capital=10_000.0,
        wrapper="cto",
        broker="Interactive Brokers",
        income_need=0.0,
    )
    base.update(kw)
    return advisor.InvestorProfile(**base)


def expect_error(fn, label: str) -> None:
    try:
        fn()
    except ValueError:
        return
    raise AssertionError(f"expected ValueError: {label}")


def main() -> None:
    # Documented profile values, copied from docs/profiles.md.
    expected = {
        "max_growth": (66.69, 61.64, -0.567, -0.332),
        "balanced": (36.85, 34.36, -0.460, -0.274),
        "conservative": (19.44, 18.51, -0.402, -0.203),
    }
    for name, (eur, net, dd, y22) in expected.items():
        spec = advisor.PROFILES[name]
        assert (spec.eur_multiple, spec.net_multiple) == (eur, net)
        assert spec.max_drawdown == dd and spec.return_2022 == y22
        assert spec.cagr_range[0] < spec.cagr_range[1]
        assert spec.maxdd_range[0] < spec.maxdd_range[1] <= 0

    # Five-question routing: default is max_growth US only for long/high tolerance.
    assert advisor.select_profile(investor()) == "max_growth"
    assert advisor.select_profile(investor(horizon_years=5)) == "conservative"
    assert advisor.select_profile(investor(drawdown_tolerance=0.40)) == "balanced"
    assert advisor.select_profile(investor(income_need=1.0)) == "balanced"
    assert (
        advisor.select_profile(investor(wrapper="pea", broker="Trade Republic"))
        == "conservative"
    )
    assert advisor.select_profile(investor(), "balanced") == "balanced"
    for label, fn in (
        ("zero horizon", lambda: investor(horizon_years=0)),
        ("long horizon", lambda: investor(horizon_years=61)),
        ("negative tolerance", lambda: investor(drawdown_tolerance=-0.1)),
        ("tolerance > 1", lambda: investor(drawdown_tolerance=1.1)),
        ("zero capital", lambda: investor(capital=0)),
        ("unknown broker", lambda: investor(broker="not-a-broker")),
        ("PEA at DEGIRO", lambda: investor(wrapper="pea", broker="DEGIRO")),
        ("negative income", lambda: investor(income_need=-1)),
    ):
        expect_error(fn, label)
    assert investor(broker="ibkr").broker == "Interactive Brokers"
    assert len(advisor.decision_tree()) == 4

    # Orders use EUR capital and convert USD prices at EUR-per-USD.
    target = {"SMH": 1.75, "SPY": 0.30}
    prices = {"SMH": 100.0, "SPY": 500.0}
    orders, warnings = advisor.build_orders(
        target, 10_000, prices, fx_rate=0.90, broker="Interactive Brokers"
    )
    assert not warnings and len(orders) == 2
    by_ticker = {o.ticker: o for o in orders}
    assert abs(by_ticker["SMH"].notional_eur - 17_500.0) < 1e-6
    assert abs(by_ticker["SPY"].notional_eur - 3_000.0) < 1e-6
    assert abs(by_ticker["SMH"].qty - 17_500 / 0.90 / 100.0) < 1e-5
    assert by_ticker["SMH"].currency == "USD"
    missing, warn = advisor.build_orders({"SMH": 1.0}, 1000, {}, fx_rate=0.9)
    assert not missing and "no usable current price" in warn[0]

    rec = advisor.recommend(
        investor(),
        "balanced",
        target_weights=target,
        prices=prices,
        fx_rate=0.90,
        as_of="2026-11-25",
    )
    assert rec.profile == "balanced"
    assert rec.target_weights == target
    assert abs(rec.gross - 2.05) < 1e-12
    assert len(rec.order_list) == 2
    assert rec.tax_estimate.rate == 0.314
    assert rec.tax_estimate.implied_tax > 0
    assert rec.expected_cagr_range[0] < rec.expected_cagr_range[1]
    assert any("post-hoc" in w for w in rec.warnings)
    assert any("not investment advice" in w for w in rec.warnings)
    jsonable = rec.to_dict()
    assert jsonable["orders"][0]["ticker"] in prices
    assert jsonable["tax_estimate"]["rate"] == 0.314

    # PEA cannot implement the CTO profile: use the documented PUST proxy and 18.6%.
    inv = investor(wrapper="pea", broker="Trade Republic")
    pea_rec = advisor.recommend(
        inv, "max_growth", prices={"PUST.PA": 100.0}, as_of="2026-11-25"
    )
    assert pea_rec.profile == "pea_proxy"
    assert pea_rec.requested_profile == "max_growth"
    assert pea_rec.target_weights == {"PUST.PA": 1.0}
    assert pea_rec.orders[0].ticker == "PUST.PA"
    assert pea_rec.orders[0].currency == "EUR"
    assert pea_rec.tax_estimate.rate == 0.186
    assert any("cannot hold SMH" in w for w in pea_rec.warnings)
    assert advisor.tax_rate_for("cto", 10) == 0.314
    assert advisor.tax_rate_for("pea", 10) == 0.186
    assert advisor.tax_rate_for("pea", 3) == 0.30
    expect_error(lambda: advisor.tax_rate_for("nope", 10), "unknown wrapper")

    # 15-year lifecycle: accumulation, decumulation, inflation and tax.
    acc = advisor.project_lifecycle(
        "conservative",
        1_000.0,
        years=3,
        inflation=0.0,
        wrapper="cto",
        return_override=0.10,
    )
    assert abs(acc.terminal_nominal - 1_331.0) < 1e-9
    assert abs(acc.terminal_liquidation_tax - 331.0 * 0.314) < 1e-9
    assert abs(acc.terminal_after_tax - (1_331.0 - 331.0 * 0.314)) < 1e-9
    assert len(acc.rows) == 3 and acc.depletion_year is None

    dec = advisor.project_lifecycle(
        "conservative",
        1_000.0,
        years=15,
        inflation=0.0,
        wrapper="cto",
        annual_withdrawal=100.0,
        return_override=0.0,
    )
    assert dec.depletion_year == 10
    assert dec.rows[0].withdrawal_gross == 100.0
    assert dec.rows[0].withdrawal_tax == 0.0
    assert dec.terminal_nominal == 0.0

    taxed = advisor.project_lifecycle(
        "conservative",
        1_000.0,
        years=2,
        inflation=0.0,
        wrapper="cto",
        annual_withdrawal=100.0,
        return_override=0.10,
    )
    assert taxed.rows[1].withdrawal_tax > 0
    assert taxed.total_tax_paid == taxed.rows[1].withdrawal_tax
    indexed = advisor.project_lifecycle(
        "conservative",
        10_000.0,
        years=2,
        inflation=0.10,
        wrapper="cto",
        annual_withdrawal=100.0,
        return_override=0.0,
    )
    assert abs(indexed.rows[1].withdrawal_gross - 110.0) < 1e-12
    expect_error(
        lambda: advisor.project_lifecycle("conservative", 1000, years=0),
        "zero years",
    )
    expect_error(
        lambda: advisor.project_lifecycle("conservative", 1000, scenario="nope"),
        "unknown scenario",
    )

    rates = {
        name: advisor.safe_withdrawal_rate(name, horizon_years=30)
        for name in advisor.PROFILE_NAMES
    }
    assert all(0.02 <= rate <= 0.04 for rate in rates.values())
    assert rates["conservative"] > rates["max_growth"]

    # Tax-aware selling: highest basis lots first, then tax the realized gain.
    lots = [
        advisor.Lot("AAA", 10.0, 10.0, 100.0),
        advisor.Lot("AAA", 10.0, 90.0, 100.0),
    ]
    plan = advisor.tax_aware_sell_order(lots, 1_000.0, wrapper="cto")
    assert len(plan.sales) == 1
    sale = plan.sales[0]
    assert sale.cost_basis == 90.0
    assert abs(sale.gain - 100.0) < 1e-9
    assert abs(sale.tax - 31.4) < 1e-9
    assert abs(plan.net_proceeds - 968.6) < 1e-9
    assert plan.unfilled == 0.0
    bigger = advisor.tax_aware_sell_order(lots, 1_500.0, wrapper="cto")
    assert [s.cost_basis for s in bigger.sales] == [90.0, 10.0]
    assert bigger.tax > plan.tax
    short = advisor.tax_aware_sell_order(lots, 5_000.0, wrapper="cto")
    assert short.unfilled > 0
    pea = advisor.tax_aware_sell_order(lots, 1_000.0, wrapper="pea")
    assert abs(pea.tax - 18.6) < 1e-9
    expect_error(
        lambda: advisor.tax_aware_sell_order(lots, -1.0), "negative sale amount"
    )

    # Formatting is the user-facing one-page contract.
    text = advisor.format_recommendation(
        advisor.recommend(
            investor(),
            "conservative",
            target_weights={"SMH": 1.5},
            prices={"SMH": 100.0},
            fx_rate=1.0,
        )
    )
    assert "selected profile: conservative" in text
    assert "NEXT ORDERS" in text
    assert "not investment advice" in text
    projection = advisor.project_lifecycle("conservative", 10_000, years=2)
    lt = advisor.format_lifecycle(projection, 0.03)
    assert "LIFECYCLE PROJECTION" in lt
    assert "planning safe withdrawal rate" in lt

    print("ok: advisor profiles, orders, tax, lifecycle and tax-aware selling")


if __name__ == "__main__":
    main()
