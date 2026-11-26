# EUR/USD hedge study: pre-registered

Branch `v4/fx-hedge`, based on master `9255b1b`. This file is written before
the hedge sweep. Results are appended below; the pre-registration is not edited
retrospectively.

## Question

The US monthly, TECH monthly and balanced A6 books are USD assets held by a
EUR investor. Does selling USD forward improve the real-life EUR path after
carry, roll cost and French CTO tax? Or is the unhedged path better because
USD strength has been a tailwind?

## Accounting

- `bot/fx_hedge.py` provides the forward/carry/P&L primitives.
- `bot/eur_ledger.replay(..., fx_hedge=FxHedgeConfig(...))` integrates the hedge
  into the same EUR account as the equity lots.
- At each month-end close `t`, the hedge notional is `ratio * USD gross position
  value` measured at `t`. The forward rate is fixed at
  `F = S_t * (1 - carry_bps/1e4 * days/360)`, where `S` is EUR per USD.
- The forward settles at the next month-end close at `S_{t+1}`. P&L is
  `notional_usd * (F - S_{t+1})` EUR. A USD-forward sale gains when the USD
  weakens (EUR per USD falls).
- Roll cost is `notional_usd * S_t * roll_bps/1e4` EUR per roll, paid at the
  roll.
- Hedge P&L and roll cost enter the same annual CTO capital-gains bucket as the
  equity lots: gains are taxed at the year-dependent PFU, losses reduce the
  year's net gain and carry forward 10 years. The roll cost is treated as a
  deductible transaction cost at settlement. This is the CTO-consistent
  treatment; it is not a separate FX tax regime.
- Carry is a **constant annual 200 bps** (USD minus EUR rate, approximate). No
  rate-curve or cross-currency-basis data is in the committed cache. Sensitivity
  at 0/400 bps is reported.
- Base roll cost is **5 bps per monthly roll** (≈60 bps/yr at 100% notional).
  Sensitivity at 2/10 bps is reported.
- No look-ahead: the ratio and notional use closes through `t`; the forward
  settles at the next month-end. Threshold mode uses only the trailing 200-day
  FX MA.

## Books

1. **US monthly**, 2.5x, SMH core, daily 200d cut, IBKR CTO.
2. **TECH monthly**, same rules.
3. **Balanced A6**, 70% US monthly book + 30% SPY, total gross 2.05x. This is a
   `paper` risk profile from v3, included because it is the realistic balanced
   path, not because it is the default.

## Variants

- Fixed hedge ratios **0/25/50/75/100%**, roll 5 bps, carry 200 bps, monthly.
- Full hedge at roll **2/5/10 bps**.
- Full hedge at carry **0/200/400 bps**.
- Threshold: high ratio 100% when EUR-per-USD is below its 200-day MA (USD weaker
  than usual / EUR stronger), low ratio 25% or 50%, roll 5 bps, carry 200 bps.
- 2x-cost sensitivity: unhedged and 100% hedge with doubled ledger fees
  (spread/fx/broker/ticket) and doubled roll cost.
- 0% and 100% are the exact boundaries; 0% must reproduce the unhedged ledger.

## Metrics

For every variant, on the EUR ledger:

- after-tax terminal multiple, CAGR, annualized vol, maxDD;
- 2018 and 2022 calendar returns, worst rolling 12m;
- block bootstrap p5/median terminal (10-day blocks, 2,000 paths, seed 7) and
  p5 maxDD, with the after-tax proportional adjustment;
- 5-year rolling windows: median, worst, share positive, share beating the
  unhedged book; and the median hedge-minus-unhedged difference;
- hedge P&L, roll cost, number of periods, average hedged notional.

## Acceptance (pre-registered)

A hedge ratio is accepted as a default risk rule only if all hold:

1. EUR maxDD improves by at least 3 pp **or** 2022 improves by at least 3 pp.
2. After-tax terminal multiple is at least 90% of the unhedged book.
3. Bootstrap after-tax p5 is no worse than unhedged.
4. The median 5-year return is at least 95% of unhedged and the hedge beats
   unhedged in at least 40% of 5-year windows.
