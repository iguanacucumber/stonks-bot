"""Systematic monthly covered calls on the SMH sleeve (Black-Scholes model).

There are no option quotes in the committed cache. This is an explicit model, not
broker-accounted P&L: flat IV, 4% rate, no dividends, European cash settlement.
The short call is written at the last trading day of a month from that close's spot,
expires at the next month close, and is marked daily with the same Black-Scholes
model. Assignment is represented by the intrinsic payoff at expiry; the underlying
shares are not physically removed (a documented optimistic tax simplification).

Coverage modes:

- ``core``: calls only against the actual SMH position in ``res.weights``. This is
  the implementable covered-call case; months when the book rotates into the stock
  sleeve produce no calls.
- ``book``: calls against gross book exposure using SMH as a proxy. Label it as a
  model variant: it is not a true covered call when the book holds a stock basket.

Contract granularity is fractional by default for $10k comparability. Set
``fractional=False`` to model whole 100-share contracts; at a $10k account one SMH
contract may exceed the sleeve, in which case no call is written.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .fx_hedge import month_end_dates
from .risk.tail import black_scholes_call

TOL = 1e-12


@dataclass(frozen=True)
class CoveredCallConfig:
    coverage: str = "core"  # "core" | "book"
    underlying: str = "SMH"
    capital: float = 10_000.0
    strike_otm: float = 0.05
    vol: float = 0.30
    rate: float = 0.04
    tenor_months: int = 1
    dividend_yield: float = 0.0
    spread_pct: float = 0.05  # full bid/ask as a fraction of mid; seller gets bid
    commission_per_contract: float = 0.65
    multiplier: int = 100
    fractional: bool = True

    def __post_init__(self) -> None:
        if self.coverage not in ("core", "book"):
            raise ValueError("coverage must be 'core' or 'book'")
        if self.strike_otm < 0 or self.strike_otm >= 1:
            raise ValueError("strike_otm must be in [0, 1)")
        if self.vol <= 0:
            raise ValueError("vol must be positive")
        if self.capital <= 0:
            raise ValueError("capital must be positive")
        if self.rate < 0:
            raise ValueError("rate must be non-negative")
        if self.tenor_months != 1:
            raise ValueError("this study is 1-month-only")
        if self.spread_pct < 0 or self.spread_pct >= 2:
            raise ValueError("spread_pct must be in [0, 2)")
        if self.commission_per_contract < 0:
            raise ValueError("commission_per_contract must be non-negative")
        if self.multiplier <= 0:
            raise ValueError("multiplier must be positive")


@dataclass
class CoveredCallResult:
    config: CoveredCallConfig
    rolls: pd.DataFrame
    daily: pd.DataFrame
    equity_adjusted: pd.Series
    premium: float
    payoff: float
    net_income: float
    capped_upside: float
    assigned_rolls: int
    uncovered_rolls: int
    avg_notional: float

    @property
    def income_yield_on_notional(self) -> float:
        return self.net_income / self.avg_notional if self.avg_notional > 0 else 0.0


def _dates_in(dates: pd.DatetimeIndex, start: pd.Timestamp, end: pd.Timestamp):
    return dates[(dates >= start) & (dates <= end)]


def build_covered_calls(
    res,
    panel: pd.DataFrame,
    config: CoveredCallConfig | None = None,
) -> CoveredCallResult:
    """Build a monthly covered-call schedule from a backtest ``Result``.

    Decision and spot are taken at the month-end close ``t0``; premium is credited
    at that close. Expiry is the next month-end close ``t1``. Nothing after ``t0``
    is used to choose strike, size or premium.
    """
    cfg = config or CoveredCallConfig()
    eq = res.equity.astype(float).dropna()
    if len(eq) < 3:
        raise ValueError("equity needs at least three observations")
    weights = res.weights.reindex(eq.index).fillna(0.0)
    px = (
        panel[["date", "ticker", "adjclose"]]
        .pivot(index="date", columns="ticker", values="adjclose")
        .sort_index()
        .ffill()
    )
    if cfg.underlying not in px.columns:
        raise ValueError(f"underlying {cfg.underlying} not in panel")
    und = px[cfg.underlying].reindex(eq.index).ffill()

    dates = pd.DatetimeIndex(eq.index)
    roll_dates = month_end_dates(dates)
    cols = [
        "cash_native",
        "liability_native",
        "pnl_native",
        "premium_native",
        "payoff_native",
        "open_premium_native",
    ]
    daily = pd.DataFrame(0.0, index=dates, columns=cols)
    rows: list[dict] = []

    for t0, t1 in zip(roll_dates[:-1], roll_dates[1:]):
        spot0 = float(und.loc[t0])
        spot1 = float(und.loc[t1])
        if not np.isfinite(spot0) or spot0 <= 0 or not np.isfinite(spot1):
            continue
        nav0 = float(eq.loc[t0])
        row = weights.loc[t0]
        smh_weight = float(row.get(cfg.underlying, 0.0))
        if cfg.coverage == "core":
            notional = max(0.0, smh_weight) * nav0 * cfg.capital
            covered_fraction = 1.0 if notional > 0 else 0.0
        else:
            gross = float(sum(abs(float(w)) for t, w in row.items() if t != "CASH"))
            notional = gross * nav0 * cfg.capital
            covered_fraction = (
                max(0.0, smh_weight) * nav0 * cfg.capital / notional
                if notional > 0
                else 0.0
            )
        if notional <= 0:
            continue
        raw_contracts = notional / (spot0 * cfg.multiplier)
        contracts = (
            raw_contracts if cfg.fractional else float(math.floor(raw_contracts))
        )
        units = contracts * cfg.multiplier
        if units <= TOL:
            continue

        strike = spot0 * (1.0 + cfg.strike_otm)
        t_years = cfg.tenor_months / 12.0
        mid = black_scholes_call(
            spot0, strike, t_years, cfg.rate, cfg.vol, cfg.dividend_yield
        )
        # Seller receives the bid; a full spread of 5% means 2.5% below mid.
        bid = mid * (1.0 - cfg.spread_pct / 2.0)
        commission = contracts * cfg.commission_per_contract
        premium = units * bid - commission
        payoff = units * max(spot1 - strike, 0.0)
        pnl = premium - payoff
        assigned = payoff > TOL

        # Daily short-call mark. At t0 this is the mid, so the immediate equity
        # adjustment is the bid/ask plus commission; at expiry it is intrinsic.
        # Mark the short call through the close before expiry. At t1 the option
        # has settled; cash already carries the payoff, so liability is zero.
        for d in _dates_in(dates, t0, t1):
            if d == t1:
                continue
            tau = max(0.0, (t1 - d).days / 365.0)
            value = black_scholes_call(
                float(und.loc[d]),
                strike,
                tau,
                cfg.rate,
                cfg.vol,
                cfg.dividend_yield,
            )
            daily.loc[d, "liability_native"] += units * value

        daily.loc[t0, "cash_native"] += premium
        daily.loc[t0, "premium_native"] += premium
        daily.loc[t0, "open_premium_native"] += premium
        # Close the old open-premium marker at expiry, then the next loop may add
        # a fresh roll on the same date.
        daily.loc[t1, "open_premium_native"] -= premium
        daily.loc[t1, "cash_native"] -= payoff
        daily.loc[t1, "payoff_native"] += payoff
        daily.loc[t1, "pnl_native"] += pnl

        rows.append(
            {
                "decision": t0,
                "expiry": t1,
                "spot0": spot0,
                "spot1": spot1,
                "strike": strike,
                "strike_otm": cfg.strike_otm,
                "vol": cfg.vol,
                "contracts": contracts,
                "units": units,
                "notional": notional,
                "covered_fraction": covered_fraction,
                "mid": mid,
                "bid": bid,
                "premium": premium,
                "commission": commission,
                "payoff": payoff,
                "pnl": pnl,
                "assigned": bool(assigned),
                "uncovered": bool(covered_fraction < 0.999),
            }
        )

    roll_df = pd.DataFrame(rows)
    if roll_df.empty:
        empty_daily = daily
        return CoveredCallResult(
            config=cfg,
            rolls=roll_df,
            daily=empty_daily,
            equity_adjusted=eq.copy(),
            premium=0.0,
            payoff=0.0,
            net_income=0.0,
            capped_upside=0.0,
            assigned_rolls=0,
            uncovered_rolls=0,
            avg_notional=0.0,
        )

    adjusted = (
        eq * cfg.capital + daily["cash_native"].cumsum() - daily["liability_native"]
    )
    return CoveredCallResult(
        config=cfg,
        rolls=roll_df,
        daily=daily,
        equity_adjusted=adjusted,
        premium=float(roll_df["premium"].sum()),
        payoff=float(roll_df["payoff"].sum()),
        net_income=float(roll_df["pnl"].sum()),
        capped_upside=float(roll_df["payoff"].sum()),
        assigned_rolls=int(roll_df["assigned"].sum()),
        uncovered_rolls=int(roll_df["uncovered"].sum()),
        avg_notional=float(roll_df["notional"].mean()),
    )


def price_at(
    spot: float, strike: float, t: float, vol: float, rate: float = 0.04
) -> float:
    """Small boundary helper used by tests and report labels."""
    return black_scholes_call(spot, strike, t, rate, vol)
