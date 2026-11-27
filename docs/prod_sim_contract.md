# v12 production simulation contract

**Boundary: simulated real money only.** No real broker, no real account, no
credentials, no live order path. The HTTP server binds to localhost by default.

## Goal

Run the monthly book as a long-lived service with a realistic simulated broker:
cash/margin, commissions, slippage, partial fills, order lifecycle, persistence,
health, metrics, alerts, crash recovery and scenario/load tests.

## Shared modules (this commit)

| path | role |
|---|---|
| `bot/prodsim/contract.py` | dataclasses, protocols, JSON/event helpers, shared validation |
| `docs/prod_sim_contract.md` | this contract |

## Branch ownership

| branch | owns | must not edit |
|---|---|---|
| `v12/server-core` | `bot/prodsim/oms.py`, `store.py`, tests | exchange, HTTP, replay |
| `v12/broker-sim` | `bot/prodsim/exchange.py`, `ledger.py`, tests | OMS, HTTP, replay |
| `v12/sim-api` | `bot/prodsim/server.py`, `api.py`, `auth.py`, `metrics.py`, tests | exchange, OMS, replay |
| `v12/sim-ops` | `bot/prodsim/clock.py`, `replay.py`, `simulation.py`, `scripts/prod_sim.py`, `deploy/`, ops/chaos/load tests | exchange, OMS internal, API internals |

If a shared change is unavoidable, keep it additive and document it in the PR.

## Interface map

```
clock/replay (sim-ops)
      │ Tick / session
      ▼
Simulation facade (sim-ops) ─────► strategy adapter (sim-ops)
      │ submit/cancel/on_tick               │ OrderRequest
      ▼                                     ▼
OMS (server-core) ───────────────► Exchange (broker-sim)
      │ OrderState/Fill                     │ AccountSnapshot
      ▼                                     ▼
Store (server-core)                 ledger/account (broker-sim)
      │ events
      ▼
HTTP API (sim-api) ◄──── SimulationProtocol
```

`SimulationProtocol`, `ExchangeProtocol`, `StoreProtocol` are in
`bot/prodsim/contract.py`. Do not change their method signatures without a
coordinated PR.

## HTTP API (default `127.0.0.1:8080`)

| method | path | auth | behavior |
|---|---|---|---|
| GET | `/healthz` | no | process alive |
| GET | `/readyz` | no | data+store+clock ready, not killed |
| GET | `/api/v1/status` | token | `SimulationStatus` |
| GET | `/api/v1/account` | token | `AccountSnapshot` |
| GET | `/api/v1/positions` | token | position list |
| GET | `/api/v1/orders?status=&limit=` | token | order list |
| GET | `/api/v1/orders/{id}` | token | one order |
| POST | `/api/v1/orders` | token + `Idempotency-Key` | submit `OrderRequest` |
| DELETE | `/api/v1/orders/{id}` | token | cancel |
| POST | `/api/v1/admin/pause` | admin token | pause clock |
| POST | `/api/v1/admin/resume` | admin token | resume clock |
| POST | `/api/v1/admin/kill` | admin token | kill switch, cancel open orders |
| GET | `/api/v1/events?since=&limit=` | token | durable event feed |
| GET | `/metrics` | no | Prometheus text |

Auth is a static bearer token from `RunConfig.api_token`/env. If empty, the
server refuses to start outside `--dev`. Never log the token.

## Real-time model

- `replay`: replay cached bars/ticks as fast as `speed` allows.
- `live-paper`: long-running loop, no real broker; uses the committed cache and
  sleeps until the next simulated tick/session. It still fails closed on stale
  data or a closed market.
- A tick is top-of-book (`bid`, `ask`, `last`, `volume`). Fills happen only on a
  subsequent tick, never on the signal bar.
- The clock must be monotonic; duplicate/out-of-order ticks are rejected.

## Real-money simulation rules

- Starting cash from `RunConfig.capital`; no deposits/withdrawals unless an
  explicit admin endpoint adds them (not required for v12).
- Commission `max(min_commission, notional * commission_bps/1e4)`.
- Slippage `slippage_bps` applied against the taker.
- Partial fills capped by `participation_cap * tick.volume`.
- Buying power includes cash + margin; default no shorting.
- Reject unknown symbols, bad qty, limit without price, insufficient buying
  power, duplicate client order id, halted symbol, max order notional.
- Daily loss / gross / position limits from `RiskLimits`; kill switch cancels
  open orders and blocks new ones.

## Required scenarios

1. Buy 100 shares, partial fill over two ticks, mark-to-market, P&L.
2. Duplicate `Idempotency-Key` returns the original order, never a second fill.
3. Cancel/replace race: cancel after partial fill leaves the filled qty intact.
4. Insufficient buying power / max order notional rejection.
5. Data outage: no tick for a session => READY false, no fabricated fill.
6. Broker timeout/duplicate submit => OMS reconciles by client order id.
7. Crash between store write and broker ack => restart replays journal, no double
   fill.
8. Kill switch during an open order => cancel + block.
9. Same-day replay idempotency: rerun produces the same final account.
10. Load: at least 10k ticks and 1k orders in-process without unbounded memory.

## Acceptance

- All new tests pass with no network.
- `scripts/release_check.py` still passes; new checks are additive.
- No real-money adapter, credentials, or order submission.
- README/RELEASE document the simulation boundary.
