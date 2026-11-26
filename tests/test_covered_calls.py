"""Covered-call model tests: premiums, assignment, cap, tax, no look-ahead.

All data is synthetic. No option quotes, no network, no cache.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot import covered_calls, eur_ledger  # noqa: E402
from bot.covered_calls import CoveredCallConfig, build_covered_calls, price_at  # noqa: E402
from bot.eur_ledger import EurLedgerConfig  # noqa: E402

TOL = 1e-9


@dataclass
class FakeRes:
    equity: pd.Series
    weights: pd.DataFrame
    trades: pd.DataFrame


def _dates() -> pd.DatetimeIndex:
    return pd.bdate_range("2021-01-04", "2021-02-26")


def _panel(spot_end: float = 120.0, spot_mid: float = 110.0) -> pd.DataFrame:
    dates = _dates()
    n = len(dates)
    # A smooth rise from 100 to `spot_end`; the first month is fixed.
    split = int(n * 0.55)
    px = np.concatenate(
        [
            np.linspace(100.0, spot_mid, split),
            np.linspace(spot_mid, spot_end, n - split),
        ]
    )
    return pd.DataFrame(
        {"date": dates, "ticker": "SMH", "adjclose": px, "close": px, "open": px}
    )


def _res(panel: pd.DataFrame, smh_weight: float = 1.0) -> FakeRes:
    dates = pd.DatetimeIndex(panel["date"].unique())
    equity = pd.Series(1.0, index=dates)
    weights = pd.DataFrame({"SMH": smh_weight, "CASH": 1.0 - smh_weight}, index=dates)
    trades = pd.DataFrame(
        columns=["date", "ticker", "side", "shares", "price", "notional", "reason"]
    )
    return FakeRes(equity, weights, trades)


def test_zero_vol_boundary_and_intrinsic() -> None:
    assert abs(price_at(100.0, 100.0, 0.0, 0.25)) < TOL
    assert abs(price_at(110.0, 100.0, 0.0, 0.25) - 10.0) < TOL
    assert abs(price_at(90.0, 100.0, 0.0, 0.25)) < TOL
    # A positive time to expiry with zero vol is a deterministic forward, not a
    # crash or a negative price.
    assert price_at(100.0, 100.0, 1.0 / 12.0, 0.0) >= 0.0
    assert price_at(100.0, 90.0, 1.0 / 12.0, 0.0) > 0.0


def test_premium_assignment_and_cap() -> None:
    panel = _panel(spot_end=120.0)
    res = _res(panel)
    cfg = CoveredCallConfig(
        coverage="core",
        strike_otm=0.02,
        vol=0.30,
        capital=20_000.0,
        fractional=False,
        commission_per_contract=0.65,
    )
    cc = build_covered_calls(res, panel, cfg)
    assert len(cc.rolls) == 1
    roll = cc.rolls.iloc[0]
    assert abs(roll["strike"] - roll["spot0"] * 1.02) < 1e-9
    assert roll["contracts"] >= 1.0
    assert roll["premium"] > 0
    assert roll["payoff"] > 0
    assert bool(roll["assigned"])
    assert cc.assigned_rolls == 1
    assert abs(cc.premium - roll["premium"]) < TOL
    assert abs(
        cc.payoff - (roll["spot1"] - roll["strike"]) * 100.0 * roll["contracts"]
    ) < TOL
    assert abs(cc.net_income - (cc.premium - cc.payoff)) < TOL

    # Assignment caps the covered sleeve: final adjusted equity is base + premium
    # minus intrinsic payoff, below the uncapped 20% rise plus premium.
    base = 20_000.0
    assert abs(cc.equity_adjusted.iloc[-1] - (base + cc.premium - cc.payoff)) < 1e-6
    uncapped = base * 1.20 + cc.premium
    assert cc.equity_adjusted.iloc[-1] < uncapped

    # Cash-flow signs: premium in at the decision close, payoff out at expiry.
    first = pd.Timestamp(roll["decision"])
    last = pd.Timestamp(roll["expiry"])
    assert cc.daily.loc[first, "premium_native"] > 0
    assert cc.daily.loc[last, "cash_native"] < 0


def test_no_lookahead_strike_and_premium() -> None:
    dates = _dates()
    panel_a = _panel(spot_end=120.0, spot_mid=110.0)
    panel_b = _panel(spot_end=80.0, spot_mid=110.0)
    # Panels differ only after the first month; decision variables must match.
    cut = dates[int(len(dates) * 0.60)]
    panel_b.loc[panel_b["date"] > cut, "adjclose"] = 80.0
    panel_b.loc[panel_b["date"] > cut, "close"] = 80.0
    res = _res(panel_a)
    cfg = CoveredCallConfig(coverage="core", strike_otm=0.05, vol=0.30)
    a = build_covered_calls(res, panel_a, cfg).rolls.iloc[0]
    b = build_covered_calls(res, panel_b, cfg).rolls.iloc[0]
    for col in ("strike", "premium", "contracts", "mid", "bid"):
        assert abs(float(a[col]) - float(b[col])) < 1e-9, col
    assert a["payoff"] != b["payoff"]


def test_eur_ledger_option_tax_and_income() -> None:
    panel = _panel(spot_end=120.0)
    res = _res(panel)
    cc = build_covered_calls(
        res,
        panel,
        CoveredCallConfig(coverage="core", strike_otm=0.02, vol=0.30, capital=10_000),
    )
    cfg = EurLedgerConfig(
        capital=10_000.0,
        tax_rate=0.314,  # force a deterministic PFU in the test
        financing_bps=0.0,
        spread_bps=0.0,
        fx_bps=0.0,
        broker_bps=0.0,
        broker_fixed_eur=0.0,
    )
    base = eur_ledger.replay(res, panel, "US", cfg)
    led = eur_ledger.replay(res, panel, "US", cfg, option_overlay=cc.daily)
    assert led.option_rolls == 1
    assert led.option_premium_eur > 0
    assert led.option_payoff_eur > 0
    # Premium and payoff are translated at their own trade dates, so their EUR
    # difference is not exactly the expiry-date P&L; both must still be present
    # and the assigned 2% OTM call is a loss on this path.
    assert led.option_pnl_eur < 0
    assert led.after_tax != base.after_tax
    # A loss offsets other gains/tax at the annual bucket rather than being taxed.
    assert led.tax_paid <= base.tax_paid + 1e-9


def test_core_has_no_calls_without_smh_and_book_flags_proxy() -> None:
    panel = _panel(spot_end=120.0)
    res = _res(panel, smh_weight=0.0)
    res.weights["SMH"] = 0.0
    res.weights["SPY"] = 1.0
    res.weights["CASH"] = 0.0
    core = build_covered_calls(
        res, panel, CoveredCallConfig(coverage="core", strike_otm=0.05, vol=0.30)
    )
    assert len(core.rolls) == 0
    assert core.premium == 0.0
    book = build_covered_calls(
        res, panel, CoveredCallConfig(coverage="book", strike_otm=0.05, vol=0.30)
    )
    # Book coverage can still write against gross in this synthetic book, but the
    # SMH holding is zero, so every roll must be flagged as not truly covered.
    assert len(book.rolls) > 0
    assert book.uncovered_rolls == len(book.rolls)


if __name__ == "__main__":
    test_zero_vol_boundary_and_intrinsic()
    test_premium_assignment_and_cap()
    test_no_lookahead_strike_and_premium()
    test_eur_ledger_option_tax_and_income()
    test_core_has_no_calls_without_smh_and_book_flags_proxy()
    print("test_covered_calls: all checks passed")
