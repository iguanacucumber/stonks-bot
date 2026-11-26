"""Daily portfolio accounting, execution with costs, stops, metrics."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable

import numpy as np
import pandas as pd

from .strategy import Config, Market, eligible, inverse_vol_weights, target_weights

Selector = Callable[[Market, pd.Timestamp, list[str], set[str]], dict[str, float]]
Overlay = Callable[[pd.Timestamp, dict[str, float], Market], dict[str, float]]


def fr_rate(year: int, pea: bool = False, override: float = 0.0) -> float:
    """French capital-gains rate by year: PFU, or PEA social contributions."""
    if override:
        return float(override)
    if pea:
        return 0.186 if year >= 2026 else 0.172
    if year < 2018:
        return 0.345  # 19% IR + 15.5% social, pre-PFU
    if year < 2026:
        return 0.30  # PFU 12.8% + 17.2%
    return 0.314  # PFU 12.8% + 18.6% from 2026


def ftt_rate(date: pd.Timestamp) -> float:
    """French financial transaction tax on purchases of large French shares."""
    if date < pd.Timestamp("2017-01-01"):
        return 0.002
    if date < pd.Timestamp("2025-04-01"):
        return 0.003
    return 0.004


def limit_fill(
    open_px: float,
    high: float,
    low: float,
    close: float,
    is_buy: bool,
    limit_bps: float,
) -> tuple[float, bool]:
    """Fill a day's limit order at the open-anchored limit, else at the close.

    The order is decided before the open and rests for the session: the limit is
    open*(1-k) for a buy and open*(1+k) for a sell. A touch of the same day's
    low/high fills at the limit; otherwise the fallback is market-on-close. The
    0 bps case returns the open unchanged, which keeps the shipped path exact.
    """
    if limit_bps <= 0 or not np.isfinite(open_px) or open_px <= 0:
        return float(open_px), False
    k = limit_bps / 1e4
    if is_buy:
        limit = open_px * (1.0 - k)
        if np.isfinite(low) and low > 0 and low <= limit:
            return float(limit), True
    else:
        limit = open_px * (1.0 + k)
        if np.isfinite(high) and high > 0 and high >= limit:
            return float(limit), True
    if np.isfinite(close) and close > 0:
        return float(close), False
    return float(open_px), False


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
    weights_open: pd.DataFrame | None = None
    eq_open_pre: pd.Series | None = None
    eq_open: pd.Series | None = None
    tax_paid: float = 0.0
    fees_paid: float = 0.0
    ftt_paid: float = 0.0
    unrealized_tax: float = 0.0


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


def target_book(
    mkt: Market,
    date: pd.Timestamp,
    held: list[str] | None = None,
    banned: set[str] | None = None,
) -> dict[str, float]:
    """Public adapter for the shipped selector, used by the live planner.

    This is the same function the backtest calls at a rebalance; the wrapper only
    avoids importing a private name from ``bot/live``. No accounting is duplicated.
    """
    return _default_selector(mkt, date, list(held or []), set(banned or ()))


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
    arr_high = mkt._px_high
    arr_low = mkt._px_low
    exec_open = cfg.exec_price == "open"
    do_limit = cfg.limit_bps > 0
    if do_limit and not exec_open:
        raise ValueError("limit_bps requires exec_price='open'")
    if cfg.dd_stop and cfg.dd_scale <= 0:
        raise ValueError("dd_scale must be > 0: a full cash exit cannot recover")
    cost_rate = cfg.cost_bps / 1e4
    borrow_rate = cfg.financing_bps / 1e4 / 252.0
    realized = 0.0
    salt_used = 0.0
    salt_year = 0
    wash: dict[int, tuple[pd.Timestamp, float]] = {}
    tax_paid = 0.0
    fees_paid = 0.0
    ftt_paid = 0.0
    fr_realized = 0.0
    fr_year: int | None = None
    loss_carry: list[list[float]] = []

    def _tax(gain: float, date: pd.Timestamp) -> float:
        """Tax net realized gains; losses carry forward, never refund."""
        nonlocal realized, salt_used, salt_year, fr_realized, fr_year
        if cfg.tax_country == "fr":
            # French CTO: tax the calendar-year net gain (PFU 30%), paid the
            # following year. Nothing is withheld trade by trade.
            if fr_year is None:
                fr_year = date.year
            fr_realized += gain
            return 0.0
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
            taxable,
            cfg.tax_rate,
            cfg.state_tax_rate,
            cfg.salt_cap - salt_used,
            cfg.salt_cap,
        )
        salt_used += used
        return tax

    def _settle_fr(year: int) -> float:
        """French annual settlement: net the year, use old losses, tax the rest."""
        nonlocal fr_realized, tax_paid, loss_carry
        loss_carry = [x for x in loss_carry if year <= x[0]]
        net = fr_realized
        fr_realized = 0.0
        if net > 0:
            for x in loss_carry:
                take = min(x[1], net)
                x[1] -= take
                net -= take
                if net <= 1e-12:
                    break
            loss_carry = [x for x in loss_carry if x[1] > 1e-12]
            tax = max(0.0, net) * fr_rate(
                year, cfg.tax_country == "fr_pea", cfg.tax_rate
            )
            tax_paid += tax
            return tax
        if net < 0:
            loss_carry.append([year + 10, -net])
        return 0.0

    def _fee_rate(ci: int, date: pd.Timestamp) -> float:
        """Approximate marginal rate, used to size an order before charging it."""
        is_us = names[ci] in cfg.us_tickers
        rate = cost_rate
        if cfg.fx_bps and is_us:
            rate += cfg.fx_bps / 1e4
        if names[ci] in cfg.ftt_tickers:
            rate += ftt_rate(date)
        grid = cfg.us_grid if is_us else cfg.broker_grid
        rate += (
            (cfg.us_bps if is_us else cfg.broker_bps) / 1e4
            if grid
            else (cfg.broker_bps / 1e4)
        )
        return rate

    def _fees(notional: float, is_buy: bool, ci: int, date: pd.Timestamp) -> float:
        """All non-tax trading frictions: spread, broker, FX, and French FTT."""
        nonlocal fees_paid, ftt_paid
        if notional <= 0:
            return 0.0
        is_us = names[ci] in cfg.us_tickers
        fee = notional * cost_rate
        grid = cfg.us_grid if is_us else cfg.broker_grid
        if grid:
            ticket = notional * cfg.initial_capital
            fixed = None
            for limit, amount in grid:
                if ticket <= limit:
                    fixed = amount
                    break
            if fixed is not None:
                fee += fixed / cfg.initial_capital
            else:
                fee += notional * (cfg.us_bps if is_us else cfg.broker_bps) / 1e4
        else:
            if cfg.broker_bps:
                fee += notional * cfg.broker_bps / 1e4
            if cfg.broker_min_eur and cfg.initial_capital > 0:
                fee += cfg.broker_min_eur / cfg.initial_capital
        if cfg.fx_bps and is_us:
            fee += notional * cfg.fx_bps / 1e4
        if is_buy and names[ci] in cfg.ftt_tickers:
            ftt = notional * ftt_rate(date)
            fee += ftt
            ftt_paid += ftt
        fees_paid += fee
        return fee

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

    calendar_set: set[pd.Timestamp] = set()
    if cfg.rebalance_mode == "calendar":
        ds = pd.Series(dates)
        ends = ds.groupby([ds.dt.year, ds.dt.month]).max()
        if cfg.rebalance_months > 1:
            ends = ends[
                (ends.dt.month - 1 - cfg.rebalance_offset) % cfg.rebalance_months == 0
            ]
        calendar_set = set(ends)

    trades: list[tuple] = []
    eqs = np.zeros(n)
    cashs = np.zeros(n)
    wmat = np.zeros((n, nc))
    wopen = np.zeros((n, nc))
    eq_open_pre_arr = np.zeros(n)
    eq_open_arr = np.zeros(n)

    for i, d in enumerate(dates):
        ri = rows.get(d)
        row = arr[ri] if ri is not None else np.full(nc, np.nan)
        # prices usable for trading: missing/non-positive -> 0.0 (skipped)
        pr = np.where(np.isnan(row) | (row <= 0), 0.0, row)
        orow = arr_open[ri] if ri is not None else np.full(nc, np.nan)
        po = np.where(np.isnan(orow) | (orow <= 0), 0.0, orow)
        hrow = arr_high[ri] if ri is not None else np.full(nc, np.nan)
        lrow = arr_low[ri] if ri is not None else np.full(nc, np.nan)
        prev_px = last_px.copy()  # prior close: the correct fallback for a missing open
        seen = pr > 0
        last_px[seen] = pr[seen]

        # margin interest on yesterday's negative cash (debt grows by the daily rate)
        if cash < 0 and borrow_rate:
            cash *= 1.0 + borrow_rate

        # French tax for the previous calendar year is paid at the start of the
        # new one (self-declared CTO, no withholding at the trade).
        if cfg.tax_country == "fr" and fr_year is not None and d.year != fr_year:
            cash -= _settle_fr(fr_year)
            fr_year = d.year

        # A missing open must be marked at the PRIOR close, never today's close:
        # using the close would leak the day's move into the open weights and make
        # the forward paper tracker's intraday leg double-count it.
        if exec_open:
            mark_open = np.where(po > 0, po, np.where(np.isnan(prev_px), pr, prev_px))
        else:
            mark_open = pr
        eq_open_pre_arr[i] = cash + float((shares * mark_open).sum())

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
            # Same mark as the reporting book: a missing open fills at the prior
            # close, not at today's close (which would be look-ahead).
            mark = mark_open
            equity_now = cash + float(shares @ mark)

            def _fill(ci: int, is_buy: bool) -> float:
                """Open fill, or the opt-in limit fill using today's range."""
                if do_limit and po[ci] > 0:
                    px, _ = limit_fill(
                        po[ci], hrow[ci], lrow[ci], pr[ci], is_buy, cfg.limit_bps
                    )
                    return px
                return float(mark[ci])

            # sell exits first
            for ci in list(held):
                if ci in pending_exit or (has_target and ci not in keep):
                    p = mark[ci]
                    if p == 0.0:
                        continue
                    fill = _fill(ci, False)
                    notional = shares[ci] * fill
                    cash += notional - _fees(notional, False, ci, d)
                    gain = _sell(ci, shares[ci], fill, d)
                    if (
                        cfg.tax_country.startswith("fr")
                        or cfg.tax_rate
                        or cfg.state_tax_rate
                    ):
                        cash -= _tax(gain, d)
                    trades.append(
                        (
                            d,
                            names[ci],
                            "SELL",
                            shares[ci],
                            float(fill),
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
                    fee_rate = _fee_rate(ci, d)
                    if delta > 0:
                        if cfg.leverage <= 1.0:
                            if cash < delta * (1.0 + fee_rate):
                                grid = (
                                    cfg.us_grid
                                    if names[ci] in cfg.us_tickers
                                    else cfg.broker_grid
                                )
                                reserve = (
                                    max((f for _, f in grid), default=0.0)
                                    / cfg.initial_capital
                                    if grid and cfg.initial_capital > 0
                                    else 0.0
                                )
                                delta = max(0.0, cash - reserve) / (1.0 + fee_rate)
                                sh = delta / p
                        else:
                            gross_now = float((shares * mark).sum())
                            headroom = cfg.leverage * equity_now - gross_now
                            affordable = max(0.0, headroom) / (1.0 + fee_rate)
                            if delta > affordable:
                                delta = affordable
                                sh = delta / p
                    if abs(delta) < 1e-12:
                        continue
                    is_buy = delta > 0
                    fill = _fill(ci, is_buy)
                    if is_buy and cfg.leverage <= 1.0:
                        # The fallback close can be above the open: cap the ticket
                        # at the actual fill price so cash cannot go negative.
                        grid = (
                            cfg.us_grid
                            if names[ci] in cfg.us_tickers
                            else cfg.broker_grid
                        )
                        reserve = (
                            max((f for _, f in grid), default=0.0) / cfg.initial_capital
                            if grid and cfg.initial_capital > 0
                            else 0.0
                        )
                        cap = max(0.0, cash - reserve) / (fill * (1.0 + fee_rate))
                        if sh > cap:
                            sh = cap
                            delta = sh * p
                    if abs(delta) < 1e-12:
                        continue
                    notional = abs(sh * fill)
                    cash -= sh * fill + _fees(notional, is_buy, ci, d)
                    if sh < 0:
                        gain = _sell(ci, -sh, fill, d)
                        if (
                            cfg.tax_country.startswith("fr")
                            or cfg.tax_rate
                            or cfg.state_tax_rate
                        ):
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
                            float(fill),
                            notional,
                            pending_reason.get(ci, "rebalance"),
                        )
                    )
                    if sh > 0:
                        if cfg.wash_sale_days and ci in wash:
                            wd, loss = wash.pop(ci)
                            if (d - wd).days <= cfg.wash_sale_days:
                                realized += loss  # disallowed loss, added back
                        if cfg.tax_lots == "hifo":
                            lots.setdefault(ci, []).append([sh, fill])
                            entry[ci] = _avg_basis(ci)
                        elif old_sh <= 0 or np.isnan(entry[ci]):
                            entry[ci] = fill
                        else:
                            entry[ci] = (entry[ci] * old_sh + fill * sh) / (old_sh + sh)
                        if old_sh <= 0 or np.isnan(peak[ci]):
                            peak[ci] = fill
                    elif shares[ci] <= 0:
                        entry[ci] = np.nan
                        peak[ci] = np.nan
            pending_target, pending_exit, pending_reason = None, set(), {}

        # executed book at the fill price, used by the forward paper tracker to split
        # overnight (previous book) from intraday (post-fill book) P&L
        eq_open_arr[i] = cash + float((shares * mark_open).sum())
        wopen[i] = (shares * mark_open) / eq_open_arr[i] if eq_open_arr[i] > 0 else 0.0

        # 2. mark to market and check trailing stops
        # A missing bar (foreign holiday, halted name) is carried at the last observed
        # price. Marking at the entry price used to invent drawdowns on every US holiday.
        prm = np.where(pr > 0, pr, last_px)
        prm = np.where(np.isnan(prm), 0.0, prm)
        vals = shares * prm
        if cfg.custody_bps:
            foreign = float(sum(vals[ci] for ci in held if names[ci] in cfg.us_tickers))
            charge = foreign * cfg.custody_bps / 1e4 / 252.0
            cash -= charge
            fees_paid += charge
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
                    cash += notional - _fees(notional, False, ci, d)
                    gain = _sell(ci, sell_sh, p, d)
                    if (
                        cfg.tax_country.startswith("fr")
                        or cfg.tax_rate
                        or cfg.state_tax_rate
                    ):
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
                    names[ci]: shares[ci] * pr[ci] / equity for ci in held if pr[ci] > 0
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
        due = (
            d in calendar_set
            if cfg.rebalance_mode == "calendar"
            else i % cfg.rebalance_days == 0
        )
        if due and i + 1 < n:
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

    unrealized_tax = 0.0
    if cfg.tax_country == "fr":
        unrealized_gain = 0.0
        for ci in held:
            p = last_px[ci]
            if shares[ci] > 0 and not np.isnan(entry[ci]) and not np.isnan(p):
                unrealized_gain += (p - entry[ci]) * shares[ci]
        carry_left = sum(x[1] for x in loss_carry)
        net = fr_realized + unrealized_gain - carry_left
        unrealized_tax = max(0.0, net) * fr_rate(
            dates[-1].year, cfg.tax_country == "fr_pea", cfg.tax_rate
        )
    elif cfg.tax_country == "fr_pea":
        # PEA: no annual tax. Social contributions are due on withdrawal, on the
        # portfolio gain (contributions are the 1.0 unit invested at the start).
        unrealized_tax = max(0.0, float(eqs[-1]) - 1.0) * fr_rate(
            dates[-1].year, True, cfg.tax_rate
        )

    equity = pd.Series(eqs, index=dates, name="equity")
    cash_s = pd.Series(cashs, index=dates, name="cash")
    tr = pd.DataFrame(
        trades,
        columns=["date", "ticker", "side", "shares", "price", "notional", "reason"],
    )
    wdf = pd.DataFrame(wmat, index=dates, columns=names)
    wdf["CASH"] = cash_s / equity
    nav = pd.DataFrame({"equity": equity, "cash": cash_s})
    wopen_df = pd.DataFrame(wopen, index=dates, columns=names)
    return Result(
        equity=equity,
        cash=cash_s,
        trades=tr,
        weights=wdf,
        nav=nav,
        weights_open=wopen_df,
        eq_open_pre=pd.Series(eq_open_pre_arr, index=dates),
        eq_open=pd.Series(eq_open_arr, index=dates),
        tax_paid=float(tax_paid),
        fees_paid=float(fees_paid),
        ftt_paid=float(ftt_paid),
        unrealized_tax=float(unrealized_tax),
    )


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
