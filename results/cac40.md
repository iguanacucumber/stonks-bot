# CAC 40 test: the same bot, a different market

Same engine, same rules, same costs. Only the market changed: 54 large French names, CAC 40
total-return benchmark, EXV3.DE (STOXX Europe 600 Technology) as the core because France has
no SOXX equivalent with a 2015 history. Window 2015-01-02 → 2026-11-24, $10k, 10 bps/side.

Reproduce: `.venv/bin/python scripts/cac40.py`

## Result: it loses to the index

| $10,000 → | final | CAGR | Sharpe | maxDD |
|---|---:|---:|---:|---:|
| **bot (same config)** | **$15,823** | **3.9%** | **0.28** | -37.0% |
| sleeve only (no core rotation) | $22,978 | 7.1% | 0.43 | -46.4% |
| CAC 40 total return | $26,644 | 8.5% | 0.55 | -38.5% |
| equal weight, 54 names | **$29,323** | 9.3% | 0.57 | -40.5% |
| EXV3.DE buy & hold (core only) | $32,227 | 10.2% | — | -40.3% |

The bot ends with **59% of the CAC 40** and **40% below equal weight**. It loses in all three
sub-periods:

| period | bot CAGR | bot $10k | CAC CAGR | CAC $10k |
|---|---:|---:|---:|---:|
| 2015-2019 | 2.4% | $11,277 | 10.2% | $16,379 |
| 2020-2022 | -4.2% | $8,772 | 4.8% | $11,539 |
| 2023-2026 | 4.5% | $11,891 | 8.2% | $13,627 |

2026 is the one win: bot +6.1% vs CAC +1.1% (drawdown -15.8%). Costs are not the story, but
they finish it: 0 / 10 / 30 / 50 bps → $17,661 / $15,823 / $12,703 / $10,199. France also
charges a 0.3% financial transaction tax on large-cap purchases since 2017, which the 30-50 bp
rows proxy.

## Why it fails

- **No stock-selection edge.** The sleeve's 7.1% CAGR sits at the **60th percentile** of 20
  random 6-name portfolios from the same universe (random median 6.4%, p5 3.4%, p95 11.4%).
  Momentum adds nothing here; equal weight beats it by 2.2 pp/yr.
- **The core timing signal is wrong-footed.** Holding EXV3.DE permanently made $32,787 (10.3%);
  the 126-day switch cut that to $15,823 by rotating into the sleeve at the wrong times.
- **French leadership rotates violently.** Luxury led 2015-2021 then de-rated, semis and banks
  led 2023-2026. A 12-1 momentum rule whipsawed: 607 trades, -37% drawdown, flat for a decade.
- **The US edge was the asset, not the rule.** In the US the same code rode SOXX; here the
  best "core" was itself the winner, and timing it destroyed 3 pp/yr.

## What would it take to win here

Tuning the window until it beats the CAC is possible but is curve fitting: a 315-day core
signal gives $30,375 (9.6%), and 252 days gives $26,178 (8.3%) — both still below buy-and-hold
EXV3.DE and below equal weight in risk-adjusted terms. A 42-day rebalance gives $22,651.
None of these is a robust improvement; they are the same overfit the ten US rounds were
criticised for, on a shorter, more concentrated market.

## Caveats

- **Not point-in-time membership.** The universe is the union of names that were CAC 40 members
  at some point in 2015-2026, held static. That is look-ahead (we know who joined), but it is
  also generous: it includes the losers (Atos -99%, Worldline, Casino, Ubisoft).
- Benchmark is CAC.PA (CAC 40 UCITS ETF, dividends reinvested); the price index ^FCHI alone
  returns 5.4%/yr and would make the bot look closer to par.
- French liquidity filter is €5M/day, price > €5, same as the US bot.
- Paper only.

## Verdict

**The bot does not transfer to the CAC 40.** The US result depends on a US semiconductor core
plus US momentum; in France the same rules lose to the index, to equal weight, and to simply
holding the core. That is the useful outcome of the test: it says the ten rounds found a US
asset, not a market-neutral strategy.
