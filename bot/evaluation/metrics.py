"""Portfolio metrics with the conventions used by the existing reports.

The rest of the repo is a collection of standalone research scripts.  This module
is the single calculation surface for new work; the conventions deliberately match
`bot.backtest.metrics` so a reproduced headline does not drift:

* 252 trading days per year;
* `years = number of daily returns / 252` (not calendar elapsed time);
* max drawdown includes the first equity point in the peak;
* Sortino uses the standard deviation of strictly negative returns (the existing
  repo definition), not downside deviation versus a target;
* a zero-volatility series has an undefined Sharpe, reported as NaN.

Everything is pure: functions take Series/arrays and return plain Python floats or
dicts so they can be unit-tested without the backtest engine.
"""

from __future__ import annotations

import math
from typing import Mapping

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def _array(values) -> np.ndarray:
    if isinstance(values, pd.Series):
        a = values.to_numpy(dtype=float)
    else:
        a = np.asarray(values, dtype=float)
    return a[np.isfinite(a)]


def to_returns(equity) -> pd.Series:
    """Simple daily returns from an equity/wealth series."""
    if isinstance(equity, pd.Series):
        return equity.astype(float).pct_change().dropna()
    a = np.asarray(equity, dtype=float)
    if len(a) < 2:
        return pd.Series(dtype=float)
    return pd.Series(a[1:] / a[:-1] - 1.0)


def total_return(equity) -> float:
    a = _array(equity)
    if len(a) < 2 or a[0] == 0:
        return float("nan")
    return float(a[-1] / a[0] - 1.0)


def multiple(equity) -> float:
    a = _array(equity)
    if len(a) < 2 or a[0] == 0:
        return float("nan")
    return float(a[-1] / a[0])


def years(equity, periods_per_year: int = TRADING_DAYS) -> float:
    n = len(_array(equity)) - 1
    return n / float(periods_per_year) if n > 0 else float("nan")


def cagr(equity, periods_per_year: int = TRADING_DAYS) -> float:
    m = multiple(equity)
    y = years(equity, periods_per_year)
    if not math.isfinite(m) or not math.isfinite(y) or y <= 0 or m <= 0:
        return float("nan")
    return float(m ** (1.0 / y) - 1.0)


def cagr_from_multiple(m: float, y: float) -> float:
    """Compound annual growth from a terminal multiple and a year count."""
    if not (math.isfinite(m) and math.isfinite(y)) or m <= 0 or y <= 0:
        return float("nan")
    return float(m ** (1.0 / y) - 1.0)


def annualized_return(returns, periods_per_year: int = TRADING_DAYS) -> float:
    """Arithmetic mean annualised, as used for alpha in the old reports."""
    r = _array(returns)
    return float(r.mean() * periods_per_year) if len(r) else float("nan")


def volatility(returns, periods_per_year: int = TRADING_DAYS) -> float:
    r = _array(returns)
    if len(r) < 2:
        return float("nan")
    return float(r.std(ddof=1) * math.sqrt(periods_per_year))


def sharpe_ratio(
    returns, periods_per_year: int = TRADING_DAYS, risk_free: float = 0.0
) -> float:
    r = _array(returns)
    if len(r) < 2:
        return float("nan")
    sd = r.std(ddof=1)
    # Floating-point cancellation leaves ~1e-19 for a mathematically constant
    # series; treat that as zero dispersion.
    if sd <= 1e-15 or not math.isfinite(sd):
        return float("nan")
    return float(
        (r.mean() - risk_free / periods_per_year) / sd * math.sqrt(periods_per_year)
    )


def sortino_ratio(returns, periods_per_year: int = TRADING_DAYS) -> float:
    r = _array(returns)
    if len(r) < 2:
        return float("nan")
    downside = r[r < 0]
    if len(downside) < 2:
        return float("nan")
    dd = downside.std(ddof=1) * math.sqrt(periods_per_year)
    if dd == 0 or not math.isfinite(dd):
        return float("nan")
    return float(r.mean() * periods_per_year / dd)


def drawdown(equity) -> pd.Series:
    """Drawdown path from the running peak, as a negative fraction."""
    if isinstance(equity, pd.Series):
        e = equity.astype(float)
    else:
        e = pd.Series(np.asarray(equity, dtype=float))
    return e / e.cummax() - 1.0


def max_drawdown(equity) -> float:
    e = _array(equity)
    if len(e) < 2:
        return float("nan")
    peak = np.maximum.accumulate(e)
    with np.errstate(divide="ignore", invalid="ignore"):
        dd = np.where(peak > 0, e / peak - 1.0, 0.0)
    return float(np.nanmin(dd))


