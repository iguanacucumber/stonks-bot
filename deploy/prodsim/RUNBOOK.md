# v12 production simulation runbook

**Boundary: simulated real money only.** This service has no real broker
adapter, no account credentials and no outbound order path. The exchange, OMS
and ledger are in-process simulations. Never point this unit at a live venue.

## What runs

`scripts/prod_sim.py run --mode live-paper` starts:

- a monotonic simulation clock with pause/resume and a kill switch;
- a deterministic top-of-book feed built from the committed daily/5-minute
  cache (no network fetch);
- the monthly book at session close, executed on the next symbol tick/open;
- a simulated exchange/ledger injected by the merged v12 modules;
- an optional localhost HTTP API (status, account, orders, events, metrics).

The API binds `127.0.0.1:8080` by default. If the API module is not merged yet,
the CLI logs a warning and keeps the simulation running; operations can still
use the status file at `$STATE_DIR/status.json`.

## Install

```sh
sudo useradd --system --home /opt/stonks-bot --shell /usr/sbin/nologin stonks
sudo install -d -o stonks -g stonks -m 0750 /opt/stonks-bot /var/lib/stonks-prodsim
# deploy the repo at /opt/stonks-bot, then:
sudo install -d -o root -g stonks -m 0750 /etc/stonks-prodsim
sudo install -o root -g stonks -m 0600 \
    deploy/prodsim/prodsim.env.example /etc/stonks-prodsim/env
sudo editor /etc/stonks-prodsim/env      # set STONKS_PRODSIM_API_TOKEN
sudo install -m 0644 deploy/prodsim/stonks-prodsim.service \
    /etc/systemd/system/stonks-prodsim.service
sudo systemctl daemon-reload
sudo systemctl enable --now stonks-prodsim.service
```

The cache must already be refreshed by the existing data job. `live-paper`
polls the cache and fails closed (`ERROR`, `READY=false`) if the newest
completed session is older than `--max-stale-days` (default 3). It never
fabricates a tick.

## Health and status

```sh
curl -sS http://127.0.0.1:8080/healthz
curl -sS http://127.0.0.1:8080/readyz
curl -sS -H "Authorization: Bearer $STONKS_PRODSIM_API_TOKEN" \
     http://127.0.0.1:8080/api/v1/status
curl -sS http://127.0.0.1:8080/metrics
```

Without the HTTP API:

```sh
uv run python scripts/prod_sim.py status --state-dir /var/lib/stonks-prodsim
```

`readyz` is false when the clock is killed/stopped, no tick has arrived, the
last tick is stale, or a supervisor-announced session produced no tick.

## Controls

```sh
TOKEN="$STONKS_PRODSIM_API_TOKEN"
curl -sS -X POST -H "Authorization: Bearer $TOKEN" \
     http://127.0.0.1:8080/api/v1/admin/pause
curl -sS -X POST -H "Authorization: Bearer $TOKEN" \
     http://127.0.0.1:8080/api/v1/admin/resume
curl -sS -X POST -H "Authorization: Bearer $TOKEN" \
     http://127.0.0.1:8080/api/v1/admin/kill
```

The kill switch cancels every open order, blocks new submissions and is
terminal for the process. A restart starts a fresh process; it does not
resurrect a killed run. Check the audit events before restarting:

```sh
curl -sS -H "Authorization: Bearer $TOKEN" \
     'http://127.0.0.1:8080/api/v1/events?since=0&limit=100'
```

## Restart and recovery

- The durable store is authoritative for orders/events/snapshots.
- On start the facade restores known client order ids, so replaying the same
  monthly target does not submit a second order.
- A store record whose broker ack was lost is reconciled by client order id;
  the simulated exchange must deduplicate. Never delete the state directory to
  "fix" a mismatch.
- After a crash, verify `readyz`, then compare `/api/v1/account` and
  `/api/v1/orders?status=NEW,ACKNOWLEDGED,PARTIAL` before resuming trading.
- Back up the state directory with the existing backup job; restore it only
  while the service is stopped.

## Operations checks

```sh
# Unit health and recent logs.
systemctl status stonks-prodsim.service
journalctl -u stonks-prodsim.service -n 200 --no-pager

# Required scenarios and the in-process load check.
uv run python scripts/prod_sim.py scenario all
uv run python scripts/prod_sim.py load --ticks 10000 --orders 1000
```

`scenario all` covers partial fills, duplicate idempotency, cancel race,
buying power, data outage, restart, kill, replay idempotency and load. It uses
in-process fakes and writes no real state.

## Incident notes

- **Data outage:** service goes `ERROR`/not ready; do not manually inject
  prices. Fix the cache refresh, then restart.
- **Runaway order rate:** pause the clock first; cancel open orders; then
  investigate. The risk limits (`max_gross`, `max_order_notional`,
  `max_orders_per_min`) are enforced by the exchange/OMS.
- **Token exposure:** stop the unit, rotate `STONKS_PRODSIM_API_TOKEN`, restart, then
  review `/api/v1/events`. The token is never logged.
- **Disk full:** the status/store writes fail closed; free space and restart.
  Do not run the service on an untracked tmpfs.

## Uninstall

```sh
sudo systemctl disable --now stonks-prodsim.service
sudo rm /etc/systemd/system/stonks-prodsim.service
sudo systemctl daemon-reload
# Keep /var/lib/stonks-prodsim for audit; archive before deleting.
```
