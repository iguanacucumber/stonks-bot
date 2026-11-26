# Intraday 8-K event drift

**Model/data status:** actual Yahoo 1h/5m intraday bars for a small set of
liquid SEC filers; 8-K acceptance timestamps from the cached SEC submissions.
No option quotes, no broker fills, no same-bar entry. This is an event-study
model, not broker-accounted P&L.

## Data and execution

- 1h: 12 tickers, 272 material 8-K events, 2024-11-27..2026-11-12; bars 09:30-16:00 ET, 2y cache.
- 5m: 12 tickers, 29 events, 2026-09-03..2026-11-12; 60d cache, execution-quality only.
- Symbols: ALB, AMAT, AMD, APH, CF, FANG, FDX, INTC, LUV, ON, OXY, TSLA.
- Event timestamp: SEC `acceptanceDateTime` converted to naive New York
  time. A missing timestamp falls back to the filing-day close and can only
  enter the next session.
- Entry: first **completed** bar whose start is strictly after acceptance,
  at that bar's open. A 16:00 auction print is never an entry bar; if the
  event is in the last hour, entry is next session 09:30.
- Exits: `close` = event-session close; `next_bar` = next bar close; `5d` =
  close of the fifth session after entry.
- Costs: 5 bps/side base, 10 bps/side 2x; includes spread/slippage, no
  separate commission. French PFU is applied annually to the equal-weight
  daily event return; idle days are zero.
- Placebo: date-shuffle of each event's entry bar within the same ticker;
  200 draws, same exit/horizon/filter/costs. `p` is one-sided in the trade
  direction (fraction of placebo means at least as good).

## Benchmarks

| book | sample | QQQ gross | QQQ after tax | BIL gross | BIL after tax |
|---|---|---:|---:|---:|---:|
| 1h_2y | 2024-11-26..2026-11-25 | 24.77% | 1.170x | 8.04% | 1.055x |
| 5m_60d | 2026-09-02..2026-11-25 | -10.35% | 0.897x | 0.86% | 1.006x |