def calmar_ratio(equity, periods_per_year: int = TRADING_DAYS) -> float:
    c = cagr(equity, periods_per_year)
    dd = max_drawdown(equity)
    if not (math.isfinite(c) and math.isfinite(dd)) or dd >= 0:
        return float("nan")
    return float(c / abs(dd))


def hit_rate(returns) -> float:
    r = _array(returns)
    return float((r > 0).mean()) if len(r) else float("nan")


def compute_metrics(
    equity, periods_per_year: int = TRADING_DAYS, risk_free: float = 0.0
) -> dict[str, float]:
    """Full metric set for one equity path."""
    r = to_returns(equity)
    m = multiple(equity)
    return {
        "multiple": m,
        "total_return": total_return(equity),
        "cagr": cagr(equity, periods_per_year),
        "years": years(equity, periods_per_year),
        "vol": volatility(r, periods_per_year),
        "sharpe": sharpe_ratio(r, periods_per_year, risk_free),
        "sortino": sortino_ratio(r, periods_per_year),
        "max_dd": max_drawdown(equity),
        "calmar": calmar_ratio(equity, periods_per_year),
        "hit_rate": hit_rate(r),
        "days": float(len(r)),
    }


def after_tax_multiple(
    pre_tax_value: float,
    capital: float,
    exit_tax: float = 0.0,
) -> float:
    """Terminal multiple after the remaining (exit) tax liability.

    `pre_tax_value` already includes all realised taxes and fees paid during the
    path; `exit_tax` is the liability on the open book at the end.
    """
    if not math.isfinite(pre_tax_value) or capital <= 0:
        return float("nan")
    return float((pre_tax_value - exit_tax) / capital)


def after_tax_summary(
    pre_tax_value: float,
    capital: float,
    exit_tax: float = 0.0,
    y: float | None = None,
) -> dict[str, float]:
    m = after_tax_multiple(pre_tax_value, capital, exit_tax)
    return {
        "after_tax_value": float(pre_tax_value - exit_tax),
        "after_tax_multiple": m,
        "after_tax_cagr": cagr_from_multiple(m, y) if y is not None else float("nan"),
    }


def beta_alpha(
    returns, benchmark, periods_per_year: int = TRADING_DAYS
) -> tuple[float, float]:
    """CAPM beta and annualised arithmetic alpha on aligned returns."""
    r = pd.Series(_array(returns))
    b = pd.Series(_array(benchmark))
    n = min(len(r), len(b))
    if n < 2:
        return float("nan"), float("nan")
    r, b = r.iloc[:n], b.iloc[:n]
    var_b = float(b.var(ddof=1))
    if var_b <= 0 or not math.isfinite(var_b):
        return float("nan"), float("nan")
    cov = float(np.cov(r, b, ddof=1)[0, 1])
    beta = cov / var_b
    alpha = (float(r.mean()) - beta * float(b.mean())) * periods_per_year
    return float(beta), float(alpha)


def summarize_row(name: str, equity, benchmark=None) -> dict[str, float | str]:
    """One table row, compatible with `bot.backtest.summarize` keys."""
    m = compute_metrics(equity)
    out: dict[str, float | str] = {"strategy": name, **m}
    if benchmark is not None:
        r = to_returns(equity)
        b = to_returns(benchmark).reindex(r.index).dropna()
        r = r.reindex(b.index)
        beta, alpha = beta_alpha(r, b)
        out["beta"] = beta
        out["alpha"] = alpha
    return out


def apply_tax_to_multiple(m: float, tax_rate: float) -> float:
    """Approximate after-tax multiple when only the terminal gain is taxed.

    Used by bootstraps and window tables: each path keeps its path-dependent
    multiple, and the gain above 1.0 pays `tax_rate`.  It is an approximation to
    the annual French settlement, not a substitute for the accounting engine.
    """
    if not math.isfinite(m):
        return float("nan")
    # Losses are not refunded (French carry-forward, no negative tax).
    if m <= 1.0:
        return float(m)
    return float(1.0 + (m - 1.0) * (1.0 - tax_rate))


def drawdown_stats(equity) -> Mapping[str, float]:
    dd = drawdown(equity)
    return {
        "max_dd": float(dd.min()),
        "avg_dd": float(dd.mean()),
        "time_in_dd": float((dd < 0).mean()),
    }
