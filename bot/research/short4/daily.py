"""Daily sector reversal, calendar/seasonality and volatility breakout.

Signals use closes/highs through `t`; entries are at the next open. The calendar
masks use only the date (known before the open). Costs are one-way bps; the
return series already subtracts the round trip as weights change. French PFU is
the year-dependent schedule in `bot.backtest.fr_rate`, applied by
`after_tax_series` from the v8 short-horizon work.

No function here fetches data or touches the network.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from bot.research.short3.daily import after_tax_series, daily_bars

SECTOR_ETFS = (
    "XLK",
    "XLE",
    "XLF",
    "XLV",
    "XLI",
    "XLP",
    "XLU",
    "XLB",
    "XLY",
    "XLRE",
    "XLC",
)


def sector_closes(start: str = "2015-01-01") -> dict[str, pd.Series]:
    out = {}
    for sym in SECTOR_ETFS + ("SPY",):
        s = daily_bars(sym)["adjclose"].dropna()
        s = s[s.index >= pd.Timestamp(start)]
        out[sym] = s
    return out


def _returns(px: pd.DataFrame) -> pd.DataFrame:
    return px.pct_change(fill_method=None).fillna(0.0)


def sector_reversal_returns(
    closes: dict[str, pd.Series],
    *,
    lookback: int = 5,
    hold: int = 5,
    top_n: int = 3,
    mode: str = "long_only",
    cost_bps: float = 5.0,
    borrow_bps: float = 500.0,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Cross-sectional sector reversal; weights are for the next session.

    Long the `top_n` worst lookback returns; `neutral` also shorts the `top_n`
    best. Rebalance every `hold` sessions at the close. Returns
    `(net, turnover, active)`.
    """
    if mode not in ("long_only", "neutral"):
        raise ValueError("mode must be long_only or neutral")
    if lookback < 1 or hold < 1 or top_n < 1:
        raise ValueError("lookback, hold and top_n must be >= 1")
    px = pd.DataFrame(closes).sort_index().dropna()
    r = _returns(px)
    idx = px.index
    weights = pd.DataFrame(0.0, index=idx, columns=px.columns)
    for i in range(lookback, len(idx) - 1, hold):
        trailing = px.iloc[i] / px.iloc[i - lookback] - 1.0
        worst = trailing.nsmallest(top_n).index
        best = trailing.nlargest(top_n).index
        end = min(i + 1 + hold, len(idx))
        if mode == "long_only":
            weights.iloc[i + 1 : end, :] = 0.0
            weights.iloc[i + 1 : end, weights.columns.get_indexer(worst)] = 1.0 / top_n
        else:
            weights.iloc[i + 1 : end, :] = 0.0
            weights.iloc[i + 1 : end, weights.columns.get_indexer(worst)] = 0.5 / top_n
            weights.iloc[i + 1 : end, weights.columns.get_indexer(best)] = -0.5 / top_n
    # weights on day t were set at the close of t-1, so they earn day t's return.
    gross = (weights * r).sum(axis=1)
    turnover = weights.diff().abs().sum(axis=1).fillna(weights.abs().sum(axis=1))
    short = weights.clip(upper=0.0).abs().sum(axis=1)
    borrow = short * borrow_bps / 1e4 / 252.0
    net = gross - turnover * cost_bps / 1e4 - borrow
    active = weights.abs().sum(axis=1) > 1e-12
    return net, turnover, active


def _holiday_dates(index: pd.DatetimeIndex) -> list[pd.Timestamp]:
    idx = pd.DatetimeIndex(index).normalize().sort_values()
    all_days = pd.bdate_range(idx.min(), idx.max())
    return [d for d in all_days if d not in set(idx)]


def calendar_mask(
    index: pd.DatetimeIndex,
    mode: str,
    *,
    k: int = 1,
    weekday: int | None = None,
) -> pd.Series:
    """Boolean holding-day mask from calendar information only.

    `mode`: tom (first/last k trading days of a month), month_end, pre_holiday,
    post_holiday, dow (weekday 0=Mon), all.
    """
    idx = pd.DatetimeIndex(index).normalize()
    mask = pd.Series(False, index=idx, dtype=bool)
    if mode == "all":
        mask[:] = True
        return mask
    if mode == "dow":
        if weekday is None or not 0 <= weekday <= 4:
            raise ValueError("weekday must be 0..4")
        mask[idx.weekday == weekday] = True
        return mask
    months = pd.Series(idx.to_period("M"), index=idx)
    if mode == "month_end":
        for _, g in idx.to_series().groupby(months):
            mask.loc[g.iloc[-1]] = True
        return mask
    if mode == "tom":
        if k < 1:
            raise ValueError("k must be >= 1")
        for _, g in idx.to_series().groupby(months):
            mask.loc[g.iloc[:k]] = True
            mask.loc[g.iloc[-k:]] = True
        return mask
    holidays = set(_holiday_dates(idx))
    if mode == "pre_holiday":
        for h in holidays:
            prior = idx[idx < h]
            if len(prior):
                mask.loc[prior[-1]] = True
        return mask
    if mode == "post_holiday":
        for h in holidays:
            nxt = idx[idx > h]
            if len(nxt):
                mask.loc[nxt[0]] = True
        return mask
    raise ValueError(f"unknown calendar mode {mode!r}")


