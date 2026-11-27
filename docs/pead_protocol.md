# PEAD 8-K item 2.02 protocol (v11, issue #99)

Status: **pre-registered before the first result run**. Base commit
`c653d74`. Data snapshot 2026-11-25. This file is the record of the rules;
`results/pead.md` is the result. Changing a rule after seeing results turns
the experiment into a new, separately registered trial.

## Question

Does a point-in-time earnings 8-K (item 2.02) produce a tradable
short-horizon drift after realistic French CTO costs and PFU tax?

## Data (cache only, no network)

- `cache/news/sub_*.json`: SEC submissions. Only `form == "8-K"` and item
  `2.02` in the `items` field. Event time is `acceptanceDateTime` converted
  to naive New York time.
- `cache/bars/<ticker>.csv`: daily adjusted OHLCV via
  `bot.research.data.read_bars`. `open/high/low` are adjusted with the same
  factor as `adjclose`, so all prices are total-return consistent.
- Sample: filing date `>= 2014-01-01` (bars start 2014-01-02) through the
  cache end 2026-11-25. Tickers without cached bars are dropped, never
  fabricated. The event ticker is `tickers[0]`.
- This is a **current large-cap filer panel**, not a point-in-time index
  membership panel. Survivorship is a stated limitation, not repaired here.

## Events, timing, de-duplication

- Event identity is `(ticker, accessionNumber)`.
- Exact duplicate accession rows are dropped. Distinct 2.02 filings for the
  same ticker whose acceptance times are less than 24 hours apart are
  collapsed to the earliest acceptance (amended/companion filings are one
  event). This is done before any return is computed.
- `event_time`:
  - acceptance timestamp in `America/New_York`, if present;
  - otherwise filing date at 16:00 ET (known after the close).
- Entry session is the first session whose open is **strictly after** the
  event is known:
  - acceptance before 09:30 ET on a trading day: that day's open;
  - acceptance 09:30-16:00 ET: next session's open;
  - acceptance at/after 16:00 ET, unknown time, or non-trading day: next
    session's open.
- Entry fill is the entry session `open`. The filing-day close is never used
  as a fill when the event was known after it.
- A position is held for `H` sessions and exits at the close of session
  `entry + H - 1` (H=5 means five sessions including entry day).
- If the ticker has no cached bar for a required session, the event is
  skipped for that horizon; nothing is forward-filled.

## Reaction measures (all known at the entry open)

- `gap = entry_open / previous_session_close - 1`.
- `close_close = previous_session_close / close_two_sessions_before - 1`.
- `open_close = previous_session_close / previous_session_open - 1`.
- `volume_z = (previous_session_volume - mean20) / std20`, where mean20 and
  std20 use the 20 sessions ending two sessions before entry (at least 10
  observations).
- These are the only reaction definitions. For after-close filings the
  close-close and open-close measures describe the pre-announcement session;
  that is deliberate and is why the gap measure is the primary.

## Primary (pre-registered, one test)

Long-only, `gap > 0`, `H = 5`, entry next session open, exit fifth session
close, equal weight 10% of current equity per position, at most 10
concurrent positions, no pyramiding in a ticker, no leverage.

- On a day with more eligible signals than free slots, take the largest
  `gap` first, then ticker ascending.
- A slot is occupied through the exit close; a new position cannot use a
  slot freed at the same close.
- Idle cash earns BIL's daily total return (the capital could sit in
  T-bills). The zero-cash-yield version is a registered secondary.
- Costs: 5 bps per side on traded notional (base), 10 bps per side (2x).
- Tax: French CTO PFU by realization year (`bot.backtest.fr_rate`: 34.5%
  before 2018, 30% 2018-2025, 31.4% in 2026), annual netting, 10-year loss
  carry, via `bot.research.short3.daily.after_tax_series`.
- Benchmarks: QQQ and BIL buy-and-hold over the **same first-entry to
  last-exit window**, 5 bps per side (10 bps at 2x), same PFU helper.

## Registered secondary grid

All rows are run and written to `results/pead_trials.csv`. Cost is 5 and
10 bps/side for every variant, so 2x-cost sensitivity is part of the grid,
not an after-the-fact rescue.

