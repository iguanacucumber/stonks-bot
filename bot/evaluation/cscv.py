"""Multiple-testing corrections: CSCV PBO and the deflated Sharpe ratio.

Two independent diagnostics are provided because they answer different questions:

* **CSCV / probability of backtest overfitting (PBO)** asks: when the best trial
  in-sample is selected from N candidates, how often does it land below the
  median out-of-sample?  It needs a T x N matrix of candidate returns.
* **Deflated Sharpe ratio (DSR)** asks: given N trials and the skew/kurtosis of
  the selected track, is its Sharpe still significant after accounting for the
  maximum expected Sharpe under the null?  It needs one selected return series
  plus the trial count (and optionally the cross-trial Sharpe dispersion).

References:
  Bailey, Borwein, López de Prado & Zhu (2014), "Pseudo-mathematics and
  financial charlatanism: the effects of backtest overfitting on out-of-sample
  performance"; Bailey & López de Prado (2014), "The deflated Sharpe ratio".
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
import math
from statistics import NormalDist

import numpy as np
import pandas as pd

TRADING_DAYS = 252
EULER_GAMMA = 0.5772156649015329


@dataclass
class CSCVResult:
    pbo: float
    n_splits: int
    n_combinations: int
    n_trials: int
    n_obs: int
    median_oos_rank: float
    median_is_sharpe: float
    median_oos_sharpe: float
    degradation: float

    def as_dict(self) -> dict[str, float]:
        return {
            "pbo": self.pbo,
            "n_splits": float(self.n_splits),
            "n_combinations": float(self.n_combinations),
            "n_trials": float(self.n_trials),
            "n_obs": float(self.n_obs),
            "median_oos_rank": self.median_oos_rank,
            "median_is_sharpe": self.median_is_sharpe,
            "median_oos_sharpe": self.median_oos_sharpe,
            "degradation": self.degradation,
        }


def _sharpe_columns(x: np.ndarray) -> np.ndarray:
    """Daily Sharpe per column, NaN if a column has no dispersion."""
    mean = x.mean(axis=0)
    sd = x.std(axis=0, ddof=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(sd > 0, mean / sd, np.nan)


def cscv_pbo(
    returns: pd.DataFrame | np.ndarray,
    n_splits: int = 10,
    dropna: bool = True,
) -> CSCVResult:
    """Combinatorially symmetric cross-validation PBO.

    ``returns`` is a T x N matrix: rows are time, columns are trials.  The sample
    is cut into ``n_splits`` contiguous time blocks (an even number, default 10).
    For every balanced split, the in-sample half is the complement of the
    out-of-sample half.  The best in-sample trial is selected by Sharpe, then its
    out-of-sample rank is recorded.  PBO is the share of splits where the selected
    trial's OOS rank is at or below the median (logit <= 0).

    Ties use average ranks.  A trial that is constantly zero or constant is
    excluded from selection if its Sharpe is NaN.
    """
    if isinstance(returns, pd.DataFrame):
        frame = returns.astype(float)
    else:
        frame = pd.DataFrame(np.asarray(returns, dtype=float))
    if dropna:
        frame = frame.dropna(how="any")
    if frame.isna().any().any():
        raise ValueError("returns contain NaN; pass dropna=True or clean them")
    # A constant trial has no rankable Sharpe; drop it rather than inventing one.
    dispersive = frame.std(axis=0, ddof=1) > 0
    frame = frame.loc[:, dispersive]
    if n_splits < 4 or n_splits % 2:
        raise ValueError("n_splits must be an even integer >= 4")
    t, n = frame.shape
    if n < 2:
        raise ValueError("CSCV needs at least 2 trials")
    if t < 2 * n_splits:
        raise ValueError("too few observations for the requested n_splits")
    x = frame.to_numpy(dtype=float)
    blocks = np.array_split(np.arange(t), n_splits)
    half = n_splits // 2
    ranks: list[float] = []
    is_sharpes: list[float] = []
    oos_sharpes: list[float] = []
    for chosen in combinations(range(n_splits), half):
        chosen = set(chosen)
        is_idx = np.concatenate([blocks[i] for i in range(n_splits) if i in chosen])
        oos_idx = np.concatenate(
            [blocks[i] for i in range(n_splits) if i not in chosen]
        )
        is_perf = _sharpe_columns(x[is_idx])
        oos_perf = _sharpe_columns(x[oos_idx])
        if not np.isfinite(is_perf).any():
            continue
        best = int(np.nanargmax(is_perf))
        if not np.isfinite(oos_perf[best]):
            continue
        finite = np.isfinite(oos_perf)
        n_finite = int(finite.sum())
        if n_finite < 2:
            continue
        # Average rank (1 = worst) of the selected trial among the finite set.
        selected_value = oos_perf[best]
        less = float(np.sum(oos_perf[finite] < selected_value))
        equal = float(np.sum(oos_perf[finite] == selected_value))
        own = less + (equal + 1.0) / 2.0
        ranks.append(own)
        is_sharpes.append(float(is_perf[best]))
        oos_sharpes.append(float(selected_value))
    if not ranks:
        raise ValueError("no valid CSCV combinations")
    ranks_arr = np.asarray(ranks)
    n_finite = n  # all columns are finite after dropna
    logits = np.log((ranks_arr / (n_finite + 1)) / (1.0 - ranks_arr / (n_finite + 1)))
    degradation = float(np.median(oos_sharpes) - np.median(is_sharpes))
    return CSCVResult(
        pbo=float((logits <= 0).mean()),
        n_splits=n_splits,
        n_combinations=len(ranks),
        n_trials=n,
        n_obs=t,
        median_oos_rank=float(np.median(ranks_arr)),
        median_is_sharpe=float(np.median(is_sharpes)),
        median_oos_sharpe=float(np.median(oos_sharpes)),
        degradation=degradation,
    )


def expected_max_sharpe(n_trials: int, trial_sr_std: float) -> float:
    """Expected maximum Sharpe under the null for N independent trials.

    ``trial_sr_std`` is the cross-trial standard deviation of (non-annualised)
    Sharpe ratios.  Formula from Bailey & López de Prado (2014), eq. 8.
    """
    if n_trials <= 1:
        return 0.0
    if trial_sr_std < 0 or not math.isfinite(trial_sr_std):
        raise ValueError("trial_sr_std must be finite and >= 0")
    z1 = NormalDist().inv_cdf(1.0 - 1.0 / n_trials)
    z2 = NormalDist().inv_cdf(1.0 - 1.0 / (n_trials * math.e))
    return float(trial_sr_std * ((1.0 - EULER_GAMMA) * z1 + EULER_GAMMA * z2))


def deflated_sharpe(
    returns,
    n_trials: int,
    trial_sharpes=None,
    periods_per_year: int = TRADING_DAYS,
) -> dict[str, float]:
    """Deflated Sharpe ratio and one-sided p-value for a selected track.

    The Sharpe ratios in the formula are **per-observation** (daily), not
    annualised: ``mean(r) / std(r)``.  The result's ``sharpe_annual`` is provided
    for display only.
    """
    r = pd.Series(returns, dtype=float).dropna().to_numpy()
    n = len(r)
    if n < 3:
        raise ValueError("need at least 3 returns")
    if n_trials < 1:
        raise ValueError("n_trials must be >= 1")
    mu = float(r.mean())
    sd = float(r.std(ddof=1))
    if sd <= 0 or not math.isfinite(sd):
        raise ValueError("returns have no dispersion; Sharpe is undefined")
    sr = mu / sd
    skew = float(pd.Series(r).skew())
    kurt = float(pd.Series(r).kurt() + 3.0)  # convert excess to raw kurtosis
    if trial_sharpes is not None:
        trial_sr = pd.Series(trial_sharpes, dtype=float).dropna().to_numpy()
        if len(trial_sr) < 2:
            raise ValueError("trial_sharpes needs at least 2 values")
        sr_std = float(trial_sr.std(ddof=1))
    else:
        # Variance of an estimated Sharpe under the null; used when only the
        # selected track and the trial count are available.
        sr_std = float(
            math.sqrt(
                max(0.0, 1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr * sr) / max(1, n - 1)
            )
        )
    sr0 = expected_max_sharpe(n_trials, sr_std)
    variance_term = 1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr * sr
    if variance_term <= 0:
        raise ValueError("invalid skew/kurtosis adjustment")
    z = (sr - sr0) * math.sqrt(max(1, n - 1)) / math.sqrt(variance_term)
    dsr = float(NormalDist().cdf(z))
    return {
        "sharpe_daily": float(sr),
        "sharpe_annual": float(sr * math.sqrt(periods_per_year)),
        "expected_max_sharpe_daily": float(sr0),
        "deflated_sharpe": dsr,
        "p_value": float(1.0 - dsr),
        "z": float(z),
        "n_obs": float(n),
        "n_trials": float(n_trials),
        "skew": skew,
        "kurtosis": kurt,
    }
