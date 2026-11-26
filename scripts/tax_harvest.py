"""Tax-loss harvesting sweep on the US monthly 2.5x EUR-ledger book.

Run: uv run python scripts/tax_harvest.py

Pre-registered grid: baseline plus -5/-10/-20% loss thresholds, with/without
immediate re-entry, 0/1/2% minimum-notional band, at 1x and 2x all-in fees.
The decision is made at the month-end close and executed at the next open. The
overlay sells a losing EUR lot and (when enabled) rebuys the same shares at the
same open, so the target weight is not changed by the harvest itself.

Acceptance (written before the sweep): a variant is an opt-in candidate only if,
at both 1x and 2x costs, it beats the baseline on EUR after-tax multiple, 5-year
median and worst window, 2018, 2022 and bootstrap p5. Otherwise reject.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import pandas as pd  # noqa: E402

from bot import (
    backtest,
    dividends as dividend_events,
    france,
    markets,
    strategy,
    universe,
)  # noqa: E402
from bot.tax_harvest import HarvestConfig, run_variant  # noqa: E402
from paper_daily import BOOKS  # noqa: E402
from scripts.france import BASE  # noqa: E402

CAP = 10_000.0
ORIG_CANONICAL = universe.canonical


def load_book():
    panel, mem = markets.build("US")
    universe.canonical = ORIG_CANONICAL
    kw = dict(BASE)
    kw.update(france.kwargs("US", "Interactive Brokers", "cto", CAP, panel))
    kw.update(BOOKS["monthly"])
    res = backtest.run(strategy.Market(panel, mem, strategy.Config(**kw)))
    tickers = sorted(set(res.trades["ticker"])) if len(res.trades) else []
    events = dividend_events.load_dividends(tickers, fetch_missing=False)
    return panel, res, (events if len(events) else None)


def fmt_money(x: float) -> str:
    return f"€{x:,.0f}"


def pct(x: float) -> str:
    return f"{x:.1%}"


def row_label(r: dict) -> str:
    return (
        f"{r['threshold']:.0%} / {'reenter' if r['reenter'] else 'cash'} / "
        f"{r['band']:.0%} band"
    )


def main() -> None:
    panel, res, divs = load_book()
    baseline = run_variant(
        res,
        panel,
        divs,
        HarvestConfig(threshold=0.0, reenter=True, band=0.0, fee_multiplier=1.0),
        CAP,
        run_bootstrap=True,
    )
    baseline2 = run_variant(
        res,
        panel,
        divs,
        HarvestConfig(threshold=0.0, reenter=True, band=0.0, fee_multiplier=2.0),
        CAP,
        run_bootstrap=True,
    )
    print(
        f"baseline EUR {baseline['multiple']:.4f}x  fees {fmt_money(baseline['fees'])}  "
        f"tax {fmt_money(baseline['tax_paid'])}  exit {fmt_money(baseline['exit_tax'])}  "
        f"div tax {fmt_money(baseline['dividend_tax'])}"
    )
    if abs(baseline["multiple"] - 66.1655) > 0.01:
        raise SystemExit(f"baseline mismatch: {baseline['multiple']:.4f}x != 66.1655x")

    rows = []
    for cost in (1.0, 2.0):
        for threshold in (0.05, 0.10, 0.20):
            for reenter in (True, False):
                for band in (0.0, 0.01, 0.02):
                    cfg = HarvestConfig(
                        threshold=threshold,
                        reenter=reenter,
                        band=band,
                        max_turnover=0.50,
                        fee_multiplier=cost,
                    )
                    r = run_variant(
                        res,
                        panel,
                        divs,
                        cfg,
                        CAP,
                        baseline_led=baseline["ledger"]
                        if cost == 1.0
                        else baseline2["ledger"],
                        run_bootstrap=True,
                    )
                    rows.append(r)
    table = pd.DataFrame(
        [
            {
                "threshold": r["threshold"],
                "reenter": r["reenter"],
                "band": r["band"],
                "cost": r["fee_multiplier"],
                "multiple": r["multiple"],
                "cagr": r["cagr"],
                "maxdd": r["maxdd"],
                "tax_paid": r["tax_paid"],
                "exit_tax": r["exit_tax"],
                "fees": r["fees"],
                "div_tax": r["dividend_tax"],
                "harvest_names": r["harvest_names"],
                "harvest_turnover": r["harvest_turnover"],
                "y2018": r["y2018"],
                "y2022": r["y2022"],
                "window_median": r["window_median"],
                "window_worst": r["window_worst"],
                "boot_p5": r["boot_p5"],
                "label": r["label"],
            }
            for r in rows
        ]
    )

    def passes(r: dict, base: dict) -> bool:
        return bool(
            r["multiple"] > base["multiple"] + 1e-9
            and r["boot_p5"] > base["boot_p5"] + 1e-9
            and r["y2018"] >= base["y2018"] - 1e-9
            and r["y2022"] >= base["y2022"] - 1e-9
            and r["window_median"] >= base["window_median"] - 1e-9
            and r["window_worst"] >= base["window_worst"] - 1e-9
        )

    by_cost = {
        1.0: [r for r in rows if r["fee_multiplier"] == 1.0],
        2.0: [r for r in rows if r["fee_multiplier"] == 2.0],
    }
    passing = [
        r
        for r in by_cost[1.0]
        if passes(r, baseline)
        and any(
            x["threshold"] == r["threshold"]
            and x["reenter"] == r["reenter"]
            and x["band"] == r["band"]
            and passes(x, baseline2)
            for x in by_cost[2.0]
        )
    ]
    verdict = (
        "ACCEPT as opt-in: " + ", ".join(row_label(r) for r in passing)
        if passing
        else "REJECT: no harvest variant survives both 1x and 2x cost gates."
    )

    lines = [
        "# Tax-loss harvesting on the US monthly 2.5x book",
        "",
        "**Pre-registration (written before the sweep).** France has no wash-sale",
        "ban: a losing EUR lot may be sold and immediately rebought. The hypothesis",
        "is that realising losses at month-end offsets other capital gains and",
        "raises after-tax EUR wealth after paying the extra round-trip fees.",
        "The strategy is an overlay on the shipped US monthly book, not a new signal.",
        "",
        "Grid: baseline; loss thresholds **-5% / -10% / -20%** measured against the",
        "EUR HIFO lot basis; **immediate re-entry or cash**; minimum harvested",
        "notional **0% / 1% / 2% of equity**; all fees at **1x and 2x**. Per-date",
        "harvest notional is capped at **50% of equity**; names the post-rebalance",
        "target no longer wants are not harvested. Decision at the month-end close,",
        "fill at the next open; the callback never sees a future price.",
        "",
        "Acceptance: a variant is opt-in only if **at both cost levels** it beats",
        "the baseline on EUR after-tax multiple, 5-year median and worst window,",
        "2018, 2022 and bootstrap p5. Otherwise reject and keep the report.",
        "",
        "## Baseline reproduction",
        "",
        "| metric | value |",
        "|---|---:|",
        f"| EUR after-tax multiple | **{baseline['multiple']:.4f}x** |",
        f"| CAGR | {pct(baseline['cagr'])} |",
        f"| maxDD | {pct(baseline['maxdd'])} |",
        f"| tax paid | {fmt_money(baseline['tax_paid'])} |",
        f"| exit tax | {fmt_money(baseline['exit_tax'])} |",
        f"| fees | {fmt_money(baseline['fees'])} |",
        f"| dividend tax | {fmt_money(baseline['dividend_tax'])} |",
        f"| 2018 / 2022 | {pct(baseline['y2018'])} / {pct(baseline['y2022'])} |",
        f"| 5y median / worst | {baseline['window_median']:.2f}x / {baseline['window_worst']:.2f}x |",
        f"| bootstrap p5 | {baseline['boot_p5']:.2f}x |",
        "",
        "Baseline is the merged EUR ledger with cached dividend events; it reproduces",
        "**66.1655x** within 0.01x, and the script exits non-zero otherwise.",
        "",
        "## All variants — 1x costs",
        "",
        "| threshold | re-entry | band | multiple | CAGR | maxDD | tax paid | exit | fees | harvest names | harvest turnover | 2018 | 2022 | 5y median | 5y worst | boot p5 | vs base |",
        "|---:|:---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        if r["fee_multiplier"] != 1.0:
            continue
        lines.append(
            f"| {r['threshold']:.0%} | {'yes' if r['reenter'] else 'no'} | "
            f"{r['band']:.0%} | {r['multiple']:.3f}x | {pct(r['cagr'])} | "
            f"{pct(r['maxdd'])} | {fmt_money(r['tax_paid'])} | "
            f"{fmt_money(r['exit_tax'])} | {fmt_money(r['fees'])} | "
            f"{r['harvest_names']} | {r['harvest_turnover']:.2f}x | "
            f"{pct(r['y2018'])} | {pct(r['y2022'])} | "
            f"{r['window_median']:.2f}x | {r['window_worst']:.2f}x | "
            f"{r['boot_p5']:.2f}x | {r['multiple'] - baseline['multiple']:+.3f}x |"
        )
    lines += [
        "",
        "## All variants — 2x costs",
        "",
        "| threshold | re-entry | band | multiple | CAGR | maxDD | tax paid | exit | fees | harvest names | harvest turnover | 2018 | 2022 | 5y median | 5y worst | boot p5 | vs base |",
        "|---:|:---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        if r["fee_multiplier"] != 2.0:
            continue
        lines.append(
            f"| {r['threshold']:.0%} | {'yes' if r['reenter'] else 'no'} | "
            f"{r['band']:.0%} | {r['multiple']:.3f}x | {pct(r['cagr'])} | "
            f"{pct(r['maxdd'])} | {fmt_money(r['tax_paid'])} | "
            f"{fmt_money(r['exit_tax'])} | {fmt_money(r['fees'])} | "
            f"{r['harvest_names']} | {r['harvest_turnover']:.2f}x | "
            f"{pct(r['y2018'])} | {pct(r['y2022'])} | "
            f"{r['window_median']:.2f}x | {r['window_worst']:.2f}x | "
            f"{r['boot_p5']:.2f}x | {r['multiple'] - baseline2['multiple']:+.3f}x |"
        )
    lines += [
        "",
        "## Reading",
        "",
        f"**Verdict: {verdict}**",
        "",
        "- Realising a loss lowers current-year PFU, but the rebought lot has a lower",
        "  basis, so most of the benefit returns as a larger exit tax. The path is",
        "  mainly tax **deferral** plus two extra fees per round trip.",
        "- The cash variants (no immediate re-entry) change market exposure until the",
        "  next monthly rebalance; they are a separate, weaker strategy and are not",
        "  a clean test of pure loss harvesting.",
        "- The 2x-cost table is the gate. A variant that only wins at 1x is not real.",
        "- All variants, including failures and cash variants, are in the tables.",
        "",
        "## Caveats",
        "",
        "- The loss test uses the EUR HIFO basis, including buy fees; the re-entry",
        "  price is the same session open used by the baseline fill.",
        "- Annual PFU and 10-year loss carry are in the merged EUR ledger; dividend",
        "  tax is separate and is not consumed by the capital-loss carry.",
        "- 5-year window multiples use a 31.4% terminal-gain overlay on the EUR curve",
        "  (same approximation as the v2 evaluation tables), not a rerun of annual",
        "  tax for each window.",
        "- The bootstrap p5 is a stationary 10k-path bootstrap with an exit-tax",
        "  overlay, not a second annual tax engine.",
        "- The baseline is the dividend-corrected EUR ledger (66.17x), not the",
        "  terminal-FX headline (70.86x).",
        "",
        "## Reproduce",
        "",
        "```bash",
        "uv run python scripts/tax_harvest.py",
        "uv run python tests/test_tax_harvest.py",
        "uv run python tests/test_eur_ledger.py",
        "```",
        "",
        "Generated by `uv run python scripts/tax_harvest.py`.",
    ]
    (ROOT / "results" / "tax_harvest.md").write_text("\n".join(lines) + "\n")
    print(f"verdict: {verdict}")
    print("wrote results/tax_harvest.md")


if __name__ == "__main__":
    main()
