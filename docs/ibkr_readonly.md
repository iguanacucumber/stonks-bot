# IBKR read-only reconciliation

`--broker ibkr-paper --read-only` opens the paper account only to inspect and
reconcile. It can read account summary, positions, cash, open orders and
executions. It cannot submit, cancel or replace an order; those methods raise
`IBKRReadOnlyError` before the transport is called. There is no real-money mode:
the account id must start with `DU`, and a `U...` id is rejected at config time.

## Permissions and setup

1. Use a **dedicated DU paper account**. Do not point the gateway at a live
   account.
2. The Client Portal Gateway must be running and logged in; read-only uses the
   same local session, no credentials in this repo.
3. Export:
   ```bash
   export IBKR_PAPER_ACCOUNT=DU1234567
   export IBKR_PAPER_READ_ONLY=true        # optional but recommended
   ```
   `--read-only` also forces the flag for one command.
4. Read-only endpoints used:
   - `GET /portfolio/{account}/summary`
   - `GET /portfolio/{account}/positions`
   - `GET /portfolio/{account}/ledger`
   - `GET /iserver/account/orders`
   - `GET /iserver/account/trades` (executions)
   - auth/account-list checks on startup

   There is no `POST`, `PUT` or `DELETE` in these paths. The test fake asserts
   zero write calls.

## Commands

```bash
# Read-only account view. This is the only status mode that contacts IBKR.
uv run python scripts/live.py status --book monthly --broker ibkr-paper --read-only

# Read-only strict reconciliation. It reads the broker, writes nothing.
uv run python scripts/live.py reconcile --book monthly --broker ibkr-paper --read-only

# Machine-readable
uv run python scripts/live.py status --book monthly --broker ibkr-paper --read-only --json
uv run python scripts/live.py reconcile --book monthly --broker ibkr-paper --read-only --json
```

`plan` and `run` without `--paper` never contact the broker, even with
`--broker ibkr-paper`; they use the deterministic preview. `run --paper` with
`--read-only` is refused (`exit 2`) and writes nothing. `reconcile --read-only
--repair-ledger` is also refused.

## What strict reconciliation checks

`reconcile --read-only` does the local fill/order/ledger audit first, then the
broker comparison:

- **Positions:** every non-zero broker position must exist in bot state with the
  same quantity (1e-4 shares); every bot position must exist at the broker.
  Extra and missing positions are separate failure lines with the human diff.
- **Cash:** broker cash must match bot cash within max(1 currency unit, 1 ppm).
- **Open orders:** every broker open order must match a bot order still
  `PENDING`/`PARTIAL` by ticker, side and remaining quantity. An extra broker
  order is an incident.
- **Executions:** every broker execution must match a bot fill by ticker, side,
  quantity and session. An unknown execution or a broker quantity larger than the
  bot fill fails closed.
- **Snapshot age:** the read-only snapshot carries `fetched_at`; a snapshot older
  than 300 seconds (or fetched for a different replay time) fails as stale.
- **Account id:** the configured DU account must be present in the gateway's
  account list.

A failure is an incident. Do not edit state to make it pass. Stop the loop,
inspect the gateway statement/positions/executions and the state file, then
restore or reconcile deliberately.

## Runbook

### Before a paper session

```bash
uv run python scripts/live.py status --book monthly --broker ibkr-paper --read-only
uv run python scripts/live.py reconcile --book monthly --broker ibkr-paper --read-only
```

Both must be healthy before `run --paper --broker ibkr-paper` (without
`--read-only`).

### Gateway down / session expired

`IBKRAuthError` means the gateway session is gone; log in again. `IBKRUnavailable`
means the gateway/network is down after retries. No state is written and no order
is sent. The next run recomputes the same deterministic plan.

### Extra position / cash mismatch / unknown execution

1. Stop the scheduler.
2. Run the read-only `reconcile --json`; keep the output.
3. Compare the broker statement, positions and executions with the state file.
4. Do not delete the extra broker position or edit a fill. Recover the missing
   execution journal first, or move the unrelated holding to a different account.
5. Restore a verified state backup only after the broker side is understood.

### Stale snapshot

The gateway answered but the snapshot is older than the allowed age. Re-run
`reconcile --read-only`; if it remains stale, check the gateway clock/session. Do
not raise the age limit just to pass the check.

## Safety boundary

- Read-only is a hard adapter flag: `submit`, `cancel`, `replace` and `execute`
  raise before any network call.
- The DU prefix check is unchanged; `U...` is rejected.
- No real broker, no secrets, no network in tests. `tests/test_ibkr.py` uses a
  fake transport with account/position/cash/order/execution payloads, timeouts,
  partial/unknown fills and extra positions.
- This adapter is an execution/reconciliation path, not the HIFO/PFU EUR tax
  ledger.

## Tests

```bash
uv run python tests/test_ibkr.py
uv run python tests/test_live.py
uv run python tests/test_ops.py
```
