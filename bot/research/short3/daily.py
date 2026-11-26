"""Daily open/close and cross-sectional families for v8."""

from __future__ import annotations

import numpy as np
import pandas as pd

from bot.backtest import fr_rate


def daily_bars(symbol: str) -> pd.DataFrame:
    from bot.research.data import read_bars

    df = read_bars(symbol).sort_values("date").reset_index(drop=True)
    # `open` is split/dividend-adjusted in read_bars; `close` is raw. Use the
    # adjusted close so open/close are on the same total-return basis.
    df["close"] = df["adjclose"]
    return df.set_index("date")


def open_close_stats(df: pd.DataFrame, start: str = "2015-01-01") -> dict:
    d = df.loc[df.index >= pd.Timestamp(start)]
    o2c = d["close"] / d["open"] - 1.0
    c2o = d["open"] / d["close"].shift(1) - 1.0
    return {
        "o2c_mean": float(o2c.mean()),
        "o2c_vol": float(o2c.std() * np.sqrt(252)),
        "o2c_hit": float((o2c > 0).mean()),
        "c2o_mean": float(c2o.mean()),
        "c2o_vol": float(c2o.std() * np.sqrt(252)),
        "c2o_hit": float((c2o > 0).mean()),
    }


def volatility_gap_fade(
    df: pd.DataFrame,
    *,
    vol_window: int = 20,
    entry_z: float = 1.5,
    cost_bps: float = 5.0,
    start: str = "2015-01-01",
) -> pd.DataFrame:
    """Short/long open-to-close after a volatility-scaled overnight gap.

    Gap z uses the trailing gap standard deviation through the prior session;
    the trade is open-to-close of the signal day, so no future data is used.
    """
    d = df.loc[df.index >= pd.Timestamp(start)].copy()
    gap = d["open"] / d["close"].shift(1) - 1.0
    sigma = gap.rolling(vol_window, min_periods=vol_window // 2).std().shift(1)
    z = gap / sigma.replace(0.0, np.nan)
    direction = pd.Series(0.0, index=d.index)
    direction[z >= entry_z] = -1.0
    direction[z <= -entry_z] = 1.0
    gross = direction * (d["close"] / d["open"] - 1.0)
    net = gross - direction.abs() * 2.0 * cost_bps / 1e4
    out = pd.DataFrame({"gap": gap, "z": z, "direction": direction, "gross": gross, "net": net}).dropna(subset=["z"])
    return out[out.direction != 0.0]


def after_tax_series(daily: pd.Series) -> tuple[pd.Series, float]:
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


def xs_reversal(
    closes: dict[str, pd.Series],
    *,
    lookback: int = 5,
    hold: int = 5,
    top_n: int = 2,
    mode: str = "long_only",
    cost_bps: float = 5.0,
    borrow_bps: float = 500.0,
    start: str = "2015-01-01",
) -> dict:
    """Cross-sectional short-term reversal on liquid ETFs.

    Rebalance every `hold` sessions at the close; weights earn the next day's
    return. Long-only buys the worst `top_n` lookback returns; neutral also
    shorts the best `top_n`.
    """
    px = pd.DataFrame({k: v for k, v in closes.items()}).sort_index()
    px = px.loc[px.index >= pd.Timestamp(start)].dropna()
    r = px.pct_change().fillna(0.0)
    idx = px.index
    weights = pd.DataFrame(0.0, index=idx, columns=px.columns)
    for i in range(max(lookback, 1), len(idx) - 1, hold):
        trailing = px.iloc[i] / px.iloc[i - lookback] - 1.0
        worst = trailing.nsmallest(top_n).index
        best = trailing.nlargest(top_n).index
        if mode == "long_only":
            weights.iloc[i + 1 : min(i + 1 + hold, len(idx)), :] = 0.0
            weights.loc[idx[i + 1 : min(i + 1 + hold, len(idx))], worst] = 1.0 / top_n
        else:
            weights.iloc[i + 1 : min(i + 1 + hold, len(idx)), :] = 0.0
            weights.loc[idx[i + 1 : min(i + 1 + hold, len(idx))], worst] = 0.5 / top_n
            weights.loc[idx[i + 1 : min(i + 1 + hold, len(idx))], best] = -0.5 / top_n
    gross = (weights.shift(1).fillna(0.0) * r).sum(axis=1)
    turnover = weights.diff().abs().sum(axis=1).fillna(weights.abs().sum(axis=1))
    cost = turnover * cost_bps / 1e4
    short = weights.clip(upper=0).abs().sum(axis=1)
    borrow = short * borrow_bps / 1e4 / 252.0
    net = gross - cost - borrow
    equity, tax = after_tax_series(net)
    rr = equity.pct_change().dropna()
    years = len(equity) / 252.0
    return {
        "equity": equity,
        "multiple": float(equity.iloc[-1]),
        "cagr": float(equity.iloc[-1] ** (1.0 / years) - 1.0),
        "maxdd": float((equity / equity.cummax() - 1.0).min()),
        "hit": float((rr > 0).mean()),
        "turnover_ann": float(turnover.mean() * 252.0),
        "tax": tax,
    }
