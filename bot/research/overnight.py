"""Close-to-open simulator.

`bot.backtest` fills at the next open and holds through the session, so it
cannot isolate the overnight gap. This module does, with explicit daily
round-trip costs and French annual tax. Every position is closed at the next
open, so there is no unrealized tax at the end.

Timing: a trigger observed at close t-1 is executed at close t (MOC) and
exited at open t+1 (MOO). That is one full day of information lag, not a
same-close fill.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from bot.backtest import fr_rate

from .metrics import max_drawdown, summarize_equity


def lag_positions(trigger: pd.DataFrame, lag: int = 1) -> pd.DataFrame:
    """Move a close-based trigger forward so it is tradable at the next close."""
    return trigger.shift(lag).fillna(0.0)


def normalize_positions(pos: pd.DataFrame) -> pd.DataFrame:
    """Equal-weight active names; gross exposure <= 1."""
    gross = pos.sum(axis=1)
    w = pos.div(gross.replace(0.0, np.nan), axis=0).fillna(0.0)
    return w


def overnight_returns(close: pd.DataFrame, open_: pd.DataFrame) -> pd.DataFrame:
    """Total overnight return from today's close to tomorrow's open."""
    return open_.shift(-1) / close - 1.0


def overnight_backtest(
    close: pd.DataFrame,
    open_: pd.DataFrame,
    positions: pd.DataFrame,
    *,
    cost_bps_per_side: float = 5.0,
    tax: bool = True,
    tax_override: float = 0.0,
    name: str = "overnight",
) -> dict:
    """Simulate a close-to-open book with daily costs and annual French tax.

    `positions` must already be lagged: row t is the weight held from close t
    to open t+1. Weights are renormalised to gross <= 1.
    """
    cols = [c for c in positions.columns if c in close.columns]
    pos = normalize_positions(positions[cols].reindex(close.index).fillna(0.0))
    ret = overnight_returns(close[cols], open_[cols]).reindex(close.index)
    gross = (pos * ret).sum(axis=1)
    # Sell yesterday's position at today's open, buy today's at today's close.
    prior = pos.shift(1).fillna(0.0)
    cost = (prior.abs().sum(axis=1) + pos.abs().sum(axis=1)) * (cost_bps_per_side / 1e4)
    net = (gross - cost).fillna(0.0)

    equity = pd.Series(index=close.index, dtype=float)
    eq = 1.0
    loss_carry: list[list[float]] = []
    tax_paid = 0.0
    year_net = 0.0
    current_year = close.index[0].year
    tax_by_year: dict[int, float] = {}
    for d, r in net.items():
        if d.year != current_year:
            if tax:
                # settle the prior calendar year
                loss_carry = [x for x in loss_carry if d.year <= x[0]]
                net_year = year_net
                if net_year > 0:
                    for lot in loss_carry:
                        take = min(lot[1], net_year)
                        lot[1] -= take
                        net_year -= take
                        if net_year <= 1e-12:
                            break
                    loss_carry = [x for x in loss_carry if x[1] > 1e-12]
                    rate = tax_override or fr_rate(current_year)
                    t = max(0.0, net_year) * rate
                    eq -= t
                    tax_paid += t
                    tax_by_year[current_year] = t
                elif net_year < 0:
                    loss_carry.append([current_year + 10, -net_year])
            year_net = 0.0
            current_year = d.year
        eq *= 1.0 + r
        year_net += r
        equity.loc[d] = eq
    if tax:
        loss_carry = [x for x in loss_carry if current_year <= x[0]]
        net_year = year_net
        if net_year > 0:
            for lot in loss_carry:
                take = min(lot[1], net_year)
                lot[1] -= take
                net_year -= take
                if net_year <= 1e-12:
                    break
            loss_carry = [x for x in loss_carry if x[1] > 1e-12]
            rate = tax_override or fr_rate(current_year)
            t = max(0.0, net_year) * rate
            eq -= t
            tax_paid += t
            tax_by_year[current_year] = t
        equity.iloc[-1] = eq

    equity = equity.ffill().fillna(1.0)
    m = summarize_equity(equity)
    m.update(
        {
            "name": name,
            "cost_bps_per_side": cost_bps_per_side,
            "trades": int((pos.diff().abs().sum(axis=1) > 1e-12).sum()),
            "avg_gross": float(pos.sum(axis=1).mean()),
            "exposure_days": int((pos.sum(axis=1) > 1e-12).sum()),
            "tax_paid": float(tax_paid),
            "tax_by_year": tax_by_year,
        }
    )
    return {
        "equity": equity,
        "returns": net,
        "gross": gross,
        "cost": cost,
        "positions": pos,
        "metrics": m,
        "maxdd": max_drawdown(equity),
        "after_tax_multiple": float(equity.iloc[-1]),
    }
