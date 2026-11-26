"""Dividend event parsing, cache and withholding/PFU credit tests.

Run: uv run python tests/test_dividends.py

No test touches the network: the fetch path is patched with a synthetic Yahoo
chart result, and every other case reads a temporary cache.
"""

from __future__ import annotations

import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot import dividends, eur_ledger  # noqa: E402


def panel(dates, prices, ticker="AAA"):
    return pd.DataFrame(
        {
            "date": pd.to_datetime(dates),
            "ticker": ticker,
            "adjclose": prices,
        }
    )


class Res:
    def __init__(self, equity, trades):
        self.equity = pd.Series(equity, index=pd.to_datetime(list(equity)))
        self.trades = pd.DataFrame(
            trades,
            columns=["date", "ticker", "side", "shares", "price", "notional", "reason"],
        )


def trade(date, side, shares, price, ticker="AAA"):
    return (pd.Timestamp(date), ticker, side, shares, price, shares * price, "test")


def free(**kw):
    base = dict(
        capital=1_000.0,
        tax_rate=0.314,
        financing_bps=0.0,
        spread_bps=0.0,
        fx_bps=0.0,
        broker_bps=0.0,
        broker_fixed_eur=0.0,
        withholding_rate=0.15,
    )
    base.update(kw)
    return eur_ledger.EurLedgerConfig(**base)


def synthetic_events():
    """One normal dividend, one after-hours US timestamp, junk rows."""
    d1 = int(pd.Timestamp("2020-01-02 15:00", tz="UTC").timestamp())
    d2 = int(pd.Timestamp("2020-01-03 02:00", tz="UTC").timestamp())  # NY Jan 2
    d3 = int(pd.Timestamp("2020-04-01 15:00", tz="UTC").timestamp())
    return {
        "meta": {"currency": "USD"},
        "events": {
            "dividends": {
                str(d1): {"amount": 0.5, "date": d1},
                str(d2): {"amount": 0.75, "date": d2},
                str(d3): {"amount": -0.2, "date": d3},
                "junk": {"amount": 0.0, "date": d3},
            }
        },
    }


def test_parse_and_cache() -> None:
    df = dividends.parse_events(synthetic_events(), "AAA")
    assert list(df.columns) == ["date", "amount", "currency"]
    assert len(df) == 1 and df.iloc[0]["amount"] == 0.75
    assert str(df.iloc[0]["date"].date()) == "2020-01-02"
    assert str(df.iloc[0]["currency"]) == "USD"

    # List form, duplicate ex-date (last wins), finite/positive checks.
    result = {
        "meta": {"currency": "EUR"},
        "events": {
            "dividends": [
                {"date": "2021-01-04", "amount": 0.1},
                {"date": "2021-01-04", "amount": 0.2},
                {"date": "2021-02-01", "amount": float("nan")},
            ]
        },
    }
    df = dividends.parse_events(result, "BBB")
    assert len(df) == 1 and abs(df.iloc[0]["amount"] - 0.2) < 1e-12
    assert df.iloc[0]["currency"] == "EUR"

    with (
        TemporaryDirectory() as tmp,
        patch.object(dividends.data, "_get", return_value=synthetic_events()) as get,
    ):
        cache = Path(tmp)
        out = dividends.fetch_dividends("AAA", cache=cache)
        assert len(out) == 1 and get.call_count == 1
        assert dividends.dividend_path("AAA", cache).exists()
        # Second read must not touch Yahoo.
        with patch.object(
            dividends.data, "_get", side_effect=AssertionError("network used")
        ):
            again = dividends.fetch_dividends("AAA", cache=cache)
        assert again.equals(out)

        # Cache-only loader never touches the network.
        with patch.object(
            dividends.data, "_get", side_effect=AssertionError("network used")
        ):
            frame = dividends.load_dividends(["AAA"], fetch_missing=False, cache=cache)
        assert len(frame) == 1 and frame.iloc[0]["ticker"] == "AAA"

        # A symbol with no dividends has an explicit empty cache, not a refetch.
        with patch.object(
            dividends.data, "_get", return_value={"meta": {}, "events": {}}
        ):
            empty = dividends.fetch_dividends("NONE", cache=cache)
        assert empty.empty
        assert dividends.dividend_path("NONE", cache).exists()


