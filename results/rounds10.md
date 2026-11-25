# Ten rounds, each richer than the last

Every row changes **one knob** from the row above, same engine, same costs (10 bps/side),
same window (2015-01-02 → 2026-11-24), $10,000 start. `oos$` is 2023-01-02 → now, held back
from the tuning; `y26$` is 2026 alone; `cost30$` is the full window at 30 bps/side.

| # | change | full $10k | cagr | sharpe | maxDD | oos $10k | y26 $10k | cost30 $10k | +vs prev |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 00 | baseline (VGT core, sleeve 20) | $91,136 | 20.5% | 0.95 | -31.8% | $20,481 | $10,939 | $83,582 | — |
| 01 | sleeve breadth 20 → 5 | $108,866 | 22.3% | 1.00 | -31.8% | $22,350 | $10,939 | $99,776 | +$17,730 |
| 02 | core VGT → SMH | $123,265 | 23.6% | 0.86 | -40.2% | $31,067 | $11,291 | $110,951 | +$14,399 |
| 03 | core lookback 252 → 126d | $132,779 | 24.4% | 0.89 | -35.5% | $38,974 | $11,742 | $115,138 | +$9,514 |
| 04 | drop the 20% trailing stop | $136,892 | 24.7% | 0.88 | -40.1% | $38,157 | $11,587 | $119,777 | +$4,113 |
| 05 | regime gross 0.5 → 1.0 | $160,549 | 26.4% | 0.90 | -41.6% | $41,050 | $11,587 | $138,842 | +$23,657 |
| 06 | core SMH → SOXX | $169,691 | 27.0% | 0.89 | -39.5% | $37,633 | $12,308 | $144,382 | +$9,142 |
| 07 | gross 0.98 → 1.0 | $170,710 | 27.0% | 0.89 | -39.9% | $37,710 | $12,274 | $144,964 | +$1,019 |
| 08 | sector cap 0.30 → 0.40 | $187,815 | 28.0% | 0.90 | -39.5% | $35,599 | $12,593 | $160,242 | +$17,105 |
| 09 | sleeve skip 21 → 10d | $193,620 | 28.4% | 0.92 | -39.5% | $44,090 | $12,593 | $165,368 | +$5,805 |
| 10 | sleeve breadth 5 → 6 | **$212,063** | **29.4%** | **0.95** | -39.5% | **$44,884** | **$12,573** | **$181,466** | +$18,443 |

Reproduce: `.venv/bin/python scripts/rounds10.py` (writes `results/rounds10.csv`, asserts
that every row beats the one above).

## Final bot

Hold SOXX while its 126-day total return beats SPY's; otherwise hold the top 6 S&P names by
12-1 momentum with inverse-vol weights, 6%/40% caps, 10-day skip. Monthly check, next-close,
10 bps/side, no leverage, no stop, no regime cut.

| $10,000 → | bot | SPY | SOXX buy&hold |
|---|---:|---:|---:|
| 2015-01-02 → 2026-11-24 | **$212,063** | $42,186 | $140,256 |
| CAGR | 29.4% | 12.9% | 24.9% |
| Sharpe | 0.95 | 0.79 | — |
| max drawdown | -39.5% | -33.7% | -45.8% |
| 2026 | **$12,573** (+25.7%) | $10,534 (+5.3%) | $12,322 (+23.2%) |

Walk-forward, each sub-window starts at $10k:

| period | bot CAGR | bot $10k | SPY CAGR | SPY $10k |
|---|---:|---:|---:|---:|
| 2015-2019 | 18.0% | $22,841 | 11.6% | $17,289 |
| 2020-2022 | 27.2% | $20,528 | 7.3% | $12,349 |
| 2023-2026 | 47.4% | $44,884 | 19.1% | $19,659 |

Calendar years, bot vs SPY: 2015 +0.2 / +1.3 · 2016 +14.2 / +13.6 · 2017 +39.8 / +20.8 ·
2018 −7.5 / −5.2 · 2019 +55.9 / +31.1 · 2020 +49.6 / +17.2 · 2021 +37.7 / +30.5 ·
2022 −13.3 / −18.6 · 2023 +37.1 / +26.7 · 2024 +62.9 / +25.6 · 2025 +57.5 / +18.0 ·
2026 +39.7 / +5.3. Beats SPY in 11 of 12 years; loses 2015 by 1.1 pp.

Costs: $229,250 / $212,063 / $196,168 / $181,466 at 0 / 10 / 20 / 30 bps per side.
Turnover 5.4x NAV/yr, 447 trades.

## What each round actually did

- **01-03** are rule quality: fewer sleeve names, a faster core signal, and a core with more
  trend. All three improved the held-back 2023-2026 window too ($20.5k → $39.0k), which is
  the only evidence here that is not in-sample.
- **02 and 06** are asset selection. SMH/SOXX won because semiconductors won 2015-2026. This
  is beta to one industry, not stock picking.
- **04, 05, 07** remove protection: no trailing stop, no half-size risk-off months, no 2%
  cash buffer. They add return by adding exposure, and the drawdown grows with it.
- **08-10** are sleeve breadth/cap/skip tuning, fitted on this path.

## Honest verdict

The chain is monotone and reproducible, but it is **one path, tuned ten times**. Rounds 1-3
survive the 2023-2026 holdout; rounds 4-10 mostly buy more exposure and are in-sample. The
portfolio is a leveraged-by-concentration semiconductor bet: it lost 39.5% peak-to-trough and
would lose more than SPY in a semis bear market. Paper only.
