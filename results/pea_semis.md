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

Re-run on 2026-11-26 with the cached bars through 2026-11-25. The CTO comparator
is the **US monthly 2.5x SMH EUR ledger**, not the older TECH/Trade Republic
headline.

## Eligibility re-check

| ticker | fund | ISIN | domicile | replication | index | PEA | source (checked 2026-11-26) |
|---|---|---|---|---|---|---|---|
| PUST.PA | Amundi PEA Nasdaq-100 | FR0011871110 | France | synthetic | Nasdaq-100 | **yes** | justETF/Amundi |
| PSP5.PA | Amundi PEA S&P 500 | FR0011871128 | France | synthetic | S&P 500 | **yes** | justETF/Amundi |
| PE500.PA | Amundi PEA S&P 500 Screened | FR0013412285 | France | synthetic | S&P 500 Scored & Screened+ | **yes** | justETF/Amundi |
| CW8.PA | Amundi MSCI World Swap | LU1681043599 | Luxembourg | synthetic | MSCI World | **yes** | justETF/Amundi |
| LQQ.PA | Amundi Nasdaq-100 Daily 2x | FR0010342592 | France | synthetic 2x daily | Nasdaq-100 2x | **yes** | justETF/Amundi |
| TNO.PA | Amundi STOXX Europe 600 Technology | LU1834988518 | Luxembourg | physical | STOXX Europe 600 Tech | **yes** | justETF |
| SMH.PA | VanEck Semiconductor | IE00BMC38736 | Ireland | physical | MVIS US Listed Semiconductor | **no** | justETF |
| SEMI.PA | iShares MSCI Global Semiconductors | IE000I8KRLL9 | Ireland | physical | MSCI ACWI IMI Semiconductors | **no** | justETF |
| CHIP.PA | Amundi MSCI Semiconductors | LU1900066033 | Luxembourg | physical | MSCI ACWI Semiconductors | **no** | justETF/Amundi |

**No pure semiconductor UCITS ETF is PEA-eligible.** The three sector-pure funds
are Irish/Luxembourg physical funds holding US/Taiwan/Korea names; they fail the
>75% EU/EEA test. TNO is PEA-eligible and European-tech, but its Yahoo history
starts in 2024-04, too short for a 12-1 momentum test; it is eligibility-only.

## After-tax results

All PEA rows are 1x EUR, Trade Republic €1/order unless marked, 5 bps spread,
18.6% social on the terminal gain, no income tax. The 2022-2026 rows are a
sub-period of a seasoned PEA; a fresh PEA closed before 5 years would also pay
12.8% IR.

### 2015-2026

| book | wrapper | variant | after tax | CAGR | maxDD | 2018 | 2022 | vs CTO | fees | exit tax | trades |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| CTO US monthly 2.5x | CTO | CTO US monthly | **66.17x** | 42.3% | -56.9% | -31.1% | -33.7% | 1.00x | €23,971 | €210,493 | 1692 |
| PUST.PA Amundi PEA Nasdaq-100 | PEA | buy & hold | **5.77x** | 15.6% | -31.4% | +3.7% | -29.9% | 0.09x | €41 | €10,891 | 2 |
| PUST.PA Amundi PEA Nasdaq-100 | PEA | monthly momentum 12-1 | **4.84x** | 13.9% | -28.2% | +3.7% | -24.0% | 0.07x | €82 | €8,779 | 9 |
| PSP5.PA Amundi PEA S&P 500 | PEA | buy & hold | **3.81x** | 11.7% | -33.7% | -0.1% | -14.2% | 0.06x | €29 | €6,420 | 2 |
| PSP5.PA Amundi PEA S&P 500 | PEA | monthly momentum 12-1 | **3.03x** | 9.6% | -33.7% | -5.8% | -5.0% | 0.05x | €110 | €4,635 | 13 |
| CW8.PA World PEA | PEA | buy & hold | **3.02x** | 9.6% | -33.6% | -3.9% | -13.9% | 0.05x | €24 | €4,626 | 2 |
| CW8.PA World PEA | PEA | monthly momentum 12-1 | **2.02x** | 6.0% | -33.6% | -7.9% | -10.4% | 0.03x | €144 | €2,336 | 21 |
| LQQ.PA PEA 2x Nasdaq (control) | PEA | buy & hold | **13.73x** | 24.2% | -61.2% | -5.7% | -59.3% | 0.21x | €90 | €29,089 | 2 |
| LQQ.PA PEA 2x Nasdaq (control) | PEA | monthly momentum 12-1 | **6.29x** | 16.4% | -52.2% | -5.7% | -41.2% | 0.10x | €272 | €12,089 | 21 |
| PUST.PA PEA Nasdaq-100 | PEA | buy & hold, 2x cost | **5.75x** | 15.6% | -31.4% | +3.7% | -29.9% | 0.09x | €161 | €10,852 | 2 |
| PUST.PA PEA Nasdaq-100 | PEA | monthly momentum, 2x cost | **4.82x** | 13.9% | -28.2% | +3.7% | -24.1% | 0.07x | €163 | €8,724 | 9 |
| PUST.PA PEA Nasdaq-100 (Bourse Direct) | PEA | buy & hold | **5.76x** | 15.6% | -31.4% | +3.7% | -29.9% | 0.09x | €110 | €10,870 | 2 |

