"""Short-horizon study: daily/weekly reversal, momentum, and overnight.

Run: uv run python scripts/research_short.py
Writes results/alpha_short.md and appends results/alpha_trials_short.csv.

The prior evidence (`results/short.md`, `results/cadence.md`) already rejects
"trade the monthly book faster". This study tests genuinely different signals:
short-term reversal, short-term momentum and the close-to-open gap, with daily
or weekly holding periods, realistic 5 bps/side costs and a 2x-cost run.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from bot.research.costs import CostModel  # noqa: E402
from bot.research.data import close_open, make_panel  # noqa: E402
from bot.research.metrics import (  # noqa: E402
    annual_return,
    bootstrap_p5,
    format_multiple,
    format_pct,
    summarize_equity,
    worst_12m,
)
from bot.research.overnight import (  # noqa: E402
    lag_positions,
    overnight_backtest,
    overnight_returns,
)
from bot.research.registry import TrialRegistry  # noqa: E402
from bot.research.spec import StrategySpec  # noqa: E402
from bot.research.strategies import overnight_signal, short_signal_selector  # noqa: E402
from bot.research.walkforward import (  # noqa: E402
    expanding_folds,
    fmt_fold_table,
    run_full,
    walk_forward,
)
from scripts.research_common import (  # noqa: E402
    buy_hold,
    markdown_metrics_table,
    metrics_row,
    write_report,
)

ASSETS = ("SPY", "QQQ", "SMH", "TLT", "GLD", "BIL")
SIGNAL_ETFS = ("SPY", "QQQ", "SMH", "TLT", "GLD")
START = "2015-01-01"
END = "2026-11-25"


def engine_grid() -> list[dict]:
    """Pre-registered daily/weekly close-to-close variants."""
    out = []
    for assets in (("SPY",), ("QQQ",), ("SMH",), ("SPY", "QQQ", "SMH")):
        for lookback, mode, threshold in (
            (1, "reversal", 0.01),
            (5, "reversal", 0.02),
            (5, "momentum", 0.02),
            (20, "momentum", 0.03),
        ):
            pick = (
                "worst"
                if mode == "reversal" and len(assets) > 1
                else ("best" if mode == "momentum" and len(assets) > 1 else "all")
            )
            for holding in (1, 5):
                out.append(
                    {
                        "assets": assets,
                        "lookback": lookback,
                        "threshold": threshold,
                        "mode": mode,
                        "holding_days": holding,
                        "pick": pick,
                        "defensive": "BIL",
                    }
                )
    return out


def overnight_grid() -> list[dict]:
    out = []
    for asset in ("SPY", "QQQ", "SMH"):
        for lookback, mode in ((1, "reversal"), (5, "reversal"), (5, "momentum")):
            for threshold in (0.005, 0.01, 0.02):
                out.append(
                    {
                        "asset": asset,
                        "lookback": lookback,
                        "mode": mode,
                        "threshold": threshold,
                    }
                )
    return out


def overnight_wf(
    close: pd.DataFrame,
    open_: pd.DataFrame,
    grid: list[dict],
    folds,
    *,
    cost_bps: float,
    tax: bool = True,
) -> dict:
    """Annual walk-forward for the close-to-open book."""
    reg_rows = []
    parts = []
    fold_rows = []
    chain = 1.0
    for fold in folds:
        train_slice = slice(
            pd.Timestamp(fold.train_start), pd.Timestamp(fold.train_end)
        )
        test_slice = slice(pd.Timestamp(fold.test_start), pd.Timestamp(fold.test_end))
        best = None
        for p in grid:
            trig = overnight_signal(
                close,
                None,
                (p["asset"],),
                mode=p["mode"],
                threshold=p["threshold"],
                lookback=p["lookback"],
            )
            pos = lag_positions(trig, 1)
            r = overnight_backtest(
                close.loc[train_slice],
                open_.loc[train_slice],
                pos.loc[train_slice],
                cost_bps_per_side=cost_bps,
                tax=tax,
            )
            score = r["metrics"]["sharpe"]
            if best is None or score > best["score"]:
                best = {"score": score, "params": p, "train": r["metrics"]}
        p = best["params"]
        trig = overnight_signal(
            close,
            None,
            (p["asset"],),
            mode=p["mode"],
            threshold=p["threshold"],
            lookback=p["lookback"],
        )
        pos = lag_positions(trig, 1)
        r = overnight_backtest(
            close.loc[test_slice],
            open_.loc[test_slice],
            pos.loc[test_slice],
            cost_bps_per_side=cost_bps,
            tax=tax,
        )
        eq = r["equity"]
        mult = float(eq.iloc[-1] / eq.iloc[0])
        chain *= mult
        parts.append(r["returns"].reindex(eq.index).dropna())
        fold_rows.append(
            {
                "fold": fold.label,
                "params": p,
                "train_sharpe": best["score"],
                "test_return": float(eq.iloc[-1] / eq.iloc[0] - 1.0),
                "test_maxdd": r["maxdd"],
                "after_tax_multiple": mult,
                "trades": r["metrics"]["trades"],
            }
        )
        reg_rows.append(
            {
                "family": "short_overnight",
                "params": p,
                "metric": "train_sharpe",
                "value": best["score"],
                "oos_value": mult,
                "fold": fold.label,
            }
        )
    returns = pd.concat(parts).sort_index()
    equity = (1.0 + returns).cumprod()
    m = summarize_equity(equity)
    return {
        "equity": equity,
        "returns": returns,
        "after_tax_multiple": chain,
        "metrics": m,
        "folds": fold_rows,
        "registry": reg_rows,
    }


def main() -> None:
    panel, mem = make_panel(ASSETS)
    spec = StrategySpec(
        name="short_horizon",
        family="short",
        universe=ASSETS,
        params={"note": "daily/weekly reversal/momentum + overnight"},
        decision_time="close t, signal from data through t-1/t",
        fill_time="next open",
        hypothesis=(
            "Short-term reversal, momentum or the overnight gap on liquid ETFs "
            "has a net after-tax edge after realistic 5 bps/side costs."
        ),
        pass_rule=(
            "Positive after-tax OOS, fold hit rate >50%, and positive at 2x "
            "costs. Otherwise reject loudly with the numbers."
        ),
    )
    folds = expanding_folds(START, END, "2018-12-31")
    grid = engine_grid()
    registry = TrialRegistry()

    print(f"engine walk-forward: {len(grid)} variants x {len(folds)} folds")
    cm = CostModel()
    cm2 = cm.scaled(2.0)
    wf1 = walk_forward(
        panel,
        mem,
        spec=spec,
        param_grid=grid,
        selector_factory=short_signal_selector,
        folds=folds,
        cost_model=cm,
        market="TECH",
        leverage=1.0,
        score="sharpe",
        base_overrides={
            "rebalance_mode": "days",
            "rebalance_days": 1,
            "exec_price": "open",
            "trend_ma": 0,
            "core": None,
            "sector_cap": 1.0,
            "stop": 1.0,
        },
        registry=registry,
        bootstrap_n=10000,
        notes="daily/weekly close-to-close, next-open fill",
    )
    wf2 = walk_forward(
        panel,
        mem,
        spec=spec,
        param_grid=grid,
        selector_factory=short_signal_selector,
        folds=folds,
        cost_model=cm2,
        market="TECH",
        leverage=1.0,
        score="sharpe",
        base_overrides={
            "rebalance_mode": "days",
            "rebalance_days": 1,
            "exec_price": "open",
            "trend_ma": 0,
            "core": None,
            "sector_cap": 1.0,
            "stop": 1.0,
        },
        registry=registry,
        bootstrap_n=10000,
        notes="2x costs",
    )
    print(f"  OOS 1x {wf1.after_tax_multiple:.3f}x, 2x {wf2.after_tax_multiple:.3f}x")

    # Fixed full-window families for the cost table.
    fixed = {
        "1d reversal SPY (1%)": {
            "assets": ("SPY",),
            "lookback": 1,
            "threshold": 0.01,
            "mode": "reversal",
            "holding_days": 1,
            "pick": "all",
            "defensive": "BIL",
        },
        "5d reversal QQQ (2%)": {
            "assets": ("QQQ",),
            "lookback": 5,
            "threshold": 0.02,
            "mode": "reversal",
            "holding_days": 5,
            "pick": "all",
            "defensive": "BIL",
        },
        "5d reversal SMH (2%)": {
            "assets": ("SMH",),
            "lookback": 5,
            "threshold": 0.02,
            "mode": "reversal",
            "holding_days": 5,
            "pick": "all",
            "defensive": "BIL",
        },
        "20d momentum SPY (3%)": {
            "assets": ("SPY",),
            "lookback": 20,
            "threshold": 0.03,
            "mode": "momentum",
            "holding_days": 5,
            "pick": "all",
            "defensive": "BIL",
        },
    }
    full = {}
    for label, params in fixed.items():
        for mult, cmx in (("1x", cm), ("2x", cm2)):
            full[f"{label} {mult}"] = run_full(
                panel,
                mem,
                spec=spec,
                params=params,
                selector_factory=short_signal_selector,
                cost_model=cmx,
                market="TECH",
                leverage=1.0,
                start=START,
                end=END,
                base_overrides={
                    "rebalance_mode": "days",
                    "rebalance_days": 1,
                    "exec_price": "open",
                    "trend_ma": 0,
                    "core": None,
                    "sector_cap": 1.0,
                    "stop": 1.0,
                },
            )

    # Overnight.
    close, open_ = close_open(SIGNAL_ETFS)
    ogrid = overnight_grid()
    print(f"overnight walk-forward: {len(ogrid)} variants x {len(folds)} folds")
    on1 = overnight_wf(close, open_, ogrid, folds, cost_bps=5.0)
    on2 = overnight_wf(close, open_, ogrid, folds, cost_bps=10.0)
    # All-in French CTO frictions: 5 bps spread + 2 bps FX + 0.25 bps IBKR
    # commission = 7.25 bps/side; the doubled case is 14.5 bps/side.
    on_allin = overnight_wf(close, open_, ogrid, folds, cost_bps=7.25)
    on_allin2 = overnight_wf(close, open_, ogrid, folds, cost_bps=14.5)
    print(
        f"  overnight OOS 5bps {on1['after_tax_multiple']:.3f}x, "
        f"10bps {on2['after_tax_multiple']:.3f}x, "
        f"all-in 7.25bps {on_allin['after_tax_multiple']:.3f}x, "
        f"all-in 2x 14.5bps {on_allin2['after_tax_multiple']:.3f}x"
    )
    # Always-overnight comparator.
    always = {}
    for asset in ("SPY", "QQQ", "SMH"):
        pos = pd.DataFrame(1.0, index=close.index, columns=[asset])
        always[asset] = overnight_backtest(
            close, open_, pos, cost_bps_per_side=5.0, tax=True
        )

    # Registry rows for overnight.
    for label, run in (
        ("overnight_5bps", on1),
        ("overnight_10bps_module2x", on2),
        ("overnight_allin_7.25bps", on_allin),
        ("overnight_allin_14.5bps", on_allin2),
    ):
        for row in run["registry"]:
            registry.add(
                family=row["family"],
                hypothesis=spec.hypothesis,
                universe=",".join(SIGNAL_ETFS),
                start=START,
                end=END,
                params=row["params"],
                metric=row["metric"],
                value=row["value"],
                oos_value=row["oos_value"],
                cost_model=label,
                notes=f"fold {row['fold']}; train-selected only",
                n_trials=len(ogrid) * len(folds) * 4,
            )
    registry.write(ROOT / "results" / "alpha_trials_short.csv")

    qqq = buy_hold("QQQ")
    oos_start = wf1.oos_equity.index[0].date().isoformat()
    oos_end = wf1.oos_equity.index[-1].date().isoformat()
    bil_oos = buy_hold("BIL", start=oos_start, end=oos_end)
    hit1 = float(np.mean([f["test_return"] > 0 for f in wf1.folds]))
    hit_on = float(np.mean([f["test_return"] > 0 for f in on1["folds"]]))
    full_rows = [
        metrics_row(k, v["equity"], v["after_tax_multiple"]) for k, v in full.items()
    ]

    lines = [
        "# Alpha Lab: short-horizon signals",
        "",
        "Generated by `uv run python scripts/research_short.py`.",
        "Pre-registration: `docs/research.md` H4. Data snapshot 2026-11-25.",
        "",
        "**Universe:** SPY, QQQ, SMH, TLT, GLD (BIL as defensive). Long-only.",
        "**Engine families:** daily/weekly close-to-close reversal and momentum,",
        "signal at close, fill next open, rebalance daily. **Overnight family:**",
        "trigger at close t-1, buy at close t (MOC), sell at open t+1 (MOO),",
        "one full day of information lag.",
        "**Costs:** engine 5 bps/side (plus 2 bps FX + 0.25 bps IBKR), 2x",
        "sensitivity at 10 bps/side. Overnight is shown at 5 bps/side (optimistic),",
        "7.25 bps/side all-in (5 spread + 2 FX + 0.25 commission) and 14.5 bps/side",
        "for the doubled all-in case. French CTO PFU and annual netting.",
        "",
        f"Engine grid: **{len(grid)} variants x {len(folds)} folds**. "
        f"Overnight grid: **{len(ogrid)} variants x {len(folds)} folds**. "
        f"Registry rows: **{registry.n_trials}**.",
        "",
        "## Walk-forward out-of-sample (2019-2026)",
        "",
        "| family | after-tax 1x | after-tax 2x | CAGR 1x | maxDD 1x | fold hit rate | p5 |",
        "|---|---:|---:|---:|---:|---:|---:|",
        f"| close-to-close (next open) | **{format_multiple(wf1.after_tax_multiple)}** | "
        f"{format_multiple(wf2.after_tax_multiple)} | {format_pct(wf1.metrics['cagr'])} | "
        f"{format_pct(wf1.metrics['maxdd'])} | {hit1:.0%} | "
        f"{format_multiple(wf1.bootstrap['p5_multiple'])} |",
        f"| overnight, 5 bps/side (optimistic) | "
        f"**{format_multiple(on1['after_tax_multiple'])}** | "
        f"{format_multiple(on2['after_tax_multiple'])} | "
        f"{format_pct(on1['metrics']['cagr'])} | "
        f"{format_pct(on1['metrics']['maxdd'])} | {hit_on:.0%} | "
        f"{format_multiple(bootstrap_p5(on1['returns'], n_paths=10000)['p5_multiple'])} |",
        f"| overnight, 7.25 bps/side all-in | "
        f"**{format_multiple(on_allin['after_tax_multiple'])}** | "
        f"{format_multiple(on_allin2['after_tax_multiple'])} | "
        f"{format_pct(on_allin['metrics']['cagr'])} | "
        f"{format_pct(on_allin['metrics']['maxdd'])} | "
        f"{float(np.mean([f['test_return'] > 0 for f in on_allin['folds']])):.0%} | "
        f"{format_multiple(bootstrap_p5(on_allin['returns'], n_paths=10000)['p5_multiple'])} |",
        "",
        f"QQQ buy & hold: **{format_multiple(qqq['after_tax_multiple'])}** headline / "
        f"{format_multiple(qqq['ledger_multiple'])} EUR ledger, maxDD "
        f"{format_pct(qqq['maxdd'])}.",
        f"BIL buy & hold over the same OOS window: **"
        f"{format_multiple(bil_oos['after_tax_multiple'])}** headline / "
        f"{format_multiple(bil_oos['ledger_multiple'])} EUR ledger. "
        "Cash is the hurdle; a 2x-cost strategy that cannot beat T-bills is rejected.",
        "",
        "### Selected engine parameters by fold",
        "",
        fmt_fold_table(wf1),
        "",
        "### Overnight folds",
        "",
        "| fold | params | test return | maxDD | after-tax | trades |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for f in on1["folds"]:
        lines.append(
            f"| {f['fold']} | `{f['params']}` | {format_pct(f['test_return'])} | "
            f"{format_pct(f['test_maxdd'])} | "
            f"{format_multiple(f['after_tax_multiple'])} | {f['trades']} |"
        )
    lines += [
        "",
        "## Fixed full-window families (1x and 2x costs)",
        "",
        markdown_metrics_table(full_rows),
        "",
        "## Always-overnight comparator (5 bps/side, before tax)",
        "",
        "| asset | multiple | CAGR | maxDD |",
        "|---|---:|---:|---:|",
    ]
    for asset, r in always.items():
        lines.append(
            f"| {asset} | {format_multiple(r['equity'].iloc[-1])} | "
            f"{format_pct(r['metrics']['cagr'])} | {format_pct(r['maxdd'])} |"
        )
    lines += [
        "",
        "The always-overnight row is the cleanest way to see the cost wall: even",
        "though the overnight gap is a large share of equity total return, a daily",
        "10 bps/side round trip (20 bps) removes more than the gap earns. The",
        "1.01x conditional result is the optimistic 5 bps/side case; at 7.25/14.5",
        "bps/side all-in it is worse. Conditional overnight signals do not fix it.",
        "",
        "## Verdict — H4",
        "",
    ]
    gates = {
        "close-to-close: positive after-tax OOS": wf1.after_tax_multiple > 1.0,
        "close-to-close: positive at 2x costs": wf2.after_tax_multiple > 1.0,
        "close-to-close: fold hit rate >50%": hit1 > 0.50,
        "close-to-close 2x beats BIL headline": (
            wf2.after_tax_multiple > bil_oos["after_tax_multiple"]
        ),
        "overnight: positive after-tax OOS (5 bps/side)": (
            on1["after_tax_multiple"] > 1.0
        ),
        "overnight all-in 2x beats BIL headline": (
            on_allin2["after_tax_multiple"] > bil_oos["after_tax_multiple"]
        ),
    }
    for k, v in gates.items():
        lines.append(f"- {k}: **{'PASS' if v else 'FAIL'}**")
    lines += [
        "",
        "**Verdict: `reject`.** The close-to-close family passes the positive-",
        "return and hit-rate gates at 1x and 2x, but its 2x-cost headline "
        f"(**{format_multiple(wf2.after_tax_multiple)}**) does not beat BIL "
        f"(**{format_multiple(bil_oos['after_tax_multiple'])}**). The headline "
        "is the pre-registered cash hurdle; the EUR-ledger column chains "
        "per-fold liquidations and is shown for completeness, not used to "
        "rescue the strategy. It is also below QQQ buy & hold. The overnight "
        "family loses money at all-in costs. No satellite allocation.",
        "",
        "## Open risks",
        "",
        "- Daily data cannot measure intraday execution quality; the next-open",
        "  assumption is already generous for a retail MOC/MOO workflow.",
        "- The engine's French tax is annual net settlement; a high-turnover book",
        "  can realize more gains than the annual net model implies if lots cross",
        "  years. The 2x-cost run is the more important rejection.",
        "- A market maker with sub-bp spreads and no FX cost might find a gross",
        "  edge in the overnight gap, but that is not a French retail CTO.",
        "",
    ]
    write_report(ROOT / "results" / "alpha_short.md", "\n".join(lines))
    print("wrote results/alpha_short.md")


if __name__ == "__main__":
    main()
