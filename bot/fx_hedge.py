"""EUR/USD hedge primitives for a USD-denominated book.

The investor is EUR-based and holds USD assets. A hedge sells USD forward (or
buys EUR forward) for a fraction of the USD exposure. At the roll date `t` the
forward rate is fixed from the spot EUR-per-USD rate known at `t` and a constant
annual carry; at the next roll the contract settles against the then-current
spot. Gains and losses are therefore in EUR and belong in the EUR ledger's
capital-gains bucket.

Carry is deliberately simple: a constant annual `carry_bps` (USD rate minus EUR
rate, approximately) is embedded in the forward:
``F = S * (1 - carry_bps/1e4 * days/day_count)``. This is the documented
simplification; there are no rate-curve or cross-currency-basis data in the
committed cache. A positive carry makes a full hedge a drag when spot is flat.

No function here looks at a future FX value. `ratio_for_date` uses only the FX
history through the decision date; the ledger settles the forward at the next
month-end.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class FxHedgeConfig:
    """One hedge policy.

    mode="fixed": always hedge `ratio`.
    mode="threshold": hedge `high_ratio` when spot is below its trailing MA
    (USD weaker than usual / EUR stronger), otherwise `low_ratio`.
    """

    mode: str = "fixed"  # "fixed" | "threshold"
    ratio: float = 1.0
    low_ratio: float = 0.25
    high_ratio: float = 1.0
    ma_window: int = 200
    roll_bps: float = 5.0
    carry_bps: float = 200.0
    day_count: int = 360

    def __post_init__(self) -> None:
        if self.mode not in ("fixed", "threshold"):
            raise ValueError("mode must be 'fixed' or 'threshold'")
        for name in ("ratio", "low_ratio", "high_ratio"):
            value = float(getattr(self, name))
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be in [0, 1]")
        if self.mode == "threshold" and self.low_ratio > self.high_ratio:
            raise ValueError("low_ratio must be <= high_ratio")
        if self.ma_window < 2:
            raise ValueError("ma_window must be >= 2")
        if not np.isfinite(self.roll_bps) or self.roll_bps < 0:
            raise ValueError("roll_bps must be finite and >= 0")
        if not np.isfinite(self.carry_bps):
            raise ValueError("carry_bps must be finite")
        if self.day_count < 1:
            raise ValueError("day_count must be >= 1")


@dataclass
class HedgePeriod:
    start: pd.Timestamp
    expiry: pd.Timestamp
    ratio: float
    notional_usd: float
    spot_start: float
    forward: float
    roll_cost_eur: float


def month_end_dates(index: pd.DatetimeIndex) -> list[pd.Timestamp]:
    """Last trading day of every month, plus the final observation.

    The final observation is included even when it is not a month-end so an open
    hedge can be settled on the last day of the replay.
    """
    dates = pd.DatetimeIndex(index).sort_values()
    out: list[pd.Timestamp] = []
    for i, d in enumerate(dates):
        if i == len(dates) - 1:
            out.append(pd.Timestamp(d))
        else:
            n = dates[i + 1]
            if (d.year, d.month) != (n.year, n.month):
                out.append(pd.Timestamp(d))
    return out


def forward_rate(
    spot: float,
    carry_bps: float,
    days: int,
    day_count: int = 360,
) -> float:
    """EUR-per-USD forward with a constant annual carry."""
    if not np.isfinite(spot) or spot <= 0:
        raise ValueError("spot must be positive and finite")
    if days < 0 or day_count < 1:
        raise ValueError("days must be >= 0 and day_count >= 1")
    factor = 1.0 - float(carry_bps) / 1e4 * float(days) / float(day_count)
    if factor <= 0:
        raise ValueError("carry/day_count make the forward non-positive")
    return float(spot * factor)


def roll_cost_eur(notional_usd: float, spot: float, roll_bps: float) -> float:
    """Cost of one roll on the EUR notional."""
    if notional_usd < 0 or roll_bps < 0:
        raise ValueError("notional_usd and roll_bps must be non-negative")
    return float(notional_usd * spot * roll_bps / 1e4)


def hedge_notional(usd_gross: float, ratio: float) -> float:
    """USD notional to hedge; 0 and 1 are exact boundaries."""
    if usd_gross < 0:
        raise ValueError("usd_gross must be non-negative")
    if not 0.0 <= ratio <= 1.0:
        raise ValueError("ratio must be in [0, 1]")
    return float(usd_gross * ratio)


def hedge_pnl_eur(notional_usd: float, forward: float, spot_end: float) -> float:
    """EUR P&L of a USD-forward sale: notional * (F - S_end)."""
    if notional_usd < 0:
        raise ValueError("notional_usd must be non-negative")
    if forward <= 0 or spot_end <= 0:
        raise ValueError("FX rates must be positive")
    return float(notional_usd * (forward - spot_end))


def ratio_for_date(spot_history: pd.Series, cfg: FxHedgeConfig) -> float:
    """Hedge ratio at the decision date, using only history through it.

    `spot_history` is EUR-per-USD. In threshold mode a spot below its trailing
    MA means the USD is weaker than usual / EUR stronger, so the higher hedge
    ratio is used. Before a full MA is available the low ratio is used.
    """
    if cfg.mode == "fixed":
        return float(cfg.ratio)
    s = spot_history.astype(float).dropna()
    if len(s) < cfg.ma_window:
        return float(cfg.low_ratio)
    ma = float(s.iloc[-cfg.ma_window :].mean())
    if not np.isfinite(ma) or ma <= 0:
        return float(cfg.low_ratio)
    return float(cfg.high_ratio if float(s.iloc[-1]) < ma else cfg.low_ratio)


def open_period(
    start: pd.Timestamp,
    expiry: pd.Timestamp,
    usd_gross: float,
    spot: float,
    cfg: FxHedgeConfig,
    ratio: float | None = None,
) -> HedgePeriod | None:
    """Build one forward period, or None when the ratio/notional is zero."""
    r = (
        ratio_for_date(pd.Series([spot]), cfg)
        if ratio is None and cfg.mode == "threshold"
        else (float(cfg.ratio) if ratio is None else float(ratio))
    )
    notional = hedge_notional(usd_gross, r)
    if notional <= 0:
        return None
    days = int((pd.Timestamp(expiry) - pd.Timestamp(start)).days)
    if days <= 0:
        return None
    fwd = forward_rate(spot, cfg.carry_bps, days, cfg.day_count)
    cost = roll_cost_eur(notional, spot, cfg.roll_bps)
    return HedgePeriod(
        start=pd.Timestamp(start),
        expiry=pd.Timestamp(expiry),
        ratio=r,
        notional_usd=notional,
        spot_start=float(spot),
        forward=fwd,
        roll_cost_eur=cost,
    )
