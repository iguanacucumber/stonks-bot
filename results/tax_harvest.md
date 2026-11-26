# Tax-loss harvesting on the US monthly 2.5x book

**Pre-registration (written before the sweep).** France has no wash-sale
ban: a losing EUR lot may be sold and immediately rebought. The hypothesis
is that realising losses at month-end offsets other capital gains and
raises after-tax EUR wealth after paying the extra round-trip fees.
The strategy is an overlay on the shipped US monthly book, not a new signal.

Grid: baseline; loss thresholds **-5% / -10% / -20%** measured against the
EUR HIFO lot basis; **immediate re-entry or cash**; minimum harvested
notional **0% / 1% / 2% of equity**; all fees at **1x and 2x**. Per-date
harvest notional is capped at **50% of equity**; names the post-rebalance
target no longer wants are not harvested. Decision at the month-end close,
fill at the next open; the callback never sees a future price.

Acceptance: a variant is opt-in only if **at both cost levels** it beats
the baseline on EUR after-tax multiple, 5-year median and worst window,
2018, 2022 and bootstrap p5. Otherwise reject and keep the report.

## Baseline reproduction

| metric | value |
|---|---:|
| EUR after-tax multiple | **66.1655x** |
| CAGR | 42.3% |
| maxDD | -56.9% |
| tax paid | €131,989 |
| exit tax | €210,493 |
| fees | €23,971 |
| dividend tax | €4,554 |
| 2018 / 2022 | -31.1% / -33.7% |
| 5y median / worst | 5.42x / 1.30x |
| bootstrap p5 | 3.56x |

Baseline is the merged EUR ledger with cached dividend events; it reproduces
**66.1655x** within 0.01x, and the script exits non-zero otherwise.

## All variants — 1x costs

| threshold | re-entry | band | multiple | CAGR | maxDD | tax paid | exit | fees | harvest names | harvest turnover | 2018 | 2022 | 5y median | 5y worst | boot p5 | vs base |
|---:|:---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 5% | yes | 0% | 66.134x | 42.3% | -57.0% | €131,870 | €210,493 | €24,366 | 17 | 0.25x | -31.2% | -33.8% | 5.42x | 1.30x | 3.55x | -0.032x |
| 5% | yes | 1% | 66.134x | 42.3% | -57.0% | €131,870 | €210,493 | €24,366 | 17 | 0.25x | -31.2% | -33.8% | 5.42x | 1.30x | 3.55x | -0.032x |
| 5% | yes | 2% | 66.134x | 42.3% | -57.0% | €131,870 | €210,493 | €24,366 | 17 | 0.25x | -31.2% | -33.8% | 5.42x | 1.30x | 3.55x | -0.032x |
| 5% | no | 0% | 66.057x | 42.3% | -56.1% | €131,315 | €210,493 | €24,168 | 17 | 0.12x | -31.6% | -36.8% | 5.30x | 1.25x | 3.47x | -0.109x |
| 5% | no | 1% | 66.057x | 42.3% | -56.1% | €131,315 | €210,493 | €24,168 | 17 | 0.12x | -31.6% | -36.8% | 5.30x | 1.25x | 3.47x | -0.109x |
| 5% | no | 2% | 66.057x | 42.3% | -56.1% | €131,315 | €210,493 | €24,168 | 17 | 0.12x | -31.6% | -36.8% | 5.30x | 1.25x | 3.47x | -0.109x |
| 10% | yes | 0% | 66.149x | 42.3% | -56.9% | €131,928 | €210,493 | €24,174 | 10 | 0.13x | -31.2% | -33.8% | 5.42x | 1.30x | 3.55x | -0.016x |
| 10% | yes | 1% | 66.149x | 42.3% | -56.9% | €131,928 | €210,493 | €24,174 | 10 | 0.13x | -31.2% | -33.8% | 5.42x | 1.30x | 3.55x | -0.016x |
| 10% | yes | 2% | 66.149x | 42.3% | -56.9% | €131,928 | €210,493 | €24,174 | 10 | 0.13x | -31.2% | -33.8% | 5.42x | 1.30x | 3.55x | -0.016x |
| 10% | no | 0% | 65.731x | 42.2% | -56.9% | €130,176 | €210,493 | €24,072 | 10 | 0.06x | -32.4% | -36.5% | 5.32x | 1.27x | 3.47x | -0.434x |
| 10% | no | 1% | 65.731x | 42.2% | -56.9% | €130,176 | €210,493 | €24,072 | 10 | 0.06x | -32.4% | -36.5% | 5.32x | 1.27x | 3.47x | -0.434x |
| 10% | no | 2% | 65.731x | 42.2% | -56.9% | €130,176 | €210,493 | €24,072 | 10 | 0.06x | -32.4% | -36.5% | 5.32x | 1.27x | 3.47x | -0.434x |
| 20% | yes | 0% | 66.162x | 42.3% | -56.9% | €131,973 | €210,493 | €24,023 | 3 | 0.03x | -31.1% | -33.7% | 5.42x | 1.30x | 3.56x | -0.004x |
| 20% | yes | 1% | 66.162x | 42.3% | -56.9% | €131,973 | €210,493 | €24,023 | 3 | 0.03x | -31.1% | -33.7% | 5.42x | 1.30x | 3.56x | -0.004x |
| 20% | yes | 2% | 66.162x | 42.3% | -56.9% | €131,973 | €210,493 | €24,023 | 3 | 0.03x | -31.1% | -33.7% | 5.42x | 1.30x | 3.56x | -0.004x |
| 20% | no | 0% | 66.040x | 42.3% | -56.5% | €131,546 | €210,493 | €23,997 | 3 | 0.02x | -31.0% | -35.4% | 5.41x | 1.29x | 3.51x | -0.125x |
| 20% | no | 1% | 66.040x | 42.3% | -56.5% | €131,546 | €210,493 | €23,997 | 3 | 0.02x | -31.0% | -35.4% | 5.41x | 1.29x | 3.51x | -0.125x |
| 20% | no | 2% | 66.040x | 42.3% | -56.5% | €131,546 | €210,493 | €23,997 | 3 | 0.02x | -31.0% | -35.4% | 5.41x | 1.29x | 3.51x | -0.125x |

