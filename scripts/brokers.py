"""French broker comparison: same after-tax books, eight retail tariffs.

Books:
  TECH  core-only tax CTO (2.5x SMH) at EUR 10k for all brokers, EUR 100k for
        the four cheapest/largest, native USD signals (the bot as it runs)
  FR    monthly French book at EUR 10k, CTO, all brokers (FTT and tickets)
  PEA   PUST.PA passive buy-and-hold at EUR 10k, the seven PEA brokers

The broker preset changes only fees/FX; signals and tax rules are common. See
results/brokers.md for the tariff sources and the caveats.

Run: uv run python scripts/brokers.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import pandas as pd  # noqa: E402

from bot import france as cfg  # noqa: E402

import france as audit  # noqa: E402
import pea_ucits as pea  # noqa: E402

START = "2015-01-01"
END = "2026-11-25"
CAPITAL = 10_000.0
BIG = 100_000.0
BIG_BROKERS = (
    "Trade Republic",
    "Bourse Direct",
    "Interactive Brokers",
    "Saxo Banque",
)
ALL_BROKERS = tuple(cfg.BROKERS)

SECTIONS = {
    "tech10": "TECH CTO core-only · €10k",
    "tech100": "TECH CTO core-only · €100k",
    "fr10": "FR CTO monthly · €10k",
    "pea10": "PEA PUST.PA buy-and-hold · €10k",
}


def _row(
    section: str,
    market: str,
    wrapper: str,
    book: str,
    broker: str,
    capital: float,
    r: dict,
) -> dict:
    after = float(r["after_tax"])
    return dict(
        section=section,
        market=market,
        wrapper=wrapper,
        book=book,
        broker=broker,
        capital=capital,
        pre_tax=float(r["pre_tax"]),
        fees=float(r["fees"]),
        ftt=float(r["ftt"]),
        tax_paid=float(r["tax_paid"]),
        exit_tax=float(r["exit_tax"]),
        after_tax=after,
        multiple=after / capital,
        cagr=float(r["cagr"]) if after > 0 else float("nan"),
        maxdd=float(r["maxdd"]) if after > 0 else float("nan"),
        trades=int(r["trades"]),
        years=float(r.get("years", 0.0)),
    )


def collect() -> pd.DataFrame:
    rows = []
    for broker in ALL_BROKERS:
        r = audit.run_book("TECH", "CTO", broker, CAPITAL, "tax", "native")
        rows.append(_row("tech10", "TECH", "CTO", "core-only tax", broker, CAPITAL, r))
    for broker in BIG_BROKERS:
        r = audit.run_book("TECH", "CTO", broker, BIG, "tax", "native")
        rows.append(_row("tech100", "TECH", "CTO", "core-only tax", broker, BIG, r))
    for broker in ALL_BROKERS:
        r = audit.run_book("FR", "CTO", broker, CAPITAL, "monthly", "native")
        rows.append(_row("fr10", "FR", "CTO", "monthly", broker, CAPITAL, r))
    for broker in cfg.PEA_BROKERS:
        r = pea.buy_hold("PUST.PA", broker, START, end=END, capital=CAPITAL)
        rows.append(_row("pea10", "FR", "PEA", "PUST buy-hold", broker, CAPITAL, r))
    return pd.DataFrame(rows)


def run_with_fx(market: str, broker: str, capital: float, fx_bps: float) -> dict:
    """One TECH run with a temporary FX override; restores the preset."""
    original = dict(cfg.BROKERS[broker])
    try:
        cfg.BROKERS[broker]["fx_bps"] = fx_bps
        return audit.run_book(market, "CTO", broker, capital, "tax", "native")
    finally:
        cfg.BROKERS[broker].clear()
        cfg.BROKERS[broker].update(original)


def fmt(x: float) -> str:
    return f"€{x:,.0f}"


def main() -> None:
    # Build the US panel before the non-US canonical alias is installed.
    df = collect()
    df.to_csv(ROOT / "results" / "brokers.csv", index=False)
    print(
        df[["section", "broker", "multiple", "fees", "ftt", "tax_paid", "exit_tax"]]
        .assign(multiple=lambda d: d.multiple.round(2))
        .to_string(index=False)
    )

    # FX sensitivity: the IBKR win and the DEGIRO model both hinge on this.
    sens = []
    for broker, bps in (
        ("Interactive Brokers", 2.0),
        ("Interactive Brokers", 3.0),
        ("Interactive Brokers", 25.0),
        ("DEGIRO", 10.0),
        ("DEGIRO", 25.0),
    ):
        r = run_with_fx("TECH", broker, CAPITAL, bps)
        sens.append(
            dict(
                broker=broker,
                fx_bps=bps,
                multiple=r["after_tax"] / CAPITAL,
                fees=r["fees"],
            )
        )
    sens = pd.DataFrame(sens)
    sens.to_csv(ROOT / "results" / "brokers_fx.csv", index=False)

    def table(section: str) -> list[str]:
        sub = df[df.section == section]
        out = [
            "| broker | pre-tax | fees (incl. FTT) | FTT | tax paid | exit tax | "
            "**after tax** | multiple | CAGR | maxDD | trades |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for _, r in sub.iterrows():
            if r["after_tax"] <= 0:
                out.append(
                    f"| {r['broker']} | n/a | {fmt(r['fees'])} | "
                    f"{fmt(r['ftt'])} | n/a | n/a | **wiped out** | "
                    f"**wiped out** | n/a | n/a | {r['trades']} |"
                )
                continue
            out.append(
                f"| {r['broker']} | {fmt(r['pre_tax'])} | {fmt(r['fees'])} | "
                f"{fmt(r['ftt'])} | {fmt(r['tax_paid'])} | {fmt(r['exit_tax'])} | "
                f"**{fmt(r['after_tax'])}** | **{r['multiple']:.2f}x** | "
                f"{r['cagr']:.1%} | {r['maxdd']:.0%} | {r['trades']} |"
            )
        return out + [""]

    tech10 = df[df.section == "tech10"].sort_values("multiple", ascending=False)
    tech100 = df[df.section == "tech100"].sort_values("multiple", ascending=False)
    fr10 = df[df.section == "fr10"].sort_values("multiple", ascending=False)
    pea10 = df[df.section == "pea10"].sort_values("multiple", ascending=False)
    best_cto = tech10.iloc[0]
    best_cto_100 = tech100.iloc[0]
    best_fr = fr10.iloc[0]
    best_pea = pea10.iloc[0]
    worst_cto = tech10.iloc[-1]
    fr_solvent = fr10[fr10.after_tax > 0]
    fr_spread = fr_solvent.multiple.max() - fr_solvent.multiple.min()

    def sensitivity(broker: str, bps: float) -> pd.Series:
        return sens[(sens.broker == broker) & (sens.fx_bps == bps)].iloc[0]

    ibkr2 = sensitivity("Interactive Brokers", 2.0)
    ibkr25 = sensitivity("Interactive Brokers", 25.0)
    deg10 = sensitivity("DEGIRO", 10.0)
    deg25 = sensitivity("DEGIRO", 25.0)
    tr = tech10[tech10.broker == "Trade Republic"].iloc[0]

    lines = [
        "# French broker comparison: eight presets, same after-tax books",
        "",
        "**Verdict: Interactive Brokers is the cheapest CTO for this bot at the",
        "modelled 2 bps FX; the advantage is the EUR/USD conversion, not the",
        "ticket. The best PEA broker is XTB, but all seven PEA brokers are within",
        "0.06x of each other and none changes the wrapper decision.**",
        "",
        "Checked 2026-11-26. This page extends `results/france.md` (two brokers)",
        "and `results/pea_ucits.md` (PEA eligibility) without re-running their",
        "full audits.",
        "",
        "## Method",
        "",
        "The same signal path is run once per broker; only the tariff changes.",
        "The **TECH** book is the core-only tax CTO: 2.5x SMH, 200-day risk cut,",
        "month-end decision, next-open fill, HIFO lots, French PFU. The **FR**",
        "book is the monthly French CTO at €10k, so it pays FTT on buys and shows",
        "the ticket-cost differences. The **PEA** book is a passive buy-and-hold",
        "of Amundi PEA Nasdaq-100 (PUST.PA), the best PEA tech fund in",
        "`results/pea_ucits.md`; it is 1x, EUR-listed, and pays 18.6% social on",
        "the terminal gain. PEA rows use the same 5 bps spread and two tickets",
        "(entry and exit), no FTT (ETFs are not French shares).",
        "",
        "`fees` includes the 5 bps spread, broker tickets, FX and FTT; `FTT` is",
        "the subset paid on French share purchases. `tax paid` is realised tax",
        "already deducted inside pre-tax; `exit tax` is the remaining liability",
        "on the open book, deducted from after tax. Fixed fees are sized from the",
        "account capital, so the €10k and €100k TECH rows are not the same",
        "share quantities.",
        "",
        "**Core-fill caveat.** The TECH rows use the merged engine's honest",
        "next-open fill for SMH/SOXX. The previous report's Trade Republic",
        "38.80x was the close-fill baseline; it is **legacy** and not comparable.",
        "The honest-open rerun gives Trade Republic 39.50x and Interactive",
        "Brokers 50.89x. The execution branch owns `bot/backtest.py` and",
        "`bot/markets.py`; do not re-run the old `--core-close-fill` path for a",
        "headline number.",
        "",
        "## TECH core-only CTO at €10k",
        "",
    ]
    lines += table("tech10")
    lines += [
        f"At the modelled tariffs, **{best_cto['broker']}** ends at",
        f"**{best_cto['multiple']:.2f}x after tax** (fees {fmt(best_cto['fees'])})",
        f"against **{tech10.iloc[1]['multiple']:.2f}x** for",
        f"{tech10.iloc[1]['broker']} and **{worst_cto['multiple']:.2f}x** for",
        f"{worst_cto['broker']} (fees {fmt(worst_cto['fees'])}). The core book",
        "trades rarely (~180 orders in 12 years), so the winner is the broker",
        "with cheap currency conversion, not the one with the cheapest ticket.",
        "",
        "## TECH core-only CTO at €100k",
        "",
    ]
    lines += table("tech100")
    lines += [
        f"**{best_cto_100['broker']}** also wins at €100k "
        f"(**{best_cto_100['multiple']:.2f}x**). The ranking is unchanged; the",
        "gap is slightly wider because the FX saving scales with notional while",
        "the fixed tickets do not.",
        "",
        "## FR CTO monthly at €10k",
        "",
    ]
    lines += table("fr10")
    lines += [
        f"**{best_fr['broker']}** wins the French monthly book too "
        f"(**{best_fr['multiple']:.2f}x** after tax, fees {fmt(best_fr['fees'])}).",
        "This book holds the USD SMH core as well as French shares, so the same",
        "FX effect applies on top of the FTT and ticket grid. FTT is",
        f"{fmt(fr10[fr10.broker == best_fr['broker']].iloc[0]['ftt'])} at",
        f"{best_fr['broker']}; it is a cost no CTO broker can avoid on French",
        "share purchases.",
        "",
        f"**Fortuneo is wiped out at this size.** Its Starter tariff charges",
        "0.35% per side and €50 on every US order to €10k; on a 2.5x monthly",
        "rebalance this consumes the €10k account, and the model's terminal",
        "equity goes negative. Read the row as **not viable**, not as a literal",
        "loss larger than the deposit: a real investor would stop trading before",
        "the fee spiral. This is the clearest demonstration that French ticket",
        "costs, not the 31.4% PFU, are what kill a small high-turnover CTO.",
        "Tax and exit-tax cells are n/a for Fortuneo because the model's equity",
        "crossed zero; the raw values are in `results/brokers.csv`.",
        "",
        "## PEA PUST.PA buy-and-hold at €10k",
        "",
        "DEGIRO is absent because it has no PEA; the other seven presets are in",
        "`france.PEA_BROKERS`, and `--wrapper pea --broker degiro` exits with a",
        "clear error.",
        "",
    ]
    lines += table("pea10")
    lines += [
        f"**{best_pea['broker']}** is the cheapest PEA broker at "
        f"**{best_pea['multiple']:.2f}x**, but the whole field spans only",
        f"{pea10.iloc[0]['multiple'] - pea10.iloc[-1]['multiple']:.3f}x "
        f"({fmt(pea10.iloc[0]['after_tax'] - pea10.iloc[-1]['after_tax'])}).",
        "The tax wrapper and the fund's return dominate; the ticket is a rounding",
        "error on a passive PEA buy-and-hold. XTB's zero-commission PEA wins by",
        f"{pea10.iloc[0]['multiple'] - pea10.iloc[1]['multiple']:.3f}x over",
        f"{pea10.iloc[1]['broker']}, i.e. about",
        f"{fmt(pea10.iloc[0]['after_tax'] - pea10.iloc[1]['after_tax'])} on €10k.",
        "That is not a robust reason to choose a broker on its own.",
        "",
        "## FX sensitivity (TECH €10k)",
        "",
        "| broker | FX assumption | after-tax multiple | fees |",
        "|---|---:|---:|---:|",
    ]
    for _, r in sens.iterrows():
        note = ""
        if r["broker"] == "Interactive Brokers" and r["fx_bps"] == 2.0:
            note = " (study model)"
        if r["broker"] == "Interactive Brokers" and r["fx_bps"] == 3.0:
            note = " (published auto-conversion)"
        if r["broker"] == "Interactive Brokers" and r["fx_bps"] == 25.0:
            note = " (same as the other brokers)"
        if r["broker"] == "DEGIRO" and r["fx_bps"] == 10.0:
            note = " (study model)"
        if r["broker"] == "DEGIRO" and r["fx_bps"] == 25.0:
            note = " (published AutoFX)"
        lines.append(
            f"| {r['broker']}{note} | {r['fx_bps']:.1f} bps | "
            f"{r['multiple']:.2f}x | {fmt(r['fees'])} |"
        )
    lines += [
        "",
        "This is the most important table on the page for the CTO verdict.",
        "IBKR's published FX is 0.002% (0.2 bps) on a manual conversion, minimum",
        "USD 2, or 0.03% (3 bps) when auto-converted inside a trade. The 2 bps",
        "model sits between them. If the investor instead pays a 25 bps",
        f"conversion, IBKR falls to {ibkr25['multiple']:.2f}x, a dead heat with",
        f"Trade Republic's {tr['multiple']:.2f}x: the entire IBKR advantage",
        "is the FX assumption.",
        "",
        "DEGIRO's 2026 tariff says AutoFX is **0.25% (25 bps)**, not the 10 bps",
        "the preset uses. At 10 bps DEGIRO looks like the runner-up at "
        f"{deg10['multiple']:.2f}x; at the published 25 bps it falls to",
        f"{deg25['multiple']:.2f}x, below Trade Republic. The 10 bps preset is",
        "the requested study assumption and is optimistic; the sensitivity row",
        "is the realistic DEGIRO number.",
        "",
        "## What this changes",
        "",
        f"1. **Best CTO broker: {best_cto['broker']}**, at the modelled FX. It is",
        f"   **{best_cto['multiple']:.2f}x at €10k and "
        f"{best_cto_100['multiple']:.2f}x at €100k**. Trade Republic is the",
        "   fallback if the investor cannot get IBKR's spot FX, and it is also the",
        "   simplest €1-ticket broker. The CLI now defaults to",
        "   `interactive-brokers` for `--country fr`; the other presets stay for",
        "   comparison.",
        f"2. **Best PEA broker: {best_pea['broker']}**, but effectively a tie with",
        "   Trade Republic, IBKR and Bourse Direct. Use the PEA only if the",
        "   money is already trapped there; the wrapper decision is not about the",
        "   ticket.",
        f"3. **Broker choice does not change CTO vs PEA.** The best CTO broker",
        f"   turns €10k into **{best_cto['multiple']:.2f}x** after tax; the best PEA",
        f"   broker turns it into **{best_pea['multiple']:.2f}x** on PUST. Even the",
        f"   worst CTO broker on the TECH book (**{worst_cto['broker']}**, "
        f"{worst_cto['multiple']:.2f}x) is far ahead. The PEA saves ~12.8 pp of",
        "   tax, but it cannot hold SMH or borrow; that exposure gap is the",
        "   decision, not the broker.",
        "4. **The French monthly book is broker-sensitive.** At €10k the solvent",
        f"   CTO brokers span {fr_spread:.2f}x of multiple; Fortuneo is",
        "   wiped out by its 0.35% per-side Starter tariff plus the US fixed fees.",
        "   The core-only book is less sensitive in relative terms because it",
        "   trades less, but its FX exposure still makes IBKR's conversion",
        "   assumption decisive.",
        "",
        "## Caveats",
        "",
        "- **IBKR FX is the winner's soft spot.** 0.002% manual (min USD 2) needs",
        "  a manual conversion and a multi-currency account; 0.03% auto is still",
        "  cheap, but the 2 bps model is an assumption. At 25 bps IBKR ties Trade",
        "  Republic. The IBKR max €29/order cap and the fixed-plan €3 minimum are",
        "  not modelled; the tiered 1.25 minimum is.",
        "- **DEGIRO has no PEA** and its published AutoFX is 0.25%. The preset's",
        "  10 bps is optimistic; see the sensitivity row. DEGIRO also charges a",
        "  €2/order handling fee, which the grid models as one €2 ticket.",
        "- **Fortuneo's first free order ≤€500/month is not modelled**, and the",
        "  €30 foreign-broker fee is omitted above €10k (the grid charges €50 to",
        "  €10k, then 0.20% only). Both make Fortuneo look slightly worse at small",
        "  tickets and slightly better above €10k than the preset.",
        "- **BoursoBank's PEA ticket is capped at 0.50% by law**, so the 0.60%",
        "  rate only applies to the CTO; the preset models 0.50% for the PEA and",
        "  0.60% for US CTO.",
        "- **XTB's 0% PEA commission stops at €100k of transactions per month**",
        "  (then 0.2%). The €10k book never approaches it; a €100k monthly",
        "  rebalance could. XTB also charges 0.5% FX on non-EUR lines, which is",
        "  why it loses the USD CTO despite the zero ticket.",
        "- **Saxo's 2026 promo (0% on 70 PEA stocks)** is ignored; the standard",
        "  0.08% Classic tariff is used. The promo is temporary and does not cover",
        "  the whole PEA universe.",
        "- **Custody**: only Bourse Direct charges it in this model (0.036%/yr on",
        "  US lines); the other presets say no custody. Check the current brochure",
        "  before opening an account.",
        "- **No dividend withholding, no bid/ask beyond 5 bps, no market impact,**",
        "  no borrow squeeze. The TECH book is 2.5x and can gap through the",
        "  leverage band.",
        "- **PEA 5-year clock**: the PUST row assumes a withdrawal after 5 years",
        "  and 18.6% social. A PEA closed earlier also pays 12.8% IR.",
        "",
        "## Sources",
        "",
        "All checked 2026-11-26 unless the source states a different date.",
        "",
    ]
    for name in ALL_BROKERS:
        info = cfg.BROKER_INFO[name]
        lines.append(
            f"- **{name}**: {info['note']} [{info['source']}]({info['source']})"
        )
    lines += [
        "- **IBKR PEA eligibility and pricing**: "
        "[interactivebrokers.ie PEA](https://www.interactivebrokers.ie/en/accounts/plan-depargne-en-action-accounts.php); "
        "[IBKR spot FX](https://www.interactivebrokers.com/en/pricing/commissions-spot-currencies.php).",
        "- **Saxo PEA comparison**: "
        "[home.saxo PEA](https://www.home.saxo/fr-fr/accounts/pea) (Classic 0.08%, "
        "min €2, 0.25% FX; promo 0% on 70 stocks to 2026-12-31).",
        "- **BoursoBank 2026 brochure**: "
        "[boursorama brochure](https://www.boursorama.com/content/brochure_tarifaire/boursorama_bt.pdf) "
        "(Découverte 1.99 € to 500 €, then 0.60%; PEA capped at 0.50%).",
        "- **DEGIRO tariff**: [Tarifs.pdf](https://degiro.fr/data/pdf/fr/Tarifs.pdf) "
        "(2026-10-29: 1 € brokerage + 1 € handling; AutoFX 0.25%).",
        "- **Fortuneo**: [fortuneo.fr/bourse](https://www.fortuneo.fr/bourse) "
        "(Starter 0.35%, first order ≤500 € free; US 0.20% min 20 € + 30 €).",
        "- **XTB**: [xtb.com/fr/pea](https://www.xtb.com/fr/pea) (0% to 100k €/month, "
        "then 0.2%; 0.5% conversion).",
        "",
        "## Reproduce",
        "",
        "```",
        "uv run python scripts/brokers.py",
        "uv run python tests/test_brokers.py",
        "uv run python tests/test_core.py",
        "```",
        "",
        "Generated by `uv run python scripts/brokers.py`.",
    ]
    (ROOT / "results" / "brokers.md").write_text("\n".join(lines) + "\n")
    print("wrote results/brokers.md, results/brokers.csv, results/brokers_fx.csv")


if __name__ == "__main__":
    main()
