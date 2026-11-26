# Forward paper loop (US + TECH)

`scripts/forward_daily.py` is the weekday operational command for the forward track.
It wraps the merged live autopilot and the legacy paper logger; it does not introduce
a new strategy or a second accounting engine. It is paper-only: no command here
sends a real order.

## What one run does

1. Resolve the last completed US session (`data.target_session`, 16:30 ET data
   delay). Weekends and NYSE holidays fail closed before any state is touched.
2. In `--paper` mode, require a post-close run on the session date. A run at 10:00 ET
   or on a holiday cannot write a close-valid row.
3. Refresh only stale bars through the existing incremental helpers, with the
   existing missing-symbol backoff. The benchmark gate is checked first; if SPY has
   no bar for the session, the command stops instead of probing the universe. It
   never runs a full cache rewrite or `fetch_all(force=True)`.
4. Run the live planner/paper broker separately for `monthly/US` and
   `monthly/TECH`; `--tax` also runs `tax/TECH`. State and ledgers are per
   `(book, market)`:
   - `state/live/monthly_us.json` + `.csv`
   - `state/live/monthly_tech.json` + `.csv`
   - `state/live/tax_tech.json` + `.csv` (when enabled)
5. In `--paper` mode, if the session is not already present, run the existing
   `scripts/paper_daily.py` for that book/market. It must produce a close-valid row
   for the session; otherwise the forward run is marked unhealthy and the row is not
   counted. The existing `scripts/paper_report.py` then refreshes
   `results/paper_audit.csv`.
6. Reconcile each live book (fills, orders, positions, cash, EUR fields, ledger).
7. Write `results/forward_status.md` with books, session, orders, reconcile state,
   paper dates and verified-gate status. Exit `0` only when every book is healthy.

Dry-run is the default. It plans and prints; it writes no state, ledger or status
file. `--paper` is the only writer.

## Commands

```bash
# Dry-run the latest completed session (no network, no writes)
uv run python scripts/forward_daily.py --no-refresh

# Daily paper run: refresh stale bars, run both books, reconcile, write status
uv run python scripts/forward_daily.py --paper

# Include the core-only tax book
uv run python scripts/forward_daily.py --paper --tax

# Replay a completed session through the live engine only.
# Legacy paper_daily rows are intentionally not written for a historical replay.
uv run python scripts/forward_daily.py --paper --as-of 2026-11-25 --no-refresh

# Machine-readable
uv run python scripts/forward_daily.py --paper --json
```

Exit codes: `0` healthy, `2` blocked/stale/holiday/health failure, `1` unexpected
error. The command is idempotent: rerunning a completed session produces no second
live ledger row and no second legacy paper row.

## Verified dates and the gate

A date counts toward the forward gate only when **all** of these are true:

- the quote was taken at/after 16:00 New York on a completed session;
- the session exists in the panel and the close is final;
- holdings have closes, and a rebalance has opens (the legacy
  `close_valid`/`data_complete` flags);
- `paper_report.py` finds the live/shadow return difference within 5 bps, the max
  weight difference within 1 bp, and full fills (`fills` count matches).

`results/paper_audit.csv` is the machine-readable record. The gate is **5 distinct
verified dates per book/market**; intraday snapshots, holiday rows, incomplete bars
and failed reconciliations never count. `results/forward_status.md` is the current
status, not a substitute for the audit.

US and TECH are separate rows and separate gates. A TECH row cannot satisfy a US
gate. The tax book is a third, separate gate.

## Holidays, missed runs, stale data

- **Holiday/weekend:** the NYSE calendar rejects the session. No state, no paper row,
  no alert escalation. Run again on the next trading day.
- **Stale benchmark:** the command exits `2` before running a book. Do not pass an
  old `--as-of` to "catch up" and do not fabricate a bar.
- **Missed one or more sessions:** run the command after the cache catches up. The
  live engine refuses an overdue pending fill (it cannot use a stale historical
  open); reconcile and replan at the next session. Existing `scripts/ops.py status`
  reports the missed-session alert.
- **Unpublished close:** if the daily bar is not available, the run is stale and
  retried after publication. A paper row is written only when the session is real
  and close-valid.
- **Partial run:** each book is independent. A US failure does not silently mark
  TECH as failed or vice versa; the status table shows which book needs attention.

## Scheduler examples

Do not enable automatically. Run after the US close and after the data updater.
`--paper` triggers the incremental refresh; keep one scheduler only.