| variant | n | hit 5bp | mean net 5bp | mean net 10bp | placebo p 5bp | placebo p 10bp | after-tax 5bp | after-tax 10bp | maxDD 5bp | maxDD 10bp | verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 1h_2y close long none | 271 | 42.07% | -0.63% | -0.73% | 1.000 | 1.000 | 0.260x | 0.212x | -75.26% | -79.52% | no (2x mean <=0) |
| 1h_2y close long high_vol | 198 | 45.45% | -0.61% | -0.71% | 1.000 | 1.000 | 0.350x | 0.296x | -65.75% | -70.85% | no (2x mean <=0) |
| 1h_2y close long high_volume | 162 | 46.91% | -0.31% | -0.41% | 0.905 | 0.905 | 0.576x | 0.504x | -50.79% | -53.78% | no (2x mean <=0) |
| 1h_2y close short none | 271 | 54.98% | 0.43% | 0.33% | 1.000 | 1.000 | 1.710x | 1.470x | -26.80% | -26.66% | reject: short diagnostic |
| 1h_2y close short high_vol | 198 | 52.53% | 0.41% | 0.31% | 1.000 | 1.000 | 1.485x | 1.315x | -24.94% | -25.07% | reject: short diagnostic |
| 1h_2y close short high_volume | 162 | 50.62% | 0.11% | 0.01% | 0.905 | 0.905 | 1.046x | 0.934x | -36.13% | -39.29% | reject: short diagnostic |
| 1h_2y next_bar long none | 272 | 42.65% | -0.48% | -0.58% | 1.000 | 1.000 | 0.327x | 0.266x | -68.73% | -74.15% | no (2x mean <=0) |
| 1h_2y next_bar long high_vol | 198 | 42.42% | -0.45% | -0.55% | 1.000 | 1.000 | 0.408x | 0.346x | -59.25% | -65.45% | no (2x mean <=0) |
| 1h_2y next_bar long high_volume | 163 | 47.24% | -0.20% | -0.30% | 0.821 | 0.821 | 0.602x | 0.527x | -51.44% | -54.26% | no (2x mean <=0) |
| 1h_2y next_bar short none | 272 | 51.84% | 0.28% | 0.18% | 1.000 | 1.000 | 1.504x | 1.295x | -31.37% | -31.34% | reject: short diagnostic |
| 1h_2y next_bar short high_vol | 198 | 51.52% | 0.25% | 0.15% | 1.000 | 1.000 | 1.387x | 1.231x | -28.57% | -28.52% | reject: short diagnostic |
| 1h_2y next_bar short high_volume | 163 | 46.01% | 0.00% | -0.10% | 0.821 | 0.821 | 1.033x | 0.920x | -40.62% | -43.62% | reject: short diagnostic |
| 1h_2y 5d long none | 272 | 53.31% | 0.59% | 0.49% | 0.383 | 0.383 | 1.792x | 1.505x | -76.50% | -77.41% | no (placebo p>=0.05) |
| 1h_2y 5d long high_vol | 198 | 52.53% | 0.61% | 0.51% | 0.473 | 0.473 | 1.755x | 1.550x | -63.70% | -64.53% | no (placebo p>=0.05) |
| 1h_2y 5d long high_volume | 163 | 55.83% | 1.28% | 1.18% | 0.229 | 0.229 | 2.704x | 2.418x | -48.02% | -48.99% | no (placebo p>=0.05) |
| 1h_2y 5d short none | 272 | 44.85% | -0.79% | -0.89% | 0.383 | 0.383 | 0.089x | 0.072x | -97.36% | -97.78% | reject: short diagnostic |
| 1h_2y 5d short high_vol | 198 | 45.45% | -0.81% | -0.91% | 0.473 | 0.473 | 0.113x | 0.096x | -95.79% | -96.27% | reject: short diagnostic |
| 1h_2y 5d short high_volume | 163 | 42.94% | -1.48% | -1.58% | 0.229 | 0.229 | 0.094x | 0.082x | -95.14% | -95.67% | reject: short diagnostic |
| 5m_60d close long none | 29 | 65.52% | 0.31% | 0.21% | 0.015 | 0.015 | 1.025x | 1.012x | -6.41% | -6.55% | execution only (n<30, 60d) |
| 5m_60d next_bar long none | 29 | 55.17% | 0.17% | 0.07% | 0.015 | 0.015 | 1.007x | 0.992x | -4.63% | -4.92% | execution only (n<30, 60d) |
| 5m_60d 5d long none | 29 | 58.62% | 0.05% | -0.05% | 0.189 | 0.189 | 1.047x | 1.034x | -20.14% | -19.80% | execution only (n<30, 60d) |

## Verdict

**Reject the intraday 8-K drift as a candidate.** No long variant beats both QQQ/BIL and the date-shuffle placebo at 2x costs with a usable sample. The closest rows are descriptive only.

The 5m sample is far below 30 events and is **not** a candidate; it is an
execution-quality check. The 1h sample is ~2 years and 272 events across 12
liquid names; overlapping events and one sector cluster mean the effective
sample is smaller. No same-bar fill is allowed.

## Sample limits

- 1h bars are Yahoo aggregates (not exchange MBO/L1); 2y window.
- 5m bars cover ~60 days; any result is execution quality, not a 2-year edge.
- Events are material 8-Ks only (`bot.news.MATERIAL`); routine 8-K items are
  excluded. Acceptance timestamps are SEC-reported and converted to ET.
- Equal-weight daily event returns assume one unit per event, no capital
  scaling, no borrow, no queue/partial fills; PFU is annual net.
- Placebo is date-shuffle, not a matched-volatility synthetic event; it
  tests whether the event date carries information beyond the ticker/time
  distribution.

Reproduce: `uv run python scripts/research_intraday_events.py` (add `--fetch`
only for the small liquid-name cache)
