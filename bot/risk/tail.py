"""Black-Scholes put-spread / collar sizing and a monthly-roll hedge study.

There are no option quotes in the committed cache. Everything here is an
explicit model with stated assumptions (flat 30% IV, 4% rate, no dividends).
It must never be presented as broker-accounted P&L.

The hedge is rolled on the last trading day of every `tenor_months` period. The
premium leaves a separate hedge cash account at the roll; the payoff arrives at
the next roll date. The adjusted equity path is main equity plus cumulative hedge
cash, so the drag is visible every day and the payoff lands at expiry.

Taxes are not applied inside :func:`simulate_hedge`. :func:`after_tax_delta`
applies the pre-registered conservative treatment: 31.4% on positive payoffs,
no deduction for premiums.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

_SQRT2 = math.sqrt(2.0)


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / _SQRT2))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _d1_d2(
    spot: float, strike: float, t: float, rate: float, vol: float, dividend: float
) -> tuple[float, float]:
    if t <= 0 or vol <= 0:
        raise ValueError("t and vol must be positive for d1/d2")
    vt = vol * math.sqrt(t)
    d1 = (math.log(spot / strike) + (rate - dividend + 0.5 * vol * vol) * t) / vt
    return d1, d1 - vt


def black_scholes_call(
    spot: float,
    strike: float,
    t: float,
    rate: float,
    vol: float,
    dividend: float = 0.0,
) -> float:
    """European call value. At t=0 this is intrinsic value."""
    if spot <= 0 or strike <= 0:
        raise ValueError("spot and strike must be positive")
    if t < 0 or vol < 0:
        raise ValueError("t and vol must be non-negative")
    if t == 0:
        return max(spot - strike, 0.0)
    if vol == 0:
        return max(spot * math.exp(-dividend * t) - strike * math.exp(-rate * t), 0.0)
    d1, d2 = _d1_d2(spot, strike, t, rate, vol, dividend)
    return spot * math.exp(-dividend * t) * _norm_cdf(d1) - strike * math.exp(
        -rate * t
    ) * _norm_cdf(d2)


def black_scholes_put(
    spot: float,
    strike: float,
    t: float,
    rate: float,
    vol: float,
    dividend: float = 0.0,
) -> float:
    """European put value. At t=0 this is intrinsic value."""
    if spot <= 0 or strike <= 0:
        raise ValueError("spot and strike must be positive")
    if t < 0 or vol < 0:
        raise ValueError("t and vol must be non-negative")
    if t == 0:
        return max(strike - spot, 0.0)
    if vol == 0:
        return max(strike * math.exp(-rate * t) - spot * math.exp(-dividend * t), 0.0)
    d1, d2 = _d1_d2(spot, strike, t, rate, vol, dividend)
    return strike * math.exp(-rate * t) * _norm_cdf(-d2) - spot * math.exp(
        -dividend * t
    ) * _norm_cdf(-d1)


def put_spread_cost(
    long_strike: float,
    short_strike: float,
    t: float,
    rate: float,
    vol: float,
    spot: float = 1.0,
    dividend: float = 0.0,
) -> float:
    """Cost per $1 of underlying notional for a long put / short lower put.

    `long_strike` must be above `short_strike`. The result is a positive debit
    when the spread has value; it is not clamped, so a mis-specified pair can
    raise or return a credit and the caller must check.
    """
    if long_strike <= short_strike:
        raise ValueError("long_strike must be above short_strike")
    long_put = black_scholes_put(spot, long_strike, t, rate, vol, dividend)
    short_put = black_scholes_put(spot, short_strike, t, rate, vol, dividend)
    return float(long_put - short_put)


def collar_cost(
    put_strike: float,
    call_strike: float,
    t: float,
    rate: float,
    vol: float,
    spot: float = 1.0,
    dividend: float = 0.0,
) -> float:
    """Net debit of a long put / short call collar, per $1 notional."""
    if put_strike >= call_strike:
        raise ValueError("put_strike must be below call_strike")
    put = black_scholes_put(spot, put_strike, t, rate, vol, dividend)
    call = black_scholes_call(spot, call_strike, t, rate, vol, dividend)
    return float(put - call)


def size_to_budget(
    nav: float,
    budget_pct: float,
    cost_per_unit: float,
    max_notional: float | None = None,
) -> float:
    """Notional such that premium is `budget_pct * nav`, capped by max_notional.

    A zero/negative cost returns the maximum notional (a credit-funded hedge has
    no premium budget constraint).
    """
    if nav <= 0:
        return 0.0
    if not np.isfinite(budget_pct) or budget_pct < 0:
        raise ValueError("budget_pct must be finite and >= 0")
    if cost_per_unit <= 0:
        return float(max_notional) if max_notional is not None else 0.0
    notional = nav * budget_pct / cost_per_unit
    if max_notional is not None:
        notional = min(notional, float(max_notional))
    return float(max(notional, 0.0))


@dataclass(frozen=True)
class HedgeConfig:
    """Model assumptions for one rolling hedge."""

    kind: str = "put_spread"  # "put_spread" | "collar"
    tenor_months: int = 3
    long_strike: float = 0.90
    short_strike: float = 0.70
    call_strike: float = 1.15
    vol: float = 0.30
    rate: float = 0.04
    budget_pct: float | None = None  # annual premium / NAV; None = full notional
    max_notional_multiple: float = 2.5
    tax_rate: float = 0.314

    def __post_init__(self) -> None:
        if self.kind not in ("put_spread", "collar"):
            raise ValueError("kind must be put_spread or collar")
        if self.tenor_months not in (1, 3, 6, 12):
            raise ValueError("tenor_months must be 1, 3, 6 or 12")
        if not 0.0 < self.long_strike < 1.5:
            raise ValueError("long_strike must be a positive fraction")
        if self.kind == "put_spread" and not (
            0.0 < self.short_strike < self.long_strike
        ):
            raise ValueError("short_strike must be below long_strike")
        if self.kind == "collar" and not self.long_strike < self.call_strike:
            raise ValueError("call_strike must be above long_strike")
        if self.vol <= 0 or self.rate < 0:
            raise ValueError("vol must be positive and rate non-negative")
        if self.budget_pct is not None and self.budget_pct < 0:
            raise ValueError("budget_pct must be non-negative")
        if self.max_notional_multiple <= 0:
            raise ValueError("max_notional_multiple must be positive")
        if not 0.0 <= self.tax_rate < 1.0:
            raise ValueError("tax_rate must be in [0, 1)")


def _roll_dates(index: pd.DatetimeIndex, tenor_months: int) -> pd.DatetimeIndex:
    if len(index) == 0:
        return index
    s = pd.Series(np.arange(len(index)), index=index)
    month_end = s.groupby([index.year, index.month]).max().to_numpy()
    month_end = pd.DatetimeIndex(index[np.sort(month_end)])
    rolls = month_end[tenor_months - 1 :: tenor_months]
    if len(rolls) == 0 or rolls[-1] < index[-1]:
        rolls = rolls.append(pd.DatetimeIndex([index[-1]]))
    return rolls


@dataclass
class HedgeResult:
    equity: pd.Series
    rolls: pd.DataFrame
    premium: float
    payoff: float


def simulate_hedge(
    equity: pd.Series,
    config: HedgeConfig,
    underlying: pd.Series | None = None,
) -> HedgeResult:
    """Roll a modelled hedge against `equity` and return the adjusted path.

    `underlying` is the hedged instrument (e.g. SMH). If omitted, the book's own
    equity index is used, which is a NAV-put approximation. Notional is sized from
    the main equity at each roll; premium and payoff are in the same units.
    """
    eq = equity.astype(float).dropna()
    if len(eq) < 3:
        raise ValueError("equity needs at least three observations")
    if (eq <= 0).any():
        raise ValueError("equity must be positive")
    und = (
        eq.copy() if underlying is None else underlying.astype(float).reindex(eq.index)
    )
    und = und.ffill().fillna(eq)
    rolls = _roll_dates(pd.DatetimeIndex(eq.index), config.tenor_months)
    if len(rolls) < 2:
        raise ValueError("not enough roll dates for the requested tenor")

    cash = pd.Series(0.0, index=eq.index, dtype=float)
    rows: list[dict] = []
    t_years = config.tenor_months / 12.0
    for t0, t1 in zip(rolls[:-1], rolls[1:]):
        spot0 = float(und.loc[t0])
        spot1 = float(und.loc[t1])
        if not np.isfinite(spot0) or spot0 <= 0 or not np.isfinite(spot1):
            continue
        nav0 = float(eq.loc[t0])
        if config.kind == "put_spread":
            cost = put_spread_cost(
                config.long_strike,
                config.short_strike,
                t_years,
                config.rate,
                config.vol,
            )
        else:
            cost = collar_cost(
                config.long_strike,
                config.call_strike,
                t_years,
                config.rate,
                config.vol,
            )
        max_notional = config.max_notional_multiple * nav0
        if config.budget_pct is None:
            notional = max_notional
        else:
            per_roll_budget = nav0 * config.budget_pct * t_years
            notional = min(max_notional, per_roll_budget / max(cost, 1e-12))
        premium = notional * cost
        ratio = spot1 / spot0
        if config.kind == "put_spread":
            payoff_frac = max(config.long_strike - ratio, 0.0) - max(
                config.short_strike - ratio, 0.0
            )
        else:
            payoff_frac = max(config.long_strike - ratio, 0.0) - max(
                ratio - config.call_strike, 0.0
            )
        payoff = notional * payoff_frac
        cash.loc[t0] -= premium
        cash.loc[t1] += payoff
        rows.append(
            {
                "roll": t0,
                "expiry": t1,
                "spot0": spot0,
                "spot1": spot1,
                "cost_per_unit": cost,
                "notional": notional,
                "premium": premium,
                "payoff": payoff,
                "pnl": payoff - premium,
            }
        )
    roll_df = pd.DataFrame(rows)
    if roll_df.empty:
        raise ValueError("no hedge rolls could be priced")
    adjusted = eq + cash.cumsum()
    return HedgeResult(
        equity=adjusted,
        rolls=roll_df,
        premium=float(roll_df["premium"].sum()),
        payoff=float(roll_df["payoff"].sum()),
    )


def after_tax_delta(result: HedgeResult, tax_rate: float = 0.314) -> float:
    """Conservative after-tax hedge P&L in equity units.

    Premiums are not deducted from taxable gains; positive payoffs are taxed.
    This deliberately understates the hedge's tax benefit.
    """
    if not 0.0 <= tax_rate < 1.0:
        raise ValueError("tax_rate must be in [0, 1)")
    positive_payoff = float(result.rolls["payoff"].clip(lower=0.0).sum())
    return float(-result.premium + positive_payoff * (1.0 - tax_rate))
