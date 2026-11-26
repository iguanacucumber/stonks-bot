# IBKR paper adapter — recorded fake-transport drills

Generated from `uv run python tests/test_ibkr.py` on 2026-11-26. The fake transport
uses the same Client Portal response keys as `ClientPortalTransport`; no gateway,
credentials, network or real order was touched.

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
ok: test_engine_end_to_end_submit_fill_reconcile
ok: 10 IBKR paper checks
```

## What each drill proves

| Drill | Assertion |
|---|---|
| broker down | three attempts then `IBKRUnavailable`; no second order |
| duplicate submit | same broker order id, one placement |
| timeout after accept | retry adopts the existing `cOID`; one broker order |
| partial fill | 4 + 6 shares become two fills; quantity 10, average 10.20 |
| reject | status `REJECTED`, zero fills |
| cancel/replace | delete + modify carry the broker order id; LMT price 99.50 |
| stale position | quantity 9 vs bot 10 fails reconciliation |
| stale cash | 1,000 vs 998 fails reconciliation |
| end-to-end | engine run 1 plans, run 2 submits/fills, state and broker reconcile |

The full paper-order cycle is the last test: `LiveEngine.run` plans a monthly book,
the next session submits those orders through `IBKRPaperBroker.execute`, fills are
applied to the state ledger, and `reconcile` agrees with the fake broker's positions
and cash.

A live gateway was not running on this machine (`https://localhost:5000` refused the
connection), so this recorded fake-transport cycle is the acceptance evidence. The
real transport is the same client used in `docs/ibkr.md`; the only substitute is the
transport object.
