"""Intraday bar loading and simple strategy simulators.

Signal is taken from a completed bar; entry is the next bar's open. The
session filter is 09:30-16:00 New York. These are research simulators, not
execution adapters.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from bot.backtest import fr_rate

ROOT = Path(__file__).resolve().parents[3]
CACHE = ROOT / "cache" / "intraday"


def load_bars(symbol: str, interval: str, range_: str) -> pd.DataFrame:
    path = CACHE / f"{symbol}_{interval}_{range_}.csv"
    if not path.exists():
        raise FileNotFoundError(path)
    df = pd.read_csv(path)
    if df.empty:
        return df
    ts = pd.to_datetime(df["ts"], utc=True).dt.tz_convert("America/New_York")
    ts = ts.dt.tz_localize(None)
    df["ts"] = ts
    df["session"] = ts.dt.normalize()
    df = df[(ts.dt.time >= pd.Timestamp("09:30").time()) & (ts.dt.time <= pd.Timestamp("16:00").time())]
    df = df.dropna(subset=["open", "close"]).sort_values("ts")
    df = df.drop_duplicates("ts", keep="last").reset_index(drop=True)
    return df


def _tax_equity(daily: pd.Series) -> tuple[pd.Series, float]:
    """Annual French PFU on net daily P&L, ten-year loss carry."""
    eq = np.empty(len(daily))
    equity = 1.0
    year = int(daily.index[0].year)
    year_start = equity
    carry: list[list[float]] = []
    tax = 0.0
    for i, (d, r) in enumerate(daily.items()):
        if int(d.year) != year:
            nr = equity / year_start - 1.0
            carry = [x for x in carry if year <= x[0]]
            if nr > 0:
                n = nr
                for lot in carry:
                    take = min(lot[1], n)
                    lot[1] -= take
                    n -= take
                carry = [x for x in carry if x[1] > 1e-12]
                t = max(0.0, n) * fr_rate(year)
                equity -= t * year_start
                tax += t * year_start
            elif nr < 0:
                carry.append([year + 10, -nr])
            year = int(d.year)
            year_start = equity
        equity *= 1.0 + float(r)
        eq[i] = equity
    nr = equity / year_start - 1.0
    carry = [x for x in carry if year <= x[0]]
    if nr > 0:
        n = nr
        for lot in carry:
            take = min(lot[1], n)
            lot[1] -= take
            n -= take
        t = max(0.0, n) * fr_rate(year)
        equity -= t * year_start
        tax += t * year_start
    eq[-1] = equity
    return pd.Series(eq, index=daily.index), float(tax)


def summarize_trades(trades: pd.DataFrame, n_sessions: int) -> dict:
    if trades.empty:
        return {"n": 0, "multiple": 1.0, "cagr": 0.0, "maxdd": 0.0, "hit": 0.0, "mean": 0.0}
    daily = trades.groupby("session")["net"].apply(lambda x: float(np.prod(1.0 + x) - 1.0))
    daily = daily.sort_index()
    equity, tax = _tax_equity(daily)
    # CAGR must use the calendar span, not the number of active trade days:
    # a sparse strategy with 20 trades over two years is not a 0.08-year path.
    span_days = max((daily.index[-1] - daily.index[0]).days, 1)
    years = span_days / 365.25
    return {
        "n": int(len(trades)),
        "sessions": int(n_sessions),
        "years": float(years),
        "multiple": float(equity.iloc[-1]),
        "cagr": float(equity.iloc[-1] ** (1.0 / years) - 1.0) if years > 0 else 0.0,
        "maxdd": float((equity / equity.cummax() - 1.0).min()),
        "hit": float((trades["net"] > 0).mean()),
        "mean": float(trades["net"].mean()),
        "tax": tax,
    }


def gap_fade(bars: pd.DataFrame, gap_bps: float = 50.0, cost_bps: float = 5.0) -> pd.DataFrame:
    rows = []
    prev_close = None
    for session, g in bars.groupby("session", sort=True):
        g = g.sort_values("ts")
        if prev_close is not None and len(g) >= 3:
            first = g.iloc[0]
            entry = float(g.iloc[1]["open"])
            exit_px = float(g.iloc[-1]["close"])
            gap = float(first["open"]) / prev_close - 1.0
            if abs(gap) >= gap_bps / 1e4 and entry > 0:
                direction = -1.0 if gap > 0 else 1.0
                gross = direction * (exit_px / entry - 1.0)
                rows.append(
                    {"session": session, "gap": gap, "direction": direction,
                     "entry": entry, "exit": exit_px,
                     "gross": gross, "net": gross - 2.0 * cost_bps / 1e4}
                )
        prev_close = float(g.iloc[-1]["close"])
    return pd.DataFrame(rows)


def opening_range_breakout(
    bars: pd.DataFrame, range_bars: int = 1, cost_bps: float = 5.0
) -> pd.DataFrame:
    rows = []
    for session, g in bars.groupby("session", sort=True):
        g = g.sort_values("ts").reset_index(drop=True)
        if len(g) < range_bars + 3:
            continue
        hi = float(g.iloc[:range_bars]["high"].max())
        lo = float(g.iloc[:range_bars]["low"].min())
        direction = 0.0
        entry = None
        for i in range(range_bars, len(g) - 1):
            c = float(g.iloc[i]["close"])
            nxt = float(g.iloc[i + 1]["open"])
            if direction == 0.0:
                if c > hi:
                    direction, entry = 1.0, nxt
                elif c < lo:
                    direction, entry = -1.0, nxt
        if direction != 0.0 and entry and entry > 0:
            exit_px = float(g.iloc[-1]["close"])
            gross = direction * (exit_px / entry - 1.0)
            rows.append(
                {"session": session, "direction": direction, "gross": gross,
                 "net": gross - 2.0 * cost_bps / 1e4}
            )
    return pd.DataFrame(rows)


def vwap_reversion(
    bars: pd.DataFrame, entry_z: float = 1.0, cost_bps: float = 5.0
) -> pd.DataFrame:
    rows = []
    for session, g in bars.groupby("session", sort=True):
        g = g.sort_values("ts").reset_index(drop=True)
        if len(g) < 5:
            continue
        typ = (g["high"] + g["low"] + g["close"]) / 3.0
        vol = g["volume"].replace(0, np.nan).fillna(1.0)
        vwap = (typ * vol).cumsum() / vol.cumsum()
        dev = (g["close"] - vwap)
        sd = dev.expanding(min_periods=3).std()
        direction, entry = 0.0, None
        for i in range(3, len(g) - 1):
            if not np.isfinite(sd.iloc[i]) or sd.iloc[i] <= 0:
                continue
            z = dev.iloc[i] / sd.iloc[i]
            if direction == 0.0:
                if z <= -entry_z:
                    direction, entry = 1.0, float(g.iloc[i + 1]["open"])
                elif z >= entry_z:
                    direction, entry = -1.0, float(g.iloc[i + 1]["open"])
        if direction != 0.0 and entry and entry > 0:
            exit_px = float(g.iloc[-1]["close"])
            gross = direction * (exit_px / entry - 1.0)
            rows.append(
                {"session": session, "direction": direction, "gross": gross,
                 "net": gross - 2.0 * cost_bps / 1e4}
            )
    return pd.DataFrame(rows)


def last_hour_momentum(bars: pd.DataFrame, cost_bps: float = 5.0) -> pd.DataFrame:
    rows = []
    for session, g in bars.groupby("session", sort=True):
        g = g.sort_values("ts").reset_index(drop=True)
        if len(g) < 4:
            continue
        signal_bar = len(g) - 2
        day_ret = float(g.iloc[signal_bar]["close"] / g.iloc[0]["open"] - 1.0)
        direction = 1.0 if day_ret > 0 else -1.0
        entry = float(g.iloc[-1]["open"])
        exit_px = float(g.iloc[-1]["close"])
        if entry > 0:
            gross = direction * (exit_px / entry - 1.0)
            rows.append(
                {"session": session, "direction": direction, "day_ret": day_ret,
                 "gross": gross, "net": gross - 2.0 * cost_bps / 1e4}
            )
    return pd.DataFrame(rows)
