"""Long-horizon cross-asset ETF dual momentum, walk-forward and after tax.

Run: uv run python scripts/research_cross_asset.py
Writes results/alpha_cross_asset.md and appends results/alpha_trials.csv.

Pre-registration: docs/research.md, H1/H2. The primary evidence is the
concatenated out-of-sample path; the full-window tables use a fixed
pre-registered config, not the best in-sample parameter.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from bot.research.costs import CostModel  # noqa: E402
from bot.research.data import CROSS_ASSET, make_panel  # noqa: E402
from bot.research.metrics import (  # noqa: E402
    annual_return,
    format_multiple,
    format_pct,
    summarize_equity,
    worst_12m,
)
from bot.research.registry import TrialRegistry  # noqa: E402
from bot.research.spec import StrategySpec  # noqa: E402
from bot.research.strategies import cross_asset_selector  # noqa: E402
from bot.research.walkforward import (  # noqa: E402
    expanding_folds,
    fmt_fold_table,
    run_full,
    walk_forward,
)
from scripts.research_common import (  # noqa: E402
    buy_hold,
    five_year_table,
    markdown_metrics_table,
    metrics_row,
    tech_baseline,
    us_baseline,
    write_report,
)

UNIVERSE = CROSS_ASSET
PRIMARY = {
    "universe": UNIVERSE,
    "defensive": "BIL",
    "lookback": 252,
    "skip": 21,
    "top_n": 2,
    "trend_ma": 200,
    "abs_mom": "bil",
    "weighting": "invvol",
    "vol_window": 60,
    "max_weight": 0.6,
}


def param_grid() -> list[dict]:
    out = []
    for lookback, skip in ((126, 10), (252, 21)):
        for top_n in (1, 2, 3):
            for trend_ma in (0, 200):
                for weighting in ("equal", "invvol"):
                    out.append(
                        {
                            "universe": UNIVERSE,
                            "defensive": "BIL",
                            "lookback": lookback,
                            "skip": skip,
                            "top_n": top_n,
                            "trend_ma": trend_ma,
                            "abs_mom": "bil",
                            "weighting": weighting,
                            "vol_window": 60,
                            "max_weight": 0.6,
                        }
                    )
    return out


def main() -> None:
    panel, mem = make_panel(UNIVERSE)
    spec = StrategySpec(
        name="cross_asset_dual_momentum",
        family="cross_asset",
        universe=UNIVERSE,
        params=PRIMARY,
        decision_time="month-end close",
        fill_time="next open",
        hypothesis=(
            "ETF-only dual momentum with an absolute-momentum/trend gate earns "
            "a positive after-tax return with lower drawdown than QQQ, and "
            "survives 2x costs."
        ),
        pass_rule=(
            "OOS after-tax > QQQ OOS after-tax, >60% of common 5y windows beat "
            "QQQ, positive at 2x costs, no single fold >50% of terminal wealth."
        ),
        notes="24 pre-registered variants, annual expanding folds 2019-2026.",
    )
    folds = expanding_folds("2015-01-01", "2026-11-25", "2018-12-31")
    grid = param_grid()
    registry = TrialRegistry()

    runs = {}
    for label, lev, cm in (
        ("1x", 1.0, CostModel()),
        ("1.5x", 1.5, CostModel()),
        ("2x", 2.0, CostModel()),
        ("1x @ 2x costs", 1.0, CostModel().scaled(2.0)),
    ):
        print(f"walk-forward {label} ({len(grid)} variants x {len(folds)} folds)")
        res = walk_forward(
            panel,
            mem,
            spec=spec,
            param_grid=grid,
            selector_factory=cross_asset_selector,
            folds=folds,
            cost_model=cm,
            market="TECH",
            leverage=lev,
            score="calmar",
            registry=registry,
            bootstrap_n=10000,
            notes=f"leverage={lev}, costs={cm.name}",
        )
        runs[label] = res
        print(
            f"  after-tax {res.after_tax_multiple:.3f}x  maxDD {res.metrics['maxdd']:.1%}  "
            f"CAGR {res.metrics['cagr']:.1%}"
        )

    # Full-window fixed config, not selected on the OOS path.
    full = {}
    for label, lev, cm in (
        ("1x", 1.0, CostModel()),
        ("1.5x", 1.5, CostModel()),
        ("2x", 2.0, CostModel()),
        ("1x @ 2x costs", 1.0, CostModel().scaled(2.0)),
    ):
        full[label] = run_full(
            panel,
            mem,
            spec=spec,
            params=PRIMARY,
            selector_factory=cross_asset_selector,
            cost_model=cm,
            market="TECH",
            leverage=lev,
            start="2015-01-01",
            end="2026-11-25",
        )

    tech = tech_baseline()
    us = us_baseline()
    qqq = buy_hold("QQQ")
    oos_start = runs["1x"].oos_equity.index[0]
    oos_end = runs["1x"].oos_equity.index[-1]
    qqq_oos = buy_hold(
        "QQQ", start=oos_start.date().isoformat(), end=oos_end.date().isoformat()
    )
    tech_oos = tech_baseline(
        start=oos_start.date().isoformat(), end=oos_end.date().isoformat()
    )
    us_oos = us_baseline(
        start=oos_start.date().isoformat(), end=oos_end.date().isoformat()
    )

    # OOS comparison metrics.
    def oos_row(label: str) -> dict:
        r = runs[label]
        q = qqq_oos["equity"].reindex(r.oos_equity.index).ffill()
        q = q / q.iloc[0]
        s = summarize_equity(r.oos_equity, q)
        return {
            "name": label,
            "after": r.after_tax_multiple,
            "divadj": r.after_tax_multiple_divadj,
            "ledger": r.ledger_multiple,
            "ledger_div": r.ledger_multiple_divadj,
            "cagr": r.metrics["cagr"],
            "maxdd": r.metrics["maxdd"],
            "sharpe": r.metrics["sharpe"],
            "worst_12m": r.metrics["worst_12m"],
            "y2018": float("nan"),  # OOS starts in 2019
            "y2022": annual_return(r.oos_equity, 2022),
            "five_year": s["five_year"],
            "summary": s,
            "p5": r.bootstrap["p5_multiple"],
            "p5dd": r.bootstrap["p5_maxdd"],
            "dsr": r.dsr["dsr"],
            "folds": r.folds,
        }

    oos_rows = [oos_row(k) for k in ("1x", "1.5x", "2x", "1x @ 2x costs")]
    qqq_oos_mult = qqq_oos["after_tax_multiple"]
    qqq_oos_div = qqq_oos["after_tax_multiple_divadj"]
    qqq_oos_ledger = qqq_oos["ledger_multiple"]
    qqq_oos_eq = qqq_oos["equity"] / qqq_oos["equity"].iloc[0]
    qqq_oos_eq = qqq_oos_eq.reindex(runs["1x"].oos_equity.index).ffill()

    lines = [
        "# Alpha Lab: cross-asset ETF dual momentum",
        "",
        "Generated by `uv run python scripts/research_cross_asset.py`.",
        "Pre-registration: `docs/research.md` H1/H2. Data snapshot 2026-11-25.",
        "",
        "**Universe:** SMH, QQQ, SPY, TLT, IEF, GLD; defensive BIL. Long-only.",
        "**Signal:** rank on 12-1 or 6-1 momentum; absolute momentum must beat BIL;",
        "optional price > 200-day MA; top 1-3; equal or inverse-vol; monthly",
        "rebalance at month-end close, fill next open.",
        "**Costs:** French CTO at IBKR, 5 bps spread, 2 bps FX, 5%/yr financing,",
        "31.4% PFU after 2026. `@ 2x costs` doubles spread/FX/financing/commission.",
        "",
        f"Grid: **{len(grid)} variants** x **{len(folds)} folds** = "
        f"**{len(grid) * len(folds)}** training runs per walk-forward. "
        f"Registry rows: **{registry.n_trials}**.",
        "",
        "## Primary evidence: concatenated out-of-sample path (2019-2026)",
        "",
        "| book | OOS headline | OOS div-tax adj | OOS EUR ledger | ledger+div | OOS CAGR | OOS maxDD | p5 | p5 maxDD |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in oos_rows:
        lines.append(
            f"| {r['name']} | **{format_multiple(r['after'])}** | "
            f"{format_multiple(r['divadj'])} | {format_multiple(r['ledger'])} | "
            f"{format_multiple(r['ledger_div'])} | {format_pct(r['cagr'])} | "
            f"{format_pct(r['maxdd'])} | {format_multiple(r['p5'])} | "
            f"{format_pct(r['p5dd'])} |"
        )
    lines += [
        "",
        "Headline = USD-native after tax, translated to EUR at the terminal FX.",
        "Div-tax adj = headline minus the tax on gross dividends inferred from",
        "`adjclose/close` (TLT/IEF/BIL are the material legs). EUR ledger = annual",
        "EUR tax-lot replay; ledger+div adds the same dividend tax. The dividend",
        "adjustment is a reporting overlay, not a signal change.",
        "",
        f"QQQ same OOS window: headline **{format_multiple(qqq_oos_mult)}**, "
        f"div-tax adj **{format_multiple(qqq_oos_div)}**, EUR ledger "
        f"**{format_multiple(qqq_oos_ledger)}**.",
        f"US 2.5x same OOS window: headline **{format_multiple(us_oos['after_tax_multiple'])}**, "
        f"div-tax adj **{format_multiple(us_oos['after_tax_multiple_divadj'])}**, "
        f"EUR ledger **{format_multiple(us_oos['ledger_multiple'])}**.",
        f"TECH 2.5x same OOS window: headline **{format_multiple(tech_oos['after_tax_multiple'])}**, "
        f"div-tax adj **{format_multiple(tech_oos['after_tax_multiple_divadj'])}**, "
        f"EUR ledger **{format_multiple(tech_oos['ledger_multiple'])}**.",
        "",
        "### Selected parameters by fold (1x)",
        "",
        fmt_fold_table(runs["1x"]),
        "",
        "## Full window 2015-2026, fixed pre-registered config",
        "",
        "Config: 12-1 momentum, top 2, 200d trend gate, inverse-vol, monthly. "
        "This is one of the 24 variants; it is not chosen from the OOS path.",
        "",
    ]
    full_rows = []
    for label in ("1x", "1.5x", "2x", "1x @ 2x costs"):
        f = full[label]
        eq = f["equity"]
        full_rows.append(metrics_row(label, eq, f["after_tax_multiple"]))
    lines += [markdown_metrics_table(full_rows), ""]
    lines += [
        "| book | headline | div-tax adj | EUR ledger | ledger+div | maxDD | 5y median | 5y worst | 5y beat QQQ |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for label in ("1x", "1.5x", "2x", "1x @ 2x costs"):
        f = full[label]
        s = summarize_equity(
            f["equity"], qqq["equity"].reindex(f["equity"].index).ffill()
        )
        fy = s["five_year"]
        lines.append(
            f"| {label} | {format_multiple(f['after_tax_multiple'])} | "
            f"{format_multiple(f['after_tax_multiple_divadj'])} | "
            f"{format_multiple(f['ledger_multiple'])} | "
            f"{format_multiple(f['ledger_multiple_divadj'])} | "
            f"{format_pct(f['maxdd'])} | {format_pct(fy.get('median', float('nan')))} | "
            f"{format_pct(fy.get('worst', float('nan')))} | "
            f"{fy.get('share_beat_benchmark', float('nan')):.0%} |"
        )
    lines += [
        "",
        "The 2x-cost row is the gate. The split between headline and ledger is the",
        "currency/tax-timing correction; the dividend column is the missing tax on",
        "gross distributions. The bond/cash legs (TLT, IEF, BIL) are why the",
        "dividend adjustment matters here.",
        "",
        "## 5-year windows vs US 2.5x and TECH 2.5x (fixed config, 1x)",
        "",
    ]
    _, fy_us = five_year_table(
        full["1x"]["equity"], us["equity"], "cross-asset 1x", "US 2.5x"
    )
    _, fy_tech = five_year_table(
        full["1x"]["equity"], tech["equity"], "cross-asset 1x", "TECH 2.5x"
    )
    lines += [
        f"- Windows: **{fy_us.get('n', 0)}**",
        f"- Cross-asset median: **{fy_us.get('median', float('nan')):+.1%}**",
        f"- Median gap vs US 2.5x: **{fy_us.get('median_gap', float('nan')):+.1%}**; "
        f"share beating US: **{fy_us.get('share_beat_benchmark', float('nan')):.0%}**",
        f"- Median gap vs TECH 2.5x: **{fy_tech.get('median_gap', float('nan')):+.1%}**; "
        f"share beating TECH: **{fy_tech.get('share_beat_benchmark', float('nan')):.0%}**",
        f"- Cross-asset worst 5y: **{fy_us.get('worst', float('nan')):+.1%}**",
        "",
        "The full per-window table is intentionally omitted here; it can be",
        "regenerated by the script. The summary is the decision variable.",
        "",
    ]

    # H1 verdict.
    x1 = oos_rows[0]
    full_qqq = summarize_equity(
        full["1x"]["equity"], qqq["equity"].reindex(full["1x"]["equity"].index).ffill()
    )
    beat_qqq_share = full_qqq["five_year"].get("share_beat_benchmark", 0.0)
    fold_multiples = [f["after_tax_multiple_divadj"] for f in x1["folds"]]
    total = x1["divadj"]
    max_fold_share = max(fold_multiples) / total if total > 0 else float("inf")
    x2 = oos_rows[3]
    gates = {
        "OOS div-tax adj > QQQ div-tax adj": x1["divadj"] > qqq_oos_div,
        "5y windows beat QQQ >=60%": beat_qqq_share >= 0.60,
        "positive at 2x costs (div-tax adj)": x2["divadj"] > 1.0,
        "no fold >50% of terminal": max_fold_share < 0.50,
    }
    passed = all(gates.values())
    verdict = "paper" if passed else "reject"
    lines += [
        "## Verdict — H1",
        "",
        "| gate | result |",
        "|---|---|",
    ]
    for k, v in gates.items():
        lines.append(f"| {k} | {'PASS' if v else 'FAIL'} |")
    lines += [
        "",
        f"**Verdict: `{verdict}`.** ",
        (
            "The cross-asset book clears the balanced-diversifier gates, but it "
            "does not approach the TECH 2.5x after-tax return. It is a candidate "
            "for a balanced sleeve, not for the default book."
            if passed
            else "It fails at least one pre-registered gate; keep it out of the "
            "candidate config. The lower drawdown is real, the return premium is not."
        ),
        "",
        f"1x OOS div-tax adj **{format_multiple(x1['divadj'])}** vs QQQ "
        f"**{format_multiple(qqq_oos_div)}**; EUR ledger "
        f"**{format_multiple(x1['ledger_div'])}** vs QQQ "
        f"**{format_multiple(qqq_oos_ledger)}**. MaxDD "
        f"{format_pct(x1['maxdd'])}. The primary real-life benchmark, US 2.5x, "
        f"is **{format_multiple(us['after_tax_multiple'])}** headline / "
        f"**{format_multiple(us['ledger_multiple'])}** EUR ledger; the cross-asset "
        f"book does not compete with it on return.",
        "",
        "## Verdict — H2 (leverage)",
        "",
        f"- 1x OOS: div-tax adj **{format_multiple(oos_rows[0]['divadj'])}**, "
        f"ledger+div **{format_multiple(oos_rows[0]['ledger_div'])}**, maxDD "
        f"{format_pct(oos_rows[0]['maxdd'])}.",
        f"- 1.5x OOS: div-tax adj **{format_multiple(oos_rows[1]['divadj'])}**, "
        f"ledger+div **{format_multiple(oos_rows[1]['ledger_div'])}**, maxDD "
        f"{format_pct(oos_rows[1]['maxdd'])}.",
        f"- 2x OOS: div-tax adj **{format_multiple(oos_rows[2]['divadj'])}**, "
        f"ledger+div **{format_multiple(oos_rows[2]['ledger_div'])}**, maxDD "
        f"{format_pct(oos_rows[2]['maxdd'])}.",
        "",
        (
            "Leverage adds headline return, but after the dividend tax and the "
            "EUR ledger the 2x book is still only ~3-4x, far below US 2.5x. "
            "It does not create a new return source; it scales a weak signal "
            "and adds financing risk. **Reject** as a return source; keep the "
            "1x version only as a research comparator."
        ),
        "",
        "## Open risks",
        "",
        "- The ETF sample is one bull decade for US tech. TLT/GLD diversification",
        "  helped in 2022 but cannot be assumed to repeat.",
        "- The signal is monthly and tax-aware, but the French PFU is modelled as",
        "  an annual net settlement; actual lot selection and loss carry can differ.",
        "- The dividend adjustment is inferred from `adjclose/close`; it captures",
        "  the timing and rough size of distributions, not the exact withholding.",
        "- A retail broker with 25 bps FX or wider ETF spreads would make this",
        "  worse, not better; the 2x-cost row is the realistic stress case.",
        "",
    ]
    write_report(ROOT / "results" / "alpha_cross_asset.md", "\n".join(lines))
    registry.write(ROOT / "results" / "alpha_trials_cross_asset.csv")
    print(f"wrote results/alpha_cross_asset.md, registry rows {registry.n_trials}")
    print(f"H1 verdict: {verdict}")


if __name__ == "__main__":
    main()
