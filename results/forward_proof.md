# Forward proof lockbox — status 2026-11-26

The gate is **WAIT**. This is the real count, not a projection:

| book | market | verified | next required | ETA 5/5 (no misses) |
|---|---|---:|---|---|
| monthly | US | 0/5 | 2026-11-27 | 2026-12-03 |
| monthly | TECH | 1/5 | 2026-11-27 | 2026-12-02 |
| tax | TECH | 0/5 | 2026-11-27 | 2026-12-03 |

2026-11-26 is Thanksgiving (NYSE closed). No 2026-11-26 audit/paper row exists
and none was fabricated. The next real session is 2026-11-27, a **half-day**
with a 13:00 ET close; the post-close data gate is 13:30 ET.

## What changed

- `bot/live/proof.py`: append-only JSONL lockbox with SHA-256 hashes, a hash
  chain, duplicate/date/close-time validation and an independent
  audit-plus-close-valid-paper check.
- `scripts/forward_gate.py --lock`: appends newly verified dates only after a
  clean gate (`WAIT` or `PASS`); a `BLOCKED` gate cannot lock.
- `scripts/forward_gate.py`: now exposes `verified_dates`/`close_valid_dates`
  and a `--state-dir` test injection point.
- `scripts/forward_watchdog.py`: non-zero on a BLOCKED gate, a missed expected
  session, an unlocked verified date, a broken lockbox chain, a missing/
  `ATTENTION` daily status or a daily run behind the completed session.
- Gate bug fixed: an unverified `close_valid=False` preview row no longer
  advances `next_required` and hides a missed session. Example: an 11-27 preview
  row with only 11-25 verified now leaves `next_required=2026-11-27` and blocks
  as a missed session after the 11-27 close, instead of expecting 11-30.
- `release_check.py` adds a `forward_proof` check: every verified audit date
  must have a valid lockbox record, and the chain must verify.

## Lockbox records

`results/forward_proof.jsonl` has exactly one record at this commit:

| date | book | market | gate at date | observed | state hash | record hash (prefix) |
|---|---|---|---:|---:|---|---|
| 2026-11-25 | monthly | TECH | 1/5 WAIT | 1/5 | absent (`present=false`) | `3e1603a6b383c205` |

Evidence hashes recorded at lock time:

- `results/paper_audit.csv`:
  `44d6187827f956eed2643064b11f3b84bbeec86e1937698fb44ebf391fd84024`
- `results/paper_daily.csv`:
  `ac17d2498cf1d2279faf2f82f79cd8010334c2f1441a2751d7e2a4511e64ae55`
- `state/live/monthly_tech.json`: absent on this clean worktree, recorded as
  `present=false, sha256=null`. The production daily path uses
  `--require-state`, which refuses a new record until that file exists.

The chain starts at the all-zero genesis hash. Any edit, reorder, duplicate or
partial append is detected by `proof.load_records`; the watchdog reports it as
`lockbox invalid`. The lockbox is committed so deleting it on a later run
leaves a visible mismatch: the watchdog fails when a verified audit date has no
record.

## Half-day proof (fake clock, no real 11-27 row)

`tests/test_forward_proof.py` proves the boundary with an explicit fake clock
and synthetic temp files, never the real `results/`:

| now (UTC) | now (ET) | completed session | lock 11-27? |
|---|---|---|---:|
| 2026-11-27T18:29:00Z | 13:29 | 2026-11-25 | rejected: still future |
| 2026-11-27T18:30:00Z | 13:30 | 2026-11-27 | accepted, half-day complete |

The 13:29 case is rejected twice: the gate treats 11-27 as a future audit date
and blocks, and the lockbox independently refuses a synthetic record whose
`recorded_at` is before the 11-27 close. The 13:30 case writes a temp record
with `verification.session=2026-11-27`. No real 2026-11-27 row was written.