### 2019-2026 (PE500 common window)

| book | wrapper | variant | after tax | CAGR | maxDD | 2022 | vs CTO | fees | exit tax |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| CTO US monthly 2.5x | CTO | CTO US monthly | **26.41x** | 54.1% | -56.5% | -32.8% | 1.00x | €9,419 | €83,478 |
| PUST.PA Amundi PEA Nasdaq-100 | PEA | buy & hold | **2.90x** | 14.8% | -31.4% | -29.9% | 0.11x | €24 | €4,336 |
| PUST.PA Amundi PEA Nasdaq-100 | PEA | monthly momentum | **2.46x** | 12.4% | -28.2% | -24.0% | 0.09x | €24 | €3,347 |
| PSP5.PA Amundi PEA S&P 500 | PEA | buy & hold | **2.36x** | 11.8% | -33.7% | -14.2% | 0.09x | €20 | €3,104 |
| PSP5.PA Amundi PEA S&P 500 | PEA | monthly momentum | **2.17x** | 10.5% | -33.7% | -5.0% | 0.08x | €52 | €2,671 |
| PE500.PA Amundi PEA S&P 500 Screened | PEA | buy & hold | **2.28x** | 11.3% | -33.6% | -14.3% | 0.09x | €20 | €2,930 |
| PE500.PA Amundi PEA S&P 500 Screened | PEA | monthly momentum | **1.99x** | 9.3% | -33.6% | -4.9% | 0.08x | €73 | €2,258 |
| CW8.PA World PEA | PEA | buy & hold | **2.09x** | 10.0% | -33.6% | -13.9% | 0.08x | €19 | €2,486 |
| CW8.PA World PEA | PEA | monthly momentum | **1.86x** | 8.4% | -33.6% | -10.4% | 0.07x | €88 | €1,965 |
| LQQ.PA PEA 2x Nasdaq (control) | PEA | buy & hold | **4.45x** | 21.3% | -61.2% | -59.3% | 0.17x | €33 | €7,886 |
| LQQ.PA PEA 2x Nasdaq (control) | PEA | monthly momentum | **2.64x** | 13.4% | -52.2% | -41.2% | 0.10x | €79 | €3,751 |

### 2022-2026

| book | wrapper | variant | after tax | CAGR | maxDD | 2022 | vs CTO | fees | exit tax |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| CTO US monthly 2.5x | CTO | CTO US monthly | **7.53x** | 51.2% | -56.0% | -7.3% | 1.00x | €2,798 | €23,592 |
| PUST.PA Amundi PEA Nasdaq-100 | PEA | buy & hold | **1.48x** | 8.2% | -30.3% | -29.9% | 0.20x | €15 | €1,098 |
| PUST.PA Amundi PEA Nasdaq-100 | PEA | monthly momentum | **1.36x** | 6.4% | -26.8% | -16.8% | 0.18x | €16 | €821 |
| PSP5.PA Amundi PEA S&P 500 | PEA | buy & hold | **1.48x** | 8.2% | -23.2% | -14.2% | 0.20x | €15 | €1,100 |
| PSP5.PA Amundi PEA S&P 500 | PEA | monthly momentum | **1.50x** | 8.5% | -23.2% | -0.2% | 0.20x | €30 | €1,138 |
| PE500.PA Amundi PEA S&P 500 Screened | PEA | buy & hold | **1.43x** | 7.5% | -23.7% | -14.3% | 0.19x | €15 | €990 |
| PE500.PA Amundi PEA S&P 500 Screened | PEA | monthly momentum | **1.38x** | 6.7% | -23.7% | -0.1% | 0.18x | €45 | €865 |
| CW8.PA World PEA | PEA | buy & hold | **1.43x** | 7.4% | -21.7% | -13.9% | 0.19x | €15 | €974 |
| CW8.PA World PEA | PEA | monthly momentum | **1.40x** | 6.9% | -21.7% | -6.2% | 0.19x | €41 | €907 |
| LQQ.PA PEA 2x Nasdaq (control) | PEA | buy & hold | **1.38x** | 6.7% | -59.9% | -59.3% | 0.18x | €14 | €874 |
| LQQ.PA PEA 2x Nasdaq (control) | PEA | monthly momentum | **1.21x** | 3.9% | -44.3% | -28.6% | 0.16x | €28 | €476 |

