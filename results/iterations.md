# Five improvement iterations

Starting point: QQQ/SPY 252-day rotation with an S&P momentum fallback. Monthly, next-close,
10 bps/side, no leverage.

| iteration | change tested | full $10k | 2026 $10k | decision |
|---|---|---:|---:|---|
| 1 robustness | split, costs, lookback | $45,417* | $11,212* | keep 252d |
| 2 core asset | QQQ vs VGT/SMH/QLD/XLK | $91,136 | $10,939 | adopt VGT |
| 3 fallback | cash/SPY/momentum/sector-9 | $91,136 | $10,939 | keep momentum |
| 4 leverage | 1.1-1.5x, QLD, vol target | $54,005* | — | reject |
| 5 final | fix execution, VGT + momentum | **$91,136** | **$10,939** | freeze |

\*Pre-fix numbers. Iteration 3 found a zero-weight execution bug: QQQ:0.0 was sold last, so the
sleeve bought 0 shares and the bot sat in cash for 21 days after each switch. The clean QQQ base
is $54,005 / 2026 $10,239. The final row is the corrected bot.

## Iteration 1 — baseline robustness (pre-fix)

Walk-forward: 2015-2019 +9.5% vs SPY +11.6%; 2020-2022 +25.5% vs +7.3%; 2023-2026 +20.7% vs +19.1%.
Costs: 14.0% / 13.6% / 13.1% / 12.7% CAGR at 0 / 10 / 20 / 30 bps.
Lookbacks 126/189/252/315d: 13.1% / 13.1% / 13.6% / 14.1%; only 252d also won 2026.
**Decision:** keep 252d; the edge was regime-dependent and died at 30 bps.

## Iteration 2 — core asset (fixed engine, momentum fallback)

| core | full CAGR | full $10k | Sharpe | maxDD | 2026 |
|---|---:|---:|---:|---:|---:|
| QQQ | 15.3% | $54,005 | 0.82 | -28.6% | +2.4% |
| **VGT** | **20.5%** | **$91,136** | **0.95** | -31.8% | **+9.4%** |
| XLK | 17.0% | $64,681 | 0.80 | -32.3% | +8.9% |
| SMH | 23.6% | $123,677 | 0.88 | -40.2% | +12.9% |
| QLD (2x QQQ) | 19.7% | $84,156 | 0.66 | -55.1% | +6.1% |
| SPY (sleeve only) | 11.2% | $35,072 | 0.67 | -22.5% | +0.6% |

VGT beats QQQ at 126/252/504d lookbacks; paired bootstrap vs QQQ is +4.97%/yr, 95% CI
+0.71% to +9.13%; sub-period CAGR 20.0% / 20.7% / 20.6%. SMH is a single-industry bet with a
-40.2% drawdown. QLD is leverage with -55% DD and worse Sharpe.
**Decision:** adopt VGT. The edge is tech beta, not stock alpha; VGT buy-and-hold alone made
$88,581, so the rotation adds ~$2.5k and trims drawdown.

## Iteration 3 — fallback asset and the execution bug

| fallback | full $10k (QQQ core) | full $10k (VGT core) | 2022 |
|---|---:|---:|---:|
| cash | $41,919 | — | — |
| SPY | $57,229 | — | -20.0% |
| **momentum sleeve** | $54,005 | **$91,136** | -12.7% |
| sector-9 12-1 top 3 | $60,851 | $82,846 | +5.9% |
| 50/50 SPY+momentum | $54,757 | — | -18.0% |

Sector-9 is the best fallback for QQQ, but after switching the core to VGT, momentum is better
($91,136 vs $82,846) because VGT leads 86% of months and the fallback is rare. The same round
found the zero-weight execution bug; fixed by dropping zero-weight targets before selling.
**Decision:** keep the momentum fallback with VGT. Sector-9 remains available for a QQQ core.

## Iteration 4 — leverage and sizing

Synthetic 1.1x / 1.2x / 1.3x / 1.5x on the pre-fix base: $49,013 / $52,659 / $56,326 / $63,588;
Sharpe falls 0.762 -> 0.741 / 0.724 / 0.710 / 0.687; maxDD -31.6% -> -34.7% / -37.8% / -43.7%.
QLD 100%: $90,207 but -52.4% DD and 0.690 Sharpe. Vol targeting with a 1.0 floor loses.
**Decision:** reject leverage. A better core (VGT) adds more return than borrowing.

## Iteration 5 — integrate and validate

Final rule: hold VGT while its 252-day total return beats SPY; otherwise hold the S&P momentum
sleeve (top 20, 126d momentum, inverse-vol, 6%/30% caps, 20% stock stop, SPY 200d regime).
Monthly, next-close, 10 bps/side, no leverage.

| | bot | SPY |
|---|---:|---:|
| $10,000 becomes | **$91,136** | $42,186 |
| CAGR | 20.5% | 12.9% |
| Sharpe | 0.95 | 0.79 |
| max drawdown | -31.8% | -33.7% |
| 2026 $10k | **$10,939** | $10,534 |

Walk-forward: 2015-2019 +20.0% vs +11.6%; 2020-2022 +15.7% vs +7.3%; 2023-2026 +20.3% vs +19.1%.
Costs 0/10/20/30 bps: $95,160 / $91,136 / $87,279 / $83,582. Paired excess +7.4%/yr, 95% CI
+1.3% to +13.3%. 2026 drawdown is worse (-24.8% vs -11.5%) because VGT is a tech book.

**Decision:** freeze. Paper only. The stock momentum + LLM sleeve remains optional and showed
no edge; the default is VGT plus the momentum fallback.
