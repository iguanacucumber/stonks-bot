"""Intraday gap-fade proof of concept from 5-minute bars."""

from __future__ import annotations

import numpy as np
import pandas as pd


def gap_fade_daily(
    bars: pd.DataFrame,
    *,
    gap_bps: float = 50.0,
    cost_bps: float = 5.0,
) -> pd.DataFrame:
    """One trade per session: fade the opening gap, exit at the last bar.

    `bars` has a DatetimeIndex and open/close columns. The first bar's open is
    compared with the prior session's last close. Entry is the first bar close,
    exit is the last bar close. This is a tiny-sample diagnostic, not evidence.
    """
    need = {"open", "close"}
    if not need <= set(bars.columns):
        raise ValueError(f"bars must include {sorted(need)}")
    b = bars.sort_index().copy()
    b["session"] = b.index.normalize()
    rows = []
    prev_close = None
    for session, g in b.groupby("session", sort=True):
        if prev_close is not None and len(g) >= 2:
            first_open = float(g["open"].iloc[0])
            entry = float(g["close"].iloc[0])
            exit_px = float(g["close"].iloc[-1])
            gap = first_open / prev_close - 1.0
            if abs(gap) >= gap_bps / 1e4 and entry > 0:
                direction = -1.0 if gap > 0 else 1.0
                gross = direction * (exit_px / entry - 1.0)
                rows.append(
                    {
                        "session": session,
                        "gap": gap,
                        "direction": direction,
                        "gross": gross,
                        "net": gross - 2.0 * cost_bps / 1e4,
                    }
                )
        prev_close = float(g["close"].iloc[-1])
    return pd.DataFrame(rows)


def summary(frame: pd.DataFrame) -> dict:
    if frame.empty:
        return {"n": 0}
    r = frame["net"].to_numpy()
    return {
        "n": int(len(r)),
        "mean": float(np.mean(r)),
        "median": float(np.median(r)),
        "hit": float((r > 0).mean()),
        "total": float(np.prod(1.0 + r) - 1.0),
    }
