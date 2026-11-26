# French broker comparison: eight presets, same after-tax books

**Verdict: Interactive Brokers is the cheapest CTO for this bot at the
modelled 2 bps FX; the advantage is the EUR/USD conversion, not the
ticket. The best PEA broker is XTB, but all seven PEA brokers are within
0.06x of each other and none changes the wrapper decision.**

Checked 2026-11-26. This page extends `results/france.md` (two brokers)
and `results/pea_ucits.md` (PEA eligibility) without re-running their
full audits.

## Method

The same signal path is run once per broker; only the tariff changes.
The **TECH** book is the core-only tax CTO: 2.5x SMH, 200-day risk cut,
month-end decision, next-open fill, HIFO lots, French PFU. The **FR**
book is the monthly French CTO at €10k, so it pays FTT on buys and shows
the ticket-cost differences. The **PEA** book is a passive buy-and-hold
of Amundi PEA Nasdaq-100 (PUST.PA), the best PEA tech fund in
`results/pea_ucits.md`; it is 1x, EUR-listed, and pays 18.6% social on
the terminal gain. PEA rows use the same 5 bps spread and two tickets
(entry and exit), no FTT (ETFs are not French shares).

`fees` includes the 5 bps spread, broker tickets, FX and FTT; `FTT` is
the subset paid on French share purchases. `tax paid` is realised tax
already deducted inside pre-tax; `exit tax` is the remaining liability
on the open book, deducted from after tax. Fixed fees are sized from the
account capital, so the €10k and €100k TECH rows are not the same
share quantities.

**Core-fill caveat.** The TECH rows use the merged engine's honest
next-open fill for SMH/SOXX. The previous report's Trade Republic
38.80x was the close-fill baseline; it is **legacy** and not comparable.
The honest-open rerun gives Trade Republic 39.50x and Interactive
Brokers 50.89x. The execution branch owns `bot/backtest.py` and
`bot/markets.py`; do not re-run the old `--core-close-fill` path for a
headline number.

## TECH core-only CTO at €10k

| broker | pre-tax | fees (incl. FTT) | FTT | tax paid | exit tax | **after tax** | multiple | CAGR | maxDD | trades |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Bourse Direct | €513,219 | €40,799 | €0 | €49,189 | €166,747 | **€346,472** | **34.65x** | 34.8% | -70% | 179 |
| Trade Republic | €581,807 | €33,533 | €0 | €53,050 | €186,819 | **€394,989** | **39.50x** | 36.3% | -69% | 179 |
| Interactive Brokers | €740,681 | €9,952 | €0 | €60,651 | €231,799 | **€508,882** | **50.89x** | 39.2% | -68% | 178 |
| Saxo Banque | €537,847 | €39,610 | €0 | €50,881 | €174,260 | **€363,587** | **36.36x** | 35.3% | -70% | 178 |
| XTB | €449,331 | €49,447 | €0 | €46,079 | €148,415 | **€300,916** | **30.09x** | 33.2% | -71% | 179 |
| Fortuneo | €371,315 | €41,567 | €0 | €38,381 | €122,078 | **€249,237** | **24.92x** | 31.1% | -71% | 179 |
| BoursoBank | €343,580 | €57,870 | €0 | €39,973 | €116,601 | **€226,978** | **22.70x** | 30.1% | -72% | 178 |
| DEGIRO | €676,882 | €19,282 | €0 | €57,560 | €213,712 | **€463,170** | **46.32x** | 38.1% | -69% | 179 |

At the modelled tariffs, **Interactive Brokers** ends at
**50.89x after tax** (fees €9,952)
against **46.32x** for
DEGIRO and **22.70x** for
BoursoBank (fees €57,870). The core book
trades rarely (~180 orders in 12 years), so the winner is the broker
with cheap currency conversion, not the one with the cheapest ticket.

## TECH core-only CTO at €100k

| broker | pre-tax | fees (incl. FTT) | FTT | tax paid | exit tax | **after tax** | multiple | CAGR | maxDD | trades |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Trade Republic | €5,854,982 | €335,547 | €0 | €533,366 | €1,879,916 | **€3,975,066** | **39.75x** | 36.4% | -69% | 179 |
| Bourse Direct | €5,265,383 | €413,091 | €0 | €503,311 | €1,710,621 | **€3,554,762** | **35.55x** | 35.1% | -70% | 179 |
| Interactive Brokers | €7,439,974 | €98,985 | €0 | €608,987 | €2,328,337 | **€5,111,637** | **51.12x** | 39.3% | -68% | 179 |
| Saxo Banque | €5,383,413 | €396,311 | €0 | €509,246 | €1,744,196 | **€3,639,216** | **36.39x** | 35.3% | -70% | 178 |

