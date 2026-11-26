"""Pre-registered signal families for Alpha Lab.

The functions here only produce target weights. Accounting, costs and tax stay
in `bot.backtest` (long/daily books) or the explicit overnight simulator.
"""

from __future__ import annotations

import math
from typing import Callable

import numpy as np
import pandas as pd

from bot import strategy

Selector = Callable[
    [strategy.Market, pd.Timestamp, list[str], set[str]], dict[str, float]
]


def _cap_weights(w: dict[str, float], cap: float) -> dict[str, float]:
    if not w:
        return {}
    out = dict(w)
    for _ in range(10):
        over = {k: v for k, v in out.items() if v > cap + 1e-12}
        if not over:
            break
        excess = sum(v - cap for v in over.values())
        for k in over:
            out[k] = cap
        room = {k: v for k, v in out.items() if v < cap - 1e-12}
        if not room:
            break
        tot = sum(room.values())
        for k in room:
            out[k] += excess * room[k] / tot
    total = sum(out.values())
    return {k: v / total for k, v in out.items() if v > 1e-12} if total > 0 else {}


def cross_asset_selector(params: dict) -> Selector:
    """Long-only dual momentum / trend across liquid ETFs.

    Params:
      universe: risky assets (defensive asset is separate)
      defensive: cash-like ETF, default BIL
      lookback, skip: momentum window in trading days (e.g. 252, 21 = 12-1)
      top_n: number of risky assets held
      trend_ma: price must be above this moving average (0 disables)
      abs_mom: "bil" (beat cash), "zero" (positive), or "none"
      weighting: "equal" or "invvol"
      vol_window: window for inverse-vol weights
      max_weight: per-asset cap after weighting
    """
    universe = tuple(params["universe"])
    defensive = params.get("defensive", "BIL")
    lookback = int(params.get("lookback", 252))
    skip = int(params.get("skip", 21))
    top_n = int(params.get("top_n", 1))
    trend_ma = int(params.get("trend_ma", 0) or 0)
    abs_mom = params.get("abs_mom", "bil")
    weighting = params.get("weighting", "equal")
    vol_window = int(params.get("vol_window", 60))
    max_weight = float(params.get("max_weight", 0.6))

    def select(
        mkt: strategy.Market, date: pd.Timestamp, held: list[str], banned: set[str]
    ) -> dict[str, float]:
        px = mkt.px
        present = [t for t in universe if t in px.columns]
        if not present:
            return {defensive: 1.0} if defensive in px.columns else {}
        mom = (
            px[present].shift(skip).loc[date] / px[present].shift(lookback).loc[date]
            - 1.0
        )
        mom = mom.replace([np.inf, -np.inf], np.nan).dropna()
        if mom.empty:
            return {defensive: 1.0} if defensive in px.columns else {}
        if abs_mom == "bil" and defensive in px.columns:
            b = px[defensive]
            bm = float(b.shift(skip).loc[date] / b.shift(lookback).loc[date] - 1.0)
        elif abs_mom == "zero":
            bm = 0.0
        else:
            bm = -np.inf
        keep: list[tuple[str, float]] = []
        for t, v in mom.items():
            if np.isfinite(bm) and v <= bm:
                continue
            if trend_ma:
                ma = px[t].rolling(trend_ma, min_periods=trend_ma).mean().loc[date]
                if not (pd.notna(ma) and px[t].loc[date] > ma):
                    continue
            keep.append((t, float(v)))
        if not keep:
            return {defensive: 1.0} if defensive in px.columns else {}
        keep.sort(key=lambda x: -x[1])
        picks = [t for t, _ in keep[:top_n]]
        if weighting == "invvol":
            ret = px[picks].pct_change(fill_method=None)
            sig = ret.rolling(vol_window, min_periods=max(10, vol_window // 3)).std()
            sigma = sig.loc[date].astype(float).replace(0.0, np.nan)
            sigma = sigma.fillna(sigma.median() if sigma.notna().any() else 0.2)
            raw = 1.0 / sigma
            w = raw / raw.sum()
            w = _cap_weights({k: float(v) for k, v in w.items()}, max_weight)
        else:
            w = {t: 1.0 / len(picks) for t in picks}
        return w

    return select


def dual_momentum_selector(params: dict) -> Selector:
    """Alias used by the long-horizon report."""
    return cross_asset_selector(params)


def trend_selector(params: dict) -> Selector:
    """Single-asset absolute trend: hold above the MA, otherwise defensive."""
    asset = params.get("asset", "SPY")
    ma = int(params.get("trend_ma", 200))
    defensive = params.get("defensive", "BIL")
    lookback = int(params.get("lookback", 0) or 0)
    skip = int(params.get("skip", 0) or 0)

    def select(
        mkt: strategy.Market, date: pd.Timestamp, held: list[str], banned: set[str]
    ) -> dict[str, float]:
        px = mkt.px
        if asset not in px.columns:
            return {}
        price = px[asset]
        ma_v = price.rolling(ma, min_periods=ma).mean().loc[date]
        on = pd.notna(ma_v) and price.loc[date] > ma_v
        if on and lookback:
            mom = price.shift(skip).loc[date] / price.shift(lookback).loc[date] - 1.0
            on = bool(pd.notna(mom) and mom > 0)
        if on:
            return {asset: 1.0}
        return {defensive: 1.0} if defensive in px.columns else {}

    return select


def _trading_index(mkt: strategy.Market) -> pd.DatetimeIndex:
    return pd.DatetimeIndex(mkt.dates)


def short_signal_selector(params: dict) -> Selector:
    """Daily/weekly cross-sectional reversal or momentum with a fixed hold.

    Params:
      assets: list of liquid ETFs to consider
      lookback: signal return window in trading days (1 = daily, 5 = weekly)
      threshold: signal trigger. Reversal: ret <= -threshold. Momentum: ret >= threshold.
      mode: "reversal" or "momentum"
      holding_days: number of trading days to hold after entry
      pick: "best" (top signal), "worst" (bottom), or "all"
      defensive: asset held when no signal; empty string means cash
    """
    assets = tuple(params.get("assets", ("SPY",)))
    lookback = int(params.get("lookback", 1))
    threshold = float(params.get("threshold", 0.0))
    mode = params.get("mode", "reversal")
    holding_days = int(params.get("holding_days", 1))
    pick = params.get("pick", "best")
    defensive = params.get("defensive", "")

    state: dict[str, object] = {"dates": None, "hold_until": -1, "asset": None}

    def select(
        mkt: strategy.Market, date: pd.Timestamp, held: list[str], banned: set[str]
    ) -> dict[str, float]:
        dates = state["dates"]
        if dates is None:
            dates = _trading_index(mkt)
            state["dates"] = dates
        i = int(dates.get_loc(date))
        current = state["asset"]
        if current and i < int(state["hold_until"]) and current in mkt.px.columns:
            return {str(current): 1.0}
        px = mkt.px
        present = [t for t in assets if t in px.columns]
        scores: list[tuple[str, float]] = []
        for t in present:
            r = float(px[t].loc[date] / px[t].shift(lookback).loc[date] - 1.0)
            if not np.isfinite(r):
                continue
            trigger = r <= -threshold if mode == "reversal" else r >= threshold
            if trigger:
                scores.append((t, r))
        if not scores:
            state["asset"] = None
            state["hold_until"] = -1
            return {defensive: 1.0} if defensive and defensive in px.columns else {}
        scores.sort(key=lambda x: x[1])
        if pick == "best":
            chosen = [scores[-1][0]]
        elif pick == "worst":
            chosen = [scores[0][0]]
        else:
            chosen = [t for t, _ in scores]
        state["asset"] = chosen[0] if len(chosen) == 1 else tuple(chosen)
        state["hold_until"] = i + holding_days
        if len(chosen) == 1:
            return {chosen[0]: 1.0}
        return {t: 1.0 / len(chosen) for t in chosen}

    return select


def overnight_signal(
    close: pd.DataFrame,
    signal: pd.DataFrame,
    assets: tuple[str, ...],
    mode: str = "reversal",
    threshold: float = 0.0,
    lookback: int = 1,
) -> pd.DataFrame:
    """Position matrix for close-to-open trades.

    `signal` is computed from information available at the prior close; the
    caller is responsible for shifting. A position of 1 means buy at today's
    close and sell at tomorrow's open. This function only builds the raw
    trigger; `overnight_backtest` applies the one-day information lag.
    """
    px = close[list(assets)]
    ret = px.pct_change(fill_method=None)
    if lookback > 1:
        sig = px / px.shift(lookback) - 1.0
    else:
        sig = ret
    trig = sig <= -threshold if mode == "reversal" else sig >= threshold
    pos = trig.astype(float)
    return pos
