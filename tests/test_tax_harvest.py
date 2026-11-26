"""Tax-loss harvesting tests: lots, boundaries, turnover, re-entry, no look-ahead."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot import eur_ledger  # noqa: E402
from bot.tax_harvest import HarvestConfig, HarvestStrategy  # noqa: E402


def panel(dates, prices, opens=None, ticker="AAA"):
    out = {
        "date": pd.to_datetime(dates),
        "ticker": ticker,
        "adjclose": prices,
    }
    if opens is not None:
        out["open"] = opens
    return pd.DataFrame(out)


class Res:
    def __init__(self, dates, trades=None, weights=None):
        self.equity = pd.Series([1.0] * len(dates), index=pd.to_datetime(dates))
        self.trades = pd.DataFrame(
            trades or [],
            columns=["date", "ticker", "side", "shares", "price", "notional", "reason"],
        )
        self.weights = (
            weights if weights is not None else pd.DataFrame(index=self.equity.index)
        )


def trade(date, side, shares, price, ticker="AAA", reason="test"):
    return (
        pd.Timestamp(date),
        ticker,
        side,
        shares,
        price,
        shares * price,
        reason,
    )


def free(**kw):
    base = dict(
        tax_rate=0.0,
        financing_bps=0.0,
        spread_bps=0.0,
        fx_bps=0.0,
        broker_bps=0.0,
        broker_fixed_eur=0.0,
        capital=100.0,
    )
    base.update(kw)
    return eur_ledger.EurLedgerConfig(**base)


def strategy(dates, weights, cfg, capital=100.0):
    res = Res(dates, weights=weights)
    return HarvestStrategy(res, cfg, capital=capital)


def test_lot_selection_and_target_weights() -> None:
    d = pd.Timestamp("2020-01-02")
    weights = pd.DataFrame({"AAA": [1.0], "BBB": [0.0]}, index=[d])
    s = strategy(
        [d],
        weights,
        HarvestConfig(threshold=0.10, reenter=True, max_turnover=2.0),
        capital=100.0,
    )
    lots = {"AAA": [[1.0, 100.0]], "BBB": [[1.0, 100.0]]}
    pos = {"AAA": 1.0, "BBB": 1.0}
    prices = pd.Series({"AAA": 80.0, "BBB": 80.0})
    out = s(d, pos, lots, prices, cash=0.0)
    # AAA is a 20% loss and kept; BBB is a 20% loss but the target is zero.
    assert [x["ticker"] for x in out] == ["AAA", "AAA"]
    assert [x["side"] for x in out] == ["SELL", "BUY"]
    assert abs(out[0]["shares"] - 1.0) < 1e-12
    assert abs(out[0]["price"] - 80.0) < 1e-12
    assert s.stats.realized_loss < 0


def test_threshold_boundary_and_band() -> None:
    d = pd.Timestamp("2020-01-02")
    weights = pd.DataFrame({"AAA": [1.0]}, index=[d])
    prices = pd.Series({"AAA": 95.0})
    pos = {"AAA": 1.0}
    lots = {"AAA": [[1.0, 100.0]]}

    exact = strategy(
        [d], weights, HarvestConfig(threshold=0.05, reenter=False, max_turnover=2.0)
    )
    assert exact(d, pos, lots, prices, 0.0), "exact -5% must harvest"

    above = strategy(
        [d], weights, HarvestConfig(threshold=0.05, reenter=False, max_turnover=2.0)
    )
    assert not above(d, pos, lots, pd.Series({"AAA": 95.01}), 0.0)

    band = strategy(
        [d],
        weights,
        HarvestConfig(threshold=0.05, reenter=True, band=0.99, max_turnover=2.0),
    )
    assert not band(d, pos, lots, prices, 1000.0), "notional below band must be skipped"


def test_turnover_cap() -> None:
    d = pd.Timestamp("2020-01-02")
    weights = pd.DataFrame({"AAA": [1.0], "BBB": [1.0]}, index=[d])
    s = strategy(
        [d],
        weights,
        HarvestConfig(threshold=0.05, reenter=True, max_turnover=0.5),
    )
    lots = {"AAA": [[1.0, 100.0]], "BBB": [[1.0, 100.0]]}
    pos = {"AAA": 1.0, "BBB": 1.0}
    prices = pd.Series({"AAA": 80.0, "BBB": 90.0})
    out = s(d, pos, lots, prices, 0.0)
    sold = sum(x["shares"] * x["price"] for x in out if x["side"] == "SELL")
    equity = 80.0 + 90.0
    assert sold <= 0.5 * equity + 1e-9, (sold, equity)
    assert all(x["ticker"] == "AAA" for x in out), "largest loss should be first"


def test_no_look_ahead_uses_open_not_close() -> None:
    d = ["2020-01-02", "2020-01-03"]
    p = panel(d, prices=[100.0, 90.0], opens=[100.0, 80.0])
    weights = pd.DataFrame({"AAA": [1.0, 1.0]}, index=pd.to_datetime(d))
    res = Res(d, trades=[trade(d[0], "BUY", 0.01, 100.0)], weights=weights)
    s = HarvestStrategy(
        res, HarvestConfig(threshold=0.10, reenter=True, max_turnover=2.0)
    )
    out = eur_ledger.replay(
        res,
        p,
        "FR",
        free(capital=100.0),
        harvest=s,
        harvest_dates=[pd.Timestamp(d[1])],
    )
    sells = out.harvest_trades[out.harvest_trades.side == "SELL"]
    assert len(sells) == 1
    # The session open is 80, the close is 90; a look-ahead close would be 90.
    assert abs(float(sells.iloc[0]["price"]) - 80.0) < 1e-12
    assert abs(float(sells.iloc[0]["shares"]) - 1.0) < 1e-12


def test_reentry_resets_basis_and_fee_multiplier() -> None:
    d = ["2020-01-02", "2020-01-03", "2020-01-06"]
    p = panel(d, prices=[100.0, 80.0, 120.0], opens=[100.0, 80.0, 120.0])
    weights = pd.DataFrame({"AAA": [1.0, 1.0, 1.0]}, index=pd.to_datetime(d))
    res = Res(
        d,
        trades=[trade(d[0], "BUY", 0.01, 100.0, reason="rebalance")],
        weights=weights,
    )
    s = HarvestStrategy(
        res, HarvestConfig(threshold=0.10, reenter=True, max_turnover=2.0)
    )
    out = eur_ledger.replay(
        res,
        p,
        "FR",
        free(capital=100.0, broker_fixed_eur=1.0),
        harvest=s,
        harvest_dates=[pd.Timestamp(d[1])],
    )
    # Sell at 80 (loss 20), rebuy at 80 + fee basis. Two extra tickets.
    assert out.harvest_count == 2
    assert abs(out.harvest_fees - 2.0) < 1e-9
    assert out.unrealized_gain > 0  # rebought at 80, marked at 120

    # 2x fee multiplier doubles the same two tickets.
    res2 = Res(
        d,
        trades=[trade(d[0], "BUY", 0.01, 100.0, reason="rebalance")],
        weights=weights,
    )
    s2 = HarvestStrategy(
        res2, HarvestConfig(threshold=0.10, reenter=True, max_turnover=2.0)
    )
    out2 = eur_ledger.replay(
        res2,
        p,
        "FR",
        free(capital=100.0, broker_fixed_eur=1.0, fee_multiplier=2.0),
        harvest=s2,
        harvest_dates=[pd.Timestamp(d[1])],
    )
    assert abs(out2.harvest_fees - 4.0) < 1e-9


def test_harvest_loss_offsets_other_gains() -> None:
    d = [
        "2020-01-02",
        "2020-02-03",
        "2020-06-01",
        "2020-12-31",
        "2021-01-04",
        "2022-01-03",
    ]
    p = panel(d, prices=[100.0, 100.0, 95.0, 120.0, 120.0, 120.0], ticker="AAA")
    p2 = panel(d, prices=[100.0, 100.0, 100.0, 120.0, 120.0, 120.0], ticker="BBB")
    p = pd.concat([p, p2], ignore_index=True)
    weights = pd.DataFrame(
        {"AAA": [1.0] * len(d), "BBB": [1.0] * len(d)}, index=pd.to_datetime(d)
    )
    res = Res(
        d,
        trades=[
            trade(d[0], "BUY", 0.005, 100.0, ticker="AAA"),
            trade(d[0], "BUY", 0.005, 100.0, ticker="BBB"),
            trade(d[4], "SELL", 0.005, 120.0, ticker="BBB"),
        ],
        weights=weights,
    )
    s = HarvestStrategy(res, HarvestConfig(threshold=0.05, reenter=True))
    out = eur_ledger.replay(
        res,
        p,
        "FR",
        free(capital=200.0, tax_rate=0.30),
        harvest=s,
        harvest_dates=[pd.Timestamp(d[2])],
    )
    # The AAA harvest realises -5; the BBB +20 is netted to +15 in 2020.
    assert abs(out.tax_paid - 4.5) < 1e-9, out.tax_paid
    assert out.harvest_count == 2


def test_config_validation() -> None:
    for bad in (
        dict(threshold=-0.1),
        dict(threshold=1.0),
        dict(band=-0.1),
        dict(max_turnover=0.0),
        dict(fee_multiplier=-1.0),
    ):
        try:
            HarvestConfig(**bad)
            raise AssertionError(f"accepted bad config {bad}")
        except ValueError:
            pass


def main() -> None:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"ok: {fn.__name__}")
    print(f"ok: {len(tests)} tax-harvest tests")


if __name__ == "__main__":
    main()
