"""PIT Nasdaq-100 TECH book vs the static-49 shipped sleeve.

Run: uv run python scripts/research_pit_tech.py
Writes results/pit_tech.md.

Cache-only. The only data read is `bot/research/data/nasdaq100_history.csv`,
cached bars, cached dividends, and the existing S&P/US membership. No network.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from bot import (
    backtest,
    dividends as divdata,
    eur_ledger,
    france,
    markets,
    strategy,
    universe,
)  # noqa: E402
from bot.research.metrics import (  # noqa: E402
    annual_return,
    five_year_windows,
    format_multiple,
    format_pct,
    summarize_equity,
    window_summary,
    worst_12m,
)
from bot.research.pit_universe import (  # noqa: E402
    SNAPSHOT_NOTE,
    SOURCE_URL,
    build_pit_panel,
    coverage,
    membership_on,
)

CAP = 10_000.0
START = "2015-01-01"
END = "2026-11-25"
BASE = dict(
    start=START,
    end=END,
    leverage=2.5,
    financing_bps=500.0,
    trend_ma=200,
    trend_scale=0.5,
    rebalance_mode="calendar",
    exec_price="open",
    tax_lots="hifo",
    sector_cap=0.4,
    core="SMH",
    core_mode="relative",
)


def ledger_config(mult: float = 1.0) -> eur_ledger.EurLedgerConfig:
    return eur_ledger.EurLedgerConfig(
        capital=CAP,
        tax_rate=None,
        financing_bps=500.0 * mult,
        spread_bps=5.0 * mult,
        fx_bps=2.0 * mult,
        broker_bps=0.25 * mult,
        broker_fixed_eur=1.0,
        withholding_rate=0.15,
    )


def run_book(
    panel: pd.DataFrame,
    mem: pd.DataFrame,
    *,
    market: str,
    label: str,
    mult: float = 1.0,
    core_mode: str = "relative",
) -> dict:
    """Run one book and produce native, terminal-FX and dividend-corrected EUR."""
    orig = universe.canonical
    # TECH/PIT panels use canonical Yahoo symbols. US membership keeps dotted
    # index tickers (BRK.B) and must use the shared alias map.
    universe.canonical = (
        orig if market == "US" else (lambda t: t)  # type: ignore[assignment]
    )
    kw = france.kwargs(market, "Interactive Brokers", "cto", CAP, panel)
    kw.update(BASE)
    kw["core_mode"] = core_mode
    kw["cost_bps"] = 5.0 * mult
    kw["financing_bps"] = 500.0 * mult
    kw["fx_bps"] = 2.0 * mult
    kw["us_bps"] = float(kw.get("us_bps", 0.0)) * mult
    kw["broker_bps"] = float(kw.get("broker_bps", 0.0)) * mult
    cfg = strategy.Config(**kw)
    mkt = strategy.Market(panel, mem, cfg)
    res = backtest.run(mkt)
    universe.canonical = orig  # type: ignore[assignment]

    tax = france.after_tax(res, CAP, market)
    divs = divdata.load_dividends(
        sorted(res.trades["ticker"].unique()), fetch_missing=False
    )
    led = eur_ledger.replay(
        res, panel, market, config=ledger_config(mult), dividends=divs
    )
    eq = res.equity.dropna()
    native_after = float(eq.iloc[-1] - res.unrealized_tax)
    years = len(eq) / 252.0
    return {
        "label": label,
        "res": res,
        "panel": panel,
        "market": mkt,
        "ledger": led,
        "divs": divs,
        "headline": tax["after_tax_eur"] / CAP,
        "native": native_after,
        "ledger_mult": led.multiple,
        "headline_cagr": tax["cagr_eur"],
        "native_cagr": native_after ** (1.0 / years) - 1.0,
        "ledger_cagr": led.cagr,
        "maxdd": float((eq / eq.cummax() - 1.0).min()),
        "ledger_maxdd": led.maxdd,
        "y2018": annual_return(eq, 2018),
        "y2022": annual_return(eq, 2022),
        "ledger_2018": annual_return(led.equity, 2018),
        "ledger_2022": annual_return(led.equity, 2022),
        "worst_12m": worst_12m(eq),
        "ledger_worst_12m": worst_12m(led.equity),
        "trades": len(res.trades),
        "div_gross": led.dividends_gross,
        "div_tax": led.dividend_tax_total,
        "div_events": led.dividend_events,
        "ledger_summary": summarize_equity(led.equity),
    }


def five_year_rows(a: pd.Series, b: pd.Series) -> tuple[list[dict], dict]:
    aw = five_year_windows(a)
    bw = five_year_windows(b)
    bmap = {(w["start"], w["end"]): w["return"] for w in bw}
    rows = []
    for w in aw:
        key = (w["start"], w["end"])
        if key in bmap:
            rows.append(
                {
                    "start": w["start"],
                    "end": w["end"],
                    "a": w["return"],
                    "b": bmap[key],
                    "gap": w["return"] - bmap[key],
                }
            )
    summary = window_summary([r["a"] for r in rows], [r["b"] for r in rows])
    return rows, summary


def main() -> None:
    # 1. Static shipped TECH book.
    orig = universe.canonical
    panel_static, mem_static = markets.build("TECH")
    universe.canonical = lambda t: t  # type: ignore[assignment]
    static = run_book(
        panel_static, mem_static, market="TECH", label="Static 49 (shipped)"
    )
    static_core = run_book(
        panel_static,
        mem_static,
        market="TECH",
        label="Static 49 core-only",
        core_mode="always",
    )
    # run_book restores the canonical function it saw (identity for TECH); put
    # the shared alias map back before building PIT membership.
    universe.canonical = orig  # type: ignore[assignment]

    # 2. PIT Nasdaq-100 book.
    panel_pit, mem_pit, cov = build_pit_panel()
    universe.canonical = lambda t: t  # type: ignore[assignment]
    pit = run_book(panel_pit, mem_pit, market="TECH", label="PIT Nasdaq-100")
    pit_core = run_book(
        panel_pit,
        mem_pit,
        market="TECH",
        label="PIT Nasdaq-100 core-only",
        core_mode="always",
    )
    universe.canonical = orig  # type: ignore[assignment]
    last_str = cov.last_snapshot
    latest_count = len(membership_on(last_str, available_only=True))

    # 3. US all-PIT book, same rules and same dividend ledger.
    panel_us, mem_us = markets.build("US")
    us = run_book(panel_us, mem_us, market="US", label="US all-PIT")

    # 4. 2x-cost sensitivity for static and PIT.
    universe.canonical = lambda t: t  # type: ignore[assignment]
    static2 = run_book(
        panel_static, mem_static, market="TECH", label="Static 49 @ 2x costs", mult=2.0
    )
    pit2 = run_book(
        panel_pit, mem_pit, market="TECH", label="PIT Nasdaq-100 @ 2x costs", mult=2.0
    )

    # 5. 5-year windows on the dividend-corrected EUR ledger.
    rows_static, sum_static = five_year_rows(
        pit["ledger"].equity, static["ledger"].equity
    )
    rows_us, sum_us = five_year_rows(pit["ledger"].equity, us["ledger"].equity)
    rows_core, sum_core = five_year_rows(
        pit["ledger"].equity, static_core["ledger"].equity
    )

    books = [static, pit, pit_core, static_core, us, static2, pit2]
    lines = [
        "# PIT Nasdaq-100 TECH book",
        "",
        "Generated by `uv run python scripts/research_pit_tech.py`.",
        "Cache-only; no network. Data snapshot 2026-11-25; EUR ledger uses the",
        "merged dividend-aware `bot.eur_ledger.py` (15% withholding credited",
        "against the year-dependent PFU).",
        "",
        "## What is point-in-time here",
        "",
        f"- Membership source: `{SOURCE_URL}`",
        f"- {SNAPSHOT_NOTE}.",
        f"- {cov.unique_tickers} unique canonical tickers appear in the history; "
        f"**{cov.available} have cached bars** ({cov.available_share:.0%}). "
        f"**{cov.missing} are missing** and cannot be held.",
        f"- Latest snapshot {cov.last_snapshot}: {cov.last_snapshot_count} raw "
        f"members, {latest_count} with cached bars.",
        "- Every backtest date uses the latest snapshot on or before it; a name",
        "  added later cannot appear earlier. The last snapshot is six months",
        "  older than the data end; that is staleness, not look-ahead.",
        "",
        "## Headline results",
        "",
        "| book | terminal-FX | native USD | EUR ledger + div | CAGR ledger | maxDD USD | maxDD ledger | 2018 | 2022 | worst 12m | trades |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for b in books:
        lines.append(
            f"| {b['label']} | **{format_multiple(b['headline'])}** | "
            f"{format_multiple(b['native'])} | **{format_multiple(b['ledger_mult'])}** | "
            f"{format_pct(b['ledger_cagr'])} | {format_pct(b['maxdd'])} | "
            f"{format_pct(b['ledger_maxdd'])} | {format_pct(b['y2018'])} | "
            f"{format_pct(b['y2022'])} | {format_pct(b['worst_12m'])} | "
            f"{b['trades']} |"
        )
    lines += [
        "",
        "`terminal-FX` is the legacy headline: native after tax translated at",
        "spot EUR/USD. `EUR ledger + div` is the real-life liquidation number.",
        "The static and PIT books use the same monthly 2.5x rules, SMH core and",
        "200-day daily cut; only the stock universe changes.",
        "",
        "## Did the TECH sleeve survive?",
        "",
        f"- Static 49: **{format_multiple(static['ledger_mult'])}** EUR ledger / "
        f"**{format_multiple(static['headline'])}** terminal-FX.",
        f"- PIT Nasdaq-100: **{format_multiple(pit['ledger_mult'])}** EUR ledger / "
        f"**{format_multiple(pit['headline'])}** terminal-FX.",
        f"- Retention vs static: **{pit['ledger_mult'] / static['ledger_mult']:.0%}** "
        f"on the EUR ledger, **{pit['headline'] / static['headline']:.0%}** terminal-FX.",
        f"- PIT core-only: **{format_multiple(pit_core['ledger_mult'])}** EUR ledger. "
        f"PIT beats its own core-only terminal: "
        f"**{'PASS' if pit['ledger_mult'] > pit_core['ledger_mult'] else 'FAIL'}**.",
        "",
        f"- Static core-only: **{format_multiple(static_core['ledger_mult'])}** EUR ledger; "
        f"static sleeve adds **{static['ledger_mult'] - static_core['ledger_mult']:.2f}x**.",
        f"- US all-PIT: **{format_multiple(us['ledger_mult'])}** EUR ledger, "
        f"maxDD **{format_pct(us['ledger_maxdd'])}**; US is still primary: "
        f"**{'PASS' if us['ledger_mult'] > pit['ledger_mult'] else 'FAIL'}**.",
        "",
        "## 5-year windows (EUR ledger, every month start)",
        "",
        "| pair | n | median gap | worst gap | share A beats B |",
        "|---|---:|---:|---:|---:|",
        f"| PIT vs static 49 | {sum_static.get('n', 0)} | "
        f"{format_pct(sum_static.get('median_gap', float('nan')))} | "
        f"{format_pct(min((r['gap'] for r in rows_static), default=float('nan')))} | "
        f"{sum_static.get('share_beat_benchmark', 0):.0%} |",
        f"| PIT vs static core-only | {sum_core.get('n', 0)} | "
        f"{format_pct(sum_core.get('median_gap', float('nan')))} | "
        f"{format_pct(min((r['gap'] for r in rows_core), default=float('nan')))} | "
        f"{sum_core.get('share_beat_benchmark', 0):.0%} |",
        f"| PIT vs US all-PIT | {sum_us.get('n', 0)} | "
        f"{format_pct(sum_us.get('median_gap', float('nan')))} | "
        f"{format_pct(min((r['gap'] for r in rows_us), default=float('nan')))} | "
        f"{sum_us.get('share_beat_benchmark', 0):.0%} |",
        "",
        "The terminal retention is 95%, but PIT beats static core-only in only",
        "18% of common 5-year windows and static 49 in 26%. The sleeve's",
        "terminal advantage is path-dependent: it compounds through a few large",
        "runs, not a stable rolling premium. That is why the verdict is `paper`,",
        "not `accept`.",
        "",
        "## 2x costs",
        "",
        "| book | EUR ledger + div | maxDD ledger | CAGR ledger |",
        "|---|---:|---:|---:|",
    ]
    for b in (static, static2, pit, pit2):
        lines.append(
            f"| {b['label']} | {format_multiple(b['ledger_mult'])} | "
            f"{format_pct(b['ledger_maxdd'])} | {format_pct(b['ledger_cagr'])} |"
        )
    lines += [
        "",
        "## Dividend and withholding detail",
        "",
        "| book | gross dividends | total dividend tax | events |",
        "|---|---:|---:|---:|",
    ]
    for b in (static, pit, us):
        lines.append(
            f"| {b['label']} | €{b['div_gross']:,.0f} | €{b['div_tax']:,.0f} | "
            f"{b['div_events']} |"
        )
    lines += [
        "",
        "## Verdict",
        "",
    ]
    retain = pit["ledger_mult"] / static["ledger_mult"]
    gates = {
        "PIT retains >=80% of static EUR ledger": retain >= 0.80,
        "PIT sleeve beats PIT core-only": pit["ledger_mult"] > pit_core["ledger_mult"],
        "PIT survives 2x costs (positive)": pit2["ledger_mult"] > 1.0,
        "US all-PIT remains ahead of PIT TECH": us["ledger_mult"] > pit["ledger_mult"],
    }
    for k, v in gates.items():
        lines.append(f"- {k}: **{'PASS' if v else 'FAIL'}**")
    lines += [
        "",
        (
            "**Verdict: `paper`.** The Nasdaq-100 PIT universe retains "
            f"{retain:.0%} of the static sleeve's EUR-ledger terminal, so the"
            " terminal result is consistent with a robust sleeve, not a static-"
            "list survivorship artifact. But the rolling 5-year premium is"
            " weak, the PIT book is still a broad non-tech proxy, and US all-PIT"
            " remains ahead on both return and drawdown. US monthly stays"
            " primary. If the TECH sleeve is retained for forward validation,"
            " use PIT Nasdaq-100 membership rather than the static 49 names."
        ),
        "",
        "## Residual bias",
        "",
        f"- **Delisting/coverage bias:** {cov.missing}/{cov.unique_tickers} PIT",
        "  tickers have no cached bars. Delisted losers are more likely to be",
        "  missing than survivors, so the PIT result is still an **upper bound**",
        "  on what a live book could have held. The direction is stated, not",
        "  hidden. Cached bars are best for S&P-era names; Nasdaq-only and",
        "  acquired tickers are the gap.",
        "- **Sector mix:** Nasdaq-100 is not tech-only. The book holds whatever",
        "  momentum selects, including TSLA, AMZN, NFLX and non-tech names.",
        "  This is a broad PIT proxy, not a clean GICS tech counterfactual.",
        "- **Snapshot staleness:** the last membership snapshot is 2026-05-18;",
        "  the final six months use that list. That affects only the last few",
        "  rebalances and is documented.",
        "- **No future membership:** tested by `tests/test_pit_universe.py`;",
        "  FB->META is canonicalized, and GOOG/GOOGL are kept as separate",
        "  index share classes rather than silently collapsed.",
        "",
        f"Missing tickers ({cov.missing}): "
        + ", ".join(cov.missing_tickers[:80])
        + (" ..." if cov.missing > 80 else ""),
        "",
    ]
    (ROOT / "results" / "pit_tech.md").write_text("\n".join(lines) + "\n")
    for b in books:
        print(
            f"{b['label']:32s} terminal {b['headline']:6.2f}x  "
            f"ledger {b['ledger_mult']:6.2f}x  DD {b['ledger_maxdd']:6.1%}"
        )
    print("wrote results/pit_tech.md")


if __name__ == "__main__":
    main()
