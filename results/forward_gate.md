# Forward gate status

Generated: 2026-11-26T17:24:00+00:00
Session: 2026-11-25
Overall: **WAIT**

| book | market | status | verified | next required | ETA 5/5 (no misses) | equity | drawdown | data | reconcile |
|---|---|---|---:|---|---:|---:|---:|---|---|
| monthly | US | WAIT | 0/5 | 2026-11-27 | 2026-12-03 | 10,000.00 | n/a | fresh | no state |
| monthly | TECH | WAIT | 1/5 | 2026-11-27 | 2026-12-02 | 10,009.46 | +0.0% | fresh | no state |
| tax | TECH | WAIT | 0/5 | 2026-11-27 | 2026-12-03 | n/a | n/a | fresh | no state |

## Required sessions

| book | market | next | remaining | ETA 5/5 (no misses) |
|---|---|---|---|---|
| monthly | US | 2026-11-27 | 2026-11-27, 2026-11-30, 2026-12-01, 2026-12-02, 2026-12-03 | 2026-12-03 |
| monthly | TECH | 2026-11-27 | 2026-11-27, 2026-11-30, 2026-12-01, 2026-12-02 | 2026-12-02 |
| tax | TECH | 2026-11-27 | 2026-11-27, 2026-11-30, 2026-12-01, 2026-12-02, 2026-12-03 | 2026-12-03 |

## Checks

### monthly/US
- verified: 0/5; close-valid paper dates: 0; rejected audit rows: 0; last verified: none; last paper: 2026-11-26
- next required: 2026-11-27; ETA 5/5 (no misses): 2026-12-03; remaining: 2026-11-27, 2026-11-30, 2026-12-01, 2026-12-02, 2026-12-03; data last: 2026-11-25; equity: 10,000.00 (preview (not close-valid)); drawdown: n/a
### monthly/TECH
- verified: 1/5; close-valid paper dates: 1; rejected audit rows: 0; last verified: 2026-11-25; last paper: 2026-11-25
- next required: 2026-11-27; ETA 5/5 (no misses): 2026-12-02; remaining: 2026-11-27, 2026-11-30, 2026-12-01, 2026-12-02; data last: 2026-11-25; equity: 10,009.46 (close-valid); drawdown: +0.0%
### tax/TECH
- verified: 0/5; close-valid paper dates: 0; rejected audit rows: 0; last verified: none; last paper: none
- next required: 2026-11-27; ETA 5/5 (no misses): 2026-12-03; remaining: 2026-11-27, 2026-11-30, 2026-12-01, 2026-12-02, 2026-12-03; data last: 2026-11-25; equity: n/a (none); drawdown: n/a
- WARN: tax book not started
- WARN: optional book has no verified dates yet

Only distinct verified dates in `results/paper_audit.csv` count. US, TECH and tax gates are separate. This file is a report, not a fabricated audit row.
