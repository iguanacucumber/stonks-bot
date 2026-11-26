# Broker read-only and corporate-action audit

Scope: `bot/live/brokers/ibkr.py`, `bot/live/corporate.py`,
`bot/live/reconcile.py`, `bot/live/state.py`, the localhost fake IBKR server and
their tests. Paper/read-only only; no real account, credentials or order path was
used.

Run:

```bash
uv run python tests/test_ibkr.py
uv run python tests/test_corporate_actions.py
```

## Read-only / zero-write evidence

- `IBKRPaperBroker._guard_write` is the first statement in `submit`, `execute`,
  `cancel` and `replace`; a read-only broker raises `IBKRReadOnlyError` before
  `ensure_ready()` or any transport call.
- Read paths (`account_summary`, `positions`, `cash`, `open_orders`, `executions`,
  `contract_info`, `read_snapshot`, `reconcile`, `reconcile_strict`) only call GET
  methods.
- `tests/test_ibkr.py` proves no POST/PUT/DELETE reaches the transport, including
  timeout/retry and CLI `--read-only` cases. The localhost
  `tests/fake_ibkr_server.py` records every write and returns 405; the real GET
  cycle leaves `write_calls == []`.

## Findings

| id | severity | finding | status |
|---|---|---|---|
| F1 | **high accounting** | EUR dividend income was stored **net** while withholding was stored separately, so `dividend_income_eur - dividend_withholding_eur` understated net cash and mixed gross/net units. | **fixed** |
| F2 | **high crash/consistency** | A crash after the atomic state write but before the corporate-action CSV append left state with an event but no CSV row. Re-running `corporate` rejected the duplicate without repairing the CSV; reconcile failed permanently without manual work. | **fixed** |
| F3 | **high audit robustness** | Reconcile caught only `ValueError` for corporate events. A truncated split/dividend event raised `KeyError` and crashed the audit instead of returning `FAIL`; duplicate `event_id` values in state were silently collapsed. | **fixed** |
| F4 | **high safety** | An IBKR fill with no `avgPrice`/`lastFillPrice` fell back to the bot's `order.ref_price`. That fabricated an execution price the broker never reported. | **fixed: fail closed** |
| F5 | **medium safety** | `reconcile_strict` accepted a caller-supplied snapshot with a different `account_id`; a cross-account view could pass if positions/cash happened to match. | **fixed: fail closed** |
| F6 | **medium idempotency** | If a submit timed out and the order filled before the retry, it disappeared from `open_orders`; the duplicate rejection could not be adopted, even when an execution carried the same `cOID`. | **fixed: execution adoption** |
| F7 | medium residual | A split adjusts state shares/cost but does not adjust an already-open broker order or pending plan quantity. A split between plan and fill can therefore leave a stale order. | open risk, not exercised in this audit |
| F8 | medium residual | Live corporate dividends apply treaty withholding only; French PFU top-up/credit is not applied in this state path (the research EUR ledger handles it separately). | documented open risk |
| F9 | low | `sync_corporate_actions` returns a changed count based on `upsert_corporate_action`, which currently returns `True` even for an identical row. | open, cosmetic |
| F10 | low | Corporate-action CSV replace fsyncs the file but not the parent directory (ledger/state have directory fsync). | open, low crash-window risk |

### F1 reproduction (fixed)

Before: dividend 10 shares × 1.00, 15% withholding, FX 0.90 stored
`dividend_income_eur = 7.65`, `dividend_withholding_eur = 1.35`; gross EUR income
should be 9.00 and net EUR cash 7.65. The test now asserts:

```python
state.dividend_income_eur == 10.0 * 0.90
state.dividend_income_eur - state.dividend_withholding_eur == 8.5 * 0.90
```

### F2 reproduction (fixed)

`test_crash_between_state_and_ca_csv_is_repairable`: save state with an applied
dividend, do **not** write the corporate CSV, then run
`reconcile(..., repair_ledger=True)`. It repairs the corporate view and passes.
`scripts/live.py corporate` also syncs state→CSV before the duplicate check, so a
crashed retry cannot leave a permanent state/CSV split.

### F3 reproduction (fixed)

`test_malformed_or_duplicate_events_fail_closed`: a dividend event without
`net_cash` previously raised `KeyError`; now reconcile returns `FAIL` with the
event id. A repeated `event_id` is also a `FAIL`.

### F4/F5/F6 reproduction (fixed)

`tests/test_ibkr.py`:
- `test_fill_without_broker_price_fails_closed`;
- `test_snapshot_account_mismatch_fails_closed`;
- `test_filled_order_can_be_adopted_from_execution`.

## Fake-server realism

The localhost fake (`tests/fake_ibkr_server.py`) covers:
- auth status and account list;
- account summary, positions, ledger cash, open orders and executions;
- malformed JSON, HTTP 500, simulated timeout/delay;
- strict reconcile mismatch, unknown execution and stale snapshot;
- write methods recorded and rejected with 405.

Tests: `test_fake_http_readonly_real_get_cycle_and_zero_writes`,
`test_fake_http_retry_malformed_timeout`,
`test_fake_http_strict_mismatch_and_unknown_execution`.

## State/ledger crash consistency

- State is replaced atomically with fsync + directory fsync; ledger upsert is
  atomic and rebuildable from state (`sync_ledger`).
- Corporate actions are state-first; the CSV is a derived audit view. The fixed
  repair path is state→CSV, never CSV→state, so it cannot invent an event.
- Reconcile verifies event ids, gross/withholding/net arithmetic, EUR
  gross/withholding, and the CSV row for every state event.

## Verdict

**Conditional pass for paper/read-only.** Zero-write is enforced at every write
entry point; the fake server records no writes; the audit found and fixed two real
accounting/consistency bugs (net-vs-gross EUR dividend income, crash-recoverability
of the corporate CSV) and three safety gaps (missing broker price, cross-account
snapshot, filled-order adoption). No real-money capability was added.

Residual risks are explicit: split/open-order adjustment, live PFU top-up, and the
cosmetic `sync_corporate_actions` count. This branch should not be merged as a
real-money broker; it remains paper/read-only.

## Tests

```bash
uv run python tests/test_ibkr.py            # 23 checks
uv run python tests/test_corporate_actions.py
uv run python tests/test_core.py
uv run python tests/test_forward.py
```

All pass locally. All fixtures use localhost/temp files; no secrets, no network.
