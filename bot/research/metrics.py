"""Metrics used by every Alpha Lab report.

The after-tax terminal multiple is always a liquidation multiple: the engine's
unrealized French tax is subtracted at the last close. Drawdowns and CAGR are
computed on the engine's pre-tax daily equity path, matching the existing
reports; the after-tax path would require an arbitrary daily tax accrual.
"""

from __future__ import annotations

import math
from statistics import NormalDist

import numpy as np
import pandas as pd

TRADING_DAYS = 252.0
EULER = 0.5772156649015329


def max_drawdown(equity: pd.Series) -> float:
    eq = equity.dropna()
    if eq.empty:
        return float("nan")
    return float((eq / eq.cummax() - 1.0).min())


def cagr(equity: pd.Series) -> float:
    eq = equity.dropna()
    if len(eq) < 2 or eq.iloc[0] <= 0:
        return float("nan")
    years = len(eq) / TRADING_DAYS
    return float((eq.iloc[-1] / eq.iloc[0]) ** (1.0 / years) - 1.0)


def annual_return(equity: pd.Series, year: int) -> float:
    eq = (
        equity.loc[str(year)] if (equity.index.year == year).any() else equity.iloc[0:0]
    )
    if len(eq) < 2:
        return float("nan")
    return float(eq.iloc[-1] / eq.iloc[0] - 1.0)


def worst_12m(equity: pd.Series) -> float:
    eq = equity.dropna()
    if len(eq) <= 252:
        return float("nan")
    return float((eq / eq.shift(252) - 1.0).min())


def five_year_windows(
    equity: pd.Series, min_years: float = 4.5
) -> list[dict[str, object]]:
    """Every month-start five-year window, using the last observation <= target."""
    eq = equity.dropna()
    if len(eq) < 252 * min_years:
        return []
    starts = pd.date_range(eq.index.min(), eq.index.max(), freq="MS")
    out: list[dict[str, object]] = []
    for start in starts:
        if start < eq.index[0]:
            continue
        end = start + pd.DateOffset(years=5)
        if end > eq.index[-1]:
            end = eq.index[-1]
        a = eq.asof(start)
        b = eq.asof(end)
        if not np.isfinite(a) or not np.isfinite(b) or a <= 0:
            continue
        if (end - start).days < 365 * min_years:
            continue
        out.append(
            {
                "start": start.date().isoformat(),
                "end": end.date().isoformat(),
                "return": float(b / a - 1.0),
            }
        )
    return out


def window_summary(returns: list[float], benchmark: list[float] | None = None) -> dict:
    if not returns:
        return {"n": 0}
    r = np.asarray(returns, dtype=float)
    out = {
        "n": int(len(r)),
        "median": float(np.median(r)),
        "worst": float(np.min(r)),
        "best": float(np.max(r)),
        "share_positive": float((r > 0).mean()),
    }
    if benchmark and len(benchmark) == len(r):
        b = np.asarray(benchmark, dtype=float)
        out["share_beat_benchmark"] = float((r > b).mean())
        out["median_gap"] = float(np.median(r - b))
        out["benchmark_median"] = float(np.median(b))
        out["benchmark_worst"] = float(np.min(b))
    return out


def sharpe(returns: pd.Series) -> float:
    r = returns.dropna()
    sd = r.std()
    if len(r) < 2 or not sd or not np.isfinite(sd):
        return float("nan")
    return float(r.mean() / sd * math.sqrt(TRADING_DAYS))


def sortino(returns: pd.Series) -> float:
    r = returns.dropna()
    downside = r[r < 0].std()
    if len(r) < 2 or not downside or not np.isfinite(downside):
        return float("nan")
    return float(r.mean() / downside * math.sqrt(TRADING_DAYS))


def _block_indices(
    rng: np.random.Generator, n_obs: int, n_paths: int, block: int
) -> np.ndarray:
    n_blocks = int(np.ceil(n_obs / block))
    starts = rng.integers(0, n_obs, size=(n_paths, n_blocks))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]).reshape(
        n_paths, n_blocks * block
    )
    return idx[:, :n_obs] % n_obs


