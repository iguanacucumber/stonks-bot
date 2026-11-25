# stonks-bot — results

Default: dual-momentum rotation. Hold SMH while its 6-month total return beats SPY;
otherwise rotate into the S&P momentum sleeve (6 names, 10-day skip). Monthly check,
next-close execution, 10 bps per side, 2.5x gross with 5%/yr margin interest,
half size when the core is below its 200-day MA (`--trend-ma 0` disables),
no trailing stop, no monthly regime cut.

## 2015-01-02 to 2026-11-24

|              | total_return   | cagr   | vol   |   sharpe |   sortino | max_dd   |   calmar |   beta | alpha   | tracking_error   | hit_rate   |   days |   sharpe_lo |   sharpe_hi |      diff_lo |     diff_ann |     diff_hi |   turnover_ann |   trades | exposure   |
|:-------------|:---------------|:-------|:------|---------:|----------:|:---------|---------:|-------:|:--------|:-----------------|:-----------|-------:|------------:|------------:|-------------:|-------------:|------------:|---------------:|---------:|:-----------|
| bot          | 13923.1%       | 51.7%  | 63.5% |     0.98 |      1.32 | -59.9%   |     0.86 |   2.39 | 29.0%   | 53.5%            | 54.9%      |   2990 |   0.435555  |    1.57475  |  0.201782    |  0.483124    | 0.754116    |          13.78 |     1874 | 209.2%     |
| spy          | 321.9%         | 12.9%  | 17.6% |     0.79 |      0.96 | -33.7%   |     0.38 |   1    | -0.0%   | 0.0%             | 54.5%      |   2990 |   0.26295   |    1.35632  | -4.95992e-16 |  1.87079e-16 | 8.79504e-16 |           0    |        0 | 100.0%     |
| equal_weight | 299.5%         | 12.4%  | 15.7% |     0.82 |      1.06 | -25.2%   |     0.49 |   0.81 | 1.7%    | 7.3%             | 54.4%      |   2990 |   0.273158  |    1.42997  | -0.044711    | -0.00779594  | 0.027927    |           4.19 |    39727 | 95.2%      |
| random_p50   | 476.2%         | 15.9%  | 41.4% |     0.56 |      0.81 | -57.2%   |     0.28 |   1.61 | 1.1%    | 32.0%            | 52.1%      |   2990 |   0.0125161 |    1.13478  | -0.0810859   |  0.0963146   | 0.266089    |          54.61 |     7428 | 213.0%     |
| random_p5    | 44.8%          | 3.2%   | 40.8% |     0.29 |      0.38 | -77.8%   |     0.04 |   1.59 | -10.4%  | 31.2%            | 51.6%      |   2990 |  -0.24673   |    0.869867 | -0.202506    | -0.0186424   | 0.164513    |          54.39 |     6986 | 212.7%     |
| random_p95   | 657.9%         | 18.6%  | 42.0% |     0.62 |      0.84 | -50.0%   |     0.35 |   1.64 | 3.1%    | 32.6%            | 53.0%      |   2990 |   0.100862  |    1.1915   | -0.0479547   |  0.12027     | 0.293903    |          55.39 |     7702 | 213.3%     |

| strategy | $10,000 becomes |
|---|---:|
| bot | $1,402,314 |
| spy | $42,186 |
| equal_weight | $39,955 |
| random_p50 | $57,622 |
| random_p5 | $14,480 |
| random_p95 | $75,792 |

## 2026 only

|     | total_return   | cagr   | vol   |   sharpe |   sortino | max_dd   |   calmar |   beta | alpha   | tracking_error   | hit_rate   |   days |   sharpe_lo |   sharpe_hi |      diff_lo |    diff_ann |     diff_hi |   turnover_ann |   trades | exposure   |
|:----|:---------------|:-------|:------|---------:|----------:|:---------|---------:|-------:|:--------|:-----------------|:-----------|-------:|------------:|------------:|-------------:|------------:|------------:|---------------:|---------:|:-----------|
| bot | 33.0%          | 37.9%  | 97.1% |     0.82 |      1.24 | -61.5%   |     0.62 |    4.4 | 52.0%   | 86.9%            | 56.7%      |    224 |    -1.06652 |     2.91832 | -0.957762    | 0.720593    | 2.37854     |          10.48 |       98 | 223.7%     |
| spy | 5.3%           | 6.0%   | 15.6% |     0.41 |      0.56 | -11.5%   |     0.53 |    1   | -0.0%   | 0.0%             | 49.6%      |    224 |    -1.28442 |     2.34004 |  3.73035e-16 | 3.10862e-15 | 5.84421e-15 |           0    |        0 | 100.0%     |

| strategy | $10,000 becomes |
|---|---:|
| bot | $13,303 |
| spy | $10,534 |

## Verdict

- Full window: bot $1,402,314 vs SPY $42,186 (+1,360,128).
- 2026: bot $13,303 vs SPY $10,534 (+2,769, +27.69%).
- Max drawdown: bot -59.9% vs SPY -33.7% (full); -61.5% vs -11.5% (2026).
- Sharpe: bot 0.98 vs SPY 0.79 (full); 0.82 vs 0.41 (2026).
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
- Taxes are not in the headline (0% rate). At 37% on realized gains the
  book makes $107,889, still 22.2%/yr: results/realism.md.
- Paper only. No orders, no broker, no keys.
