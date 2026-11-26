# IBKR paper adapter

`--broker ibkr-paper` connects the live autopilot to an Interactive Brokers
**paper** account through the Client Portal Web API. The local deterministic
`paper` broker remains the default. There is no real-money adapter, no live-account
switch, and no credential in this repository.

## Why the Client Portal Web API

`ib_insync` is a good library, but it would add another runtime dependency and a
TWS/Gateway socket layer to the test surface. The Client Portal Web API is plain
JSON over HTTP:

- `requests` is already a project dependency;
- the Client Portal Gateway owns login, 2FA and the paper session, so this repo
  never sees a password or OAuth token;
- every call can be replaced by a fake transport with the same response shapes,
  which is how the failure drills run without a network.

The trade-off is that the gateway must be running and logged in before a paper run.
That is documented in the runbook below.

## Safety boundary

- Only account ids starting with `DU` are accepted. A `U...` live account id is
  rejected at configuration time.
- `plan`, `status`, and `run` without `--paper` never contact the broker. They use
  the deterministic local preview broker.
- `run --paper --broker ibkr-paper` is the only path that submits orders.
- The broker name is part of the live config signature. Switching an existing
  state file from `paper` to `ibkr-paper` fails closed until
  `--allow-config-change` is used after reconciliation.
- Use a dedicated paper account. Reconciliation treats any extra broker position
  as an incident, not as an unrelated holding to ignore.
- Market orders, long-only. No shorting, options, margin calls or live orders.
- A full day order that does not terminalise within `IBKR_PAPER_MAX_POLL` is
  cancelled; fills already received are recorded, never rounded up.

## Setup

1. In IBKR Account Management, open a **paper trading account** and note its
   `DU...` account id. Enable the paper account for API access if your gateway
   configuration asks for it.
2. Download and run the IBKR Client Portal Gateway. The standard local endpoint is
   `https://localhost:5000/v1/api`. The gateway serves a self-signed certificate;
   the adapter verifies TLS by default.
3. Log in to the gateway in a browser, complete 2FA, and confirm
   `GET /v1/api/iserver/auth/status` reports `authenticated: true`.
4. Export the environment variables. Use the placeholders in `.env.example`; never
   commit a real account id or session.
5. Check the gateway before an order session:

```bash
curl -k https://localhost:5000/v1/api/iserver/auth/status
uv run python scripts/live.py status --book monthly --broker ibkr-paper
```

If the gateway is using a self-signed certificate on localhost only, set
`IBKR_PAPER_VERIFY_SSL=false` for that local session. Do not disable verification
for a remote gateway.

### Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `IBKR_PAPER_ACCOUNT` | required | `DU...` paper account id |
| `IBKR_PAPER_BASE_URL` | `https://localhost:5000/v1/api` | gateway API root |
| `IBKR_PAPER_TIMEOUT` | `15` | per-request timeout, seconds |
| `IBKR_PAPER_POLL_INTERVAL` | `1` | order-status poll interval, seconds |
| `IBKR_PAPER_MAX_POLL` | `30` | maximum wait for a day order |
| `IBKR_PAPER_MAX_RETRIES` | `3` | retries for transient transport failures |
| `IBKR_PAPER_BACKOFF` | `0.5` | exponential backoff base, seconds |
| `IBKR_PAPER_VERIFY_SSL` | `true` | TLS certificate verification |

Missing or malformed values fail before any order is sent. There is no `.env`
loader in the bot; export the variables in the shell, systemd unit, or process
manager. `.env` is gitignored.

## Commands

```bash
# Read-only preview. Does not need IBKR credentials and never contacts the broker.
uv run python scripts/live.py plan --book monthly --broker ibkr-paper

# Dry run: same preview, no state written.
uv run python scripts/live.py run --book monthly --broker ibkr-paper

# Paper execution: settles the prior plan through IBKR paper, then plans the next.
uv run python scripts/live.py run --book monthly --broker ibkr-paper --paper

# Local fill/state audit plus broker positions and cash.
uv run python scripts/live.py reconcile --book monthly --broker ibkr-paper
```

Recommended dedicated state directory:

```bash
uv run python scripts/live.py run --book monthly --broker ibkr-paper --paper \
  --state-dir state/ibkr-paper
```

The first `run --paper` only plans orders. The next completed session settles that
plan at the open through IBKR. Re-running a completed session is a no-op.

## Order lifecycle

| Stage | Adapter behaviour |
|---|---|
| submit | deterministic `cOID` from `OrderIntent.order_id`; check open orders for the same `cOID` first |
| acknowledge | parse the order id; confirmation prompts are answered once with `confirmed: true` |
| partial fill | poll status, emit one `Fill` per cumulative quantity delta, apply it to the paper portfolio |
| fill | terminal status; remaining quantity must be zero or the order is left as `PARTIAL` |
| cancel | `DELETE /iserver/account/{account}/order/{orderId}` |
| replace | `POST /iserver/account/{account}/order/{orderId}` with the new quantity/limit price |
| reject | status `Rejected`/`Inactive` becomes `REJECTED`; no fill is fabricated |