def bootstrap_paths(
    returns: pd.Series | np.ndarray,
    n_paths: int = 10000,
    block: int = 21,
    seed: int = 7,
    chunk: int = 1000,
) -> tuple[np.ndarray, np.ndarray]:
    """Block-bootstrap terminal wealth and max drawdown, in chunks.

    Returns (terminal multiples, max drawdowns), both length `n_paths`.
    """
    r = np.asarray(pd.Series(returns).dropna(), dtype=float)
    if r.size < block * 2:
        raise ValueError("not enough observations for a block bootstrap")
    rng = np.random.default_rng(seed)
    finals = np.empty(n_paths)
    dds = np.empty(n_paths)
    done = 0
    while done < n_paths:
        size = min(chunk, n_paths - done)
        idx = _block_indices(rng, r.size, size, block)
        paths = np.cumprod(1.0 + r[idx], axis=1)
        finals[done : done + size] = paths[:, -1]
        peak = np.maximum.accumulate(paths, axis=1)
        dds[done : done + size] = (paths / peak - 1.0).min(axis=1)
        done += size
    return finals, dds


def bootstrap_p5(
    returns: pd.Series | np.ndarray,
    n_paths: int = 10000,
    block: int = 21,
    seed: int = 7,
) -> dict[str, float]:
    finals, dds = bootstrap_paths(returns, n_paths=n_paths, block=block, seed=seed)
    return {
        "p5_multiple": float(np.quantile(finals, 0.05)),
        "median_multiple": float(np.quantile(finals, 0.50)),
        "p95_multiple": float(np.quantile(finals, 0.95)),
        "p5_maxdd": float(np.quantile(dds, 0.05)),
        "median_maxdd": float(np.quantile(dds, 0.50)),
        "p95_maxdd": float(np.quantile(dds, 0.95)),
        "p_negative": float((finals < 1.0).mean()),
    }


def deflated_sharpe(
    returns: pd.Series, trial_sharpes: list[float] | np.ndarray
) -> dict[str, float]:
    """Deflated Sharpe ratio (Bailey & Lopez de Prado, 2014).

    `trial_sharpes` must contain the in-sample Sharpe of every variant tried,
    including the selected one. Returns annualised SR, expected max SR under
    the null, and the deflated probability that the selected SR is real.
    """
    r = returns.dropna()
    if len(r) < 30:
        return {"sharpe": float("nan"), "sr0": float("nan"), "dsr": float("nan")}
    sr = sharpe(r)
    trials = np.asarray([x for x in trial_sharpes if np.isfinite(x)], dtype=float)
    n = max(2, len(trials))
    var_sr = float(np.var(trials, ddof=1)) if len(trials) > 1 else 0.0
    if var_sr > 0 and n > 1:
        z1 = NormalDist().inv_cdf(1.0 - 1.0 / n)
        z2 = NormalDist().inv_cdf(1.0 - 1.0 / (n * math.e))
        sr0 = math.sqrt(var_sr) * ((1.0 - EULER) * z1 + EULER * z2)
    else:
        sr0 = 0.0
    skew = float(r.skew())
    kurt = float(r.kurtosis() + 3.0)
    denom = 1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr * sr
    denom = max(denom, 1e-9)
    dsr = NormalDist().cdf((sr - sr0) * math.sqrt(len(r) - 1) / math.sqrt(denom))
    return {"sharpe": sr, "sr0": sr0, "dsr": float(dsr), "n_trials": float(n)}


def summarize_equity(
    equity: pd.Series,
    benchmark: pd.Series | None = None,
    years: int = 5,
) -> dict:
    """One consistent metric block for reports."""
    eq = equity.dropna()
    r = eq.pct_change().dropna()
    out = {
        "start": eq.index[0].date().isoformat(),
        "end": eq.index[-1].date().isoformat(),
        "days": int(len(eq)),
        "total_return": float(eq.iloc[-1] / eq.iloc[0] - 1.0),
        "cagr": cagr(eq),
        "maxdd": max_drawdown(eq),
        "vol": float(r.std() * math.sqrt(TRADING_DAYS)),
        "sharpe": sharpe(r),
        "sortino": sortino(r),
        "worst_12m": worst_12m(eq),
        "calmar": (
            cagr(eq) / abs(max_drawdown(eq)) if max_drawdown(eq) < 0 else float("nan")
        ),
    }
    windows = five_year_windows(eq)
    bench_windows = five_year_windows(benchmark) if benchmark is not None else []
    out["five_year"] = window_summary(
        [w["return"] for w in windows],
        [w["return"] for w in bench_windows] if bench_windows else None,
    )
    out["five_year_windows"] = windows
    return out


def format_pct(x: float, digits: int = 1) -> str:
    if x is None or not np.isfinite(x):
        return "—"
    return f"{x * 100:+.{digits}f}%"


def format_multiple(x: float) -> str:
    if x is None or not np.isfinite(x):
        return "—"
    return f"{x:.2f}x"
