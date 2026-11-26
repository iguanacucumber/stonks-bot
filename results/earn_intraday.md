# Intraday earnings 2.02 continuation/fade

**Model/data status:** cached Yahoo 1h/2y and 5m/60d bars plus cached SEC 8-K acceptance timestamps. No network, no synthetic rows, no option quotes, no broker fills, no same-bar entry. This is an event-study model, not broker-accounted P&L.

## Method

- Event: 8-K whose SEC items include **2.02** (results of operations). Acceptance time is converted to naive New York time.
- Signal bar: first **completed** regular-session bar whose start is strictly after acceptance. A 16:00 auction print is never a bar.
- Entry: open of the **next** bar after the signal bar. No same-bar fill. For an after-close release this means the next session's 10:30 ET open, not the opening print; the overnight gap is given up.
- Condition: opening gap (signal-bar open vs prior close), first-bar reaction (signal-bar close/open), or total move (signal-bar close vs prior close). Continuation trades the sign; fade trades against it.
- Exits: entry-session close (`close`), next-session close (`next_session`), or close five sessions after entry (`5d`).
- Costs: 5 bps/side base and 10 bps/side at 2x. Shorts pay 5%/yr borrow (10%/yr at 2x); short proceeds earn no interest. Longs are cash-backed, so there is no leverage financing.
- Portfolio: maximum five concurrent events, 20% of equity each, earliest events first. Idle cash earns BIL (a zero-idle multiple is kept in the CSV notes/columns as a conservative check).
- Tax: annual French PFU (31.4% from 2026, 30% earlier) on net portfolio P&L, ten-year loss carry.
- Placebo: each event is moved to a random non-event session for the same ticker, keeping its time of day; 200 draws, same horizon/costs/direction. `p` is one-sided: fraction of placebo mean net returns at least as good.

## Sample

- **1h/2y:** 121 item-2.02 events across 12 symbols, event dates 2024-12-19..2026-11-12; bars 2024-11-26..2026-11-25.
  - Per symbol: ALB 8, AMAT 8, AMD 8, APH 8, CF 8, FANG 16, FDX 9, INTC 8, LUV 8, ON 8, OXY 16, TSLA 16.
  - Acceptance buckets: after_close 95, pre_open 22, intraday 4; timestamp source: accepted 121.
- **5m/60d:** 15 item-2.02 events across 12 symbols, event dates 2026-10-02..2026-11-12; bars 2026-09-02..2026-11-25.
  - Per symbol: ALB 1, AMAT 1, AMD 1, APH 1, CF 1, FANG 2, FDX 1, INTC 1, LUV 1, ON 1, OXY 2, TSLA 2.
  - Acceptance buckets: after_close 12, pre_open 2, intraday 1; timestamp source: accepted 15.
- The screen is the SEC item code, not a hand-curated quarterly earnings list. FANG, OXY and TSLA file 2.02 more often than quarterly (16 filings each in the 2y window); the other nine names have 8-9. The 121-event 1h sample is therefore broader than quarterly earnings. A nine-name quarterly-only subset is reported below as a sensitivity.
- 5m has only 15 events in 60 days. It is an execution-quality check, never evidence for a candidate.

## Benchmarks

| book | sample | QQQ gross | QQQ after tax | BIL gross | BIL after tax |
|---|---|---|---|---|---|
| 1h_2y | 2024-11-26..2026-11-25 | +24.77% | 1.170x | +8.04% | 1.055x |
| 5m_60d | 2026-09-02..2026-11-25 | -10.35% | 0.897x | +0.86% | 1.006x |

## 1h variants, all registered

Sorted by 2x after-tax multiple. `x` includes BIL carry on idle cash; `x_idle` is the same path with zero idle return. `n` is usable events; `traded` is after the five-concurrent cap. The trial registry has 218 rows: 72 for 1h, 72 for 5m, 72 for the full-deploy sizing check and 2 quarterly-only sensitivity rows.

