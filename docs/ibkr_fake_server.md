# Fake IBKR Client Portal server (localhost only)

`tests/fake_ibkr_server.py` is a stdlib `ThreadingHTTPServer` that speaks enough
of the Client Portal Web API for the read-only adapter. It binds `127.0.0.1:0`
(ephemeral port) and never opens a remote connection. It is test infrastructure,
not a production broker.

## Routes

| Method | Path | Payload |
|---|---|---|
| GET | `/v1/api/iserver/auth/status` | authenticated flag |
| GET | `/v1/api/iserver/accounts` | account list |
| GET | `/v1/api/portfolio/{account}/summary` | account summary |
| GET | `/v1/api/portfolio/{account}/positions` | position rows |
| GET | `/v1/api/portfolio/{account}/ledger` | cash balance |
| GET | `/v1/api/iserver/account/orders` | open orders |
| GET | `/v1/api/iserver/account/trades` | executions |
| GET | `/v1/api/iserver/secdef/search` | contract id lookup |
| GET | `/v1/api/iserver/contract/{conid}/info` | contract info |
| POST/PUT/DELETE | any | **405**, recorded in `state.write_calls` |

## Failure knobs

`FakeIBKRState` supports `delay`, `fail_times`/`fail_status`, `malformed` and
arbitrary `positions`, `orders`, `executions`, `cash` and `summary`. The tests use
these to prove retry/backoff, timeout, malformed-JSON fail-closed, stale snapshots,
extra/missing positions and unknown executions.

The important read-only assertion is:

```python
assert srv.state.write_calls == []
assert all(method == "GET" for method, _ in srv.state.requests)
```

## Test coverage

`tests/test_ibkr.py` starts a real localhost server and runs the adapter through
the production `ClientPortalTransport` (not the in-process fake transport). The
same test file also keeps the in-process lifecycle drills (submit, partial fill,
cancel/replace, duplicate, broker down) for the non-read-only adapter.

No gateway, credential, network or real order is used. The server is stopped and
joined in the context manager's `__exit__`.
