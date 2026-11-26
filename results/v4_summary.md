# v4 summary — robustness, tax, FX and forward operations

Date: 2026-11-26. Built on the merged v2/v3 stack. All v4 studies are paper or
research only; no real orders.

## Merged

| workstream | issue | branch | PR | merge commit |
|---|---|---|---|---|
| PIT Nasdaq-100 universe | #35 | `v4/pit-tech` | #41 | `19d9d03` |
| Tax-loss harvesting | #36 | `v4/tax-harvest` | #42 | `034fa2b` |
| Forward paper loop | #37 | `v4/forward` | #39 | `c5004a6` |
| EUR/USD hedge | #38 | `v4/fx-hedge` | #40 | `9a5843d` |

## PIT Nasdaq-100: sleeve survives, US still primary

| book | terminal-FX | EUR ledger + div | CAGR | maxDD |
|---|---:|---:|---:|---:|
| Static 49 shipped | 57.01x | **52.98x** | 39.7% | -65.6% |
| PIT Nasdaq-100 | 55.14x | **50.29x** | 39.1% | -64.0% |
| PIT core-only | 45.33x | 42.57x | 37.1% | -64.8% |
| US all-PIT | 70.86x | **66.17x** | 42.3% | -56.9% |

PIT retains 95% of the static EUR-ledger terminal; the sleeve is not mainly a
static-list survivorship artifact. But the Nasdaq-100 proxy is broad (not
tech-only), only 59% of its 266 tickers have cached bars, and rolling 5-year
premiums are weak. Verdict: `paper` robustness check; US remains primary; TECH
stays forward-validation only.

## Tax-loss harvesting: reject

Baseline 66.1655x. All harvest variants (5/10/20% loss thresholds, re-entry or
cash, 0/1/2% bands, 1x/2x costs) lose to the baseline. The gain is mainly tax
deferral: the rebought lot has a lower basis and the benefit returns as a larger
exit tax; each round trip also pays fees. Verdict: **reject**, keep off.

## EUR/USD hedge: reject

| US hedge | after-tax | maxDD | 2022 | p5 |
|---|---:|---:|---:|---:|
| 0% | **66.17x** | **-56.9%** | -33.7% | **2.53x** |
| 25% | 60.97x | -58.7% | -37.0% | 2.18x |
| 50% | 55.77x | -60.5% | -40.4% | 1.86x |
| 75% | 50.58x | -63.5% | -44.1% | 1.50x |
| 100% | 45.38x | -67.0% | -48.0% | 1.10x |

USD acted as a risk-off cushion in 2022; hedging removed it and added carry/roll
cost. Even zero carry leaves a full hedge below the unhedged path. Verdict:
**reject**, keep the unhedged EUR path. TECH/balanced full hedges are worse.

## Forward paper loop

`scripts/forward_daily.py --paper --tax` runs `monthly/US`, `monthly/TECH` and
`tax/TECH`, refreshes only stale bars, reconciles, writes
`results/forward_status.md`, and fails closed on holidays/stale data. A weekday
automation runs at 21:45 UTC.

Current gate on 2026-11-25: US 0/5, TECH 1/5. No row was fabricated; a US
preview row remains `close_valid=False`.

## Tests

All `tests/test_*.py` pass on master, including
`test_pit_universe`, `test_fx_hedge`, `test_tax_harvest`, `test_forward`,
`test_eur_ledger`, `test_live`, `test_ops`, `test_ibkr`.

## Remaining risks

- Forward gate is still 0/5 US, 1/5 TECH; no forward edge is proven.
- PIT data misses 110/266 tickers, mostly delisted/acquired losers: upper bound.
- Tax/FX studies are negative results; they are kept so they are not repeated.
- Automation relies on the environment staying alive; `ops.py` monitors state but
  alert delivery needs operator secrets.