| signal | mode | thr bps | exit | n | traded | hit 5bp | mean 5bp | mean 10bp | p 5bp | p 10bp | x 5bp | x 10bp | x10 idle | maxDD 10bp | verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| move | continuation | 0 | 5d | 121 | 115 | 49.6% | +0.85% | +0.70% | 0.070 | 0.095 | 1.179x | 1.149x | 1.094x | -13.5% | no (placebo p>=0.05) |
| gap | continuation | 0 | 5d | 121 | 115 | 53.7% | +0.74% | +0.58% | 0.085 | 0.100 | 1.160x | 1.131x | 1.078x | -13.8% | no (placebo p>=0.05) |
| move | continuation | 200 | 5d | 81 | 80 | 51.9% | +0.71% | +0.55% | 0.264 | 0.274 | 1.138x | 1.117x | 1.060x | -12.2% | no (placebo p>=0.05) |
| reaction | continuation | 0 | 5d | 121 | 115 | 52.9% | +0.56% | +0.41% | 0.149 | 0.179 | 1.130x | 1.101x | 1.049x | -12.0% | no (placebo p>=0.05) |
| gap | fade | 0 | close | 121 | 121 | 56.2% | +0.25% | +0.15% | 0.035 | 0.035 | 1.099x | 1.080x | 1.021x | -6.1% | no (below QQQ after tax) |
| reaction | continuation | 200 | close | 55 | 55 | 52.7% | +0.33% | +0.22% | 0.189 | 0.189 | 1.082x | 1.073x | 1.017x | -2.2% | no (placebo p>=0.05) |
| gap | continuation | 200 | next_session | 77 | 77 | 53.2% | +0.26% | +0.15% | 0.408 | 0.413 | 1.083x | 1.070x | 1.015x | -3.9% | no (placebo p>=0.05) |
| move | fade | 200 | close | 81 | 81 | 53.1% | +0.23% | +0.13% | 0.289 | 0.289 | 1.082x | 1.069x | 1.008x | -5.6% | no (placebo p>=0.05) |
| gap | fade | 200 | close | 77 | 77 | 49.4% | +0.17% | +0.07% | 0.338 | 0.338 | 1.074x | 1.062x | 0.997x | -5.6% | no (placebo p>=0.05) |
| reaction | continuation | 200 | 5d | 55 | 55 | 50.9% | +0.23% | +0.07% | 0.378 | 0.393 | 1.074x | 1.060x | 1.005x | -8.0% | no (placebo p>=0.05) |
| move | continuation | 0 | next_session | 121 | 121 | 54.5% | +0.16% | +0.04% | 0.194 | 0.214 | 1.078x | 1.057x | 1.004x | -5.6% | no (placebo p>=0.05) |
| reaction | continuation | 200 | next_session | 55 | 55 | 54.5% | +0.14% | +0.02% | 0.478 | 0.483 | 1.064x | 1.055x | 0.999x | -4.6% | no (placebo p>=0.05) |
| move | fade | 0 | close | 121 | 121 | 52.1% | +0.09% | -0.01% | 0.159 | 0.169 | 1.068x | 1.050x | 0.989x | -5.1% | no (2x mean<=0) |
| gap | continuation | 200 | 5d | 77 | 76 | 50.6% | +0.29% | +0.13% | 0.338 | 0.343 | 1.067x | 1.049x | 0.996x | -9.6% | no (placebo p>=0.05) |
| move | continuation | 200 | next_session | 81 | 81 | 54.3% | +0.08% | -0.03% | 0.408 | 0.408 | 1.061x | 1.047x | 0.992x | -5.6% | no (2x mean<=0) |
| reaction | continuation | 0 | close | 121 | 121 | 50.4% | +0.04% | -0.06% | 0.179 | 0.179 | 1.061x | 1.042x | 0.984x | -3.5% | no (2x mean<=0) |
| gap | continuation | 0 | next_session | 121 | 121 | 51.2% | +0.01% | -0.11% | 0.353 | 0.363 | 1.052x | 1.032x | 0.972x | -5.1% | no (2x mean<=0) |
| reaction | continuation | 0 | next_session | 121 | 121 | 47.9% | -0.03% | -0.14% | 0.378 | 0.388 | 1.046x | 1.026x | 0.965x | -7.0% | no (2x mean<=0) |
| reaction | fade | 200 | close | 55 | 55 | 43.6% | -0.53% | -0.63% | 0.816 | 0.816 | 1.012x | 1.004x | 0.932x | -6.7% | no (2x mean<=0) |
| gap | continuation | 200 | close | 77 | 77 | 46.8% | -0.37% | -0.47% | 0.667 | 0.667 | 1.012x | 1.000x | 0.928x | -5.5% | no (2x mean<=0) |
| reaction | fade | 200 | next_session | 55 | 55 | 43.6% | -0.36% | -0.47% | 0.527 | 0.527 | 1.009x | 0.999x | 0.938x | -8.6% | no (2x mean<=0) |
| reaction | fade | 0 | close | 121 | 121 | 46.3% | -0.24% | -0.35% | 0.826 | 0.831 | 1.010x | 0.988x | 0.918x | -8.9% | no (2x mean<=0) |
| move | continuation | 200 | close | 81 | 81 | 42.0% | -0.43% | -0.53% | 0.716 | 0.716 | 1.003x | 0.987x | 0.916x | -4.2% | no (2x mean<=0) |
| move | fade | 200 | next_session | 81 | 81 | 44.4% | -0.31% | -0.42% | 0.597 | 0.607 | 1.001x | 0.986x | 0.927x | -10.6% | no (2x mean<=0) |
| move | continuation | 0 | close | 121 | 121 | 44.6% | -0.29% | -0.39% | 0.846 | 0.851 | 1.003x | 0.979x | 0.908x | -3.8% | no (2x mean<=0) |
| gap | fade | 0 | next_session | 121 | 121 | 47.9% | -0.23% | -0.34% | 0.652 | 0.662 | 1.001x | 0.979x | 0.917x | -9.9% | no (2x mean<=0) |
| reaction | fade | 0 | next_session | 121 | 121 | 51.2% | -0.20% | -0.31% | 0.627 | 0.637 | 0.997x | 0.974x | 0.917x | -13.0% | no (2x mean<=0) |
| gap | fade | 200 | next_session | 77 | 77 | 45.5% | -0.49% | -0.59% | 0.597 | 0.607 | 0.982x | 0.968x | 0.909x | -8.3% | no (2x mean<=0) |
| reaction | fade | 200 | 5d | 55 | 55 | 47.3% | -0.53% | -0.67% | 0.627 | 0.642 | 0.976x | 0.964x | 0.909x | -13.1% | no (2x mean<=0) |
| gap | fade | 200 | 5d | 77 | 76 | 49.4% | -0.59% | -0.74% | 0.672 | 0.687 | 0.973x | 0.955x | 0.901x | -14.6% | no (2x mean<=0) |
| move | fade | 0 | next_session | 121 | 121 | 44.6% | -0.38% | -0.49% | 0.811 | 0.821 | 0.971x | 0.949x | 0.885x | -13.2% | no (2x mean<=0) |
| gap | continuation | 0 | close | 121 | 121 | 40.5% | -0.45% | -0.55% | 0.970 | 0.970 | 0.963x | 0.940x | 0.873x | -7.9% | no (2x mean<=0) |
| move | fade | 200 | 5d | 81 | 80 | 48.1% | -1.01% | -1.15% | 0.741 | 0.741 | 0.877x | 0.859x | 0.812x | -25.4% | no (2x mean<=0) |
| reaction | fade | 0 | 5d | 121 | 115 | 45.5% | -0.86% | -1.01% | 0.856 | 0.861 | 0.868x | 0.844x | 0.796x | -24.9% | no (2x mean<=0) |
| gap | fade | 0 | 5d | 121 | 115 | 44.6% | -1.04% | -1.19% | 0.920 | 0.920 | 0.849x | 0.820x | 0.768x | -28.4% | no (2x mean<=0) |
| move | fade | 0 | 5d | 121 | 115 | 48.8% | -1.16% | -1.30% | 0.935 | 0.935 | 0.827x | 0.800x | 0.750x | -30.5% | no (2x mean<=0) |

