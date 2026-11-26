# v8 broker fake-server and corporate-action evidence

Generated from `uv run python tests/test_ibkr.py` and
`uv run python tests/test_corporate_actions.py` on 2026-11-26. All localhost; no
gateway, external network, credentials or real order.

## Fake HTTP read-only cycle

`tests/fake_ibkr_server.py` starts a stdlib `ThreadingHTTPServer` on
`127.0.0.1:0`. The adapter connects through the production
`ClientPortalTransport`, not an in-process mock.

```text
ok: test_fake_http_readonly_real_get_cycle_and_zero_writes
ok: test_fake_http_retry_malformed_timeout
ok: test_fake_http_strict_mismatch_and_unknown_execution
ok: 20 IBKR paper checks
```

Assertions:

- account summary, positions, cash, open orders and executions parsed from real
  HTTP GET responses;
- contract lookup via `/iserver/secdef/search` and contract info via
  `/iserver/contract/{conid}/info`;
- every recorded request method is `GET`;
- `write_calls == []` after attempts to `submit`, `cancel`, `replace`, `execute`;
- HTTP 500 is retried and succeeds; malformed JSON fails closed; timeout raises
  `IBKRUnavailable`; zero writes in all cases;
- extra/missing positions, cash mismatch and unknown executions fail strict
  reconciliation.

## Corporate actions

```text
ok: corporate splits, dividends, state ledger and reconcile
```

- 2:1 split: 10 shares @ 100 -> 20 shares @ 50; cash unchanged.
- Cash dividend: 10 shares × 1.00, 15% withholding, FX 0.90 -> €8.50 net;
  state/CSV event; EUR fields updated.
- Missing position fails; a state event without its corporate-action CSV row fails
  reconciliation.
- `scripts/live.py corporate` applies and rejects a duplicate `event_id`;
  `--read-only` refuses the write.

## Scope

The fake server is test infrastructure, not a production gateway. The recorded
transcript proves the HTTP transport and read-only guard; it does not prove a live
IBKR paper fill. Corporate actions are paper-state accounting events, not broker
instructions.
