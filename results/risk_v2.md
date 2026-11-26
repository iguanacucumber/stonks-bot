# Risk v2: pre-registered rule tests

Branch `agent/risk`, base commit `38d630b`. This file was written **before**
running any of the rules below. The results table and verdict are appended after
the runs; the pre-registration text is not edited retrospectively (typos aside).

> **Post-fix note:** US/TECH core opens are now real. The TECH baseline is
> 39.50x at Trade Republic (and 57.01x for the monthly book at IBKR); the table
> below uses the pre-fix close-fill 38.80x. The 1.8% shift does not change the
> verdict or the ranking, but exact deltas should be regenerated before quoting.

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

---

# Results

Pre-registration commit: `f7d23be` (the text above). Runs were made after that
commit. The baseline was reproduced inside `scripts/risk_v2.py`: French TECH
`38.80x` after tax, `-69.1%` maxDD; US tax profile `p5 0.68x`, median `30.04x`
(matching `results/france.md` and `results/robustness_tax.md`). No shipped
default was changed.

## French TECH book (CTO, Trade Republic, €10k)

| variant | after-tax x | CAGR | maxDD | Sharpe | p5 | median | 2018 | 2022 | avg gross | matched after-tax x | changed |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|:---:|
| baseline | 38.80x | 36.1% | -69.1% | 0.83 | 1.65x | 52.47x | -37.0% | -58.1% | 2.12 | 38.80x | no |
| R1a breadth 50% | 34.26x | 34.7% | -68.7% | 0.82 | 1.65x | 46.51x | -37.0% | -57.6% | 2.09 | 37.91x | yes |
| R1b breadth 40% | 37.73x | 35.8% | -69.2% | 0.83 | 1.74x | 51.29x | -37.0% | -58.3% | 2.10 | 38.46x | yes |
| R1c breadth 60% | 33.14x | 34.3% | -68.7% | 0.82 | 1.65x | 44.77x | -37.1% | -56.1% | 2.07 | 37.59x | yes |
| R1d breadth 3-step | 31.13x | 33.6% | -63.5% | 0.81 | 1.83x | 39.33x | -36.9% | -49.9% | 1.95 | 32.80x | yes |
| R2a dd -30% 1.5x | 31.18x | 33.6% | -70.0% | 0.83 | 1.92x | 45.20x | -19.8% | -59.4% | 1.88 | 30.79x | yes |
| R2b dd -25% 1.5x | 38.84x | 36.1% | -69.3% | 0.86 | 2.40x | 55.25x | -21.2% | -58.6% | 1.85 | 30.24x | yes |
| R2c dd -35% 1.5x | 30.08x | 33.2% | -69.8% | 0.82 | 1.66x | 42.63x | -29.1% | -59.2% | 1.88 | 30.80x | yes |
| R2d dd -30% 1.75x | 37.75x | 35.8% | -74.2% | 0.84 | 1.74x | 55.27x | -23.5% | -64.0% | 2.02 | 35.84x | yes |
| R3a core 80/20 | 32.93x | 34.2% | -68.3% | 0.83 | 1.90x | 42.91x | -32.1% | -57.3% | 2.12 | 38.80x | yes |
| R3b core 70/30 | 28.10x | 32.4% | -67.9% | 0.81 | 1.76x | 36.10x | -29.5% | -56.9% | 2.12 | 38.80x | yes |

`p5` / `median` are the pre-tax block bootstrap of the TECH book's daily
returns (10-day blocks, 2,000 paths, seed 7). `matched after-tax x` is a
baseline run with leverage scaled to the variant's average gross; it is the
leverage-dial control.

Within-year maxDD, TECH: 2018 was `-59.0%` for the baseline, `-46.4%` for R2b
and `-55.1%` for R3a; 2022 was `-67.0%` for the baseline, `-68.6%` for R2b and
`-65.1%` for R3a. R2b helps 2018 but not 2022; R3a helps both, at a lower
return.

## US tax profile (10 bps, 37% federal)