The best fixed-slot 2x row is **move continuation thr=0bps 5d**: n=121, mean net +0.70%, after-tax 1.149x vs QQQ 1.170x and BIL 1.055x, placebo p=0.095, maxDD -13.5%.

## Capital-utilization upper bound

The main portfolio gives each of the five slots 20% of equity. As a sizing sensitivity, `equal_active` gives active positions 100% of equity split equally each day, with no charge for rebalancing. This is an upper bound, not a deployable accounting result. All 36 full-deploy variants are in the registry; the top ten by 2x multiple are shown.

| signal | mode | thr bps | exit | n | mean 10bp | p 10bp | x10 fixed | x10 full | maxDD full | 2025 ret full | 2026 ret full | verdict full |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| gap | fade | 0 | close | 121 | +0.15% | 0.035 | 1.080x | 1.190x | -20.9% | +29.44% | -0.71% | no (full-deploy fewer than 2 positive years) |
| reaction | continuation | 200 | close | 55 | +0.22% | 0.189 | 1.073x | 1.144x | -12.8% | +20.39% | +1.07% | no (placebo p>=0.05) |
| gap | continuation | 200 | next_session | 77 | +0.15% | 0.413 | 1.070x | 1.103x | -23.0% | -21.09% | +50.18% | no (placebo p>=0.05) |
| reaction | continuation | 0 | next_session | 121 | -0.14% | 0.388 | 1.026x | 1.102x | -21.8% | -15.03% | +34.51% | no (2x mean<=0) |
| move | continuation | 200 | next_session | 81 | -0.03% | 0.408 | 1.047x | 1.071x | -24.6% | -20.39% | +40.30% | no (2x mean<=0) |
| reaction | continuation | 200 | 5d | 55 | +0.07% | 0.393 | 1.060x | 1.058x | -33.9% | -12.91% | +29.44% | no (placebo p>=0.05) |
| move | continuation | 0 | next_session | 121 | +0.04% | 0.214 | 1.057x | 1.047x | -26.2% | -18.68% | +31.59% | no (placebo p>=0.05) |
| reaction | continuation | 200 | next_session | 55 | +0.02% | 0.483 | 1.055x | 1.043x | -24.4% | -18.23% | +29.92% | no (placebo p>=0.05) |
| reaction | continuation | 0 | close | 121 | -0.06% | 0.179 | 1.042x | 1.016x | -15.7% | +2.18% | +1.03% | no (2x mean<=0) |
| move | fade | 200 | close | 81 | +0.13% | 0.289 | 1.069x | 1.014x | -18.5% | +18.18% | -10.30% | no (placebo p>=0.05) |

