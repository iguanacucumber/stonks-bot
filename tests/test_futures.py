"""Tests for the futures leverage model.

Run: uv run python tests/test_futures.py
Synthetic series only; no network.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot.futures import (  # noqa: E402
    FuturesConfig,
    _tax_year,
    roll_dates,
    simulate_futures,
)


def dates(n: int = 300) -> pd.DatetimeIndex:
    return pd.bdate_range("2024-01-01", periods=n)


def test_roll_dates_are_quarter_ends():
    ds = dates(300)
    rolls = roll_dates(ds)
    assert len(rolls) >= 4
    for d in rolls[:4]:
        assert d.month in (3, 6, 9, 12)
        # No later session in the same month may exist.
        later = ds[(ds.year == d.year) & (ds.month == d.month) & (ds > d)]
        assert len(later) == 0


def test_financing_math_and_target_exposure():
    ds = dates(252)
    r = pd.Series(0.0, index=ds)
    cfg = FuturesConfig(
        target_leverage=2.5,
        implied_financing_bps=500.0,
        cash_rate_bps=0.0,
        roll_bps=0.0,
        commission_bps=0.0,
        rebalance_bps=0.0,
    )
    out = simulate_futures(r, config=cfg)
    # Zero index return, 5% financing on 2.5x notional, no tax because the
    # path is a loss. First-day fee is exactly 2.5*0.05/252; afterwards the
    # fixed notional makes leverage drift until the monthly reset.
    first = 1.0 - 2.5 * 0.05 / 252.0
    assert abs(out["equity"].iloc[0] - first) < 1e-12
    assert out["multiple"] < first
    assert abs(out["exposure"].iloc[0] - 2.5 / first) < 1e-9
    assert 2.4 < out["exposure"].mean() < 2.6
    assert out["maxdd"] < 0


def test_cash_interest_improves_but_does_not_create_free_lunch():
    ds = dates(252)
    r = pd.Series(0.0, index=ds)
    base = simulate_futures(
        r,
        config=FuturesConfig(
            target_leverage=2.5,
            implied_financing_bps=450.0,
            cash_rate_bps=0.0,
            roll_bps=0.0,
            commission_bps=0.0,
            rebalance_bps=0.0,
        ),
    )
    cash = simulate_futures(
        r,
        config=FuturesConfig(
            target_leverage=2.5,
            implied_financing_bps=450.0,
            cash_rate_bps=300.0,
            roll_bps=0.0,
            commission_bps=0.0,
            rebalance_bps=0.0,
        ),
    )
    assert cash["multiple"] > base["multiple"]
    # 3% cash does not cover 4.5% financing on 2.5x notional.
    assert cash["multiple"] < 1.0


def test_roll_and_commission_costs_reduce_wealth():
    ds = dates(300)
    r = pd.Series(0.001, index=ds)
    free = simulate_futures(
        r,
        config=FuturesConfig(
            target_leverage=2.5,
            implied_financing_bps=0.0,
            cash_rate_bps=0.0,
            roll_bps=0.0,
            commission_bps=0.0,
            rebalance_bps=0.0,
            tax_override=0.0,
        ),
    )
    costly = simulate_futures(
        r,
        config=FuturesConfig(
            target_leverage=2.5,
            implied_financing_bps=0.0,
            cash_rate_bps=0.0,
            roll_bps=25.0,
            commission_bps=5.0,
            rebalance_bps=5.0,
            tax_override=0.0,
        ),
    )
    assert costly["multiple"] < free["multiple"]


def test_no_lookahead_on_last_return():
    ds = dates(80)
    r1 = pd.Series(0.001, index=ds)
    r2 = r1.copy()
    r2.iloc[-1] = -0.20
    cfg = FuturesConfig(target_leverage=2.5, roll_bps=0.0, commission_bps=0.0)
    a = simulate_futures(r1, config=cfg)
    b = simulate_futures(r2, config=cfg)
    assert np.allclose(a["equity"].iloc[:-1], b["equity"].iloc[:-1])


def test_tax_loss_carry_and_boundaries():
    carry: list[list[float]] = []
    tax_loss, carry = _tax_year(-0.2, 2020, carry, FuturesConfig(tax_override=0.30))
    assert tax_loss == 0.0
    assert carry
    tax_gain, carry = _tax_year(0.1, 2021, carry, FuturesConfig(tax_override=0.30))
    assert tax_gain < 0.1 * 0.30
    assert tax_gain >= 0.0
    assert not carry or carry[0][1] >= 0.0


def test_config_rejects_bad_boundaries():
    for kwargs in (
        {"target_leverage": 0.0},
        {"target_leverage": -1.0},
        {"roll_bps": -0.1},
        {"tax_override": 1.0},
    ):
        try:
            FuturesConfig(**kwargs)
        except ValueError:
            continue
        raise AssertionError(f"accepted invalid config {kwargs}")


def main() -> None:
    checks = [
        test_roll_dates_are_quarter_ends,
        test_financing_math_and_target_exposure,
        test_cash_interest_improves_but_does_not_create_free_lunch,
        test_roll_and_commission_costs_reduce_wealth,
        test_no_lookahead_on_last_return,
        test_tax_loss_carry_and_boundaries,
        test_config_rejects_bad_boundaries,
    ]
    for fn in checks:
        fn()
        print(f"ok: {fn.__name__}")
    print(f"ok: {len(checks)} futures checks")


if __name__ == "__main__":
    main()
