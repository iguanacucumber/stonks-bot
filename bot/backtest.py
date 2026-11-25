"""Daily portfolio accounting, execution with costs, stops, metrics."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable

import numpy as np
import pandas as pd

from .strategy import Config, Market, eligible, inverse_vol_weights, target_weights

Selector = Callable[[Market, pd.Timestamp, list[str], set[str]], dict[str, float]]
Overlay = Callable[[pd.Timestamp, dict[str, float], Market], dict[str, float]]


@dataclass
class Result:
    equity: pd.Series
    cash: pd.Series
    trades: pd.DataFrame
    weights: pd.DataFrame
    nav: pd.DataFrame


def _sector_fallback(mkt: Market, date: pd.Timestamp) -> dict[str, float]:
    cfg = mkt.cfg
    names = [t for t in cfg.fallback_sectors if t in mkt.px.columns]
    scores = {}
    for t in names:
        c = mkt.px[t]
        v = c.shift(21).loc[date] / c.shift(273).loc[date] - 1
        if pd.notna(v):
            scores[t] = float(v)
    if not scores:
        return {}
    top = sorted(scores, key=scores.get, reverse=True)[:cfg.sector_top]
    return {t: cfg.gross / len(top) for t in top}


def _default_selector(
    mkt: Market, date: pd.Timestamp, held: list[str], banned: set[str]
) -> dict[str, float]:
    cfg = mkt.cfg
    core = cfg.core if (cfg.core in mkt.px.columns and cfg.core_weight > 0) else None
    core_on = True
    if core and cfg.core_mode in ("relative", "relative_switch"):
        c = mkt.px[core]
        q = c.loc[date] / c.shift(cfg.core_lookback).loc[date] - 1
        s = mkt.spy.loc[date] / mkt.spy.shift(cfg.core_lookback).loc[date] - 1
        core_on = bool(pd.notna(q) and pd.notna(s) and q > s)
        if core and cfg.core_mode == "relative_switch" and not core_on:
            core = cfg.core_alt if cfg.core_alt in mkt.px.columns else None
    if core and core_on:
        return {core: cfg.core_weight}
    if core and cfg.fallback == "sectors":
        return _sector_fallback(mkt, date)
    picks = [t for t in _select_excluding(mkt, date, banned) if t != core]
    w = inverse_vol_weights(mkt, date, picks)
    if not bool(mkt.regime.loc[date]):
        w = {t: x * cfg.regime_gross for t, x in w.items()}
    return w


def _select_excluding(mkt: Market, date: pd.Timestamp, banned: set[str]) -> list[str]:
    cfg = mkt.cfg
    df = eligible(mkt, date)
    if df.empty:
        return []
    df = df.drop(index=[t for t in banned if t in df.index], errors="ignore")
    max_per_sector = max(1, int(np.floor(cfg.sector_cap * cfg.top_n)))
    counts: dict[str, int] = {}
    picks: list[str] = []
    for ticker, row in df.iterrows():
        sec = row["sector"]
        if counts.get(sec, 0) >= max_per_sector:
            continue
        picks.append(ticker)
        counts[sec] = counts.get(sec, 0) + 1
        if len(picks) == cfg.top_n:
            break
    return picks


def run(
    mkt: Market, selector: Selector | None = None, overlay: Overlay | None = None
) -> Result:
    cfg = mkt.cfg
    selector = selector or _default_selector
    dates = list(mkt.dates)
    n = len(dates)
    px = mkt.px

    cash = 1.0
    pos: dict[str, float] = {}
    entry: dict[str, float] = {}
    peak: dict[str, float] = {}
    banned_until: dict[str, int] = {}
    pending_target: dict[str, float] | None = None
    pending_exit: set[str] = set()
    pending_reason: dict[str, str] = {}

    eqs, cashs, wrows, trades = [], [], [], []

    def price(t: str, d: pd.Timestamp) -> float | None:
        v = px.at[d, t] if (d in px.index and t in px.columns) else np.nan
        return float(v) if pd.notna(v) and v > 0 else None

    for i, d in enumerate(dates):
        # 1. execute orders decided at the previous close, at today's close
        if pending_target is not None or pending_exit:
            has_target = pending_target is not None
            target = {t: w for t, w in (pending_target or {}).items() if w > 1e-9}
            for t in pending_exit:
                target.pop(t, None)
            equity_now = cash + sum(sh * (price(t, d) or 0.0) for t, sh in pos.items())
            # sell exits first
            for t in list(pos):
                if t in pending_exit or (has_target and t not in target):
                    p = price(t, d)
                    if p is None:
                        continue
                    notional = pos[t] * p
                    cash += notional - notional * cfg.cost_bps / 1e4
                    trades.append(
                        (
                            d,
                            t,
                            "SELL",
                            pos[t],
                            p,
                            notional,
                            pending_reason.get(t, "rebalance"),
                        )
                    )
                    del pos[t]
                    entry.pop(t, None)
                    peak.pop(t, None)
            if target:
                equity_now = cash + sum(
                    sh * (price(t, d) or 0.0) for t, sh in pos.items()
                )
                for t, w in target.items():
                    p = price(t, d)
                    if p is None:
                        continue
                    cur = pos.get(t, 0.0) * p
                    delta = w * equity_now - cur
                    if abs(delta) < 1e-4 * equity_now:
                        continue
                    sh = delta / p
                    cost = abs(delta) * cfg.cost_bps / 1e4
                    if delta > 0 and cash < delta + cost:
                        delta = max(0.0, cash - cost)
                        sh = delta / p
                        cost = delta * cfg.cost_bps / 1e4
                    cash -= delta + cost
                    pos[t] = pos.get(t, 0.0) + sh
                    side = "BUY" if sh > 0 else "SELL"
                    trades.append(
                        (
                            d,
                            t,
                            side,
                            abs(sh),
                            p,
                            abs(delta),
                            pending_reason.get(t, "rebalance"),
                        )
                    )
                    if t not in entry and pos.get(t, 0) > 0:
                        entry[t] = p
                        peak[t] = p
            pending_target, pending_exit, pending_reason = None, set(), {}

        # 2. mark to market and check trailing stops
        vals = {}
        for t, sh in pos.items():
            p = price(t, d)
            if p is None:
                p = entry.get(t, 0.0)
            vals[t] = sh * p
            protected = {cfg.core, *cfg.fallback_sectors}
            if t in peak and p > 0 and t not in protected:
                peak[t] = max(peak[t], p)
                if p < peak[t] * (1.0 - cfg.stop):
                    pending_exit.add(t)
                    pending_reason[t] = "stop"
                    banned_until[t] = i + cfg.reentry_days
        equity = cash + sum(vals.values())
        eqs.append(equity)
        cashs.append(cash)
        wrows.append({t: v / equity for t, v in vals.items()} | {"CASH": cash / equity})

        # 3. decide next close's trades
        banned = {t for t, until in banned_until.items() if until > i}
        if i % cfg.rebalance_days == 0 and i + 1 < n:
            held = list(pos)
            base = selector(mkt, d, held, banned)
            if overlay is not None:
                base = overlay(d, base, mkt)
            pending_target = base
            pending_reason = {t: "rebalance" for t in base}
        # stops also queue their own exit
        # (pending_target is merged at execution; stopped names are removed)

    equity = pd.Series(eqs, index=dates, name="equity")
    cash_s = pd.Series(cashs, index=dates, name="cash")
    tr = pd.DataFrame(
        trades,
        columns=["date", "ticker", "side", "shares", "price", "notional", "reason"],
    )
    wdf = pd.DataFrame(wrows, index=dates).fillna(0.0)
    nav = pd.DataFrame({"equity": equity, "cash": cash_s})
    return Result(equity=equity, cash=cash_s, trades=tr, weights=wdf, nav=nav)


def spy_result(mkt: Market) -> Result:
    spy = mkt.spy.reindex(mkt.dates).ffill()
    eq = spy / spy.iloc[0]
    return Result(
        equity=eq,
        cash=pd.Series(0.0, index=eq.index),
        trades=pd.DataFrame(
            columns=["date", "ticker", "side", "shares", "price", "notional", "reason"]
        ),
        weights=pd.DataFrame({"SPY": 1.0}, index=eq.index),
        nav=pd.DataFrame({"equity": eq, "cash": 0.0}),
    )


def equal_weight_selector(
    mkt: Market, date: pd.Timestamp, held: list[str], banned: set[str]
) -> dict[str, float]:
    df = eligible(mkt, date)
    if df.empty:
        return {}
    names = list(df.index)
    return {t: mkt.cfg.gross / len(names) for t in names}


def random_selector(seed: int, top_n: int) -> Selector:
    rng = np.random.default_rng(seed)

    def sel(
        mkt: Market, date: pd.Timestamp, held: list[str], banned: set[str]
    ) -> dict[str, float]:
        df = eligible(mkt, date)
        df = df.drop(index=[t for t in banned if t in df.index], errors="ignore")
        if df.empty:
            return {}
        k = min(top_n, len(df))
        names = list(rng.choice(list(df.index), size=k, replace=False))
        return {t: mkt.cfg.gross / k for t in names}

    return sel


def metrics(equity: pd.Series, spy: pd.Series) -> dict[str, float]:
    r = equity.pct_change().dropna()
    b = spy.reindex(r.index).pct_change().dropna()
    j = r.index.intersection(b.index)
    r, b = r.loc[j], b.loc[j]
    if len(r) < 2:
        return {}
    years = len(r) / 252.0
    total = equity.iloc[-1] / equity.iloc[0]
    cagr = total ** (1 / years) - 1
    vol = r.std() * np.sqrt(252)
    sharpe = (r.mean() * 252) / vol if vol > 0 else np.nan
    downside = r[r < 0].std() * np.sqrt(252)
    sortino = (r.mean() * 252) / downside if downside and downside > 0 else np.nan
    dd = (equity / equity.cummax() - 1).min()
    beta = np.cov(r, b)[0, 1] / np.var(b) if np.var(b) > 0 else np.nan
    alpha = (r.mean() - beta * b.mean()) * 252
    te = (r - b).std() * np.sqrt(252)
    return {
        "total_return": total - 1,
        "cagr": cagr,
        "vol": vol,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_dd": dd,
        "calmar": cagr / abs(dd) if dd < 0 else np.nan,
        "beta": beta,
        "alpha": alpha,
        "tracking_error": te,
        "hit_rate": (r > 0).mean(),
        "days": len(r),
    }


def bootstrap_sharpe(
    r: pd.Series, n: int = 2000, block: int = 5, seed: int = 7
) -> tuple[float, float]:
    rng = np.random.default_rng(seed)
    x = r.to_numpy()
    m = len(x)
    if m < block * 2:
        return np.nan, np.nan
    nb = int(np.ceil(m / block))
    out = np.empty(n)
    for k in range(n):
        starts = rng.integers(0, m, nb)
        idx = (starts[:, None] + np.arange(block)[None, :]).ravel() % m
        samp = x[idx][:m]
        sd = samp.std()
        out[k] = samp.mean() / sd * np.sqrt(252) if sd > 0 else 0.0
    return float(np.quantile(out, 0.025)), float(np.quantile(out, 0.975))


def bootstrap_diff(
    r: pd.Series, b: pd.Series, n: int = 2000, block: int = 5, seed: int = 11
) -> tuple[float, float, float]:
    rng = np.random.default_rng(seed)
    d = (r - b).dropna().to_numpy()
    m = len(d)
    nb = int(np.ceil(m / block))
    out = np.empty(n)
    for k in range(n):
        starts = rng.integers(0, m, nb)
        idx = (starts[:, None] + np.arange(block)[None, :]).ravel() % m
        out[k] = d[idx][:m].mean() * 252
    return (
        float(np.quantile(out, 0.025)),
        float(d.mean() * 252),
        float(np.quantile(out, 0.975)),
    )


def summarize(res: Result, mkt: Market) -> tuple[dict[str, float], pd.Series]:
    spy = mkt.spy.reindex(res.equity.index).ffill()
    m = metrics(res.equity, spy)
    r = res.equity.pct_change().dropna()
    b = spy.pct_change().dropna().reindex(r.index).dropna()
    r = r.reindex(b.index)
    lo, hi = bootstrap_sharpe(r)
    m["sharpe_lo"], m["sharpe_hi"] = lo, hi
    lo, mid, hi = bootstrap_diff(r, b)
    m["diff_lo"], m["diff_ann"], m["diff_hi"] = lo, mid, hi
    if len(res.trades):
        years = len(res.equity) / 252
        m["turnover_ann"] = res.trades["notional"].sum() / res.equity.mean() / years
        m["trades"] = len(res.trades)
    else:
        m["turnover_ann"] = 0.0
        m["trades"] = 0
    m["exposure"] = float((1 - res.cash / res.equity).mean())
    return m, spy
