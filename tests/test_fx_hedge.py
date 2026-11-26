"""FX-hedge primitives and EUR-ledger integration tests.

Run: uv run python tests/test_fx_hedge.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot import eur_ledger  # noqa: E402
from bot.fx_hedge import (  # noqa: E402
    FxHedgeConfig,
    forward_rate,
    hedge_notional,
    hedge_pnl_eur,
    month_end_dates,
    ratio_for_date,
    roll_cost_eur,
)


def test_forward_and_pnl() -> None:
    assert np.isclose(forward_rate(0.90, 200.0, 30), 0.8985)
    # Zero carry is exactly spot, and full hedge P&L has the right sign.
    assert forward_rate(0.90, 0.0, 30) == 0.90
    assert hedge_pnl_eur(10_000, 0.8985, 0.85) > 0
    assert hedge_pnl_eur(10_000, 0.8985, 0.95) < 0
    assert hedge_pnl_eur(10_000, 0.8985, 0.8985) == 0.0
    # Roll cost is on EUR notional; 5 bps of €9,000 is €4.50.
    assert np.isclose(roll_cost_eur(10_000, 0.90, 5.0), 4.50)
    assert roll_cost_eur(10_000, 0.90, 0.0) == 0.0
    try:
        forward_rate(0.9, 0.0, -1)
        raise AssertionError("negative days must raise")
    except ValueError:
        pass


def test_ratio_boundaries() -> None:
    assert hedge_notional(100_000, 0.0) == 0.0
    assert hedge_notional(100_000, 1.0) == 100_000
    assert hedge_notional(100_000, 0.25) == 25_000
    for bad in (-0.01, 1.01):
        try:
            hedge_notional(100_000, bad)
            raise AssertionError("ratio outside [0,1] must raise")
        except ValueError:
            pass
    assert FxHedgeConfig(ratio=0.0).ratio == 0.0
    assert FxHedgeConfig(ratio=1.0).ratio == 1.0


def test_ratio_no_lookahead() -> None:
    dates = pd.bdate_range("2019-01-01", periods=300)
    fx = pd.Series(np.linspace(0.9, 0.8, len(dates)), index=dates)
    cfg = FxHedgeConfig(mode="threshold", low_ratio=0.25, high_ratio=1.0, ma_window=50)
    # The ratio at date k depends only on the trailing ma_window, so appending
    # future FX values and truncating back must give the same answer.
    for k in (50, 120, 200):
        assert ratio_for_date(fx.iloc[:k], cfg) == ratio_for_date(
            fx.iloc[k - 50 : k], cfg
        )
    # Threshold is binary and uses only the trailing window.
    assert ratio_for_date(fx, cfg) in (0.25, 1.0)
    # Before a full MA, the low ratio is used.
    assert ratio_for_date(fx.iloc[:10], cfg) == 0.25


def test_month_end_dates() -> None:
    dates = pd.bdate_range("2020-01-01", "2020-03-15")
    rolls = month_end_dates(dates)
    assert pd.Timestamp("2020-01-31") in rolls
    assert pd.Timestamp("2020-02-28") in rolls
    assert rolls[-1] == dates[-1]


def _fake_book() -> tuple[SimpleNamespace, pd.DataFrame, pd.Series]:
    dates = pd.bdate_range("2020-01-01", "2020-06-30")
    # USD weakens from 0.90 to 0.80 EUR per USD: unhedged loses, full hedge wins.
    fx = pd.Series(np.linspace(0.90, 0.80, len(dates)), index=dates)
    panel = pd.DataFrame({"date": dates, "ticker": "SPY", "adjclose": 100.0})
    trades = pd.DataFrame(
        [
            {
                "date": dates[0],
                "ticker": "SPY",
                "side": "BUY",
                "shares": 0.01,
                "price": 100.0,
                "notional": 1.0,
                "reason": "test",
            }
        ]
    )
    res = SimpleNamespace(equity=pd.Series(1.0, index=dates), trades=trades)
    return res, panel, fx


def test_ledger_integration() -> None:
    res, panel, fx = _fake_book()
    cfg = eur_ledger.EurLedgerConfig(
        capital=10_000,
        tax_rate=0.0,
        financing_bps=0.0,
        spread_bps=0.0,
        fx_bps=0.0,
        broker_bps=0.0,
        broker_fixed_eur=0.0,
    )
    with patch.object(eur_ledger, "_fx_series", return_value=fx):
        unhedged = eur_ledger.replay(res, panel, "US", cfg)
        zero = eur_ledger.replay(
            res, panel, "US", cfg, fx_hedge=FxHedgeConfig(ratio=0.0)
        )
        full = eur_ledger.replay(
            res, panel, "US", cfg, fx_hedge=FxHedgeConfig(ratio=1.0, carry_bps=0.0)
        )
        half = eur_ledger.replay(
            res, panel, "US", cfg, fx_hedge=FxHedgeConfig(ratio=0.5, carry_bps=0.0)
        )
    assert np.isclose(unhedged.multiple, zero.multiple)
    assert np.isclose(zero.fx_hedge_pnl, 0.0)
    assert full.fx_hedge_pnl > 0
    assert full.multiple > unhedged.multiple
    assert unhedged.multiple < half.multiple < full.multiple
    assert full.fx_hedge_periods >= 4
    assert len(full.fx_hedge_log) == full.fx_hedge_periods
    # A positive roll cost must reduce the hedged terminal.
    with patch.object(eur_ledger, "_fx_series", return_value=fx):
        costly = eur_ledger.replay(
            res,
            panel,
            "US",
            cfg,
            fx_hedge=FxHedgeConfig(ratio=1.0, carry_bps=0.0, roll_bps=10.0),
        )
    assert costly.multiple < full.multiple


def test_ledger_tax_bucket() -> None:
    """Hedge P&L is part of the annual capital-gains bucket, not tax-free."""
    res, panel, fx = _fake_book()
    # Make the underlying rise enough that the book + hedge is a net gain, so
    # the annual CTO tax is actually due.
    panel = panel.copy()
    panel["adjclose"] = np.linspace(100.0, 130.0, len(panel))
    cfg = eur_ledger.EurLedgerConfig(
        capital=10_000,
        tax_rate=0.30,
        financing_bps=0.0,
        spread_bps=0.0,
        fx_bps=0.0,
        broker_bps=0.0,
        broker_fixed_eur=0.0,
    )
    with patch.object(eur_ledger, "_fx_series", return_value=fx):
        no_tax = eur_ledger.replay(
            res,
            panel,
            "US",
            eur_ledger.EurLedgerConfig(**{**cfg.__dict__, "tax_rate": 0.0}),
            fx_hedge=FxHedgeConfig(ratio=1.0, carry_bps=0.0),
        )
        taxed = eur_ledger.replay(
            res,
            panel,
            "US",
            cfg,
            fx_hedge=FxHedgeConfig(ratio=1.0, carry_bps=0.0),
        )
    assert taxed.after_tax < no_tax.after_tax
    assert taxed.tax_paid + taxed.exit_tax > 0


def main() -> None:
    test_forward_and_pnl()
    test_ratio_boundaries()
    test_ratio_no_lookahead()
    test_month_end_dates()
    test_ledger_integration()
    test_ledger_tax_bucket()
    print("ok: FX hedge signs, costs, boundaries, causality and EUR-ledger tax")


if __name__ == "__main__":
    main()
