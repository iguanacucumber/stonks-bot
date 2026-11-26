# Forward paper books: monthly and tax

Two forward books now log side by side. They never share rows or files.

- **monthly** — the shipped TECH book (SMH core with relative rotation, 2.5x,
  half size below the 200-day MA). This is the default, so existing commands are
  unchanged.
- **tax** — the core-only taxable shape: `core="SMH"`, `core_mode="always"`,
  `leverage=2.5`, `trend_ma=200`, `trend_scale=0.5`, `leverage_band=0.30`,
  `tax_lots="hifo"`, calendar rebalance, next-open fills. Paper rows are
  **pre-tax engine marks**: `tax_country` stays `"us"` and `tax_rate=0`, so no
  French tax is deducted from `day_move`. The after-tax layer can be applied to
  these marks separately.

## Commands

Monthly (the current weekday job; `--book monthly` is the default):

```sh
uv run python scripts/paper_daily.py --book monthly --market TECH
uv run python scripts/paper_report.py --book monthly --market TECH
uv run python scripts/paper_health.py --book monthly
```

Tax book (new weekday job, after the monthly refresh):

```sh
uv run python scripts/paper_daily.py --book tax --market TECH
uv run python scripts/paper_report.py --book tax --market TECH
uv run python scripts/paper_health.py --book tax
```

`--replay N` and `--reconcile` accept `--book` too. A tax replay writes
`results/paper_replay_tax.csv`; it never overwrites the monthly replay.

## Current gates

Run on 2026-11-26 against the existing files:

```text
GATE: WAIT 1/5 distinct dates (book monthly)
GATE: WAIT 0/5 distinct dates (book tax)
```

- The 2026-11-25 close-valid row belongs to **monthly** and stays reconciled.
- The **tax** book has no rows yet. `paper_daily_tax.csv` does not exist, so the
  report prints `0/5` instead of inventing a row. No tax row was copied or
  synthesized.
- The first scheduled `--book tax` run creates the tax files and starts its own
  five-date gate.

Health, checked with `--as-of 2026-11-25T22:00:00+00:00`:

```text
HEALTH OK  book=monthly  rows=7  close_valid=1  valid_dates=1  last_valid=2026-11-25  now_et=2026-11-25 17:00
HEALTH STALE: no paper_daily_tax.csv (book tax)
```

The tax health check is expected to be stale until the first tax row exists.
Do not wire its alert as a failure before that first run.

## Files

Changed:

- `scripts/paper_daily.py` — `--book {monthly,tax}`, per-book config and files,
  per-book `--reconcile`/`--replay`; monthly defaults and files unchanged.
- `scripts/paper_report.py` — `--book`, reads the matching daily file and shadow
  backtest, prints the gate for that book.
- `scripts/paper_health.py` — `--book`, checks the matching daily file.
- `tests/test_core.py` — no-network assertion that the monthly config is empty
  and the tax config is the core-only shape, with separate file names.
- `results/paper_forward.md` — this note.

Created on first run, not committed here:

- `results/paper_daily_tax.csv`
- `results/paper_ledger_tax.csv`
- `results/paper_replay_tax.csv` (only with `--book tax --replay N`)

Existing monthly files are untouched:

- `results/paper_daily.csv`
- `results/paper_ledger.csv`
- `results/paper_replay.csv`

## Guards

Both books use the same close-valid/data-complete guard:

- `close_valid` requires a quote at or after 16:00 New York time.
- The row is downgraded if a held name is missing a close, or if a rebalance is
  missing an open.
- `--reconcile` rewrites only the last close-valid row of the selected book from
  final bars, preserving the live prices recorded in that row.
- The report skips intraday and incomplete rows and counts distinct quote dates
  only.

## After merge

The existing monthly automation (`aut_214468bbe001VIxK8wlbulpsqf`, cron
`15 21 * * 1-5`) only logs the monthly book. Add a second weekday automation for
the tax book, after the monthly one has refreshed the cache:

- cron `20 21 * * 1-5`
- `uv run python scripts/paper_daily.py --book tax --market TECH`
- then `paper_report.py --book tax --market TECH` and
  `paper_health.py --book tax`

Do not add `--refresh` to the tax job unless it runs independently; the monthly
job already force-fetches the shared cache, and the cache is read-only for this
branch. Also update the monthly job's report/health commands to pass
`--book monthly` explicitly, or keep relying on the default.
