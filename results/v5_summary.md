# v5 summary — wrapper, estate, PEA and income findings

Date: 2026-11-26. Built on the merged v2–v4 stack. All studies are paper/research
only; no real orders.

## Estate tax: treaty is the live protection, not a wrapper

At €660k / 2.5x, a 100% US-situs CTO holds roughly **$725k net US-situs**
(gross $1.81M). The France-US estate treaty's pro-rata credit removes the US
estate tax in the worked examples ($0 vs $226k without the treaty). French
droits de succession remain (~€111k for one child). UCITS/PEA/assurance-vie
remove US situs but not French inheritance tax.

The treaty is not automatic: it needs 706-NA/8833 filings, France domicile, and
a decedent who is not a US citizen/green-card holder. Keep US-situs at/below
$60k only as treaty-failure insurance; spouse planning needs QDOT/treaty advice.
Details: `docs/estate_tax.md`, `results/estate_tax.md`.

## UCITS core: reject as replacement

Common window 2021-08..2026-11, US monthly 2.5x, same signal:

| core | EUR ledger | CAGR | maxDD |
|---|---:|---:|---:|
| SMH (US) | 7.11x | 44.8% | -54.9% |
| SMH.L (IE, acc) | 2.86x | 22.0% | -73.9% |
| SEMI.AS (IE, acc) | 3.25x | 24.9% | -66.1% |

The Irish accumulating structure has real dividend/estate advantages, but the
return gap is far larger (40-46% retention). Keep US SMH; treat UCITS as a
paper/estate-planning overlay only. `results/ucits_core.md`.

## PEA: no pure semiconductor fund

SMH.PA, SEMI.PA and CHIP.PA are not PEA-eligible (checked 2026-11-26). PEA
candidates are broad: PUST.PA (Nasdaq-100) 5.77x after social vs CTO US monthly
66.17x; LQQ.PA 2x Nasdaq 13.73x with -61.2% maxDD. PEA is not a substitute for
the semis core. `results/pea_semis.md`.

## Covered calls: model-only income opt-in

No option quotes. Black-Scholes, monthly 2/5/10% OTM, IV 25/30/40%, European
cash settlement, fractional contracts. At 40% IV and 10% OTM the overlay looks
good (US book 75.96x vs the report's 66.72x gross-dividend baseline; the exact dividend-aware baseline is 66.17x, maxDD -48.5% vs -56.9%, income €91k); at 25/30%
IV most variants have negative option P&L. It is a `paper` income opt-in, not a
default: the model is optimistic about assignment, contract size and the
volatility smile. `results/covered_calls.md`.

## Net recommendation

- Keep **US monthly 2.5x CTO** as the growth default.
- Keep **balanced 70/30** as the lower-drawdown opt-in.
- Do not switch to UCITS or PEA for return reasons.
- Handle US estate tax with treaty filings/beneficiary planning, not by
  sacrificing the core return.
- Covered calls only as a paper income experiment.

## Tests

All `tests/test_*.py` pass on master, including `test_estate`, `test_pea_semis`,
`test_ucits`, `test_covered_calls`.