| family | variants | count |
|---|---|---:|
| long-only reaction x hold | filters {gap, close_close, open_close, volume_z} x holds {3,5,10,20} x costs {5,10} | 32 |
| long-short gap | holds {3,5,10,20} x costs {5,10} | 8 |
| zero cash yield | primary gap x holds {3,5,10,20} x costs {5,10} | 8 |
| walk-forward by year | choose one of the 16 long-only variants on all prior years, test next year, chain; costs {5,10} | 2 |

Total pre-registered strategy trials: **50**. `n_trials = 50` is used in the
DSR and multiple-testing penalties even if some rows are identical in
places. The two buy-and-hold benchmarks are comparison rows, not trials.

- `volume_z` variant: `gap > 0` and `volume_z > 1`.
- Long-short: long the 5 largest positive gaps, short the 5 most negative
  gaps, 10% of equity per position, gross 100%, net 0, borrow 500 bps/year
  on short notional, no short rebate, no dividends paid on shorts (stated
  limitation), same 5/10 bps per side.
- Walk-forward: for year Y (2017..2026), rank the 16 long-only variants by
  Sharpe on all sessions from 2014-01-02 through Y-1-12-31; use that
  variant's daily net return in Y. If the best training Sharpe is negative,
  hold BIL for Y. No test-year data is used in selection.

## Statistics

- Event-level mean net return, median, hit rate, Newey-West-free plain
  t-stat (reported as descriptive), and 95% block-bootstrap CI.
- Portfolio after-tax terminal multiple, CAGR, max drawdown, Sharpe, worst
  12 months, calendar-year table, exposure and cash drag.
- Placebo: 500 random-entry draws per long-only variant, matched to the
  number of eligible events per ticker, with the same hold, reaction filter
  and costs. Entry and reaction are recomputed on the random session. The
  p-value is `(1 + #{placebo >= actual}) / (N + 1)` for the event-level
  mean. Portfolio-level placebo (same procedure, full simulation) is run for
  the primary and for any secondary that beats QQQ at 2x costs.
- Block bootstrap: 5,000 paired paths, 21-session blocks, 5th percentile
  terminal multiple and max drawdown, and `P(strategy <= QQQ)` /
  `P(strategy <= BIL)` on the same resampled dates.
- Deflated Sharpe ratio (DSR) with all 50 trial Sharpes.
- CSCV PBO on the T x 50 matrix of daily net returns of the registered
  trials.
- Yearly gate uses annual after-tax approximation
  `r - max(0, r) * fr_rate(year)` and the same for BIL/QQQ, and also the
  pre-tax net return for transparency.

## Pass/fail gates

Primary is a **paper candidate** only if every gate passes:

1. after-tax 1x terminal multiple > BIL after-tax, same window;
2. after-tax 1x terminal multiple > QQQ after-tax, same window;
3. after-tax 2x terminal multiple > BIL after-tax, same window;
4. after-tax 2x terminal multiple > QQQ after-tax, same window;
5. event-level placebo p < 0.05 and portfolio placebo p < 0.05;
6. DSR >= 0.95 with `n_trials = 50`;
7. at least 60% of calendar years have positive after-tax return and at
   least 60% beat BIL after tax;
8. excluding the best calendar year, the compounded after-tax return still
   beats BIL over the remaining years (no single year carries the result);
9. max concurrent positions <= 10, no leverage, no same-ticker pyramid,
   all timestamps and bars from cache.

If the primary fails, the family is **rejected** unless a pre-registered
secondary passes all of:

- gates 1-6 at its own 2x cost, with its own placebo and DSR using all 50
  trials;
- Bonferroni `p < 0.05 / 50 = 0.001` **and** Benjamini-Hochberg q < 0.05
  across the 50 trials;
- yearly gates 7-8;
- at least one adjacent hold (3/10/20) also beats QQQ at 2x;
- long-short only: still beats BIL at 2x with borrow raised to 1,000 bps
  and zero short rebate.

A marginal winner is a reject. No rule changes after seeing results.

## Verdict labels

`paper` = all primary gates pass, forward paper evidence still missing.
`reject` = any gate fails. No live or default change is part of this branch.
