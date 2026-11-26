"""Index-futures leverage overlay and roll/tax arithmetic.

This is a research model, not an execution adapter. It compares a continuously
rebalanced index-futures position with an ETF+margin implementation of the same
index exposure.

Futures economics used here
---------------------------
A fully collateralised equity-index futures position earns the index total
return minus the implied financing rate, plus interest on the cash collateral:

    r_position = exposure * (r_index_total - r_implied) + r_cash

This is the no-arbitrage relation for a cash-and-carry index future; dividends
are embedded in the futures basis. The model does not add dividends separately.

French tax assumption (documented, not tax advice)
--------------------------------------------------
Retail CTO futures gains are treated as capital gains under the French PFU
(30% through 2025, 31.4% from 2026), netted annually, with a ten-year loss
carry. The model taxes each calendar year's net EUR gain and carries losses;
there is no separate exit tax because variation margin is marked annually.
This is conservative versus deferring all gains to liquidation.

Roll assumption
---------------
Yahoo `ES=F`/`NQ=F` are continuous front-month series, not individual contracts.
The model charges a quarterly roll cost at the last trading day of Mar/Jun/Sep/
Dec, plus a small commission/slippage. It never uses a future roll date to set
an earlier position.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from bot.backtest import fr_rate


@dataclass(frozen=True)
class FuturesConfig:
    target_leverage: float = 2.5
    implied_financing_bps: float = 450.0
    cash_rate_bps: float = 0.0
    roll_bps: float = 1.0
    commission_bps: float = 0.5
    rebalance_bps: float = 0.5
    loss_carry_years: int = 10
    tax_override: float = 0.0

    def __post_init__(self) -> None:
        if self.target_leverage <= 0:
            raise ValueError("target_leverage must be > 0")
        for name in (
            "implied_financing_bps",
            "cash_rate_bps",
            "roll_bps",
            "commission_bps",
            "rebalance_bps",
        ):
            if float(getattr(self, name)) < 0:
                raise ValueError(f"{name} must be >= 0")
        if not 0.0 <= float(self.tax_override) < 1.0:
            raise ValueError("tax_override must be in [0, 1)")

    def scaled_costs(self, multiplier: float) -> "FuturesConfig":
        return FuturesConfig(
            target_leverage=self.target_leverage,
            implied_financing_bps=self.implied_financing_bps,
            cash_rate_bps=self.cash_rate_bps,
            roll_bps=self.roll_bps * multiplier,
            commission_bps=self.commission_bps * multiplier,
            rebalance_bps=self.rebalance_bps * multiplier,
            loss_carry_years=self.loss_carry_years,
            tax_override=self.tax_override,
        )


# CME contract metadata for documentation and capacity checks. The model uses a
# continuous notional, so these do not change returns; they size the account at
# which one contract is a sensible unit.
CONTRACTS = {
    "ES": {"name": "E-mini S&P 500", "multiplier": 50.0, "tick": 0.25, "fee_usd": 2.5},
    "NQ": {
        "name": "E-mini Nasdaq-100",
        "multiplier": 20.0,
        "tick": 0.25,
        "fee_usd": 2.5,
    },
    "MES": {
        "name": "Micro E-mini S&P 500",
        "multiplier": 5.0,
        "tick": 0.25,
        "fee_usd": 1.25,
    },
    "MNQ": {
        "name": "Micro E-mini Nasdaq-100",
        "multiplier": 2.0,
        "tick": 0.25,
        "fee_usd": 1.25,
    },
}


def roll_dates(dates: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """Last trading session of Mar/Jun/Sep/Dec in the supplied calendar."""
    ds = pd.DatetimeIndex(pd.to_datetime(dates)).normalize().unique().sort_values()
    out = []
    for year in sorted(set(ds.year)):
        for month in (3, 6, 9, 12):
            part = ds[(ds.year == year) & (ds.month == month)]
            if len(part):
                out.append(part.max())
    return pd.DatetimeIndex(out)


def is_roll_date(date: pd.Timestamp, dates: pd.DatetimeIndex) -> bool:
    return pd.Timestamp(date).normalize() in set(roll_dates(dates))


def implied_financing(
    futures: pd.Series, spot: pd.Series, days_to_expiry: int = 90
) -> pd.Series:
    """Annualised simple basis (F/S - 1) * 365/days.

    Only a diagnostic: with a continuous front-month series and no contract
    expiry metadata this is noisy and includes dividend and roll effects.
    """
    a, b = futures.align(spot, join="inner")
    if days_to_expiry <= 0:
        raise ValueError("days_to_expiry must be positive")
    return (a / b - 1.0) * (365.0 / days_to_expiry)


def _tax_year(
    net_return: float, year: int, loss_carry: list[list[float]], cfg: FuturesConfig
) -> tuple[float, list[list[float]]]:
    """Tax one year's net return, mutate a copy of the loss carry."""
    carry = [list(x) for x in loss_carry]
    carry = [x for x in carry if year <= x[0]]
    if net_return > 0:
        net = net_return
        for lot in carry:
            take = min(lot[1], net)
            lot[1] -= take
            net -= take
            if net <= 1e-12:
                break
        carry = [x for x in carry if x[1] > 1e-12]
        rate = cfg.tax_override or fr_rate(year)
        return max(0.0, net) * rate, carry
    if net_return < 0:
        carry.append([year + cfg.loss_carry_years, -net_return])
    return 0.0, carry