Cron (server timezone UTC; 21:45 UTC is 16:45 ET in EST, 17:45 ET in EDT):

```cron
45 21 * * 1-5 stonks cd /opt/stonks-bot && \
  mkdir -p state/live && \
  flock -n state/live/forward.lock \
  uv run python scripts/forward_daily.py --paper >> state/live/forward.log 2>&1
```

Because cron uses a fixed UTC hour, use a systemd timer with a timezone if the
one-hour DST shift matters. Example (adjust paths/user):

`/etc/systemd/system/stonks-forward.service`

```ini
[Unit]
Description=stonks-bot forward paper loop (US + TECH)
After=network-online.target

[Service]
Type=oneshot
WorkingDirectory=/opt/stonks-bot
ExecStart=/usr/bin/env uv run python scripts/forward_daily.py --paper
User=stonks
Group=stonks
```

`/etc/systemd/system/stonks-forward.timer`

```ini
[Unit]
Description=Run the forward paper loop after the US close

[Timer]
OnCalendar=Mon..Fri 16:45 America/New_York
Persistent=true
RandomizedDelaySec=120

[Install]
WantedBy=timers.target
```

Enable with `systemctl enable --now stonks-forward.timer`. A missed timer catches up
once on the next boot; the command is idempotent. Keep the existing
`stonks-live.service`/timer separate only if you deliberately want two chains; the
forward command already runs the live books for both markets.

## Reading the output

```text
FORWARD DAILY PAPER OK  session=2026-11-25 ...
  monthly/US: OK ... live=ok orders=6 reconcile=OK paper_dates=0 gate=WAIT 0/5
  monthly/TECH: OK ... live=ok orders=6 reconcile=OK paper_dates=1 gate=WAIT 1/5
```

`results/forward_status.md` is rewritten atomically. A gate of `WAIT 1/5` means one
verified date, not a track record; do not quote a forward edge until 5/5. If the
legacy logger cannot produce a close-valid row, the book is marked unhealthy even
though the live state may be valid; the two tracks are deliberately kept separate.

## Gate report (read-only)

`scripts/forward_gate.py` is the audit gate. It reads `results/paper_audit.csv`
and the per-book `paper_daily*.csv`; it never writes an audit or paper row.
`--write-status` updates only `results/forward_gate.md` (equity, drawdown, data
freshness, reconcile, next required session, per-book checks). `--check` is the
automation-safe exit-code wrapper.

```bash
uv run python scripts/forward_gate.py                  # table
uv run python scripts/forward_gate.py --json           # machine-readable
uv run python scripts/forward_gate.py --check --write-status
uv run python scripts/forward_gate.py --check --since 2026-11-25
```

Exit codes: `0` PASS (all required books >=5 verified), `2` WAIT (short but
healthy), `3` BLOCKED (missing/duplicate/conflicting rows, stale benchmark,
missed required session, or reconcile failure), `1` unexpected error. `--since`
prints NEW VERIFIED dates; it does not change the gate count.

Run it immediately after the daily job so a MISSED/BLOCKED condition is visible in
the same cron/service log:

```bash
uv run python scripts/forward_daily.py --paper --tax
uv run python scripts/forward_gate.py --check --write-status
```

Only distinct verified dates count. Duplicate dates, non-verified rows, preview
rows with `close_valid=False`, holidays and missing files are reported explicitly.
The current gate on 2026-11-26 (holiday) is US 0/5, TECH 1/5, tax 0/5; the next
required session is 2026-11-27. No 2026-11-26 row exists and none is fabricated.

## Tests

```bash
uv run python tests/test_forward.py
uv run python tests/test_live.py
uv run python tests/test_ops.py
uv run python tests/test_core.py
uv run python tests/test_data.py
```

`test_forward.py` uses a synthetic panel and temp state/results; it does not invoke
subprocesses or the network. It covers dry-run no-write, idempotent paper rerun,
US/TECH separation, stale/holiday fail-closed, injected refresh, gate counting and
health failure.

## Limitations

- The legacy paper logger and live ledger are two views: the live ledger records
  planned orders/fills, the legacy CSV records the shadow-book daily move. They can
  diverge by execution timing; the gate uses the legacy reconciliation.
- The legacy logger needs a real after-close run to write a row. Historical
  `--as-of` replay updates only the live state.
- Paper uses adjusted cache prices, not raw broker fills; no real broker, no secrets.
- `paper_daily.py`/`paper_report.py` remain the source of the verified gate; changing
  their assumptions requires re-baselining.
