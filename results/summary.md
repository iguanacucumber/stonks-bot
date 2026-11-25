# stonks-bot — results

Default: dual-momentum rotation. Hold VGT while its 12-month total return beats SPY;
otherwise rotate into the S&P momentum sleeve. Monthly check, next-close execution,
10 bps per side, no leverage. The LLM overlay is optional and showed no edge.

## 2015-01-02 to 2026-11-24

|              | total_return   | cagr   | vol   |   sharpe |   sortino | max_dd   |   calmar |   beta | alpha   | tracking_error   | hit_rate   |   days |   sharpe_lo |   sharpe_hi |      diff_lo |     diff_ann |      diff_hi |   turnover_ann |   trades | exposure   |
|:-------------|:---------------|:-------|:------|---------:|----------:|:---------|---------:|-------:|:--------|:-----------------|:-----------|-------:|------------:|------------:|-------------:|-------------:|-------------:|---------------:|---------:|:-----------|
| bot          | 811.4%         | 20.5%  | 22.3% |     0.95 |      1.2  | -31.8%   |     0.64 |   1.09 | 6.1%    | 11.6%            | 56.6%      |   2990 |   0.437197  |     1.52155 |  0.0131171   |  0.0743575   |  0.132903    |           3.47 |      723 | 96.1%      |
| spy          | 321.9%         | 12.9%  | 17.6% |     0.79 |      0.96 | -33.7%   |     0.38 |   1    | -0.0%   | 0.0%             | 54.5%      |   2990 |   0.26295   |     1.35632 | -4.95992e-16 |  1.87079e-16 |  8.79504e-16 |           0    |        0 | 100.0%     |
| equal_weight | 286.5%         | 12.1%  | 17.7% |     0.73 |      0.9  | -37.5%   |     0.32 |   0.97 | -0.4%   | 4.8%             | 54.5%      |   2990 |   0.203567  |     1.34059 | -0.0347547   | -0.0072374   |  0.0196444   |           2.26 |    29245 | 98.0%      |
| random_p50   | 204.6%         | 9.8%   | 17.3% |     0.63 |      0.81 | -30.8%   |     0.32 |   0.87 | -1.1%   | 8.5%             | 53.4%      |   2990 |   0.109237  |     1.23687 | -0.0765682   | -0.0279119   |  0.019516    |          22.08 |     5506 | 96.9%      |
| random_p5    | 107.7%         | 6.4%   | 16.9% |     0.44 |      0.56 | -33.6%   |     0.19 |   0.84 | -4.4%   | 8.2%             | 52.9%      |   2990 |  -0.0781955 |     1.025   | -0.106358    | -0.0598769   | -0.0145144   |          21.9  |     5483 | 96.7%      |
| random_p95   | 279.1%         | 11.9%  | 17.5% |     0.74 |      0.95 | -29.0%   |     0.37 |   0.88 | 0.6%    | 8.8%             | 54.1%      |   2990 |   0.189916  |     1.33817 | -0.0544644   | -0.00949293  |  0.0341402   |          22.22 |     5519 | 97.0%      |

| strategy | $10,000 becomes |
|---|---:|
| bot | $91,136 |
| spy | $42,186 |
| equal_weight | $38,651 |
| random_p50 | $30,464 |
| random_p5 | $20,771 |
| random_p95 | $37,915 |

## 2026 only

|             | total_return   | cagr   | vol   |   sharpe |   sortino | max_dd   |   calmar |   beta | alpha   | tracking_error   | hit_rate   |   days |   sharpe_lo |   sharpe_hi |      diff_lo |    diff_ann |     diff_hi |   turnover_ann |   trades | exposure   |
|:------------|:---------------|:-------|:------|---------:|----------:|:---------|---------:|-------:|:--------|:-----------------|:-----------|-------:|------------:|------------:|-------------:|------------:|------------:|---------------:|---------:|:-----------|
| bot         | 9.4%           | 10.6%  | 30.2% |     0.49 |      0.71 | -24.8%   |     0.43 |   1.75 | 3.7%    | 17.6%            | 54.5%      |    224 |    -1.35283 |     2.45203 | -0.282154    | 0.0753311   | 0.41327     |           0.97 |        1 | 99.6%      |
| spy         | 5.3%           | 6.0%   | 15.6% |     0.41 |      0.56 | -11.5%   |     0.53 |   1    | -0.0%   | 0.0%             | 49.6%      |    224 |    -1.28442 |     2.34004 |  3.73035e-16 | 3.10862e-15 | 5.84421e-15 |           0    |        0 | 100.0%     |
| qqq_buyhold | 4.4%           | 5.0%   | 24.3% |     0.29 |      0.4  | -19.9%   |     0.25 |   1.48 | -2.4%   | 11.0%            | 52.2%      |    224 |    -1.49588 |     2.23731 | -0.212512    | 0.0076515   | 0.226751    |           0    |        0 | 100.0%     |
| vgt_buyhold | 9.7%           | 11.0%  | 30.2% |     0.49 |      0.71 | -24.8%   |     0.44 |   1.75 | 3.7%    | 17.6%            | 54.5%      |    224 |    -1.35283 |     2.45635 | -0.279753    | 0.0785835   | 0.415075    |           0    |        0 | 100.0%     |

| strategy | $10,000 becomes |
|---|---:|
| bot | $10,939 |
| spy | $10,534 |
| qqq_buyhold | $10,443 |
| vgt_buyhold | $10,971 |

## Verdict

- Full window: bot $91,136 vs SPY $42,186 (+48,950).
- 2026: bot $10,939 vs SPY $10,534 (+405, +4.05%).
- Max drawdown: bot -31.8% vs SPY -33.7% (full); -24.8% vs -11.5% (2026).
- Sharpe: bot 0.95 vs SPY 0.79 (full); 0.49 vs 0.41 (2026).
- Paired excess return: +7.4%/yr, 95% CI +1.3% to +13.3%. The 95% CI excludes zero on one
  path; treat it as evidence, not a guarantee.

## Caveats

- VGT is a tech-sector ETF: the edge is tech beta plus fewer sleeve months, not alpha.
- VGT buy-and-hold alone made $88,581; the rotation adds only ~$2.5k and trims drawdown.
- Random-signal shuffles put the rotation at the 55th percentile: no timing skill.
- 2026: +9.4% vs SPY +5.3%, but 2026 max drawdown -24.8% vs -11.5%.
- Walk-forward CAGR: 20.0% / 15.7% / 20.3% vs SPY 11.6% / 7.3% / 19.1%.
- The stock momentum + LLM sleeve showed no edge and is optional, not default.
- Paper only. No orders, no broker, no keys.
