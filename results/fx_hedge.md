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

(To be appended after the runs.)

## Reproduce

```bash
uv run python scripts/fx_hedge.py
uv run python tests/test_fx_hedge.py
uv run python tests/test_eur_ledger.py
```
