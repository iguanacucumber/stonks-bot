"""Broker presets: ticket fees, PEA eligibility, and after-tax arithmetic."""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot import backtest, france, strategy  # noqa: E402

TOL = 1e-9


def close(actual: float, expected: float, label: str) -> None:
    assert abs(actual - expected) < TOL, f"{label}: {actual!r} != {expected!r}"


def main():
    # Registry: every preset has a source and only Config fields.
    config_fields = set(inspect.signature(strategy.Config).parameters)
    assert set(france.BROKERS) == set(france.BROKER_INFO)
    for name, cfg in france.BROKERS.items():
        assert set(cfg) <= config_fields, f"{name}: non-Config field in BROKERS"
        info = france.BROKER_INFO[name]
        assert info["source"].startswith("https://"), name
        assert isinstance(info["pea"], bool), name
        assert info["note"], name

    # PEA eligibility: every broker except DEGIRO, and the guard says so.
    assert set(france.PEA_BROKERS) == set(france.BROKERS) - {"DEGIRO"}
    assert "DEGIRO" not in france.PEA_BROKERS
    try:
        france.require_pea_broker("degiro")
        raise AssertionError("DEGIRO must be rejected for a PEA")
    except ValueError as exc:
        assert "does not offer a PEA" in str(exc)
    assert france.require_pea_broker("saxo") == "Saxo Banque"
    assert france.DEFAULT_BROKER == "Interactive Brokers"
    assert france.DEFAULT_BROKER in france.PEA_BROKERS
    assert {
        "interactive-brokers",
        "ibkr",
        "saxo",
        "xtb",
        "fortuneo",
        "boursobank",
        "degiro",
    } <= set(france.ALIASES)
    for alias, name in france.ALIASES.items():
        assert france.broker_name(alias) == name

    # PEA / Euronext tickets.
    pea_cases = {
        "Bourse Direct": [(500, 0.99), (1000, 1.90), (4400, 3.80), (10000, 9.00)],
        "Trade Republic": [(100, 1.00), (100000, 1.00)],
        "Interactive Brokers": [(500, 1.25), (2500, 1.25), (10000, 5.00)],
        "Saxo Banque": [(500, 2.00), (2500, 2.00), (10000, 8.00)],
        "XTB": [(10000, 0.00), (100000, 0.00)],
        "Fortuneo": [(500, 1.75), (1000, 3.50), (10000, 35.00)],
        "BoursoBank": [(500, 1.99), (10000, 50.00)],
        "DEGIRO": [(100, 2.00), (100000, 2.00)],
    }
    for broker, cases in pea_cases.items():
        for notional, expected in cases:
            close(
                france.ticket_fee(broker, notional, False),
                expected,
                f"{broker} EUR {notional}",
            )

    # US tickets.
    us_cases = {
        "Bourse Direct": [(10000, 8.50), (20000, 18.00)],
        "Trade Republic": [(10000, 1.00)],
        # USD 0.005/share at USD 200 = 0.25 bps, USD 1 minimum to USD 40k.
        "Interactive Brokers": [(10000, 1.00), (40000, 1.00), (100000, 2.50)],
        "Saxo Banque": [(1250, 1.00), (10000, 8.00)],
        "XTB": [(10000, 0.00)],
        "Fortuneo": [(5000, 50.00), (10000, 50.00), (100000, 200.00)],
        "BoursoBank": [(10000, 60.00)],
        "DEGIRO": [(10000, 2.00)],
    }
    for broker, cases in us_cases.items():
        for notional, expected in cases:
            close(
                france.ticket_fee(broker, notional, True),
                expected,
                f"{broker} USD {notional}",
            )

    # A Config built from a preset carries the tariff, not the metadata.
    kw = france.broker_kwargs("boursobank", 10000)
    assert kw["initial_capital"] == 10000.0
    assert kw["broker_bps"] == 50.0 and kw["us_bps"] == 60.0
    assert "source" not in kw and "pea" not in kw
    cfg = strategy.Config(**kw)
    assert cfg.broker_grid == ((500, 1.99),)

    # after_tax sanity: 2x terminal equity, 10% remaining tax, 1% fees.
    idx = pd.DatetimeIndex(["2020-01-01", "2020-12-31"])
    res = backtest.Result(
        equity=pd.Series([1.0, 2.0], index=idx),
        cash=pd.Series([0.0, 0.0], index=idx),
        trades=pd.DataFrame(),
        weights=pd.DataFrame(),
        nav=pd.DataFrame(),
        tax_paid=0.0,
        fees_paid=0.01,
        ftt_paid=0.0,
        unrealized_tax=0.10,
    )
    f = france.after_tax(res, 10000, "FR")
    close(f["pre_tax_eur"], 20000.0, "pre-tax EUR")
    close(f["fees_eur"], 100.0, "fees EUR")
    close(f["exit_tax_eur"], 1000.0, "exit tax EUR")
    close(f["after_tax_eur"], 19000.0, "after-tax EUR")
    close(f["after_tax_eur"] / 10000.0, 1.90, "after-tax multiple")
    assert f["after_tax_eur"] < f["pre_tax_eur"]

    # FX translation: USDCHF quotes CHF per USD, so one CHF is
    # 1/(EURUSD*USDCHF) EUR, not EURUSD/USDCHF.
    fx_dates = pd.bdate_range("2020-01-01", periods=2)
    fx = {"EURUSD=X": 1.10, "GBPUSD=X": 1.30, "USDCHF=X": 0.80}
    old_fetch = france.data.fetch_one
    france.data.fetch_one = lambda symbol: pd.DataFrame(
        {"date": fx_dates, "adjclose": fx[symbol]}
    )
    try:
        close(france.eur_per_unit("US").iloc[0], 1.0 / 1.10, "USD->EUR")
        close(france.eur_per_unit("GB").iloc[0], 1.30 / 1.10, "GBP->EUR")
        close(france.eur_per_unit("CH").iloc[0], 1.0 / (1.10 * 0.80), "CHF->EUR")
    finally:
        france.data.fetch_one = old_fetch

    print(
        "ok: 8 broker presets, PEA guard, ticket fees, after-tax helper "
        f"({len(france.PEA_BROKERS)} PEA-eligible)"
    )


if __name__ == "__main__":
    main()
