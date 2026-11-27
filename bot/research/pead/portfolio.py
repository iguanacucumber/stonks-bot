"""Event-driven equal-weight portfolio with slots, cash yield and costs.

Accounting is in dollars (or fractions of initial capital): every entry gets
10% of equity at the entry open, idle cash earns BIL when enabled, and each
side pays ``cost_bps`` on traded notional. Shorts pay 500 bps/year borrow and
receive no rebate.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .events import eligible


@dataclass
class Position:
    ticker: str
    direction: int
    entry_date: pd.Timestamp
    exit_date: pd.Timestamp
    entry_price: float
    exit_close: float
    units: float
    notional: float
    gap: float


@dataclass
class PortfolioResult:
    equity: pd.Series
    daily_returns: pd.Series
    trades: pd.DataFrame
    exposure: pd.DataFrame
    n_signals: int
    n_entries: int
    skipped_slots: int
    skipped_active: int
    skipped_cash: int
    avg_positions: float
    avg_invested: float
    avg_cash: float
    max_concurrent: int
    cash_yield: float
    long_short: bool
    hold: int
    cost_bps: float
    params: dict = field(default_factory=dict)


def _bil_returns(
    bars: dict[str, pd.DataFrame], calendar: pd.DatetimeIndex
) -> pd.Series:
    frame = bars.get("BIL")
    if frame is None:
        return pd.Series(0.0, index=calendar)
    r = frame["adjclose"].pct_change()
    return r.reindex(calendar).fillna(0.0)


def _open_marks(
    active: list[Position], bars: dict[str, pd.DataFrame], date: pd.Timestamp
) -> tuple[float, float]:
    long_value = 0.0
    short_liability = 0.0
    for p in active:
        frame = bars[p.ticker]
        px = float(frame["open"].asof(date))
        if p.direction > 0:
            long_value += p.units * px
        else:
            short_liability += p.units * px
    return long_value, short_liability


def simulate(
    timing: pd.DataFrame,
    bars: dict[str, pd.DataFrame],
    *,
    hold: int = 5,
    cost_bps: float = 5.0,
    max_positions: int = 10,
    reaction: str = "gap",
    long_short: bool = False,
    max_per_side: int = 5,
    borrow_bps: float = 500.0,
    cash_yield: bool = True,
    capital: float = 1.0,
    calendar: pd.DatetimeIndex | None = None,
) -> PortfolioResult:
    """Run one pre-registered variant."""
    if hold < 1:
        raise ValueError("hold must be >= 1")
    if max_positions < 1:
        raise ValueError("max_positions must be >= 1")
    events = eligible(timing, bars, hold=hold, reaction=reaction)
    if events.empty:
        empty = pd.Series(dtype=float)
        return PortfolioResult(
            equity=empty,
            daily_returns=empty,
            trades=pd.DataFrame(),
            exposure=pd.DataFrame(),
            n_signals=0,
            n_entries=0,
            skipped_slots=0,
            skipped_active=0,
            skipped_cash=0,
            avg_positions=0.0,
            avg_invested=0.0,
            avg_cash=0.0,
            max_concurrent=0,
            cash_yield=0.0,
            long_short=long_short,
            hold=hold,
            cost_bps=cost_bps,
            params={"reaction": reaction, "long_short": long_short},
        )
    if calendar is None:
        calendars = [set(bars[t].index) for t in events["ticker"].unique() if t in bars]
        calendar = pd.DatetimeIndex(sorted(set.union(*calendars)))
    calendar = pd.DatetimeIndex(calendar).sort_values()
    first = events["entry_date"].min()
    last = max(
        bars[row.ticker].index[row.entry_idx + hold - 1] for row in events.itertuples()
    )
    dates = calendar[(calendar >= first) & (calendar <= last)]
    if len(dates) == 0:
        raise ValueError("empty portfolio calendar")
    bil = (
        _bil_returns(bars, dates)
        if cash_yield and not long_short
        else pd.Series(0.0, index=dates)
    )

    signals = events.copy()
    # Longs strongest gap first; shorts most negative gap first.
    signals["_rank"] = np.where(long_short, signals["gap"].abs(), signals["gap"])
    signals = signals.sort_values(["_rank", "ticker"], ascending=[False, True])
    by_date: dict[pd.Timestamp, list] = {}
    for row in signals.itertuples(index=False):
        exit_date = bars[row.ticker].index[row.entry_idx + hold - 1]
        by_date.setdefault(row.entry_date, []).append((row, exit_date))

    cash = float(capital)
    active: list[Position] = []
    trades: list[dict] = []
    equity_rows: list[dict] = []
    n_signals = 0
    skipped_slots = 0
    skipped_active = 0
    skipped_cash = 0
    max_concurrent = 0
    cash_interest = 0.0

    for date in dates:
        # 1) cash earns T-bill return over the session
        if cash > 0 and bil.get(date, 0.0):
            interest = cash * float(bil.get(date, 0.0))
            cash += interest
            cash_interest += interest

        # 2) entries at the open
        candidates = by_date.get(date, [])
        if candidates:
            long_open, short_open = _open_marks(active, bars, date)
            equity_open = cash + long_open - short_open
            for row, exit_date in candidates:
                n_signals += 1
                if any(p.ticker == row.ticker for p in active):
                    skipped_active += 1
                    continue
                if long_short:
                    side = 1 if row.gap > 0 else -1
                    side_count = sum(1 for p in active if p.direction == side)
                    if side_count >= max_per_side:
                        skipped_slots += 1
                        continue
                else:
                    side = 1
                    if len(active) >= max_positions:
                        skipped_slots += 1
                        continue
                alloc = max(0.0, equity_open / float(max_positions))
                cost_rate = cost_bps / 1e4
                notional = (
                    alloc / (1.0 + cost_rate) if side > 0 else alloc / (1.0 - cost_rate)
                )
                cost = notional * cost_rate
                if side > 0 and cash < notional + cost:
                    # Use the remaining cash for the last slot instead of
                    # silently running an effective 9-position book.
                    if cash <= 0:
                        skipped_cash += 1
                        continue
                    notional = cash / (1.0 + cost_rate)
                    cost = notional * cost_rate
                if notional <= 0:
                    skipped_cash += 1
                    continue
                entry_px = float(bars[row.ticker]["open"].asof(date))
                if not np.isfinite(entry_px) or entry_px <= 0:
                    skipped_cash += 1
                    continue
                units = notional / entry_px
                if side > 0:
                    cash -= notional + cost
                else:
                    cash += notional - cost
                active.append(
                    Position(
                        ticker=row.ticker,
                        direction=side,
                        entry_date=date,
                        exit_date=pd.Timestamp(exit_date),
                        entry_price=entry_px,
                        exit_close=float("nan"),
                        units=units,
                        notional=notional,
                        gap=float(row.gap),
                    )
                )
                if len(active) > max_concurrent:
                    max_concurrent = len(active)

        # 3) mark to close, pay borrow on shorts, exit at the close
        positions_today = len(active)
        long_value = 0.0
        short_liability = 0.0
        still: list[Position] = []
        for p in active:
            close_px = float(bars[p.ticker]["close"].asof(date))
            if p.direction < 0:
                daily_borrow = p.notional * borrow_bps / 1e4 / 252.0
                cash -= daily_borrow
            if date >= p.exit_date:
                p.exit_close = close_px
                exit_cost_bps = cost_bps / 1e4
                if p.direction > 0:
                    proceeds = p.units * close_px * (1.0 - exit_cost_bps)
                    cash += proceeds
                    pnl = proceeds - p.notional - p.notional * cost_bps / 1e4
                else:
                    buyback = p.units * close_px * (1.0 + exit_cost_bps)
                    cash -= buyback
                    pnl = p.notional - buyback - p.notional * cost_bps / 1e4
                trades.append(
                    {
                        "ticker": p.ticker,
                        "direction": "long" if p.direction > 0 else "short",
                        "entry_date": p.entry_date,
                        "exit_date": date,
                        "entry_price": p.entry_price,
                        "exit_price": close_px,
                        "gap": p.gap,
                        "alloc": p.notional,
                        "pnl": pnl,
                        "return": pnl / p.notional if p.notional else np.nan,
                    }
                )
            else:
                if p.direction > 0:
                    long_value += p.units * close_px
                else:
                    short_liability += p.units * close_px
                still.append(p)
        active = still
        equity = cash + long_value - short_liability
        invested = long_value + short_liability
        equity_rows.append(
            {
                "date": date,
                "equity": equity,
                "cash": cash,
                "long_value": long_value,
                "short_liability": short_liability,
                "invested": invested,
                "positions": positions_today,
            }
        )

    equity = pd.Series(
        [r["equity"] for r in equity_rows],
        index=pd.DatetimeIndex([r["date"] for r in equity_rows]),
        name="equity",
    )
    daily = equity.pct_change()
    daily.iloc[0] = equity.iloc[0] / capital - 1.0
    exposure = pd.DataFrame(equity_rows).set_index("date")
    exposure["invested_share"] = exposure["invested"] / exposure["equity"]
    exposure["cash_share"] = exposure["cash"] / exposure["equity"]
    trades_df = pd.DataFrame(trades)
    return PortfolioResult(
        equity=equity,
        daily_returns=daily.fillna(0.0),
        trades=trades_df,
        exposure=exposure,
        n_signals=n_signals,
        n_entries=len(trades_df),
        skipped_slots=skipped_slots,
        skipped_active=skipped_active,
        skipped_cash=skipped_cash,
        avg_positions=float(exposure["positions"].mean()),
        avg_invested=float(exposure["invested_share"].mean()),
        avg_cash=float(exposure["cash_share"].mean()),
        max_concurrent=int(exposure["positions"].max()) if len(exposure) else 0,
        cash_yield=float(cash_interest / capital),
        long_short=long_short,
        hold=hold,
        cost_bps=cost_bps,
        params={"reaction": reaction, "long_short": long_short},
    )


def event_returns(
    timing: pd.DataFrame,
    bars: dict[str, pd.DataFrame],
    *,
    hold: int = 5,
    cost_bps: float = 5.0,
    reaction: str = "gap",
    long_short: bool = False,
    borrow_bps: float = 500.0,
) -> pd.DataFrame:
    """Unconditional event-level returns, before portfolio slot restrictions."""
    events = eligible(timing, bars, hold=hold, reaction=reaction)
    rows = []
    for row in events.itertuples(index=False):
        frame = bars[row.ticker]
        exit_idx = row.entry_idx + hold - 1
        exit_px = float(frame["close"].iloc[exit_idx])
        if long_short and row.gap < 0:
            gross = row.entry_open / exit_px - 1.0
            borrow = borrow_bps / 1e4 * (hold / 252.0)
            net = gross - 2.0 * cost_bps / 1e4 - borrow
            direction = "short"
        else:
            gross = exit_px / row.entry_open - 1.0
            net = gross - 2.0 * cost_bps / 1e4
            direction = "long"
        rows.append(
            {
                "ticker": row.ticker,
                "entry_date": row.entry_date,
                "exit_date": frame.index[exit_idx],
                "gap": row.gap,
                "direction": direction,
                "gross": gross,
                "net": net,
            }
        )
    return pd.DataFrame(rows)


def buy_hold(
    bars: dict[str, pd.DataFrame],
    symbol: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
    *,
    cost_bps: float = 5.0,
) -> pd.Series:
    """Buy at the start open, sell at the end close; daily total returns."""
    frame = bars[symbol].loc[
        (bars[symbol].index >= start) & (bars[symbol].index <= end)
    ]
    if len(frame) < 2:
        raise ValueError(f"not enough {symbol} bars for benchmark")
    entry = float(frame["open"].iloc[0])
    eq = frame["close"] / entry
    eq = eq * (1.0 - cost_bps / 1e4)
    eq.iloc[-1] *= 1.0 - cost_bps / 1e4
    r = eq.pct_change()
    r.iloc[0] = eq.iloc[0] - 1.0
    return r.fillna(0.0)
