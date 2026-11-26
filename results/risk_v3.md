# Risk v3: survivable compounding, pre-registered

Branch `v2/risk-alloc`, base `93b39c4`. This file is written **before** the v3
sweep runs. The pre-registration is not edited retrospectively; results are
appended below the line.

## Baseline (reproduced first)

TECH monthly book, SMH core, 2.5x gross, 500 bps financing, daily 200-day SMH cut
to 50%, monthly calendar decision at the close, next-open fill, IBKR, French CTO
€10k, HIFO, 5 bps spread + 2 bps FX + €1/order. 2015-01-02..2026-11-25.

| metric | value |
|---|---:|
| after-tax multiple | **57.0125x** |
| pre-tax multiple | 68.1693x |
| CAGR after tax | 40.56% |
| max drawdown (pre-tax equity) | **-67.83%** |
| 2018 return | -17.26% |
| 2022 return | -56.58% |
| worst rolling 12m | -67.14% (ending 2022-12-27) |
| 2015-2019 multiple / CAGR | 3.82x / 30.8% |
| 2020-2026 multiple / CAGR | 16.94x / 50.8% |
| bootstrap p5 / median (pre-tax, 10d block) | 2.31x / 70.27x |
| bootstrap p5 / median (after-tax approximation) | 1.93x / 58.77x |
| fees / PFU paid / exit tax | €26,423 / €135,224 / €179,830 |
| trades | 1,836 |

Reproduction:

```bash
uv run python scripts/risk_v3.py --baseline
```

`scripts/risk_v3.py --baseline` recomputes the figures above and asserts the
after-tax multiple is 57.0125x ± 0.01 and maxDD is -67.83% ± 0.1 pp.

## What is already rejected (not re-tested as new)

`results/rejected.md`: monthly vol targeting as a raw leverage dial, VIX cut, hard
drawdown-to-cash, TLT/GLD in the risk-off slot, 80/20 SMH/SPY, selection
hysteresis, risk-adjusted momentum ranking, 42-day rebalance, core rotation.
`results/risk_v2.md`: breadth scale and single-step drawdown ladders on TECH and
US; the French exit tax makes the ladders unattractive.
`results/leverage_fr.md`: 1.5x/2x/3x all fail after tax; 2.5x stays.
`results/allocation.md`: 60% bot / 40% SPY is the pre-tax Sharpe optimum, taxes
ignored.

v3 is deliberately different: every dynamic exposure rule is judged against a
**leverage-matched constant-leverage baseline**, not against 2.5x. A rule that
only changes average gross is a leverage dial regardless of its Sharpe.

## Common protocol

- Signals use only adjusted closes, membership and equity through the decision
  close `t`; fills are at the next open `t+1` (the shipped convention). No rule
  sees a future price, future membership, or the full-sample result.
- Primary book: TECH monthly at IBKR, French CTO, after-tax (PFU + exit tax).
  Secondary check: the same rule on the US tax-profile book (37% federal, 10 bps)
  where the tax treatment is different. A rule that only works in one tax regime
  is rejected.
- Metrics: after-tax multiple, after-tax CAGR, pre-tax maxDD, 2018, 2022, worst
  rolling 12m, 2015-2019 and 2020-2026 halves, 10-day block bootstrap p5 and
  median (2,000 paths, seed 7), average gross, turnover, fees, PFU, exit tax.
- Bootstrap after-tax p5 is approximated as pre-tax p5 × (after-tax multiple /
  pre-tax multiple). This is a proportional-tax approximation, not a second tax
  engine; it is stated wherever it is used.
- 2x-cost sensitivity: rerun the baseline and every finalist with `cost_bps`
  doubled (5 → 10 bps; broker commission and FX unchanged).
- Trial count includes every variant looked at, including failures. Leverage-
  matched controls and cost reruns are reported separately as diagnostics.

## Pre-registered families

### V. Volatility targeting with bands and hysteresis

- `sigma_t` = 20-day realized volatility of SMH adjusted close returns through
  `t`, annualized by √252.
- Raw target `g* = clip(target_vol / sigma_t, 0.5, 2.5)`.
- No-trade band 10%: keep the previous gross if `|g*/g_prev - 1| < 0.10`.
- Hysteresis 10%: re-risk (increase gross) only if `g* >= 1.10 * g_prev` **and**
  `sigma_t <= target_vol * 0.90`; de-risk (cut gross) at `g* <= 0.90 * g_prev`.
