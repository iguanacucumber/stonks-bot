# Forward paper track

The shipped TECH book (SMH core, 2.5x, half size below the 200-day MA) now runs
unattended: a weekday job at **21:15 UTC** (after the US close) appends a row to
`results/paper_ledger.csv` and `results/paper_daily.csv`, with a log in
`results/paper_daily.log`.

- Automation: `aut_21435414b001hyZ0E5xugHgtvy`, cron `15 21 * * 1-5`.
- Command: `uv run python scripts/paper_daily.py --market TECH --refresh`, then
  `uv run python scripts/paper_report.py --market TECH` into `results/paper_daily.log`.
- `--refresh` force-fetches the 54 TECH/ETF symbols. Without it the panel never advances:
  `fetch_one` serves the cached CSV forever.
- It records **actual holdings** from the backtest (post-trade weights), not a fresh
  hypothetical target: the book only changes at rebalances and risk exits.

## Replay validation (last 5 sessions)

The logger's daily return is recomputed from held weights and panel closes, then
compared with the backtest's equity return. The gap is financing on negative cash,
not a fill mismatch.

| signal | fill | mark | gross | risk scale | paper | backtest | diff |
|---|---|---|---|---:|---:|---:|---:|
| 2026-11-16 | 2026-11-17 | 2026-11-18 | 1.27 | 50% | +2.75% | +2.74% | +0.53 bp |
| 2026-11-17 | 2026-11-18 | 2026-11-19 | 1.26 | 50% | -0.01% | -0.02% | +0.52 bp |
| 2026-11-18 | 2026-11-19 | 2026-11-20 | 1.26 | 50% | -6.98% | -6.98% | +0.52 bp |
| 2026-11-19 | 2026-11-20 | 2026-11-23 | 1.28 | 50% | +4.90% | +4.90% | +0.56 bp |
| 2026-11-20 | 2026-11-23 | 2026-11-24 | 1.27 | 50% | -0.88% | -0.89% | +0.53 bp |

Mean |difference|: **0.5 bps/day**. The forward accounting matches the backtest.

## Live vs shadow

`uv run python scripts/paper_report.py --market TECH` reads the live rows, compounds the
recorded day moves, and compares each day with the backtest's close-to-close return for the
same date. It skips rows whose date has no completed daily bar yet. Current state:

First comparable row (2026-11-25, taken intraday at ~14:40 ET):

| date | live | shadow | diff | gross | risk | live equity | shadow equity |
|---|---|---:|---:|---:|---:|---:|---:|
| 2026-11-25 | -0.13% | -1.02% | +88.8 bp | 1.27 | 50% | $9,987 | $9,898 |

The 88.8 bp gap is expected intraday noise: the live leg uses the latest 1-minute price,
the shadow uses the daily bar's close field, and the market was still open. The 21:15 UTC
run is the first close-valid row.

**Bug found by the report:** the logger used *today's* post-close weights for today's move,
but today's trades fill at today's close, so the day's P&L belongs to **yesterday's**
holdings. It now records `held_from` and uses the prior book. Without this, the first live
row was +0.57% versus the shadow's -1.02% — a fake +159 bp.

The report now has a hard gate: `GATE: WAIT 1/5 distinct dates` until five rows exist.
It prints the table for inspection but refuses to call it a track record.

## 21-session reconciliation

`uv run python scripts/paper_daily.py --replay 21` over 2026-10-14 → 2026-11-24:

- |tracking difference| median **0.55 bps/day**, p95 **15.4 bps**, max **25.3 bps**.
- Gross exposure ranged **1.25x → 2.50x**; the risk scale was 50% on part of the window
  (SMH below its 200-day MA) and 100% on the rest.
- The median is financing on negative cash. The largest days are rebalance/leverage-change
  days, where the backtest also pays 10 bps and books the trade at the close.

## First live entry (2026-11-25)

| holding | weight | quote | vs prior close |
|---|---:|---:|---:|
| PANW | 24.18% | 354.02 | +1.31% |
| AMGN | 22.33% | 472.69 | +1.77% |
| ISRG | 22.00% | 428.11 | -1.24% |
| CRWD | 20.47% | 206.62 | -1.93% |
| ASML | 19.93% | 1302.05 | -2.55% |
| ARM | 18.06% | 162.01 | +0.28% |

Gross 127%, risk scale 50% (SMH below its 200-day MA), **day move -0.41%**,
$10,000 → $9,958.60 at the time of the snapshot. The after-close run replaces this
intraday mark with the official close.

## Slippage check against the backtest

- Backtest fill: next close, 10 bps per side, no gap cost.
- Paper row: latest quote, then the official close after 21:15 UTC.
- The replay above uses the same closes as the backtest and matches to 0.5 bps, so any
  forward drift is execution, not accounting.
- Measured separately: filling at the next **open** costs ~$400k of terminal wealth
  over the window (US $1.40M → $1.00M). A live fill between the signal close and the
  next open would sit between those numbers; the job logs the close, so the paper
  track keeps the backtest's assumption.

## Reading the files

- `paper_ledger.csv`: time, signal date, target JSON, price JSON, stop flags.
- `paper_daily.csv`: time, market, signal date, quote date, gross, risk scale, day move,
  equity, holdings JSON, prices JSON.
- `paper_replay.csv`: the five-session reconciliation above.

Forward status: **one distinct live date so far** (2026-11-25; duplicate intraday runs are
deduped by quote date in the report).
One day is not a track record. The job adds one row per weekday, so five weekday rows
appear after five sessions; re-run the replay then to compare the forward week with the
backtest. Nothing here is fabricated: the 21-session table above is a replay on panel
closes, not live data.
