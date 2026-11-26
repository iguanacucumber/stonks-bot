# Operations runbook (paper live loop)

This page is the human procedure for the merged live autopilot. The bot still has
**no real broker**: `--paper` writes state/ledger only, and no command here sends an
order. The operator surface is `scripts/ops.py`; it reuses `bot.live` state, engine,
calendar, reconciliation and health rather than forking the accounting engine.

## Quick commands

```bash
uv run python scripts/ops.py status                 # last run, next session, gate, orders
uv run python scripts/ops.py status --json
uv run python scripts/ops.py doctor                 # cache/state/permissions/FX/disk
uv run python scripts/ops.py doctor --json
uv run python scripts/ops.py backup                 # state + paper CSVs + live.toml
uv run python scripts/ops.py restore ARCHIVE --force
uv run python scripts/ops.py alert-test --kind missed_session
uv run python scripts/ops.py drill
uv run python scripts/ops_drill.py --json
```

Use `--book monthly|tax`, `--market US|TECH|...`, `--state-dir PATH`,
`--config live.toml` and `--now ISO` (the last is for deterministic tests/replays).
The default live book is US monthly, matching `bot/live/config.py`; TECH monthly
remains the separate forward-validation track.

## Status

`status` is read-only and prints:

- the resolved completed session and the next NYSE session;
- the latest recorded run (`state.runs`), state file and update time;
- benchmark/panel freshness, FX rate/date and whether FX is fresh;
- drawdown/breaker state and gross target;
- state/ledger reconciliation issues and warnings;
- pending plan, open order intents and `pending_cut`;
- the forward gate from `results/paper_audit.csv`;
- a missed-session/overdue-plan alert payload when applicable.

Exit code: `0` healthy, `2` attention (blocked, reconcile failure, missed session or
health failure). It never writes state.

## Doctor

`doctor` checks the files the daily loop needs, not the strategy:

| check | blocking? |
|---|---|
| cache root, bars dir, required benchmark/core/FX bars | yes |
| US `panel.parquet` and `membership.parquet` readable and non-empty | yes |
| benchmark close fresh within `max_stale_days` | yes |
| state dir writable, state JSON/version/config signature valid | yes |
| state/ledger reconcile (orders, fills, cash, positions, EUR fields) | yes |
| free disk space above the threshold (default 200 MB) | yes |
| core/auxiliary bar freshness, FX age, missing-symbol/backoff files | no (warning) |

Exit code is non-zero (`2`) on any blocking check, so a cron wrapper can alert on
`ops.py doctor`. It does not fetch data, repair the ledger or create a state file.

## Backup and restore

`backup` creates one archive plus a sidecar checksum:

```
state/backups/stonks-backup-20261126T190000Z.tar.gz
state/backups/stonks-backup-20261126T190000Z.tar.gz.sha256
```

The archive contains `manifest.json` first, then every file under `state/`,
`results/paper_*.csv` and the selected live TOML config (default `live.toml` or
`config/*.toml`). The manifest stores SHA-256 and size for every member; the sidecar
stores the archive SHA-256. The archive is written to a temporary file, fsynced and
renamed into place.

`restore` verifies both layers before writing anything. It refuses to overwrite
existing files without `--force`, rejects members outside the requested roots and
stages each file before an atomic replace. A restore of the same archive is
byte-for-byte reproducible.

Recommended schedule: daily after the live run, plus before a config change or a
manual intervention. Keep at least 14 daily archives outside the repository
(`/var/backups/stonks-bot` or object storage with lifecycle rules). Test restore
monthly in a scratch directory; `ops.py drill` covers the code path, not your disk.

## Alerting

Alerting is **off by default**. Configure only the channels you want:

| channel | required environment |
|---|---|
| webhook | `STONKS_ALERT_WEBHOOK_URL` |
| Telegram | `STONKS_ALERT_TELEGRAM_BOT_TOKEN`, `STONKS_ALERT_TELEGRAM_CHAT_ID` |
| email | `STONKS_ALERT_EMAIL_TO`, `STONKS_ALERT_EMAIL_FROM`, `STONKS_SMTP_HOST` |

Optional: `STONKS_SMTP_PORT` (default 587), `STONKS_SMTP_USER`,
`STONKS_SMTP_PASSWORD`, `STONKS_SMTP_TLS` (default on). Put these in the service
environment or a root-owned env file, never in git, a report or a shell history.
`build_manager()` creates no channel when its variables are absent.

`alert-test` is a dry run by default: it prints the payload and says
`dry-run: payload validated, no network call`. Add `--send` only after checking the
recipient and secrets. The missed-session payload includes book, market, last and
current sessions, missed-session count and any pending fill session.

## Runbook

### Data outage / stale benchmark

1. `ops.py status` shows `data_fresh=false`, the last benchmark date and a stale
   alert; the live engine also returns `status=BLOCKED` with zero orders.
