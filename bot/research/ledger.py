"""EUR-native ledger adapter for Alpha Lab reports.

This is a research copy of the algorithm in `bot/eur_ledger.py` (branch
`v3/eur-ledger`, PR #22), kept inside `bot/research/` so this branch can report
both the headline EUR translation and the EUR tax-lot ledger without depending
on an unmerged branch. The main orchestrator should replace this with the real
module at integration time; the numbers should match within rounding.

Signals and share quantities stay USD-native. Only the accounting currency
changes: EUR cost basis at the trade-date FX, HIFO lots, 31.4% PFU on annual
net EUR gains, ten-year loss carry, 5% financing on negative EUR cash.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from bot import france


@dataclass(frozen=True)
class EurLedgerConfig:
    capital: float = 10_000.0
    tax_rate: float = 0.314
    financing_bps: float = 500.0
    spread_bps: float = 5.0
    fx_bps: float = 2.0
    broker_bps: float = 0.25
    broker_fixed_eur: float = 1.0
    loss_carry_years: int = 10


def _consume_hifo(
    lots: list[list[float]], shares: float, price: float
) -> tuple[float, float]:
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
    return notional * rate + cfg.broker_fixed_eur


def _fx_series(market: str, dates: pd.DatetimeIndex) -> pd.Series:
    fx = france.eur_per_unit(market)
    if fx is None:
        return pd.Series(1.0, index=dates, name="eur_per_unit")
    out = fx.reindex(fx.index.union(dates)).ffill().reindex(dates)
    if out.isna().any():
        raise ValueError(f"missing FX for {market} on {out.index[out.isna()][:3]}")
    return out.rename("eur_per_unit")


@dataclass
class LedgerResult:
    equity: pd.Series
    after_tax: float
    multiple: float
    cagr: float
    maxdd: float
    tax_paid: float
    exit_tax: float
    fees: float
    final_fx: float
    fx: pd.Series


def replay(
    res,
    panel: pd.DataFrame,
    market: str = "TECH",
    config: EurLedgerConfig | None = None,
) -> LedgerResult:
    cfg = config or EurLedgerConfig()
    dates = pd.DatetimeIndex(res.equity.index)
    if len(dates) == 0:
        raise ValueError("empty equity series")
    fx = _fx_series(market, dates)
    px = panel[["date", "ticker", "adjclose"]].copy()
    px["px_eur"] = (
        px["adjclose"].to_numpy(dtype=float) * fx.reindex(px["date"]).to_numpy()
    )
    pxe = px.pivot(index="date", columns="ticker", values="px_eur").sort_index().ffill()
    pxe = pxe.reindex(index=pxe.index.union(dates)).ffill().reindex(dates)

    trades = res.trades.copy()
    by_date = {}
    if len(trades):
        trades["date"] = pd.to_datetime(trades["date"])
        trades = trades.sort_values("date", kind="stable")
        by_date = {d: g for d, g in trades.groupby("date", sort=False)}

    cash = float(cfg.capital)
    lots: dict[str, list[list[float]]] = {}
    pos: dict[str, float] = {}
    last_px: dict[str, float] = {}
    realized_year = 0.0
    loss_carry: list[list[float]] = []
    year = int(dates[0].year)
    tax_paid = 0.0
    total_fees = 0.0
    eq: list[float] = []

    for d in dates:
        if int(d.year) != year:
            net = realized_year
            realized_year = 0.0
            loss_carry = [x for x in loss_carry if int(d.year) <= x[0]]
            if net > 0:
                for lot in loss_carry:
                    take = min(lot[1], net)
                    lot[1] -= take
                    net -= take
                    if net <= 1e-12:
                        break
                loss_carry = [x for x in loss_carry if x[1] > 1e-12]
                tax = max(0.0, net) * cfg.tax_rate
                cash -= tax
                tax_paid += tax
            elif net < 0:
                loss_carry.append([int(d.year) + cfg.loss_carry_years, -net])
            year = int(d.year)

        if cash < 0 and cfg.financing_bps:
            cash *= 1.0 + cfg.financing_bps / 1e4 / 252.0

        group = by_date.get(d)
        if group is not None:
            fxd = float(fx.loc[d])
            for tr in group.itertuples():
                price = float(tr.price) * fxd
                shares = float(tr.shares) * cfg.capital
                notional = shares * price
                fee = _fee(notional, cfg)
                total_fees += fee
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

        value = 0.0
        for ticker, shares in pos.items():
            if ticker not in pxe.columns:
                continue
            p = float(pxe[ticker].loc[d])
            if np.isfinite(p):
                last_px[ticker] = p
            else:
                p = last_px.get(ticker, 0.0)
            value += shares * p
        eq.append(cash + value)

    equity = pd.Series(eq, index=dates, name="equity_eur")
    unrealized = 0.0
    for ticker, shares in pos.items():
        p = last_px.get(ticker, 0.0)
        cost = sum(lot[0] * lot[1] for lot in lots.get(ticker, []))
        unrealized += shares * p - cost
    carry_left = sum(x[1] for x in loss_carry)
    net = realized_year + unrealized - carry_left
    exit_tax = max(0.0, net) * cfg.tax_rate
    final = float(equity.iloc[-1])
    after = final - exit_tax
    years = len(dates) / 252.0
    return LedgerResult(
        equity=equity,
        after_tax=after,
        multiple=after / cfg.capital,
        cagr=(after / cfg.capital) ** (1.0 / years) - 1.0,
        maxdd=float((equity / equity.cummax() - 1.0).min()),
        tax_paid=tax_paid,
        exit_tax=exit_tax,
        fees=total_fees,
        final_fx=float(fx.iloc[-1]),
        fx=fx,
    )
