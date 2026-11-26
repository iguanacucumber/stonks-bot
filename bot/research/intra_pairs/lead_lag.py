"""Intraday lead-lag: source bar -> target next bar open-to-close."""

from __future__ import annotations

import numpy as np
import pandas as pd


def align_bars(a: pd.DataFrame, b: pd.DataFrame) -> pd.DataFrame:
    cols = ["open", "high", "low", "close", "volume"]
    x = a.set_index("ts")[cols].add_prefix("src_")
    y = b.set_index("ts")[cols].add_prefix("tgt_")
    return x.join(y, how="inner").dropna(subset=["src_close", "tgt_open", "tgt_close"])


def lead_lag_trades(
    src: pd.DataFrame,
    tgt: pd.DataFrame,
    *,
    lag: int = 1,
    threshold: float = 0.0,
    mode: str = "momentum",
    cost_bps: float = 5.0,
) -> pd.DataFrame:
    """Signal from a completed source bar; target next bar open->close.

    lag=1 uses the immediately preceding source bar. lag=2 uses one earlier
    source bar, etc. The target bar is always the next bar after the signal
    close; no same-bar fill.
    """
    if lag < 1:
        raise ValueError("lag must be >= 1")
    df = align_bars(src, tgt)
    if len(df) < lag + 2:
        return pd.DataFrame()
    src_ret = (df["src_close"] / df["src_open"] - 1.0).shift(lag - 1)
    tgt_ret = df["tgt_close"] / df["tgt_open"] - 1.0
    # The trade at bar i+1 uses signal at bar i; shift target return back one.
    tgt_next = tgt_ret.shift(-1)
    direction = np.sign(src_ret)
    if mode == "reversal":
        direction = -direction
    active = (direction != 0.0) & (src_ret.abs() >= threshold) & tgt_next.notna()
    rows = []
    for ts in df.index[active]:
        i = df.index.get_loc(ts)
        if i + 1 >= len(df):
            continue
        d = float(direction.loc[ts])
        gross = d * float(tgt_next.loc[ts])
        rows.append(
            {
                "session": pd.Timestamp(ts).normalize(),
                "ts": ts,
                "direction": d,
                "source_ret": float(src_ret.loc[ts]),
                "gross": gross,
                "net": gross - 2.0 * cost_bps / 1e4,
            }
        )
    return pd.DataFrame(rows)


def lead_lag_regression_trades(
    src: pd.DataFrame,
    tgt: pd.DataFrame,
    *,
    window: int = 100,
    threshold: float = 0.0,
    cost_bps: float = 5.0,
) -> pd.DataFrame:
    """Expanding/rolling beta signal, estimated only on prior bars."""
    df = align_bars(src, tgt)
    if len(df) < window + 3:
        return pd.DataFrame()
    s = df["src_close"] / df["src_open"] - 1.0
    t = (df["tgt_close"] / df["tgt_open"] - 1.0).shift(-1)
    beta = s.rolling(window).cov(t) / s.rolling(window).var()
    signal = beta.shift(1) * s
    direction = np.sign(signal)
    active = (direction != 0.0) & (signal.abs() >= threshold) & t.notna()
    rows = []
    for ts in df.index[active]:
        d = float(direction.loc[ts])
        gross = d * float(t.loc[ts])
        rows.append(
            {
                "session": pd.Timestamp(ts).normalize(),
                "ts": ts,
                "direction": d,
                "source_ret": float(s.loc[ts]),
                "gross": gross,
                "net": gross - 2.0 * cost_bps / 1e4,
            }
        )
    return pd.DataFrame(rows)
