"""Assurance-vie wrapper model: fees, 8-year tax, estate tax and break-even.

This is a planning model, not tax or legal advice. It applies an annual
unit-linked contract fee to a strategy NAV, adds per-trade contract fees,
then taxes the terminal gain under the French assurance-vie rules:

* before the 8th anniversary: 30% PFU on the gain;
* after 8 years: 7.5% income tax + 17.2% social = 24.7% on the gain above the
  annual €4,600 (single) / €9,200 (couple) abattement;
* death: 990 I gives €152,500 per beneficiary for premiums paid before 70,
  then 20% up to €700,000 taxable and 31.25% above; a spouse/PACS beneficiary
  is exempt. Premiums paid after 70 use the separate €30,500 global regime
  (757 B), where the taxable base is premiums, not accumulated capital.

The model does not assume the contract can hold a 2.5x leveraged strategy for
free. It applies the wrapper to the same gross strategy path and flags the
implementation constraint separately.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .estate import assurance_vie_tax, french_inheritance_tax

AV_AFTER_8_INCOME = 0.075
AV_SOCIAL = 0.172
AV_AFTER_8_RATE = AV_AFTER_8_INCOME + AV_SOCIAL
AV_BEFORE_8_RATE = 0.30
AV_ABATTEMENT_SINGLE = 4_600.0
AV_ABATTEMENT_COUPLE = 9_200.0
DEFAULT_TRANSACTION_BPS = 10.0


@dataclass(frozen=True)
class AVConfig:
    annual_fee: float = 0.006
    transaction_bps: float = DEFAULT_TRANSACTION_BPS
    abattement: float = AV_ABATTEMENT_SINGLE
    years: float = 12.0
    before_8: bool | None = None

    def __post_init__(self) -> None:
        if not 0.0 <= self.annual_fee < 0.10:
            raise ValueError("annual_fee must be in [0, 10%)")
        if self.transaction_bps < 0:
            raise ValueError("transaction_bps must be >= 0")
        if self.abattement < 0:
            raise ValueError("abattement must be >= 0")
        if self.years <= 0:
            raise ValueError("years must be > 0")


def av_terminal_tax(
    gain: float,
    years: float,
    abattement: float = AV_ABATTEMENT_SINGLE,
    before_8: bool | None = None,
) -> float:
    """French AV income/social tax on a terminal gain.

    ``before_8`` overrides the 8-year test for tests/sensitivity. After 8 years,
    the abattement applies once in this terminal model; a real contract can use
    it each year across partial withdrawals.
    """
    g = max(0.0, float(gain))
    early = (years < 8.0) if before_8 is None else bool(before_8)
    if early:
        return g * AV_BEFORE_8_RATE
    return max(0.0, g - float(abattement)) * AV_AFTER_8_RATE


def av_after_tax_multiple(
    pre_tax_multiple: float,
    years: float,
    abattement: float = AV_ABATTEMENT_SINGLE,
    before_8: bool | None = None,
    capital: float = 10_000.0,
) -> float:
    """After-tax AV multiple from a pre-tax wealth multiple."""
    m = max(0.0, float(pre_tax_multiple))
    gain = m - 1.0
    tax = av_terminal_tax(gain * float(capital), years, abattement, before_8)
    return 1.0 + gain - tax / float(capital)


def av_nav_path(
    equity: pd.Series,
    trades: pd.DataFrame,
    config: AVConfig,
) -> pd.Series:
    """Apply annual and per-trade AV fees to an untaxed strategy NAV.

    ``equity`` must be the strategy path **before French capital-gains tax**
    (the engine run with tax disabled), because the AV contract is the tax
    wrapper. The engine's own trading fees are already in ``equity``.
    """
    if len(equity) < 2:
        raise ValueError("equity path is empty")
    nav = equity.astype(float).copy()
    # Normalise to 1.0 without changing the strategy's return path.
    nav = nav / float(nav.iloc[0])
    daily_fee = (1.0 + config.annual_fee) ** (1.0 / 252.0) - 1.0
    nav = nav * (1.0 - daily_fee) ** np.arange(len(nav))
    if config.transaction_bps and len(trades):
        tr = trades.copy()
        tr["date"] = pd.to_datetime(tr["date"])
        notional = pd.to_numeric(tr["notional"], errors="coerce").fillna(0.0)
        fee = notional * config.transaction_bps / 1e4
        by_date = fee.groupby(tr["date"]).sum()
        eq = equity.astype(float)
        for date, amount in by_date.items():
            if date not in nav.index:
                continue
            base = float(eq.loc[date])
            if base > 0:
                factor = max(0.0, 1.0 - float(amount) / base)
                nav.loc[date:] = nav.loc[date:] * factor
    return nav


def av_result(
    equity: pd.Series,
    trades: pd.DataFrame,
    capital: float = 10_000.0,
    config: AVConfig | None = None,
) -> dict[str, float]:
    cfg = config or AVConfig()
    nav = av_nav_path(equity, trades, cfg)
    years = (len(nav) - 1) / 252.0 if cfg.years is None else float(cfg.years)
    pre = float(nav.iloc[-1])
    after = av_after_tax_multiple(
        pre, years, cfg.abattement, cfg.before_8, capital=capital
    )
    return {
        "pre_tax_multiple": pre,
        "after_tax_multiple": after,
        "after_tax_value": after * capital,
        "terminal_tax": (pre - after) * capital,
        "annual_fee": cfg.annual_fee,
        "transaction_bps": cfg.transaction_bps,
        "years": years,
        "abattement": cfg.abattement,
        "before_8": (years < 8.0) if cfg.before_8 is None else cfg.before_8,
    }


def annual_return(nav: pd.Series, year: int) -> float:
    e = nav.loc[str(year)]
    return float(e.iloc[-1] / e.iloc[0] - 1.0) if len(e) > 2 else float("nan")


def max_drawdown(nav: pd.Series) -> float:
    return float((nav / nav.cummax() - 1.0).min())


def break_even_fee(
    equity: pd.Series,
    trades: pd.DataFrame,
    cto_multiple: float,
    capital: float = 10_000.0,
    transaction_bps: float = DEFAULT_TRANSACTION_BPS,
    abattement: float = AV_ABATTEMENT_SINGLE,
    years: float = 12.0,
    hi: float = 0.099,
    tol: float = 1e-6,
) -> float | None:
    """Annual fee at which AV after-tax multiple equals the CTO multiple.

    Returns None when AV is already below CTO at zero fee, or still above CTO
    at ``hi`` (the bracket did not contain a crossing).
    """

    def f(fee: float) -> float:
        return (
            av_result(
                equity,
                trades,
                capital,
                AVConfig(
                    annual_fee=fee,
                    transaction_bps=transaction_bps,
                    abattement=abattement,
                    years=years,
                ),
            )["after_tax_multiple"]
            - cto_multiple
        )

    if f(0.0) <= 0:
        return None
    if f(hi) > 0:
        return None
    lo = 0.0
    for _ in range(80):
        mid = (lo + hi) / 2.0
        if f(mid) > 0:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol:
            break
    return (lo + hi) / 2.0


def av_estate_tax(
    capital_eur: float,
    heir: str = "child",
    beneficiaries: int = 1,
    premiums_before_70: bool = True,
    premiums_after_70: float | None = None,
) -> float:
    """Death tax for an AV contract, before/after the 70-year premium split.

    Before 70: 990 I on the transmitted capital (€152,500 per beneficiary,
    20% to €700k taxable, 31.25% above). After 70: the €30,500 global abatement
    applies to premiums only; accumulated gains are exempt, then French
    inheritance tax applies to the excess premium by heir.
    """
    if premiums_before_70:
        return assurance_vie_tax(
            capital_eur,
            heir=heir,
            beneficiaries=beneficiaries,
            premiums_before_70=True,
        )
    if premiums_after_70 is None:
        raise ValueError("premiums_after_70 is required for the after-70 regime")
    taxable = max(0.0, float(premiums_after_70) - 30_500.0)
    return french_inheritance_tax(taxable, heir=heir, heirs=beneficiaries)


def estate_comparison(capital_eur: float = 660_000.0, beneficiaries: int = 1) -> dict:
    """990 I AV estate tax versus CTO French inheritance tax at a point in time.

    CTO US estate tax is deliberately not added here; at €660k the France-US
    treaty pro-rata credit is zero in the merged estate model. See
    `bot/estate.py` for the full US/French combined calculation.
    """
    av_tax = av_estate_tax(
        capital_eur, heir="child", beneficiaries=beneficiaries, premiums_before_70=True
    )
    cto_tax = french_inheritance_tax(capital_eur, heir="child", heirs=beneficiaries)
    return {
        "capital_eur": float(capital_eur),
        "beneficiaries": int(beneficiaries),
        "av_990i_tax": float(av_tax),
        "cto_french_inheritance_tax": float(cto_tax),
        "av_advantage": float(cto_tax - av_tax),
    }
