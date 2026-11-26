"""Currency dimension: native, terminal-FX headline, and EUR ledger.

A USD-denominated book is not the same wealth as a EUR-denominated tax book.
Three numbers must be labelled separately and never mixed:

``native``
    After-tax result in the asset currency (USD for TECH/US).  This is a
    research number, not what a French investor keeps.
``terminal_fx``
    The existing report convention: native after-tax result multiplied by the
    terminal EUR/USD rate.  It ignores annual EUR tax on FX moves and the
    trade-date EUR cost basis, so it is an upper bound for a French CTO and is
    **not** the real-life result.
``eur_ledger``
    The real-life accounting replay from `bot/eur_ledger.py`: EUR cost basis at
    trade-date FX, HIFO lots, annual PFU and exit tax.  The module is merged on
    master; this package imports it when present and reports an explicit
    "unavailable" row rather than silently falling back to the optimistic
    headline.

`reproduce.py` prints all three columns.  Trial rows carry an
``accounting_mode`` field so a result cannot be compared across modes by
accident.
"""

from __future__ import annotations

from importlib.util import find_spec
from typing import Any

import pandas as pd

from .metrics import cagr_from_multiple


ACCOUNTING_MODES = ("native", "terminal_fx", "eur_ledger")
REAL_LIFE_MODE = "eur_ledger"
MODE_LABELS = {
    "native": "native currency, after tax (research)",
    "terminal_fx": "terminal-FX headline (legacy upper bound, NOT real-life)",
    "eur_ledger": "EUR ledger (annual EUR tax, trade-date FX; real-life)",
}


def eur_ledger_available() -> bool:
    return find_spec("bot.eur_ledger") is not None


def require_mode(mode: str) -> str:
    if mode not in ACCOUNTING_MODES:
        raise ValueError(f"unknown accounting mode: {mode}")
    return mode


def is_real_life(mode: str) -> bool:
    return require_mode(mode) == REAL_LIFE_MODE


def native_after_tax_multiple(res, capital: float) -> float:
    """After-tax multiple in the asset currency (engine's unrealized tax)."""
    if capital <= 0:
        raise ValueError("capital must be > 0")
    eq = float(res.equity.iloc[-1])
    tax = float(getattr(res, "unrealized_tax", 0.0))
    return eq - tax


def ledger_summary(
    res,
    panel: pd.DataFrame,
    market: str,
    capital: float = 10_000.0,
    tax_rate: float | None = None,
    financing_bps: float = 500.0,
    spread_bps: float = 5.0,
    fx_bps: float = 2.0,
    broker_bps: float = 0.25,
    broker_fixed_eur: float = 1.0,
    ledger: Any | None = None,
) -> dict[str, Any] | None:
    """Run the EUR ledger overlay if `bot.eur_ledger` is merged.

    ``ledger`` is an optional already-computed ``EurLedgerResult``.  Passing it
    makes the function testable without rebuilding the accounting engine and
    lets a caller reuse one replay across several report tables.
    """
    if ledger is None:
        if not eur_ledger_available():
            return None
        from bot import eur_ledger  # type: ignore[import-not-found]

        cfg = eur_ledger.EurLedgerConfig(
            capital=capital,
            tax_rate=tax_rate,
            financing_bps=financing_bps,
            spread_bps=spread_bps,
            fx_bps=fx_bps,
            broker_bps=broker_bps,
            broker_fixed_eur=broker_fixed_eur,
        )
        ledger = eur_ledger.replay(res, panel, market, cfg)
    years = (len(res.equity) - 1) / 252.0
    return {
        "mode": "eur_ledger",
        "multiple": float(ledger.multiple),
        "after_tax_value": float(ledger.after_tax),
        "cagr": cagr_from_multiple(float(ledger.multiple), years),
        "max_dd": float(ledger.maxdd),
        "fees": float(ledger.fees),
        "tax_paid": float(ledger.tax_paid),
        "exit_tax": float(ledger.exit_tax),
        "reconcile_gap": float(ledger.reconcile_gap),
        "years": years,
    }


def dimension_table(
    res,
    panel: pd.DataFrame,
    market: str,
    capital: float = 10_000.0,
    tax_rate: float | None = None,
    native_label: str = "native",
    fx: float | None = None,
    ledger: Any | None = None,
) -> pd.DataFrame:
    """Rows for native, terminal-FX and (when available) EUR ledger.

    ``fx`` overrides the terminal EUR-per-native-unit factor (tests and
    sensitivity runs).  Otherwise it comes from ``bot.france.after_tax``.
    """
    from bot import france

    f = france.after_tax(res, capital, market)
    native = native_after_tax_multiple(res, capital)
    factor = float(fx) if fx is not None else float(f["fx"])
    years = (len(res.equity) - 1) / 252.0
    rows = [
        {
            "accounting_mode": "native",
            "label": MODE_LABELS["native"],
            "multiple": native,
            "after_tax_value": native * capital,
            "cagr": cagr_from_multiple(native, years),
            "max_dd": f["maxdd"],
            "fees": f["fees_native"],
            "tax_paid": f["tax_paid_native"],
            "exit_tax": f["exit_tax_native"],
            "is_real_life": False,
            "note": native_label,
        },
        {
            "accounting_mode": "terminal_fx",
            "label": MODE_LABELS["terminal_fx"],
            "multiple": native * factor,
            "after_tax_value": native * factor * capital,
            "cagr": cagr_from_multiple(native * factor, years),
            "max_dd": f["maxdd"],
            "fees": f["fees_native"] * factor,
            "tax_paid": f["tax_paid_native"] * factor,
            "exit_tax": f["exit_tax_native"] * factor,
            "is_real_life": False,
            "note": f"terminal FX {factor:.4f}; FX path/tax timing ignored",
        },
    ]
    led = ledger_summary(
        res,
        panel,
        market,
        capital=capital,
        tax_rate=tax_rate,
        ledger=ledger,
    )
    if led is not None:
        rows.append(
            {
                "accounting_mode": "eur_ledger",
                "label": MODE_LABELS["eur_ledger"],
                "multiple": led["multiple"],
                "after_tax_value": led["after_tax_value"],
                "cagr": led["cagr"],
                "max_dd": led["max_dd"],
                "fees": led["fees"],
                "tax_paid": led["tax_paid"],
                "exit_tax": led["exit_tax"],
                "is_real_life": True,
                "note": f"reconcile gap €{led['reconcile_gap']:,.0f}",
            }
        )
    return pd.DataFrame(rows)
