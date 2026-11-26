"""US estate-tax exposure: worked examples and mitigation scenarios.

Run: uv run python scripts/estate_tax.py
      uv run python scripts/estate_tax.py --capital 660000 --us-situs-share 1 \
        --leverage 2.5 --heir child --json

This is a planning calculator, not tax or legal advice. It reads no market data
and never calls the network. Legal sources and dates are in docs/estate_tax.md.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot import estate  # noqa: E402


def fmt_eur(x: float) -> str:
    return f"€{x:,.0f}"


def fmt_usd(x: float) -> str:
    return f"${x:,.0f}"


def row_table(rows: list[dict], label_key: str = "label") -> list[str]:
    out = [
        "| scenario | US-situs net | US estate tax | French inheritance | combined | after-tax estate |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        out.append(
            f"| {r[label_key]} | {fmt_usd(r['net_us_situs_usd'])} | "
            f"**{fmt_usd(r['us_tax_usd'])}** | {fmt_eur(r['french_tax_eur'])} | "
            f"{fmt_eur(r['combined_tax_eur'])} | {fmt_eur(r['after_tax_estate_eur'])} |"
        )
    return out


def build_report(rows: list[dict], scenarios: list[dict]) -> str:
    current = scenarios[0]
    no_treaty = scenarios[1]
    spouse = estate.assess(
        estate.EstateInputs(
            capital_eur=current["capital_eur"],
            us_situs_share=1.0,
            wrapper="cto",
            treaty=False,
            heir="spouse",
        )
    )
    lines = [
        "# US estate-tax exposure for a French investor",
        "",
        "> **Not tax or legal advice.** This is a reproducible planning model, not a",
        "> tax return or an opinion. Domicile, treaty residence, ownership, debt,",
        "> beneficiary clauses and the fund's legal domicile decide the real result.",
        "> Have a French notaire/avocat and a US estate-tax adviser review the facts.",
        "",
        "## Headline",
        "",
        "A French-domiciled investor who is not a US citizen and holds US-domiciled",
        "ETFs (SMH/SPY/QQQ) or US shares directly has **US-situs assets**. Without a",
        "treaty the US estate-tax exemption is only **$60,000**, enforced through a",
        "**$13,000 unified credit**; above it the marginal rate reaches **40%**.",
        "The 2026 US citizen/domiciliary exclusion is **$15,000,000**, but that is",
        "not the NRNC threshold.",
        "",
        f"At **€660,000** in the current US book, the model shows "
        f"**{fmt_usd(current['gross_us_situs_usd'])} gross** and "
        f"**{fmt_usd(current['net_us_situs_usd'])} net US-situs exposure** at 2.5x "
        "gross. The France-US treaty formula gives a pro-rata credit of "
        f"**{fmt_usd(current['us_credit_formula'])}**, capped at the "
        f"**{fmt_usd(current['us_tentative_tax'])}** tentative tax, so the US "
        f"estate tax is **{fmt_usd(current['us_tax_usd'])}**. Without the treaty it would be "
        f"**{fmt_usd(no_treaty['us_tax_usd'])}**. French droits de succession are "
        f"**{fmt_eur(current['french_tax_eur'])}** for one child in this example.",
        "",
        "The treaty does not repeal French inheritance tax. It mainly prevents the",
        "US from taxing the same US-situs assets again; France taxes the worldwide",
        "estate and credits the US tax on the US slice (capped at the French tax",
        "on that slice).",
        "",
        "## Worked examples (100% US-situs CTO, one child)",
        "",
        "EUR/USD = 1.0989 (2026-11-24 cache close). US tax is in USD; French tax is",
        "in EUR. ‘Combined’ applies the French credit for US tax on the US slice.",
        "‘No treaty’ still includes the French inheritance tax and the credit.",
        "",
        "| capital | treaty | capital USD | net US-situs | US estate tax | French inheritance | combined | after-tax estate |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        lines.append(
            f"| {fmt_eur(r['capital_eur'])} | {'yes' if r['treaty'] else 'no'} | "
            f"{fmt_usd(r['capital_usd'])} | {fmt_usd(r['net_us_situs_usd'])} | "
            f"**{fmt_usd(r['us_tax_usd'])}** | {fmt_eur(r['french_tax_eur'])} | "
            f"{fmt_eur(r['combined_tax_eur'])} | {fmt_eur(r['after_tax_estate_eur'])} |"
        )
    lines += [
        "",
        "- At €10k the US-situs value is under $60k: no US estate tax even without",
        "  the treaty, and French tax is zero for a child under the €100k allowance.",
        "- At €100k the no-treaty US tax starts; the treaty pro-rata credit removes",
        "  it. French tax is still zero for one child because the €100k allowance",
        "  exactly covers the estate.",
        "- At €660k the no-treaty US tax is material; the treaty credit removes it.",
        "  French inheritance tax remains the larger live liability.",
        "",
        "## Current book and mitigation (€660k, 2.5x gross, one child)",
        "",
        "The current book is assumed 100% US-situs: SMH/SPY/QQQ and US shares.",
        "The UCITS, PEA and assurance-vie rows are structural illustrations; they",
        "are not recommendations and they do not remove French inheritance tax.",
        "",
    ]
    lines += row_table(scenarios, "scenario")
    lines += [
        "",
        f"Spouse scenario without the treaty: US estate tax "
        f"**{fmt_usd(spouse['us_tax_usd'])}**, French inheritance tax "
        f"**{fmt_eur(spouse['french_tax_eur'])}**. A surviving spouse/PACS partner",
        "is exempt from French droits de succession, but US law does not give a",
        "non-US spouse the unlimited marital deduction; the France-US treaty adds a",
        "capped marital deduction/QDOT route. Do not assume the spouse is exempt",
        "from US estate tax without checking the treaty election.",
        "",
        "## What the scenarios mean",
        "",
        "1. **UCITS core (Irish/Luxembourg-domiciled).** The fund shares are generally",
        "   not US-situs, even when the fund owns US equities. This removes the US",
        "   estate-tax tail. It does not remove French inheritance tax, and it changes",
        "   fees, dividend withholding, tracking and counterparty structure. A UCITS",
        "   ETF is not automatically PEA-eligible; check the fund.",
        "2. **PEA.** A French PEA can hold eligible EU/EEA UCITS, not US-domiciled",
        "   ETFs or US shares. The model treats the PEA as non-US-situs, so no US",
        "   estate tax; French inheritance tax still applies. The PEA is closed on",
        "   death and its tax advantage stops.",
        "3. **Assurance-vie.** The contract is a French wrapper; the model treats it",
        "   as non-US-situs. French death tax is the specific 990 I regime: €152,500",
        "   per beneficiary for premiums paid before 70, then 20% to €700k taxable",
        "   and 31.25% above. Fees, fund menu and the beneficiary clause matter. The",
        "   US-situs status of assets inside a contract must be confirmed; do not",
        "   rely on the wrapper alone.",
        "4. **Keep US-situs at/below $60k.** This removes the US filing/tax tail",
        "   without a treaty, but leaves the rest of the portfolio in UCITS or cash.",
        "   Under the treaty the US tax is already zero for these examples; the",
        "   $60k cap is mainly insurance against treaty/domicile failure.",
        "5. **Spouse/beneficiary planning.** French spouse/PACS exemption is broad;",
        "   US marital relief for a non-US spouse is not automatic. A QDOT or the",
        "   treaty election needs advice. Beneficiary clauses on assurance-vie",
        "   override the ordinary estate for the insured amount.",
        "",
        "## Trade-offs",
        "",
        "- UCITS/PEA/assurance-vie reduce US-situs exposure but may have higher fees,",
        "  lower liquidity, different withholding, counterparty/synthetic risk, or a",
        "  smaller fund menu. The switch can also realise French capital gains.",
        "- Assurance-vie and PEA do not avoid French inheritance tax automatically;",
        "  they change the base/allowances. The French spouse exemption and the",
        "  €100k per-child allowance are usually the largest ordinary-estate reliefs.",
        "- The France-US treaty only helps a decedent **domiciled in France** for",
        "  treaty purposes, not a US citizen/green-card holder, and requires",
        "  information and filings (Form 706-NA; treaty position/Form 8833).",
        "- The US $60,000 threshold is not indexed. The US citizen $15,000,000",
        "  exclusion is a different rule and does not apply to an NRNC estate.",
        "- Leverage: the model uses net US-situs value for the tax and reports gross",
        "  exposure separately. Debt allocation, margin terms and situs of cash can",
        "  change the estate-tax base; get advice.",
        "",
        "## Sources (checked 2026-11-26 unless stated)",
        "",
        "- IRS, *Some nonresidents with U.S. assets must file estate tax returns*:",
        "  US-situs categories; $60,000 filing threshold.",
        "  https://www.irs.gov/individuals/international-taxpayers/some-nonresidents-with-us-assets-must-file-estate-tax-returns",
        "- IRS, *Estate tax for nonresidents not citizens of the United States*:",
        "  filing threshold not indexed; US-situs scope.",
        "  https://www.irs.gov/businesses/small-businesses-self-employed/estate-tax-for-nonresidents-not-citizens-of-the-united-states",
        "- IRS, *Instructions for Form 706-NA* (Rev. September 2025): $13,000",
        "  unified credit; France is a death-tax treaty country; pro-rata credit",
        "  under IRC 2102(b)(3)(A). https://www.irs.gov/instructions/i706na",
        "- IRS, *Technical Explanation, US-France Estate and Gift Tax Protocol*",
        "  (signed 2004-12-08), Article 12(3): greater of the NRNC credit or the",
        "  citizen credit prorated by US-situs/worldwide gross estate.",
        "  https://www.irs.gov/pub/irs-lbi/treaty-france-estate-gift-protocol-technical-explanation-8-12-2004.pdf",
        "- US Treasury, *Protocol Amending the US-France Estate and Gift Tax",
        "  Convention* (2004-12-08).",
        "  https://home.treasury.gov/system/files/131/Treaty-France-Estate-Gift-Protocol-8-12-2004.pdf",
        "- IRS, *What's new — Estate and gift tax*: 2026 basic exclusion",
        "  $15,000,000; credit equivalent $5,945,800.",
        "  https://www.irs.gov/businesses/small-businesses-self-employed/whats-new-estate-and-gift-tax",
        "- IRS, *Instructions for Form 706* (Rev. July 2026), Table A: unified",
        "  rate schedule 18%-40%. https://www.irs.gov/instructions/i706",
        "- Service-Public.gouv.fr, *Inheritance tax: how much should you pay in",
        "  2026?* (2026-03-15): €100,000 child allowance; 5%-45% direct-line scale;",
        "  spouse/PACS exempt.",
        "  https://www.service-public.gouv.fr/particuliers/actualites/A18843?lang=en",
        "- impots.gouv.fr, *Je suis bénéficiaire d’une assurance-vie*: €152,500 per",
        "  beneficiary before 70; €30,500 global after 70.",
        "  https://www.impots.gouv.fr/particulier/questions/je-suis-beneficiaire-dune-assurance-vie-comment-la-declarer",
        "- economie.gouv.fr, *Quelle est la fiscalité de l’assurance-vie ?*",
        "  (2026-09-17): 20% to €700k taxable, 31.25% above; spouse/PACS exempt.",
        "  https://www.economie.gouv.fr/particuliers/gerer-mon-argent/gerer-mon-budget-et-mon-epargne/quelle-est-la-fiscalite-de-lassurance-vie",
        "- French tax code (CGI) arts. 777, 779, 796-0 bis; 990 I; 757 B. The",
        "  calculator encodes the 2026 direct-line and assurance-vie thresholds.",
        "- UCITS situs: IRS stock-situs rule is place of incorporation; an",
        "  Ireland/Luxembourg UCITS is a foreign company, so its shares are generally",
        "  not US-situs even when it holds US equities. This is the consistent",
        "  practitioner reading, not an IRS fund-by-fund ruling; confirm the fund",
        "  domicile and structure. See IRC §§2104(a), 2105(b) and the IRS NRNC page",
        "  above.",
        "",
        "## Reproduce",
        "",
        "```bash",
        "uv run python scripts/estate_tax.py",
        "uv run python scripts/estate_tax.py --capital 660000 --us-situs-share 1 \\",
        "  --wrapper cto --treaty --heir child --leverage 2.5 --json",
        "uv run python tests/test_estate.py",
        "```",
        "",
        "Generated by `uv run python scripts/estate_tax.py`.",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capital", type=float, default=660_000.0)
    parser.add_argument("--us-situs-share", type=float, default=1.0)
    parser.add_argument(
        "--wrapper", choices=("cto", "pea", "assurance_vie", "ucits"), default="cto"
    )
    parser.add_argument("--heir", choices=("child", "spouse", "other"), default="child")
    parser.add_argument("--heirs", type=int, default=1)
    parser.add_argument("--leverage", type=float, default=2.5)
    parser.add_argument("--eur-usd", type=float, default=estate.DEFAULT_EUR_USD)
    parser.add_argument("--treaty", dest="treaty", action="store_true", default=True)
    parser.add_argument("--no-treaty", dest="treaty", action="store_false")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    inp = estate.EstateInputs(
        capital_eur=args.capital,
        us_situs_share=args.us_situs_share,
        wrapper=args.wrapper,
        treaty=args.treaty,
        heir=args.heir,
        heirs=args.heirs,
        leverage=args.leverage,
        eur_usd=args.eur_usd,
    )
    row = estate.assess(inp)
    if args.json:
        print(json.dumps(row, indent=2, sort_keys=True))
        return
    rows = estate.worked_examples(eur_usd=args.eur_usd)
    scenarios = estate.mitigation_scenarios(
        estate.EstateInputs(
            capital_eur=args.capital,
            us_situs_share=args.us_situs_share,
            wrapper=args.wrapper,
            treaty=args.treaty,
            heir=args.heir,
            heirs=args.heirs,
            leverage=args.leverage,
            eur_usd=args.eur_usd,
        )
    )
    report = build_report(rows, scenarios)
    out = ROOT / "results" / "estate_tax.md"
    out.write_text(report)
    json_out = ROOT / "results" / "estate_tax.json"
    json_out.write_text(
        json.dumps(
            {
                "inputs": inp.__dict__,
                "assessment": row,
                "worked_examples": rows,
                "scenarios": scenarios,
            },
            indent=2,
            default=str,
        )
        + "\n"
    )
    print(
        f"US-situs net {fmt_usd(row['net_us_situs_usd'])}; "
        f"US tax {fmt_usd(row['us_tax_usd'])}; "
        f"French inheritance {fmt_eur(row['french_tax_eur'])}; "
        f"combined {fmt_eur(row['combined_tax_eur'])}"
    )
    print(f"wrote {out} and {json_out}")


if __name__ == "__main__":
    main()
