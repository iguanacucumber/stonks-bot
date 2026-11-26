# Daily sector reversal, seasonality and volatility breakout — pre-registered

Branch `v9/short-daily`, based on `c7eab28`. Written before the sweep.

## Hurdles

Every candidate must, after French PFU and costs:

1. beat **BIL** buy-and-hold over the same 2019-2026 OOS path;
2. beat **QQQ** buy-and-hold over the same path;
3. survive **2x costs** (10 bps one-way);
4. not be driven by one year/fold;
5. for calendar rules, beat a same-size random-date placebo (one-sided p < 0.05).

Failure of any gate is `reject`. No satellite allocation unless all pass.

## Common accounting

- Daily signals use data through the close `t`; entries are the next open.
- Calendar rules use only the date (known before the open).
- Base cost 5 bps one-way; 2x is 10 bps. Costs are charged when the position
  changes; short borrow/financing 500 bps/yr on short notional.
- French CTO PFU is annual, year-dependent (30% through 2025, 31.4% from 2026),
  via the existing `after_tax_series`.
- No leverage, no stock picks, no look-ahead. ETF-only and survivorship-free.

## 1. Sector cross-sectional reversal

Universe: 11 SPDR sectors (XLK, XLE, XLF, XLV, XLI, XLP, XLU, XLB, XLY, XLRE,
XLC) plus SPY. At every `hold`-session reset, long the `top_n` worst lookback
returns; `neutral` also shorts the `top_n` best at half gross each side.

Variants:

- lookback 1 or 5 sessions;
- hold 1, 5 or 10 sessions;
- top_n 2, 3 or 5;
- mode long_only or neutral;
- cost 5 or 10 bps; borrow 500 bps on shorts.

## 2. Calendar/seasonality

Assets: SPY, QQQ, SMH. Rules:

- turn-of-month: first/last 1, 2 or 3 trading sessions of each month;
- month-end: last session;
- day-of-week: Monday through Friday;
- pre-holiday and post-holiday, using weekdays missing from the SPY calendar.
  This is an exchange-calendar approximation; missing data days are treated as
  holidays and disclosed.
- placebo: random dates with the same number of active sessions, 200 draws.

## 3. Volatility breakout

Assets: SPY, QQQ, SMH. Signal at close `t`: close above the prior `window`-day
high, optionally only when ATR14/close is above its trailing 252-day median.
Entry next open, exit at the close after `hold` sessions; one position at a time.

Variants: window 20/50; hold 1/3/5; volatility filter on/off; cost 5/10 bps.

## 4. Walk-forward

Annual expanding folds 2019-2026: train 2015 through the prior year, select the
variant by training Sharpe, apply it to the next calendar year. The concatenated
2019-2026 test path is the OOS result; the test year is never used for selection.
All variants are reported on the full window and in the trial CSV, including
failures.

## Outputs

- `results/short_daily.md`: every family/variant, full-window + OOS, n, hit,
  maxDD, 5/10 bps, placebo where applicable, verdict.
- `results/short_daily_trials.csv`: full trial registry.
- `scripts/research_short_daily.py`, `tests/test_short_daily.py`.

---

# Results

(To be appended after the runs.)
