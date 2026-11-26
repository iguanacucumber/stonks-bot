# US estate tax for a French investor (2026 planning note)

> **Not tax or legal advice.** This note is a reproducible calculator and a map
> of the questions to ask a French notaire/avocat and a US estate-tax adviser.
> Domicile, treaty residence, ownership, debt, beneficiary clauses and the legal
> domicile of a fund decide the real result. Do not rely on a web page or this
> repository for a filing position.

## The two taxes are different

A French resident who is not a US citizen can face **two separate transfer
taxes** at death:

1. **US federal estate tax** on US-situs assets. It is imposed on the estate,
   not on the heir. For a nonresident non-citizen (NRNC) the statutory exemption
   is only **$60,000**, implemented as a **$13,000 unified credit**. The top
   marginal rate is **40%** (18%-40% schedule; the top bracket starts at
   $1,000,000 of taxable value). The 2026 **$15,000,000** basic exclusion and
   $5,945,800 credit apply to US citizens/domiciliaries, not to an NRNC.
2. **French droits de succession** on the worldwide estate of a person
   domiciled in France. The 2026 direct-line scale is 5%-45% after a
   **€100,000** allowance per child; a surviving spouse/PACS partner is exempt.
   Unrelated beneficiaries pay 60% after €1,594. Assurance-vie has a separate
   death regime.

They can both apply. The France-US estate and gift tax treaty avoids double
taxation and gives a French-domiciled NRNC a **pro-rata US unified credit**. It
does not make French inheritance tax disappear.

## What is US-situs?

IRS guidance says US-situs assets include US real estate, tangible personal
property in the US, and **stock of a corporation organized under US law**, even
if the certificate is held abroad or through a foreign broker. US-domiciled ETFs
(SMH, SPY, QQQ, VOO, etc.) are generally treated as US-situs. The fund's name or
listing currency does not decide it; the fund's legal domicile does.

An Ireland- or Luxembourg-domiciled UCITS ETF is generally a share of a foreign
company and therefore **not US-situs**, even when the fund owns only US stocks.
This is the consistent practitioner reading of IRC §§2104-2105, not an IRS
fund-by-fund ruling. Confirm the fund's legal domicile and structure.

Cash at a US broker can also be US-situs; cash at a foreign branch or qualifying
bank deposit can be outside. The calculator models securities, not cash at a US
custodian.

## The France-US treaty

The 1978 France-US estate and gift tax convention, as amended by the 2004
protocol, gives the estate of a non-US citizen **domiciled in France** a unified
credit equal to the greater of:

- the ordinary NRNC credit ($13,000), or
- the US citizen credit ($5,945,800 in 2026) multiplied by the ratio of the
  US-situs gross estate to the entire worldwide gross estate.

This is Article 12(3) of the 2004 protocol. The IRS Form 706-NA instructions
list France among the treaties with this pro-rata credit. The estate must
provide the worldwide valuation and the information needed to compute the
credit; a treaty position is disclosed (Form 706-NA and, where applicable,
Form 8833). A US citizen or US-domiciled decedent does not get this relief.

For a worldwide estate below the 2026 **$15,000,000** US exclusion, the
pro-rata credit will often eliminate the US tax entirely, even with substantial
US-situs assets. Above that level the calculation can produce a US tax; the
calculator handles it. The treaty does not help if the decedent is not
French-domiciled for treaty purposes, or if the US-situs assets are not the kind
the treaty allows the US to tax.

**Practical point:** the treaty can make the US tax zero while a Form 706-NA is
still required because US-situs assets exceed $60,000. Filing is not the same as
owing.

## The current book

The US monthly 2.5x book uses US-domiciled ETFs and US shares. At €660,000 net
capital and 2.5x gross, the model shows:

| item | value |
|---|---:|
| gross US-situs securities | $1,813,184 |
| net US-situs value (after allocated margin debt) | $725,274 |
| US estate tax, France treaty | **$0** |
| US estate tax, no treaty | **$226,151** |
| French droits de succession, one child | **€110,962** |
| combined, treaty | **€110,962** |

The treaty removes the US tax in this example; French inheritance tax remains.
Without the treaty, the US tail is material. The gross/net distinction matters:
the calculator taxes the net US-situs value but reports gross exposure so the
leverage is not hidden. Debt allocation, margin terms and the situs of cash
require advice.

