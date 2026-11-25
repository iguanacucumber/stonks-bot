# stonks-bot — results

Default: dual-momentum rotation. Hold SOXX while its 6-month total return beats SPY;
otherwise rotate into the S&P momentum sleeve (6 names, 10-day skip). Monthly check,
next-close execution, 10 bps per side, no leverage, no trailing stop, no regime cut.

## 2015-01-02 to 2026-11-24

|              | total_return   | cagr   | vol   |   sharpe |   sortino | max_dd   |   calmar |   beta | alpha   | tracking_error   | hit_rate   |   days |   sharpe_lo |   sharpe_hi |      diff_lo |     diff_ann |      diff_hi |   turnover_ann |   trades | exposure   |
|:-------------|:---------------|:-------|:------|---------:|----------:|:---------|---------:|-------:|:--------|:-----------------|:-----------|-------:|------------:|------------:|-------------:|-------------:|-------------:|---------------:|---------:|:-----------|
| bot          | 2020.6%        | 29.4%  | 32.6% |     0.95 |      1.27 | -39.5%   |     0.74 |   1.39 | 11.9%   | 22.6%            | 54.6%      |   2990 |   0.429088  |    1.51963  |  0.0535176   |  0.173789    |  0.293415    |           5.43 |      447 | 99.9%      |
| spy          | 321.9%         | 12.9%  | 17.6% |     0.79 |      0.96 | -33.7%   |     0.38 |   1    | -0.0%   | 0.0%             | 54.5%      |   2990 |   0.26295   |    1.35632  | -4.95992e-16 |  1.87079e-16 |  8.79504e-16 |           0    |        0 | 100.0%     |
| equal_weight | 296.8%         | 12.3%  | 18.0% |     0.73 |      0.9  | -38.2%   |     0.32 |   0.99 | -0.4%   | 4.9%             | 54.5%      |   2990 |   0.204949  |    1.34263  | -0.0324847   | -0.00440135  |  0.0231491   |           2.3  |    29458 | 99.9%      |
| random_p50   | 144.4%         | 7.8%   | 21.1% |     0.46 |      0.6  | -39.1%   |     0.22 |   0.97 | -3.8%   | 12.5%            | 53.1%      |   2990 |  -0.0575119 |    1.03196  | -0.106716    | -0.0397864   |  0.0308481   |          23.61 |     1693 | 100.0%     |
| random_p5    | 39.1%          | 2.8%   | 20.3% |     0.24 |      0.32 | -46.4%   |     0.07 |   0.93 | -8.1%   | 11.9%            | 51.7%      |   2990 |  -0.285198  |    0.822167 | -0.15506     | -0.087119    | -0.0164443   |          23.4  |     1686 | 99.9%      |
| random_p95   | 363.4%         | 13.8%  | 22.6% |     0.71 |      0.94 | -32.8%   |     0.36 |   1.06 | 1.7%    | 13.1%            | 54.0%      |   2990 |   0.187862  |    1.30344  | -0.0557866   |  0.0153334   |  0.085188    |          23.75 |     1700 | 100.0%     |

| strategy | $10,000 becomes |
|---|---:|
| bot | $212,063 |
| spy | $42,186 |
| equal_weight | $39,678 |
| random_p50 | $24,442 |
| random_p5 | $13,910 |
| random_p95 | $46,338 |

## 2026 only

|              | total_return   | cagr   | vol   |   sharpe |   sortino | max_dd   |   calmar |   beta | alpha   | tracking_error   | hit_rate   |   days |   sharpe_lo |   sharpe_hi |      diff_lo |    diff_ann |     diff_hi |   turnover_ann |   trades | exposure   |
|:-------------|:---------------|:-------|:------|---------:|----------:|:---------|---------:|-------:|:--------|:-----------------|:-----------|-------:|------------:|------------:|-------------:|------------:|------------:|---------------:|---------:|:-----------|
| bot          | 25.7%          | 29.4%  | 50.3% |     0.77 |      1.12 | -40.9%   |     0.72 |   2.28 | 24.2%   | 40.9%            | 58.9%      |    224 |    -1.17956 |     2.88354 | -0.52103     | 0.312194    | 1.12806     |           2.91 |        8 | 99.6%      |
| spy          | 5.3%           | 6.0%   | 15.6% |     0.41 |      0.56 | -11.5%   |     0.53 |   1    | -0.0%   | 0.0%             | 49.6%      |    224 |    -1.28442 |     2.34004 |  3.73035e-16 | 3.10862e-15 | 5.84421e-15 |           0    |        0 | 100.0%     |
| qqq_buyhold  | 4.4%           | 5.0%   | 24.3% |     0.29 |      0.4  | -19.9%   |     0.25 |   1.48 | -2.4%   | 11.0%            | 52.2%      |    224 |    -1.49588 |     2.23731 | -0.212512    | 0.0076515   | 0.226751    |           0    |        0 | 100.0%     |
| soxx_buyhold | 23.2%          | 26.5%  | 55.4% |     0.67 |      0.93 | -43.4%   |     0.61 |   2.77 | 19.9%   | 44.5%            | 58.9%      |    224 |    -1.08533 |     2.75069 | -0.543711    | 0.317081    | 1.15555     |           0    |        0 | 100.0%     |

| strategy | $10,000 becomes |
|---|---:|
| bot | $12,573 |
| spy | $10,534 |
| qqq_buyhold | $10,443 |
| soxx_buyhold | $12,322 |

## Verdict

- Full window: bot $212,063 vs SPY $42,186 (+169,877).
- 2026: bot $12,573 vs SPY $10,534 (+2,039, +20.39%).
- Max drawdown: bot -39.5% vs SPY -33.7% (full); -40.9% vs -11.5% (2026).
- Sharpe: bot 0.95 vs SPY 0.79 (full); 0.77 vs 0.41 (2026).
- Paired excess return: +17.4%/yr, 95% CI +5.4% to +29.3%. The 95% CI excludes zero on one
  path; treat it as evidence, not a guarantee.

## Caveats

- SOXX is a semiconductor ETF: most of the return is one industry's 11-year run, not alpha.
- SOXX buy-and-hold made $140,256 with a -45.8% drawdown; the rotation adds ~$72k
  and cuts the drawdown to -39.5%, but the core is still one industry.
- 2022 was still -13.3% and the full-window drawdown is -39.5% vs SPY -33.7%.
- No trailing stop and no regime cut: the ten rounds traded crash protection for return.
- Ten rounds were tuned on this one price path; 2023-2026 was held back as a check and won.
- Exact costs still leave $181,466 at 30 bps; turnover is 5.4x NAV/yr.
- Paper only. No orders, no broker, no keys.
