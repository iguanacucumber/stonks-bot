"""Benchmark attribution and cost/tax decomposition.

Return attribution is return-based, not holdings-based: it decomposes the
strategy's daily return into market beta, annualised alpha, tracking error and
up/down capture.  That is the honest available view because the engine's trades,
taxes and financing make a clean holdings-based Brinson attribution misleading.

The cost/tax decomposition adds back every cash outflow the engine records.  It
is an additive terminal approximation: costs compound, so the components are
reported in multiple points and as a share of the gross (frictionless) multiple,
with an explicit ``cost_drag_pct``.  Do not read the components as independent
multiplicative haircuts.
"""

from __future__ import annotations

import math
from typing import Iterable

import numpy as np
import pandas as pd

from .metrics import (
    TRADING_DAYS,
    apply_tax_to_multiple,
    cagr_from_multiple,
    max_drawdown,
    to_returns,
)


def _align(returns, benchmark):
    r = pd.Series(returns, dtype=float).dropna()
    b = pd.Series(benchmark, dtype=float).dropna()
    j = r.index.intersection(b.index)
    if len(j) == 0:  # positional fallback for synthetic arrays
        n = min(len(r), len(b))
        r, b = r.iloc[:n].reset_index(drop=True), b.iloc[:n].reset_index(drop=True)
        j = r.index
    return r.loc[j], b.loc[j]


def _compound(x: pd.Series) -> float:
    return float(np.prod(1.0 + x.to_numpy()) - 1.0)


def benchmark_attribution(
    returns, benchmark, periods_per_year: int = TRADING_DAYS
) -> dict[str, float]:
    """Beta/alpha/TE/capture statistics for a strategy versus a benchmark."""
    r, b = _align(returns, benchmark)
    n = len(r)
    if n < 2:
        return {"n": float(n)}
    var_b = float(b.var(ddof=1))
    beta = float(np.cov(r, b, ddof=1)[0, 1] / var_b) if var_b > 0 else float("nan")
    alpha = (
        float((r.mean() - beta * b.mean()) * periods_per_year)
        if math.isfinite(beta)
        else float("nan")
    )
    active = r - b
    te = float(active.std(ddof=1) * math.sqrt(periods_per_year))
    ir = (
        float(active.mean() * periods_per_year / te)
        if te > 0 and math.isfinite(te)
        else float("nan")
    )
    corr = float(r.corr(b)) if n > 2 else float("nan")
    # Capture ratios use average returns in up/down benchmark periods.  This is
    # the standard definition and is exactly 2.0 for a 2x daily-rebalanced book.
    up = b > 0
    down = b < 0
    up_cap = (
        float(r[up].mean() / b[up].mean())
        if up.any() and b[up].mean() != 0
        else float("nan")
    )
    down_cap = (
        float(r[down].mean() / b[down].mean())
        if down.any() and b[down].mean() != 0
        else float("nan")
    )
    return {
        "n": float(n),
        "beta": beta,
        "alpha_ann": alpha,
        "r2": float(corr * corr) if math.isfinite(corr) else float("nan"),
        "correlation": corr,
        "tracking_error": te,
        "information_ratio": ir,
        "excess_cagr": float(
            (1.0 + _compound(r)) ** (periods_per_year / n)
            - (1.0 + _compound(b)) ** (periods_per_year / n)
        ),
        "up_capture": float(up_cap),
        "down_capture": float(down_cap),
        "hit_rate": float((r > 0).mean()),
        "benchmark_hit_rate": float((b > 0).mean()),
        "worst_day": float(r.min()),
        "worst_benchmark_day": float(b.min()),
    }


def exposure_breakdown(
    weights: pd.DataFrame,
    core: Iterable[str] = ("SMH", "SOXX"),
    cash_col: str = "CASH",
) -> dict[str, float]:
    """Average gross/core/sleeve/cash exposure from the daily weights matrix."""
    if weights.empty:
        return {}
    core_set = {c for c in core if c in weights.columns}
    cash = (
        weights[cash_col].astype(float)
        if cash_col in weights.columns
        else pd.Series(0.0, index=weights.index)
    )
    tradables = weights.drop(columns=[cash_col], errors="ignore").astype(float)
    gross = tradables.sum(axis=1)
    core_w = (
        tradables[list(core_set)].sum(axis=1)
        if core_set
        else pd.Series(0.0, index=weights.index)
    )
    sleeve = gross - core_w
    return {
        "avg_gross": float(gross.mean()),
        "avg_core": float(core_w.mean()),
        "avg_sleeve": float(sleeve.mean()),
        "avg_cash": float(cash.mean()),
        "max_gross": float(gross.max()),
        "avg_names": float((tradables > 1e-9).sum(axis=1).mean()),
        "days": float(len(weights)),
    }


