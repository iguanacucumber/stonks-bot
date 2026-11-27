"""Metrics and inference for the PEAD experiment.

All functions are deterministic given their seed. No network access.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from bot.backtest import fr_rate
from bot.evaluation.cscv import cscv_pbo, deflated_sharpe
from bot.research.metrics import (
    annual_return,
    cagr,
    max_drawdown,
    sharpe,
    summarize_equity,
    worst_12m,
)
from bot.research.short3.daily import after_tax_series

from .events import eligible, reaction_mask
from .portfolio import PortfolioResult, event_returns


def after_tax_metrics(
    daily: pd.Series,
    *,
    cost_bps: float = 5.0,
) -> dict:
    """Pre-tax and annual-PFU after-tax path for a daily net return series."""
    r = daily.fillna(0.0).astype(float)
    eq = (1.0 + r).cumprod()
    eq.name = "equity"
    eq_at, tax = after_tax_series(r)
    years = max(len(r) / 252.0, 1e-9)
    return {
        "n_days": int(len(r)),
        "pretax_multiple": float(eq.iloc[-1]) if len(eq) else float("nan"),
        "after_tax_multiple": float(eq_at.iloc[-1]) if len(eq_at) else float("nan"),
        "after_tax_tax": float(tax),
        "pretax_cagr": float(eq.iloc[-1] ** (1.0 / years) - 1.0)
        if len(eq)
        else float("nan"),
        "after_tax_cagr": float(eq_at.iloc[-1] ** (1.0 / years) - 1.0)
        if len(eq_at)
        else float("nan"),
        "maxdd": max_drawdown(eq),
        "after_tax_maxdd": max_drawdown(eq_at),
        "sharpe": sharpe(r),
        "vol": float(r.std() * math.sqrt(252.0)),
        "worst_12m": worst_12m(eq),
        "cost_bps": float(cost_bps),
    }


def terminal_after_tax_metrics(
    daily: pd.Series,
    *,
    end_year: int,
    cost_bps: float = 5.0,
) -> dict:
    """Buy-and-hold metrics: PFU on the terminal gain, not annually.

    A passive ETF realises capital gains only when sold. Taxing the full
    annual return would overstate the QQQ hurdle and is not the repo's
    buy-and-hold convention (`france.after_tax` uses terminal unrealized tax).
    """
    r = daily.fillna(0.0).astype(float)
    eq = (1.0 + r).cumprod()
    eq.name = "equity"
    pre = float(eq.iloc[-1]) if len(eq) else float("nan")
    tax = max(0.0, pre - 1.0) * fr_rate(int(end_year))
    after = pre - tax
    years = max(len(r) / 252.0, 1e-9)
    return {
        "n_days": int(len(r)),
        "pretax_multiple": pre,
        "after_tax_multiple": float(after),
        "after_tax_tax": float(tax),
        "pretax_cagr": float(pre ** (1.0 / years) - 1.0) if len(eq) else float("nan"),
        "after_tax_cagr": float(after ** (1.0 / years) - 1.0)
        if len(eq)
        else float("nan"),
        "maxdd": max_drawdown(eq),
        "after_tax_maxdd": max_drawdown(eq),
        "sharpe": sharpe(r),
        "vol": float(r.std() * math.sqrt(252.0)),
        "worst_12m": worst_12m(eq),
        "cost_bps": float(cost_bps),
    }


def event_stats(returns: pd.DataFrame, *, n_boot: int = 5000, seed: int = 7) -> dict:
    """Descriptive event-level stats and a month-block bootstrap CI."""
    if returns.empty:
        return {
            "n": 0,
            "mean_gross": float("nan"),
            "mean_net": float("nan"),
            "median_net": float("nan"),
            "hit": float("nan"),
            "t": float("nan"),
            "ci_low": float("nan"),
            "ci_high": float("nan"),
        }
    r = returns["net"].to_numpy(dtype=float)
    n = len(r)
    mean = float(r.mean())
    sd = float(r.std(ddof=1)) if n > 1 else float("nan")
    t = mean / sd * math.sqrt(n) if sd and math.isfinite(sd) else float("nan")
    rng = np.random.default_rng(seed)
    boot = np.empty(n_boot)
    if "entry_date" in returns.columns:
        frame = returns[["entry_date", "net"]].dropna().sort_values("entry_date")
        groups = [
            g["net"].to_numpy(dtype=float)
            for _, g in frame.groupby(frame["entry_date"].dt.to_period("M"))
        ]
    else:
        groups = []
    if len(groups) >= 2:
        # Month blocks preserve earnings-season clustering and overlapping
        # five-day returns better than resampling individual events.
        for k in range(n_boot):
            pick = rng.integers(0, len(groups), size=len(groups))
            boot[k] = float(np.concatenate([groups[i] for i in pick]).mean())
    else:
        for k in range(n_boot):
            boot[k] = rng.choice(r, size=n, replace=True).mean()
    return {
        "n": int(n),
        "mean_gross": float(returns["gross"].mean()),
        "mean_net": mean,
        "median_net": float(np.median(r)),
        "hit": float((r > 0).mean()),
        "t": float(t),
        "ci_low": float(np.quantile(boot, 0.025)),
        "ci_high": float(np.quantile(boot, 0.975)),
    }


def _arrays_for_ticker(bars: pd.DataFrame) -> pd.DataFrame:
    """Reaction and forward-return arrays indexed by entry session."""
    close = bars["close"].to_numpy(dtype=float)
    open_ = bars["open"].to_numpy(dtype=float)
    volume = bars["volume"].to_numpy(dtype=float)
    n = len(close)
    out = pd.DataFrame(index=np.arange(n))
    gap = np.full(n, np.nan)
    gap[1:] = open_[1:] / close[:-1] - 1.0
    cc = np.full(n, np.nan)
    cc[2:] = close[1:-1] / close[:-2] - 1.0
    oc = np.full(n, np.nan)
    oc[1:] = close[:-1] / open_[:-1] - 1.0
    vz = np.full(n, np.nan)
    for i in range(21, n):
        hist = volume[i - 21 : i - 1]
        sd = float(hist.std(ddof=1))
        if sd > 0:
            vz[i] = (volume[i - 1] - float(hist.mean())) / sd
    out["gap"] = gap
    out["close_close"] = cc
    out["open_close"] = oc
    out["volume_z"] = vz
    return out


def _eligible_pool(
    arrays: pd.DataFrame, reaction: str, hold: int, n_bars: int
) -> np.ndarray:
    mask = reaction_mask(arrays, reaction).to_numpy()
    ok = mask & np.isfinite(arrays["gap"].to_numpy())
    max_entry = n_bars - hold
    idx = np.flatnonzero(ok & (np.arange(len(arrays)) <= max_entry))
    return idx


def placebo_pvalue(
    timing: pd.DataFrame,
    bars: dict[str, pd.DataFrame],
    *,
    hold: int,
    reaction: str,
    cost_bps: float = 5.0,
    n: int = 500,
    seed: int = 11,
) -> dict:
    """Random-session placebo for the event-level mean.

    Random entries are drawn with replacement from sessions that satisfy the
    same reaction filter, matched to the actual event count per ticker. This
    asks whether the event dates add anything beyond the unconditional filter.
    """
    actual = event_returns(
        timing, bars, hold=hold, cost_bps=cost_bps, reaction=reaction
    )
    if actual.empty:
        return {"n": 0, "actual_mean": float("nan"), "p_value": float("nan")}
    counts = actual.groupby("ticker").size().to_dict()
    cache = {t: _arrays_for_ticker(bars[t]) for t in counts}
    pools = {t: _eligible_pool(cache[t], reaction, hold, len(bars[t])) for t in counts}
    rng = np.random.default_rng(seed)
    sums = np.zeros(n)
    totals = np.zeros(n)
    for ticker, count in counts.items():
        pool = pools[ticker]
        if len(pool) == 0:
            continue
        pick = rng.choice(pool, size=(n, count), replace=True)
        frame = bars[ticker]
        open_ = frame["open"].to_numpy(dtype=float)
        close = frame["close"].to_numpy(dtype=float)
        net = close[pick + hold - 1] / open_[pick] - 1.0 - 2.0 * cost_bps / 1e4
        sums += net.sum(axis=1)
        totals += count
    means = np.divide(sums, totals, out=np.full(n, np.nan), where=totals > 0)
    actual_mean = float(actual["net"].mean())
    return {
        "n": int(len(actual)),
        "actual_mean": actual_mean,
        "placebo_mean": float(np.nanmean(means)),
        "placebo_p95": float(np.nanquantile(means, 0.95)),
        "p_value": float((1.0 + np.sum(means >= actual_mean)) / (n + 1.0)),
    }


def placebo_portfolio(
    timing: pd.DataFrame,
    bars: dict[str, pd.DataFrame],
    result: PortfolioResult,
    *,
    hold: int,
    reaction: str,
    cost_bps: float,
    n: int = 500,
    seed: int = 11,
) -> dict:
    """Random-entry placebo for the full portfolio path."""
    from .portfolio import simulate

    actual = result.equity.iloc[-1] / result.equity.iloc[0] - 1.0
    events = eligible(timing, bars, hold=hold, reaction=reaction)
    if events.empty:
        return {"actual_return": float("nan"), "p_value": float("nan")}
    counts = events.groupby("ticker").size().to_dict()
    cache = {t: _arrays_for_ticker(bars[t]) for t in counts}
    pools = {t: _eligible_pool(cache[t], reaction, hold, len(bars[t])) for t in counts}
    rng = np.random.default_rng(seed)
    finals = np.empty(n)
    for k in range(n):
        rows = []
        for ticker, count in counts.items():
            pool = pools[ticker]
            if len(pool) == 0:
                continue
            pick = rng.choice(pool, size=count, replace=True)
            frame = bars[ticker]
            for i in pick:
                rows.append(
                    {
                        "ticker": ticker,
                        "accession": f"placebo-{k}-{ticker}-{i}",
                        "filed": frame.index[int(i)],
                        "event_time": frame.index[int(i)],
                        "entry_date": frame.index[int(i)],
                        "entry_idx": int(i),
                        "entry_open": float(frame["open"].iloc[int(i)]),
                        "prev_close": float(frame["close"].iloc[int(i) - 1]),
                        "gap": float(cache[ticker]["gap"].iloc[int(i)]),
                        "close_close": float(cache[ticker]["close_close"].iloc[int(i)]),
                        "open_close": float(cache[ticker]["open_close"].iloc[int(i)]),
                        "volume_z": float(cache[ticker]["volume_z"].iloc[int(i)]),
                        "timestamp_source": "placebo",
                    }
                )
        if not rows:
            finals[k] = float("nan")
            continue
        path = simulate(
            pd.DataFrame(rows),
            bars,
            hold=hold,
            cost_bps=cost_bps,
            reaction=reaction,
            cash_yield=not result.long_short,
            long_short=result.long_short,
        )
        finals[k] = path.equity.iloc[-1] / path.equity.iloc[0] - 1.0
    return {
        "actual_return": float(actual),
        "placebo_mean": float(np.nanmean(finals)),
        "placebo_p95": float(np.nanquantile(finals, 0.95)),
        "p_value": float((1.0 + np.sum(finals >= actual)) / (n + 1.0)),
    }


def _block_indices(
    rng: np.random.Generator, n_obs: int, n_paths: int, block: int
) -> np.ndarray:
    n_blocks = int(np.ceil(n_obs / block))
    starts = rng.integers(0, n_obs, size=(n_paths, n_blocks))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]).reshape(
        n_paths, n_blocks * block
    )
    return idx[:, :n_obs] % n_obs


def paired_bootstrap(
    strategy: pd.Series,
    benchmark: pd.Series,
    *,
    n_paths: int = 5000,
    block: int = 21,
    seed: int = 7,
) -> dict:
    """Paired block bootstrap of terminal multiples and max drawdowns."""
    frame = pd.concat(
        [strategy.rename("strategy"), benchmark.rename("benchmark")], axis=1
    ).dropna()
    if len(frame) < block * 2:
        return {
            "p5_strategy": float("nan"),
            "p5_maxdd": float("nan"),
            "prob_beat": float("nan"),
            "median_gap": float("nan"),
        }
    rng = np.random.default_rng(seed)
    idx = _block_indices(rng, len(frame), n_paths, block)
    s = frame["strategy"].to_numpy()[idx]
    b = frame["benchmark"].to_numpy()[idx]
    paths_s = np.cumprod(1.0 + s, axis=1)
    paths_b = np.cumprod(1.0 + b, axis=1)
    dd = (paths_s / np.maximum.accumulate(paths_s, axis=1) - 1.0).min(axis=1)
    return {
        "p5_strategy": float(np.quantile(paths_s[:, -1], 0.05)),
        "p95_strategy": float(np.quantile(paths_s[:, -1], 0.95)),
        "median_strategy": float(np.median(paths_s[:, -1])),
        "p5_maxdd": float(np.quantile(dd, 0.05)),
        "prob_beat": float((paths_s[:, -1] > paths_b[:, -1]).mean()),
        "median_gap": float(np.median(paths_s[:, -1] - paths_b[:, -1])),
    }


def year_table(
    daily: pd.Series,
    qqq: pd.Series,
    bil: pd.Series,
) -> pd.DataFrame:
    """Calendar-year pre-tax and approximate after-tax returns."""
    frame = pd.concat(
        [daily.rename("strategy"), qqq.rename("qqq"), bil.rename("bil")], axis=1
    ).dropna()
    rows = []
    for year, g in frame.groupby(frame.index.year):

        def comp(series: pd.Series) -> float:
            return float((1.0 + series).prod() - 1.0)

        s = comp(g["strategy"])
        q = comp(g["qqq"])
        b = comp(g["bil"])
        rate = fr_rate(int(year))
        rows.append(
            {
                "year": int(year),
                "strategy_pretax": s,
                "strategy_after_tax": s - max(0.0, s) * rate,
                "qqq": q,
                "bil": b,
                "excess_bil": s - b,
                "excess_qqq": s - q,
            }
        )
    return pd.DataFrame(rows)


def gate_table(
    primary: dict,
    qqq: dict,
    bil: dict,
    years: pd.DataFrame,
    *,
    placebo_event_p: float,
    placebo_portfolio_p: float,
    dsr: float,
    max_concurrent: int,
) -> pd.DataFrame:
    """Apply the pre-registered pass/fail gates to the primary."""
    if years.empty:
        rows = []
    else:
        pos = float((years["strategy_after_tax"] > 0).mean())
        beat = float((years["strategy_after_tax"] > years["bil"]).mean())
        best_idx = years["strategy_after_tax"].idxmax()
        ex = years.drop(index=best_idx)
        ex_strategy = float((1.0 + ex["strategy_after_tax"]).prod() - 1.0)
        ex_bil = float((1.0 + ex["bil"]).prod() - 1.0)
        rows = [
            {
                "gate": "1x after-tax > BIL",
                "value": primary["after_tax_multiple"],
                "hurdle": bil["after_tax_multiple"],
                "pass": primary["after_tax_multiple"] > bil["after_tax_multiple"],
            },
            {
                "gate": "1x after-tax > QQQ",
                "value": primary["after_tax_multiple"],
                "hurdle": qqq["after_tax_multiple"],
                "pass": primary["after_tax_multiple"] > qqq["after_tax_multiple"],
            },
            {
                "gate": "2x after-tax > BIL",
                "value": primary.get("after_tax_multiple_2x", float("nan")),
                "hurdle": bil.get("after_tax_multiple_2x", float("nan")),
                "pass": primary.get("after_tax_multiple_2x", -1)
                > bil.get("after_tax_multiple_2x", 1),
            },
            {
                "gate": "2x after-tax > QQQ",
                "value": primary.get("after_tax_multiple_2x", float("nan")),
                "hurdle": qqq.get("after_tax_multiple_2x", float("nan")),
                "pass": primary.get("after_tax_multiple_2x", -1)
                > qqq.get("after_tax_multiple_2x", 1),
            },
            {
                "gate": "event placebo p<0.05",
                "value": placebo_event_p,
                "hurdle": 0.05,
                "pass": placebo_event_p < 0.05,
            },
            {
                "gate": "portfolio placebo p<0.05",
                "value": placebo_portfolio_p,
                "hurdle": 0.05,
                "pass": placebo_portfolio_p < 0.05,
            },
            {"gate": "DSR>=0.95", "value": dsr, "hurdle": 0.95, "pass": dsr >= 0.95},
            {
                "gate": ">=60% years positive",
                "value": pos,
                "hurdle": 0.60,
                "pass": pos >= 0.60,
            },
            {
                "gate": ">=60% years beat BIL",
                "value": beat,
                "hurdle": 0.60,
                "pass": beat >= 0.60,
            },
            {
                "gate": "ex-best year > BIL",
                "value": ex_strategy,
                "hurdle": ex_bil,
                "pass": ex_strategy > ex_bil,
            },
            {
                "gate": "max concurrent <=10",
                "value": max_concurrent,
                "hurdle": 10,
                "pass": max_concurrent <= 10,
            },
        ]
    return pd.DataFrame(rows)


def walk_forward_variants(
    daily: dict[tuple[str, int], pd.Series],
    bil: pd.Series,
    *,
    start_year: int = 2017,
) -> tuple[pd.Series, list[dict]]:
    """Expanding-year selection among long-only reaction/hold variants."""
    keys = sorted(daily)
    if not keys:
        return pd.Series(dtype=float), []
    frame = pd.DataFrame({f"{r}|{h}": daily[(r, h)] for r, h in keys}).sort_index()
    years = sorted({int(y) for y in frame.index.year if int(y) >= start_year})
    parts: list[pd.Series] = []
    records: list[dict] = []
    for year in years:
        train = frame.loc[frame.index.year < year]
        test = frame.loc[frame.index.year == year]
        best = None
        for key in frame.columns:
            r = train[key].dropna()
            if len(r) < 252:
                continue
            sd = float(r.std(ddof=1))
            score = float(r.mean() / sd) if sd > 0 else -1e9
            if best is None or score > best[0]:
                best = (score, key)
        if best is None or best[0] <= 0:
            test_ret = bil.reindex(test.index).fillna(0.0)
            chosen = "BIL"
            score = best[0] if best else float("nan")
        else:
            chosen = best[1]
            test_ret = test[chosen]
            score = best[0]
        parts.append(test_ret.rename(str(year)))
        records.append(
            {
                "year": int(year),
                "chosen": chosen,
                "train_sharpe": float(score) if np.isfinite(score) else float("nan"),
                "test_return": float((1.0 + test_ret).prod() - 1.0),
            }
        )
    if not parts:
        return pd.Series(dtype=float), records
    return pd.concat(parts).sort_index(), records


def trial_sharpes(daily: dict[tuple, pd.Series]) -> list[float]:
    out = []
    for series in daily.values():
        r = series.dropna()
        if len(r) < 30:
            continue
        sd = float(r.std(ddof=1))
        if sd > 0:
            out.append(float(r.mean() / sd))
    return out


def cscv_matrix(daily: dict[tuple, pd.Series]) -> pd.DataFrame:
    frame = pd.DataFrame(
        {f"{k[0]}|{k[1]}|{k[2]}": v for k, v in daily.items()}
    ).sort_index()
    return frame.fillna(0.0)


def summarize_result(
    result: PortfolioResult,
    *,
    cost_bps: float,
    qqq: pd.Series,
    bil: pd.Series,
) -> dict:
    daily = result.daily_returns
    m = after_tax_metrics(daily, cost_bps=cost_bps)
    m.update(
        {
            "n_signals": result.n_signals,
            "n_trades": result.n_entries,
            "skipped_slots": result.skipped_slots,
            "skipped_active": result.skipped_active,
            "skipped_cash": result.skipped_cash,
            "avg_positions": result.avg_positions,
            "avg_invested": result.avg_invested,
            "avg_cash": result.avg_cash,
            "max_concurrent": result.max_concurrent,
            "cash_yield": result.cash_yield,
        }
    )
    return m
