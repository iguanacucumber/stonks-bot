"""Assurance-vie fee, 8-year tax, 990 I and break-even tests."""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot import assurance_vie as av  # noqa: E402
from bot import estate  # noqa: E402


def close(actual: float, expected: float, tol: float = 1e-9) -> None:
    assert abs(float(actual) - float(expected)) <= tol, (actual, expected)


def test_8_year_tax_and_abattement() -> None:
    close(av.av_terminal_tax(100_000, years=5), 30_000)
    close(av.av_terminal_tax(100_000, years=10), (100_000 - 4_600) * 0.247)
    close(
        av.av_terminal_tax(100_000, years=10, abattement=9_200),
        (100_000 - 9_200) * 0.247,
    )
    close(av.av_terminal_tax(3_000, years=10), 0)  # gain below abattement
    close(av.av_after_tax_multiple(2.0, years=10, capital=10_000), 1.0 + 1.0 - (10_000 - 4_600) * 0.247 / 10_000)
    # Explicit early override.
    close(av.av_terminal_tax(100_000, years=10, before_8=True), 30_000)


def test_annual_fee_compounding() -> None:
    dates = pd.bdate_range("2020-01-01", periods=253)
    equity = pd.Series(1.0, index=dates)
    nav = av.av_nav_path(equity, pd.DataFrame(), av.AVConfig(annual_fee=0.008))
    daily = (1.008) ** (1 / 252) - 1
    close(float(nav.iloc[-1]), (1 - daily) ** 252, 1e-12)
    close(float(nav.iloc[0]), 1.0, 1e-12)
    # Zero fee is the identity.
    same = av.av_nav_path(equity, pd.DataFrame(), av.AVConfig(annual_fee=0.0))
    close(float(same.iloc[-1]), 1.0, 1e-12)


def test_transaction_fee() -> None:
    dates = pd.bdate_range("2020-01-01", periods=4)
    equity = pd.Series(10_000.0, index=dates)
    trades = pd.DataFrame(
        {
            "date": [dates[1]],
            "ticker": ["AAA"],
            "side": ["BUY"],
            "shares": [1.0],
            "price": [1000.0],
            "notional": [1000.0],
            "reason": ["test"],
        }
    )
    nav = av.av_nav_path(
        equity, trades, av.AVConfig(annual_fee=0.0, transaction_bps=10.0)
    )
    close(float(nav.iloc[0]), 1.0, 1e-12)
    close(float(nav.iloc[1]), 0.9999, 1e-12)
    close(float(nav.iloc[-1]), 0.9999, 1e-12)


def test_990i_before_and_after_70() -> None:
    close(av.av_estate_tax(660_000, "child", 1, premiums_before_70=True), 101_500)
    close(av.av_estate_tax(660_000, "child", 2, premiums_before_70=True), 71_000)
    close(av.av_estate_tax(660_000, "spouse", 1, premiums_before_70=True), 0)
    after = av.av_estate_tax(
        660_000,
        "child",
        1,
        premiums_before_70=False,
        premiums_after_70=660_000,
    )
    expected = estate.french_inheritance_tax(660_000 - 30_500, "child", 1)
    close(after, expected)
    try:
        av.av_estate_tax(660_000, premiums_before_70=False)
        raise AssertionError("after-70 needs premiums")
    except ValueError:
        pass


def test_break_even_fee() -> None:
    dates = pd.bdate_range("2015-01-01", periods=3_025)
    # A smooth 12-year path ending at 2.0x; enough to bracket a positive fee.
    equity = pd.Series(np.linspace(1.0, 2.0, len(dates)), index=dates)
    be = av.break_even_fee(
        equity,
        pd.DataFrame(),
        cto_multiple=1.50,
        years=12.0,
        transaction_bps=0.0,
    )
    assert be is not None and 0 < be < 0.10, be
    at = av.av_result(
        equity,
        pd.DataFrame(),
        10_000.0,
        av.AVConfig(annual_fee=be, transaction_bps=0.0, years=12.0),
    )
    close(at["after_tax_multiple"], 1.50, 2e-4)
    # A CTO multiple above the AV-at-zero result has no break-even fee.
    assert (
        av.break_even_fee(
            equity,
            pd.DataFrame(),
            cto_multiple=10.0,
            years=12.0,
            transaction_bps=0.0,
        )
        is None
    )


def test_estate_comparison_beneficiaries() -> None:
    one = av.estate_comparison(660_000, 1)
    two = av.estate_comparison(660_000, 2)
    close(one["av_990i_tax"], 101_500)
    close(two["av_990i_tax"], 71_000)
    assert two["av_advantage"] > one["av_advantage"]
    assert one["av_advantage"] > 0


def test_config_validation() -> None:
    for bad in (
        dict(annual_fee=-0.01),
        dict(annual_fee=0.2),
        dict(transaction_bps=-1),
        dict(abattement=-1),
        dict(years=0),
    ):
        try:
            av.AVConfig(**bad)
            raise AssertionError(f"accepted bad config {bad}")
        except ValueError:
            pass


def main() -> None:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"ok: {fn.__name__}")
    print(f"ok: {len(tests)} assurance-vie tests")


if __name__ == "__main__":
    main()
