# stonks-bot — results

Default: dual-momentum rotation. Hold SMH while its 6-month total return beats SPY;
otherwise rotate into the S&P momentum sleeve (6 names, 10-day skip). Month-end check,
next-open execution, 10 bps per side, 2.5x gross with 5%/yr margin interest,
half size when the core is below its 200-day MA (`--trend-ma 0` disables),
no trailing stop.

## 2015-01-02 to 2026-11-24

|              | total_return   | cagr   | vol   |   sharpe |   sortino | max_dd   |   calmar |   beta | alpha   | tracking_error   | hit_rate   |   days |   sharpe_lo |   sharpe_hi |      diff_lo |     diff_ann |     diff_hi |   turnover_ann |   trades | exposure   |
|:-------------|:---------------|:-------|:------|---------:|----------:|:---------|---------:|-------:|:--------|:-----------------|:-----------|-------:|------------:|------------:|-------------:|-------------:|------------:|---------------:|---------:|:-----------|
| bot          | 21817.6%       | 57.5%  | 62.9% |     1.04 |      1.4  | -60.7%   |     0.95 |   2.34 | 33.0%   | 53.0%            | 54.5%      |   2990 |   0.49733   |    1.63521  |  0.235222    |  0.517159    | 0.784423    |          15.12 |     1693 | 204.0%     |
| spy          | 321.9%         | 12.9%  | 17.6% |     0.79 |      0.96 | -33.7%   |     0.38 |   1    | -0.0%   | 0.0%             | 54.5%      |   2990 |   0.26295   |    1.35632  | -4.95992e-16 |  1.87079e-16 | 8.79504e-16 |           0    |        0 | 100.0%     |
| equal_weight | 330.5%         | 13.1%  | 15.7% |     0.86 |      1.11 | -25.0%   |     0.52 |   0.81 | 2.3%    | 7.4%             | 53.8%      |   2990 |   0.307127  |    1.46174  | -0.0389282   | -0.00147036  | 0.0352959   |           4.08 |    39411 | 94.1%      |
| random_p50   | 149.8%         | 8.0%   | 39.7% |     0.39 |      0.53 | -62.8%   |     0.13 |   1.61 | -7.0%   | 29.8%            | 52.4%      |   2990 |  -0.135231  |    0.977193 | -0.144331    |  0.0186007   | 0.182648    |          54.42 |     6436 | 208.3%     |
| random_p5    | 68.3%          | 4.5%   | 38.6% |     0.31 |      0.4  | -69.8%   |     0.06 |   1.56 | -9.8%   | 28.8%            | 52.3%      |   2990 |  -0.246154  |    0.927884 | -0.187257    | -0.0127811   | 0.159631    |          54.19 |     6236 | 208.3%     |
| random_p95   | 560.3%         | 17.1%  | 40.3% |     0.6  |      0.83 | -54.5%   |     0.32 |   1.63 | 1.7%    | 30.6%            | 52.7%      |   2990 |   0.0820609 |    1.17682  | -0.0600253   |  0.0969999   | 0.260932    |          54.53 |     6933 | 208.4%     |

| strategy | $10,000 becomes |
|---|---:|
| bot | $2,191,762 |
| spy | $42,186 |
| equal_weight | $43,051 |
| random_p50 | $24,980 |
| random_p5 | $16,829 |
| random_p95 | $66,026 |

## 2026 only

|     | total_return   | cagr   | vol   |   sharpe |   sortino | max_dd   |   calmar |   beta | alpha   | tracking_error   | hit_rate   |   days |   sharpe_lo |   sharpe_hi |      diff_lo |    diff_ann |     diff_hi |   turnover_ann |   trades | exposure   |
|:----|:---------------|:-------|:------|---------:|----------:|:---------|---------:|-------:|:--------|:-----------------|:-----------|-------:|------------:|------------:|-------------:|------------:|------------:|---------------:|---------:|:-----------|
| bot | 17.6%          | 20.1%  | 96.4% |     0.68 |      0.96 | -60.7%   |     0.33 |    4.3 | 37.9%   | 86.3%            | 52.2%      |    224 |    -1.24477 |     2.83416 | -1.09376     | 0.57749     | 2.26931     |          13.85 |       90 | 202.9%     |
| spy | 5.3%           | 6.0%   | 15.6% |     0.41 |      0.56 | -11.5%   |     0.53 |    1   | -0.0%   | 0.0%             | 49.6%      |    224 |    -1.28442 |     2.34004 |  3.73035e-16 | 3.10862e-15 | 5.84421e-15 |           0    |        0 | 100.0%     |

| strategy | $10,000 becomes |
|---|---:|
| bot | $11,765 |
| spy | $10,534 |

## Verdict

- Full window: bot $2,191,762 vs SPY $42,186 (+2,149,576).
- 2026: bot $11,765 vs SPY $10,534 (+1,231, +12.31%).
- Max drawdown: bot -60.7% vs SPY -33.7% (full); -60.7% vs -11.5% (2026).
- Sharpe: bot 1.04 vs SPY 0.79 (full); 0.68 vs 0.41 (2026).
- Paired excess return: +51.7%/yr, 95% CI +23.5% to +78.4%. The 95% CI excludes zero on one
  path; treat it as evidence, not a guarantee.

## Caveats

- SMH is a semiconductor ETF: most of the return is one industry's 11-year run, not alpha.
- Leverage: 2.5x gross at 5.0%/yr.
  The full-window drawdown is -60.7% vs -33.7% for SPY;
  leverage multiplies the losses as well as the wins.
- Daily de-leveraging when gross drifts above target adds turnover:
  15.1x NAV/yr, which is why costs matter.
- A daily 200-day core cut halves exposure below the MA; there is no trailing stop.
  2018 was -34.5% and 2022 -39.3% (SPY -5.2% and -18.6%).
- Ten rounds were tuned on this one price path; 2023-2026 was held back as a check and won.
- Taxes are not in the headline (0% rate). At 37% on realized gains the
  book makes $164,988, still 26.7%/yr: results/realism.md.
- Paper only. No orders, no broker, no keys.