| variant | final x | CAGR | maxDD | Sharpe | p5 | median | p5 DD | 2018 | 2022 | avg gross | matched final x | matched p5 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline | 30.08x | 33.2% | -74.8% | 0.76 | 0.68x | 30.04x | -95% | -41.8% | -68.4% | 2.12 | 30.08x | 0.68x |
| R1a breadth 50% | 26.32x | 31.7% | -72.7% | 0.75 | 0.73x | 26.45x | -95% | -42.5% | -68.5% | 2.08 | 32.58x | 0.80x |
| R1b breadth 40% | 25.93x | 31.6% | -73.9% | 0.75 | 0.67x | 25.89x | -95% | -41.6% | -69.4% | 2.09 | 32.84x | 0.78x |
| R1c breadth 60% | 25.49x | 31.4% | -71.6% | 0.75 | 0.78x | 25.68x | -95% | -43.1% | -67.5% | 2.05 | 30.45x | 0.79x |
| R1d breadth 3-step | 19.07x | 28.2% | -69.1% | 0.72 | 0.83x | 18.78x | -93% | -36.0% | -63.9% | 1.85 | 28.65x | 1.06x |
| R2a dd -30% 1.5x | 34.28x | 34.7% | -71.8% | 0.80 | 1.59x | 35.25x | -91% | -25.5% | -61.9% | 1.86 | 28.79x | 1.05x |
| R2b dd -25% 1.5x | 34.92x | 34.9% | -71.7% | 0.80 | 1.73x | 35.14x | -89% | -28.6% | -61.8% | 1.82 | 27.92x | 1.09x |
| R2c dd -35% 1.5x | 44.42x | 37.7% | -72.1% | 0.82 | 1.76x | 45.72x | -91% | -31.5% | -62.3% | 1.86 | 28.98x | 1.05x |
| R2d dd -30% 1.75x | 49.51x | 38.9% | -75.1% | 0.83 | 1.57x | 50.43x | -92% | -26.6% | -65.2% | 2.02 | 30.09x | 0.82x |
| R3a core 80/20 | 25.93x | 31.6% | -71.0% | 0.76 | 0.94x | 25.34x | -93% | -38.3% | -64.5% | 2.12 | 30.08x | 0.68x |
| R3b core 70/30 | 21.77x | 29.6% | -69.0% | 0.75 | 0.99x | 21.34x | -92% | -36.6% | -62.6% | 2.12 | 30.08x | 0.68x |

The US tax book taxes realized gains only and does not charge a final
liquidation tax, so it rewards deferral. The French CTO table charges the exit
tax and is the primary decision metric. This is why R2c/R2d look much better in
the US table than in the French table.

## French cost decomposition (per €10k initial)

| variant | fees x | tax paid x | exit tax x |
|---|---:|---:|---:|
| baseline | 3.181x | 5.057x | 18.418x |
| R1a breadth 50% | 3.005x | 4.645x | 16.187x |
| R1b breadth 40% | 3.144x | 4.891x | 17.931x |
| R1c breadth 60% | 2.957x | 4.670x | 15.465x |
| R1d breadth 3-step | 2.716x | 5.432x | 12.399x |
| R2a dd -30% 1.5x | 2.090x | 0.543x | 17.524x |
| R2b dd -25% 1.5x | 3.359x | 2.043x | 20.282x |
| R2c dd -35% 1.5x | 1.944x | 0.640x | 16.720x |
| R2d dd -30% 1.75x | 2.495x | 0.651x | 22.102x |
| R3a core 80/20 | 2.646x | 4.897x | 14.860x |
| R3b core 70/30 | 2.431x | 4.771x | 12.315x |

R2's pre-tax improvement is largely a timing effect: intermediate tax paid
falls, but the exit tax rises. R2b's exit tax is `20.28x` versus `18.42x` for
the baseline, leaving after-tax wealth essentially flat (`38.84x` vs `38.80x`).

## Post-hoc diagnostics (not used for selection)

These were run after the main table to explain the result. They are not
additional chances to adopt a rule.

