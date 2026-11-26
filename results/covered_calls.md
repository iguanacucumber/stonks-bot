# Covered calls: income vs capped upside

## Model and pre-registered decision rule

No option quotes are in the cache. This is a **Black-Scholes model**, not
broker-accounted P&L. Assumptions: monthly calls written at the month-end
close, expiry the next month-end close, 2/5/10% OTM strikes, flat IV
25/30/40%, 4% rate, no dividends, European cash settlement, 5% bid/ask
(seller receives bid), $0.65 per contract, 100-share multiplier, fractional
contracts for $10k comparability. Premium is credited at the decision
close; payoff is intrinsic at expiry. Assignment is the cash-settled
intrinsic payoff; shares are not physically removed, so the model is
optimistic about the share-lot tax basis (see caveats).

Coverage: `core` writes only against the actual SMH position (a true
covered call; no calls in stock-sleeve months); `book` uses SMH as a proxy
for gross book exposure and is not a true covered call when the book holds
a stock basket. The uncovered-roll count is reported.

The rule below was fixed before looking at the results: an overlay is an
**income opt-in** only if (1) net option income is positive, (2) it keeps
at least 85% of baseline EUR-ledger terminal wealth after 2x costs, and
(3) it improves maxDD by at least 3 pp or the worst 5-year CAGR by at least
2 pp. Otherwise it is a reject; 85-90% terminal with a tail gain is
`paper`. No rule changes the default.

## Baselines

| book | EUR ledger x | CAGR | maxDD | 2018 | 2022 | 5y median | 5y worst |
|---|---:|---:|---:|---:|---:|---:|---:|
| US monthly | 66.721x | 42.4% | -56.4% | -30.2% | -32.8% | 48.4% | 5.2% |
| A6 70/30 | 36.850x | 35.5% | -46.0% | -19.0% | -27.1% | 39.8% | 6.7% |

EUR ledger: year-dependent PFU, HIFO lots, trade-date FX, IBKR-like fees,
gross distributions (no dividend-event overlay). The baseline is re-run
with the same code path as every overlay.

## US monthly

