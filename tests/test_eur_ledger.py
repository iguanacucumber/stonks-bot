"""Checks for the EUR ledger overlay. Run: uv run python tests/test_eur_ledger.py."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot import eur_ledger  # noqa: E402


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
        tax_rate=0.0,
        financing_bps=0.0,
        spread_bps=0.0,
        fx_bps=0.0,
        broker_bps=0.0,
        broker_fixed_eur=0.0,
        capital=10.0,
    )
    base.update(kw)
    return eur_ledger.EurLedgerConfig(**base)


def main() -> None:
    # 1. no FX, no tax, no fees: buy one share at 10, mark at 12
    d = ["2020-01-02", "2020-01-03"]
    r = Res(
        {d[0]: 1.0, d[1]: 1.2},
        [trade(d[0], "BUY", 0.1, 10.0)],
    )
    out = eur_ledger.replay(r, panel(d, [10.0, 12.0]), "FR", free())
    assert abs(out.after_tax - 12.0) < 1e-9, out.after_tax
    assert abs(out.multiple - 1.2) < 1e-9

    # 2. constant FX 0.5: USD 12 becomes EUR 6, cash 5 + position 6 = 11
    with patch.object(
        eur_ledger.france,
        "eur_per_unit",
        lambda market: pd.Series(0.5, index=pd.to_datetime(d)),
    ):
        out = eur_ledger.replay(r, panel(d, [10.0, 12.0]), "US", free())
    assert abs(out.after_tax - 11.0) < 1e-9, out.after_tax
    assert abs(out.fx.iloc[-1] - 0.5) < 1e-12

    # 3. annual tax: buy 10 at 1, sell at 2, pay 31.4% at the next year
    d = ["2020-01-02", "2020-06-01", "2020-12-31", "2021-01-04"]
    r = Res(
        {x: 1.0 for x in d},
        [
            trade(d[0], "BUY", 0.1, 1.0),
            trade(d[1], "SELL", 0.1, 2.0),
        ],
    )
    out = eur_ledger.replay(
        r,
        panel(d, [1.0, 2.0, 2.0, 2.0]),
        "FR",
        free(capital=100.0, tax_rate=0.314),
    )
    assert abs(out.tax_paid - 3.14) < 1e-9, out.tax_paid
    assert abs(out.exit_tax) < 1e-12
    assert abs(out.after_tax - 106.86) < 1e-9, out.after_tax

    # 4. loss carry: -10 in 2020 offsets +20 in 2021; tax only on 10
    d = [
        "2020-01-02",
        "2020-02-03",
        "2020-12-31",
        "2021-01-04",
        "2021-02-01",
        "2021-12-31",
        "2022-01-03",
    ]
    r = Res(
        {x: 1.0 for x in d},
        [
            trade(d[0], "BUY", 0.1, 2.0),
            trade(d[1], "SELL", 0.1, 1.0),  # -10
            trade(d[3], "BUY", 0.1, 1.0),
            trade(d[4], "SELL", 0.1, 3.0),  # +20, net 10
        ],
    )
    out = eur_ledger.replay(
        r,
        panel(d, [2.0, 1.0, 1.0, 1.0, 3.0, 3.0, 3.0]),
        "FR",
        free(capital=100.0, tax_rate=0.314),
    )
    assert abs(out.tax_paid - 3.14) < 1e-9, out.tax_paid

    # 5. HIFO: buy at 10 and 20, sell one at 15 -> realizes -5, not +5
    d = ["2020-01-02", "2020-01-03", "2020-01-06"]
    r = Res(
        {x: 1.0 for x in d},
        [
            trade(d[0], "BUY", 0.01, 10.0),
            trade(d[1], "BUY", 0.01, 20.0),
            trade(d[2], "SELL", 0.01, 15.0),
        ],
    )
    out = eur_ledger.replay(r, panel(d, [10.0, 20.0, 15.0]), "FR", free(capital=100.0))
    assert abs(out.realized_current_year + 5.0) < 1e-9, out.realized_current_year
    assert abs(out.unrealized_gain - 5.0) < 1e-9, out.unrealized_gain

    # 6. fixed fee is charged per trade
    out = eur_ledger.replay(
        r,
        panel(d, [10.0, 20.0, 15.0]),
        "FR",
        free(capital=100.0, broker_fixed_eur=1.0),
    )
    assert abs(out.fees - 3.0) < 1e-9, out.fees

    # 7. no trades: cash is unchanged, equity is flat
    r = Res({"2020-01-02": 1.0, "2020-01-03": 1.0}, [])
    out = eur_ledger.replay(
        r, panel(["2020-01-02", "2020-01-03"], [10.0, 11.0]), "FR", free()
    )
    assert abs(out.after_tax - 10.0) < 1e-9
    assert abs(out.fees) < 1e-12

    # 8. FX path: buy at 0.5, mark at 0.25 -> EUR position halves
    with patch.object(
        eur_ledger.france,
        "eur_per_unit",
        lambda market: pd.Series([0.5, 0.25], index=pd.to_datetime(d[:2])),
    ):
        out = eur_ledger.replay(
            Res(
                {"2020-01-02": 1.0, "2020-01-03": 1.0},
                [trade("2020-01-02", "BUY", 0.1, 10.0)],
            ),
            panel(d[:2], [10.0, 10.0]),
            "US",
            free(),
        )
    assert abs(out.after_tax - 7.5) < 1e-9, out.after_tax

    print("ok: EUR ledger HIFO, FX, PFU, loss carry, fees, flat cash")


if __name__ == "__main__":
    main()