Also tested: holiday 11-26, duplicate audit dates, future date, audit-only row,
duplicate lock append, tampered record hash, truncated final line, duplicate
lockbox key, unverified-preview skip regression, and watchdog exits on
missed/blocked/daily-ATTENTION/unlocked/tampered states.

## Current watchdog output (abridged)

```text
FORWARD WATCHDOG WAIT  session=2026-11-25
  gate: WAIT
  monthly/US: WAIT 0/5 next=2026-11-27 eta_5of5=2026-12-03
  monthly/TECH: WAIT 1/5 next=2026-11-27 eta_5of5=2026-12-02
  tax/TECH: WAIT 0/5 next=2026-11-27 eta_5of5=2026-12-03
  next: 2026-11-27 half-day; close 13:00 ET, data gate after 13:30 ET
  eta: last book 5/5 on 2026-12-03 if no session is missed
  lockbox: 1 record(s), last=2026-11-25, chain=OK
  daily: OK session=2026-11-25
  WARN: live state hash absent for: 2026-11-25
```

Exit code is `0` for a healthy WAIT. Any missed session or blocked daily run
returns `2`; the 21:45 UTC job uses the watchdog exit code as the alert.

## Exact remaining dates

From 2026-11-25, the calendar's required sessions are:

- **US 0/5** — 11-27, 11-30, 12-01, 12-02, 12-03 → ETA **2026-12-03**.
- **TECH 1/5** — 11-27, 11-30, 12-01, 12-02 → ETA **2026-12-02**.
- **Tax 0/5** — 11-27, 11-30, 12-01, 12-02, 12-03 → ETA **2026-12-03**.

11-30 is a normal Monday session; it is not skipped. If any session is missed,
the required list and ETA extend and the watchdog returns `2`.

## Wall-clock blocker

The 2026-11-27 half-day has not happened at this commit (2026-11-26). The
earliest honest 11-27 proof is after 13:30 ET on 2026-11-27, when the session's
paper row can be close-valid. The earliest all-books 5/5 is 2026-12-02 (TECH)
or **2026-12-03** (US/tax), and only if no session is missed. This cannot be
accelerated by a code change, a replay or a fabricated row.

## Release check

`scripts/release_check.py` was re-run on this branch after rebasing onto
`origin/master`: **PASS**, 11 PASS / 1 WAIT / 0 FAIL, 38/38 test scripts. The
new `forward_proof` check reports:

```text
forward_proof PASS  1 record(s), last=2026-11-25, chain=OK, locked=monthly/TECH=1
forward_gate  WAIT  monthly/TECH=WAIT 1/5, monthly/US=WAIT 0/5,
                    tax/TECH=WAIT 0/5; next=2026-11-27
```

The release check still refuses a BLOCKED gate and now also refuses a verified
audit date without a valid lockbox record. WAIT remains the honest outcome.

## Reproduce

```bash
# Current gate and lockbox status
uv run python scripts/forward_gate.py --check --write-status
uv run python scripts/forward_watchdog.py

# After a real close, append the new verified date(s)
uv run python scripts/forward_gate.py --check --write-status --lock --require-state

# Proof tests
uv run python tests/test_forward_proof.py
uv run python tests/test_forward_gate.py
```

## 2026-11-27 missed session (recorded 2026-11-28)

The weekday automation caught up at 06:23 UTC on 2026-11-28 instead of running
before the 2026-11-27 13:00 ET half-day close + 30-minute data gate.
`forward_daily.py` correctly refused to write a close-valid row after that
window and wrote `results/forward_status.md` with `ATTENTION`. No 2026-11-27
audit or paper row exists and none was fabricated. The cache was advanced to
11-27 on 11-28, so only the honest *missed session* blocker remains for
monthly/TECH; US and tax still expect 11-30. The gate stays `BLOCKED` until
the operator decides whether to void the partial TECH track and restart a
fresh 5-session window, or keep the historical break on record.
