# Risk v3: survivable compounding, pre-registered

Branch `v2/risk-alloc`, base `93b39c4`. This file is written **before** the v3
sweep runs. The pre-registration is not edited retrospectively; results are
appended below the line.

## Primary/secondary pivot (orchestrator instruction, before the main sweep)

The main decision book is now the **US monthly** book, not TECH. The US book has a
higher after-tax return and a materially lower drawdown. TECH is the secondary
check. Numbers are reported in **EUR-ledger** terms (`bot/eur_ledger.py`, PR #22)
where feasible, because a terminal-FX headline can flatter a rule.

| book | headline after tax | EUR-ledger after tax | EUR maxDD | EUR 2018 | EUR 2022 | EUR CAGR |
|---|---:|---:|---:|---:|---:|---:|
| **US monthly (primary)** | 70.856x | **66.721x** | **-56.67%** | -30.58% | -33.16% | 41.5% |
| TECH monthly (secondary) | 57.013x | 53.450x | -65.27% | -10.58% | -51.64% | 38.9% |

US baseline also: pre-tax 84.275x, EUR bootstrap net p5/median 2.53x/60.56x,
p5 maxDD -89.7%, EUR worst rolling 12m -48.41% (ending 2019-01-29), EUR
2015-2019 2.20x / 17.1% after-tax CAGR / -55.8% DD, EUR 2020-2026 20.61x / 55.2%
after-tax CAGR / -56.7% DD, 1,692 trades, 15.5x annual turnover. The EUR ledger now uses the year-dependent French rate (30% through
2025, 31.4% from 2026), merged from master `d6491d1`.

### Dividend-tax amendment (before the main sweep)

Adjusted closes reinvest dividends gross, so the EUR-ledger numbers above still
omit French PFU on distributions. The runner subtracts an explicit daily drag on
gross weights: SMH 1.0%, SPY 1.3%, QQQ 0.6%, TLT 3.8%, IEF 2.8%, BIL 4.5%,
GLD 0%, other equities 1.2% default, all at a flat 31.4%. This is a modelled
yield, not a point-in-time dividend history; it is deliberately harsh on bond
and cash sleeves. The 31.4% is the 2026 rate applied to every year, so the drag
is conservative relative to the year-dependent capital-gains rate.

| book | EUR after tax, gross-div | dividend drag | **EUR after tax, net** | net maxDD | net p5 |
|---|---:|---:|---:|---:|---:|
| **US monthly (primary)** | 66.721x | 67 bps/yr | **61.643x** | **-56.67%** | **2.53x** |
| TECH monthly (secondary) | 53.450x | 67 bps/yr | 49.333x | -65.27% | 1.91x |

The primary baseline is **61.643x, -56.67% maxDD, 2.53x p5, 17.1% / 55.2%
after-tax half CAGRs**. The gross-div 66.72x is retained only for reconciliation
with PR #22. All decision gates below use the net-of-dividend-tax baseline.

The original TECH baseline below is retained as the secondary reproduction and as
the continuation of the pre-registered TECH table.

## Baseline (reproduced first, TECH secondary)

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
- Primary book: **US monthly** at IBKR, French CTO, after-tax (PFU + exit tax),
  EUR-ledger accounting. Secondary: TECH monthly, same tax wrapper. A rule that
  only works in one book or one tax regime is rejected. A US tax-profile check
  (37% federal, 10 bps, no exit tax) is a third view, not the decision metric.
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

- A1: 60% **primary book** + 40% SPY, fixed, total target gross 1.9.
- A2: 60% **primary book** + 40% cross-asset trend sleeve, fixed, total gross 1.9.
- A3: 50% **primary book** + 50% cross-asset trend sleeve, total gross 1.75.
- A4: dynamic vol-parity primary/ETF-trend weights, 63-day realized vol of each
  standalone sleeve, weight cap 30-80%, rebalanced monthly; total gross
  `w*2.5 + (1-w)*1.0`.
- A5: 70% **primary book** + 30% cross-asset trend sleeve, total gross 2.05.
- A6 (post-hoc balanced neighbour): 70% **primary book** + 30% SPY, total gross
  2.05. A1's 60/40 and A6's 70/30 are the two ends of the balanced trade-off;
  neither was selected by a full-sample parameter sweep. A6 is not eligible for
  max-growth adoption.
- All combined books run inside one engine/account so French tax and costs are
  account-level, not a synthetic blend. The custom selector scales the TECH and
  ETF sleeve targets; `cfg.leverage` is 2.5 and the selector returns
  `target_gross / 2.5`.
- The standalone ETF trend sleeve (E0) is reported as a benchmark and is a
  diagnostic, not a candidate profile.
- Balanced gates (opt-in profile, not default, **US primary, net dividends**):
  1. EUR maxDD no worse than -47% (9.7 pp better than the -56.67% US baseline);
  2. EUR after-tax multiple >= 28x (at least 45% of the 61.64x baseline);
  3. bootstrap after-tax p5 >= 2.53x (no worse than the US baseline);
  4. EUR 2018 and 2022 both better than the US baseline (-30.58%, -33.16%);
  5. each half (2015-2019, 2020-2026) after-tax CAGR >= 10% **and** at least
     50% of the baseline half CAGR (8.56% and 27.60%); half maxDD no worse than
     -50%. The original 20% absolute gate is withdrawn: it would reject every
     lower-risk profile, including the baseline's own 17.1% first half.
  6. 2x-cost EUR after-tax multiple >= 25x.
- The balanced profile is the passing candidate with the highest EUR after-tax
  multiple; ties go to the lower maxDD. The pre-registered fallbacks are A1
  (60% US book / 40% SPY) and a lower-gross US book; a vol-target variant may
  compete only if it clears all six gates.
- TECH secondary balanced gate (for comparison only): EUR maxDD no worse than
  -55%, EUR after-tax >= 25x, p5 >= 1.91x, 2018/2022 better than the TECH
  baseline.

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

## Max-growth acceptance (pre-registered, US primary, net dividends)

A variant replaces the max-growth default only if all hold:

1. EUR after-tax multiple >= 61.64x, or >= 58.56x (95%) with p5 at least 20%
   higher and EUR maxDD at least 10 pp better.
2. EUR maxDD no worse than -56.67%.
3. EUR 2018 no worse than -32.58% and 2022 no worse than -35.16%; at least one
   of the two improves by >= 3 pp.
4. Bootstrap EUR after-tax p5 >= 2.53x.
5. It beats its leverage-matched control on EUR after-tax multiple or p5.
6. Neither half (2015-2019, 2020-2026) loses more than 2 pp of CAGR versus the
   US baseline halves (17.1%, 55.2%).
7. Neighbouring pre-registered parameter values point the same way; no
   knife-edge. If the primary fails but a neighbour wins, the family is rejected.

TECH secondary max-growth gates (net dividends): EUR after-tax >= 49.33x, maxDD
no worse than -65.27%, 2018 no worse than -12.58%, 2022 no worse than -53.64%,
p5 >= 1.91x, beats the matched control, and neither half loses more than 2 pp of
CAGR.

If a family fails, all its variants stay in the report and the default does not
change.

## Amendments before the main sweep

These are dated before the full v3 table, but after small design probes. They are
recorded so the main table cannot silently move a goalpost.

1. **Comparison direction.** "maxDD <= -50%" in the balanced gates means the
   drawdown is no worse than -50%, i.e. `maxdd >= -0.50`. The same reading applies
   to "no worse than -67.8%" in the max-growth gates. All verdicts use the
   numeric `maxdd` and the baseline value explicitly.
2. **V7-V9 targets 40/45/50% are post-hoc.** V1-V4 were the pre-registered
   vol-target variants (20/25/30/35%). The design probe put V4 at 19.1x and the
   40% neighbour at 27.5x, so the 40-50% neighbourhood was added and labelled
   post-hoc for the balanced-profile trade-off. The final table supersedes the
   probe numbers (V4 is 15.52x, V8 29.78x). Primary V3 (30%) remains the
   family's pre-registered representative; none of V7-V9 is max-growth
   eligible.
3. **H3 collar is a model failure, not an economic result.** The short call is
   not covered when the daily 200-day cut moves the book to cash; a standalone
   collar cash-flow model can therefore go negative. It is reported as
   unimplementable with this engine, not as evidence for or against collars.

## Verdict labels

`accept` = max-growth gates pass. `balanced` = opt-in balanced gates pass, default
unchanged. `paper` = promising but not enough evidence. `reject` = fails a gate.
Every variant gets a label; failures are kept.

## Trial count

- US primary runs: 30 = 1 baseline + 1 ETF sleeve + 9 V (V1-V6 pre-registered;
  V7-V9 post-hoc) + 3 D + 3 R + 6 A (A1-A5 pre-registered, A6 post-hoc) +
  2 B + 2 T (250/300d neighbours) + 4 H. TECH secondary repeats 29 of them.
- Diagnostic runs: one constant-leverage control for every V/D/R/A/T rule,
  plus the full 10 bps rerun (`--costs`). Controls and cost reruns are written
  to `results/risk_v3_main.csv` and `results/risk_v3_costs.csv`; they are not
  counted as independent strategy trials.
- No post-hoc parameter sweep was used to rescue a failed family. V7-V9, A6,
  T250/T300 and the lower-gross B ladder are labelled post-hoc/neighbour
  diagnostics in the tables.

---

# Results

Run on branch `v2/risk-alloc`, after merging master `d6491d1` (year-dependent
French EUR-ledger tax). Primary book: US monthly at IBKR, French CTO, EUR ledger,
net of the modelled dividend tax. Secondary: TECH. The tables below are generated
from `results/risk_v3_main.csv` and `results/risk_v3_costs.csv` by the committed
runner; no number is hand-entered.

## Baselines

| book | headline | EUR ledger | dividend drag | **EUR net decision** | net maxDD | net p5 | 2018 | 2022 | worst 12m |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **US primary** | 70.86x | 66.72x | 67 bps/yr | **61.64x** | **-56.67%** | **2.53x** | -30.58% | -33.16% | -48.41% |
| TECH secondary | 57.01x | 53.45x | 67 bps/yr | 49.33x | -65.27% | 1.91x | -10.58% | -51.64% | -63.26% |

`--baseline` asserts both net multiples and both net maxDDs. The dividend drag is
a flat 31.4% on gross distributions, so it is conservative for 2018-2025; see the
caveat below. Half-window after-tax CAGRs: US 17.1% / 55.2%, TECH 21.1% / 46.4%.

## US primary, 5 bps

| variant | net after-tax | CAGR | maxDD | 2018 | 2022 | worst12 | p5 | avg gross | 2x net | gates |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|:---|
| baseline | 61.64x | 41.5% | -56.7% | -30.6% | -33.2% | -48.4% | 2.53x | 2.04 | 55.12x | - |
| E0 ETF trend | 1.20x | 1.5% | -18.2% | +2.2% | -2.5% | -17.3% | 0.75x | 0.96 | 1.17x | - |
| V1 vol 20% | 3.87x | 12.1% | -24.7% | -8.4% | -7.1% | -20.9% | 1.47x | 0.64 | 3.76x | - |
| V2 vol 25% | 6.60x | 17.2% | -29.6% | -9.6% | -8.9% | -25.4% | 1.81x | 0.81 | 6.34x | - |
| V3 vol 30% | 9.90x | 21.3% | -35.4% | -12.8% | -10.7% | -30.6% | 2.10x | 1.00 | 9.42x | - |
| V4 vol 35% | 15.52x | 26.0% | -41.1% | -15.0% | -10.6% | -34.9% | 2.38x | 1.20 | 14.62x | - |
| V5 vol 30% band 5% | 9.84x | 21.2% | -36.7% | -12.5% | -10.8% | -30.4% | 2.01x | 1.00 | 9.36x | - |
| V6 vol 30% hyst 0% | 11.09x | 22.5% | -35.4% | -12.8% | -10.6% | -30.6% | 2.24x | 1.02 | 10.54x | - |
| V7 vol 40% (post-hoc) | 22.48x | 30.0% | -44.5% | -18.3% | -12.2% | -38.9% | 2.61x | 1.37 | 20.95x | - |
| **V8 vol 45% (post-hoc)** | **29.78x** | 33.1% | **-46.1%** | -18.8% | **-13.7%** | -40.1% | **2.90x** | 1.50 | **27.53x** | BAL |
| V9 vol 50% (post-hoc) | 39.59x | 36.3% | -47.9% | -23.3% | -15.9% | -43.1% | 3.34x | 1.62 | 36.36x | - |
| D1 dd -25% | 27.31x | 32.1% | -53.7% | -28.8% | -25.5% | -46.1% | 1.63x | 1.80 | 25.68x | - |
| D2 dd -30% | 20.85x | 29.1% | -57.5% | -25.3% | -27.6% | -43.8% | 1.13x | 1.81 | 18.53x | - |
| D3 dd -35% | 32.79x | 34.2% | -57.6% | -30.6% | -28.7% | -46.2% | 1.71x | 1.87 | 31.47x | - |
| R1 SPY<200d | 29.75x | 33.1% | -56.9% | -34.0% | -35.7% | -51.9% | 1.48x | 1.93 | 26.80x | - |
| R2 SPY+SMH<200d | 36.87x | 35.5% | -56.8% | -34.0% | -35.8% | -51.9% | 1.61x | 1.96 | 33.10x | - |
| R3 SMH 12m<=0 | 42.83x | 37.2% | -56.4% | -28.7% | -32.5% | -48.0% | 1.84x | 1.90 | 38.75x | - |
| A1 60% book/40% SPY | 25.21x | 31.2% | -43.0% | -16.4% | -25.2% | -38.7% | 2.61x | 1.61 | 23.70x | - |
| A2 60% book/40% trend | 18.65x | 27.9% | -41.1% | -15.9% | -22.9% | -37.3% | 2.36x | 1.61 | 17.44x | - |
| A3 50% book/50% trend | 12.26x | 23.5% | -37.1% | -12.9% | -20.2% | -34.2% | 2.07x | 1.49 | 11.55x | - |
| A5 70% book/30% trend | 27.46x | 32.2% | -45.1% | -19.0% | -25.7% | -40.1% | 2.56x | 1.74 | 25.46x | - |
| **A6 70% book/30% SPY (post-hoc)** | **34.36x** | 34.7% | **-46.0%** | -19.4% | -27.4% | -41.0% | **2.79x** | 1.74 | **31.99x** | BAL |
| A4 dynamic 30-80% | 5.06x | 14.6% | -28.1% | -7.4% | -14.6% | -26.5% | 1.56x | 1.25 | 4.84x | - |
| B1 US 2.0x | 36.70x | 35.4% | -49.1% | -22.4% | -26.8% | -40.8% | 2.72x | 1.66 | 33.74x | - |
| B2 US 1.5x | 18.51x | 27.9% | -40.2% | -15.4% | -20.3% | -34.2% | 2.50x | 1.26 | 17.50x | - |
| T250d trend | 56.54x | 40.5% | -58.3% | -18.4% | -39.4% | -51.0% | 2.26x | 2.06 | 50.75x | - |
| T300d trend | 43.35x | 37.3% | -59.4% | -19.5% | -39.8% | -51.2% | 1.46x | 2.09 | 39.03x | - |

Gates are `ret/dd/stress-year/p5/halves/control`; a blank `-` means no gate
passed. No row in this table passes the max-growth gate. `BAL` marks the two
post-hoc balanced candidates that pass all six balanced gates.

## Leverage-matched controls

Every dynamic rule has a constant-leverage control with the same full-window
average gross. The full table is in `results/risk_v3_main.csv`.

| variant | net | control lev | control net | control p5 | verdict |
|---|---:|---:|---:|---:|---|
| V3 vol 30% | 9.90x | 1.23x | 12.08x | 2.36x | fails on control |
| V4 vol 35% | 15.52x | 1.47x | 17.58x | 2.48x | fails on control |
| V8 vol 45% | 29.78x | 1.84x | 29.97x | 2.66x | beats control on p5 only |
| D1 dd -25% | 27.31x | 2.21x | 46.18x | 2.67x | fails on control |
| R3 SMH 12m | 42.83x | 2.32x | 52.15x | 2.62x | fails on control |
| A1 60/40 SPY | 25.21x | 1.98x | 35.71x | 2.73x | fails on control |
| A6 70/30 SPY | 34.36x | 2.13x | 42.64x | 2.69x | beats control on p5 only |
| T250d trend | 56.54x | 2.52x | 62.85x | 2.51x | fails on control |

The control comparison is the honest test: V8 and A6 do not beat a constant
1.84x / 2.13x book on terminal wealth. They buy a better lower tail (p5) and a
lower drawdown, not more money. That is a risk preference, not an alpha claim.

## TECH secondary

| variant | net after-tax | CAGR | maxDD | 2018 | 2022 | p5 | 2x net |
|---|---:|---:|---:|---:|---:|---:|---:|
| baseline | 49.33x | 38.9% | -65.3% | -10.6% | -51.6% | 1.91x | 44.01x |
| V3 vol 30% | 9.55x | 20.9% | -34.2% | +1.6% | -17.7% | 2.00x | 9.06x |
| V4 vol 35% | 15.68x | 26.1% | -39.3% | +1.9% | -19.2% | 2.30x | 14.71x |
| V7 vol 40% | 22.63x | 30.0% | -42.0% | +0.4% | -21.7% | 2.53x | 20.98x |
| V8 vol 45% | 29.44x | 32.9% | -44.7% | +1.7% | -24.2% | 2.75x | 27.08x |
| D2 dd -30% | 17.67x | 27.4% | -61.9% | -15.8% | -47.5% | 1.09x | 17.50x |
| R1 SPY<200d | 37.70x | 35.7% | -63.2% | -6.2% | -40.3% | 1.64x | 33.85x |
| R3 SMH 12m | 38.16x | 35.9% | -62.8% | -6.2% | -46.6% | 1.56x | 34.30x |
| A1 60/40 SPY | 24.92x | 31.1% | -52.8% | -3.3% | -41.7% | 2.37x | 23.33x |
| A2 60/40 trend | 17.28x | 27.1% | -48.3% | -3.0% | -35.1% | 2.05x | 16.11x |
| A6 70/30 SPY | 32.24x | 34.0% | -56.2% | -4.3% | -44.5% | 2.40x | 29.89x |

TECH confirms the direction: vol targeting cuts the 2022 loss from -51.6% to
-13.7% at 45% target, but gives up most of the terminal wealth. No TECH rule
passes the TECH max-growth gate, and the TECH balanced gate is not the decision
metric. The US and TECH rankings agree, which is the useful part.

## Tail hedges (Black-Scholes model, no option quotes)

| variant | net after-tax | maxDD | p5 | 2018 | 2022 | premium | payoff | verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| H1 3m 90/70 2.5x | 75.28x | -80.8% | 0.43x | -48.8% | -35.4% | 35.03x | 70.94x | reject: worse DD, p5 0.43x |
| H2 3m 90/70 1.5% | 62.80x | -57.3% | 2.36x | -31.7% | -33.3% | 2.98x | 6.03x | reject: no DD/p5 improvement |
| H3 3m 90/115 collar | 108.65x | -108.1% | invalid | -20.0% | -20.6% | 1.66x | -27.66x | reject: unimplementable model |
| H4 1m 95/85 1.5% | 61.84x | -57.3% | 2.34x | -31.8% | -32.0% | 2.82x | 4.39x | reject: no improvement |

H1 adds terminal wealth but deepens the drawdown from -56.7% to -80.8% and cuts
p5 to 0.43x because the premium is charged when NAV is small. H2/H4 are
approximately flat after tax with slightly worse drawdown, lower p5, and worse
2018/2022. H3's standalone short call is not covered when the daily trend cut
moves the book to cash; the model can go negative, so it is a model failure, not
an economic rejection of collars. There are no option quotes in the cache; this
is not broker-accounted P&L.

## 2x-cost sensitivity (finalists)

| variant | 5 bps net | 10 bps net | 5 bps DD | 10 bps DD |
|---|---:|---:|---:|---:|
| baseline | 61.64x | 55.12x | -56.7% | -57.1% |
| V8 vol 45% | 29.78x | 27.53x | -46.1% | -46.6% |
| A1 60/40 SPY | 25.21x | 23.70x | -43.0% | -42.8% |
| A2 60/40 trend | 18.65x | 17.44x | -41.1% | -41.1% |
| A6 70/30 SPY | 34.36x | 31.99x | -46.0% | -45.8% |

A6 and V8 clear the 25x balanced 2x-cost gate; A1 does not. The baseline remains
the highest-return book at 2x costs but keeps the -57% drawdown.

## Verdicts

### Max growth: keep the baseline, `reject` every V/D/R/T/B/A candidate

No V/D/R/T/B/A variant passes the max-growth gates. The pre-registered V3
(30% vol target) is 9.90x with -35.4% DD: it is a legitimate lower-risk book, but
it gives up 84% of terminal wealth and fails the return gate. V4 is 15.52x;
V7-V9 are post-hoc neighbours and are not eligible for the default. The
drawdown ladders (D1 27.31x, D2 20.85x, D3 32.79x) all fail the return and/or
control gate; the 200-day monthly overlays (R1/R2/R3) reduce return without
cutting maxDD below -56%; T250/T300 are worse than 200d on 2022 (-39.4%/-39.8%
vs -33.2%) and maxDD, so **200d stays**. B1/B2 are leverage dials. A1/A2/A3/A5
fail return or 2x cost; A4 is far too low-return. Verdict: **reject** for
max-growth; the default US monthly 2.5x book is unchanged.

### Balanced: A6 and V8 pass, both `paper` (post-hoc, not default)

Two post-hoc neighbours pass all six balanced gates after net dividends and 2x
costs. The pre-registered A1 60/40 SPY fails the 28x return gate and the 25x
2x-cost gate. The exact configs:

- **A6 (preferred balanced, highest after-tax):** 70% US monthly max-growth book
  + 30% SPY, monthly rebalance, total target gross 2.05x, same IBKR/CTO/HIFO
  costs and daily SMH 200d cut at portfolio level. Net **34.36x**, CAGR 34.7%,
  maxDD **-46.0%**, p5 **2.79x**, 2018 -19.4%, 2022 -27.4%, 2x net **31.99x**.
  It beats its 2.13x constant-leverage control only on p5 (control 42.64x,
  2.69x p5), so it is a tail/drawdown improvement, not extra return.
- **V8 (lower-2022 alternative):** 45% annualized vol target on SMH, 20-day
  realized vol, gross clipped to [0.5, 2.5], 10% no-trade band, 10% hysteresis,
  0.5x max monthly step, applied to the US monthly book. Net **29.78x**, CAGR
  33.1%, maxDD **-46.1%**, p5 **2.90x**, 2018 -18.8%, 2022 **-13.7%**, 2x net
  **27.53x**. It also beats its 1.84x control only on p5.
- **A1 (conservative, fails gate):** 60% book / 40% SPY, net 25.21x, -43.0% DD,
  2x 23.70x. It is the lower-drawdown end of the same frontier but below the
  pre-registered 28x return and 25x 2x-cost gates.

The honest label is **paper**, not `accept`: both passing profiles were added
after the pre-registered V1-V4/A1-A5 set, and both beat the matched control only
on the lower tail. They are exact, implementable risk preferences, but they need
forward evidence before becoming a default.

### Multi-strategy allocator: `reject` the cross-asset trend sleeve

The ETF trend sleeve (E0) makes only 1.20x net over 12 years with a -18.2% DD;
it is too weak to carry a 40% allocation. A2/A3/A5 are 18.65x / 12.26x / 27.46x
with -41% to -45% DD, all below the simple SPY blend. The dynamic vol-parity A4
is 5.06x with -28.1% DD: lower risk, but far too little return. Verdict:
**reject** for the balanced profile; keep the code and the negative result so it
is not retried as a free lunch.

### Tail hedges: `reject` all four

H1/H3 fail the drawdown gate outright (H1 -80.8%, H3 -108.1% model-invalid).
H2/H4 do not improve maxDD or p5 and have worse 2018/2022 than the baseline. The
premium drag is not paid back in compounding. Reject.

### Dividend-tax caveat

The net numbers apply a flat 31.4% PFU to modelled gross distributions. This is
conservative for 2018-2025 (the capital-gains rate was 30%) and important for
bond/cash sleeves: TLT/IEF/BIL carry 2.8-4.5% yields, so A2/A3/A5/E0 after-tax
numbers would be overstated without the drag. The drag is a scalar per ticker,
not a point-in-time dividend history; it is not a substitute for a dividend
ledger. SPY-only blends have a smaller drag (~0.4-0.5%/yr on the blend) than
bond-heavy blends (~1.0-1.5%/yr).

### Multiple testing / deflation

The US sweep has 25 non-hedge strategy trials (V1-V9, D1-D3, R1-R3, A1-A6,
B1-B2, T250/T300) plus the baseline, E0 and four hedges. Using the 25 trial
daily Sharpes, the deflated-Sharpe benchmark is 0.092 annualized; A6's normal
DSR is 0.9972 and V8's is 0.9976 (no skew/kurtosis correction). This is a
multiple-testing note, not a promotion: the DSR is high because the candidates
are low-vol, and both are still post-hoc. The trial count includes the T250/T300
neighbours and labels V7-V9/A6 as post-hoc. V1-V6/D1-D3/R1-R3/A1-A5 were
pre-registered; no post-hoc sweep was used to rescue a failed family.

## Exact commands

```bash
uv run python scripts/risk_v3.py --baseline
uv run python scripts/risk_v3.py
uv run python scripts/risk_v3.py --costs
uv run python tests/test_risk_v3.py
```

`--baseline` asserts the US/TECH headline and net-of-dividend EUR multiples and
maxDDs. `--costs` reruns the full suite at 10 bps and writes
`results/risk_v3_costs.csv`; the main run reads it to fill the 2x-cost gate.