def test_withholding_and_credit() -> None:
    d = ["2020-01-02", "2020-01-03", "2020-01-06"]
    r = Res({x: 1.0 for x in d}, [trade(d[0], "BUY", 0.01, 10.0)])
    divs = pd.DataFrame(
        {"date": [pd.Timestamp(d[1])], "ticker": ["AAA"], "amount": [1.0]}
    )

    out = eur_ledger.replay(
        r, panel(d, [10.0, 10.0, 10.0]), "FR", free(), dividends=divs
    )
    assert abs(out.dividends_gross - 10.0) < 1e-9
    assert abs(out.dividend_withholding - 1.5) < 1e-9
    assert abs(out.dividend_tax_topup - 1.64) < 1e-9
    assert abs(out.dividend_tax_credit - 1.5) < 1e-9
    assert abs(out.dividend_tax_total - 3.14) < 1e-9
    assert abs(out.dividends_net - 6.86) < 1e-9
    assert out.dividend_events == 1
    # Total dividend tax is PFU, not PFU + withholding (no double tax).
    assert abs(out.dividend_tax_total - 10.0 * 0.314) < 1e-9
    assert abs(out.after_tax - 996.86) < 1e-9

    # Withholding above PFU is not refunded; credit is capped at the PFU.
    high = eur_ledger.replay(
        r,
        panel(d, [10.0, 10.0, 10.0]),
        "FR",
        free(withholding_rate=0.40),
        dividends=divs,
    )
    assert abs(high.dividend_tax_total - 4.0) < 1e-9
    assert abs(high.dividend_tax_topup) < 1e-12

    # PEA: no US dividend tax and no US dividend cash.
    pea = eur_ledger.replay(
        r,
        panel(d, [10.0, 10.0, 10.0]),
        "FR",
        free(wrapper="pea"),
        dividends=divs,
    )
    assert pea.dividends_gross == 0.0 and pea.dividend_tax_total == 0.0
    assert abs(pea.after_tax - 1_000.0) < 1e-9


def test_year_dependent_dividend_rate() -> None:
    d = [
        "2024-01-02",
        "2024-06-03",
        "2025-06-03",
        "2026-06-03",
        "2026-06-04",
    ]
    r = Res({x: 1.0 for x in d}, [trade(d[0], "BUY", 0.01, 10.0)])
    divs = pd.DataFrame(
        {
            "date": [pd.Timestamp(d[1]), pd.Timestamp(d[2]), pd.Timestamp(d[3])],
            "ticker": ["AAA", "AAA", "AAA"],
            "amount": [1.0, 1.0, 1.0],
        }
    )
    out = eur_ledger.replay(
        r,
        panel(d, [10.0] * len(d)),
        "FR",
        free(tax_rate=None),  # year-dependent French PFU
        dividends=divs,
    )
    # 2024/2025: 30% on 10 EUR = 3.00 each; 2026: 31.4% = 3.14.
    assert abs(out.dividends_gross - 30.0) < 1e-9
    assert abs(out.dividend_withholding - 4.5) < 1e-9
    assert abs(out.dividend_tax_topup - 4.64) < 1e-9
    assert abs(out.dividend_tax_total - 9.14) < 1e-9
    assert abs(out.after_tax - 990.86) < 1e-9


def test_cash_accumulation() -> None:
    d = ["2020-01-02", "2020-01-03", "2020-01-06"]
    r = Res({x: 1.0 for x in d}, [trade(d[0], "BUY", 0.01, 10.0)])
    divs = pd.DataFrame(
        {"date": [pd.Timestamp(d[1])], "ticker": ["AAA"], "amount": [1.0]}
    )
    out = eur_ledger.replay(
        r,
        panel(d, [10.0, 10.0, 10.0]),
        "FR",
        free(dividend_reinvest=False),
        dividends=divs,
    )
    # Gross dividend moved to cash, one synthetic share removed, tax still PFU.
    assert abs(out.after_tax - 996.86) < 1e-9
    assert abs(float(out.cash.iloc[-1]) - 906.86) < 1e-9
    assert out.fees == 0.0
    assert abs(out.realized_current_year) < 1e-12


def test_loss_carry_is_separate() -> None:
    d = [
        "2020-01-02",
        "2020-06-01",
        "2020-12-31",
        "2021-01-04",
        "2021-01-05",
        "2021-12-31",
        "2022-01-03",
        "2022-06-01",
        "2022-12-30",
        "2023-01-03",
    ]
    r = Res(
        {x: 1.0 for x in d},
        [
            trade(d[0], "BUY", 0.1, 10.0),  # 10 shares, cost 100
            trade(d[1], "SELL", 0.1, 5.0),  # capital loss -50
            trade(d[3], "BUY", 0.1, 5.0),  # 10 shares, cost 50
            trade(d[7], "SELL", 0.1, 6.0),  # gain +10
        ],
    )
    divs = pd.DataFrame(
        {"date": [pd.Timestamp(d[4])], "ticker": ["AAA"], "amount": [1.0]}
    )
    out = eur_ledger.replay(
        r,
        panel(d, [10.0, 5.0, 5.0, 5.0, 5.0, 5.0, 5.0, 6.0, 6.0, 6.0]),
        "FR",
        free(capital=100.0),
        dividends=divs,
    )
    # Dividend PFU is paid even though a capital loss is carried forward.
    assert abs(out.dividend_tax_total - 3.14) < 1e-9
    assert abs(out.tax_paid) < 1e-12  # 2022 gain absorbed by the 2020 loss
    assert abs(out.loss_carry - 40.0) < 1e-9
    assert abs(out.dividends_gross - 10.0) < 1e-9


def main() -> None:
    test_parse_and_cache()
    test_withholding_and_credit()
    test_year_dependent_dividend_rate()
    test_cash_accumulation()
    test_loss_carry_is_separate()
    print(
        "ok: dividend parse/cache, 15% withholding, PFU credit (no double tax), "
        "cash mode, PEA skip, loss carry separate"
    )


if __name__ == "__main__":
    main()