5. At 2x costs, after-tax terminal is at least 85% of unhedged.
6. The result is not driven by a single year or a single path; neighbouring
   ratios point the same way.

If no ratio passes, the verdict is **reject** and the default stays unhedged.
The full hedge is the control, not an automatic recommendation. All failures are
reported.

## Trial count

- 3 books × 5 fixed ratios = 15 base variants.
- 3 books × 2 extra roll costs = 6.
- 3 books × 2 extra carry levels = 6.
- 3 books × 2 threshold variants = 6.
- 3 books × 2 2x-cost runs = 6.
- Total: 39 ledger runs, including the 0% unhedged baseline in the ratio matrix.
  No post-hoc parameter sweep will be used to rescue a failed ratio.

---

# Results

Run on branch `v4/fx-hedge` after merging master `9255b1b`. All numbers are the
EUR ledger with cached dividend events where available. Base carry 200 bps,
base roll 5 bps, 5 bps spread + 2 bps FX + IBKR tickets. The unhedged row is the
0% boundary and reproduces the current EUR ledger (US 66.17x, TECH 52.98x,
balanced 36.56x).

## US monthly

| variant | after-tax | CAGR | vol | maxDD | 2018 | 2022 | p5 | 5y median | 5y beat | hedge P&L | roll cost | 2x after |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| unhedged | 66.17x | 42.3% | 63.6% | -56.9% | -31.1% | -33.7% | 2.53x | 627.0% | 0% | €0 | €0 | 51.99x |
| fixed 25% | 60.97x | 41.4% | 65.3% | -58.7% | -32.8% | -37.0% | 2.18x | 615.0% | 22% | €-65,182 | €11,940 | n/a |
| fixed 50% | 55.77x | 40.3% | 67.3% | -60.5% | -34.4% | -40.4% | 1.86x | 614.3% | 22% | €-130,364 | €23,881 | n/a |
| fixed 75% | 50.58x | 39.1% | 69.8% | -63.5% | -36.0% | -44.1% | 1.50x | 612.1% | 22% | €-195,546 | €35,821 | n/a |
| fixed 100% | 45.38x | 37.9% | 72.7% | -67.0% | -37.6% | -48.0% | 1.10x | 591.6% | 22% | €-260,728 | €47,762 | 33.97x |
| full roll 2bps | 46.48x | 38.2% | 71.7% | -66.4% | -36.7% | -47.1% | 1.17x | 590.6% | 23% | €-260,728 | €19,105 | n/a |
| full roll 10bps | 43.54x | 37.4% | 74.7% | -68.0% | -39.3% | -49.6% | 0.96x | 597.5% | 24% | €-260,728 | €95,524 | n/a |
| full carry 0bps | 51.53x | 39.4% | 67.4% | -63.8% | -32.7% | -42.9% | 1.68x | 586.5% | 25% | €-180,366 | €47,762 | n/a |
| full carry 400bps | 39.22x | 36.2% | 79.5% | -70.4% | -43.9% | -53.8% | 0.64x | 587.1% | 29% | €-341,090 | €47,762 | n/a |
| threshold 25%/100% | 56.24x | 40.4% | 67.7% | -62.6% | -33.6% | -37.0% | 2.02x | 592.6% | 19% | €-119,781 | €27,195 | n/a |
| threshold 50%/100% | 52.62x | 39.6% | 69.1% | -63.2% | -34.9% | -40.5% | 1.65x | 604.2% | 19% | €-166,763 | €34,050 | n/a |
| fixed 0% 2x | 51.99x | 39.5% | 62.8% | -57.5% | -33.2% | -34.4% | 2.00x | 555.4% | 0% | €0 | €0 | n/a |
| fixed 100% 2x | 33.97x | 34.6% | 74.1% | -68.2% | -41.5% | -50.4% | 0.76x | 530.5% | 2% | €-206,577 | €76,547 | n/a |

US unhedged makes 66.17x. A 25% hedge already loses 8% of terminal wealth and
2.6 pp of drawdown; 100% loses 31% of terminal wealth, 10 pp of drawdown, and
cuts p5 from 2.53x to 1.10x. Even zero carry leaves the full hedge at 51.53x,
still below unhedged, and 2 bps rolls only reach 46.48x. The 5-year hedge beats
unhedged in 22% of windows, far below the 40% gate.

