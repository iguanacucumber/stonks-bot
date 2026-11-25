# Rejected experiments

Ideas tested and not shipped. Numbers are 2015-2026, $10k, 2x, 500 bps unless stated.
Keeping this file is cheaper than re-running bad ideas.

## 1. Benchmark 200-day trend overlay

Scale exposure when SPY/SMH is below its 200-day MA, monthly sampled.

| variant | US final | US DD | 2022 | TECH final | TECH DD |
|---|---:|---:|---:|---:|---:|
| none | **$889,382** | -60.7% | -30.6% | **$676,182** | -71.6% |
| SPY<MA -> 50% | $555,000 | -59.2% | -20.7% | $466,000 | -58.0% |
| SPY<MA -> cash | $241,000 | -59.2% | -17.7% | $242,000 | -58.0% |
| SMH<MA -> 50% | $775,000 | -58.0% | -20.4% | $672,000 | -59.5% |

Verdict: **monthly sampling** is a leverage knob, not a signal. The daily version is
different and is now shipped: checked every day, 2.5x + 200d cut to 50% makes $1,368,999
vs $1,222,440 for plain 2.5x while cutting the drawdown from -69.4% to -59.9%
(`results/leverage.md`). Sampling frequency was the whole difference. Watching the
benchmark instead of the core is worse at every lookback ($694k US vs $1,369k), and
"either" is worse too; 250d adds $14k but 9pp more drawdown. Core-200d stands.

## 2. Core rotation among tech ETFs

Monthly top-1 by 126d/10d from {SOXX, SMH, XLK, QQQ, VGT}.

| strategy | final | CAGR | Sharpe | maxDD |
|---|---:|---:|---:|---:|
| rotate 5 ETFs | $425,760 | 37.2% | 0.83 | -66.3% |
| rotate 3 (SOXX, SMH, QQQ) | $462,111 | 38.1% | 0.84 | -69.8% |
| always SMH | $522,854 | 39.6% | 0.84 | -73.5% |
| **default dual-momentum book** | **$889,382** | **46.0%** | **0.93** | **-60.7%** |

Verdict: rotation is a mild improvement over always-SOXX and loses to always-SMH, the
hindsight-best name in its own pool. Rotation-minus-SOXX mean return is -0.3%/yr (t=-0.05)
full-window. The signal is fine; the pool choice is the result.

## 3. Volatility targeting

Scale gross to a target portfolio vol, cap 3x, monthly.

| target | US final | avg gross | maxDD | Sharpe |
|---|---:|---:|---:|---:|
| const 2x | $762,997 | 1.93 | -66.1% | 0.898 |
| 30% | $1,138,318 | 2.64 | -73.3% | 0.902 |
| 22% | $412,017 | 1.55 | -52.5% | 0.905 |
| 15% | $173,640 | 1.14 | -40.3% | 0.885 |

Verdict: the target is just a leverage dial. Sharpe is flat across the row; the 30% row
makes more money because it borrows more, not because it times risk.

## 4. Merged S&P + Nasdaq universe

Union of point-in-time S&P 500 and the 49 Nasdaq names, same rules.

| universe | final |
|---|---:|
| S&P only (SOXX core at the time) | $762,997 |
| merged + SOXX core | $737,092 |
| Nasdaq only + SMH core | $676,182 |
| **S&P + SMH core** | **$889,382** |

Verdict: a bigger universe dilutes the sleeve. Rejected.

## 5. Drawdown circuit breaker

At -X% from the equity peak, cut gross to 50% until the drawdown recovers to -10%.

| stop | US final | maxDD | Sharpe |
|---|---:|---:|---:|
| off | **$889,382** | -60.7% | 0.927 |
| 20% | $326,792 | -46.2% | 0.903 |
| 25% | $434,294 | -44.7% | 0.936 |
| 30% | $534,479 | -46.7% | 0.947 |

Verdict: halves the money to cut the drawdown by ~15pp. Sharpe rises slightly, so it is a
legitimate risk preference, not an improvement. Available as `--dd-stop` (off by default).

## 6. Faster rebalancing

Weekly and daily clocks: 60-300x NAV turnover, worse 1-day and 1-week means, and at 2x the
S&P book goes from $889k to $22k (weekly) / $1.5k (daily). See `results/short.md`.

## 7. Next-open execution

Signal at the close, fill at the next open instead of the next close (removes one day
of lag, pays the overnight gap).

| market | close fill | open fill | close DD | open DD |
|---|---:|---:|---:|---:|
| S&P 500 | **$1,402,314** | $995,571 | -59.9% | -65.4% |
| NASDAQ tech | **$1,226,554** | $1,111,132 | -63.1% | -63.3% |

Verdict: the overnight gap is a cost, not a free lunch. Momentum names tend to gap in
the signal's direction, so buying at the open pays up. Close fill stays the default;
`exec_price="open"` remains available for a different execution assumption.

## 8. SOXX/SMH rotation

Top-1 by 126d/10d between SOXX and SMH: $545k at 2x, below SMH alone ($889k). Rejected.
