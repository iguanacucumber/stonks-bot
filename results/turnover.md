# Turnover attribution and beta

Shipped config: 2.5x, daily 200-day cut, month-end, next open, 10 bps.

| market | turnover/yr | beta to SPY | correlation | trades |
|---|---:|---:|---:|---:|
| US | 15.1x | 2.34 | 0.66 | 1,693 |
| TECH | 16.2x | 2.20 | 0.77 | 2,000 |

## Where the turnover comes from

| market | reason | trades | total turnover (x NAV) | share |
|---|---|---:|---:|---:|
| TECH | rebalance | 529 | 146.3x | 76% |
| TECH | deleverage | 1,324 | 26.0x | 14% |
| TECH | risk_off | 147 | 19.9x | 10% |
| US | rebalance | 473 | 134.4x | 75% |
| US | deleverage | 1,088 | 25.8x | 14% |
| US | risk_off | 132 | 19.3x | 11% |

The point: **75% of turnover is the monthly rebalance**, not the daily
leverage trims (14%) or the trend cut (10%). The sleeve rotation is the cost
driver; the core-only tax profile cuts it, which is why it wins after tax.
