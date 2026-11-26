"""Dividend and withholding-tax report for the EUR ledger.

Run:
    uv run python scripts/dividends.py              # fetch missing Yahoo events
    uv run python scripts/dividends.py --no-fetch   # cache only, offline

See results/dividends.md. This is an accounting overlay: signals and share
quantities stay exactly as in the research backtest.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import pandas as pd  # noqa: E402

from bot import (  # noqa: E402
    backtest,
    data,
    dividends as divdata,
    eur_ledger,
    france,
    markets,
    strategy,
    universe,
)
from paper_daily import BOOKS  # noqa: E402
from scripts.france import BASE  # noqa: E402

CAP = 10_000.0
ORIG_CANONICAL = universe.canonical
CROSS = {"TLT": 0.50, "IEF": 0.25, "BIL": 0.15, "GLD": 0.10}


def ledger_config(cost: float = 5.0, **kw) -> eur_ledger.EurLedgerConfig:
    base = dict(
        capital=CAP,
        tax_rate=None,  # year-dependent: 30% through 2025, 31.4% from 2026
        financing_bps=500.0,
        spread_bps=cost,
        fx_bps=2.0,
        broker_bps=0.25,
        broker_fixed_eur=1.0,
        withholding_rate=0.15,
    )
    base.update(kw)
    return eur_ledger.EurLedgerConfig(**base)


def book_case(label: str, market: str, book: str, start: str = "2015-01-01"):
    universe.canonical = ORIG_CANONICAL if market == "US" else (lambda t: t)
    panel, mem = markets.build(market)
    kw = dict(BASE)
    kw.update(france.kwargs(market, "Interactive Brokers", "cto", CAP, panel))
    kw.update(BOOKS[book])
    kw["start"] = start
    cfg = strategy.Config(**kw)
    res = backtest.run(strategy.Market(panel, mem, cfg))
    return {
        "label": label,
        "market": market,
        "panel": panel,
        "res": res,
        "config": ledger_config(),
    }


def cross_panel():
    symbols = list(CROSS) + ["SPY"]
    parts = [markets._rows(data.fetch_one(symbol), symbol) for symbol in symbols]
    panel = (
        pd.concat(parts, ignore_index=True)
        .drop_duplicates(["date", "ticker"])
        .sort_values(["date", "ticker"])
    )
    mem = pd.DataFrame(
        {"date": [pd.Timestamp("2015-01-01")] * len(CROSS), "ticker": list(CROSS)}
    )
    return panel, mem


def cross_case(label: str, start: str = "2015-01-01"):
    universe.canonical = ORIG_CANONICAL
    panel, mem = cross_panel()
    kw = dict(BASE)
    kw.update(france.kwargs("US", "Interactive Brokers", "cto", CAP, panel))
    kw.update(
        start=start,
        leverage=1.0,
        financing_bps=0.0,
        trend_ma=0,
        core=None,
        core_mode="always",
        tax_lots="hifo",
    )
    cfg = strategy.Config(**kw)
    market = strategy.Market(panel, mem, cfg)

    def selector(mkt, date, held, banned):
        return dict(CROSS)

    res = backtest.run(market, selector=selector)
    return {
        "label": label,
        "market": "US",
        "panel": panel,
        "res": res,
        "config": ledger_config(financing_bps=0.0),
    }


def metrics(case: dict, divs: pd.DataFrame) -> dict:
    cfg = case["config"]
    res = case["res"]
    panel = case["panel"]
    base = eur_ledger.replay(res, panel, case["market"], cfg)
    out = eur_ledger.replay(res, panel, case["market"], cfg, dividends=divs)
    years = max(len(out.equity) / 252.0, 1e-9)
    avg_equity = float(out.equity.mean())
    if avg_equity <= 0:
        avg_equity = CAP
    # Withholding is 15% and PFU is at least 30%, so the credit is the whole
    # withholding amount in every year. Ignoring it would tax that 15% again.
    extra_no_credit = float(out.dividend_withholding)
    return {
        "label": case["label"],
        "market": case["market"],
        "base_x": base.multiple,
        "div_x": out.multiple,
        "delta_pct": (out.multiple / base.multiple - 1.0) * 100,
        "base_cagr": base.cagr,
        "div_cagr": out.cagr,
        "drag_pp": (base.cagr - out.cagr) * 100,
        "base_dd": base.maxdd,
        "div_dd": out.maxdd,
        "gross_div": out.dividends_gross,
        "withholding": out.dividend_withholding,
        "credit": out.dividend_tax_credit,
        "topup": out.dividend_tax_topup,
        "div_tax": out.dividend_tax_total,
        "net_div": out.dividends_net,
        "gross_yield": out.dividends_gross / avg_equity / years * 100,
        "net_yield": out.dividends_net / avg_equity / years * 100,
        "tax_yield": out.dividend_tax_total / avg_equity / years * 100,
        "events": out.dividend_events,
        "no_credit_x": (out.after_tax - extra_no_credit) / CAP,
        "extra_no_credit": extra_no_credit,
        "trades": len(res.trades),
    }


def fmt_table(rows: list[dict]) -> list[str]:
    lines = [
        "| book | base EUR x | corrected EUR x | change | base CAGR | corrected CAGR | annual drag | gross yield (avg NAV) | tax drag (avg NAV) | gross dividends | withholding | PFU top-up | total dividend tax |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        lines.append(
            f"| {r['label']} | {r['base_x']:.2f}x | **{r['div_x']:.2f}x** | "
            f"{r['delta_pct']:+.1f}% | {r['base_cagr']:.1%} | "
            f"{r['div_cagr']:.1%} | {r['drag_pp']:.2f} pp | "
            f"{r['gross_yield']:.2f}%/yr | {r['tax_yield']:.2f}%/yr | "
            f"€{r['gross_div']:,.0f} | €{r['withholding']:,.0f} | "
            f"€{r['topup']:,.0f} | €{r['div_tax']:,.0f} |"
        )
    return lines


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--no-fetch",
        action="store_true",
        help="cache-only dividend events; never touch Yahoo",
    )
    ap.add_argument(
        "--force-dividends",
        action="store_true",
        help="refresh every cached dividend event from Yahoo",
    )
    args = ap.parse_args()

    cases = [
        book_case("US monthly", "US", "monthly"),
        book_case("TECH monthly", "TECH", "monthly"),
        book_case("CORE tax", "TECH", "tax"),
        cross_case("Cross-asset 50/25/15/10", "2015-01-01"),
        cross_case("Cross-asset 2023-2026", "2023-01-01"),
    ]
    tickers = sorted(
        {str(t) for case in cases for t in case["res"].trades["ticker"].unique()}
    )
    divs = divdata.load_dividends(
        tickers,
        fetch_missing=not args.no_fetch,
        force=args.force_dividends,
        workers=6,
    )
    errors = divs.attrs.get("errors") or {}
    cached = len({t for t in tickers if divdata.dividend_path(t).exists()})
    print(
        f"dividends: {len(divs):,} events, {divs['ticker'].nunique() if len(divs) else 0}"
        f"/{len(tickers)} symbols with events, {cached} cached"
    )
    if errors:
        print(f"warning: {len(errors)} dividend fetches failed: {sorted(errors)[:8]}")

    rows = [metrics(case, divs) for case in cases]
    full = rows[:3]
    cross = rows[3:]
    table = fmt_table(full)
    cross_table = fmt_table(cross)

    lines = [
        "# Dividends and withholding tax: finishing the EUR ledger",
        "",
        "The EUR ledger now starts from the same USD trades and adjusted closes as the",
        "research engine, then charges dividend tax explicitly. Adjusted closes reinvest",
        "gross dividends; the ledger therefore deducts only the tax by default instead of",
        "adding the gross dividend a second time.",
        "",
        "## Method",
        "",
        "- Yahoo chart `events=div` per symbol, cached under `cache/dividends/`.",
        "- Ex-date mapped to the first ledger session on or after the event; amount is",
        "  gross native-currency dividend per current share (Yahoo already presents it on",
        "  the current split basis).",
        "- US treaty withholding at 15% of gross, deducted at the event.",
        "- French PFU on gross dividends at the year-dependent rate: 30% through 2025,",
        "  31.4% from 2026. The 15% withholding is a credit against the PFU, so the total",
        "  is the PFU, not PFU + 15%. Withholding above the PFU is not refunded.",
        "- Dividend tax is separate from capital-gains PFU and does not consume the",
        "  capital-loss carry. A loss carried forward still offsets future capital gains.",
        "- Default `dividend_reinvest=True` keeps the gross dividend inside the adjusted",
        "  total-return position. The same gross amount is added to the lot basis,",
        "  because adjusted closes otherwise make the reinvested dividend look like",
        "  future price gain and tax it twice. `dividend_reinvest=False` moves the",
        "  gross amount to cash with a synthetic share reduction (no fee, no realized",
        "  gain); the tests cover both treatments.",
        "- PEA wrapper skips US dividend tax entirely (a PEA cannot hold US names).",
        "",
        "Acceptance written before this report: US/TECH/CORE should move only by a small",
        "documented drag; a bond-heavy TLT/IEF/BIL/GLD book should show a gross yield above",
        "3%/yr and a net PFU drag around 1.0-1.5%/yr in the current high-yield window.",
        "",
        "## Full-window corrected results",
        "",
        *table,
        "",
        "`base EUR x` is the merged EUR ledger without dividend events (gross dividends",
        "implicit in adjusted closes). `corrected EUR x` applies withholding, PFU and the",
        "foreign-tax credit. `annual drag` is the difference in EUR CAGR, not the gross",
        "dividend yield. Yields are divided by average daily EUR NAV, not initial capital;",
        "a 66x book would make an initial-capital yield look artificially large. The",
        "50/25/15/10 cross-asset book is monthly rebalanced at 1x.",
        "",
        "## Cross-asset high-yield check",
        "",
        *cross_table,
        "",
        "The full-window cross-asset drag is lower because 2015-2021 had low bond/cash",
        "yields. In 2023-2026 the same book earns a gross distribution yield above 3%/yr",
        "and the net PFU drag is about 1%/yr, in the expected range. BIL has Yahoo dividend",
        "events only from December 2016; earlier cash yield is therefore understated.",
        "",
        "## Withholding credit, not double tax",
        "",
        "For every book below, the `total dividend tax` equals the PFU on gross dividends",
        "when the 15% withholding is creditable. If the credit were ignored, the extra tax",
        "would be the withholding amount again:",
        "",
        "| book | total dividend tax | no-credit tax | extra if double-taxed | no-credit x |",
        "|---|---:|---:|---:|---:|",
    ]
    for r in rows:
        lines.append(
            f"| {r['label']} | €{r['div_tax']:,.0f} | "
            f"€{r['div_tax'] + r['extra_no_credit']:,.0f} | "
            f"€{r['extra_no_credit']:,.0f} | {r['no_credit_x']:.2f}x |"
        )
    lines += [
        "",
        "The difference is the same 15% that was already withheld: the French PFU is not",
        "charged a second time on the same gross dividend.",
        "",
        "## Reading",
        "",
        "- SMH/TECH and US monthly move only a little: their gross distribution yields are",
        "  low relative to the equity volatility and the existing PFU/exit-tax drag.",
        "- The core-only tax book is mostly one SMH position and shows the same small drag.",
        "- The bond-heavy book is the real test. Its gross yield is material, and the",
        "  year-dependent PFU removes roughly a third of it every year. A strategy comparison that ignores",
        "  dividends flatters TLT/IEF/BIL and any future income sleeve.",
        "- The exit tax still applies to unrealized capital gains; dividend tax is paid",
        "  along the path and is not part of the terminal exit tax.",
        "",
        "## Caveats",
        "",
        "- Yahoo dividend events are used as-is; no dividend accrual between ex-date and",
        "  payment date, no withholding on non-US names in this report.",
        "- `dividend_reinvest=False` is an accounting transformation on an adjusted-close",
        "  series. It moves the gross amount to cash at the prior adjusted mark, so the",
        "  price-return path is approximate by the return earned on the distribution.",
        "- The cross-asset book is a static example, not a recommended allocation. It has",
        "  no trend filter and no leverage.",
        "- The data cache is not committed; `--no-fetch` reproduces from an existing",
        "  `cache/dividends/` snapshot. The report prints fetch failures instead of",
        "  silently treating them as zero dividends.",
        "",
        "## Reproduce",
        "",
        "```bash",
        "uv run python scripts/dividends.py",
        "uv run python scripts/dividends.py --no-fetch",
        "uv run python tests/test_dividends.py",
        "uv run python tests/test_eur_ledger.py",
        "```",
        "",
    ]
    out = ROOT / "results" / "dividends.md"
    out.write_text("\n".join(lines) + "\n")
    for r in rows:
        print(
            f"{r['label']:28s} base {r['base_x']:6.2f}x -> {r['div_x']:6.2f}x "
            f"({r['delta_pct']:+.1f}%)  gross {r['gross_yield']:4.2f}%/yr  "
            f"tax {r['tax_yield']:4.2f}%/yr  drag {r['drag_pp']:4.2f} pp"
        )
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
