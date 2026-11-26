"""Capped inverse-vol and risk-parity weights.

Both functions are pure: they take a volatility vector or a covariance matrix
and return long-only weights that sum to `gross`. They do not know about dates,
taxes or the engine; the caller is responsible for forming a causal estimate.

Risk parity is solved by cyclical coordinate descent on the equal-risk-contribution
problem. For two assets the solution collapses to inverse vol regardless of
correlation; for a diagonal covariance it is inverse vol for every n. Caps are
enforced by clipping and renormalising inside the iteration, so a capped solution
is "risk parity subject to the cap", not an exact unconstrained solution.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def _validate(gross: float, cap: float, n: int) -> None:
    if not np.isfinite(gross) or gross <= 0:
        raise ValueError("gross must be a positive finite number")
    if not np.isfinite(cap) or cap <= 0:
        raise ValueError("cap must be a positive finite number")
    if cap * n < gross - 1e-12:
        raise ValueError(f"cap {cap} is infeasible for {n} assets and gross {gross}")


def inverse_vol_weights(
    sigma: pd.Series | dict[str, float],
    gross: float = 1.0,
    cap: float = 1.0,
) -> pd.Series:
    """Long-only inverse-vol weights, iteratively capped and renormalised.

    Non-finite or non-positive volatilities are dropped. If nothing is left the
    result is an empty Series; the caller decides whether that means cash.
    """
    s = pd.Series(sigma, dtype=float).dropna()
    s = s[np.isfinite(s) & (s > 0)]
    if s.empty:
        return pd.Series(dtype=float, name="weight")
    _validate(gross, cap, len(s))
    raw = 1.0 / s
    w = raw / raw.sum() * gross
    for _ in range(100):
        over = w > cap + 1e-12
        if not over.any():
            break
        excess = float((w[over] - cap).sum())
        w[over] = cap
        room = ~over
        if not room.any():
            break
        w[room] = w[room] + excess * w[room] / w[room].sum()
    # one final normalisation for floating-point drift
    w = w / w.sum() * gross
    w.name = "weight"
    return w


def risk_contributions(cov: pd.DataFrame, weights: pd.Series) -> pd.Series:
    """Variance contribution of each asset: w_i * (Sigma w)_i."""
    c = (
        pd.DataFrame(cov)
        .astype(float)
        .reindex(index=weights.index, columns=weights.index)
    )
    w = weights.astype(float)
    return w * (c.to_numpy() @ w.to_numpy())


def portfolio_vol(cov: pd.DataFrame, weights: pd.Series) -> float:
    """Annualized portfolio volatility from a covariance matrix and weights."""
    c = (
        pd.DataFrame(cov)
        .astype(float)
        .reindex(index=weights.index, columns=weights.index)
    )
    w = weights.to_numpy(dtype=float)
    var = float(w @ c.to_numpy() @ w)
    return float(np.sqrt(max(var, 0.0)))


def risk_parity_weights(
    cov: pd.DataFrame,
    gross: float = 1.0,
    cap: float = 1.0,
    iters: int = 1000,
    tol: float = 1e-12,
) -> pd.Series:
    """Long-only equal-risk-contribution weights with a per-asset cap.

    `cov` must be a square, symmetric, finite covariance matrix with a strictly
    positive diagonal. The iteration is deterministic; it returns the last
    feasible iterate if `tol` is not reached.
    """
    c = pd.DataFrame(cov).astype(float)
    if c.empty or c.shape[0] != c.shape[1]:
        raise ValueError("cov must be a non-empty square matrix")
    if list(c.index) != list(c.columns):
        raise ValueError("cov index and columns must match")
    values = c.to_numpy()
    if not np.isfinite(values).all():
        raise ValueError("cov must be finite")
    if not np.allclose(values, values.T, atol=1e-12):
        raise ValueError("cov must be symmetric")
    diag = np.diag(values)
    if (diag <= 0).any():
        raise ValueError("cov diagonal must be strictly positive")
    _validate(gross, cap, len(c))

    names = list(c.index)
    vol = pd.Series(np.sqrt(diag), index=names)
    w = inverse_vol_weights(vol, gross=gross, cap=cap).to_numpy(dtype=float)

    for _ in range(int(iters)):
        cw = values @ w
        pv = float(w @ cw)
        if pv <= 0:
            break
        target = pv / len(w)
        new = w.copy()
        for i in range(len(w)):
            a = values[i, i]
            b = float(cw[i] - a * w[i])
            disc = b * b + 4.0 * a * target
            new[i] = (-b + np.sqrt(max(disc, 0.0))) / (2.0 * a)
        new = np.clip(new, 0.0, cap)
        total = float(new.sum())
        if total <= 0:
            break
        new = new / total * gross
        if np.max(np.abs(new - w)) < tol:
            w = new
            break
        w = new

    out = pd.Series(w, index=names, name="weight", dtype=float)
    return out
