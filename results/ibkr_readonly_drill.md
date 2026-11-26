# IBKR read-only reconciliation — fake-transport evidence

Generated from `uv run python tests/test_ibkr.py` on 2026-11-26. The fake transport
uses the same Client Portal response keys as `ClientPortalTransport`; no gateway,
credentials, network or real order was touched. All 17 checks pass.

```text
ok: test_config_is_paper_only_and_env_only
ok: test_plan_never_contacts_ibkr
ok: test_submit_is_idempotent
ok: test_submit_timeout_adopts_existing_order
ok: test_partial_then_full_fill_cycle_and_reconcile
ok: test_reject_returns_no_fill
ok: test_cancel_and_replace
ok: test_broker_down_retries_then_fails_closed
ok: test_stale_position_fails_reconcile
ok: test_read_only_snapshot_methods_and_no_writes
ok: test_strict_reconcile_positions_cash_and_human_diff
ok: test_strict_reconcile_unknown_execution_partial_and_stale
ok: test_read_only_timeout_fails_closed_without_writes
ok: test_read_only_cli_refuses_paper_writes
ok: test_cli_reconcile_read_only_uses_strict_broker
ok: test_cli_status_read_only_exposes_broker_view
ok: test_engine_end_to_end_submit_fill_reconcile
ok: 17 IBKR paper checks
```

## Read-only assertions

| Drill | Assertion |
|---|---|
| account summary / positions / cash / open orders / executions | fake payloads parsed into `IBKRReadOnlySnapshot` |
| zero writes | read-only `submit`, `cancel`, `replace`, `execute` raise `IBKRReadOnlyError`; place/cancel/replace counters stay zero |
| CLI status | `status --broker ibkr-paper --read-only --json` exposes `broker_read_only`; no writes |
| CLI reconcile | `reconcile --broker ibkr-paper --read-only --json` uses strict broker reconcile; no writes |
| CLI paper guard | `run --paper --read-only` exits 2 and writes nothing |
| extra position | broker QQQ 2 with no bot position fails, with human diff |
| missing position | bot position absent at broker fails |
| cash mismatch | broker 998 vs bot 1,000 fails |
| quantity mismatch | broker SMH 9 vs bot 10 fails with `broker 9 vs bot 10 (diff -1)` |
| unknown execution | broker execution 10 shares vs bot fill 4 shares fails |
| stale snapshot | fetched 2020 vs now 2026 fails as stale |
| timeout | three read attempts then `IBKRUnavailable`; no write calls |
| DU-only | `U...` account rejected at config time; `DU...` accepted |
| read-only signature | read-only toggle does not change the live state signature |

## Notes

- A live gateway was not running on this machine (`https://localhost:5000`
  refused the connection); this is the acceptance evidence for the read-only
  transport. The real transport uses the same method names.
- Read-only endpoints are `GET` only: account summary, positions, ledger cash,
  open orders and `/iserver/account/trades` executions. No `POST`, `PUT` or
  `DELETE` path is reachable through the read-only surface.
- Reconciliation is fail-closed: any extra/missing position, cash mismatch,
  unknown execution/open order or stale snapshot returns a failure report and a
  non-zero CLI exit. `--read-only --repair-ledger` is refused.
