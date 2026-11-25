"""Daily portfolio accounting, execution with costs, stops, metrics."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable

import numpy as np
import pandas as pd

from .strategy import Config, Market, eligible, inverse_vol_weights, target_weights

Selector = Callable[[Market, pd.Timestamp, list[str], set[str]], dict[str, float]]
Overlay = Callable[[pd.Timestamp, dict[str, float], Market], dict[str, float]]


def tax_on_gain(
    taxable: float, federal: float, state: float, salt_left: float, salt_cap: float
) -> tuple[float, float]:
    """Federal + state tax on a gain; state tax is SALT-deductible up to the cap.

    Returns (tax, salt_deduction_used). Federal rate should include NIIT if desired.
    """
    if taxable <= 0:
        return 0.0, 0.0
    state_tax = taxable * state
    deductible = min(state_tax, max(0.0, salt_left)) if state and salt_cap > 0 else 0.0
    return (taxable - deductible) * federal + state_tax, deductible


def consume_lots(lots: list[list[float]], shares: float, price: float) -> float:
    """Sell `shares` highest-basis-first, mutating `lots`; return realized gain."""
    lots.sort(key=lambda lot: -lot[1])
    remaining, gain = shares, 0.0
    while remaining > 1e-12 and lots:
        lot = lots[0]
        take = min(lot[0], remaining)
        gain += (price - lot[1]) * take
        lot[0] -= take
        remaining -= take
        if lot[0] <= 1e-12:
            lots.pop(0)
    return gain


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
    arr_open = mkt._px_open
    exec_open = cfg.exec_price == "open"
    if cfg.dd_stop and cfg.dd_scale <= 0:
        raise ValueError("dd_scale must be > 0: a full cash exit cannot recover")
    cost_rate = cfg.cost_bps / 1e4
    borrow_rate = cfg.financing_bps / 1e4 / 252.0
    realized = 0.0
    salt_used = 0.0
    salt_year = 0
    wash: dict[int, tuple[pd.Timestamp, float]] = {}

    def _tax(gain: float, date: pd.Timestamp) -> float:
        """Tax net realized gains; losses carry forward, never refund."""
        nonlocal realized, salt_used, salt_year
        if date.year != salt_year:
            salt_year = date.year
            salt_used = 0.0
        before = max(0.0, realized)
        realized += gain
        after = max(0.0, realized)
        taxable = max(0.0, after - before)
        if taxable <= 0:
            return 0.0
        tax, used = tax_on_gain(
            taxable, cfg.tax_rate, cfg.state_tax_rate,
            cfg.salt_cap - salt_used, cfg.salt_cap,
        )
        salt_used += used
        return tax

    lots: dict[int, list[list[float]]] = {}

    def _avg_basis(ci: int) -> float:
        ls = lots.get(ci, [])
        n = sum(l[0] for l in ls)
        return sum(l[0] * l[1] for l in ls) / n if n > 0 else float("nan")

    def _sell(ci: int, sh: float, p: float, date: pd.Timestamp) -> float:
        """Realize `sh` shares at price p; return the realized gain.

        `avg` keeps one average basis. `hifo` consumes the highest-priced lots
        first, which realizes the smallest gain (lot-level tax deferral).
        """
        if cfg.tax_lots != "hifo":
            gain = 0.0 if np.isnan(entry[ci]) else (p - entry[ci]) * sh
            if cfg.wash_sale_days and gain < 0:
                wash[ci] = (date, -gain)
            return gain
        ls = lots.setdefault(ci, [])
        gain = consume_lots(ls, sh, p)
        entry[ci] = _avg_basis(ci)
        if cfg.wash_sale_days and gain < 0:
            wash[ci] = (date, -gain)
        return gain
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
    peak_eq = 1.0
    risk_off = False
    risk_reason = ""

    trades: list[tuple] = []
    eqs = np.zeros(n)
    cashs = np.zeros(n)
    wmat = np.zeros((n, nc))

    for i, d in enumerate(dates):
        ri = rows.get(d)
        row = arr[ri] if ri is not None else np.full(nc, np.nan)
        # prices usable for trading: missing/non-positive -> 0.0 (skipped)
        pr = np.where(np.isnan(row) | (row <= 0), 0.0, row)
        orow = arr_open[ri] if ri is not None else np.full(nc, np.nan)
        po = np.where(np.isnan(orow) | (orow <= 0), 0.0, orow)
        seen = pr > 0
        last_px[seen] = pr[seen]

        # margin interest on yesterday's negative cash (debt grows by the daily rate)
        if cash < 0 and borrow_rate:
            cash *= 1.0 + borrow_rate

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
            mark = np.where(exec_open & (po > 0), po, pr)
            equity_now = cash + float(shares @ mark)
            # sell exits first
            for ci in list(held):
                if ci in pending_exit or (has_target and ci not in keep):
                    p = mark[ci]
                    if p == 0.0:
                        continue
                    notional = shares[ci] * p
                    cash += notional - notional * cost_rate
                    gain = _sell(ci, shares[ci], p, d)
                    if cfg.tax_rate or cfg.state_tax_rate:
                        cash -= _tax(gain, d)
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
                equity_now = cash + float(shares @ mark)
                if pending_exit:
                    tgt = [(ci, w) for ci, w in tgt if ci not in pending_exit]
                for ci, w in tgt:
                    p = mark[ci]
                    if p == 0.0:
                        continue
                    cur = shares[ci] * p
                    delta = w * equity_now - cur
                    if abs(delta) < max(cfg.trade_band, 1e-4) * equity_now:
                        continue
                    sh = delta / p
                    cost = abs(delta) * cost_rate
                    if delta > 0:
                        if cfg.leverage <= 1.0:
                            if cash < delta + cost:
                                delta = max(0.0, cash - cost)
                                sh = delta / p
                                cost = delta * cost_rate
                        else:
                            gross_now = float((shares * mark).sum())
                            headroom = cfg.leverage * equity_now - gross_now
                            affordable = max(0.0, headroom) / (1.0 + cost_rate)
                            if delta > affordable:
                                delta = affordable
                                sh = delta / p
                                cost = delta * cost_rate
                    cash -= delta + cost
                    if sh < 0:
                        gain = _sell(ci, -sh, p, d)
                        if cfg.tax_rate or cfg.state_tax_rate:
                            cash -= _tax(gain, d)
                    if shares[ci] == 0.0 and sh != 0.0:
                        held.append(ci)
                    old_sh = shares[ci]
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
                    if sh > 0:
                        if cfg.wash_sale_days and ci in wash:
                            wd, loss = wash.pop(ci)
                            if (d - wd).days <= cfg.wash_sale_days:
                                realized += loss  # disallowed loss, added back
                        if cfg.tax_lots == "hifo":
                            lots.setdefault(ci, []).append([sh, p])
                            entry[ci] = _avg_basis(ci)
                        elif old_sh <= 0 or np.isnan(entry[ci]):
                            entry[ci] = p
                        else:
                            entry[ci] = (entry[ci] * old_sh + p * sh) / (old_sh + sh)
                        if old_sh <= 0 or np.isnan(peak[ci]):
                            peak[ci] = p
                    elif shares[ci] <= 0:
                        entry[ci] = np.nan
                        peak[ci] = np.nan
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

        # margin discipline: after a drop, gross exposure drifts above the target.
        # A real account would be forced to sell, so do it here too (and pay costs).
        if cfg.leverage > 1.0 and equity > 0:
            for _ in range(2):
                gross_now = float((shares * prm).sum())
                cap = cfg.leverage * equity
                if gross_now <= cap * (1.0 + cfg.leverage_band):
                    break
                scale = cap / gross_now
                for ci in list(held):
                    p = pr[ci]
                    if p <= 0:
                        continue
                    sell_sh = shares[ci] * (1.0 - scale)
                    if sell_sh <= 0:
                        continue
                    notional = sell_sh * p
                    cash += notional - notional * cost_rate
                    gain = _sell(ci, sell_sh, p, d)
                    if cfg.tax_rate or cfg.state_tax_rate:
                        cash -= _tax(gain, d)
                    shares[ci] -= sell_sh
                    trades.append(
                        (
                            d,
                            names[ci],
                            "SELL",
                            sell_sh,
                            float(p),
                            notional,
                            "deleverage",
                        )
                    )
                    if shares[ci] * p < 1e-6:
                        shares[ci] = 0.0
                        entry[ci] = np.nan
                        peak[ci] = np.nan
                        held.remove(ci)
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

        # 2b. drawdown circuit breaker, decided at the close and executed next close
        if (cfg.dd_stop or cfg.trend_ma) and equity > 0:
            peak_eq = max(peak_eq, equity)
            dd = equity / peak_eq - 1.0
            dd_breach = bool(cfg.dd_stop) and dd <= -cfg.dd_stop
            trend_breach = False
            if cfg.trend_ma and mkt.core_ma is not None and cfg.core in cols:
                ma = mkt.core_ma.loc[d]
                px_core = pr[cols[cfg.core]]
                trend_breach = bool(pd.notna(ma) and px_core > 0 and px_core < ma)
            if not risk_off and (dd_breach or trend_breach) and held:
                risk_off = True
                risk_reason = "dd" if dd_breach else "trend"
                cur = {
                    names[ci]: shares[ci] * pr[ci] / equity
                    for ci in held
                    if pr[ci] > 0
                }
                scale = cfg.dd_scale if risk_reason == "dd" else cfg.trend_scale
                pending_target = {t: w * scale for t, w in cur.items()}
                pending_reason = {cols[t]: "risk_off" for t in pending_target}
            elif risk_off:
                # a trend exit clears when the trend is back, not when cash "recovers"
                if risk_reason == "trend" and not trend_breach:
                    risk_off = False
                elif risk_reason == "dd" and dd >= -cfg.dd_recover:
                    risk_off = False

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
            if cfg.leverage != 1.0 and base:
                lev = cfg.leverage
                if cfg.leverage_mode == "trend" and not bool(mkt.regime.loc[d]):
                    lev = cfg.leverage_down
                if risk_off:
                    lev *= cfg.dd_scale if risk_reason == "dd" else cfg.trend_scale
                base = {t: w * lev for t, w in base.items()}
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


def horizon_returns(eq: pd.Series, live: float = 1.0) -> dict[str, float]:
    """Trailing returns ending at the last close, chained with today's live move."""
    out = {}
    for label, n in (("1 day", 1), ("1 week", 5), ("1 month", 21), ("1 year", 252)):
        # `live` is today's not-yet-in-the-series move, so it replaces one day of history
        j = n - 1 if live != 1.0 else n
        out[label] = (
            float("nan")
            if len(eq) <= j
            else float(eq.iloc[-1] / eq.iloc[-1 - j] * live - 1)
        )
    return out


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