Full-deploy rows above QQQ: gap fade thr=0bps close 1.190x (2026 -0.71%).

## Per-year and per-ticker folds of the leading 2x row

Leading row: **move continuation thr=0bps 5d**. Folds use the 5 bps trade sample; the 10 bps means are 0.10% lower per trade plus the doubled short borrow.

| year | n | mean net 5bp | hit | gross mean |
|---|---|---|---|---|
| 2024 | 1 | -3.11% | 0.0% | -2.87% |
| 2025 | 60 | +0.11% | 48.3% | +0.26% |
| 2026 | 60 | +1.67% | 51.7% | +1.82% |

| ticker | n | mean net 5bp | hit |
|---|---|---|---|
| ALB | 8 | +0.72% | 37.5% |
| AMAT | 8 | +3.40% | 75.0% |
| AMD | 8 | +1.80% | 50.0% |
| APH | 8 | +2.26% | 62.5% |
| CF | 8 | -1.41% | 37.5% |
| FANG | 16 | -0.15% | 50.0% |
| FDX | 9 | -3.97% | 11.1% |
| INTC | 8 | +5.86% | 75.0% |
| LUV | 8 | +5.10% | 75.0% |
| ON | 8 | -2.81% | 25.0% |
| OXY | 16 | +0.63% | 56.2% |
| TSLA | 16 | +0.75% | 43.8% |

Concurrency: 121 candidate events, 115 traded and 6 skipped at the five-slot cap; uncapped maximum was 7 concurrent positions.

## Quarterly-only sensitivity

Drop FANG/OXY/TSLA, whose 2.02 filings are monthly rather than quarterly. This is a data-quality check on the leading row, not a new search.