## Verdict

**Reject the PEA semiconductor substitute. Keep the CTO US monthly book for this
exposure.**

1. **No pure PEA semiconductor fund exists among the checked candidates.** SMH,
   SEMI and CHIP are not PEA-eligible; they are physical Irish/Luxembourg funds
   holding US/Taiwan/Korea semis. A synthetic wrapper does not automatically
   solve this: the fund still needs the >75% EU/EEA basket. Forcing SMH or CHIP
   into a PEA is not possible; forcing a European tech proxy is a different
   exposure, not a semiconductor substitute.

2. **The best PEA tech fund does not come close.** PUST buy-and-hold is
   **5.77x** full window vs CTO **66.17x** (8.7%); 2.90x vs 26.41x from 2019
   (11.0%); 1.48x vs 7.53x from 2022 (19.7%). PUST has a much lower maxDD
   (-31.4% vs -56.9%), but its terminal is below the 25% lower-drawdown floor
   (25% of 66.17x = 16.54x). The PEA saves 12.8 pp of tax on the gain; the
   missing US semiconductor index and account-level leverage cost far more.

3. **The 2x PEA Nasdaq control still loses.** LQQ is 13.73x full window, 20.8%
   of the CTO, with a **-61.2%** maxDD. It is a daily-reset product with a
   1-day KID horizon; it is not a long-term substitute.

4. **PSP5/PE500/CW8 are broad-market satellites, not substitutes.** PSP5 3.81x,
   CW8 3.02x, PE500 2.28x (2019 window), all with -33% to -34% maxDD. PUST beats
   the World PEA, but the whole PEA group is 3-22% of the CTO terminal.

5. **Monthly 12-1 momentum did not help.** It reduced PUST from 5.77x to 4.84x,
   PSP5 from 3.81x to 3.03x, CW8 from 3.02x to 2.02x and LQQ from 13.73x to
   6.29x. Only PSP5 in the 2022 window improved marginally (1.50x vs 1.48x) and
   its 2022 drawdown was better; not enough to change the verdict. Buy-and-hold
   is the better PEA behavior in this sample.

6. **Costs and broker choice do not change the answer.** PUST 2x cost 5.75x vs
   5.77x; Bourse Direct 5.76x vs Trade Republic 5.77x. The fee drag is small for
   a buy-and-hold PEA; the exposure gap dominates.

**Use the PEA for European cash equities / low-turnover European ETFs or a small
PUST satellite if the money is already trapped there.** Do not use it as a
substitute for the 2.5x SMH core.

## Caveats

- PUST/PSP5/PE500/CW8 are synthetic; the swap counterparty is a real risk, capped
  by UCITS rules but not zero.
- PEA tax assumes a seasoned (>5 year) plan and 18.6% social contributions on the
  terminal gain. The 2022-2026 window is 4.9 years; a fresh plan closed then
  would also pay 12.8% IR.
- LQQ is a daily-reset 2x product, not a long-term 2x index; path-dependent decay
  and the 1-day KID horizon are real.
- Dividend withholding is not modelled separately; accumulating NAVs are used.
  PUST's swap receives the Nasdaq net total return, so fund-level withholding is
  inside the index.
- TNO is PEA-eligible but its Yahoo history starts in 2024-04; it is excluded
  from the momentum comparison rather than forced into a 2-year result.
- The eligibility claims are a point-in-time check (2026-11-26) from issuer and
  justETF pages; re-check before trading. No new full cache refresh was run.

## Reproduce

```bash
uv run python scripts/pea_semis.py
uv run python tests/test_pea_semis.py
uv run python tests/test_pea.py
uv run python tests/test_core.py
```
