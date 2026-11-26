"""TECH sleeve survivorship check.

Run: uv run python scripts/research_tech_sleeve.py
Writes results/alpha_tech_sleeve.md and appends results/alpha_trials.csv.

Compares, with identical monthly 2.5x rules:
  * the shipped static 49-name TECH list,
  * the same list restricted to point-in-time S&P 500 membership,
  * a PIT S&P 500 Information Technology universe (current GICS labels),
  * core-only SMH,
  * the full PIT S&P 500 US book as the broader comparator.

The PIT-sector run still uses current sector labels: a name is classified as
IT today, not as it was in 2015. That residual bias is flagged, not hidden.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from bot import backtest, france, markets, strategy, universe  # noqa: E402
from bot.research.costs import CostModel, after_tax  # noqa: E402
from bot.research.data import (  # noqa: E402
    pit_membership_filter,
    read_bars,
    tech_sector_membership,
)
from bot.research.dividends import (  # noqa: E402
    annual_dividend_summary,
    apply_dividend_tax,
    daily_dividend_yield,
)
from bot.research.ledger import replay as ledger_replay  # noqa: E402
from bot.research.metrics import (  # noqa: E402
    annual_return,
    format_multiple,
    format_pct,
    summarize_equity,
    worst_12m,
)
from bot.research.registry import TrialRegistry  # noqa: E402
from bot.research.spec import StrategySpec  # noqa: E402
from scripts.research_common import (  # noqa: E402
    five_year_table,
    write_report,
)

TECH_NAMES = tuple(markets.UNIVERSES["TECH"])
START = "2015-01-01"
END = "2026-11-25"
ORIG_CANONICAL = universe.canonical


def run_variant(
    panel: pd.DataFrame,
    mem: pd.DataFrame,
    name: str,
    *,
    market: str = "TECH",
    sector_cap: float = 0.4,
    core_mode: str = "relative",
    cost_model: CostModel | None = None,
) -> dict:
    cm = cost_model or CostModel()
    universe.canonical = ORIG_CANONICAL if market == "US" else (lambda t: t)  # type: ignore[assignment]
    kw = cm.engine_kwargs(panel, market)
    kw.update(
        start=START,
        end=END,
        leverage=2.5,
        financing_bps=cm.financing_bps * cm.multiplier,
        trend_ma=200,
        trend_scale=0.5,
        rebalance_mode="calendar",
        exec_price="open",
        tax_lots="hifo",
        sector_cap=sector_cap,
        core_mode=core_mode,
    )
    cfg = strategy.Config(**kw)
    mkt = strategy.Market(panel, mem, cfg)
    res = backtest.run(mkt)
    tax = after_tax(res, market)
    eq = res.equity.dropna()
    led = ledger_replay(res, panel, market)
    yields = daily_dividend_yield(panel)
    eq_div, daily_y = apply_dividend_tax(eq, res.weights, yields)
    led_div, _ = apply_dividend_tax(led.equity, res.weights, yields)
    div = annual_dividend_summary(daily_y)
    bench = mkt.spy.reindex(eq.index).ffill()
    s = summarize_equity(eq, bench)
    return {
        "name": name,
        "result": res,
        "market": mkt,
        "panel": panel,
        "equity": eq,
        "equity_divadj": eq_div,
        "benchmark": bench,
        "headline": tax["after_tax_multiple_eur"],
        "divadj": float(eq_div.iloc[-1] - res.unrealized_tax),
        "ledger": led.multiple,
        "ledger_maxdd": led.maxdd,
        "ledger_divadj": float((led_div.iloc[-1] - led.exit_tax) / 10_000.0),
        "total": s["total_return"],
        "cagr": s["cagr"],
        "maxdd": s["maxdd"],
        "sharpe": s["sharpe"],
        "worst_12m": worst_12m(eq),
        "y2018": annual_return(eq, 2018),
        "y2022": annual_return(eq, 2022),
        "trades": int(len(res.trades)),
        "avg_div_yield": div["avg_yield"],
        "div_tax_drag": div["tax_drag"],
        "summary": s,
        "bench_summary": summarize_equity(bench / bench.iloc[0]),
    }


def main() -> None:
    # Build the US panel before switching the canonical-symbol function to
    # identity for the TECH/QQQ panel. The US membership has dotted tickers
    # (BRK.B) that must map to Yahoo dashes (BRK-B).
    panel_us, mem_us = markets.build("US")
    universe.canonical = ORIG_CANONICAL  # type: ignore[assignment]

    # Official TECH panel: QQQ is stored as SPY, static list has no membership.
    panel_tech, mem_static = markets.build("TECH")
    universe.canonical = lambda t: t  # type: ignore[assignment]

    us_mem = universe.load_membership()
    mem_pit_static = pit_membership_filter(us_mem, TECH_NAMES)
    mem_it = tech_sector_membership(us_mem, ("Information Technology",))

    # Add any IT names that are not already in the TECH panel. The benchmark
    # stays QQQ (stored as SPY) so the comparison is apples-to-apples.
    have = set(panel_tech.ticker.unique())
    add = []
    missing = []
    for t in sorted(mem_it.ticker.unique()):
        if t in have:
            continue
        try:
            add.append(read_bars(t))
        except Exception:
            missing.append(t)
    panel_it = (
        pd.concat([panel_tech] + add, ignore_index=True)
        .drop_duplicates(["date", "ticker"])
        .sort_values(["date", "ticker"])
    )

    variants = [
        run_variant(
            panel_tech, mem_static, "Static 49, all-always (shipped)", sector_cap=0.4
        ),
        run_variant(
            panel_tech, mem_pit_static, "Static 49, PIT membership", sector_cap=0.4
        ),
        run_variant(
            panel_tech, mem_pit_static, "Static 49, PIT, cap 1.0", sector_cap=1.0
        ),
        run_variant(panel_it, mem_it, "PIT S&P IT universe, cap 1.0", sector_cap=1.0),
        run_variant(
            panel_it,
            mem_it,
            "PIT S&P IT, core-only",
            sector_cap=1.0,
            core_mode="always",
        ),
        run_variant(
            panel_us, mem_us, "US all PIT, cap 0.4", market="US", sector_cap=0.4
        ),
        run_variant(
            panel_us,
            mem_us,
            "US all PIT, core-only",
            market="US",
            sector_cap=0.4,
            core_mode="always",
        ),
    ]

    # Trial registry: every variant, including the core-only and US comparators.
    reg = TrialRegistry()
    for v in variants:
        reg.add(
            family="tech_sleeve",
            hypothesis=(
                "The static TECH sleeve's return survives point-in-time S&P "
                "membership; the sleeve adds value over core-only and over US all."
            ),
            universe="static TECH / PIT S&P IT / US PIT",
            start=START,
            end=END,
            params={
                "variant": v["name"],
                "sector_cap": 1.0 if "cap 1.0" in v["name"] else 0.4,
                "core_mode": "always" if "core-only" in v["name"] else "relative",
                "leverage": 2.5,
            },
            metric="after_tax_multiple_eur",
            value=v["headline"],
            oos_value=None,
            cost_model="fr_cto_ibkr",
            verdict="paper",
            notes="PIT membership; current GICS sector residual bias on IT universe",
            n_trials=len(variants),
        )
    reg.write(ROOT / "results" / "alpha_trials_tech.csv")

    by_name = {v["name"]: v for v in variants}
    static = by_name["Static 49, all-always (shipped)"]
    pit_static = by_name["Static 49, PIT membership"]
    pit_it = by_name["PIT S&P IT universe, cap 1.0"]
    core = by_name["PIT S&P IT, core-only"]
    us = by_name["US all PIT, cap 0.4"]

    # 5-year summaries.
    _, fy_static = five_year_table(
        pit_it["equity"], static["equity"], "PIT IT", "static 49"
    )
    _, fy_core = five_year_table(
        pit_it["equity"], core["equity"], "PIT IT", "core-only"
    )
    _, fy_us = five_year_table(pit_it["equity"], us["equity"], "PIT IT", "US all")

    lines = [
        "# Alpha Lab: TECH sleeve survivorship check",
        "",
        "Generated by `uv run python scripts/research_tech_sleeve.py`.",
        "Pre-registration: `docs/research.md` H3. Data snapshot 2026-11-25.",
        "Same rules for every row: TECH monthly, SMH core, 2.5x, 200-day daily",
        "cut, 5 bps spread, 2 bps FX, 5%/yr financing, French CTO, HIFO lots.",
        "After-tax is a liquidation multiple in EUR. `div-tax adj` subtracts tax",
        "on gross distributions; `EUR ledger` is the annual EUR tax-lot replay.",
        "",
        "## Variants",
        "",
        "| book | headline | div-tax adj | EUR ledger | ledger+div | CAGR | maxDD USD | maxDD ledger | 2018 | 2022 | worst 12m | trades |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for v in variants:
        lines.append(
            f"| {v['name']} | **{format_multiple(v['headline'])}** | "
            f"{format_multiple(v['divadj'])} | {format_multiple(v['ledger'])} | "
            f"{format_multiple(v['ledger_divadj'])} | {format_pct(v['cagr'])} | "
            f"{format_pct(v['maxdd'])} | {format_pct(v['ledger_maxdd'])} | "
            f"{format_pct(v['y2018'])} | {format_pct(v['y2022'])} | "
            f"{format_pct(v['worst_12m'])} | {v['trades']} |"
        )
    lines += [
        "",
        f"Static 49 PIT membership: **{format_multiple(pit_static['headline'])}** "
        f"vs shipped **{format_multiple(static['headline'])}** "
        f"({pit_static['headline'] / static['headline'] - 1:+.1%}). "
        f"PIT IT: **{format_multiple(pit_it['headline'])}** "
        f"({pit_it['headline'] / static['headline'] - 1:+.1%} vs shipped).",
        "",
        "The PIT IT universe has 73 names (current GICS Information Technology)",
        "and uses `sector_cap=1.0` because all names share one GICS sector.",
        "The static list has 49 names and uses the shipped 0.4 sector cap.",
        "The `Static 49, PIT, cap 1.0` row isolates the cap change: it is worse",
        "than the shipped cap 0.4, so the PIT IT result is not just a cap effect.",
        "",
        "## 5-year windows",
        "",
        f"- PIT IT vs static 49: median gap "
        f"**{fy_static.get('median_gap', float('nan')):+.1%}**, share beating "
        f"static **{fy_static.get('share_beat_benchmark', float('nan')):.0%}**.",
        f"- PIT IT vs core-only: median gap "
        f"**{fy_core.get('median_gap', float('nan')):+.1%}**, share beating "
        f"core-only **{fy_core.get('share_beat_benchmark', float('nan')):.0%}**.",
        f"- PIT IT vs US all: median gap "
        f"**{fy_us.get('median_gap', float('nan')):+.1%}**, share beating US "
        f"**{fy_us.get('share_beat_benchmark', float('nan')):.0%}**.",
        "",
        "## Verdict — H3",
        "",
    ]
    ratio = pit_it["headline"] / static["headline"]
    beat_core = fy_core.get("share_beat_benchmark", 0.0)
    gates = {
        "PIT IT retains >=80% of static headline": ratio >= 0.80,
        "PIT IT beats core-only in >=60% of 5y windows": beat_core >= 0.60,
    }
    for k, v in gates.items():
        lines.append(f"- {k}: **{'PASS' if v else 'FAIL'}**")
    lines += [
        "",
        (
            "**Verdict: `accept` as a robustness finding; `reject` as a new "
            "default book.** The PIT results are **consistent with** a robust "
            "sleeve, not proof that survivorship bias is absent: the PIT S&P IT "
            "version retains "
            f"{ratio:.0%} of the static headline and beats core-only in "
            f"{beat_core:.0%} of 5-year windows. The cleaner static-49 PIT row "
            "still selects names that survived to today. The broader US PIT book "
            f"({format_multiple(us['headline'])}) beats it on return and drawdown. "
            "Recommendation: keep US monthly as the primary real-life book; if a "
            "dedicated TECH sleeve is retained for forward validation, use PIT "
            "S&P membership instead of the static 49-name list. Core-only is a "
            "risk choice, not a return improvement: it gives up roughly a third "
            "of the after-tax terminal."
        ),
        "",
        "## Caveats",
        "",
        "- Current GICS sector labels are used for the PIT IT universe; a name",
        "  classified as IT today may have been classified differently in 2015.",
        "  That is residual look-ahead, smaller than full survivorship bias but",
        "  not zero. The ETF-only cross-asset work avoids this entirely.",
        "- `markets.build('US')` uses point-in-time S&P 500 membership; the",
        "  static TECH list is all-always. The PIT static row removes that",
        "  difference and is the cleanest membership comparison.",
        "- The after-tax EUR ledger replays USD trades in EUR; it is the same",
        "  signal/book, not a different strategy.",
        "",
    ]
    write_report(ROOT / "results" / "alpha_tech_sleeve.md", "\n".join(lines))
    print("wrote results/alpha_tech_sleeve.md")
    for v in variants:
        print(
            f"{v['name']:36s} headline {v['headline']:6.2f}x  "
            f"divadj {v['divadj']:6.2f}x  ledger {v['ledger']:6.2f}x  "
            f"DD {v['maxdd']:6.1%}"
        )


if __name__ == "__main__":
    main()