## All variants — 2x costs

| threshold | re-entry | band | multiple | CAGR | maxDD | tax paid | exit | fees | harvest names | harvest turnover | 2018 | 2022 | 5y median | 5y worst | boot p5 | vs base |
|---:|:---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 5% | yes | 0% | 64.203x | 42.0% | -59.5% | €127,256 | €207,651 | €48,741 | 19 | 0.25x | -35.3% | -35.9% | 5.51x | 1.21x | 3.00x | -0.065x |
| 5% | yes | 1% | 64.204x | 42.0% | -59.5% | €127,259 | €207,651 | €48,732 | 17 | 0.25x | -35.3% | -35.9% | 5.51x | 1.22x | 3.00x | -0.064x |
| 5% | yes | 2% | 64.204x | 42.0% | -59.5% | €127,259 | €207,651 | €48,732 | 17 | 0.25x | -35.3% | -35.9% | 5.51x | 1.22x | 3.00x | -0.064x |
| 5% | no | 0% | 64.162x | 42.0% | -58.7% | €126,767 | €207,651 | €48,341 | 19 | 0.13x | -35.8% | -39.0% | 5.38x | 1.16x | 2.92x | -0.106x |
| 5% | no | 1% | 64.161x | 42.0% | -58.7% | €126,763 | €207,651 | €48,337 | 17 | 0.13x | -35.8% | -39.0% | 5.38x | 1.16x | 2.92x | -0.107x |
| 5% | no | 2% | 64.161x | 42.0% | -58.7% | €126,763 | €207,651 | €48,337 | 17 | 0.13x | -35.8% | -39.0% | 5.38x | 1.16x | 2.92x | -0.107x |
| 10% | yes | 0% | 64.235x | 42.0% | -59.5% | €127,374 | €207,651 | €48,347 | 10 | 0.13x | -35.3% | -35.9% | 5.51x | 1.22x | 3.01x | -0.033x |
| 10% | yes | 1% | 64.235x | 42.0% | -59.5% | €127,374 | €207,651 | €48,347 | 10 | 0.13x | -35.3% | -35.9% | 5.51x | 1.22x | 3.01x | -0.033x |
| 10% | yes | 2% | 64.235x | 42.0% | -59.5% | €127,374 | €207,651 | €48,347 | 10 | 0.13x | -35.3% | -35.9% | 5.51x | 1.22x | 3.01x | -0.033x |
| 10% | no | 0% | 63.829x | 41.9% | -59.7% | €125,653 | €207,651 | €48,145 | 10 | 0.06x | -36.5% | -38.7% | 5.40x | 1.19x | 2.95x | -0.439x |
| 10% | no | 1% | 63.829x | 41.9% | -59.7% | €125,653 | €207,651 | €48,145 | 10 | 0.06x | -36.5% | -38.7% | 5.40x | 1.19x | 2.95x | -0.439x |
| 10% | no | 2% | 63.829x | 41.9% | -59.7% | €125,653 | €207,651 | €48,145 | 10 | 0.06x | -36.5% | -38.7% | 5.40x | 1.19x | 2.95x | -0.439x |
| 20% | yes | 0% | 64.260x | 42.0% | -59.4% | €127,465 | €207,651 | €48,045 | 3 | 0.03x | -35.2% | -35.8% | 5.51x | 1.22x | 3.02x | -0.008x |
| 20% | yes | 1% | 64.260x | 42.0% | -59.4% | €127,465 | €207,651 | €48,045 | 3 | 0.03x | -35.2% | -35.8% | 5.51x | 1.22x | 3.02x | -0.008x |
| 20% | yes | 2% | 64.260x | 42.0% | -59.4% | €127,465 | €207,651 | €48,045 | 3 | 0.03x | -35.2% | -35.8% | 5.51x | 1.22x | 3.02x | -0.008x |
| 20% | no | 0% | 64.141x | 42.0% | -59.1% | €127,046 | €207,651 | €47,993 | 3 | 0.02x | -35.1% | -37.5% | 5.50x | 1.21x | 2.96x | -0.127x |
| 20% | no | 1% | 64.141x | 42.0% | -59.1% | €127,046 | €207,651 | €47,993 | 3 | 0.02x | -35.1% | -37.5% | 5.50x | 1.21x | 2.96x | -0.127x |
| 20% | no | 2% | 64.141x | 42.0% | -59.1% | €127,046 | €207,651 | €47,993 | 3 | 0.02x | -35.1% | -37.5% | 5.50x | 1.21x | 2.96x | -0.127x |

