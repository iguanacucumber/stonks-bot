# v12 production simulation — end-to-end evidence

Date: 2026-11-27. Commit: `1ae7028` + demo. **Simulated money only.**
No real broker, no credentials, no live order path.

## What was built

| layer | module | evidence |
|---|---|---|
| simulated exchange/ledger | `bot/prodsim/exchange.py`, `ledger.py` | cash, margin, fees, slippage, partial fills, limit/day/IOC/GTC, splits/dividends |
| OMS + durable store | `bot/prodsim/oms.py`, `store.py` | idempotency, cancel race, crash recovery, SQLite WAL journal |
| HTTP API | `bot/prodsim/api.py`, `server.py` | bearer/admin auth, orders, account, positions, events, metrics, pause/resume/kill |
| real-time loop | `bot/prodsim/clock.py`, `replay.py`, `simulation.py`, `strategy.py` | replay/live-paper clock, cached-bar ticks, monthly target, kill switch |
| CLI/deploy | `scripts/prod_sim.py`, `deploy/prodsim/` | run/status/scenario/load, systemd unit, env example, runbook |

## End-to-end HTTP demo

Command:

```bash
uv run python scripts/prodsim_demo.py --port 8099
```

Result (`/tmp/opencode/prodsim-demo.json`):

| step | result |
|---|---|
| `GET /healthz` | 200 `ok` |
| feed first tick | `readyz` 200, ready true |
| `POST /api/v1/orders` | **201**, order `SIM-00000001`, `ACKNOWLEDGED` |
| same `Idempotency-Key` replay | **200**, same order id, no second broker order |
| next two ticks | **2 fills** of 50 shares at 100.5401 and 101.0402 |
| `GET /api/v1/orders` | 200, `FILLED`, filled 100 |
| `GET /api/v1/account` | 200, cash 89,919.98, equity 100,020.98, fees 1.0079 |
| `GET /api/v1/positions` | 200, SPY 100 @ 100.790154, unrealized +21.98 |
| `GET /metrics` | 200 Prometheus text |
| `POST /api/v1/admin/kill` | 200, state `KILLED` |
| `GET /readyz` after kill | **503**, state `KILLED`, ready false |

The API stayed up after the kill so the final status/account remained readable.
`sim.stop()` owns HTTP shutdown.

## Required scenarios

`uv run python scripts/prod_sim.py scenario all`:

| scenario | ok | ticks | orders | fills | errors |
|---|:---:|---:|---:|---:|---:|
| partial_fill | yes | 3 | 1 | 2 | 0 |
| duplicate | yes | 3 | 1 | 1 | 0 |
| cancel_race | yes | 3 | 1 | 1 | 0 |
| buying_power | yes | 1 | 1 | 0 | 0 |
| data_outage | yes | 1 | 0 | 0 | 0 |
| restart | yes | 1 | 0 | 1 | 0 |
| kill | yes | 1 | 1 | 0 | 0 |
| replay_idempotency | yes | — | 5 | 5 | — |
| load | yes | 10,100 | 1,000 | 1,000 | 0 |

Replay idempotency: two runs produced identical cash, equity, fees, fills and
positions (`identical=true`).

Load: **10,100 ticks / 1,000 orders / 1,000 fills**, 0 errors, 0.743 s,
**3.8 MB peak**, 3,001 events.

## Tests

```bash
for t in tests/test_prodsim_*.py; do uv run python "$t"; done
```

All 10 prodsim scripts pass: contract, exchange, OMS, store, API, clock, replay,
strategy, simulation, scenarios, plus the exchange+OMS+store integration test.
Full repo release check remains **11 PASS / 1 WAIT / 0 FAIL** (forward gate).

## Boundaries and limitations

- **No real money.** The exchange/ledger is in-process. There is no live broker
  adapter, no credentials, no order path to a venue.
- **API is localhost by default.** A non-empty bearer token is required outside
  `--dev`; admin actions use a separate admin token.
- **Monthly universe preview.** The CLI previews the target at replay start;
  a later target that introduces a new symbol is rejected as `unknown symbol`
  (fail closed). Widen `--symbols` or restart with a refreshed preview.
- **Replay feed, not exchange data.** Ticks are deterministic OHLCV-derived
  top-of-book, not a live market feed. No queue position or auction model.
- **Forward proof is separate.** The forward gate is still WAIT (US 0/5,
  TECH 1/5, tax 0/5); this server does not change that evidence.