## Worked examples

100% US-situs CTO, one child, EUR/USD 1.0989 (2026-11-24 cache close).
US tax is USD; French tax is EUR. Combined applies the French credit for US tax
on the US slice.

| capital | treaty | net US-situs | US estate tax | French inheritance | combined | after-tax estate |
|---|---:|---:|---:|---:|---:|---:|
| €10,000 | yes | $10,989 | $0 | €0 | €0 | €10,000 |
| €10,000 | no | $10,989 | $0 | €0 | €0 | €10,000 |
| €100,000 | yes | $109,890 | $0 | €0 | €0 | €100,000 |
| €100,000 | no | $109,890 | $13,767 | €0 | €12,528 | €87,472 |
| €660,000 | yes | $725,274 | $0 | €110,962 | €110,962 | €549,038 |
| €660,000 | no | $725,274 | $226,151 | €110,962 | €205,798 | €454,202 |

The €100k no-treaty US tax is not simply 40% of the excess over $60k: the
statutory tentative tax is progressive (the tax on $109,890 is $26,767, less the
$13,000 credit). Above $1m the marginal rate is 40%. The calculator uses the
actual Table A schedule, not a flat approximation.

The French tax at €660k is for one child after the €100k allowance; the
effective rate is about 16.8% of the estate. With two children the per-child
allowance and the split change the result. A spouse/PACS partner is exempt from
French inheritance tax, but US marital relief for a non-US spouse is not
automatic: the domestic unlimited marital deduction is limited to US-citizen
spouses; a QDOT or the treaty's capped election is needed. The treaty's
pro-rata credit still applies to the US tax on US-situs assets.

## Mitigation options and trade-offs

| option | US-situs effect | French inheritance effect | main trade-off |
|---|---|---|---|
| Keep US-domiciled ETFs/stocks | 100% exposed | ordinary French estate | lowest cost/liquidity for US exposure; US tail |
| Switch core to Irish/Luxembourg UCITS | generally 0 | ordinary French estate; not automatically PEA | TER, withholding, tracking, counterparty; selling may trigger French gains |
| PEA with eligible EU UCITS | 0 in the model | ordinary French estate; PEA closes at death | cannot hold SMH/SPY/QQQ or borrow; fund menu/limits |
| Assurance-vie | 0 in the model | 990 I: €152,500 per beneficiary before 70, then 20%/31.25%; €30,500 global after 70; spouse exempt | fees, insurer/fund menu, beneficiary clause; confirm look-through/situs |
| Keep US-situs at/below $60k | no US filing/tax tail without treaty | ordinary French estate on the rest | caps US exposure; rebalancing and costs |
| Spouse/beneficiary planning | can defer/allocate US tax | spouse/PACS exempt; children's allowances | US non-citizen spouse needs QDOT/treaty election; not automatic |
| Rely on the France treaty | pro-rata credit; often zero US tax under $15m worldwide | France taxes worldwide, credits US tax | domicile/treaty conditions, worldwide valuation, filings, law can change |

A UCITS core or PEA removes US-situs exposure, but it does not remove French
inheritance tax. It can also change the investment result: the repo's UCITS/PEA
comparisons show a material return gap versus the 2.5x US book. Paying more fees
or giving up return to avoid an estate tax that the treaty may already eliminate
is not automatically rational. The right comparison is after-tax family wealth,
not the US tax line alone.

## Recommended order of work

1. **Map situs.** List each holding, its legal domicile (not listing venue),
   broker/custodian and gross/net value. US-domiciled ETFs and US shares are
   US-situs; Irish UCITS generally are not.
2. **Confirm domicile and treaty facts.** French domicile for the treaty,
   citizenship/green card, prior US residence, spouse's status. A US citizen or
   green-card holder is in a different regime.
3. **Value the French estate.** Worldwide assets, debts, marriage regime,
   beneficiaries, prior gifts within 15 years, assurance-vie premiums and ages.
   French tax is often the larger live liability.
4. **Do not over-insure the US tail.** For a French-domiciled investor with a
   worldwide estate below the US exclusion, the treaty credit may already remove
   the US tax. Model the treaty and no-treaty cases separately.