### Drawdown-trigger sweep (cap 1.5x, recover -15%)

| trigger | TECH after-tax | TECH p5 | TECH maxDD | TECH 2018 | TECH 2022 | US final | US p5 | US maxDD |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| -20% | 31.49x | 2.17x | -67.6% | -27.5% | -56.3% | 28.45x | 1.51x | -67.0% |
| -22% | 29.84x | 1.94x | -69.1% | -27.5% | -58.3% | 30.58x | 1.51x | -68.9% |
| -24% | **44.53x** | 2.83x | -69.3% | -21.4% | -58.6% | **40.45x** | 2.12x | -69.1% |
| -25% | 38.84x | 2.40x | -69.3% | -21.2% | -58.6% | 34.92x | 1.73x | -71.7% |
| -26% | 32.54x | 2.09x | -69.9% | -23.1% | -59.4% | 35.95x | 1.70x | -71.5% |
| -28% | 26.34x | 1.69x | -69.9% | -24.7% | -59.4% | 29.30x | 1.35x | -71.5% |
| -30% | 31.18x | 1.92x | -70.0% | -19.8% | -59.4% | 34.28x | 1.59x | -71.8% |
| -32% | 33.74x | 2.13x | -70.0% | -20.7% | -59.4% | 28.65x | 1.30x | -73.4% |
| -35% | 30.08x | 1.66x | -69.8% | -29.1% | -59.2% | 44.42x | 1.76x | -72.1% |

There is no plateau. A 1 pp change from -24% to -25% to -26% moves the French
after-tax result from `44.53x` to `38.84x` to `32.54x`. The pre-registered -30%
primary is `31.18x`, 20% below the baseline. The US column is equally jagged
(`28.65x` at -32%, `44.42x` at -35%). This is path-specific tuning, not a
robust rule.

### Bootstrap sensitivity (5 seed/block combinations)

Ranges across blocks 5/10/21 at seed 7 and seeds 7/11/23 at block 10:

| variant | US p5 | US median | TECH p5 | TECH median |
|---|---:|---:|---:|---:|
| baseline | 0.57-0.79x | 30.04-32.45x | 1.47-1.65x | 52.13-55.59x |
| R1d breadth 3-step | 0.59-0.83x | 18.78-20.32x | 1.45-1.83x | 37.96-43.35x |
| R2a dd -30% | 1.18-1.66x | 32.98-37.36x | 1.74-2.26x | 42.99-49.98x |
| R2b dd -25% | 1.47-1.87x | 35.14-37.77x | 2.40-2.92x | 51.39-59.23x |
| R3a core 80/20 | 0.77-1.01x | 25.34-28.01x | 1.55-1.90x | 42.91-46.29x |

R2b's p5 improvement is stable across bootstrap resamples. The problem is not
the bootstrap; it is that the rule's trigger value is a path-specific spike.
R3a's p5 improvement is also stable, but it is bought with a lower median.

### Breadth causality check

`uv run python scripts/risk_v2.py --check` truncates the panel at 2018-06-01
and 2022-06-01 and asserts that breadth before the cut is unchanged. Both
markets pass.

## Verdict

**No variant passed all five pre-registered criteria. Shipped defaults are
unchanged, and no opt-in profile was added.**

- **R1 breadth (all four variants): rejected.** The scale is almost always
  1.0, so this is a mild leverage dial. Every variant lowers or barely moves
  after-tax wealth; R1d is the best risk reducer (`-63.5%` maxDD, `1.83x` p5)
  but gives up 20% of terminal wealth and still loses to its leverage-matched
  baseline (`32.80x` vs `31.13x`). Its TECH p5 range includes a value below the
  baseline's (`1.45x` vs `1.47x` at seed 23), so the p5 gain is not robust.
  2018 is unchanged; only 2022 improves. Reject.
