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

## Benchmarks and walk-forward summary

QQQ OOS (2019-2026): **2.80x** after tax; BIL **1.16x**. Full window QQQ **3.79x**, BIL **1.18x**.

| family | OOS 5bps | OOS 10bps | OOS CAGR | OOS maxDD | OOS hit | positive folds |
|---|---:|---:|---:|---:|---:|---:|
| sector | **1.29x** | **1.12x** | 3.3% | -35.9% | 52.4% | 6/8 |
| calendar | **1.01x** | **0.91x** | 0.1% | -21.2% | 53.2% | 5/8 |
| breakout | **0.91x** | **0.87x** | -1.2% | -14.8% | 49.0% | 2/8 |

## Walk-forward folds

### sector

| fold | selected | test return | test maxDD | active days | train Sharpe |
|---|---|---:|---:|---:|---:|
| 2019 | lb5 h5 n2 neutral | -0.1% | -5.7% | 252 | 0.07 |
| 2020 | lb1 h5 n5 long_only | +4.1% | -35.9% | 253 | 0.92 |
| 2021 | lb5 h5 n2 long_only | +3.8% | -11.5% | 252 | 0.47 |
| 2022 | lb1 h5 n5 long_only | -21.2% | -29.2% | 251 | 0.71 |
| 2023 | lb5 h10 n5 long_only | +15.7% | -9.8% | 250 | 0.46 |
| 2024 | lb5 h10 n5 long_only | +9.7% | -8.1% | 252 | 0.53 |
| 2025 | lb5 h10 n5 long_only | +18.2% | -15.0% | 250 | 0.55 |
| 2026 | lb5 h10 n5 long_only | +13.9% | -9.9% | 226 | 0.60 |

### calendar

| fold | selected | test return | test maxDD | active days | train Sharpe |
|---|---|---:|---:|---:|---:|
| 2019 | SMH post_holiday k1 | -7.1% | -7.9% | 9 | 0.63 |
| 2020 | QQQ dow2 | +9.0% | -12.3% | 52 | 0.39 |
| 2021 | QQQ tom k3 | -5.9% | -6.6% | 72 | 0.50 |
| 2022 | SMH tom k3 | +2.6% | -13.2% | 72 | 0.38 |
| 2023 | QQQ tom k2 | +17.2% | -2.5% | 48 | 0.52 |
| 2024 | QQQ tom k2 | -12.7% | -12.0% | 48 | 0.67 |
| 2025 | QQQ tom k2 | +0.8% | -6.4% | 48 | 0.46 |
| 2026 | QQQ tom k2 | +4.5% | -8.3% | 44 | 0.44 |

### breakout

| fold | selected | test return | test maxDD | active days | train Sharpe |
|---|---|---:|---:|---:|---:|
| 2019 | SPY w50 h3 volon | +0.9% | -0.1% | 5 | 0.55 |
| 2020 | SPY w50 h3 volon | -1.3% | -5.6% | 25 | 0.61 |
| 2021 | SMH w50 h3 volon | -1.5% | -2.3% | 3 | 0.63 |
| 2022 | SMH w50 h3 volon | -0.7% | -1.5% | 3 | 0.52 |
| 2023 | SMH w50 h3 volon | +0.0% | 0.0% | 0 | 0.46 |
| 2024 | SMH w50 h3 volon | -3.9% | -9.8% | 27 | 0.43 |
| 2025 | SPY w20 h5 voloff | -6.2% | -6.7% | 101 | 0.36 |
| 2026 | SPY w20 h5 volon | +4.3% | -2.2% | 34 | 0.26 |

## All variants

See `results/short_daily_tables.md` for all 105 variants, including failures. The CSV is `results/short_daily_trials.csv`.

