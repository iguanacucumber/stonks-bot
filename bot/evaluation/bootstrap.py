"""Bootstrap confidence bands for terminal multiple and max drawdown.

Two block schemes are supported:

``stationary``
    Politis-Romano stationary bootstrap: geometric block lengths with mean
    ``expected_block``; the next observation continues the current block with
    probability ``1 - 1/expected_block``, otherwise a new random block starts.

``block``
    Circular fixed-length block bootstrap.  This is the exact scheme used by the
    existing ``scripts/robustness*.py`` reports: draw ``ceil(m / block)`` random
    starts and stitch ``block`` consecutive observations (wrapping at the end),
    then truncate to ``m``.  Reusing it keeps new numbers comparable with the old
    tables; ``window`` is a non-overlapping variant for sensitivity.

The default is 10,000 paths, as required by ``docs/research-protocol.md``.  Paths
start from wealth 1.0, so a first-day loss is included in the drawdown (the old
one-off scripts started the peak at the first simulated price; the difference is
one observation and does not move the reported p5 by a meaningful amount).
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math

import numpy as np
import pandas as pd

TRADING_DAYS = 252


@dataclass
class BootstrapResult:
    method: str
    n_paths: int
    n_days: int
    block: int
    seed: int
    terminal: np.ndarray = field(repr=False)
    max_dd: np.ndarray = field(repr=False)
    after_tax_terminal: np.ndarray | None = field(default=None, repr=False)

    def summary(self) -> dict[str, float]:
        out = {
            "method": self.method,
            "n_paths": float(self.n_paths),
            "n_days": float(self.n_days),
            "block": float(self.block),
            "terminal_p5": float(np.quantile(self.terminal, 0.05)),
            "terminal_p50": float(np.quantile(self.terminal, 0.50)),
            "terminal_p95": float(np.quantile(self.terminal, 0.95)),
            "max_dd_p5": float(np.quantile(self.max_dd, 0.05)),
            "max_dd_p50": float(np.quantile(self.max_dd, 0.50)),
            "max_dd_p95": float(np.quantile(self.max_dd, 0.95)),
            "prob_loss": float((self.terminal < 1.0).mean()),
        }
        if self.after_tax_terminal is not None:
            out.update(
                {
                    "after_tax_p5": float(np.quantile(self.after_tax_terminal, 0.05)),
                    "after_tax_p50": float(np.quantile(self.after_tax_terminal, 0.50)),
                    "after_tax_p95": float(np.quantile(self.after_tax_terminal, 0.95)),
                    "after_tax_prob_loss": float(
                        (self.after_tax_terminal < 1.0).mean()
                    ),
                }
            )
        return out


def _clean_returns(returns) -> np.ndarray:
    if isinstance(returns, pd.Series):
        x = returns.dropna().to_numpy(dtype=float)
    else:
        x = np.asarray(returns, dtype=float)
        x = x[np.isfinite(x)]
    if len(x) < 2:
        raise ValueError("bootstrap needs at least 2 finite returns")
    return x


def _block_indices(
    rng: np.random.Generator, m: int, n_paths: int, block: int, circular: bool
) -> np.ndarray:
    if block < 1:
        raise ValueError("block must be >= 1")
    nb = int(math.ceil(m / block))
    starts = rng.integers(0, m, size=(n_paths, nb))
    offsets = np.arange(block)
    if circular:
        idx = (starts[:, :, None] + offsets[None, None, :]) % m
    else:
        idx = starts[:, :, None] + offsets[None, None, :]
        idx = np.minimum(idx, m - 1)
    return idx.reshape(n_paths, nb * block)[:, :m]


def _stationary_indices(
    rng: np.random.Generator,
    m: int,
    n_paths: int,
    expected_block: float,
) -> np.ndarray:
    if expected_block < 1:
        raise ValueError("expected_block must be >= 1")
    p = 1.0 / float(expected_block)
    idx = np.empty((n_paths, m), dtype=np.int64)
    idx[:, 0] = rng.integers(0, m, size=n_paths)
    for t in range(1, m):
        new = rng.random(n_paths) < p
        fresh = rng.integers(0, m, size=n_paths)
        idx[:, t] = np.where(new, fresh, (idx[:, t - 1] + 1) % m)
    return idx


def _path_stats(returns: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Terminal multiple and max drawdown for each row of a path matrix."""
    wealth = np.cumprod(1.0 + returns, axis=1)
    terminal = wealth[:, -1]
    with_initial = np.concatenate([np.ones((wealth.shape[0], 1)), wealth], axis=1)
    peak = np.maximum.accumulate(with_initial, axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        dd = np.where(peak > 0, with_initial / peak - 1.0, 0.0)
    max_dd = np.nanmin(dd, axis=1)
    return terminal, max_dd


def bootstrap(
    returns,
    n_paths: int = 10_000,
    method: str = "stationary",
    block: int = 21,
    expected_block: float | None = None,
    seed: int = 7,
    tax_rate: float | None = None,
    chunk: int = 512,
) -> BootstrapResult:
    """Bootstrap daily returns and return the full path distribution.

    ``tax_rate`` adds an approximate after-tax terminal path: only gains above
    1.0 are taxed and losses are not refunded (French loss carry-forward, not a
    negative tax).  It is a terminal-gain approximation, not a rerun of the
    annual tax engine.
    """
    x = _clean_returns(returns)
    m = len(x)
    if method not in ("stationary", "block", "window"):
        raise ValueError("method must be stationary, block or window")
    if n_paths < 1:
        raise ValueError("n_paths must be >= 1")
    expected_block = float(block if expected_block is None else expected_block)
    rng = np.random.default_rng(seed)
    terminal = np.empty(n_paths)
    max_dd = np.empty(n_paths)
    done = 0
    while done < n_paths:
        k = min(chunk, n_paths - done)
        if method == "stationary":
            idx = _stationary_indices(rng, m, k, expected_block)
        elif method == "block":
            idx = _block_indices(rng, m, k, block, circular=True)
        else:
            # Non-overlapping circular blocks: start at block boundaries with a
            # random rotation.  Useful as a conservative sensitivity check.
            nb = int(math.ceil(m / block))
            rotation = rng.integers(0, block, size=(k, 1))
            starts = (
                rotation
                + np.arange(nb)[None, :] * block
                + rng.integers(0, m, size=(k, 1))
            ) % m
            idx = (starts[:, :, None] + np.arange(block)[None, None, :]) % m
            idx = idx.reshape(k, nb * block)[:, :m]
        sampled = x[idx]
        term, dd = _path_stats(sampled)
        terminal[done : done + k] = term
        max_dd[done : done + k] = dd
        done += k

    after = None
    if tax_rate is not None:
        if not 0.0 <= tax_rate <= 1.0:
            raise ValueError("tax_rate must be in [0, 1]")
        after = np.where(
            terminal <= 1.0,
            terminal,
            1.0 + (terminal - 1.0) * (1.0 - tax_rate),
        )
    return BootstrapResult(
        method=method,
        n_paths=n_paths,
        n_days=m,
        block=int(round(expected_block if method == "stationary" else block)),
        seed=seed,
        terminal=terminal,
        max_dd=max_dd,
        after_tax_terminal=after,
    )


def probability_of_loss(returns, n_paths: int = 10_000, **kwargs) -> float:
    """Share of bootstrap paths ending below the starting wealth."""
    res = bootstrap(returns, n_paths=n_paths, **kwargs)
    return float((res.terminal < 1.0).mean())


def bootstrap_table(returns, n_paths: int = 10_000, seed: int = 7) -> pd.DataFrame:
    """Rows for stationary and circular-block bootstraps with the same seed."""
    rows = []
    for method in ("stationary", "block"):
        res = bootstrap(
            returns,
            n_paths=n_paths,
            method=method,
            block=21,
            expected_block=21,
            seed=seed,
        )
        row = res.summary()
        rows.append(row)
    return pd.DataFrame(rows)
