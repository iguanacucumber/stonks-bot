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
    top = sorted(scores, key=scores.get, reverse=True)[: cfg.sector_top]
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
    """Daily accounting. Holdings live in numpy arrays so the per-day mark-to-market
    and stop check are vector ops; only rebalances touch per-name Python."""
    cfg = mkt.cfg
    selector = selector or _default_selector
    dates = list(mkt.dates)
    n = len(dates)
    names = list(mkt.px.columns)
    nc = len(names)
    cols = mkt._px_col
    rows = mkt._px_row
    arr = mkt._px_arr
    cost_rate = cfg.cost_bps / 1e4
    stop_keep = 1.0 - cfg.stop
    protected_ci = np.array(
        [cols[t] for t in {cfg.core, *cfg.fallback_sectors} if t in cols], dtype=int
    )

    cash = 1.0
    shares = np.zeros(nc)
    entry = np.full(nc, np.nan)
    last_px = np.full(nc, np.nan)
    peak = np.full(nc, np.nan)
    banned = np.full(nc, -1, dtype=int)
    held: list[int] = []  # column indices, insertion order
    pending_target: dict[str, float] | None = None
    pending_exit: set[int] = set()
    pending_reason: dict[int, str] = {}

    trades: list[tuple] = []
    eqs = np.zeros(n)
    cashs = np.zeros(n)
    wmat = np.zeros((n, nc))

    for i, d in enumerate(dates):
        ri = rows.get(d)
        row = arr[ri] if ri is not None else np.full(nc, np.nan)
        # prices usable for trading: missing/non-positive -> 0.0 (skipped)
        pr = np.where(np.isnan(row) | (row <= 0), 0.0, row)
        seen = pr > 0
        last_px[seen] = pr[seen]

        # 1. execute orders decided at the previous close, at today's close
        if pending_target is not None or pending_exit:
            has_target = pending_target is not None
            tgt: list[tuple[int, float]] = []
            keep: set[int] = set()
            if has_target:
                for t, w in pending_target.items():
                    ci = cols.get(t)
                    if ci is not None and w > 1e-9:
                        tgt.append((ci, w))
                        keep.add(ci)
            for ci in pending_exit:
                keep.discard(ci)
            equity_now = cash + float(shares @ pr)
            # sell exits first
            for ci in list(held):
                if ci in pending_exit or (has_target and ci not in keep):
                    p = pr[ci]
                    if p == 0.0:
                        continue
                    notional = shares[ci] * p
                    cash += notional - notional * cost_rate
                    trades.append(
                        (
                            d,
                            names[ci],
                            "SELL",
                            shares[ci],
                            float(p),
                            notional,
                            pending_reason.get(ci, "rebalance"),
                        )
                    )
                    shares[ci] = 0.0
                    entry[ci] = np.nan
                    peak[ci] = np.nan
                    held.remove(ci)
            if tgt:
                equity_now = cash + float(shares @ pr)
                if pending_exit:
                    tgt = [(ci, w) for ci, w in tgt if ci not in pending_exit]
                for ci, w in tgt:
                    p = pr[ci]
                    if p == 0.0:
                        continue
                    cur = shares[ci] * p
                    delta = w * equity_now - cur
                    if abs(delta) < 1e-4 * equity_now:
                        continue
                    sh = delta / p
                    cost = abs(delta) * cost_rate
                    if delta > 0 and cash < delta + cost:
                        delta = max(0.0, cash - cost)
                        sh = delta / p
                        cost = delta * cost_rate
                    cash -= delta + cost
                    if shares[ci] == 0.0 and sh != 0.0:
                        held.append(ci)
                    shares[ci] += sh
                    trades.append(
                        (
                            d,
                            names[ci],
                            "BUY" if sh > 0 else "SELL",
                            abs(sh),
                            float(p),
                            abs(delta),
                            pending_reason.get(ci, "rebalance"),
                        )
                    )
                    if np.isnan(entry[ci]) and shares[ci] > 0:
                        entry[ci] = p
                        peak[ci] = p
            pending_target, pending_exit, pending_reason = None, set(), {}

        # 2. mark to market and check trailing stops
        # A missing bar (foreign holiday, halted name) is carried at the last observed
        # price. Marking at the entry price used to invent drawdowns on every US holiday.
        prm = np.where(pr > 0, pr, last_px)
        prm = np.where(np.isnan(prm), 0.0, prm)
        vals = shares * prm
        equity = cash + float(vals.sum())
        eqs[i] = equity
        cashs[i] = cash
        wmat[i] = vals / equity

        if held:
            hi = np.array(held, dtype=int)
            live = (pr[hi] > 0) & ~np.isin(hi, protected_ci)
            if live.any():
                li = hi[live]
                peak[li] = np.maximum(peak[li], pr[li])
                stopped = li[pr[li] < peak[li] * stop_keep]
                for ci in stopped:
                    pending_exit.add(int(ci))
                    pending_reason[int(ci)] = "stop"
                    banned[ci] = i + cfg.reentry_days

        # 3. decide next close's trades
        if i % cfg.rebalance_days == 0 and i + 1 < n:
            banned_names = (
                {names[ci] for ci in np.flatnonzero(banned > i)}
                if (banned > i).any()
                else set()
            )
            base = selector(mkt, d, [names[ci] for ci in held], banned_names)
            if overlay is not None:
                base = overlay(d, base, mkt)
            pending_target = base
            pending_reason = {cols[t]: "rebalance" for t in base if t in cols}

    equity = pd.Series(eqs, index=dates, name="equity")
    cash_s = pd.Series(cashs, index=dates, name="cash")
    tr = pd.DataFrame(
        trades,
        columns=["date", "ticker", "side", "shares", "price", "notional", "reason"],
    )
    wdf = pd.DataFrame(wmat, index=dates, columns=names)
    wdf["CASH"] = cash_s / equity
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
