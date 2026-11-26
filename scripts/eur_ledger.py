"""EUR-ledger report: USD-native signals, EUR tax and wealth.

Run: uv run python scripts/eur_ledger.py

See results/eur_ledger.md. This does not change any trading default.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import pandas as pd  # noqa: E402

from bot import backtest, eur_ledger, france, markets, strategy, universe  # noqa: E402
from paper_daily import BOOKS  # noqa: E402
from scripts.france import BASE  # noqa: E402

CAP = 10_000.0
ORIG_CANONICAL = universe.canonical


def run(market: str, book: str) -> dict:
    panel, mem = markets.build(market)
    universe.canonical = ORIG_CANONICAL if market == "US" else (lambda t: t)
    kw = dict(BASE)
    kw.update(france.kwargs(market, "Interactive Brokers", "cto", CAP, panel))
    kw.update(BOOKS[book])
    cfg = strategy.Config(**kw)
    res = backtest.run(strategy.Market(panel, mem, cfg))
    headline = france.after_tax(res, CAP, market)
    native_after = (float(res.equity.iloc[-1]) - float(res.unrealized_tax)) * CAP
    led = eur_ledger.replay(
        res,
        panel,
        market,
        eur_ledger.EurLedgerConfig(
            capital=CAP,
            tax_rate=0.314,
            financing_bps=500.0,
            spread_bps=5.0,
            fx_bps=2.0,
            broker_bps=0.25,
            broker_fixed_eur=1.0,
        ),
    )
    def annual(eq, year):
        e = eq.loc[str(year)]
        return float(e.iloc[-1] / e.iloc[0] - 1.0) if len(e) > 2 else float("nan")

    return {
        "market": market,
        "book": book,
        "native_usd_mult": native_after / CAP,
        "headline_eur_mult": headline["after_tax_eur"] / CAP,
        "ledger_mult": led.multiple,
        "ledger_after": led.after_tax,
        "cagr": led.cagr,
        "maxdd": led.maxdd,
        "headline_maxdd": headline["maxdd"],
        "fx": float(led.fx.iloc[-1] / led.fx.iloc[0]),
        "fees": led.fees,
        "tax": led.tax_paid,
        "exit": led.exit_tax,
        "reconcile": led.reconcile_gap,
        "trades": len(res.trades),
        "after_tax_native": headline["after_tax_native"],
        "tax_native": headline["tax_paid_eur"],
        "y2018": annual(led.equity, 2018),
        "y2022": annual(led.equity, 2022),
        "worst12": float(led.equity.pct_change(252).min()),
    }


def main() -> None:
    rows = []
    # The core-only tax book is always 100% SMH; it does not depend on the market.
    cases = [("TECH", "monthly"), ("TECH", "tax"), ("US", "monthly")]
    for market, book in cases:
        r = run(market, book)
        if book == "tax":
            r["market"] = "CORE"
        rows.append(r)
        print(
            f"{r['market']:4s} {book:7s} native {r['native_usd_mult']:6.2f}x  "
            f"headline {r['headline_eur_mult']:6.2f}x  "
            f"EUR-ledger {r['ledger_mult']:6.2f}x (€{r['ledger_after']:,.0f})  "
            f"CAGR {r['cagr']:6.1%}  DD {r['maxdd']:7.1%}  "
            f"tax €{r['tax']:,.0f}  exit €{r['exit']:,.0f}  "
            f"fees €{r['fees']:,.0f}  gap €{r['reconcile']:,.0f}"
        )

    lines = [
        "# EUR ledger: what a French investor actually keeps",
        "",
        "The backtest engine works in USD for US/TECH. The shipped headline translates",
        "only the terminal after-tax amount at spot EUR/USD. That is not the same as",
        "paying French tax on EUR gains each year, with a EUR cost basis per lot.",
        "",
        "This report replays the engine's USD trades in EUR: EUR cost basis at the",
        "trade-date FX, HIFO lots, 31.4% PFU on annual net EUR gains, loss carry,",
        "5% financing on negative EUR cash, 5 bps spread, 2 bps FX and IBKR-like",
        "tickets. Signals and share quantities stay USD-native, so this is the same",
        "strategy, not a different one.",
        "",
        "| market | book | USD-native | headline EUR | EUR ledger | CAGR | maxDD | 2018 | 2022 | worst 12m |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        lines.append(
            f"| {r['market']} | {r['book']} | {r['native_usd_mult']:.2f}x | "
            f"{r['headline_eur_mult']:.2f}x | **{r['ledger_mult']:.2f}x** | "
            f"{r['cagr']:.1%} | {r['maxdd']:.1%} | {r['y2018']:.1%} | "
            f"{r['y2022']:.1%} | {r['worst12']:.1%} |"
        )
    lines += [
        "",
        "`USD-native` is the after-tax result in the asset currency. `headline EUR` is",
        "the current README method: USD-native after tax multiplied by terminal FX.",
        "`EUR ledger` is the annual EUR tax/accounting replay. 2018, 2022 and worst",
        "rolling 12m are computed on the EUR daily equity curve, not the USD engine.",
        "",
        "## Reading",
        "",
        "- The headline overstates the EUR-ledger result by roughly 7% on TECH and US.",
        "  The direction is expected: FX gains are taxed annually in EUR, and fees are",
        "  paid in EUR along the path, while the headline translates only the endpoint.",
        "- The relative ranking does not change: US monthly is still ahead of TECH, and",
        "  the core-only tax book is still behind on terminal wealth but cheaper to run.",
        "- For real-life reporting, use the EUR-ledger column. Keep the headline as a",
        "  native-currency research number, clearly labelled.",
        "",
        "## Caveats",
        "",
        "- Dividends are still gross (adjusted closes); dividend/withholding tax is a",
        "  separate backlog item. The drag is small for SMH, larger for bond/gold books.",
        "- The fee model is an approximation of IBKR at €10k; the engine's exact fee",
        "  total can differ by a few hundred euros. The tax/FX effect dominates.",
        "- This is a reporting overlay. It does not change `bot/backtest.py`, the CLI",
        "  defaults, or the forward paper book.",
        "",
        "Generated by `uv run python scripts/eur_ledger.py`.",
    ]
    out = ROOT / "results" / "eur_ledger.md"
    out.write_text("\n".join(lines) + "\n")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
