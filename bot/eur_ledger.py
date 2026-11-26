"""EUR-native accounting for USD-denominated strategies.

The backtest engine works in the asset currency (USD for US/TECH). A French
investor's wealth and tax base are EUR. Translating only the terminal equity at
spot ignores two things:

1. annual French tax is assessed on EUR gains, including FX gains/losses;
2. the EUR cost basis of each lot is set at the trade-date FX.

This module replays the engine's trades in EUR. Signals and share quantities stay
USD-native (that is what the bot actually sees); only the accounting currency
changes. It is a reporting/validation layer, not a trading strategy.

French PFU is year-dependent: 30% through 2025 and 31.4% from 2026, matching
`bot.backtest.fr_rate`. Dividends use the same schedule, with the 15% US treaty
withholding credited against the PFU.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable

import numpy as np
import pandas as pd

from . import france
from .fx_hedge import (
    FxHedgeConfig,
    HedgePeriod,
    forward_rate,
    hedge_notional,
    hedge_pnl_eur,
    month_end_dates,
    ratio_for_date,
    roll_cost_eur,
)


def french_rate(year: int) -> float:
    """French CTO rate by realization year, matching `backtest.fr_rate`."""
    if year < 2018:
        return 0.345  # 19% IR + 15.5% social, pre-PFU
    if year < 2026:
        return 0.30  # PFU 12.8% + 17.2%
    return 0.314  # PFU 12.8% + 18.6% from 2026


@dataclass(frozen=True)
class EurLedgerConfig:
    capital: float = 10_000.0
    # None = year-dependent French rate (30% through 2025, 31.4% from 2026).
    # A float forces a flat stressed rate for sensitivity tests.
    tax_rate: float | None = None
    financing_bps: float = 500.0
    spread_bps: float = 5.0
    fx_bps: float = 2.0
    broker_bps: float = 0.25
    broker_fixed_eur: float = 1.0
    loss_carry_years: int = 10
    # Dividend treatment. Adjusted closes already reinvest the gross distribution,
    # so the default replay only charges withholding/PFU and leaves the gross
    # dividend inside the total-return position. Because adjusted closes also make
    # the reinvested dividend look like future price gain, the same gross amount is
    # added to the lot basis to avoid taxing it twice. Set dividend_reinvest=False
    # to move the gross amount to cash as an accounting transformation (see report).
    withholding_rate: float = 0.15
    # None -> year-dependent French PFU (30% through 2025, 31.4% from 2026),
    # unless tax_rate forces a flat stressed rate. A float overrides only dividends.
    dividend_tax_rate: float | None = None
    dividend_reinvest: bool = True
    wrapper: str = "cto"  # "cto" | "pea"; PEA skips US dividend tax
    # Multiplier on every trading fee (spread + FX + broker + fixed ticket).
    # 2.0 is the 2x-cost sensitivity; it does not change the market price.
    fee_multiplier: float = 1.0

    def __post_init__(self) -> None:
        if not 0.0 <= float(self.withholding_rate) < 1.0:
            raise ValueError("withholding_rate must be in [0, 1)")
        if self.dividend_tax_rate is not None and not (
            0.0 <= float(self.dividend_tax_rate) < 1.0
        ):
            raise ValueError("dividend_tax_rate must be None or in [0, 1)")
        if str(self.wrapper).lower() not in ("cto", "pea"):
            raise ValueError("wrapper must be 'cto' or 'pea'")
        if not 0.0 <= float(self.fee_multiplier):
            raise ValueError("fee_multiplier must be >= 0")


@dataclass
class EurLedgerResult:
    equity: pd.Series
    cash: pd.Series
    fx: pd.Series
    trades: pd.DataFrame
    fees: float
    tax_paid: float
    exit_tax: float
    after_tax: float
    multiple: float
    cagr: float
    maxdd: float
    unrealized_gain: float
    realized_current_year: float
    loss_carry: float
    reconcile_gap: float
    config: EurLedgerConfig = field(default_factory=EurLedgerConfig)
    dividends_gross: float = 0.0
    dividend_withholding: float = 0.0
    dividend_tax_credit: float = 0.0
    dividend_tax_topup: float = 0.0
    dividend_tax_total: float = 0.0
    dividends_net: float = 0.0
    dividend_events: int = 0
    dividend_basis_added: float = 0.0
    dividend_reinvest: bool = True
    harvest_trades: pd.DataFrame = field(default_factory=pd.DataFrame)
    harvest_notional: float = 0.0
    harvest_fees: float = 0.0
    harvest_count: int = 0
    fx_hedge_pnl: float = 0.0
    fx_hedge_cost: float = 0.0
    fx_hedge_periods: int = 0
    fx_hedge_avg_notional: float = 0.0
    # Covered-call overlay (model): cash premium/payoff and short-call liability.
    option_premium_eur: float = 0.0
    option_payoff_eur: float = 0.0
    option_pnl_eur: float = 0.0
    option_liability_eur: float = 0.0
    option_rolls: int = 0
    option_open_premium_eur: float = 0.0
    fx_hedge_log: pd.DataFrame = field(
        default_factory=lambda: pd.DataFrame(
            columns=[
                "start",
                "expiry",
                "ratio",
                "notional_usd",
                "spot_start",
                "forward",
                "spot_end",
                "roll_cost_eur",
                "pnl_eur",
            ]
        )
    )


def _pfu_rate(cfg: EurLedgerConfig, year: int) -> float:
    """Dividend PFU rate: explicit override, forced flat rate, or by year."""
    if cfg.dividend_tax_rate is not None:
        return float(cfg.dividend_tax_rate)
    if cfg.tax_rate is not None:
        return float(cfg.tax_rate)
    return french_rate(year)


def _consume_hifo(
    lots: list[list[float]], shares: float, price: float
) -> tuple[float, float]:
    """Sell highest-basis lots first. Returns (realized gain, shares sold)."""
    lots.sort(key=lambda lot: -lot[1])
    remaining, gain = shares, 0.0
    while remaining > 1e-12 and lots:
        take = min(lots[0][0], remaining)
        gain += (price - lots[0][1]) * take
        lots[0][0] -= take
        remaining -= take
        if lots[0][0] <= 1e-12:
            lots.pop(0)
    return gain, shares - remaining


def _fee(notional: float, cfg: EurLedgerConfig) -> float:
    if notional <= 0:
        return 0.0
    rate = (cfg.spread_bps + cfg.fx_bps + cfg.broker_bps) / 1e4
    return (notional * rate + cfg.broker_fixed_eur) * float(cfg.fee_multiplier)


def _fx_series(market: str, dates: pd.DatetimeIndex) -> pd.Series:
    fx = france.eur_per_unit(market)
    if fx is None:
        return pd.Series(1.0, index=dates, name="eur_per_unit")
    out = fx.reindex(fx.index.union(dates)).ffill().reindex(dates)
    if out.isna().any():
        raise ValueError(f"missing FX for {market} on {out.index[out.isna()][:3]}")
    return out.rename("eur_per_unit")


def _dividend_map(
    dividends: pd.DataFrame | None,
    dates: pd.DatetimeIndex,
) -> dict[pd.Timestamp, list[tuple[str, float]]]:
    """Map ex-dates to the first ledger session on/after each event.

    The schedule is native-currency gross amount per current share. A weekend
    ex-date is applied at the next session; an event outside the replay window
    is ignored. Duplicate date/ticker rows keep the last value, never sum.
    """
    if dividends is None or len(dividends) == 0 or len(dates) == 0:
        return {}
    df = pd.DataFrame(dividends).copy()
    date_col = next(
        (c for c in ("date", "ex_date", "ex-date") if c in df.columns), None
    )
    if date_col is None or "ticker" not in df.columns or "amount" not in df.columns:
        raise ValueError("dividends need date/ex_date, ticker and amount columns")
    df["date"] = pd.to_datetime(df[date_col]).dt.normalize()
    df["ticker"] = df["ticker"].astype(str)
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce")
    df = df.dropna(subset=["date", "ticker", "amount"])
    df = df[np.isfinite(df["amount"]) & (df["amount"] > 0)]
    df = df.drop_duplicates(["date", "ticker"], keep="last")
    if df.empty:
        return {}
    first, last = dates[0], dates[-1]
    df = df[(df["date"] >= first) & (df["date"] <= last)]
    if df.empty:
        return {}
    index = dates.searchsorted(df["date"].to_numpy(), side="left")
    out: dict[pd.Timestamp, list[tuple[str, float]]] = {}
    for session, ticker, amount in zip(
        dates[index], df["ticker"].to_numpy(), df["amount"].to_numpy()
    ):
        out.setdefault(pd.Timestamp(session), []).append((str(ticker), float(amount)))
    return out


def _reduce_lots(lots: list[list[float]], sold: float, shares: float) -> None:
    """Scale lots when a synthetic cash distribution removes shares."""
    if shares <= 0 or sold <= 0:
        return
    keep = max(0.0, (shares - min(sold, shares)) / shares)
    for lot in lots:
        lot[0] *= keep
    lots[:] = [lot for lot in lots if lot[0] > 1e-12]


HarvestDecision = Callable[
    [pd.Timestamp, dict[str, float], dict[str, list[list[float]]], pd.Series, float],
    list[dict],
]


def replay(
    res,
    panel: pd.DataFrame,
    market: str,
    config: EurLedgerConfig | None = None,
    dividends: pd.DataFrame | None = None,
    fx_hedge: FxHedgeConfig | None = None,
    harvest: HarvestDecision | None = None,
    harvest_dates: Iterable[pd.Timestamp] | None = None,
    option_overlay: pd.DataFrame | None = None,
) -> EurLedgerResult:
    """Replay `res.trades` in EUR with HIFO lots, PFU and margin interest.

    `res` is a `bot.backtest.Result` (or any object with `.equity` and `.trades`).
    `panel` is the native long panel used by the engine (date, ticker, adjclose).
    `dividends` is an optional long frame (date/ex_date, ticker, amount) of gross
    native-currency dividends per current share. Because adjusted closes already
    reinvest gross dividends, the default only deducts dividend taxes. With
    `cfg.dividend_reinvest=False`, the gross amount is also moved from the
    position to cash by a synthetic share reduction (no fee, no realized gain).

    `harvest` is an optional callback for tax-loss harvesting. It receives the
    session, current EUR positions, HIFO lots, the session's EUR open prices and
    cash, and returns synthetic trades (``ticker``, ``side``, ``shares`` in
    ledger units, ``price`` in EUR). It runs before the engine's own trades on
    that session. `harvest_dates` limits the callback to the supplied sessions.

    `option_overlay` is an optional daily frame with native-currency columns
    ``cash_native`` (premium net of commission at a roll, minus payoff at expiry),
    ``liability_native`` (positive mark of the short call), ``pnl_native``
    (taxable option P&L at expiry), ``premium_native``/``payoff_native`` for
    reporting, and ``open_premium_native`` (premium still open at the end).
    It is a model feed from ``bot.covered_calls``; no option quotes are assumed.
    """
    cfg = config or EurLedgerConfig()
    dates = pd.DatetimeIndex(res.equity.index)
    if len(dates) == 0:
        raise ValueError("empty equity series")
    fx = _fx_series(market, dates)
    opt = None
    if option_overlay is not None:
        need = {
            "cash_native",
            "liability_native",
            "pnl_native",
            "premium_native",
            "payoff_native",
            "open_premium_native",
        }
        missing = need - set(option_overlay.columns)
        if missing:
            raise ValueError(f"option overlay missing columns: {sorted(missing)}")
        opt = option_overlay.copy()
        opt.index = pd.to_datetime(opt.index).normalize()
        opt = opt.reindex(dates).fillna(0.0)
    div_by_date = (
        {} if str(cfg.wrapper).lower() == "pea" else _dividend_map(dividends, dates)
    )

    px = panel[["date", "ticker", "adjclose"]].copy()
    px["px_eur"] = (
        px["adjclose"].to_numpy(dtype=float) * fx.reindex(px["date"]).to_numpy()
    )
    pxe = px.pivot(index="date", columns="ticker", values="px_eur").sort_index().ffill()
    # only need the dates we mark
    pxe = pxe.reindex(index=pxe.index.union(dates)).ffill().reindex(dates)
    pxu = (
        panel[["date", "ticker", "adjclose"]]
        .pivot(index="date", columns="ticker", values="adjclose")
        .sort_index()
        .ffill()
        .reindex(dates)
    )
    roll_dates = month_end_dates(dates) if fx_hedge is not None else []
    next_roll = {
        pd.Timestamp(a): pd.Timestamp(b)
        for a, b in zip(roll_dates[:-1], roll_dates[1:])
    }
    pxo = None
    if harvest is not None and "open" in panel.columns:
        po = panel[["date", "ticker", "open"]].copy()
        po["px_eur"] = (
            pd.to_numeric(po["open"], errors="coerce").to_numpy(dtype=float)
            * fx.reindex(po["date"]).to_numpy()
        )
        pxo = (
            po.pivot(index="date", columns="ticker", values="px_eur")
            .sort_index()
            .ffill()
            .reindex(index=dates)
            .ffill()
        )
    hdates: set[pd.Timestamp] | None = None
    if harvest_dates is not None:
        hdates = {pd.Timestamp(x).normalize() for x in harvest_dates}

    trades = res.trades.copy()
    if len(trades):
        trades["date"] = pd.to_datetime(trades["date"])
        trades = trades.sort_values("date", kind="stable")
        by_date = {d: g for d, g in trades.groupby("date", sort=False)}
    else:
        by_date = {}
    fees_out: list[float] = []

    cash = float(cfg.capital)
    lots: dict[str, list[list[float]]] = {}
    pos: dict[str, float] = {}
    last_px: dict[str, float] = {}
    realized_year = 0.0
    loss_carry: list[list[float]] = []
    year = int(dates[0].year)
    tax_paid = 0.0
    total_fees = 0.0
    dividends_gross = 0.0
    dividend_withholding = 0.0
    dividend_tax_credit = 0.0
    dividend_tax_topup = 0.0
    dividend_tax_total = 0.0
    dividend_events = 0
    dividend_basis_added = 0.0
    hedge_period: HedgePeriod | None = None
    hedge_logs: list[dict] = []
    fx_hedge_pnl = 0.0
    fx_hedge_cost = 0.0
    fx_hedge_periods = 0
    fx_hedge_notional_days = 0.0
    fx_hedge_days = 0
    option_premium_eur = 0.0
    option_payoff_eur = 0.0
    option_pnl_eur = 0.0
    option_liability_eur = 0.0
    option_open_premium_eur = 0.0
    option_rolls = 0
    harvest_rows: list[dict] = []
    harvest_notional = 0.0
    harvest_fees = 0.0
    eq: list[float] = []
    cashs: list[float] = []

    for d in dates:
        if int(d.year) != year:
            net = realized_year
            realized_year = 0.0
            loss_carry = [x for x in loss_carry if int(d.year) <= x[0]]
            if net > 0:
                for x in loss_carry:
                    take = min(x[1], net)
                    x[1] -= take
                    net -= take
                    if net <= 1e-12:
                        break
                loss_carry = [x for x in loss_carry if x[1] > 1e-12]
                rate = (
                    cfg.tax_rate
                    if cfg.tax_rate is not None
                    else french_rate(year)  # year the gain was realized
                )
                tax = max(0.0, net) * rate
                cash -= tax
                tax_paid += tax
            elif net < 0:
                loss_carry.append([int(d.year) + cfg.loss_carry_years, -net])
            year = int(d.year)

        if cash < 0 and cfg.financing_bps:
            cash *= 1.0 + cfg.financing_bps / 1e4 / 252.0

        # Dividends are known before the ex-date open. Process them before the
        # day's fills: a seller on the ex-date still receives the distribution,
        # a buyer on the ex-date does not.
        for ticker, amount in div_by_date.get(d, ()):
            shares = float(pos.get(ticker, 0.0))
            if shares <= 1e-12:
                continue
            fxd = float(fx.loc[d])
            gross = shares * float(amount) * fxd
            if not np.isfinite(gross) or gross <= 0:
                continue
            withholding = gross * float(cfg.withholding_rate)
            pfu = gross * _pfu_rate(cfg, int(d.year))
            credit = min(withholding, pfu)
            topup = max(0.0, pfu - credit)
            tax = withholding + topup
            if not cfg.dividend_reinvest:
                # Adjusted closes already reinvest the gross amount. Move it to
                # cash by removing the equivalent position value at the prior
                # mark. This is bookkeeping, not a taxable sale: no fee and no
                # realized gain is created.
                px = last_px.get(ticker)
                if px is None or px <= 0:
                    px = float(pxe[ticker].loc[d])
                if np.isfinite(px) and px > 0:
                    sold = min(shares, gross / px)
                    _reduce_lots(lots.setdefault(ticker, []), sold, shares)
                    cash += sold * px
                    pos[ticker] = shares - sold
                    if pos[ticker] <= 1e-12:
                        pos.pop(ticker, None)
            cash -= tax
            if cfg.dividend_reinvest:
                # Adjusted closes already reinvest the gross distribution. Raise
                # the tax basis by the same gross amount so a later sale does not
                # tax the reinvested dividend again as a capital gain.
                ticker_lots = lots.get(ticker, [])
                lot_shares = sum(float(lot[0]) for lot in ticker_lots)
                if lot_shares > 1e-12:
                    add = gross / lot_shares
                    for lot in ticker_lots:
                        lot[1] += add
                    dividend_basis_added += gross
            dividends_gross += gross
            dividend_withholding += withholding
            dividend_tax_credit += credit
            dividend_tax_topup += topup
            dividend_tax_total += tax
            dividend_events += 1

        # Tax-loss harvesting runs before the engine's own fills on the same
        # session: sell a losing lot, immediately rebuy if requested, then let
        # the normal target rebalance continue. The callback sees the live EUR
        # lots, so its threshold and band use the same basis as the tax replay.
        if harvest is not None and (hdates is None or d.normalize() in hdates):
            price_row = pxo.loc[d] if pxo is not None else pxe.loc[d]
            synthetic = harvest(d, pos, lots, price_row, cash) or []
            for tr in synthetic:
                ticker = str(tr["ticker"])
                side = str(tr["side"]).upper()
                shares = float(tr["shares"])
                price = float(tr["price"])
                if shares <= 1e-12 or not np.isfinite(price) or price <= 0:
                    continue
                notional = shares * price
                fee = _fee(notional, cfg)
                total_fees += fee
                harvest_fees += fee
                harvest_notional += notional
                ls = lots.setdefault(ticker, [])
                if side == "BUY":
                    cash -= notional + fee
                    ls.append([shares, (notional + fee) / shares])
                    pos[ticker] = pos.get(ticker, 0.0) + shares
                else:
                    held = float(pos.get(ticker, 0.0))
                    if held <= 1e-12:
                        continue
                    shares = min(shares, held)
                    notional = shares * price
                    gain, sold = _consume_hifo(ls, shares, price)
                    cash += sold * price - fee
                    realized_year += gain - fee
                    pos[ticker] = max(0.0, pos.get(ticker, 0.0) - sold)
                    if pos[ticker] <= 1e-12:
                        pos.pop(ticker, None)
                harvest_rows.append(
                    {
                        "date": d,
                        "ticker": ticker,
                        "side": side,
                        "shares": shares,
                        "price": price,
                        "notional": notional,
                        "reason": "harvest",
                        "fee_eur": fee,
                    }
                )

        group = by_date.get(d)
        if group is not None:
            fxd = float(fx.loc[d])
            for tr in group.itertuples():
                price = float(tr.price) * fxd
                shares = float(tr.shares) * cfg.capital
                notional = shares * price
                fee = _fee(notional, cfg)
                total_fees += fee
                fees_out.append(fee)
                ls = lots.setdefault(tr.ticker, [])
                if tr.side == "BUY":
                    cash -= notional + fee
                    ls.append([shares, (notional + fee) / shares])
                    pos[tr.ticker] = pos.get(tr.ticker, 0.0) + shares
                else:
                    gain, sold = _consume_hifo(ls, shares, price)
                    cash += sold * price - fee
                    realized_year += gain - fee
                    pos[tr.ticker] = pos.get(tr.ticker, 0.0) - sold
                    if pos[tr.ticker] <= 1e-12:
                        pos.pop(tr.ticker, None)

        spot = float(fx.loc[d])
        if hedge_period is not None and d >= hedge_period.expiry:
            pnl = hedge_pnl_eur(hedge_period.notional_usd, hedge_period.forward, spot)
            cash += pnl
            # CTO netting: hedge P&L and its roll cost enter the same annual
            # capital-gains bucket as the equity lots; a loss carries forward.
            realized_year += pnl - hedge_period.roll_cost_eur
            fx_hedge_pnl += pnl
            fx_hedge_cost += hedge_period.roll_cost_eur
            fx_hedge_periods += 1
            hedge_logs.append(
                {
                    "start": hedge_period.start,
                    "expiry": hedge_period.expiry,
                    "ratio": hedge_period.ratio,
                    "notional_usd": hedge_period.notional_usd,
                    "spot_start": hedge_period.spot_start,
                    "forward": hedge_period.forward,
                    "spot_end": spot,
                    "roll_cost_eur": hedge_period.roll_cost_eur,
                    "pnl_eur": pnl,
                }
            )
            hedge_period = None

        value = 0.0
        usd_value = 0.0
        for ticker, shares in pos.items():
            if ticker not in pxe.columns:
                continue
            p = float(pxe[ticker].loc[d])
            if np.isfinite(p):
                last_px[ticker] = p
            else:
                p = last_px.get(ticker, 0.0)
            value += shares * p
            if ticker in pxu.columns:
                pu = float(pxu[ticker].loc[d])
                if np.isfinite(pu) and pu > 0:
                    usd_value += shares * pu

        if fx_hedge is not None and d in next_roll and usd_value > 0 and d < dates[-1]:
            ratio = ratio_for_date(fx.loc[:d], fx_hedge)
            notional = hedge_notional(usd_value, ratio)
            if notional > 0:
                days = int((next_roll[d] - d).days)
                fwd = forward_rate(spot, fx_hedge.carry_bps, days, fx_hedge.day_count)
                cost = roll_cost_eur(notional, spot, fx_hedge.roll_bps)
                cash -= cost
                fx_hedge_cost += cost
                hedge_period = HedgePeriod(
                    start=d,
                    expiry=next_roll[d],
                    ratio=ratio,
                    notional_usd=notional,
                    spot_start=spot,
                    forward=fwd,
                    roll_cost_eur=cost,
                )
                fx_hedge_notional_days += notional * days
                fx_hedge_days += days

        if opt is not None:
            row = opt.loc[d]
            fxd = float(fx.loc[d])
            cash_native = float(row["cash_native"])
            pnl_native = float(row["pnl_native"])
            premium_native = float(row["premium_native"])
            payoff_native = float(row["payoff_native"])
            option_liability_eur = float(row["liability_native"]) * fxd
            option_open_premium_eur = float(row["open_premium_native"]) * fxd
            if cash_native:
                cash += cash_native * fxd
            if premium_native:
                option_premium_eur += premium_native * fxd
                option_rolls += 1
            if payoff_native:
                option_payoff_eur += payoff_native * fxd
            if pnl_native:
                # Listed-option gains/losses enter the same annual CTO netting
                # bucket as equity realizations; the yearly PFU loop applies.
                pnl_eur = pnl_native * fxd
                realized_year += pnl_eur
                option_pnl_eur += pnl_eur

        eq.append(cash + value - option_liability_eur)
        cashs.append(cash)

    equity = pd.Series(eq, index=dates, name="equity_eur")
    cash_s = pd.Series(cashs, index=dates, name="cash_eur")

    unrealized = 0.0
    for ticker, shares in pos.items():
        p = last_px.get(ticker, 0.0)
        cost = sum(lot[0] * lot[1] for lot in lots.get(ticker, []))
        unrealized += shares * p - cost
    carry_left = sum(x[1] for x in loss_carry)
    option_unrealized = option_open_premium_eur - option_liability_eur
    net = realized_year + unrealized + option_unrealized - carry_left
    exit_rate = (
        cfg.tax_rate if cfg.tax_rate is not None else french_rate(int(dates[-1].year))
    )
    exit_tax = max(0.0, net) * exit_rate
    final = float(equity.iloc[-1])
    after = final - exit_tax
    years = len(dates) / 252.0
    native = float(res.equity.iloc[-1]) * cfg.capital
    reconcile_gap = after - native * float(fx.iloc[-1])

    trades_out = trades.copy()
    if len(trades_out):
        trades_out["fee_eur"] = fees_out[: len(trades_out)]
    return EurLedgerResult(
        equity=equity,
        cash=cash_s,
        fx=fx,
        trades=trades_out,
        fees=total_fees,
        tax_paid=tax_paid,
        exit_tax=exit_tax,
        after_tax=after,
        multiple=after / cfg.capital,
        cagr=(after / cfg.capital) ** (1.0 / years) - 1.0,
        maxdd=float((equity / equity.cummax() - 1.0).min()),
        unrealized_gain=unrealized,
        realized_current_year=realized_year,
        loss_carry=carry_left,
        reconcile_gap=reconcile_gap,
        config=cfg,
        dividends_gross=dividends_gross,
        dividend_withholding=dividend_withholding,
        dividend_tax_credit=dividend_tax_credit,
        dividend_tax_topup=dividend_tax_topup,
        dividend_tax_total=dividend_tax_total,
        dividends_net=dividends_gross - dividend_tax_total,
        dividend_events=dividend_events,
        dividend_basis_added=dividend_basis_added,
        dividend_reinvest=bool(cfg.dividend_reinvest),
        fx_hedge_pnl=fx_hedge_pnl,
        fx_hedge_cost=fx_hedge_cost,
        fx_hedge_periods=fx_hedge_periods,
        fx_hedge_avg_notional=(
            fx_hedge_notional_days / fx_hedge_days if fx_hedge_days else 0.0
        ),
        fx_hedge_log=pd.DataFrame(hedge_logs),
        option_premium_eur=option_premium_eur,
        option_payoff_eur=option_payoff_eur,
        option_pnl_eur=option_pnl_eur,
        option_liability_eur=option_liability_eur,
        option_rolls=option_rolls,
        option_open_premium_eur=option_open_premium_eur,
        harvest_trades=(
            pd.DataFrame(harvest_rows)
            if harvest_rows
            else pd.DataFrame(
                columns=[
                    "date",
                    "ticker",
                    "side",
                    "shares",
                    "price",
                    "notional",
                    "reason",
                    "fee_eur",
                ]
            )
        ),
        harvest_notional=harvest_notional,
        harvest_fees=harvest_fees,
        harvest_count=len(harvest_rows),
    )