| coverage | OTM | IV | EUR x | CAGR | maxDD | 2018 | 2022 | 5y med | 5y worst | 5y beat | income € | 2x x | verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| book | 2% | 25% | -7.711x | n/a% | -111.9% | -17.4% | -44.0% | 35.6% | -38.9% | 3% | -946,441 | -12.65x | reject: model ruin (equity <= 0) |
| book | 2% | 30% | 18.011x | 27.6% | -83.8% | -4.6% | -29.8% | 39.3% | 3.3% | 23% | -681,090 | 12.46x | reject: negative income |
| book | 2% | 40% | 60.418x | 41.2% | -54.7% | 11.6% | -13.1% | 45.8% | 26.2% | 44% | -146,315 | 54.90x | reject: negative income |
| book | 5% | 25% | 7.200x | 18.1% | -94.7% | -18.8% | -42.0% | 40.7% | -17.1% | 15% | -797,955 | 3.39x | reject: negative income |
| book | 5% | 30% | 27.404x | 32.1% | -75.6% | -8.8% | -31.1% | 43.9% | 12.6% | 33% | -564,710 | 23.85x | reject: negative income |
| book | 5% | 40% | 65.579x | 42.2% | -52.6% | 6.5% | -16.1% | 48.5% | 26.9% | 45% | -68,635 | 61.10x | reject: negative income |
| book | 10% | 25% | 36.360x | 35.3% | -68.2% | -20.3% | -40.9% | 45.2% | 10.4% | 20% | -421,783 | 34.00x | reject: negative income |
| book | 10% | 30% | 47.537x | 38.4% | -60.6% | -13.5% | -34.1% | 47.0% | 14.6% | 35% | -278,295 | 44.93x | reject: negative income |
| book | 10% | 40% | 75.958x | 44.0% | -48.5% | -0.2% | -21.5% | 50.2% | 23.1% | 45% | 91,380 | 72.66x | accept: income opt-in |
| core | 2% | 25% | -2.254x | n/a% | -104.9% | -28.9% | -21.2% | 43.6% | -58.0% | 28% | -894,904 | -6.89x | reject: model ruin (equity <= 0) |
| core | 2% | 30% | 20.551x | 29.0% | -81.7% | -16.3% | -16.0% | 43.3% | 4.0% | 36% | -659,618 | 15.37x | reject: negative income |
| core | 2% | 40% | 57.548x | 40.7% | -55.9% | -1.7% | -8.9% | 47.8% | 24.3% | 46% | -185,438 | 52.47x | reject: negative income |
| core | 5% | 25% | 12.157x | 23.4% | -90.2% | -24.8% | -24.4% | 43.6% | -7.0% | 34% | -749,398 | 8.53x | reject: negative income |
| core | 5% | 30% | 29.274x | 32.9% | -74.1% | -16.3% | -19.8% | 45.9% | 13.2% | 42% | -542,581 | 25.93x | reject: negative income |
| core | 5% | 40% | 63.024x | 41.7% | -53.6% | -3.3% | -12.6% | 49.3% | 25.0% | 47% | -102,715 | 58.86x | reject: negative income |
| core | 10% | 25% | 38.932x | 36.1% | -66.6% | -22.2% | -29.7% | 45.7% | 9.7% | 23% | -389,839 | 36.63x | reject: negative income |
| core | 10% | 30% | 48.792x | 38.7% | -60.1% | -16.7% | -26.0% | 47.4% | 13.5% | 39% | -262,609 | 46.27x | reject: negative income |
| core | 10% | 40% | 73.911x | 43.7% | -49.1% | -5.9% | -19.1% | 50.4% | 21.1% | 46% | 65,179 | 70.79x | accept: income opt-in |

Best terminal in this group: `book` 10% OTM 40% IV at 75.958x; net option income €91,380; worst 5-year CAGR 23.1%.

## A6 70/30

| coverage | OTM | IV | EUR x | CAGR | maxDD | 2018 | 2022 | 5y med | 5y worst | 5y beat | income € | 2x x | verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| book | 2% | 25% | -1.618x | n/a% | -109.0% | -8.0% | -37.1% | 28.3% | -42.8% | 2% | -486,263 | -3.95x | reject: model ruin (equity <= 0) |
| book | 2% | 30% | 11.791x | 23.1% | -72.6% | 1.8% | -24.8% | 31.8% | 2.2% | 22% | -346,555 | 9.15x | reject: negative income |
| book | 2% | 40% | 34.533x | 34.7% | -45.4% | 15.2% | -9.9% | 38.1% | 22.5% | 45% | -64,995 | 31.79x | reject: negative income |
| book | 5% | 25% | 6.172x | 16.6% | -86.6% | -10.4% | -35.5% | 32.4% | -11.5% | 14% | -407,733 | 4.44x | reject: negative income |
| book | 5% | 30% | 16.967x | 26.9% | -63.9% | -2.3% | -26.1% | 35.5% | 9.0% | 34% | -284,929 | 15.29x | reject: negative income |
| book | 5% | 40% | 37.348x | 35.6% | -43.6% | 10.5% | -12.8% | 40.2% | 23.4% | 47% | -23,745 | 35.16x | reject: negative income |
| book | 10% | 25% | 20.730x | 29.1% | -58.3% | -11.7% | -34.5% | 37.2% | 10.7% | 20% | -223,297 | 19.69x | reject: negative income |
| book | 10% | 30% | 26.645x | 31.8% | -50.4% | -6.3% | -28.7% | 38.9% | 14.3% | 37% | -147,750 | 25.47x | reject: negative income |
| book | 10% | 40% | 41.808x | 36.9% | -40.2% | 4.8% | -17.5% | 41.9% | 21.7% | 48% | 46,885 | 40.25x | accept: income opt-in |
| core | 2% | 25% | 8.150x | 19.3% | -82.0% | -17.9% | -18.5% | 34.9% | -5.5% | 26% | -389,678 | 6.19x | reject: negative income |
| core | 2% | 30% | 17.026x | 27.0% | -63.2% | -9.9% | -14.4% | 36.2% | 10.0% | 35% | -285,546 | 15.19x | reject: negative income |
| core | 2% | 40% | 33.376x | 34.4% | -45.4% | 0.8% | -8.6% | 39.9% | 20.8% | 48% | -75,686 | 31.15x | reject: negative income |
| core | 5% | 25% | 13.614x | 24.6% | -71.2% | -16.0% | -21.0% | 36.1% | 5.6% | 33% | -324,418 | 12.34x | reject: negative income |
| core | 5% | 30% | 20.787x | 29.1% | -57.9% | -10.2% | -17.4% | 38.2% | 14.2% | 44% | -232,886 | 19.35x | reject: negative income |
| core | 5% | 40% | 35.917x | 35.2% | -43.7% | -0.6% | -11.6% | 41.3% | 21.5% | 48% | -38,212 | 34.09x | reject: negative income |
| core | 10% | 25% | 24.347x | 30.8% | -54.2% | -14.1% | -24.9% | 38.3% | 9.7% | 23% | -175,138 | 23.38x | reject: negative income |
| core | 10% | 30% | 28.729x | 32.7% | -48.6% | -10.4% | -22.2% | 39.7% | 12.4% | 41% | -118,829 | 27.66x | reject: negative income |
| core | 10% | 40% | 39.992x | 36.4% | -40.7% | -2.6% | -16.8% | 42.1% | 18.3% | 48% | 26,241 | 38.64x | accept: income opt-in |

