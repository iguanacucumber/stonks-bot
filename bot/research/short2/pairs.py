"""Daily relative-value/pairs research for short-horizon v7.

Signals use closes through date t and positions earn the next session's
close-to-close return. No same-day fill. Costs, short borrow and annual French
PFU are explicit. The model is a research overlay, not an execution adapter.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from bot.backtest import fr_rate


@dataclass(frozen=True)
class PairConfig:
    lookback: int = 120
    entry_z: float = 2.0
    exit_z: float = 0.0
    max_hold: int = 10
    mode: str = "neutral"  # "neutral" or "long_only"
    cost_bps: float = 5.0
    borrow_bps: float = 500.0
    tax: bool = True
    tax_override: float = 0.0

    def __post_init__(self) -> None:
        if self.lookback < 10:
            raise ValueError("lookback must be >= 10")
        if self.entry_z <= 0 or self.exit_z < 0:
            raise ValueError("z thresholds must be non-negative")
        if self.max_hold < 1:
            raise ValueError("max_hold must be >= 1")
        if self.mode not in ("neutral", "long_only"):
            raise ValueError("mode must be neutral or long_only")


def spread_z(p1: pd.Series, p2: pd.Series, lookback: int) -> pd.Series:
    """Rolling z-score of log(p1/p2), using only data through each row."""
    spread = np.log(p1 / p2)
    mean = spread.rolling(lookback, min_periods=lookback).mean()
    std = spread.rolling(lookback, min_periods=lookback).std()
    return ((spread - mean) / std.replace(0.0, np.nan)).rename("z")


def pair_positions(
    p1: pd.Series,
    p2: pd.Series,
    cfg: PairConfig,
) -> pd.DataFrame:
    """Target weights at each close, applied to the next day's return.

    Neutral: long/short 0.5 each when the spread is stretched.
    Long-only: hold one cheap leg at 1.0, no short.
    """
    z = spread_z(p1, p2, cfg.lookback)
    idx = p1.index
    w1 = np.zeros(len(idx))
    w2 = np.zeros(len(idx))
    state = 0  # +1 long spread, -1 short spread, 0 flat
    held = 0
    for i, date in enumerate(idx):
        zi = z.iloc[i]
        if not np.isfinite(zi):
            w1[i] = w2[i] = 0.0
            continue
        if state == 0:
            if zi <= -cfg.entry_z:
                state = 1
                held = 0
            elif zi >= cfg.entry_z:
                state = -1
                held = 0
        elif state == 1:
            held += 1
            if zi >= -cfg.exit_z or held >= cfg.max_hold:
                state = 0
        elif state == -1:
            held += 1
            if zi <= cfg.exit_z or held >= cfg.max_hold:
                state = 0
        if state == 1:
            if cfg.mode == "neutral":
                w1[i], w2[i] = 0.5, -0.5
            else:
                w1[i], w2[i] = 1.0, 0.0
        elif state == -1:
            if cfg.mode == "neutral":
                w1[i], w2[i] = -0.5, 0.5
            else:
                w1[i], w2[i] = 0.0, 1.0
    return pd.DataFrame({"w1": w1, "w2": w2}, index=idx)


def backtest_pair(
    p1: pd.Series,
    p2: pd.Series,
    cfg: PairConfig,
    *,
    start: str | None = None,
    end: str | None = None,
) -> dict:
    """After-tax EUR-agnostic (USD) pairs path with costs and borrow."""
    px = pd.concat([p1.rename("p1"), p2.rename("p2")], axis=1).dropna()
    if start is not None:
        px = px.loc[px.index >= pd.Timestamp(start)]
    if end is not None:
        px = px.loc[px.index <= pd.Timestamp(end)]
    if len(px) < cfg.lookback + 5:
        raise ValueError("not enough observations for lookback")
    r = px.pct_change().fillna(0.0)
    w = pair_positions(px["p1"], px["p2"], cfg)
    # Position decided at close t earns close t -> close t+1.
    pos = w.shift(1).fillna(0.0)
    gross = (pos["w1"] * r["p1"] + pos["w2"] * r["p2"]).fillna(0.0)
    turnover = pos.diff().abs().sum(axis=1).fillna(pos.abs().sum(axis=1))
    cost = turnover * cfg.cost_bps / 1e4
    borrow = pos["w2"].abs() * cfg.borrow_bps / 1e4 / 252.0
    net = gross - cost - borrow

    eq = np.empty(len(net))
    equity = 1.0
    year = int(net.index[0].year)
    year_start = equity
    loss_carry: list[list[float]] = []
    tax_paid = 0.0
    for i, (d, x) in enumerate(net.items()):
        if int(d.year) != year:
            nr = equity / year_start - 1.0
            if cfg.tax:
                carry = [z for z in loss_carry if year <= z[0]]
                if nr > 0:
                    n = nr
                    for lot in carry:
                        take = min(lot[1], n)
                        lot[1] -= take
                        n -= take
                    loss_carry = [z for z in carry if z[1] > 1e-12]
                    rate = cfg.tax_override or fr_rate(year)
                    t = max(0.0, n) * rate
                    equity -= t * year_start
                    tax_paid += t * year_start
                elif nr < 0:
                    loss_carry.append([year + 10, -nr])
            year = int(d.year)
            year_start = equity
        equity *= 1.0 + float(x)
        eq[i] = equity
    if cfg.tax:
        nr = equity / year_start - 1.0
        carry = [z for z in loss_carry if year <= z[0]]
        if nr > 0:
            n = nr
            for lot in carry:
                take = min(lot[1], n)
                lot[1] -= take
                n -= take
            rate = cfg.tax_override or fr_rate(year)
            t = max(0.0, n) * rate
            equity -= t * year_start
            tax_paid += t * year_start
    eq[-1] = equity
    s = pd.Series(eq, index=net.index, name="equity")
    rr = s.pct_change().dropna()
    years = len(s) / 252.0
    return {
        "equity": s,
        "returns": rr,
        "weights": pos,
        "turnover_ann": float(turnover.mean() * 252.0),
        "trades": int((turnover > 1e-12).sum()),
        "tax_paid": float(tax_paid),
        "multiple": float(s.iloc[-1]),
        "cagr": float(s.iloc[-1] ** (1.0 / years) - 1.0),
        "maxdd": float((s / s.cummax() - 1.0).min()),
        "hit_rate": float((rr > 0).mean()),
    }
