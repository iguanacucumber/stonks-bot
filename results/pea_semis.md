# PEA semiconductor/tech alternatives: pre-registered

Branch `v5/pea`, based on master `034fa2b`. Written before the 2026 re-run.

## Question

Is there a PEA-eligible semiconductor or tech UCITS ETF that is a credible
long-term substitute for the CTO US monthly book? The comparison is the current
CTO US monthly EUR ledger (**66.17x** full window), not the older TECH/Trade
Republic 38.80x number.

## Eligibility rule

A fund must invest **more than 75% of its assets in EU/EEA-headquartered
companies** to be PEA-eligible (Code monétaire et financier art. L221-31;
AMF/BOFiP). A physical ETF holding Nvidia/TSMC/SK hynix fails. A synthetic ETF
can hold a >75% EU basket and swap the performance for a non-EU index.

Eligibility is checked against the issuer/justETF page on **2026-11-26**. A
`pea=yes` claim without a source/date is not enough.

## Candidates

| ticker | fund | ISIN | domicile | replication | index | PEA | source |
|---|---|---|---|---|---|---|---|
| PUST.PA | Amundi PEA Nasdaq-100 UCITS ETF Acc | FR0011871110 | France | synthetic | Nasdaq-100 | yes | justETF/Amundi 2026-11-26 |
| PSP5.PA | Amundi PEA S&P 500 UCITS ETF Acc | FR0011871128 | France | synthetic | S&P 500 | yes | justETF/Amundi 2026-11-26 |
| PE500.PA | Amundi PEA S&P 500 Screened UCITS ETF Acc | FR0013412285 | France | synthetic | S&P 500 Scored & Screened+ | yes | justETF/Amundi 2026-11-26 |
| CW8.PA | Amundi MSCI World Swap UCITS ETF EUR Acc | LU1681043599 | Luxembourg | synthetic | MSCI World | yes | justETF/Amundi 2026-11-26 |
| LQQ.PA | Amundi Nasdaq-100 Daily (2x) Leveraged UCITS ETF Acc | FR0010342592 | France | synthetic 2x daily | Nasdaq-100 2x | yes | justETF/Amundi 2026-11-26 |
| TNO.PA | Amundi STOXX Europe 600 Technology UCITS ETF Acc | LU1834988518 | Luxembourg | physical | STOXX Europe 600 Tech | yes | justETF 2026-11-26 |
| SMH.PA | VanEck Semiconductor UCITS ETF | IE00BMC38736 | Ireland | physical | MVIS US Listed Semiconductor 10% Capped | **no** | justETF 2026-11-26 |
| SEMI.PA | iShares MSCI Global Semiconductors UCITS ETF | IE000I8KRLL9 | Ireland | physical | MSCI ACWI IMI Semiconductors | **no** | justETF 2026-11-26 |
| CHIP.PA | Amundi MSCI Semiconductors UCITS ETF Acc | LU1900066033 | Luxembourg | physical | MSCI ACWI Semiconductors | **no** | justETF/Amundi 2026-11-26 |

TNO has Yahoo history only from 2024-04; it is listed for completeness but is
too short for a 12-1 monthly momentum test. The pure semiconductor funds are
not PEA-eligible, so no pure PEA semis product exists among the checked
candidates.

## Method

- PEA is **1x EUR**, no account-level leverage, no US core. LQQ is shown only as
  an embedded-leverage control; its KID is a 1-day product.
- PEA tax: 18.6% social contributions on the terminal gain, assuming the PEA is
  more than 5 years old (2026 rate). No income tax. No dividend withholding is
  modelled separately; accumulating NAVs are used.
- Buy-and-hold: one entry and one exit ticket, 5 bps spread, Trade Republic
  €1/order. Bourse Direct sensitivity uses its Euronext grid.
- Monthly momentum: at each month-end close, hold the ETF if its 12-1 total
  return is positive (`P[t-21] / P[t-252] - 1 > 0`), otherwise move to cash at
  the next open. No look-ahead; no daily trend overlay.
- CTO comparator: US monthly 2.5x SMH, 500 bps financing, daily 200-day SMH cut,
  IBKR CTO, EUR ledger with cached dividends. Same start/end window as each PEA
  row.
- Windows: 2015-2026 for PUST/PSP5/CW8/LQQ; 2019-04-25..2026 for PE500 (its
  Yahoo start); 2022-2026 for all. A 2022 start is a sub-period of a seasoned
  PEA, not a fresh 5-year PEA.
- Fees: 5 bps spread, Trade Republic €1/order; Bourse Direct sensitivity.
  PEA has no FTT on these ETFs.
- 2x-cost sensitivity: double spread and tickets on the best PEA candidate.

## Acceptance

A PEA candidate is a credible substitute only if:

1. It is actually PEA-eligible with a source/date.
2. Same-window after-tax multiple is at least 50% of the CTO US monthly EUR
   ledger, or it delivers a materially lower maxDD with at least 25% of the CTO
   terminal.
3. It beats the World PEA (CW8) after tax in the same window.
4. The result is not driven by one year; 2018/2022 and the 5-year windows are
   reported.
5. At 2x costs it remains above the 25% terminal floor.

If no **pure semiconductor** PEA fund exists, the mission verdict is **reject**
and the report must say so explicitly. A broad Nasdaq/S&P PEA fund can be a
satellite, not a substitute for the US semis core.

## Trials

- 5 PEA candidates × buy-and-hold and monthly momentum × 3 windows, where data
  allows.
- LQQ is a control, not a recommendation.
- TNO is eligibility-only (data too short).
- Every row is reported; no post-hoc selection.

---

# Results

(To be appended after the runs.)
