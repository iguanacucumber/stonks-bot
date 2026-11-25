# Five improvement iterations

Starting point: QQQ/SPY 252-day rotation with an S&P momentum fallback. Monthly,
next-close, 10 bps/side, no leverage.

| iteration | change tested | full $10k | 2026 $10k | decision |
|---|---|---:|---:|---|
| 1 robustness | split, costs, lookback | $45,417* | $11,212* | keep 252d |
| 2 core asset | QQQ vs VGT/XLK/SMH/QLD/SPY | $60,851 | $10,526 | keep QQQ |
| 3 fallback | cash/SPY/momentum/sector-9 | $60,851 | $10,526 | adopt sector-9 |
| 4 leverage | 1.1-1.5x, QLD, vol target | $45,417* | — | reject |
| 5 final | execution fix + sector-9 + no ETF stops | **$60,851** | **$10,526** | freeze |

\* Pre-fix numbers. Iteration 3 found a zero-weight execution bug (QQQ:0.0 caused a 21-day
cash gap after each switch). The clean base is $54,005. The final row is the corrected bot.

## Iteration 1 — baseline robustness

| period | bot CAGR | bot $10k | SPY CAGR | SPY $10k |
|---|---:|---:|---:|---:|
| 2015-2019 | 9.5% | $15,686 | 11.6% | $17,289 |
| 2020-2022 | 25.5% | $19,744 | 7.3% | $12,349 |
| 2023-2026 | 20.7% | $20,709 | 19.1% | $19,659 |

Costs: 14.0% / 13.6% / 13.1% / 12.7% CAGR at 0 / 10 / 20 / 30 bps.
Lookback 126/189/252/315d full: 13.1% / 13.1% / 13.6% / 14.1%; only 252d also wins 2026.
**Decision:** keep 252d; the edge is regime-dependent and dies at 30 bps.

## Iteration 2 — core asset (sector-9 fallback held fixed)

| core | full CAGR | full $10k | Sharpe | maxDD | 2026 |
|---|---:|---:|---:|---:|---:|
| **QQQ** | 16.4% | **$60,851** | 0.86 | -28.6% | 5.3% |
| VGT | 18.9% | $82,846 | 0.90 | -31.8% | 9.4% |
| XLK | 16.4% | $60,664 | 0.77 | -35.5% | 8.9% |
| SMH | 22.9% | $126,648 | 0.87 | -40.2% | 12.9% |
| QLD (2x QQQ) | 20.5% | $91,835 | 0.68 | -51.7% | 9.1% |
| SPY | 11.3% | $33,485 | 0.67 | -37.6% | 15.1% |

**Decision:** keep QQQ. VGT/SMH/QLD make more in-sample but are concentrated tech/semis or
leveraged bets with worse drawdowns; choosing them is ex-post sector selection, not a robust
core. QQQ + sector-9 is the diversified version.

## Iteration 3 — fallback asset

| fallback when QQQ lags | full CAGR | full $10k | Sharpe | 2022 | 2026 |
|---|---:|---:|---:|---:|---:|
| cash | 12.8% | $41,919 | 0.76 | — | 12.3%* |
| SPY | 15.8% | $57,229 | 0.81 | -20.0% | 5.8% |
| momentum sleeve | 15.3% | $54,005 | 0.82 | -12.7% | 4.7% |
| 50/50 SPY+momentum | 15.4% | $54,757 | 0.82 | -18.0% | 5.3% |
| **sector-9 12-1 top 3 EW** | **16.4%** | **$60,851** | **0.86** | **+5.9%** | **5.3%** |
| stock low-vol top 10 | 15.8% | $57,005 | 0.83 | — | 11.1%* |

\* Inflated by the execution bug; corrected numbers are lower.

The fallback runs only ~24% of days. Sector-9 is the only arm that protects in 2022, survives
20 bps, has no membership/survivorship, and has fewer moving parts than the stock sleeve.
**Decision:** adopt sector-9 top 3 equal weight. Also fix the zero-weight execution bug.

## Iteration 4 — leverage and sizing

Synthetic 1.1x / 1.2x / 1.3x / 1.5x on the pre-fix base: $49,013 / $52,659 / $56,326 / $63,588.
Sharpe falls 0.762 -> 0.741 / 0.724 / 0.710 / 0.687; maxDD -31.6% -> -34.7% / -37.8% / -43.7%.
QLD 100%: $90,207 but -52.4% DD and 0.690 Sharpe. Vol targeting with a 1.0 floor loses.
**Decision:** reject leverage. The sector fallback adds return without borrowing; leverage buys
beta more expensively than a better asset mix.

## Iteration 5 — integrate and validate

- Fixed `backtest.run` to drop zero-weight targets before selling, removing the 21-day cash gap.
- Default: QQQ while its 252d return > SPY, else top-3 sector-9 12-1 momentum, equal weight.
- Sector ETFs and the core are exempt from the stock trailing stop (the tested rule has none).
- Removed stale membership/ticker aliases and added the 2026 changes before this run.

Final, 10 bps, 2015-01-02 -> 2026-11-24:

| | bot | SPY |
|---|---:|---:|
| $10,000 becomes | **$60,851** | $42,186 |
| CAGR | 16.4% | 12.9% |
| Sharpe | 0.86 | 0.79 |
| max drawdown | **-28.6%** | -33.7% |
| 2026 $10k | $10,526 | $10,534 |

Walk-forward: 2015-2019 +12.8% vs 11.6%; 2020-2022 +32.0% vs 7.3%; 2023-2026 +15.6% vs 19.1%.
Costs: 0/10/20/30 bps -> $64,305 / $60,927 / $57,723 / $54,687 (pre-stop-change; final differs
by <0.2%). Paired excess +3.5%/yr, 95% CI -1.4% to +8.4%.

**Decision:** freeze. The full-window terminal wealth win is large; 2026 is a tie; 2023-2026
lags SPY. Paper only.
