"""Rolling multi-year windows and their summary table.

The existing robustness scripts rerun the whole accounting engine from every
start date (fresh tax lots, fresh leverage path).  That is the gold standard and
remains the right tool for a start-date sweep.  This module is the cheap,
comparable view on **one** already-computed path: slice a 5-year window, report
the multiple/CAGR/max drawdown, and compare it with a benchmark slice.  Use it
for the routine report table; use ``scripts/robustness_best.py`` when the start
date itself must reset the accounting state.

The benchmark comparison is total-return (adjusted close).  When ``tax_rate`` is
set, both sides pay the same terminal-gain tax approximation, so the "beat"
column is after tax in the same sense as the headline after-tax multiple.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math

import numpy as np
import pandas as pd

from .metrics import (
    TRADING_DAYS,
    apply_tax_to_multiple,
    cagr_from_multiple,
    max_drawdown,
)


@dataclass
class WindowTable:
    rows: pd.DataFrame = field(repr=False)
    summary: dict[str, float | str]

    def to_markdown(self, floatfmt: str = ".2f") -> str:
        df = self.rows.copy()
        header = (
            "| start | end | years | multiple | CAGR | maxDD | benchmark | gap | beat |\n"
            "|---|---|---:|---:|---:|---:|---:|---:|:---:|"
        )
        lines = [header]
        for _, r in df.iterrows():
            lines.append(
                f"| {r['start']} | {r['end']} | {r['years']:.1f} | "
                f"{r['multiple']:{floatfmt}}x | {r['cagr']:.1%} | "
                f"{r['max_dd']:.1%} | {r['benchmark']:{floatfmt}}x | "
                f"{r['gap']:+{floatfmt}}x | {'yes' if r['beat'] else 'no'} |"
            )
        return "\n".join(lines)


def _window_multiple(
    series: pd.Series, start: pd.Timestamp, end: pd.Timestamp
) -> tuple[pd.Series, float]:
    s = series.loc[(series.index >= start) & (series.index <= end)].dropna()
    if len(s) < 2 or s.iloc[0] <= 0:
        return s, float("nan")
    return s, float(s.iloc[-1] / s.iloc[0])


def _apply_tax(m: float, tax_rate: float | None) -> float:
    if tax_rate is None or not math.isfinite(m):
        return m
    return apply_tax_to_multiple(m, tax_rate)


def rolling_windows(
    equity: pd.Series,
    benchmark: pd.Series | None = None,
    years: int = 5,
    freq: str = "MS",
    min_obs: int = 252,
    include_partial: bool = False,
    tax_rate: float | None = None,
    periods_per_year: int = TRADING_DAYS,
) -> WindowTable:
    """All rolling ``years``-year windows on one equity path.

    ``freq`` is the start-date frequency (monthly by default).  A window starts
    at the first observed trading day on/after each calendar start and ends at
    the last observed day on/before ``start + years``.  Partials are excluded
    unless ``include_partial=True``.
    """
    if not isinstance(equity.index, pd.DatetimeIndex):
        raise TypeError("equity must have a DatetimeIndex")
    if years <= 0:
        raise ValueError("years must be > 0")
    e = equity.astype(float).sort_index().dropna()
    if benchmark is not None:
        b = benchmark.astype(float).sort_index().ffill().reindex(e.index)
    else:
        b = None
    first, last = e.index[0], e.index[-1]
    starts = pd.date_range(first.normalize().replace(day=1), last, freq=freq)
    rows = []
    for cal_start in starts:
        target_end = cal_start + pd.DateOffset(years=years)
        if target_end > last and not include_partial:
            continue
        sl = e.loc[(e.index >= cal_start) & (e.index <= target_end)].dropna()
        if len(sl) < min_obs:
            continue
        start, end = sl.index[0], sl.index[-1]
        m = float(sl.iloc[-1] / sl.iloc[0])
        m_tax = _apply_tax(m, tax_rate)
        yrs = (len(sl) - 1) / float(periods_per_year)
        row = {
            "start": start.date().isoformat(),
            "end": end.date().isoformat(),
            "years": yrs,
            "multiple": m_tax,
            "cagr": cagr_from_multiple(m_tax, yrs),
            "max_dd": max_drawdown(sl),
        }
        if b is not None:
            bm = _window_multiple(b, start, end)[1]
            bm_tax = _apply_tax(bm, tax_rate)
            row["benchmark"] = bm_tax
            row["gap"] = m_tax - bm_tax
            row["beat"] = bool(m_tax > bm_tax)
        else:
            row["benchmark"] = float("nan")
            row["gap"] = float("nan")
            row["beat"] = False
        rows.append(row)
    df = pd.DataFrame(rows)
    summary: dict[str, float | str] = {
        "n_windows": float(len(df)),
        "years": float(years),
    }
    if len(df):
        summary.update(
            {
                "median_multiple": float(df["multiple"].median()),
                "worst_multiple": float(df["multiple"].min()),
                "best_multiple": float(df["multiple"].max()),
                "p5_multiple": float(df["multiple"].quantile(0.05)),
                "median_cagr": float(df["cagr"].median()),
                "worst_max_dd": float(df["max_dd"].min()),
                "share_beat": float(df["beat"].mean())
                if b is not None
                else float("nan"),
            }
        )
        if b is not None:
            summary["median_benchmark"] = float(df["benchmark"].median())
            worst_i = df["multiple"].idxmin()
            summary["worst_start"] = str(df.loc[worst_i, "start"])
            summary["worst_end"] = str(df.loc[worst_i, "end"])
    return WindowTable(rows=df, summary=summary)


def five_year_table(
    equity: pd.Series,
    benchmark: pd.Series | None = None,
    tax_rate: float | None = None,
    include_partial: bool = False,
    periods_per_year: int = TRADING_DAYS,
) -> WindowTable:
    """Convenience wrapper for the protocol's 5-year rolling requirement."""
    return rolling_windows(
        equity,
        benchmark=benchmark,
        years=5,
        tax_rate=tax_rate,
        include_partial=include_partial,
        periods_per_year=periods_per_year,
    )


def yearly_returns(equity: pd.Series) -> pd.Series:
    """Calendar-year returns from an equity path (first/last obs in each year)."""
    e = equity.astype(float).sort_index().dropna()
    out = {}
    for year, s in e.groupby(e.index.year):
        if len(s) >= 2:
            out[year] = float(s.iloc[-1] / s.iloc[0] - 1.0)
    return pd.Series(out, name="return")