## Reading

**Verdict: REJECT: no harvest variant survives both 1x and 2x cost gates.**

- Realising a loss lowers current-year PFU, but the rebought lot has a lower
  basis, so most of the benefit returns as a larger exit tax. The path is
  mainly tax **deferral** plus two extra fees per round trip.
- The cash variants (no immediate re-entry) change market exposure until the
  next monthly rebalance; they are a separate, weaker strategy and are not
  a clean test of pure loss harvesting.
- The 2x-cost table is the gate. A variant that only wins at 1x is not real.
- All variants, including failures and cash variants, are in the tables.

## Caveats

- The loss test uses the EUR HIFO basis, including buy fees; the re-entry
  price is the same session open used by the baseline fill.
- Annual PFU and 10-year loss carry are in the merged EUR ledger; dividend
  tax is separate and is not consumed by the capital-loss carry.
- 5-year window multiples use a 31.4% terminal-gain overlay on the EUR curve
  (same approximation as the v2 evaluation tables), not a rerun of annual
  tax for each window.
- The bootstrap p5 is a stationary 10k-path bootstrap with an exit-tax
  overlay, not a second annual tax engine.
- The baseline is the dividend-corrected EUR ledger (66.17x), not the
  terminal-FX headline (70.86x).

## Reproduce

```bash
uv run python scripts/tax_harvest.py
uv run python tests/test_tax_harvest.py
uv run python tests/test_eur_ledger.py
```

Generated by `uv run python scripts/tax_harvest.py`.