Best terminal in this group: `book` 10% OTM 40% IV at 41.808x; net option income €46,885; worst 5-year CAGR 21.7%.

## What happened

The overlay is short SMH calls into a strong SMH bull market. Premium
income is real, but the intrinsic payoff at expiry exceeds it in the
assigned months; the net option P&L is negative in most variants. The
cap also arrives in the same months as the book's largest recoveries,
so maxDD and the worst 5-year window do not improve enough to pay for the
terminal loss. The PFU treatment is included in the EUR ledger through
the annual CTO netting bucket; the option P&L is not tax-free.

`book` coverage writes against gross exposure that is not always SMH;
those rows are a model variant, not an implementable covered call. The
core rows are the implementable comparison.

## Decision

**Accept only as a model-only income opt-in:** 4 of 36 variants pass, all of them 10% OTM / 40% IV. Every 25% and 30% IV variant has negative net option income. Keep the no-overlay US monthly book as the default; this is not a validated live strategy.
The 10%/40% rows improve terminal, maxDD and the worst 5-year window in this sample, but the 40% IV is an assumption (no option quotes in cache) and the model is optimistic on European cash settlement and fractional contracts. Treat it as a hypothesis for a paper/income sleeve, not a default allocation.

## Caveats

- No option quotes in the cache; flat IV ignores the volatility smile,
  term structure and event vol. The model is not broker-accounted P&L.
- European cash settlement avoids physical assignment and share-lot tax.
  Physical assignment would realize the share gain and reset the lot;
  the tax drag could be worse or better. Treat the model as optimistic.
- Fractional contracts are used so a $10k account can be compared with
  the research equity path. Real SMH contracts are 100 shares; at $10k
  one contract may exceed the sleeve, so the implementable income can be
  zero until the account is larger.
- Dividends are gross; no dividend-event overlay is applied to either
  side. The comparison is internally consistent but not the final real-
  life EUR number.
- The `book` coverage is a proxy and is not a true covered call when the
  book holds a stock basket; use the `core` rows for implementation.

Full machine-readable table: `results/covered_calls.csv`.

Reproduce: `uv run python scripts/covered_calls.py`.
