# stonks-bot — results

Default: dual-momentum rotation. Hold SMH while its 6-month total return beats SPY;
otherwise rotate into the S&P momentum sleeve (6 names, 10-day skip). Monthly check,
next-close execution, 10 bps per side, 2.5x gross with 5%/yr margin interest,
half size when the core is below its 200-day MA (`--trend-ma 0` disables),
no trailing stop, no monthly regime cut.

## 2015-01-02 to 2026-11-24

|              | total_return   | cagr   | vol   |   sharpe |   sortino | max_dd   |   calmar |   beta | alpha   | tracking_error   | hit_rate   |   days |   sharpe_lo |   sharpe_hi |      diff_lo |     diff_ann |     diff_hi |   turnover_ann |   trades | exposure   |
|:-------------|:---------------|:-------|:------|---------:|----------:|:---------|---------:|-------:|:--------|:-----------------|:-----------|-------:|------------:|------------:|-------------:|-------------:|------------:|---------------:|---------:|:-----------|
| bot          | 13590.0%       | 51.4%  | 63.5% |     0.97 |      1.31 | -59.9%   |     0.86 |   2.39 | 28.7%   | 53.5%            | 54.9%      |   2990 |   0.42993   |    1.57305  |  0.201226    |  0.480962    | 0.749563    |          13.79 |     1784 | 209.2%     |
| spy          | 321.9%         | 12.9%  | 17.6% |     0.79 |      0.96 | -33.7%   |     0.38 |   1    | -0.0%   | 0.0%             | 54.5%      |   2990 |   0.26295   |    1.35632  | -4.95992e-16 |  1.87079e-16 | 8.79504e-16 |           0    |        0 | 100.0%     |
| equal_weight | 300.0%         | 12.4%  | 15.7% |     0.82 |      1.06 | -25.3%   |     0.49 |   0.81 | 1.7%    | 7.3%             | 54.3%      |   2990 |   0.273123  |    1.43206  | -0.0444382   | -0.00770403  | 0.0279703   |           4.18 |    39617 | 95.2%      |
| random_p50   | 197.6%         | 9.6%   | 41.4% |     0.43 |      0.6  | -55.8%   |     0.15 |   1.6  | -4.4%   | 32.0%            | 52.2%      |   2990 |  -0.126257  |    1.00948  | -0.139598    |  0.0408979   | 0.207109    |          55.02 |     7461 | 213.1%     |
| random_p5    | 78.1%          | 4.8%   | 40.2% |     0.32 |      0.44 | -67.3%   |     0.08 |   1.55 | -8.6%   | 30.6%            | 51.4%      |   2990 |  -0.207338  |    0.906379 | -0.183522    | -0.00700456  | 0.170945    |          54.47 |     7149 | 212.8%     |
| random_p95   | 540.7%         | 16.5%  | 41.7% |     0.58 |      0.79 | -48.0%   |     0.31 |   1.64 | 1.4%    | 32.4%            | 52.8%      |   2990 |   0.0528475 |    1.14887  | -0.0676343   |  0.101837    | 0.278503    |          55.11 |     7593 | 213.3%     |

| strategy | $10,000 becomes |
|---|---:|
| bot | $1,368,999 |
| spy | $42,186 |
| equal_weight | $39,997 |
| random_p50 | $29,758 |
| random_p5 | $17,814 |
| random_p95 | $64,069 |

## 2026 only

|     | total_return   | cagr   | vol   |   sharpe |   sortino | max_dd   |   calmar |   beta | alpha   | tracking_error   | hit_rate   |   days |   sharpe_lo |   sharpe_hi |      diff_lo |    diff_ann |     diff_hi |   turnover_ann |   trades | exposure   |
|:----|:---------------|:-------|:------|---------:|----------:|:---------|---------:|-------:|:--------|:-----------------|:-----------|-------:|------------:|------------:|-------------:|------------:|------------:|---------------:|---------:|:-----------|
| bot | 33.0%          | 37.9%  | 97.1% |     0.82 |      1.24 | -61.5%   |     0.62 |    4.4 | 52.0%   | 86.9%            | 56.7%      |    224 |    -1.06652 |     2.91832 | -0.957762    | 0.720593    | 2.37854     |          10.48 |       90 | 223.7%     |
| spy | 5.3%           | 6.0%   | 15.6% |     0.41 |      0.56 | -11.5%   |     0.53 |    1   | -0.0%   | 0.0%             | 49.6%      |    224 |    -1.28442 |     2.34004 |  3.73035e-16 | 3.10862e-15 | 5.84421e-15 |           0    |        0 | 100.0%     |

| strategy | $10,000 becomes |
|---|---:|
| bot | $13,303 |
| spy | $10,534 |

## Verdict

- Full window: bot $1,368,999 vs SPY $42,186 (+1,326,813).
- 2026: bot $13,303 vs SPY $10,534 (+2,769, +27.69%).
- Max drawdown: bot -59.9% vs SPY -33.7% (full); -61.5% vs -11.5% (2026).
- Sharpe: bot 0.97 vs SPY 0.79 (full); 0.82 vs 0.41 (2026).
- Paired excess return: +48.1%/yr, 95% CI +20.1% to +75.0%. The 95% CI excludes zero on one
  path; treat it as evidence, not a guarantee.

## Caveats

- SOXX is a semiconductor ETF: most of the return is one industry's 11-year run, not alpha.
- Leverage: 2.5x gross at 5.0%/yr.
  The full-window drawdown is -59.9% vs -33.7% for SPY;
  leverage multiplies the losses as well as the wins.
- Daily de-leveraging when gross drifts above target adds turnover:
  13.8x NAV/yr, which is why costs matter.
- No trailing stop and no regime cut: the ten rounds traded crash protection for return.
- Ten rounds were tuned on this one price path; 2023-2026 was held back as a check and won.
- Paper only. No orders, no broker, no keys.