If a submit times out after the broker accepted the order, the retry searches open
orders for the same `cOID`. IBKR's duplicate-client-order-id error is treated as an
idempotent hit. A second submit for the same bot order never creates a second
broker order.

## Failure drills

`uv run python tests/test_ibkr.py` uses `FakeTransport`, which returns the same
Client Portal response keys as the real client. The recorded drill output is in
`results/ibkr_paper_drill.md`.

| Drill | Expected result |
|---|---|
| broker down | configured retries, then `IBKRUnavailable`; no order persisted |
| timeout after accept | retry adopts the existing `cOID`; exactly one broker order |
| duplicate submit | second call returns the first broker order; no second placement |
| partial fill | two fills, portfolio quantity and average cost match the broker |
| reject | `REJECTED`, zero fills |
| cancel/replace | delete and modify calls carry the correct broker order id |
| stale position | reconciliation fails with the ticker and quantity difference |
| end-to-end | engine run 1 plans; run 2 submits, fills, persists and reconciles |

## Reconciliation

`scripts/live.py reconcile --broker ibkr-paper` runs the existing local audit
(fills vs orders vs portfolio vs ledger) and then compares the bot portfolio with
the paper account:

- every non-zero broker position must exist in the bot state with the same quantity
  within `1e-4` shares;
- extra broker positions fail the check;
- broker cash must match the bot cash within the larger of 1 currency unit or
  1 ppm of the balance.

The paper account must be dedicated to this bot. If it holds anything else, the
check fails by design. A failure is an incident: stop, inspect the gateway, the
state file and the broker statement, then repair state or the account. Do not use
`--allow-config-change` to paper over a position mismatch.

## Runbook

### Before the session

1. Gateway logged in and `/iserver/auth/status` authenticates.
2. `status --broker ibkr-paper` shows the expected book, market, cash and pending
   plan.
3. `reconcile --broker ibkr-paper` is OK.
4. The forward gate is reported; it is informational for this adapter and does not
   bypass the live rails.

### Daily order run

```bash
uv run python scripts/live.py run --book monthly --broker ibkr-paper --paper \
  --state-dir state/ibkr-paper
uv run python scripts/live.py reconcile --book monthly --broker ibkr-paper \
  --state-dir state/ibkr-paper
```

Run after the US close and after the cached bar exists. Re-running the same session
is safe.

### Broker down or session expired

`IBKRAuthError` means the gateway session is gone. Re-login in the browser and rerun.
`IBKRUnavailable` means the gateway or network is down; no order is sent and no state
is lost. The next run recomputes the same deterministic plan.

### Reject

The order is recorded as `REJECTED` with no fill. The next session marks the actual
portfolio and the next month-end plan sizes from it. Inspect the gateway message and
the live report; do not hand-edit fills.

### Partial fill or timeout

The adapter records the fills it saw, cancels the remainder, and marks the order
`PARTIAL` (or `CANCELLED` with zero fill). The next run sees the actual position.
Residuals are not retried automatically; the next monthly plan sizes from the actual
book.

### Stale position or cash mismatch

Stop the loop. Run `reconcile --broker ibkr-paper`. Compare the state file, the IBKR
paper statement and the gateway positions. If the broker has a fill the bot missed,
recover the fill journal from the gateway and reconcile before allowing another run.
Never fabricate a fill or edit state to make the check pass.

### Cancel a pending plan manually

There is no CLI cancel command yet; use the adapter in a Python shell with the
recorded broker order id from `status`:

```python
from bot.live.brokers.ibkr import IBKRPaperBroker
broker = IBKRPaperBroker.from_env()
broker.cancel("<broker-order-id>")
```

Then reconcile. This is intentionally manual: cancelling an order is a human
decision, not an automatic retry.

## Known limits

- Market orders only; limit replace is implemented for manual use and drills.
- Fractional quantities depend on the IBKR paper product; the config keeps the
  research default (`fractional_shares=true`). A rejected fractional order is a
  broker reject, not silently rounded.
- Commissions use the broker's reported amount when present. If the status payload
  omits it, the adapter records zero and reconciliation will expose the cash
  difference.
- The local paper ledger is pre-tax and average-cost. This adapter is an execution
  path, not the HIFO/PFU EUR tax ledger.
- The gateway is a local web service. Never expose it publicly; it is a trading
  session, not a public API.
- There is no real-money mode. A `U...` account id is refused even if a user points
  the gateway at it.