## TECH monthly

| variant | after-tax | CAGR | vol | maxDD | 2018 | 2022 | p5 | 5y median | 5y beat | hedge P&L | roll cost | 2x after |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| unhedged | 52.98x | 39.7% | 63.6% | -65.6% | -10.5% | -52.4% | 1.96x | 512.5% | 0% | €0 | €0 | 42.17x |
| fixed 25% | 48.68x | 38.7% | 65.5% | -67.8% | -12.1% | -56.5% | 1.56x | 478.3% | 19% | €-52,557 | €10,000 | n/a |
| fixed 50% | 44.39x | 37.6% | 68.0% | -71.3% | -13.7% | -60.7% | 1.32x | 453.0% | 18% | €-105,114 | €20,001 | n/a |
| fixed 75% | 40.09x | 36.4% | 71.1% | -75.3% | -15.3% | -65.3% | 0.98x | 439.1% | 18% | €-157,670 | €30,001 | n/a |
| fixed 100% | 35.79x | 35.2% | 75.1% | -79.5% | -16.9% | -70.1% | 0.69x | 419.4% | 18% | €-210,227 | €40,002 | 26.97x |
| full roll 2bps | 36.74x | 35.4% | 73.7% | -78.6% | -16.1% | -69.0% | 0.77x | 422.9% | 19% | €-210,227 | €16,001 | n/a |
| full roll 10bps | 34.21x | 34.6% | 77.5% | -81.1% | -18.2% | -72.1% | 0.59x | 413.4% | 17% | €-210,227 | €80,003 | n/a |
| full carry 0bps | 41.08x | 36.7% | 68.5% | -74.6% | -12.4% | -63.9% | 1.17x | 437.7% | 23% | €-142,859 | €40,002 | n/a |
| full carry 400bps | 30.50x | 33.3% | 84.0% | -85.0% | -21.8% | -77.2% | 0.36x | 399.5% | 14% | €-277,595 | €40,002 | n/a |
| threshold 25%/100% | 44.72x | 37.7% | 68.0% | -71.9% | -12.8% | -57.6% | 1.36x | 448.0% | 18% | €-97,557 | €22,831 | n/a |
| threshold 50%/100% | 41.74x | 36.9% | 69.9% | -73.0% | -14.1% | -61.6% | 1.11x | 442.5% | 17% | €-135,113 | €28,555 | n/a |
| fixed 0% 2x | 42.17x | 37.0% | 62.8% | -65.7% | -13.0% | -52.6% | 1.64x | 452.5% | 0% | €0 | €0 | n/a |
| fixed 100% 2x | 26.97x | 32.0% | 77.0% | -81.2% | -20.5% | -72.3% | 0.47x | 361.7% | 1% | €-169,107 | €65,243 | n/a |

TECH is the same story with a bigger 2022 loss: full hedge takes 2022 from
-52.4% to -70.1% and p5 from 1.96x to 0.69x. The hedge removed the USD-strength
cushion exactly when it was most valuable.

## Balanced A6

