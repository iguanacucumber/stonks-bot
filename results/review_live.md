# v10 live/forward paper accounting review

Date: 2026-11-26. Base: `dd2890e`. Data snapshot: 2026-11-25.
Scope: forward/live paper session resolution, gate counting, reconciliation,
idempotency and known-date drift. No real orders, no secrets, no fabricated rows.

## Verdict

**PASS with WAIT.** The gate is honest and does not count a date without a
distinct, verified, close-valid paper row. Current gate is **US 0/5, TECH 1/5,
tax 0/5**, next required **2026-11-27**. No 2026-11-26 (holiday) or 2026-11-27 row
exists. Forward evidence is not proven.

## Findings

### F1 — HIGH — fabricated/non-session audit dates could increment the gate

`scripts/forward_gate.py` counted any distinct `verified=True` audit date. It did
not require the date to be an NYSE session, on/before the completed session, or to
have a matching close-valid paper row when the paper file had no close rows.

Repro: an audit row for Sunday `2026-11-29` (or a future/non-session date) with a
paper file that had no close-valid row incremented the counter. The new regression
test `test_verified_audit_needs_real_session_and_close_valid_paper` covers both a
Sunday and an audit-only date.

Fix: filter invalid/non-session/future verified dates before counting, and always
require a matching close-valid paper date. The date remains visible as BLOCKED,
never silently dropped. Gate never increments on an audit row alone.

### F2 — HIGH — live engine used the fixed 16:30 ET target on half-days

`bot/live/engine.resolve_session` used `data.target_session` (fixed 16:30 cutoff).
At a 13:00 ET half-day, 13:30 ET still resolved to the prior session; at a
holiday it could also resolve inconsistently. The gate/daily already needed the
actual-close rule.

Fix: engine now uses `calendar.target_session` (13:30 half-day, 16:30 normal) and
keeps a weekday holiday date so `build_snapshot` fails closed rather than silently
replaying the prior session. Tests cover `2026-11-27` 13:29/13:30 and holiday
`2026-11-26` in `tests/test_live.py`.

### F3 — MEDIUM — gate equity/drawdown used a single-day mark

`results/paper_daily.csv` stores `equity = capital * (1 + day_move)`, a single-day
mark. `forward_gate.py` displayed the latest such value as wealth. With multiple
verified dates this understates compounding. `paper_report.py` already writes the
compounded `live_equity` into `paper_audit.csv`.

Fix: the gate prefers the audit's compounded `live_equity` series for equity and
drawdown, falling back to paper rows only when no audit equity exists. Regression:
`test_gate_uses_compounded_audit_equity`.

### F4 — MEDIUM — same-name reweights could skip missing-open checks

`paper_daily` treated a book as rebalanced only when the ticker set or total gross
changed. A month-end reweight among the same names with the same gross could be
marked `data_complete=True` without the required opens.

Fix: `is_rebalanced()` compares every name weight. Unit regression added to
`tests/test_core.py`.

### F5 — LOW — audit merge crashed when fills/exposure was absent

`paper_report.audit_frame` split a missing `fills` column and raised `KeyError: 1`
when the exposure merge was empty.

Fix: missing fills default to `0/0` and missing weights/returns are unverified.
Regression added to `tests/test_core.py`.

## Current gate (reproduced, not fabricated)

Command:

```bash
uv run python scripts/forward_gate.py --check \
  --now 2026-11-26T17:24:00+00:00 --bench-last 2026-11-25
```

```text
FORWARD GATE WAIT  now=2026-11-26T17:24:00+00:00  session=2026-11-25
monthly/US    WAIT 0/5  next=2026-11-27  ETA 5/5=2026-12-03
monthly/TECH  WAIT 1/5  next=2026-11-27  ETA 5/5=2026-12-02
tax/TECH      WAIT 0/5  next=2026-11-27  ETA 5/5=2026-12-03
```

Remaining sessions (no misses assumed): US `11-27, 11-30, 12-01, 12-02, 12-03`;
TECH `11-27, 11-30, 12-01, 12-02`; tax `11-27, 11-30, 12-01, 12-02, 12-03`.

`2026-11-26` is Thanksgiving and has no row. `2026-11-27` has not completed yet.
No date was backfilled.

## Reconciliation / idempotency checks

- State is atomic (`os.replace` + fsync); ledger keys `(book, market, session)` are
  upserted, and rerunning a session does not create a second row.
- `reconcile.py` rebuilds cash, positions, fees, realized PnL, EUR fields and
  corporate actions from the fill journal; missing state is reported, not faked.
- Duplicate audit dates are excluded from the count and block the book; conflicting
  rows block. Non-verified/preview rows do not count.
- Paper vs shadow drift on the known verified date 2026-11-25 is 0.58 bps, within
  the 5 bps gate; TECH is 1/5 verified. US preview is `close_valid=False`.
- All existing `tests/test_*.py` pass after the fixes. Targeted:
  `tests/test_forward_gate.py`, `tests/test_forward.py`, `tests/test_live.py`,
  `tests/test_core.py`.

## Residual risks

- `forward_daily.gate_text` remains an advisory display helper; the authoritative
  counter is `scripts/forward_gate.py` with the checks above.
- The gate is calendar-driven; any missed required session extends the remaining
  list/ETA and should be investigated, not backfilled.
- Paper ledger is pre-tax/average-cost; it is not the after-tax EUR ledger.
- No real broker/order path is exercised by this audit.