**Interactive Brokers** also wins at €100k (**51.12x**). The ranking is unchanged; the
gap is slightly wider because the FX saving scales with notional while
the fixed tickets do not.

## FR CTO monthly at €10k

| broker | pre-tax | fees (incl. FTT) | FTT | tax paid | exit tax | **after tax** | multiple | CAGR | maxDD | trades |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Bourse Direct | €83,103 | €25,682 | €2,942 | €26,155 | €18,992 | **€64,111** | **6.41x** | 16.6% | -70% | 1247 |
| Trade Republic | €160,584 | €24,923 | €4,850 | €42,941 | €35,850 | **€124,734** | **12.47x** | 23.2% | -70% | 1229 |
| Interactive Brokers | €206,809 | €15,543 | €5,757 | €50,267 | €44,624 | **€162,185** | **16.22x** | 25.9% | -69% | 1233 |
| Saxo Banque | €134,346 | €28,092 | €4,310 | €38,427 | €30,374 | **€103,972** | **10.40x** | 21.4% | -70% | 1237 |
| XTB | €124,434 | €32,092 | €4,151 | €37,387 | €28,820 | **€95,614** | **9.56x** | 20.5% | -70% | 1229 |
| Fortuneo | n/a | €29,930 | €182 | n/a | n/a | **wiped out** | **wiped out** | n/a | n/a | 397 |
| BoursoBank | €52,120 | €32,433 | €2,405 | €22,258 | €12,614 | **€39,507** | **3.95x** | 12.0% | -73% | 1249 |
| DEGIRO | €182,394 | €18,757 | €5,218 | €45,726 | €39,834 | **€142,559** | **14.26x** | 24.6% | -70% | 1229 |

**Interactive Brokers** wins the French monthly book too (**16.22x** after tax, fees €15,543).
This book holds the USD SMH core as well as French shares, so the same
FX effect applies on top of the FTT and ticket grid. FTT is
€5,757 at
Interactive Brokers; it is a cost no CTO broker can avoid on French
share purchases.

**Fortuneo is wiped out at this size.** Its Starter tariff charges
0.35% per side and €50 on every US order to €10k; on a 2.5x monthly
rebalance this consumes the €10k account, and the model's terminal
equity goes negative. Read the row as **not viable**, not as a literal
loss larger than the deposit: a real investor would stop trading before
the fee spiral. This is the clearest demonstration that French ticket
costs, not the 31.4% PFU, are what kill a small high-turnover CTO.
Tax and exit-tax cells are n/a for Fortuneo because the model's equity
crossed zero; the raw values are in `results/brokers.csv`.

## PEA PUST.PA buy-and-hold at €10k

DEGIRO is absent because it has no PEA; the other seven presets are in
`france.PEA_BROKERS`, and `--wrapper pea --broker degiro` exits with a
clear error.

| broker | pre-tax | fees (incl. FTT) | FTT | tax paid | exit tax | **after tax** | multiple | CAGR | maxDD | trades |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Bourse Direct | €68,440 | €110 | €0 | €0 | €10,870 | **€57,570** | **5.76x** | 15.6% | -31% | 2 |
| Trade Republic | €68,556 | €41 | €0 | €0 | €10,891 | **€57,664** | **5.77x** | 15.6% | -31% | 2 |
| Interactive Brokers | €68,495 | €79 | €0 | €0 | €10,880 | **€57,615** | **5.76x** | 15.6% | -31% | 2 |
| Saxo Banque | €68,454 | €102 | €0 | €0 | €10,872 | **€57,582** | **5.76x** | 15.6% | -31% | 2 |
| XTB | €68,564 | €39 | €0 | €0 | €10,893 | **€57,671** | **5.77x** | 15.6% | -31% | 2 |
| Fortuneo | €68,084 | €313 | €0 | €0 | €10,804 | **€57,281** | **5.73x** | 15.5% | -31% | 2 |
| BoursoBank | €67,879 | €430 | €0 | €0 | €10,766 | **€57,114** | **5.71x** | 15.5% | -31% | 2 |

**XTB** is the cheapest PEA broker at **5.77x**, but the whole field spans only
0.056x (€557).
The tax wrapper and the fund's return dominate; the ticket is a rounding
error on a passive PEA buy-and-hold. XTB's zero-commission PEA wins by
0.001x over
Trade Republic, i.e. about
€6 on €10k.
That is not a robust reason to choose a broker on its own.

## FX sensitivity (TECH €10k)

