# v9 summary — more short-horizon families, still no edge

Date: 2026-11-26. Actual intraday data used. All short-horizon families rejected;
forward gate still WAIT. No satellite allocation.

## Intraday pairs / lead-lag (actual 1h 2y, 5m 60d)

| family | best 1h | n | after-tax | maxDD |
|---|---|---:|---:|---:|
| lead-lag SMH→QQQ | lag 2 momentum | 1,942 | 0.185x | -81.9% |
| intraday pair SMH/SOXX | z=1.5 hold=3 long-only | 391 | **1.078x** | -19.2% |
| cross-sectional 1-bar reversal | long-only | 2,981 | 0.046x | -95.5% |

QQQ same 2y sample: **1.247x**; best pair 0.80x at 2x costs. Best 5m 0.821x vs QQQ
0.897x. **Reject.**

## Intraday 8-K drift

272 material 8-K events, 12 liquid names, 1h 2y; 29 events on 5m 60d.

| variant | n | mean 10bp | placebo p | after-tax 10bp |
|---|---:|---:|---:|---:|
| close long | 271 | -0.73% | 1.000 | 0.212x |
| 5d long | 272 | +0.49% | 0.383 | 1.505x |
| 5d long high-volume | 163 | +1.18% | 0.229 | 2.418x |
| 5m close long | 29 | +0.21% | 0.015 | 1.012x (execution only) |

No long variant beats both QQQ/BIL and the date-shuffle placebo with a usable
sample. **Reject.**

## Daily sector / calendar / breakout (walk-forward 2019-2026)

| family | OOS 5bps | OOS 10bps | OQQ OOS | maxDD | positive folds |
|---|---:|---:|---:|---:|---:|
| sector reversal | 1.29x | 1.12x | 2.80x | -35.9% | 6/8 |
| calendar | 1.01x | 0.91x | 2.80x | -21.2% | 5/8 |
| volatility breakout | 0.91x | 0.87x | 2.80x | -14.8% | 2/8 |

All below QQQ; most below BIL at 2x. **Reject.**

## Verdict

No short-horizon candidate clears costs/BIL/QQQ. The complete conclusion and all
trial registries are in `results/short_horizon_conclusion.md`,
`results/intraday_pairs.md`, `results/intraday_events.md`,
`results/short_daily.md`.

Forward gate: **US 0/5, TECH 1/5, tax 0/5**, ETA 5/5 2026-12-02/03 if no missed
sessions. No dates fabricated. Weekday automation at 21:45 UTC.

This is a negative-results release, not a done short-term system.