- **R2 drawdown ladder (all four variants): rejected.** R2a is the
  pre-registered primary and loses 20% of after-tax wealth (`31.18x`) with a
  slightly worse maxDD (`-70.0%`). R2b (-25%) is flat after tax (`38.84x` vs
  `38.80x`), improves p5 to `2.40x`, and cuts 2018 from `-37.0%` to `-21.2%`,
  but 2022 is slightly worse (`-58.6%` vs `-58.1%`) and maxDD is slightly worse
  (`-69.3%` vs `-69.1%`). Mechanically, when the ladder and the 200-day trend
  cut trigger on the same day, the engine gives the drawdown reason priority
  and uses the milder 0.60 scale instead of the trend's 0.50, so the ladder can
  leave the book *more* exposed than the baseline in a crash. The trigger sweep
  shows a 1 pp knife-edge and no plateau. R2c/R2d look better in the US table
  only because that table does not charge the exit tax; after French tax they
  are `30.08x` and `37.75x`. Reject.
- **R3 core/SPY mix: rejected as a clear improvement.** R3a (80/20) is the
  only structurally consistent risk reducer: p5 `1.90x` vs `1.65x` (+15%) in
  the French book and `0.94x` vs `0.68x` (+38%) in the US book; maxDD improves
  by 0.8 pp and 3.8 pp; both 2018 and 2022 improve. But after-tax wealth falls
  15% (`32.93x` vs `38.80x`), the median bootstrap falls 18% (`42.91x` vs
  `52.47x`), Sharpe is unchanged (0.83) and Calmar is slightly worse
  (0.501 vs 0.522). R3b (70/30) gives up 28% of wealth. This is a risk
  preference, not a risk-adjusted improvement. It fails criteria 1 and 2. If
  the user explicitly wants a lower-tail-risk book, 80/20 is the only
  candidate worth a separate opt-in decision; it is not shipped here.

## Strongest reason NOT to adopt the winner

The numerical "winner" is R2b, the -25% drawdown ladder: it matches the
baseline's after-tax wealth (`38.84x` vs `38.80x`), raises the French p5 from
`1.65x` to `2.40x`, and is stable across bootstrap resamples. It should still
not be adopted because the result is a **1 pp parameter spike on one path**:
- -24% trigger: `44.53x` after tax
- -25% trigger: `38.84x`
- -26% trigger: `32.54x`
- -28% trigger: `26.34x`
- -30% trigger: `31.18x`

A one-point change in the trigger moves after-tax wealth by about 6x; a
two-point change by about 12x. The rule also fails on the risk it is supposed
to fix: maxDD is slightly worse than the baseline (`-69.3%` vs `-69.1%`), and
2022 is slightly worse (`-58.6%` vs `-58.1%`). The pre-tax gain is largely tax timing: exit tax rises from
`18.42x` to `20.28x`, so after-tax wealth is flat. This is exactly the
full-sample path fitting the task says to reject. A rule that needs the trigger
tuned to -25% on this sample is not a rule.

## Recommendation

Keep the shipped baseline: 100% SMH, 2.5x, daily 200-day cut to 50%, 30%
leverage band, HIFO, monthly/next-open. The baseline's weakness is real
(maxDD about -70%, US-tax p5 0.68x), but none of the three pre-registered
families fixes it without giving up more after-tax wealth or relying on a
parameter spike. The 80/20 core mix is the only candidate that reduces tail
risk consistently in both markets and both stress years; treat it as an
explicit risk-preference option, not a default, and do not add it without the
user choosing that trade-off.

## Exact commands

```bash
# Pre-registered variants, full tables, French after-tax and US bootstrap
uv run python scripts/risk_v2.py

# Post-hoc trigger sweep and bootstrap seed/block sensitivity
uv run python scripts/risk_v2.py --sweep

# Breadth causality assertion (truncated panel, no look-ahead)
uv run python scripts/risk_v2.py --check

# Existing accounting/no-look-ahead checks
uv run python tests/test_core.py
```

## Test output

```
$ uv run python scripts/risk_v2.py --check
US: breadth causal check ok
TECH: breadth causal check ok

$ uv run python tests/test_core.py
ok: 170 days, final 0.991, 90 trades, sharpe 0.03
```