| variant | after-tax | CAGR | vol | maxDD | 2018 | 2022 | p5 | 5y median | 5y beat | hedge P&L | roll cost | 2x after |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| unhedged | 36.56x | 35.4% | 49.6% | -46.9% | -19.5% | -27.6% | 2.87x | 442.9% | 0% | €0 | €0 | 31.96x |
| fixed 25% | 34.14x | 34.6% | 50.8% | -48.0% | -21.1% | -30.4% | 2.58x | 433.5% | 19% | €-29,763 | €6,291 | n/a |
| fixed 50% | 31.70x | 33.8% | 52.4% | -50.0% | -22.6% | -33.3% | 2.28x | 418.5% | 19% | €-59,526 | €12,582 | n/a |
| fixed 75% | 29.25x | 32.9% | 54.2% | -53.4% | -24.2% | -36.4% | 1.95x | 419.5% | 19% | €-89,289 | €18,874 | n/a |
| fixed 100% | 26.81x | 31.9% | 56.4% | -56.8% | -25.9% | -39.7% | 1.56x | 420.5% | 19% | €-119,052 | €25,165 | 22.48x |
| full roll 2bps | 27.40x | 32.1% | 55.6% | -56.2% | -25.2% | -38.9% | 1.67x | 424.6% | 20% | €-119,052 | €10,066 | n/a |
| full roll 10bps | 25.82x | 31.5% | 57.7% | -57.8% | -27.1% | -41.1% | 1.38x | 413.6% | 16% | €-119,052 | €50,330 | n/a |
| full carry 0bps | 30.12x | 33.2% | 52.6% | -53.4% | -22.0% | -35.3% | 2.18x | 442.3% | 25% | €-76,700 | €25,165 | n/a |
| full carry 400bps | 23.50x | 30.4% | 61.0% | -60.5% | -30.4% | -44.7% | 1.05x | 397.1% | 12% | €-161,404 | €25,165 | n/a |
| threshold 25%/100% | 31.75x | 33.8% | 52.8% | -49.7% | -22.6% | -30.2% | 2.48x | 421.1% | 19% | €-56,500 | €14,812 | n/a |
| threshold 50%/100% | 30.11x | 33.2% | 53.8% | -51.3% | -23.7% | -33.2% | 2.18x | 411.3% | 18% | €-77,350 | €18,263 | n/a |
| fixed 0% 2x | 31.96x | 33.9% | 49.2% | -46.4% | -21.1% | -28.3% | 2.58x | 412.7% | 0% | €0 | €0 | n/a |
| fixed 100% 2x | 22.48x | 30.0% | 57.4% | -58.0% | -28.5% | -41.8% | 1.21x | 390.0% | 5% | €-104,816 | €44,653 | n/a |

The balanced book behaves the same way. Full hedge 26.81x vs 36.56x unhedged,
maxDD -56.8% vs -46.9%, p5 1.56x vs 2.87x. The threshold variants do not rescue
it: 25%/100% is 31.75x with -49.7% DD and p5 2.48x.

## Verdict

**Reject. Keep the unhedged EUR path.** No fixed ratio, threshold, carry or roll
variant passes the pre-registered acceptance gates. Hedging reduces terminal
wealth in every book, raises annualized volatility, deepens maxDD and lowers the
bootstrap p5. The 5-year hedge beats unhedged in only 17-29% of windows, below
the 40% gate. At 2x costs the full hedge falls to 33.97x US, 26.97x TECH and
22.48x balanced, all below the 85% terminal gate.

The economic reason is visible in the stress years: the USD is a risk-off
asset for a EUR investor. In 2022 the unhedged US book lost 33.7% while the full
hedge lost 48.0%; in TECH, -52.4% became -70.1%. EUR weakness cushioned the USD
equity drawdown. Hedging removes that natural diversification and pays a positive
carry/roll cost for the privilege. Even zero carry (51.53x US) and 2 bps rolls
(46.48x US) do not close the gap.

The full hedge is the control, not a recommendation. The default remains
unhedged. The code and negative result are kept so this is not retried as a free
lunch.

## Trial count

39 ledger runs: 15 fixed-ratio base variants (3 books × 5 ratios), 6 extra roll
costs, 6 extra carry levels, 6 threshold variants and 6 2x-cost runs. All are in
`results/fx_hedge.csv`; the markdown tables are generated by the script. The
0% row is the unhedged baseline in each book.

## Caveats

- Carry is a constant 200 bps, not a rate curve; no cross-currency basis data is
  in the cache. 0/400 bps sensitivity is reported.
- Hedge notional is gross USD position value, consistent with a EUR-funded
  margin account; a USD-funded account would have a different net exposure.
- Month-end rolls only; no intra-month option or stop. Forward P&L is realized at
  the next month-end.
- Hedge P&L and roll cost are netted in the annual CTO capital-gains bucket;
  roll-cost deduction timing across a year boundary is approximate.
- Dividends use cached events where available; the base EUR ledger already
  includes the dividend overlay. This does not change the verdict.

## Reproduce

```bash
uv run python scripts/fx_hedge.py
uv run python tests/test_fx_hedge.py
uv run python tests/test_eur_ledger.py
```
