# Short-horizon conclusion: no surviving edge

This file consolidates every short-horizon family tested. None clears the
after-tax hurdles (BIL, QQQ) at 2x costs. That is the result, not a missing test.

## Hurdles

- Beat BIL (cash) after tax.
- Beat QQQ buy-and-hold after tax over the same window.
- Survive 2x costs (10 bps/side or more).
- No single year/fold drives the result.
- Implementable in a French CTO (spread, FX, PFU).

## Families tested

| wave | family | data | best result | verdict |
|---|---|---|---|---|
| v1 | faster rebalancing of monthly book | daily 2015-2026 | weekly 23% CAGR, daily destroyed by costs/PFU | reject |
| v2 | 1/5-day reversal, 20-day momentum | daily 2015-2026 | OOS close-to-close 2.64x vs QQQ 5.49x | reject |
| v2 | overnight close-to-open | daily 2015-2026 | 1.01x optimistic; 0.82x all-in; 0.60x doubled | reject |
| v2 | covered calls | Black-Scholes model | 40% IV/10% OTM 75.96x vs 66.17x baseline; 25/30% IV negative | paper opt-in only |
| v7 | pairs: SMH/SOXX, QQQ/SPY, sector/SPY | daily 2015-2026 | OOS 1.88x vs QQQ 4.44x; fixed SMH/SOXX 3.49x (one pair) | reject |
| v7 | 8-K event drift | SEC 8-K cache | 5d +0.39% p=0.24; 20d +1.91% p=0.07 vs placebo | reject |
| v8 | 1h opening range, gap fade, VWAP, last-hour | Yahoo 1h, 2y | best SPY gap fade 1.04x vs QQQ 1.26x; 2x 0.92x | reject |
| v8 | 5m execution proxy | Yahoo 5m, 60d | tiny sample; best 1.05x | reject |
| v8 | daily open-to-close vs close-to-open | daily 2015-2026 | descriptive; O2C mean ~0; overnight already rejected | reject |
| v8 | volatility-scaled gap fade | daily 2015-2026 | best 0.93x after tax | reject |
| v8 | cross-sectional short-term reversal | daily 2015-2026 | best 2.22x vs QQQ 5.49x; 2x costs 1.84x | reject |
| v8 | VIX-conditioned gap fade | daily 2015-2026 | no regime edge | reject |
| v9 | intraday lead-lag / pairs / cross-section | 1h 2y, 5m 60d | best pair 1.053x vs QQQ 1.247x; 0.912x at 2x | reject |
| v9 | intraday 8-K event drift | 1h 2y, 5m 60d | best 5d long 2.704x but placebo p=0.229; 5m n=29 | reject |
| v9 | sector cross-sectional reversal | daily walk-forward 2019-2026 | 1.29x vs QQQ 2.80x; 1.12x at 2x | reject |
| v9 | calendar/seasonality | daily 2019-2026 | 1.01x; below BIL at 2x | reject |
| v9 | volatility breakout | daily 2019-2026 | 0.91x; 2/8 positive folds | reject |

## Why the result is credible

- Actual intraday data was used (Yahoo 1h 2y and 5m 60d, 09:30-16:00 ET).
- Signals use only completed bars; entry is the next bar open.
- Costs are 5 bps/side base and 10 bps 2x; French PFU is modeled.
- Pairs/events use placebos/walk-forward; intraday sample limits are stated.
- Every variant and failed fold is in the trial registries:
  `results/alpha_trials.csv`, `results/short2_trials.csv`, `results/short3.md`.

## What would be needed to revisit

- A paid intraday/MBO dataset with several years of history and auction data.
- A market-making/execution edge with sub-bp spreads and queue position.
- A funding/borrow model for neutral pairs.
- Real forward paper evidence at 5/5 before any allocation.

Until then: **no short-horizon satellite**. The monthly US book remains the only
production strategy. This is a negative result, deliberately kept.
