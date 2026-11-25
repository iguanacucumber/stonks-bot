# stonks-bot — results

Default: dual-momentum rotation. Hold SMH while its 6-month total return beats SPY;
otherwise rotate into the S&P momentum sleeve (6 names, 10-day skip). Monthly check,
next-close execution, 10 bps per side, 2x gross with 5%/yr margin interest
(`--leverage 1` for the unlevered book), no trailing stop, no regime cut.

## 2015-01-02 to 2026-11-24

|              | total_return   | cagr   | vol   |   sharpe |   sortino | max_dd   |   calmar |   beta | alpha   | tracking_error   | hit_rate   |   days |   sharpe_lo |   sharpe_hi |      diff_lo |     diff_ann |     diff_hi |   turnover_ann |   trades | exposure   |
|:-------------|:---------------|:-------|:------|---------:|----------:|:---------|---------:|-------:|:--------|:-----------------|:-----------|-------:|------------:|------------:|-------------:|-------------:|------------:|---------------:|---------:|:-----------|
| bot          | 8793.8%        | 46.0%  | 61.2% |     0.93 |      1.25 | -60.7%   |     0.76 |   2.66 | 20.0%   | 49.0%            | 54.9%      |   2990 |   0.40541   |    1.49268  |  0.180679    |  0.429969    | 0.680423    |          10.66 |     3023 | 193.2%     |
| spy          | 321.9%         | 12.9%  | 17.6% |     0.79 |      0.96 | -33.7%   |     0.38 |   1    | -0.0%   | 0.0%             | 54.5%      |   2990 |   0.26295   |    1.35632  | -4.95992e-16 |  1.87079e-16 | 8.79504e-16 |           0    |        0 | 100.0%     |
| equal_weight | 296.8%         | 12.3%  | 18.0% |     0.73 |      0.9  | -38.2%   |     0.32 |   0.99 | -0.4%   | 4.9%             | 54.5%      |   2990 |   0.204949  |    1.34263  | -0.0324847   | -0.00440135  | 0.0231491   |           2.3  |    29458 | 99.9%      |
| random_p50   | 98.4%          | 5.9%   | 41.5% |     0.35 |      0.46 | -66.4%   |     0.09 |   1.9  | -12.2%  | 29.3%            | 52.4%      |   2990 |  -0.177178  |    0.914792 | -0.152672    |  0.00721074  | 0.178659    |          47.26 |     9305 | 195.7%     |
| random_p5    | 2.8%           | 0.2%   | 40.8% |     0.22 |      0.28 | -79.6%   |     0    |   1.84 | -17.4%  | 28.4%            | 51.3%      |   2990 |  -0.302145  |    0.8057   | -0.216672    | -0.0466361   | 0.119643    |          46.85 |     8735 | 195.6%     |
| random_p95   | 443.2%         | 15.3%  | 44.0% |     0.55 |      0.73 | -60.5%   |     0.23 |   2.03 | -3.4%   | 31.4%            | 53.0%      |   2990 |   0.0301667 |    1.12339  | -0.0672139   |  0.0908282   | 0.252448    |          47.53 |     9567 | 195.9%     |

| strategy | $10,000 becomes |
|---|---:|
| bot | $889,382 |
| spy | $42,186 |
| equal_weight | $39,678 |
| random_p50 | $19,838 |
| random_p5 | $10,278 |
| random_p95 | $54,322 |

## 2026 only

|     | total_return   | cagr   | vol   |   sharpe |   sortino | max_dd   |   calmar |   beta | alpha   | tracking_error   | hit_rate   |   days |   sharpe_lo |   sharpe_hi |      diff_lo |    diff_ann |     diff_hi |   turnover_ann |   trades | exposure   |
|:----|:---------------|:-------|:------|---------:|----------:|:---------|---------:|-------:|:--------|:-----------------|:-----------|-------:|------------:|------------:|-------------:|------------:|------------:|---------------:|---------:|:-----------|
| bot | 15.0%          | 17.0%  | 83.2% |     0.61 |      0.9  | -60.6%   |     0.28 |   4.04 | 25.3%   | 72.2%            | 56.7%      |    224 |    -1.27178 |     2.70182 | -0.976775    | 0.433475    | 1.83192     |           8.56 |      148 | 189.6%     |
| spy | 5.3%           | 6.0%   | 15.6% |     0.41 |      0.56 | -11.5%   |     0.53 |   1    | -0.0%   | 0.0%             | 49.6%      |    224 |    -1.28442 |     2.34004 |  3.73035e-16 | 3.10862e-15 | 5.84421e-15 |           0    |        0 | 100.0%     |

| strategy | $10,000 becomes |
|---|---:|
| bot | $11,500 |
| spy | $10,534 |

## Verdict

- Full window: bot $889,382 vs SPY $42,186 (+847,196).
- 2026: bot $11,500 vs SPY $10,534 (+966, +9.66%).
- Max drawdown: bot -60.7% vs SPY -33.7% (full); -60.6% vs -11.5% (2026).
- Sharpe: bot 0.93 vs SPY 0.79 (full); 0.61 vs 0.41 (2026).
- Paired excess return: +43.0%/yr, 95% CI +18.1% to +68.0%. The 95% CI excludes zero on one
  path; treat it as evidence, not a guarantee.

## Caveats

- SOXX is a semiconductor ETF: most of the return is one industry's 11-year run, not alpha.
- Leverage: 2.0x gross at 5.0%/yr.
  The full-window drawdown is -60.7% vs -33.7% for SPY;
  leverage multiplies the losses as well as the wins.
- Daily de-leveraging when gross drifts above target adds turnover:
  10.7x NAV/yr, which is why costs matter.
- No trailing stop and no regime cut: the ten rounds traded crash protection for return.
- Ten rounds were tuned on this one price path; 2023-2026 was held back as a check and won.
- Paper only. No orders, no broker, no keys.
