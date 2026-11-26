"""Walk-forward runner around the existing `bot.backtest` accounting engine.

Design choices that matter for honesty:

* Parameters are chosen on the training window only. The test window is never
  touched by selection.
* The test book starts with a warm-up window (default 400 calendar days) so
  month-end signals and carried positions exist on the first test day. Equity
  is sliced from the test start, so warm-up P&L is not counted.
* The concatenated out-of-sample path is built from test-fold daily returns.
  The after-tax multiple chains each fold's liquidation multiple, which is
  conservative: every fold pays its exit tax before the next fold starts.
* Drawdowns are reported on the pre-tax daily path, consistent with the
  existing shipped reports. The after-tax terminal is a liquidation value.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Iterable

import numpy as np
import pandas as pd

from bot import backtest, strategy

from .costs import CostModel, after_tax
from .dividends import (
    annual_dividend_summary,
    apply_dividend_tax,
    daily_dividend_yield,
)
from .ledger import replay as ledger_replay
from .metrics import (
    bootstrap_p5,
    deflated_sharpe,
    format_multiple,
    max_drawdown,
    summarize_equity,
)
from .registry import TrialRegistry
from .spec import StrategySpec

Selector = Callable[
    [strategy.Market, pd.Timestamp, list[str], set[str]], dict[str, float]
]


@dataclass(frozen=True)
class Fold:
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    label: str = ""


@dataclass
class WalkForwardResult:
    family: str
    spec: StrategySpec
    folds: list[dict]
    oos_returns: pd.Series
    oos_equity: pd.Series
    oos_benchmark: pd.Series
    after_tax_multiple: float
    after_tax_multiple_divadj: float
    ledger_multiple: float
    ledger_multiple_divadj: float
    metrics: dict
    bootstrap: dict
    dsr: dict
    trial_rows: list[dict]
    n_trials: int
    params_by_fold: list[dict] = field(default_factory=list)
    after_tax_multiple_eur: float | None = None
    oos_equity_eur: pd.Series | None = None
    eur_metrics: dict = field(default_factory=dict)
    eur_bootstrap: dict = field(default_factory=dict)

    def fold_table(self) -> list[dict]:
        return self.folds


def expanding_folds(
    start: str = "2015-01-01",
    end: str = "2026-12-31",
    first_train_end: str = "2018-12-31",
    last_test_start: str | None = None,
) -> list[Fold]:
    """Annual expanding folds: train through year Y, test year Y+1."""
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    train_end = pd.Timestamp(first_train_end)
    folds: list[Fold] = []
    while True:
        test_start = pd.Timestamp(year=train_end.year + 1, month=1, day=1)
        if last_test_start and test_start > pd.Timestamp(last_test_start):
            break
        if test_start > end_ts:
            break
        test_end = min(pd.Timestamp(year=test_start.year, month=12, day=31), end_ts)
        folds.append(
            Fold(
                train_start=start_ts.date().isoformat(),
                train_end=train_end.date().isoformat(),
                test_start=test_start.date().isoformat(),
                test_end=test_end.date().isoformat(),
                label=f"{test_start.year}",
            )
        )
        train_end = test_end
        if test_end >= end_ts:
            break
    return folds


def _config(
    spec: StrategySpec,
    cost_model: CostModel,
    panel: pd.DataFrame,
    market: str,
    leverage: float,
    start: str,
    end: str,
    overrides: dict,
) -> strategy.Config:
    kw = cost_model.engine_kwargs(panel, market)
    kw.update(
        {
            "start": start,
            "end": end,
            "leverage": float(leverage),
            "tax_lots": "hifo",
            "stop": 1.0,
            "rebalance_mode": "calendar",
            "exec_price": "open",
            "trend_ma": 0,
        }
    )
    kw.update(overrides)
    return strategy.Config(**kw)


def _score(metrics: dict, kind: str) -> float:
    if kind == "calmar":
        x = metrics.get("calmar", float("nan"))
    elif kind == "sharpe":
        x = metrics.get("sharpe", float("nan"))
    elif kind == "cagr":
        x = metrics.get("cagr", float("nan"))
    else:
        raise ValueError(f"unknown score {kind}")
    return float(x) if x is not None and np.isfinite(x) else -1e9


def _run(
    panel: pd.DataFrame,
    mem: pd.DataFrame,
    cfg: strategy.Config,
    selector: Selector,
) -> tuple[strategy.Market, backtest.Result]:
    mkt = strategy.Market(panel, mem, cfg)
    return mkt, backtest.run(mkt, selector=selector)


def walk_forward(
    panel: pd.DataFrame,
    mem: pd.DataFrame,
    *,
    spec: StrategySpec,
    param_grid: Iterable[dict],
    selector_factory: Callable[[dict], Selector],
    folds: list[Fold],
    cost_model: CostModel,
    market: str = "TECH",
    leverage: float = 1.0,
    score: str = "calmar",
    warmup_days: int = 400,
    base_overrides: dict | None = None,
    registry: TrialRegistry | None = None,
    verdict: str = "",
    notes: str = "",
    bootstrap_n: int = 10000,
) -> WalkForwardResult:
    """Run a pre-registered parameter grid through expanding walk-forward folds."""
    param_grid = list(param_grid)
    if not param_grid:
        raise ValueError("param_grid is empty")
    overrides = dict(base_overrides or {})
    all_train: list[dict] = []
    trial_rows: list[dict] = []
    fold_records: list[dict] = []
    oos_parts: list[pd.Series] = []
    oos_div_parts: list[pd.Series] = []
    bench_parts: list[pd.Series] = []
    after_chain = 1.0
    after_div_chain = 1.0
    ledger_chain = 1.0
    ledger_div_chain = 1.0
    trial_sharpes: list[float] = []
    yields = daily_dividend_yield(panel)
    total_trials = len(param_grid) * len(folds)

    for fold in folds:
        best = None
        fold_train_rows: list[dict] = []
        for params in param_grid:
            selector = selector_factory(params)
            cfg = _config(
                spec,
                cost_model,
                panel,
                market,
                leverage,
                fold.train_start,
                fold.train_end,
                overrides,
            )
            try:
                mkt, res = _run(panel, mem, cfg, selector)
            except Exception as exc:  # a variant may be infeasible (no data)
                row = {
                    "fold": fold.label,
                    "params": params,
                    "error": str(exc),
                    "score": -1e9,
                    "train_sharpe": float("nan"),
                }
                fold_train_rows.append(row)
                all_train.append(row)
                continue
            m, _ = backtest.summarize(res, mkt)
            val = _score(m, score)
            row = {
                "fold": fold.label,
                "params": params,
                "score": val,
                "train_cagr": float(m.get("cagr", float("nan"))),
                "train_sharpe": float(m.get("sharpe", float("nan"))),
                "train_maxdd": float(m.get("max_dd", float("nan"))),
                "train_trades": int(len(res.trades)),
            }
            fold_train_rows.append(row)
            all_train.append(row)
            trial_sharpes.append(row["train_sharpe"])
            if best is None or val > best["score"]:
                best = row
        if best is None or "error" in best:
            raise RuntimeError(f"all variants failed in fold {fold.label}")
        best_params = best["params"]

        warm_start = (
            (pd.Timestamp(fold.test_start) - pd.Timedelta(days=warmup_days))
            .date()
            .isoformat()
        )
        cfg = _config(
            spec,
            cost_model,
            panel,
            market,
            leverage,
            warm_start,
            fold.test_end,
            overrides,
        )
        mkt, res = _run(panel, mem, cfg, selector_factory(best_params))
        eq = res.equity.dropna()
        test_eq = eq.loc[pd.Timestamp(fold.test_start) : pd.Timestamp(fold.test_end)]
        if len(test_eq) < 5:
            raise RuntimeError(f"test fold {fold.label} has too few observations")
        start_eq = float(test_eq.iloc[0])
        end_eq = float(test_eq.iloc[-1])
        exit_tax = float(res.unrealized_tax)
        after_mult = (end_eq - exit_tax) / start_eq
        after_chain *= after_mult

        led = ledger_replay(res, panel, market)
        led_test = led.equity.loc[test_eq.index]
        led_start = float(led_test.iloc[0])
        ledger_mult = (float(led_test.iloc[-1]) - led.exit_tax) / led_start
        ledger_chain *= ledger_mult

        test_weights = res.weights.loc[test_eq.index]
        eq_div, daily_y = apply_dividend_tax(test_eq, test_weights, yields)
        div_mult = (float(eq_div.iloc[-1]) - exit_tax) / start_eq
        after_div_chain *= div_mult
        led_div, _ = apply_dividend_tax(led_test, test_weights, yields)
        led_div_mult = (float(led_div.iloc[-1]) - led.exit_tax) / led_start
        ledger_div_chain *= led_div_mult
        div_summary = annual_dividend_summary(daily_y)

        fold_returns = test_eq.pct_change().dropna()
        fold_div_returns = eq_div.pct_change().dropna()
        bench = mkt.spy.reindex(test_eq.index).ffill()
        bench_returns = bench.pct_change().dropna().reindex(fold_returns.index).dropna()
        fold_returns = fold_returns.reindex(bench_returns.index).dropna()
        fold_div_returns = fold_div_returns.reindex(bench_returns.index).dropna()
        oos_parts.append(fold_returns)
        oos_div_parts.append(fold_div_returns)
        bench_parts.append(bench_returns)

        fm = summarize_equity(test_eq / start_eq)
        bm = summarize_equity(bench / bench.iloc[0])
        fold_records.append(
            {
                "fold": fold.label,
                "train_start": fold.train_start,
                "train_end": fold.train_end,
                "test_start": fold.test_start,
                "test_end": fold.test_end,
                "params": best_params,
                "train_score": best["score"],
                "train_cagr": best.get("train_cagr"),
                "train_sharpe": best.get("train_sharpe"),
                "train_maxdd": best.get("train_maxdd"),
                "test_cagr": fm["cagr"],
                "test_maxdd": fm["maxdd"],
                "test_sharpe": fm["sharpe"],
                "test_return": fm["total_return"],
                "after_tax_multiple": after_mult,
                "after_tax_multiple_divadj": div_mult,
                "ledger_multiple": ledger_mult,
                "ledger_multiple_divadj": led_div_mult,
                "avg_div_yield": div_summary["avg_yield"],
                "div_tax_drag": div_summary["tax_drag"],
                "bench_return": bm["total_return"],
                "bench_maxdd": bm["maxdd"],
                "trades": int(
                    len(
                        res.trades.loc[
                            (res.trades["date"] >= pd.Timestamp(fold.test_start))
                            & (res.trades["date"] <= pd.Timestamp(fold.test_end))
                        ]
                    )
                )
                if len(res.trades)
                else 0,
            }
        )
        trial_rows.append(
            {
                "family": spec.family,
                "hypothesis": spec.hypothesis,
                "universe": ",".join(spec.universe),
                "start": fold.test_start,
                "end": fold.test_end,
                "params_json": spec.params_json() + "|" + str(best_params),
                "metric": f"train_{score}",
                "value": best["score"],
                "oos_value": after_mult,
                "cost_model": cost_model.name,
                "verdict": verdict,
                "notes": f"fold {fold.label}; selected on train only; {notes}",
                "n_trials": total_trials,
            }
        )

    oos_returns = pd.concat(oos_parts).sort_index()
    oos_div_returns = pd.concat(oos_div_parts).sort_index()
    oos_benchmark = pd.concat(bench_parts).sort_index()
    oos_equity = (1.0 + oos_returns).cumprod()
    oos_div_equity = (1.0 + oos_div_returns).cumprod()
    oos_bench = (1.0 + oos_benchmark).cumprod()
    metrics = summarize_equity(oos_equity, oos_bench)
    boot = bootstrap_p5(oos_returns, n_paths=bootstrap_n)
    dsr = deflated_sharpe(oos_returns, trial_sharpes)
    metrics["after_tax_multiple"] = after_chain
    metrics["after_tax_multiple_divadj"] = after_div_chain
    metrics["ledger_multiple"] = ledger_chain
    metrics["ledger_multiple_divadj"] = ledger_div_chain
    metrics["after_tax_cagr"] = (
        after_chain ** (252.0 / len(oos_returns)) - 1.0
        if len(oos_returns) > 0 and after_chain > 0
        else float("nan")
    )
    metrics["n_trials"] = total_trials

    if registry is not None:
        registry.extend(trial_rows)
        # Also keep every train attempt for auditability. These rows have no OOS
        # value; they are the pre-registration trail, not candidate results.
        for row in all_train:
            if "error" in row:
                continue
            registry.add(
                family=spec.family,
                hypothesis=spec.hypothesis,
                universe=",".join(spec.universe),
                start=folds[0].train_start,
                end=folds[-1].test_end,
                params=row["params"],
                metric=f"train_{score}",
                value=row["score"],
                cost_model=cost_model.name,
                notes=f"fold {row['fold']}; training attempt only",
                n_trials=total_trials,
            )

    return WalkForwardResult(
        family=spec.family,
        spec=spec,
        folds=fold_records,
        oos_returns=oos_returns,
        oos_equity=oos_equity,
        oos_benchmark=oos_bench,
        after_tax_multiple=after_chain,
        after_tax_multiple_divadj=after_div_chain,
        ledger_multiple=ledger_chain,
        ledger_multiple_divadj=ledger_div_chain,
        metrics=metrics,
        bootstrap=boot,
        dsr=dsr,
        trial_rows=trial_rows,
        n_trials=total_trials,
        params_by_fold=[f["params"] for f in fold_records],
    )


def run_full(
    panel: pd.DataFrame,
    mem: pd.DataFrame,
    *,
    spec: StrategySpec,
    params: dict,
    selector_factory: Callable[[dict], Selector],
    cost_model: CostModel,
    market: str = "TECH",
    leverage: float = 1.0,
    start: str = "2015-01-01",
    end: str = "2026-12-31",
    base_overrides: dict | None = None,
) -> dict:
    """One full-window run, used for headline tables and cost sensitivities."""
    overrides = dict(base_overrides or {})
    cfg = _config(spec, cost_model, panel, market, leverage, start, end, overrides)
    mkt, res = _run(panel, mem, cfg, selector_factory(params))
    eq = res.equity.dropna()
    m, _ = backtest.summarize(res, mkt)
    tax = after_tax(res, market)
    led = ledger_replay(res, panel, market)
    eq_div, daily_y = apply_dividend_tax(eq, res.weights, daily_dividend_yield(panel))
    led_div, _ = apply_dividend_tax(
        led.equity, res.weights, daily_dividend_yield(panel)
    )
    div = annual_dividend_summary(daily_y)
    return {
        "result": res,
        "market": mkt,
        "metrics": m,
        "after_tax": tax,
        "equity": eq,
        "equity_divadj": eq_div,
        "summary": summarize_equity(eq, mkt.spy.reindex(eq.index).ffill()),
        "after_tax_multiple": tax["after_tax_multiple_eur"],
        "after_tax_multiple_divadj": float(eq_div.iloc[-1] - res.unrealized_tax),
        "ledger_multiple": led.multiple,
        "ledger_maxdd": led.maxdd,
        "ledger_multiple_divadj": float((led_div.iloc[-1] - led.exit_tax) / 10_000.0),
        "avg_div_yield": div["avg_yield"],
        "div_tax_drag": div["tax_drag"],
        "maxdd": float((eq / eq.cummax() - 1.0).min()),
        "trades": int(len(res.trades)),
    }


def fmt_fold_table(result: WalkForwardResult) -> str:
    lines = [
        "| fold | train | test | best params | test CAGR | test maxDD | after-tax |",
        "|---|---|---|---|---:|---:|---:|",
    ]
    for f in result.folds:
        lines.append(
            f"| {f['fold']} | {f['train_start']}–{f['train_end']} | "
            f"{f['test_start']}–{f['test_end']} | `{f['params']}` | "
            f"{f['test_cagr']:.1%} | {f['test_maxdd']:.1%} | "
            f"{format_multiple(f['after_tax_multiple'])} |"
        )
    return "\n".join(lines)
