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
| v11 | earnings 8-K 2.02 intraday continuation/fade | 2.02 timestamps + 1h 2y, 5m 60d | fixed-slot best 1.149x vs QQQ 1.170x; full-capital gap fade 1.190x but 2026 -0.7%, p=0.035 | reject |
| v11 | 8-K 2.02 PEAD (positive gap, 5d) | SEC 8-K cache + daily bars | 2.73x after tax vs QQQ 5.94x; 2.45x at 2x; placebo p=0.002, DSR 0.950 | reject |

v11 isolated earnings releases (8-K item 2.02) and used SEC acceptance
timestamps, not filing dates. The daily PEAD primary does show event-specific
drift: 1,756 positive-gap events, +0.73% net mean per five sessions, placebo
p=0.002, and a higher Sharpe than QQQ (1.16 vs 0.87) with a smaller
drawdown. It still fails the pre-registered QQQ terminal hurdle after tax
(2.73x vs 5.94x; 2.45x vs 5.94x at 2x costs), and no registered secondary
clears it. The event edge is real in this current-filer sample but too
small and too under-invested to beat buy-and-hold QQQ; survivorship in the
86-ticker cache is a further reason not to rescue it. Verdict: reject.

## Why the result is credible

- Actual intraday data was used (Yahoo 1h 2y and 5m 60d, 09:30-16:00 ET).
- Signals use only completed bars; entry is the next bar open.
- Costs are 5 bps/side base and 10 bps 2x; French PFU is modeled.
- Pairs/events use placebos/walk-forward; intraday sample limits are stated.
- v11 earnings 2.02 uses SEC acceptance timestamps, a one-bar execution
  delay, 5-slot concurrency, short borrow and date-shift placebos. It reports
  both a 20%-slot risk budget and an upper-bound full-capital sizing; the only
  full-capital lead fails the year-fold rule. The 5m book has only 15 events
  in 60 days and is execution quality only.
- The daily PEAD study uses SEC acceptance times, completed pre-entry bars,
  24h companion-filing de-duplication, 5/10 bps per side and 50 registered
  trials. Survivorship in the current-filer cache is stated, not hidden.
- Every variant and failed fold is in the trial registries:
  `results/alpha_trials.csv`, `results/short2_trials.csv`,
  `results/short3.md`, `results/earn_intraday_trials.csv`,
  `results/pead_trials.csv` (50 daily PEAD trials, protocol in
  `docs/pead_protocol.md`).

## What would be needed to revisit

- A paid intraday/MBO dataset with several years of history and auction data.
- A curated quarterly earnings calendar and auction-capable execution; the
  cached 2.02 screen includes monthly filers, and a one-bar delay gives up the
  overnight gap that carries most of the earnings reaction.
- A market-making/execution edge with sub-bp spreads and queue position.
- A funding/borrow model for neutral pairs.
- A point-in-time earnings-event sample with delisted/acquired names; the
  current 86-ticker daily PEAD cache is survivor-biased.
- Real forward paper evidence at 5/5 before any allocation.

Until then: **no short-horizon satellite**. The monthly US book remains the only
production strategy. This is a negative result, deliberately kept.