| cost | n | mean net | after-tax x | placebo p |
|---|---|---|---|---|
| 5 bps | 73 | +1.15% | 1.168x | 0.104 |
| 10 bps | 73 | +0.99% | 1.149x | 0.104 |

## 5m execution-quality check, 60 days

Same grid on 5m bars. The sample is 15 events or fewer; every row is **not** a candidate regardless of its multiple.

| signal | mode | thr bps | exit | n | mean 5bp | mean 10bp | x 5bp | x 10bp | p 5bp | verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| reaction | continuation | 0 | 5d | 15 | +2.94% | +2.79% | 1.085x | 1.082x | 0.065 | execution only (n<30, 60d) |
| move | continuation | 0 | 5d | 15 | +2.20% | +2.06% | 1.070x | 1.067x | 0.194 | execution only (n<30, 60d) |
| move | continuation | 0 | next_session | 15 | +2.85% | +2.74% | 1.066x | 1.064x | 0.005 | execution only (n<30, 60d) |
| reaction | continuation | 0 | next_session | 15 | +2.32% | +2.21% | 1.054x | 1.052x | 0.010 | execution only (n<30, 60d) |
| gap | continuation | 0 | 5d | 15 | +2.76% | +2.62% | 1.036x | 1.033x | 0.080 | execution only (n<30, 60d) |
| reaction | continuation | 200 | 5d | 5 | +3.68% | +3.56% | 1.030x | 1.029x | 0.184 | execution only (n<30, 60d) |
| reaction | continuation | 200 | next_session | 5 | +2.07% | +1.96% | 1.020x | 1.019x | 0.211 | execution only (n<30, 60d) |
| gap | continuation | 200 | next_session | 6 | +1.50% | +1.39% | 1.018x | 1.017x | 0.142 | execution only (n<30, 60d) |
| move | continuation | 200 | next_session | 8 | +0.84% | +0.73% | 1.015x | 1.014x | 0.333 | execution only (n<30, 60d) |
| move | fade | 200 | close | 8 | +0.51% | +0.41% | 1.012x | 1.010x | 0.343 | execution only (n<30, 60d) |
| reaction | fade | 200 | close | 5 | +0.66% | +0.56% | 1.011x | 1.010x | 0.355 | execution only (n<30, 60d) |
| reaction | fade | 0 | close | 15 | +0.11% | +0.01% | 1.008x | 1.006x | 0.284 | execution only (n<30, 60d) |
| gap | continuation | 200 | 5d | 6 | +0.07% | -0.07% | 1.007x | 1.006x | 0.531 | execution only (n<30, 60d) |
| gap | fade | 0 | close | 15 | +0.06% | -0.04% | 1.007x | 1.005x | 0.398 | execution only (n<30, 60d) |
| move | continuation | 200 | 5d | 8 | +0.04% | -0.10% | 1.007x | 1.005x | 0.591 | execution only (n<30, 60d) |
| gap | fade | 200 | close | 6 | +0.09% | -0.02% | 1.007x | 1.006x | 0.409 | execution only (n<30, 60d) |
| gap | continuation | 0 | next_session | 15 | +0.03% | -0.09% | 1.006x | 1.004x | 0.443 | execution only (n<30, 60d) |
| move | continuation | 0 | close | 15 | +0.00% | -0.10% | 1.006x | 1.004x | 0.428 | execution only (n<30, 60d) |
| gap | continuation | 200 | close | 6 | -0.29% | -0.39% | 1.003x | 1.003x | 0.596 | execution only (n<30, 60d) |
| move | fade | 0 | close | 15 | -0.21% | -0.31% | 1.001x | 0.999x | 0.577 | execution only (n<30, 60d) |
| gap | fade | 200 | 5d | 6 | -0.37% | -0.53% | 1.001x | 1.000x | 0.474 | execution only (n<30, 60d) |
| move | fade | 200 | 5d | 8 | -0.34% | -0.50% | 1.001x | 0.998x | 0.414 | execution only (n<30, 60d) |
| gap | continuation | 0 | close | 15 | -0.26% | -0.36% | 1.000x | 0.997x | 0.607 | execution only (n<30, 60d) |
| reaction | continuation | 200 | close | 5 | -0.87% | -0.97% | 1.000x | 0.999x | 0.658 | execution only (n<30, 60d) |
| reaction | continuation | 0 | close | 15 | -0.31% | -0.42% | 0.999x | 0.996x | 0.721 | execution only (n<30, 60d) |
| gap | fade | 0 | next_session | 15 | -0.25% | -0.36% | 0.999x | 0.995x | 0.562 | execution only (n<30, 60d) |
| move | continuation | 200 | close | 8 | -0.71% | -0.81% | 0.997x | 0.995x | 0.662 | execution only (n<30, 60d) |
| move | fade | 200 | next_session | 8 | -1.07% | -1.18% | 0.991x | 0.989x | 0.672 | execution only (n<30, 60d) |
| gap | fade | 200 | next_session | 6 | -1.72% | -1.83% | 0.988x | 0.986x | 0.863 | execution only (n<30, 60d) |
| reaction | fade | 200 | next_session | 5 | -2.30% | -2.41% | 0.986x | 0.984x | 0.803 | execution only (n<30, 60d) |
| reaction | fade | 200 | 5d | 5 | -3.98% | -4.16% | 0.969x | 0.968x | 0.837 | execution only (n<30, 60d) |
| gap | fade | 0 | 5d | 15 | -3.06% | -3.22% | 0.953x | 0.950x | 0.925 | execution only (n<30, 60d) |
| reaction | fade | 0 | next_session | 15 | -2.55% | -2.66% | 0.934x | 0.930x | 0.995 | execution only (n<30, 60d) |
| move | fade | 0 | next_session | 15 | -3.08% | -3.19% | 0.919x | 0.916x | 1.000 | execution only (n<30, 60d) |
| move | fade | 0 | 5d | 15 | -2.50% | -2.66% | 0.910x | 0.906x | 0.811 | execution only (n<30, 60d) |
| reaction | fade | 0 | 5d | 15 | -3.24% | -3.39% | 0.890x | 0.887x | 0.940 | execution only (n<30, 60d) |

