# stonks-bot — results

Default: dual-momentum rotation. Hold QQQ while its 12-month total return beats SPY;
otherwise rotate into the S&P momentum sleeve. Monthly check, next-close execution,
10 bps per side, no leverage. The LLM overlay is optional and showed no edge.

## 2015-01-02 to 2026-11-24

|              | total_return   | cagr   | vol   |   sharpe |   sortino | max_dd   |   calmar |   beta | alpha   | tracking_error   | hit_rate   |   days |   sharpe_lo |   sharpe_hi |      diff_lo |     diff_ann |      diff_hi |   turnover_ann |   trades | exposure   |
|:-------------|:---------------|:-------|:------|---------:|----------:|:---------|---------:|-------:|:--------|:-----------------|:-----------|-------:|------------:|------------:|-------------:|-------------:|-------------:|---------------:|---------:|:-----------|
| bot          | 354.2%         | 13.6%  | 19.2% |     0.76 |      0.92 | -28.6%   |     0.48 |   0.9  | 2.2%    | 11.0%            | 51.9%      |   2990 |   0.251357  |     1.31807 | -0.0501387   |  0.00907073  |  0.0678846   |           3.95 |     1266 | 88.8%      |
| spy          | 321.9%         | 12.9%  | 17.6% |     0.79 |      0.96 | -33.7%   |     0.38 |   1    | -0.0%   | 0.0%             | 54.5%      |   2990 |   0.26295   |     1.35632 | -4.95992e-16 |  1.87079e-16 |  8.79504e-16 |           0    |        0 | 100.0%     |
| equal_weight | 286.5%         | 12.1%  | 17.7% |     0.73 |      0.9  | -37.5%   |     0.32 |   0.97 | -0.4%   | 4.8%             | 54.5%      |   2990 |   0.203567  |     1.34059 | -0.0347547   | -0.0072374   |  0.0196444   |           2.26 |    29245 | 98.0%      |
| random_p50   | 204.6%         | 9.8%   | 17.3% |     0.63 |      0.81 | -30.8%   |     0.32 |   0.87 | -1.1%   | 8.5%             | 53.4%      |   2990 |   0.109237  |     1.23687 | -0.0765682   | -0.0279119   |  0.019516    |          22.08 |     5506 | 96.9%      |
| random_p5    | 107.7%         | 6.4%   | 16.9% |     0.44 |      0.56 | -33.6%   |     0.19 |   0.84 | -4.4%   | 8.2%             | 52.9%      |   2990 |  -0.0781955 |     1.025   | -0.106358    | -0.0598769   | -0.0145144   |          21.9  |     5483 | 96.7%      |
| random_p95   | 279.1%         | 11.9%  | 17.5% |     0.74 |      0.95 | -29.0%   |     0.37 |   0.88 | 0.6%    | 8.8%             | 54.1%      |   2990 |   0.189916  |     1.33817 | -0.0544644   | -0.00949293  |  0.0341402   |          22.22 |     5519 | 97.0%      |

| strategy | $10,000 becomes |
|---|---:|
| bot | $45,417 |
| spy | $42,186 |
| equal_weight | $38,651 |
| random_p50 | $30,464 |
| random_p5 | $20,771 |
| random_p95 | $37,915 |

## 2026 only

|               | total_return   | cagr   | vol   |   sharpe |   sortino | max_dd   |   calmar |   beta | alpha   | tracking_error   | hit_rate   |   days |   sharpe_lo |   sharpe_hi |      diff_lo |     diff_ann |     diff_hi |   turnover_ann |   trades | exposure   |
|:--------------|:---------------|:-------|:------|---------:|----------:|:---------|---------:|-------:|:--------|:-----------------|:-----------|-------:|------------:|------------:|-------------:|-------------:|------------:|---------------:|---------:|:-----------|
| dual_momentum | 12.1%          | 13.7%  | 20.2% |     0.74 |      1.11 | -11.7%   |     1.17 |   0.97 | 8.9%    | 13.5%            | 50.0%      |    224 |    -1.30328 |     2.94813 | -0.169376    |  0.0781415   | 0.329109    |           2.15 |       22 | 92.9%      |
| spy           | 5.3%           | 6.0%   | 15.6% |     0.41 |      0.56 | -11.5%   |     0.53 |   1    | -0.0%   | 0.0%             | 49.6%      |    224 |    -1.28442 |     2.34004 |  3.73035e-16 |  3.10862e-15 | 5.84421e-15 |           0    |        0 | 100.0%     |
| qqq_buyhold   | 4.4%           | 5.0%   | 24.3% |     0.29 |      0.4  | -19.9%   |     0.25 |   1.48 | -2.4%   | 11.0%            | 52.2%      |    224 |    -1.49588 |     2.23731 | -0.212512    |  0.0076515   | 0.226751    |           0    |        0 | 100.0%     |
| core80_mom20  | 3.2%           | 3.6%   | 22.8% |     0.27 |      0.39 | -18.7%   |     0.19 |   1.37 | -2.4%   | 10.0%            | 55.4%      |    224 |    -1.44145 |     2.1968  | -0.202556    | -0.00932037  | 0.182419    |           3.42 |      341 | 95.3%      |
| core50_mom50  | 2.3%           | 2.6%   | 22.0% |     0.23 |      0.33 | -17.7%   |     0.15 |   1.24 | -2.7%   | 11.3%            | 54.0%      |    224 |    -1.47682 |     2.10839 | -0.23433     | -0.0206948   | 0.18397     |           7.29 |      344 | 91.3%      |

| strategy | $10,000 becomes |
|---|---:|
| dual_momentum | $11,212 |
| spy | $10,534 |
| qqq_buyhold | $10,443 |
| core80_mom20 | $10,320 |
| core50_mom50 | $10,232 |

## Verdict

- Full window: bot $45,417 vs SPY $42,186 (+$3,231, +7.7%).
- 2026: bot $11,212 vs SPY $10,534 (+$678, +6.4%).
- Max drawdown: bot -28.6% vs SPY -33.7% (full), -11.7% vs -11.5% (2026).
- Sharpe: bot 0.76 vs SPY 0.79 (full), 0.74 vs 0.41 (2026).
- The full-window paired excess return is +0.9%/yr with a bootstrap 95% CI of
  -5.0% to +6.8%. It wins on terminal wealth, but the edge is not statistically
  significant. The source is Nasdaq-100 beta, not stock-picking alpha.

## Caveats

- One path, 11.9 years. One November 2026 switch out of QQQ drives most of the 2026 edge.
- QQQ concentration: top holdings dominate; a tech bear market hits this harder than SPY.
- The stock momentum sleeve has ~0.1-0.3 pp/yr survivorship inflation and the LLM overlay
  showed no evidence of adding money, so neither is in the default.
- Paper only. No orders, no broker, no keys.