def calendar_returns(
    returns: pd.Series,
    mask: pd.Series,
    *,
    cost_bps: float = 5.0,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Long-only calendar strategy; the calendar date is known before the open."""
    r = returns.reindex(mask.index).fillna(0.0)
    pos = mask.astype(float)
    turnover = pos.diff().abs().fillna(pos.abs())
    net = pos * r - turnover * cost_bps / 1e4
    active = mask
    return net, turnover, active


def calendar_placebo(
    returns: pd.Series,
    active_count: int,
    *,
    cost_bps: float = 5.0,
    n: int = 200,
    seed: int = 7,
) -> dict:
    """Random-date control with the same number of active days.

    Returns the placebo mean distribution and the share of placebo paths with a
    mean at least as high as the actual signal (a one-sided p-value).
    """
    r = returns.dropna()
    if active_count <= 0 or len(r) < active_count:
        return {"n": n, "mean": float("nan"), "p5": float("nan"), "p95": float("nan")}
    rng = np.random.default_rng(seed)
    means = np.empty(n)
    for i in range(n):
        sel = rng.choice(len(r), size=int(active_count), replace=False)
        mask = pd.Series(False, index=r.index)
        mask.iloc[sel] = True
        net, _, _ = calendar_returns(r, mask, cost_bps=cost_bps)
        means[i] = float(net[mask].mean())
    return {
        "n": n,
        "mean": float(means.mean()),
        "p5": float(np.quantile(means, 0.05)),
        "p95": float(np.quantile(means, 0.95)),
        "means": means,
    }


def breakout_returns(
    df: pd.DataFrame,
    *,
    window: int = 20,
    hold: int = 3,
    vol_filter: bool = True,
    cost_bps: float = 5.0,
) -> tuple[pd.Series, pd.Series, int]:
    """Daily high/ATR breakout, next-open entry, close exit after `hold` days.

    Signal at close `t`: close > prior `window`-day high, optionally with
    ATR14/close above its trailing 252-day median (all shifted by one session).
    Entry is the next open; exit is the close after `hold` sessions. Returns
    `(net, active, trades)`.
    """
    d = df.copy().sort_index()
    high = d["high"].astype(float)
    low = d["low"].astype(float)
    close = d["close"].astype(float)
    open_ = d["open"].astype(float)
    prev = close.shift(1)
    tr = pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(
        axis=1
    )
    atr = tr.rolling(14, min_periods=14).mean()
    atr_pct = atr / close.replace(0.0, np.nan)
    vol_ok = (
        atr_pct > atr_pct.rolling(252, min_periods=60).median().shift(1)
        if vol_filter
        else pd.Series(True, index=d.index)
    )
    upper = high.shift(1).rolling(window, min_periods=window).max()
    signal = (close > upper) & vol_ok
    signal = signal.fillna(False)

    eq = [1.0]
    active = [False]
    pos = False
    entry_i = exit_i = 0
    trades = 0
    cost = cost_bps / 1e4
    for i in range(1, len(d)):
        cur = eq[-1]
        day_active = False
        if pos:
            day_active = True
            if i == entry_i:
                cur *= 1.0 + (close.iloc[i] / open_.iloc[i] - 1.0)
            else:
                cur *= 1.0 + (close.iloc[i] / close.iloc[i - 1] - 1.0)
            if i >= exit_i:
                cur *= 1.0 - cost
                pos = False
        if not pos and bool(signal.iloc[i - 1]):
            pos = True
            entry_i = i
            exit_i = min(i + hold - 1, len(d) - 1)
            trades += 1
            day_active = True
            cur *= 1.0 - cost
            cur *= 1.0 + (close.iloc[i] / open_.iloc[i] - 1.0)
            if i >= exit_i:
                cur *= 1.0 - cost
                pos = False
        eq.append(cur)
        active.append(day_active)
    equity = pd.Series(eq, index=d.index, name="equity")
    net = equity.pct_change().fillna(0.0)
    return net, pd.Series(active, index=d.index), trades


def stats(
    net: pd.Series,
    active: pd.Series | None,
    *,
    cost_bps: float,
    label: str,
) -> dict:
    net = net.fillna(0.0)
    equity, tax = after_tax_series(net)
    rr = equity.pct_change().dropna()
    years = len(net) / 252.0
    hit_base = net[active] if active is not None else net
    hit_base = hit_base[hit_base.index.isin(net.index)]
    return {
        "label": label,
        "cost_bps": cost_bps,
        "n": int(active.sum()) if active is not None else len(net),
        "multiple": float(equity.iloc[-1]),
        "cagr": float(equity.iloc[-1] ** (1.0 / years) - 1.0),
        "maxdd": float((equity / equity.cummax() - 1.0).min()),
        "sharpe": float(net.mean() / net.std() * np.sqrt(252))
        if net.std() > 0
        else float("nan"),
        "hit": float((hit_base > 0).mean()) if len(hit_base) else float("nan"),
        "tax": float(tax),
        "mean_active": float(hit_base.mean()) if len(hit_base) else float("nan"),
    }


def buy_hold_returns(
    symbol: str, start: str = "2019-01-01", end: str = "2026-11-25"
) -> pd.Series:
    s = daily_bars(symbol)["adjclose"].dropna()
    s = s[(s.index >= pd.Timestamp(start)) & (s.index <= pd.Timestamp(end))]
    return s.pct_change(fill_method=None).fillna(0.0)
