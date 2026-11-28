# Forward paper status

Generated: 2026-11-28T06:23:14+00:00
Session: 
Mode: paper
Overall: **ATTENTION**

| book | market | live | orders | reconcile | paper dates | verified gate |
|---|---|---|---:|---|---:|---|

## State


## Errors

- paper mode requires a completed half-day: now_et=2026-11-28 01:23 session=2026-11-27 close=13:00 ET (+30m data delay); refusing a close-valid row

## Next action

- Fix the errors/blockers above; do not backfill a stale open.

No real orders were sent. The live ledger is separate from the legacy `paper_daily.csv` track; US and TECH rows are never mixed.
