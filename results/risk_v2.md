# Risk v2: pre-registered rule tests

Branch `agent/risk`, base commit `38d630b`. This file was written **before**
running any of the rules below. The results table and verdict are appended after
the runs; the pre-registration text is not edited retrospectively (typos aside).

## Baseline to beat

Two views of the same shipped core-only tax book (100% SMH, 2.5x gross, 30%
leverage band, HIFO, monthly decision, next-open fill, daily 200-day SMH cut to
50%):

| view | window | after tax | CAGR | maxDD | p5 | median |
|---|---|---:|---:|---:|---:|---:|
| French CTO, Trade Republic, €10k, native TECH | 2015-01-02..2026-11-25 | **38.80x** | 36.1% | **-69.1%** | 1.65x* | 52.47x* |
| US tax profile, 10 bps, 37% federal | same | n/a | n/a | -74.8% | **0.68x** | 30.04x |

\* The 1.65x / 52.47x p5/median are the TECH book's own pre-tax bootstrap, not
the US tax-profile numbers. The 0.68x is the US tax-profile p5 quoted in
`results/robustness_tax.md`; it is reproduced exactly here. The two views are
reported separately because the French after-tax path and the US 37%-tax
robustness path are different accounting assumptions on the same strategy.

Baseline reproduction commands are in the "Exact commands" section.

## Rules (pre-registered)

All signals are causal: a value at date `t` uses only prices, membership and
equity up to `t`; the decision is made at the close and filled at the next open
(the shipped execution convention). No rule uses a future price, a future
membership snapshot, or the full-sample result.

### R1. Breadth scale

- Breadth `b_t` = fraction of the book's point-in-time market members whose
  adjusted close is above their own 200-day simple moving average at `t`.
  Members with fewer than 200 observations are excluded from numerator and
  denominator. If there are no valid members, `b_t = NaN` and no adjustment is
  made.
- Scale `s_t = 1.0` when `b_t >= 0.50`; otherwise
  `s_t = 0.50 + b_t` (linear: 0.50 at `b=0`, 1.00 at `b=0.50`).
- Applied to the target gross at the monthly decision only:
  `gross = 2.5 * s_t`, then the daily 200-day trend cut may further multiply by
  0.50. It is not a daily trading rule.
- Universe: the panel's own point-in-time membership. For the French TECH book
  that is the 49-name tech universe; for the US robustness book it is the S&P
  500 point-in-time membership. This is the market the book actually trades, not
  a SPY-only proxy.
- Pre-registered variants:
  - **R1a primary:** threshold 0.50, floor 0.50.
  - **R1b:** threshold 0.40, floor 0.50.
  - **R1c:** threshold 0.60, floor 0.50.
  - **R1d (structural alternative):** three steps: `b < 1/3 -> 0.50`,
    `1/3 <= b < 2/3 -> 0.75`, `b >= 2/3 -> 1.00`.

### R2. Drawdown ladder

- At an equity drawdown of **-30%** from the running peak, cap target gross at
  **1.5x** (`dd_scale = 0.60` of the 2.5x target) until the drawdown recovers to
  **-15%**; then restore the normal target at the next monthly decision.
- Uses the existing `dd_stop` / `dd_scale` / `dd_recover` engine fields; no new
  code.
- Pre-registered variants:
  - **R2a primary:** `dd_stop=0.30, dd_scale=0.60, dd_recover=0.15`.
  - **R2b:** `dd_stop=0.25, dd_scale=0.60, dd_recover=0.15`.
  - **R2c:** `dd_stop=0.35, dd_scale=0.60, dd_recover=0.15`.
  - **R2d:** `dd_stop=0.30, dd_scale=0.70, dd_recover=0.15` (1.75x cap).
- Because the daily 200-day trend cut already halves exposure, this rule may be
  inert, or it may override the trend cut with a milder 0.60 scale on the same
  day. Both outcomes will be reported, including the number of days the ladder
  is active.

### R3. Core/SPY mix

- Core target = **80% SMH + 20% SPY** by weight, rebalanced at the monthly
  decision; total core weight 1.0 before leverage; 2.5x gross on the blend. The
  daily trend rule stays on SMH's 200-day MA.
- Pre-registered variants:
  - **R3a primary:** 80/20.
  - **R3b:** 70/30.
- Implementation for the test: a custom selector returns
  `{SMH: 0.8, SPY: 0.2}` when the core is on. SPY is already in the panel, so
  this is a real two-ETF book with costs on the SPY leg at each rebalance, not a
  synthetic daily-rebalanced index. The tax profile is `core_mode="always"`, so
  the mix applies throughout the primary test.

### Not re-tested

Hard drawdown stop to cash, volatility targeting, VIX cut, TLT/GLD defensive
overlay, 42-day rebalance, core rotation, selection hysteresis, and
risk-adjusted momentum ranking are already rejected in `results/rejected.md`.
None is part of this test.

## Evaluation protocol

For every variant, on both the French TECH book and the US tax-profile book:

1. Full window 2015-01-02..2026-11-25: after-tax multiple (French view), CAGR,
   maxDD, Sharpe, turnover, average gross exposure.
2. Calendar-year 2018 and 2022 return and within-year maxDD, taken from the
   full-window run (so the drawdown peak and the ladder state are realistic; a
   fresh 2018/2022 start would erase the pre-crash peak).
3. Block bootstrap of daily equity returns: 10-day blocks, 2,000 paths, seed 7
   (the existing `scripts/robustness.py` method). Report p5, median, p95 of
   terminal wealth and median/p5 maxDD.
4. French after-tax view: TECH, CTO, Trade Republic, €10k, 5 bps spread, 1 EUR
   per order, 25 bps FX, HIFO, PFU schedule; report after-tax multiple, CAGR,
   fees, tax paid, exit tax.
5. Leverage-matched baseline: for any variant whose average gross differs from
   the baseline, rerun the baseline with `leverage = 2.5 * avg_gross_variant /
   avg_gross_baseline`. A variant that cannot beat this matched baseline is a
   leverage dial, not a timing signal.

## Decision criteria (pre-registered)

A variant is **adopted as the default** only if all five hold:

1. After-tax terminal multiple beats the baseline, or is within 5% of it while
   the bootstrap p5 is at least 20% higher and maxDD is at least 10 pp better.
2. Bootstrap p5 is no worse than the baseline, and median terminal wealth is at
   least 90% of the baseline.
3. maxDD is no worse than the baseline; 2018 and 2022 are each no worse by more
   than 2 pp, and at least one of them improves by at least 3 pp.
4. It beats the leverage-matched baseline on terminal wealth or p5.
5. Neighbouring pre-registered parameter values point the same way; no
   knife-edge result, and no single year or single path drives the gain.

If a variant passes 2-5 but not 1, it can be added as an **opt-in profile** with
the default unchanged. Otherwise it is rejected.

All tested variants are reported, including inert and losing ones. A negative
result is a valid outcome; the shipped defaults do not change without passing
the criteria above.
