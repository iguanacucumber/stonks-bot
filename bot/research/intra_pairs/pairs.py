"""Intraday log-spread pairs on aligned ETF bars."""

from __future__ import annotations

import numpy as np
import pandas as pd


def align(a: pd.DataFrame, b: pd.DataFrame) -> pd.DataFrame:
    cols = ["open", "high", "low", "close", "volume"]
    x = a.set_index("ts")[cols].add_prefix("a_")
    y = b.set_index("ts")[cols].add_prefix("b_")
    return x.join(y, how="inner").dropna(subset=["a_open", "a_close", "b_open", "b_close"])


def pair_trades(
    a: pd.DataFrame,
    b: pd.DataFrame,
    *,
    lookback: int = 40,
    entry_z: float = 2.0,
    exit_z: float = 0.0,
    max_hold: int = 3,
    mode: str = "neutral",
    cost_bps: float = 5.0,
) -> pd.DataFrame:
    """Enter at next bar open when z is stretched; exit at bar close."""
    df = align(a, b)
    if len(df) < lookback + 3:
        return pd.DataFrame()
    spread = np.log(df["a_close"] / df["b_close"])
    z = (spread - spread.rolling(lookback).mean()) / spread.rolling(lookback).std()
    rows = []
    state = 0
    entry_i = None
    entry_a = entry_b = None
    for i in range(lookback, len(df) - 1):
        zi = z.iloc[i]
        if not np.isfinite(zi):
            continue
        if state == 0:
            # Never enter at the next session's open from a last-bar signal.
            if i + 1 >= len(df) or df.index[i].normalize() != df.index[i + 1].normalize():
                continue
            if zi <= -entry_z:
                state, entry_i = 1, i + 1
                entry_a = float(df["a_open"].iloc[i + 1])
                entry_b = float(df["b_open"].iloc[i + 1])
            elif zi >= entry_z:
                state, entry_i = -1, i + 1
                entry_a = float(df["a_open"].iloc[i + 1])
                entry_b = float(df["b_open"].iloc[i + 1])
            if state != 0:
                continue
        if state != 0:
            held = i + 1 - entry_i
            at_session_end = (
                i + 1 >= len(df)
                or df.index[i].normalize() != df.index[i + 1].normalize()
            )
            exit_now = (
                abs(zi) <= exit_z
                or held >= max_hold
                or at_session_end
            )
            if exit_now and entry_a and entry_b:
                exit_i = i if at_session_end else i + 1
                exit_a = float(df["a_close"].iloc[exit_i])
                exit_b = float(df["b_close"].iloc[exit_i])
                ra = exit_a / entry_a - 1.0
                rb = exit_b / entry_b - 1.0
                if mode == "neutral":
                    gross = state * 0.5 * ra - state * 0.5 * rb
                    cost = 4.0 * cost_bps / 1e4
                else:
                    gross = state * ra if state == 1 else -state * rb
                    cost = 2.0 * cost_bps / 1e4
                rows.append(
                    {
                        "session": pd.Timestamp(df.index[entry_i]).normalize(),
                        "entry_ts": df.index[entry_i],
                        "exit_ts": df.index[exit_i],
                        "direction": float(state),
                        "gross": gross,
                        "net": gross - cost,
                    }
                )
                state = 0
                entry_i = None
                entry_a = entry_b = None
    return pd.DataFrame(rows)