5. **If restructuring:** prioritise new money into UCITS/PEA/assurance-vie,
   avoid a forced sale of embedded gains, compare all-in fees and the loss of
   the US book's return/leverage, and keep enough US-situs below $60k only as a
   treaty-failure buffer if that is the chosen policy.
6. **Document the beneficiary position.** A French will/assurance-vie clause
   does not automatically solve US estate tax. A non-US spouse may need a QDOT
   or the treaty election; get US counsel.
7. **Recheck annually.** The US exclusion is indexed after 2026; the NRNC
   $60,000 threshold is not. The French scale and assurance-vie thresholds can
   change with a finance law.

## Sources and dates

All sources checked **2026-11-26** unless noted.

- IRS, *Some nonresidents with U.S. assets must file estate tax returns* —
  US-situs categories, $60,000 filing threshold:
  https://www.irs.gov/individuals/international-taxpayers/some-nonresidents-with-us-assets-must-file-estate-tax-returns
- IRS, *Estate tax for nonresidents not citizens of the United States* —
  US-situs scope; threshold not indexed:
  https://www.irs.gov/businesses/small-businesses-self-employed/estate-tax-for-nonresidents-not-citizens-of-the-united-states
- IRS, *Instructions for Form 706-NA* (Rev. September 2025) — $13,000 credit,
  France treaty list, pro-rata credit under IRC §2102(b)(3)(A):
  https://www.irs.gov/instructions/i706na
- IRS, *Technical Explanation, US-France Estate and Gift Tax Protocol*
  (2004-12-08), Article 12(3):
  https://www.irs.gov/pub/irs-lbi/treaty-france-estate-gift-protocol-technical-explanation-8-12-2004.pdf
- US Treasury, *Protocol Amending the US-France Estate and Gift Tax
  Convention* (2004-12-08):
  https://home.treasury.gov/system/files/131/Treaty-France-Estate-Gift-Protocol-8-12-2004.pdf
- IRS, *What's new — Estate and gift tax* — 2026 basic exclusion $15,000,000:
  https://www.irs.gov/businesses/small-businesses-self-employed/whats-new-estate-and-gift-tax
- IRS, *Instructions for Form 706* (Rev. July 2026), Table A — 18%-40%
  unified rate schedule and $5,945,800 credit:
  https://www.irs.gov/instructions/i706
- Service-Public.gouv.fr, *Inheritance tax: how much should you pay in 2026?*
  (2026-03-15) — €100,000 child allowance, 5%-45% scale, spouse/PACS exempt:
  https://www.service-public.gouv.fr/particuliers/actualites/A18843?lang=en
- impots.gouv.fr, *Je suis bénéficiaire d’une assurance-vie, comment la
  déclarer ?* — €152,500 per beneficiary before 70; €30,500 global after 70:
  https://www.impots.gouv.fr/particulier/questions/je-suis-beneficiaire-dune-assurance-vie-comment-la-declarer
- economie.gouv.fr, *Quelle est la fiscalité de l’assurance-vie ?*
  (2026-09-17) — 20%/31.25% above the allowance; spouse/PACS exempt:
  https://www.economie.gouv.fr/particuliers/gerer-mon-argent/gerer-mon-budget-et-mon-epargne/quelle-est-la-fiscalite-de-lassurance-vie
- French tax code (CGI) arts. 777 (scale), 779 (allowances), 796-0 bis
  (spouse/PACS), 990 I (assurance-vie before 70), 757 B (after 70). The
  calculator encodes the 2026 direct-line and assurance-vie thresholds.
- PEA eligibility: the repo audit `results/pea_ucits.md` documents that a PEA
  can hold eligible EU/EEA UCITS but not US-domiciled ETFs or US shares.
- UCITS situs: IRC §§2104(a) and 2105(b); practitioner reading that an
  Ireland/Luxembourg UCITS is a foreign company and its shares are generally
  non-US-situs. This is not an IRS fund-by-fund ruling; confirm the fund's legal
  domicile and structure.

## Reproduce

```bash
uv run python scripts/estate_tax.py
uv run python scripts/estate_tax.py --capital 660000 --us-situs-share 1 \
  --wrapper cto --treaty --heir child --leverage 2.5 --json
uv run python tests/test_estate.py
```

`results/estate_tax.md` is the generated worked-example report;
`results/estate_tax.json` has the machine-readable inputs and calculations.