- Maximum change 0.5x gross per monthly decision. Missing sigma holds the
  previous gross. Initial gross 2.5.
- Applied at the monthly decision only, by scaling the selector weights to
  `g_t / 2.5` with `cfg.leverage = 2.5`. The daily 200-day cut still applies on
  top (so a vol cut and a trend cut can compound).
- Variants (pre-registered): V1 target 20%, V2 25%, V3 30% (primary), V4 35%;
  V5 target 30%, band 5%; V6 target 30%, hysteresis 0%.
- Leverage-matched control for each: constant leverage equal to the variant's
  full-window average gross, same costs and tax. This is a diagnostic, not an
  implementable strategy.
- Pass: all max-growth gates below, **and** after-tax multiple or p5 beats the
  leverage-matched control. If the control wins, reject the family as a leverage
  dial.

### D. Drawdown circuit breaker

- Existing engine rule: at an equity drawdown of -X from the running peak, cut
  gross to 60% (1.5x) until the drawdown recovers to -15%. Decided at the close,
  executed at the next close (the engine's convention).
- Pre-registered variants: D1 X=25%, D2 X=30% (primary), D3 X=35%.
- No new engine code, no multi-rung ladder: `results/risk_v2.md` already showed
  the trigger is a knife-edge. The point of re-running is the current IBKR
  baseline and the leverage-matched / 2x-cost view, not another trigger sweep.
- Pass: max-growth gates; 2018 and 2022 must both be no worse.

### R. Regime / trend overlay on top of the daily core cut

- R1: at the monthly decision, if SPY is below its 200-day MA, scale the whole
  target to 50%; above, 100%. The daily SMH 200-day cut remains.
- R2: same, but require **both** SPY and SMH below their 200-day MAs (the daily
  cut already handles SMH intra-month, so this is a slower confirmation).
- R3: 12-month total return of SMH <= 0 at the monthly decision -> scale to 50%.
- Pass: max-growth gates. R3 is the primary. A monthly overlay that merely
  duplicates the daily cut is rejected as inert.

### A. Multi-strategy allocator

The second sleeve is a cross-asset trend book, ETF-only and survivorship-free:
SPY, QQQ, TLT, IEF, GLD, BIL. At each monthly decision, hold every ETF above its
own 200-day MA; weight the selected ETFs by inverse 60-day volatility with a 40%
cap; if none is above trend, hold BIL. 1x gross, 5 bps cost, next-open fill.
No international equity sleeve is included because EFA/EEM are not in the
committed cache; this is stated as a limitation.

- A1: 60% TECH book + 40% SPY, fixed, total target gross 1.9.
- A2: 60% TECH book + 40% cross-asset trend sleeve, fixed, total gross 1.9.
- A3: 50% TECH book + 50% cross-asset trend sleeve, total gross 1.75.
- A4: dynamic vol-parity TECH/ETF-trend weights, 63-day realized vol of each
  standalone sleeve, weight cap 30-80%, rebalanced monthly; total gross
  `w*2.5 + (1-w)*1.0`.
- A5: 70% TECH book + 30% cross-asset trend sleeve, total gross 2.05.
- All combined books run inside one engine/account so French tax and costs are
  account-level, not a synthetic blend. The custom selector scales the TECH and
  ETF sleeve targets; `cfg.leverage` is 2.5 and the selector returns
  `target_gross / 2.5`.
- The standalone ETF trend sleeve (E0) is reported as a benchmark and is a
  diagnostic, not a candidate profile.
- Balanced gates (opt-in profile, not default):
  1. maxDD <= -50% (at least 17.8 pp better than baseline);
  2. after-tax multiple >= 25x;
  3. bootstrap after-tax p5 >= 1.93x (no worse than baseline);
  4. 2018 and 2022 both better than baseline;
  5. each half (2015-2019, 2020-2026) after-tax CAGR >= 20% and half maxDD
     no worse than -50%;
  6. 2x-cost after-tax multiple >= 20x.
- The balanced profile is the passing candidate with the highest after-tax
  multiple; ties go to the lower maxDD. A1 is the pre-registered fallback if no
  combined candidate passes.

### H. Tail hedge cost study

No options data is in the cache. This is an explicit Black-Scholes model, not
engine accounting, and is labelled as such:

- IV 30% for SMH, risk-free 4%, no dividends. Long put strike 90% of spot, short
  put 70% for the 3-month spread; 95%/85% for the 1-month spread. Collar: long
  90% put, short 115% call, zero net premium at the modelled IV.
- Notional is either 2.5x NAV (hedge the gross book) or sized so the annual
  premium is 1.5% of NAV.
- Premium is paid at the roll; payoff at expiry is based on the month/quarter-end
  spot. Hedge P&L is added to the baseline equity path. Taxes: 31.4% on positive
  hedge P&L, no deduction for premium (conservative), and the baseline effective
  tax ratio is applied to the adjusted path. This is an approximation and is
  reported as `after-tax (model)`.
- Variants: H1 3m 90/70 put spread, 2.5x NAV; H2 3m 90/70 put spread, 1.5%
  NAV/yr budget; H3 zero-cost 3m 90/115 collar, 2.5x NAV; H4 1m 95/85 put
  spread, 1.5% NAV/yr budget.
- Pass: after-tax multiple >= 90% of baseline, maxDD at least 10 pp better, and
  bootstrap p5 at least 20% better. If the premium drag is not paid back in
  compounding, reject.

## Max-growth acceptance (pre-registered)

A variant replaces the max-growth default only if all hold:

1. After-tax multiple >= 57.01x, or >= 54.16x (95%) with p5 at least 20% higher
   and maxDD at least 10 pp better.
2. maxDD no worse than -67.8%.
3. 2018 no worse than -19.3% and 2022 no worse than -58.6%; at least one of the
   two improves by >= 3 pp.
4. Bootstrap after-tax p5 >= 1.93x.
5. It beats its leverage-matched control on after-tax multiple or p5.
6. Neither half (2015-2019, 2020-2026) loses more than 2 pp of CAGR versus the
   baseline half.
7. Neighbouring pre-registered parameter values point the same way; no
   knife-edge. If the primary fails but a neighbour wins, the family is rejected.

If a family fails, all its variants stay in the report and the default does not
change.

## Amendments before the main sweep

These are dated before the full v3 table, but after small design probes. They are
recorded so the main table cannot silently move a goalpost.

1. **Comparison direction.** "maxDD <= -50%" in the balanced gates means the
   drawdown is no worse than -50%, i.e. `maxdd >= -0.50`. The same reading applies
   to "no worse than -67.8%" in the max-growth gates. All verdicts use the
   numeric `maxdd` and the baseline value explicitly.
2. **V7 target 40% is post-hoc.** V1-V4 were the pre-registered vol-target
   variants (20/25/30/35%). The 35% variant nearly passes the balanced return
   gate (19.1x) and the 40% neighbour is materially better (27.5x, -44.8% DD).
   V7 is added as a **post-hoc neighbour** for the balanced profile only. It is
   not eligible to replace max_growth, and the report includes the full 35-50%
   neighbourhood so the reader can see whether it is a plateau. Primary V3
   (30%) remains the family's pre-registered representative.
3. **H3 collar is a model failure, not an economic result.** The short call is
   not covered when the daily 200-day cut moves the book to cash; a standalone
   collar cash-flow model can therefore go negative. It is reported as
   unimplementable with this engine, not as evidence for or against collars.

## Verdict labels

`accept` = max-growth gates pass. `balanced` = opt-in balanced gates pass, default
unchanged. `paper` = promising but not enough evidence. `reject` = fails a gate.
Every variant gets a label; failures are kept.

## Trial count

- Strategy variants: 1 baseline + 6 V + 3 D + 3 R + 5 A + 4 H = 22.
- Diagnostic runs: 6 leverage-matched V controls + 1 ETF-trend benchmark +
  1 cost-sensitivity baseline + finalist cost reruns. Reported separately.
- No post-hoc parameter sweep will be used to rescue a failed family. If a
  diagnostic is added after the main table, it is labelled post-hoc.

---

# Results

(To be appended after the runs. Pre-registration commit is recorded in the PR.)

## Exact commands

```bash
uv run python scripts/risk_v3.py --baseline
uv run python scripts/risk_v3.py
uv run python scripts/risk_v3.py --costs
uv run python tests/test_risk_v3.py
```
