# PEA-eligible semiconductor/tech exposure vs the CTO core book

**Verdict: the CTO still wins.** As of 2026-11-26, no pure
semiconductor UCITS ETF is PEA-eligible. The best 1x listed PEA tech
fund, Amundi PEA Nasdaq-100 (PUST), turns €10k into **5.08x after
tax** over 2015-2026; the CTO 2.5x SMH book turns it into **38.80x**.
Even a PEA-eligible 2x Nasdaq ETF (LQQ) reaches only 14.42x. The PEA
wrapper is cheaper on tax (18.6% social vs 31.4% PFU) but it cannot
buy the US semiconductor index, and without a daily-reset 2x wrapper
it cannot match the CTO's leverage; that gap dominates.

## Eligibility evidence

The PEA rule is not 'UCITS = eligible'. A fund must invest **more than
75% of its assets in companies headquartered in the EU/EEA** (Code
monétaire et financier art. L221-31; AMF/BOFiP; economie.gouv.fr). A
physical ETF that holds Nvidia, TSMC and SK hynix fails that test. A
synthetic ETF can hold a >75% EU basket and swap the performance for a
non-EU index, which is how PUST and CW8 qualify.

Rule sources: [economie.gouv.fr](https://www.economie.gouv.fr/particuliers/plan-epargne-actions-pea),
[Legifrance L221-31](https://www.legifrance.gouv.fr/codes/id/LEGISCTA000006170310),
[BOFiP BOI-RPPM-RCM-40-50-20-20](https://bofip.impots.gouv.fr/doctrine/BOI-RPPM-RCM-40-50-20-20).

| ETF | ISIN | Domicile | Replication | PEA | Source |
|---|---|---|---|---|---|
| Amundi PEA Nasdaq-100 UCITS ETF Acc (PUST.PA) | FR0011871110 | France | synthetic (swap) | **yes** | [issuer/justETF](https://www.justetf.com/en-fr/etf-profile.html?isin=FR0011871110) |
| Amundi MSCI World Swap UCITS ETF EUR Acc (CW8.PA) | LU1681043599 | Luxembourg | synthetic (swap) | **yes** | [issuer/justETF](https://www.justetf.com/en-fr/etf-profile.html?isin=LU1681043599) |
| Amundi Nasdaq-100 Daily (2x) Leveraged UCITS ETF Acc (LQQ.PA) | FR0010342592 | France | synthetic (2x daily swap) | **yes** | [issuer/justETF](https://www.justetf.com/en-fr/etf-profile.html?isin=FR0010342592) |
| VanEck Semiconductor UCITS ETF (SMH.PA) | IE00BMC38736 | Ireland | physical | no | [issuer/justETF](https://www.justetf.com/en-fr/etf-profile.html?isin=IE00BMC38736) |
| iShares MSCI Global Semiconductors UCITS ETF (SEMI.PA) | IE000I8KRLL9 | Ireland | physical | no | [issuer/justETF](https://www.quantalys.com/Fonds/2012022) |
| Amundi MSCI Semiconductors UCITS ETF Acc (CHIP.PA) | LU1900066033 | Luxembourg | physical | no | [issuer/justETF](https://www.amundietf.lu/en/individual/products/equity/amundi-msci-semiconductors-ucits-etf-acc/lu1900066033) |

Key points:

- **VanEck Semiconductor UCITS (IE00BMC38736)**: Ireland, physical,
  justETF explicitly says 'Eligible for PEA: No'. Same for the iShares
  MSCI Global Semiconductors (IE000I8KRLL9) and Amundi MSCI
  Semiconductors (LU1900066033); Amundi's own product page says 'Tax
  data (PEA): No'. The swap-based Fineco AM MSCI World Semiconductors
  (IE000C87OS21) is not listed as PEA-eligible by justETF either;
  synthetic replication alone is not enough: the fund must hold the
  >75% EU basket.
- **PUST (FR0011871110)**: France, synthetic, TER 0.30%, PEA eligible,
  Yahoo history from 2014-05-20. The prospectus commits to >75% EU
  equities; the fund holds a European basket and swaps into the
  Nasdaq-100. It is a tech fund, **not a pure semiconductor fund**:
  NVDA, Micron and AMD are top holdings, but Apple/Microsoft/Amazon are
  there too.
- **CW8 (LU1681043599)**: Luxembourg, synthetic, TER 0.38%, PEA
  eligible, Yahoo history from 2009. Used as the passive World PEA
  benchmark.
- **LQQ (FR0010342592)**: France, synthetic, TER 0.60%, PEA eligible,
  2x daily Nasdaq-100. The PEA cannot borrow at account level, but this
  ETF embeds the leverage. Yahoo history from 2009 (cache from 2014).
  Checked separately because the base case is 1x.
- A European tech ETF such as Amundi STOXX Europe 600 Technology
  (TNO, LU1834988518) is also PEA eligible, but its Yahoo line starts
  only in 2024; the older iShares line (EXV3.DE) returned 2.46x after
  tax, below PUST and CW8. It is not the best PEA tech candidate.

## Method

All books are €10,000. PUST is 1x (the PEA cannot borrow at account
level), core-only, `core_mode='always'`, 200-day trend on the core with
a 50% cut, month-end decision, next-open fill, 5 bps spread. CW8 is a
passive buy-and-hold with the same entry/exit fees and 18.6% social, no
trend overlay. LQQ uses the same 200-day book on a 2x daily ETF; its
leverage is embedded, not margin. PEA rows use
`tax_country='fr_pea'`, HIFO lots and 18.6% social contributions on the
terminal gain; CTO rows use the French PFU schedule (31.4% in 2026).
Trade Republic charges €1/order, Bourse Direct the Euronext grid
(0.99/1.90/2.90/3.80 then 0.09%) plus 0.036%/yr custody on foreign
names; the bot charges that custody only on US lines, so the model
charges none of these EUR holdings (see caveat). No FTT: ETFs are not
French shares, and none of the five basket companies is
French-headquartered. PUST/CW8/LQQ are EUR-listed, so no FX.
Data: Yahoo adjusted closes, end 2026-11-25. PUST.PA, CW8.PA and
LQQ.PA were new cache entries; no existing cache file was refreshed.

The direct-stock basket is an equal-weight, month-end rebalanced
synthetic index of ASML, ASM International, Infineon, STMicroelectronics
and BESI. It is **indicative only**: the five names are chosen with
hindsight, the index charges one ticket per rebalance instead of five,
and the result is not a UCITS product. It answers the narrower
question: what did the best PEA-eligible European semis do?

## After-tax results

### 2015-2026 (start-date window)

| book | wrapper | broker | pre-tax | fees | FTT | tax paid | exit tax | **after tax** | CAGR | maxDD | vs CTO |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| CTO 2.5x SMH | CTO | Trade Republic | €572,212 | €31,815 | €0 | €50,569 | €184,183 | **38.80x** | 36.1% | -69% | 1.00x |
| PEA Nasdaq-100 (PUST) | PEA | Trade Republic | €60,137 | €468 | €0 | €0 | €9,325 | **5.08x** | 14.4% | -27% | 0.13x |
| World PEA (CW8) | PEA | Trade Republic | €34,869 | €24 | €0 | €0 | €4,626 | **3.02x** | 9.6% | -34% | 0.08x |
| PEA Nasdaq-100 (PUST) | PEA | Bourse Direct | €58,595 | €1,107 | €0 | €0 | €9,039 | **4.96x** | 14.2% | -27% | 0.13x |
| World PEA (CW8) | PEA | Bourse Direct | €34,810 | €63 | €0 | €0 | €4,615 | **3.02x** | 9.6% | -34% | 0.08x |
| EU semi basket (5 names) | PEA | Trade Republic | €172,264 | €1,035 | €0 | €0 | €30,181 | **14.21x** | 24.7% | -49% | 0.37x |
| PEA 2x Nasdaq (LQQ) | PEA | Trade Republic | €174,881 | €965 | €0 | €0 | €30,668 | **14.42x** | 24.7% | -58% | 0.37x |

### 2018-2026 (start-date window)

| book | wrapper | broker | pre-tax | fees | FTT | tax paid | exit tax | **after tax** | CAGR | maxDD | vs CTO |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| CTO 2.5x SMH | CTO | Trade Republic | €204,380 | €11,079 | €0 | €14,872 | €65,363 | **13.90x** | 34.5% | -69% | 1.00x |
| PEA Nasdaq-100 (PUST) | PEA | Trade Republic | €42,604 | €303 | €0 | €0 | €6,064 | **3.65x** | 15.4% | -27% | 0.26x |
| World PEA (CW8) | PEA | Trade Republic | €26,507 | €20 | €0 | €0 | €3,070 | **2.34x** | 9.9% | -34% | 0.17x |
| PEA Nasdaq-100 (PUST) | PEA | Bourse Direct | €41,797 | €702 | €0 | €0 | €5,914 | **3.59x** | 15.2% | -27% | 0.26x |
| World PEA (CW8) | PEA | Bourse Direct | €26,463 | €51 | €0 | €0 | €3,062 | **2.34x** | 9.9% | -34% | 0.17x |
| EU semi basket (5 names) | PEA | Trade Republic | €56,287 | €365 | €0 | €0 | €8,609 | **4.77x** | 19.0% | -49% | 0.34x |
| PEA 2x Nasdaq (LQQ) | PEA | Trade Republic | €70,843 | €408 | €0 | €0 | €11,317 | **5.95x** | 21.8% | -58% | 0.43x |

### 2022-2026 (start-date window)

| book | wrapper | broker | pre-tax | fees | FTT | tax paid | exit tax | **after tax** | CAGR | maxDD | vs CTO |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| CTO 2.5x SMH | CTO | Trade Republic | €71,029 | €3,416 | €0 | €2,543 | €21,915 | **4.91x** | 38.6% | -69% | 1.00x |
| PEA Nasdaq-100 (PUST) | PEA | Trade Republic | €19,610 | €128 | €0 | €0 | €1,788 | **1.78x** | 12.3% | -25% | 0.36x |
| World PEA (CW8) | PEA | Trade Republic | €15,234 | €15 | €0 | €0 | €974 | **1.43x** | 7.4% | -22% | 0.29x |
| PEA Nasdaq-100 (PUST) | PEA | Bourse Direct | €19,371 | €279 | €0 | €0 | €1,743 | **1.76x** | 12.1% | -25% | 0.36x |
| World PEA (CW8) | PEA | Bourse Direct | €15,210 | €35 | €0 | €0 | €969 | **1.42x** | 7.4% | -22% | 0.29x |
| EU semi basket (5 names) | PEA | Trade Republic | €19,652 | €127 | €0 | €0 | €1,795 | **1.79x** | 12.4% | -49% | 0.36x |
| PEA 2x Nasdaq (LQQ) | PEA | Trade Republic | €22,760 | €129 | €0 | €0 | €2,373 | **2.04x** | 15.4% | -47% | 0.42x |

## Verdict

1. **The CTO is still the right wrapper for this exposure.** Full
window: CTO **38.80x** vs PUST **5.08x**;
2018 start: 13.90x vs 3.65x; 2022
start: 4.91x vs 1.78x. The PEA saves
~12.8 pp of tax on the gain, but it gives up the US semiconductor
index and, at account level, leverage. At 1x, PUST's 14.4% after-tax
CAGR cannot catch the CTO's 36.1%, even with a much smaller -27%
maxDD vs -69%.

2. **PUST beats the World PEA, but not by enough to change the
answer.** 5.08x vs CW8 3.02x, with a
lower drawdown (-27% vs -34%). If the money is already trapped in a
PEA and the choice is Nasdaq-100 vs World, PUST is the better
satellite. It is not a substitute for the CTO core.

3. **Even the PEA's embedded-leverage loophole does not close the
gap.** LQQ, a PEA-eligible 2x daily Nasdaq-100 ETF, ends at
**14.42x** after tax with a -58% maxDD.
That is better than 1x PUST but still 2.7x behind the CTO, and the
daily-reset product is explicitly a short-horizon instrument (the KID
recommends a 1-day holding period). Its leverage cost and volatility
drag are already inside the NAV.

4. **Even the hindsight European semi basket does not close the gap.**
The five-name equal-weight basket ends at **14.21x**
after tax, with a -49% maxDD. That is 2.7x behind the CTO, and it
required picking ASML, ASM, Infineon, STM and BESI today. A rules-based
European semi book would not have been this clean.

5. **The PEA is a wrapper for European cash equities and low-turnover
   European ETFs, not for this bot's high-beta core.** Use it for
   European dividend/quality exposure or a small PUST satellite; keep
   the 2.5x SMH core in the CTO. The tax saving is real, but the
   exposure/leverage gap is much larger.

## Caveats

- **Survivorship**: the direct basket and the country universes are
  static lists of today's large caps. The basket is the most biased
  number in this report.
- **PUST is synthetic**: BNP Paribas is the swap counterparty. UCITS
  caps counterparty exposure, but it is not zero. PUST also has a
  0.30% TER, already inside the NAV.
- **LQQ is a daily-reset 2x product**, not a long-term 2x index. Its KID
  assumes a 1-day holding period; path-dependent decay can hurt a
  multi-year holder even when the index rises. The backtest uses the
  actual NAV path, so the drag is in the result, but the product is a
  risk-management problem, not a free leverage switch.
- **PEA 5-year clock**: the model assumes a withdrawal after 5 years.
  The 2022-2026 window is only 4.9 years; a fresh PEA closed at the end
  would pay the 12.8% IR as well. Treat that row as a sub-period of an
  existing PEA, not a standalone 2022 PEA.
- **Tax model**: social contributions are charged on the terminal
  portfolio gain, not on each trade. The €150k PEA contribution cap is
  not binding at €10k.
- **Dividends/withholding**: accumulating NAVs are used; no separate
  dividend withholding is modelled. PUST's swap receives the Nasdaq
  net total return, so the fund-level withholding is already in the
  index.
- **Execution**: 5 bps spread, no market impact, no borrow squeeze. The
  CTO 2.5x book can be forced to deleverage in a gap; the leverage band
  is a simplification.
- **Bourse Direct custody**: the bot charges 0.036%/yr only on US lines.
  CW8 is Luxembourg-domiciled, so a real Bourse Direct PEA may add about
  €80 over the window; PUST and LQQ are French. The ranking does not
  change.
- **Basket costs**: the synthetic index is charged one ticket per
  rebalance. Five real stock orders add roughly €0.2k at Trade Republic
  over the window (<0.02x), more at Bourse Direct; the ranking does not
  change.

## Reproduce

```
uv run python scripts/pea_ucits.py
uv run python tests/test_pea.py
uv run python tests/test_core.py
```

Generated by `uv run python scripts/pea_ucits.py`.