| broker | FX assumption | after-tax multiple | fees |
|---|---:|---:|---:|
| Interactive Brokers (study model) | 2.0 bps | 50.89x | €9,952 |
| Interactive Brokers (published auto-conversion) | 3.0 bps | 50.33x | €11,214 |
| Interactive Brokers (same as the other brokers) | 25.0 bps | 39.47x | €33,704 |
| DEGIRO (study model) | 10.0 bps | 46.32x | €19,282 |
| DEGIRO (published AutoFX) | 25.0 bps | 39.22x | €33,508 |

This is the most important table on the page for the CTO verdict.
IBKR's published FX is 0.002% (0.2 bps) on a manual conversion, minimum
USD 2, or 0.03% (3 bps) when auto-converted inside a trade. The 2 bps
model sits between them. If the investor instead pays a 25 bps
conversion, IBKR falls to 39.47x, a dead heat with
Trade Republic's 39.50x: the entire IBKR advantage
is the FX assumption.

DEGIRO's 2026 tariff says AutoFX is **0.25% (25 bps)**, not the 10 bps
the preset uses. At 10 bps DEGIRO looks like the runner-up at 46.32x; at the published 25 bps it falls to
39.22x, below Trade Republic. The 10 bps preset is
the requested study assumption and is optimistic; the sensitivity row
is the realistic DEGIRO number.

## What this changes

1. **Best CTO broker: Interactive Brokers**, at the modelled FX. It is
   **50.89x at €10k and 51.12x at €100k**. Trade Republic is the
   fallback if the investor cannot get IBKR's spot FX, and it is also the
   simplest €1-ticket broker. The CLI now defaults to
   `interactive-brokers` for `--country fr`; the other presets stay for
   comparison.
2. **Best PEA broker: XTB**, but effectively a tie with
   Trade Republic, IBKR and Bourse Direct. Use the PEA only if the
   money is already trapped there; the wrapper decision is not about the
   ticket.
3. **Broker choice does not change CTO vs PEA.** The best CTO broker
   turns €10k into **50.89x** after tax; the best PEA
   broker turns it into **5.77x** on PUST. Even the
   worst CTO broker on the TECH book (**BoursoBank**, 22.70x) is far ahead. The PEA saves ~12.8 pp of
   tax, but it cannot hold SMH or borrow; that exposure gap is the
   decision, not the broker.
4. **The French monthly book is broker-sensitive.** At €10k the solvent
   CTO brokers span 12.27x of multiple; Fortuneo is
   wiped out by its 0.35% per-side Starter tariff plus the US fixed fees.
   The core-only book is less sensitive in relative terms because it
   trades less, but its FX exposure still makes IBKR's conversion
   assumption decisive.

## Caveats

- **IBKR FX is the winner's soft spot.** 0.002% manual (min USD 2) needs
  a manual conversion and a multi-currency account; 0.03% auto is still
  cheap, but the 2 bps model is an assumption. At 25 bps IBKR ties Trade
  Republic. The IBKR max €29/order cap and the fixed-plan €3 minimum are
  not modelled; the tiered 1.25 minimum is.
- **DEGIRO has no PEA** and its published AutoFX is 0.25%. The preset's
  10 bps is optimistic; see the sensitivity row. DEGIRO also charges a
  €2/order handling fee, which the grid models as one €2 ticket.
- **Fortuneo's first free order ≤€500/month is not modelled**, and the
  €30 foreign-broker fee is omitted above €10k (the grid charges €50 to
  €10k, then 0.20% only). Both make Fortuneo look slightly worse at small
  tickets and slightly better above €10k than the preset.
- **BoursoBank's PEA ticket is capped at 0.50% by law**, so the 0.60%
  rate only applies to the CTO; the preset models 0.50% for the PEA and
  0.60% for US CTO.
- **XTB's 0% PEA commission stops at €100k of transactions per month**
  (then 0.2%). The €10k book never approaches it; a €100k monthly
  rebalance could. XTB also charges 0.5% FX on non-EUR lines, which is
  why it loses the USD CTO despite the zero ticket.
- **Saxo's 2026 promo (0% on 70 PEA stocks)** is ignored; the standard
  0.08% Classic tariff is used. The promo is temporary and does not cover
  the whole PEA universe.
- **Custody**: only Bourse Direct charges it in this model (0.036%/yr on
  US lines); the other presets say no custody. Check the current brochure
  before opening an account.
- **No dividend withholding, no bid/ask beyond 5 bps, no market impact,**
  no borrow squeeze. The TECH book is 2.5x and can gap through the
  leverage band.