2. Do **not** fabricate a bar or run with an old `--as-of`. Check
   `cache/bars/SPY.csv`, `cache/bars/QQQ.csv` or the selected benchmark and the
   updater/cron log.
3. Restore network/updater, refresh only stale symbols with the documented update
   command, then rerun the idempotent live command. The same session recomputes the
   same deterministic plan.
4. If the outage spans sessions, `status` raises a missed-session alert. Run the
   missing completed session only if its data is genuinely present; otherwise
   reconcile and accept the gap rather than backdating a fill.

### Broker reject / broker down

There is no live adapter in this wave. A future adapter must fail closed. For the
paper book, a broker exception is an incident: no retry loop, no duplicate order.
Confirm `ops.py status` shows no accepted fills, inspect the ledger/state with
`ops.py status`, then rerun only after the adapter is healthy. A rejected order is
not a reason to hand-edit the ledger; reconcile first.

### Duplicate order

1. `ops.py status`/`reconcile` reports duplicate order ids or a duplicate fill.
2. Stop the timer/cron. Do not rerun the same plan while the duplicate is present.
3. Restore the last verified backup if the state is inconsistent, or use
   `scripts/live.py reconcile --repair-ledger` when the state is correct and only
   the ledger view is missing. A duplicate in state itself requires manual review;
   never delete a fill to make the report green.
4. Record what happened and rerun the idempotent session; verify one row per
   `(book, market, session)`.

### Drawdown breach

The breaker is a **non-validated safety overlay** (default -35% -> 0.5x, recovery
-10%). `status` shows `drawdown.dd`, `risk_off`, `risk_reason`, `scale` and any
`pending_cut`. A blocked cut is retried, not forgotten. Do not disable the breaker
to hide a drawdown; if you disable it for research parity, document the change and
re-run the evidence. Escalate to the owner and keep the paper ledger untouched.

### State corruption

1. `ops.py doctor` fails `state_file`/`ledger_reconcile`; the live engine refuses to
   start a new book silently.
2. Stop the scheduler. Preserve the corrupt file for forensics.
3. `ops.py restore LAST_VERIFIED_ARCHIVE --force`; then `ops.py doctor` and
   `scripts/live.py reconcile`. Compare the ledger row count and plan id.
4. If no verified backup exists, use a fresh `--state-dir` only after deciding that
   the old history is not recoverable. Never hand-write a state that claims fills
   that were not recorded.

### Missed cron / systemd timer

`status` emits `missed_session` with the last/current sessions and expected action.
Check `systemctl status stonks-live.timer` or the cron log, then run the daily
command manually for the latest completed session. The loop is idempotent: a retry
of a completed session is a no-op. If a pending plan is overdue, do not backfill the
old open; reconcile and replan at the next session.

### Holiday / market closed

The NYSE calendar rejects weekends and full-day holidays before touching state.
`status`/`doctor` may still show the last session. No action is needed; the next
session is computed with `bot.live.calendar`. Do not run `--as-of` on a holiday to
“catch up”.

### Rollback

1. Stop the timer/cron (`systemctl stop stonks-live.timer`, disable only if needed).
2. Create a current backup even if the state is suspect:
   `ops.py backup --out /var/backups/stonks-bot/pre-rollback`.
3. Restore the last verified archive and run `ops.py doctor` + `reconcile`.
4. Roll the code back with `git checkout <known-good-commit>` (never force-push
   master), reinstall the same venv, and run `tests/test_live.py`, `tests/test_ops.py`
   and `ops.py drill`.
5. Keep the failed state/ledger and logs; publish the incident summary before
   re-enabling the timer.

## Scheduler examples

Do **not** enable these automatically. Choose one scheduler; examples live under
`deploy/`:

- `deploy/systemd/stonks-live.service` + `.timer` (recommended; timezone-aware,
  `Persistent=true` catches a missed timer);
- `deploy/systemd/stonks-backup.service` + `.timer`;
- `deploy/cron/stonks-live.cron` (simple cron with `flock`).

The daily command is:

```bash
uv run python scripts/live.py run --book monthly --market US --paper
```

Run after the US close and after the daily bar is published. The loop is
idempotent, so a retry is safe. Keep logs in `state/live/cron.log` (cron) or the
journal (systemd), rotate them, and alert on a non-zero exit or a missed run.

## Failure drill and tests

`scripts/ops_drill.py` runs five deterministic scenarios in a temporary directory:
stale data (exit 2), duplicate order (exit 3), broker down (exit 2), corrupt state
(exit 2) and missed session (exit 2). Each returns the alert payload an operator
would receive. `scripts/ops.py drill` is the same command with the ops CLI.

```bash
uv run python tests/test_ops.py
uv run python tests/test_live.py
uv run python scripts/ops.py drill
```

The ops tests use temporary fixtures; they do not read the cache, write the real
state or use the network. `alert-test` is dry-run unless `--send` is explicit.