def financing_cost(result, financing_bps: float = 500.0, periods: int = 252) -> float:
    """Approximate margin interest in initial-equity units.

    The engine grows negative cash by the daily rate before each session.  Using
    yesterday's negative cash is the same convention, up to the one-day timing.
    """
    cash = getattr(result, "cash", None)
    if cash is None or len(cash) == 0 or financing_bps <= 0:
        return 0.0
    debt = cash.shift(1).fillna(0.0).clip(upper=0.0).abs()
    return float((debt * (financing_bps / 1e4 / periods)).sum())


def cost_tax_decomposition(
    result,
    capital: float = 1.0,
    financing_bps: float = 500.0,
    fx: float = 1.0,
    periods: int = 252,
) -> dict[str, float]:
    """Decompose the terminal multiple into fees, tax, financing and exit tax.

    All amounts are in the same units as ``capital`` after ``fx`` translation.
    The engine stores costs as fractions of starting equity, so the terminal
    multiple components are directly comparable to the after-tax multiple.
    """
    eq = result.equity.astype(float)
    if len(eq) < 2 or eq.iloc[0] <= 0:
        raise ValueError("result has no usable equity path")
    # Convert every component to the reporting currency.  With fx=1 this is
    # the native decomposition; with fx=EUR/USD it matches the terminal-FX
    # headline multiple.
    pre = float(eq.iloc[-1] / eq.iloc[0]) * fx
    fees = float(getattr(result, "fees_paid", 0.0) / eq.iloc[0]) * fx
    ftt = float(getattr(result, "ftt_paid", 0.0) / eq.iloc[0]) * fx
    tax = float(getattr(result, "tax_paid", 0.0) / eq.iloc[0]) * fx
    exit_tax = float(getattr(result, "unrealized_tax", 0.0) / eq.iloc[0]) * fx
    fin = financing_cost(result, financing_bps, periods) / eq.iloc[0] * fx
    after = pre - exit_tax
    gross = pre + fees + tax + fin + exit_tax
    out = {
        "pre_tax_multiple": pre,
        "fx": float(fx),
        "fees": fees,
        "fees_ex_ftt": fees - ftt,
        "ftt": ftt,
        "tax_paid": tax,
        "financing": fin,
        "exit_tax": exit_tax,
        "after_tax_multiple": after,
        "gross_multiple": gross,
        "cost_drag_pct": float(1.0 - after / gross) if gross > 0 else float("nan"),
        "fee_share": float(fees / gross) if gross > 0 else float("nan"),
        "tax_share": float((tax + exit_tax) / gross) if gross > 0 else float("nan"),
        "years": float((len(eq) - 1) / periods),
    }
    if capital and capital > 0:
        for k in ("fees", "fees_ex_ftt", "ftt", "tax_paid", "financing", "exit_tax"):
            out[f"{k}_amount"] = float(out[k] * capital)
        out["after_tax_value"] = float(after * capital)
        out["gross_value"] = float(gross * capital)
    return out


def after_tax_benchmark_multiple(
    benchmark_equity: pd.Series, tax_rate: float, fx: float = 1.0
) -> float:
    """Buy-and-hold benchmark multiple after a terminal-gain tax approximation."""
    b = benchmark_equity.dropna().astype(float)
    if len(b) < 2 or b.iloc[0] <= 0:
        return float("nan")
    m = float(b.iloc[-1] / b.iloc[0])
    return apply_tax_to_multiple(m, tax_rate) * fx


def summary_row(
    name: str,
    equity: pd.Series,
    benchmark: pd.Series | None = None,
    tax_rate: float = 0.314,
) -> dict[str, float | str]:
    """Compact row used by the headline markdown table."""
    r = to_returns(equity)
    if benchmark is not None:
        attribution = benchmark_attribution(r, to_returns(benchmark).reindex(r.index))
    else:
        attribution = {}
    return {
        "strategy": name,
        "multiple": float(equity.iloc[-1] / equity.iloc[0]),
        "after_tax_approx": apply_tax_to_multiple(
            float(equity.iloc[-1] / equity.iloc[0]), tax_rate
        ),
        "cagr": float(
            cagr_from_multiple(equity.iloc[-1] / equity.iloc[0], len(r) / TRADING_DAYS)
        ),
        "max_dd": max_drawdown(equity),
        **attribution,
    }