- **PEA 5-year clock**: the PUST row assumes a withdrawal after 5 years
  and 18.6% social. A PEA closed earlier also pays 12.8% IR.

## Sources

All checked 2026-11-26 unless the source states a different date.

- **Bourse Direct**: Euronext 0.99/1.90/2.90/3.80 EUR, 0.09% above 4.4k; US 8.50 EUR to 10k then 0.09%; 0.036%/yr custody on foreign names; 0.25% FX modelled. [https://www.boursedirect.fr/fr/tarifs](https://www.boursedirect.fr/fr/tarifs)
- **Trade Republic**: EUR 1/order, no custody, ~0.25% FX on USD names. [https://traderepublic.com/fr-fr/pea](https://traderepublic.com/fr-fr/pea)
- **Interactive Brokers**: Euronext 0.05%, min 1.25 EUR (tiered plan; fixed plan min 3 EUR); US USD 0.005/share, min USD 1; FX 0.002% manual / 0.03% auto, modelled at 2 bps; no custody. [https://www.interactivebrokers.ie/fr/pricing/commissions-stocks.php](https://www.interactivebrokers.ie/fr/pricing/commissions-stocks.php)
- **Saxo Banque**: Classic 0.08%, min EUR 2 Euronext / USD 1 US; 0.25% FX; no custody. 2026 promo (0% on 70 PEA stocks) ignored. [https://www.home.saxo/fr-fr/accounts/pea](https://www.home.saxo/fr-fr/accounts/pea)
- **XTB**: PEA 0% to EUR 100k/month, then 0.2%; US CTO 0% commission, 0.5% FX conversion; no custody. [https://www.xtb.com/fr/pea](https://www.xtb.com/fr/pea)
- **Fortuneo**: PEA Starter 0.35%, first order <= EUR 500 each month free (not modelled); US CTO 0.20%, min EUR 20, + EUR 30 foreign-broker fee (modelled as EUR 50 to 10k; the EUR 30 is omitted above 10k); 0.25% FX; no custody. [https://www.fortuneo.fr/bourse](https://www.fortuneo.fr/bourse)
- **BoursoBank**: Decouverte EUR 1.99 to EUR 500 then 0.60%, PEA capped at 0.50%; US CTO 0.60%; 0.25% FX; no custody. [https://www.boursobank.com/aide-en-ligne/bourse/comment-investir-en-bourse/fonctionnement-de-la-bourse/question/comment-sont-calcules-les-frais-de-courtage-43063140](https://www.boursobank.com/aide-en-ligne/bourse/comment-investir-en-bourse/fonctionnement-de-la-bourse/question/comment-sont-calcules-les-frais-de-courtage-43063140)
- **DEGIRO**: CTO only, no PEA. EUR 2/order (1 EUR brokerage + 1 EUR handling) on Euronext and US; AutoFX is 0.25% in the 2026 tariff, but the study models 10 bps and reports a 25 bps sensitivity. [https://degiro.fr/data/pdf/fr/Tarifs.pdf](https://degiro.fr/data/pdf/fr/Tarifs.pdf)
- **IBKR PEA eligibility and pricing**: [interactivebrokers.ie PEA](https://www.interactivebrokers.ie/en/accounts/plan-depargne-en-action-accounts.php); [IBKR spot FX](https://www.interactivebrokers.com/en/pricing/commissions-spot-currencies.php).
- **Saxo PEA comparison**: [home.saxo PEA](https://www.home.saxo/fr-fr/accounts/pea) (Classic 0.08%, min €2, 0.25% FX; promo 0% on 70 stocks to 2026-12-31).
- **BoursoBank 2026 brochure**: [boursorama brochure](https://www.boursorama.com/content/brochure_tarifaire/boursorama_bt.pdf) (Découverte 1.99 € to 500 €, then 0.60%; PEA capped at 0.50%).
- **DEGIRO tariff**: [Tarifs.pdf](https://degiro.fr/data/pdf/fr/Tarifs.pdf) (2026-10-29: 1 € brokerage + 1 € handling; AutoFX 0.25%).
- **Fortuneo**: [fortuneo.fr/bourse](https://www.fortuneo.fr/bourse) (Starter 0.35%, first order ≤500 € free; US 0.20% min 20 € + 30 €).
- **XTB**: [xtb.com/fr/pea](https://www.xtb.com/fr/pea) (0% to 100k €/month, then 0.2%; 0.5% conversion).

## Reproduce

```
uv run python scripts/brokers.py
uv run python tests/test_brokers.py
uv run python tests/test_core.py
```

Generated by `uv run python scripts/brokers.py`.