def simulate_futures(
    index_returns: pd.Series,
    *,
    config: FuturesConfig | None = None,
    fx_eur_per_usd: pd.Series | None = None,
    dates: pd.DatetimeIndex | None = None,
) -> dict:
    """Monthly-rebalanced futures overlay on an index total-return series.

    `index_returns` are daily total returns in USD. If `fx_eur_per_usd` is
    supplied, each day's return is translated to EUR before leverage; this is
    the EUR-investor view. The position is set at the prior close and earns the
    next day's return, so there is no same-day look-ahead.
    """
    cfg = config or FuturesConfig()
    idx = (
        pd.DatetimeIndex(index_returns.index)
        if dates is None
        else pd.DatetimeIndex(dates)
    )
    r = index_returns.reindex(idx).fillna(0.0).astype(float)
    if fx_eur_per_usd is not None:
        fx = fx_eur_per_usd.reindex(idx).ffill()
        fx_ret = fx.pct_change().fillna(0.0)
        r = (1.0 + r) * (1.0 + fx_ret) - 1.0
    if len(r) < 2:
        raise ValueError("not enough observations")

    equity = 1.0
    notional = cfg.target_leverage * equity
    eq = np.empty(len(r))
    exp = np.empty(len(r))
    costs = np.empty(len(r))
    tax_paid = 0.0
    loss_carry: list[list[float]] = []
    year = int(idx[0].year)
    year_start_equity = equity
    fin_daily = cfg.implied_financing_bps / 1e4 / 252.0
    cash_daily = cfg.cash_rate_bps / 1e4 / 252.0
    rolls = set(roll_dates(idx))

    for i, d in enumerate(idx):
        # Existing notional earns today's return. Cash collateral earns cash.
        lev = notional / equity if equity > 0 else 0.0
        gross = lev * (float(r.iloc[i]) - fin_daily) + cash_daily
        equity *= 1.0 + gross
        cost = 0.0
        if d in rolls:
            cost = (cfg.roll_bps + cfg.commission_bps) / 1e4 * lev
            equity *= 1.0 - cost
        # Month-end reset to the target notional; charge a small rebalance cost.
        if i + 1 < len(r) and d.month != idx[i + 1].month:
            old = notional
            notional = cfg.target_leverage * equity
            cost += cfg.rebalance_bps / 1e4 * abs(notional - old) / max(equity, 1e-12)
            equity *= 1.0 - cost
        if int(d.year) != year:
            net = equity / year_start_equity - 1.0
            tax, loss_carry = _tax_year(net, year, loss_carry, cfg)
            equity -= tax * year_start_equity
            tax_paid += tax * year_start_equity
            year_start_equity = equity
            year = int(d.year)
        eq[i] = equity
        exp[i] = notional / equity if equity > 0 else 0.0
        costs[i] = cost

    # Final calendar year settlement.
    net = equity / year_start_equity - 1.0
    tax, loss_carry = _tax_year(net, year, loss_carry, cfg)
    equity -= tax * year_start_equity
    tax_paid += tax * year_start_equity
    eq[-1] = equity

    equity_s = pd.Series(eq, index=idx, name="equity_eur")
    ret = equity_s.pct_change().dropna()
    peak = equity_s.cummax()
    years = len(equity_s) / 252.0
    return {
        "equity": equity_s,
        "returns": ret,
        "exposure": pd.Series(exp, index=idx, name="exposure"),
        "costs": pd.Series(costs, index=idx, name="cost"),
        "tax_paid": float(tax_paid),
        "multiple": float(equity_s.iloc[-1]),
        "cagr": float(equity_s.iloc[-1] ** (1.0 / years) - 1.0),
        "maxdd": float((equity_s / peak - 1.0).min()),
        "config": cfg,
    }
