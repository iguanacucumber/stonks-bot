# stonks-bot — results

Default: dual-momentum rotation. Hold SMH while its 6-month total return beats SPY;
otherwise rotate into the S&P momentum sleeve (6 names, 10-day skip). Monthly check,
next-close execution, 10 bps per side, 2.5x gross with 5%/yr margin interest,
half size when the core is below its 200-day MA (`--trend-ma 0` disables),
no trailing stop, no monthly regime cut.

## 2015-01-02 to 2026-11-24

|              | total_return   | cagr   | vol   |   sharpe |   sortino | max_dd   |   calmar |   beta | alpha   | tracking_error   | hit_rate   |   days |   sharpe_lo |   sharpe_hi |      diff_lo |     diff_ann |     diff_hi |   turnover_ann |   trades | exposure   |
|:-------------|:---------------|:-------|:------|---------:|----------:|:---------|---------:|-------:|:--------|:-----------------|:-----------|-------:|------------:|------------:|-------------:|-------------:|------------:|---------------:|---------:|:-----------|
| bot          | 13923.1%       | 51.7%  | 63.5% |     0.98 |      1.32 | -59.9%   |     0.86 |   2.39 | 29.0%   | 53.5%            | 54.9%      |   2990 |   0.435555  |    1.57475  |  0.201782    |  0.483124    | 0.754116    |          13.78 |     1784 | 209.2%     |
| spy          | 321.9%         | 12.9%  | 17.6% |     0.79 |      0.96 | -33.7%   |     0.38 |   1    | -0.0%   | 0.0%             | 54.5%      |   2990 |   0.26295   |    1.35632  | -4.95992e-16 |  1.87079e-16 | 8.79504e-16 |           0    |        0 | 100.0%     |
| equal_weight | 299.5%         | 12.4%  | 15.7% |     0.82 |      1.06 | -25.2%   |     0.49 |   0.81 | 1.7%    | 7.3%             | 54.4%      |   2990 |   0.273158  |    1.42997  | -0.044711    | -0.00779594  | 0.027927    |           4.19 |    39727 | 95.2%      |
| random_p50   | 271.0%         | 11.6%  | 41.6% |     0.47 |      0.62 | -59.6%   |     0.18 |   1.64 | -3.5%   | 31.9%            | 52.0%      |   2990 |  -0.0455796 |    1.05874  | -0.111189    |  0.0630802   | 0.244083    |          54.55 |     7513 | 213.0%     |
| random_p5    | 20.8%          | 1.5%   | 40.7% |     0.25 |      0.33 | -81.1%   |     0.02 |   1.6  | -12.3%  | 31.1%            | 51.4%      |   2990 |  -0.290358  |    0.841379 | -0.218674    | -0.0326552   | 0.150782    |          54.35 |     7032 | 212.7%     |
| random_p95   | 713.5%         | 19.3%  | 42.7% |     0.64 |      0.87 | -51.0%   |     0.35 |   1.71 | 3.7%    | 33.0%            | 53.1%      |   2990 |   0.108506  |    1.21674  | -0.0430115   |  0.124814    | 0.293531    |          55.34 |     7818 | 213.2%     |

| strategy | $10,000 becomes |
|---|---:|
| bot | $1,402,314 |
| spy | $42,186 |
| equal_weight | $39,955 |
| random_p50 | $37,098 |
| random_p5 | $12,080 |
| random_p95 | $81,353 |

## 2026 only

|     | total_return   | cagr   | vol    |   sharpe |   sortino | max_dd   |   calmar |   beta | alpha   | tracking_error   | hit_rate   |   days |   sharpe_lo |   sharpe_hi |      diff_lo |    diff_ann |     diff_hi |   turnover_ann |   trades | exposure   |
|:----|:---------------|:-------|:-------|---------:|----------:|:---------|---------:|-------:|:--------|:-----------------|:-----------|-------:|------------:|------------:|-------------:|------------:|------------:|---------------:|---------:|:-----------|
| bot | 30.8%          | 35.3%  | 120.1% |     0.86 |      1.25 | -64.1%   |     0.55 |   4.69 | 73.6%   | 111.4%           | 53.6%      |    224 |    -0.8071  |     2.64161 | -0.938124    | 0.95449     | 2.90558     |          31.52 |      573 | 229.5%     |
| spy | 5.3%           | 6.0%   | 15.6%  |     0.41 |      0.56 | -11.5%   |     0.53 |   1    | -0.0%   | 0.0%             | 49.6%      |    224 |    -1.28442 |     2.34004 |  3.73035e-16 | 3.10862e-15 | 5.84421e-15 |           0    |        0 | 100.0%     |

| strategy | $10,000 becomes |
|---|---:|
| bot | $13,082 |
| spy | $10,534 |

## Verdict

- Full window: bot $1,402,314 vs SPY $42,186 (+1,360,128).
- 2026: bot $13,082 vs SPY $10,534 (+2,549, +25.49%).
- Max drawdown: bot -59.9% vs SPY -33.7% (full); -64.1% vs -11.5% (2026).
- Sharpe: bot 0.98 vs SPY 0.79 (full); 0.86 vs 0.41 (2026).
- Paired excess return: +48.3%/yr, 95% CI +20.2% to +75.4%. The 95% CI excludes zero on one
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