Best 5m row is **reaction continuation thr=0bps 5d** with n=15 and 2x after-tax 1.082x; this is execution quality only.

## Verdict

**Reject as a deployable candidate, but record one fragile upper-bound lead.** No 1h variant clears QQQ (1.170x) and BIL (1.055x) after tax at 2x costs under the pre-specified 20% slot sizing. The only full-capital row above QQQ is **gap fade thr=0bps close**: 2x after-tax 1.190x with placebo p=0.035, but its 2025 full-deploy return is +29.44% and 2026 is -0.71%, with maxDD -20.9%. It fails the year-fold rule, is one of 36 variants at p=0.035, and the full-deploy path does not charge rebalancing. Keep it only as a paper-research lead; do not merge. The best fixed-slot 2x row is 1.149x, below QQQ.

Under the pre-specified 20% risk budget the first-hour reaction does not clear QQQ. The full-capital gap-fade lead is not robust: it is one marginal p=0.035 hit among 36 variants, its 2026 return is negative, and its -20.9% drawdown buys only a two-point multiple edge; charging rebalancing would make it worse. That is not enough for a candidate. The 5m book is too small to change the verdict.

## Limits

- 1h bars are Yahoo aggregates, 2 years, 12 liquid names. They are not exchange MBO/L1 and do not model queue position or partial fills.
- The after-close reaction is entered at 10:30 ET, one completed hour after the open. A faster implementation would face the opening auction, wider spreads and the same no-same-bar rule.
- Overlapping earnings and one sector cluster reduce the effective sample. The five-slot cap binds only lightly (maximum uncapped seven).
- Placebo is a date shuffle, not a matched-volatility synthetic event; it tests whether the event date carries information beyond the ticker/time distribution.
- No deflated Sharpe/CSCV is reported. With 36 variants, the gap-fade p=0.035 is a single-test result and does not survive a family-wise correction.
- No live/real-money code, no broker adapter, no order routing.

Reproduce: `uv run python scripts/research_earn_intraday.py` (cached data only; no `--fetch`).
