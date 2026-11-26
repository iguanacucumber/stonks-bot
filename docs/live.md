# Live autopilot (paper only)

`scripts/live.py` turns the monthly research book into an idempotent daily loop. It
writes no orders: the only execution adapter is the deterministic paper broker. Real
trading would require a new adapter behind `bot.live.broker.Broker` and an explicit
opt-in flag; neither exists here.

## Market choice

New live plans default to **US monthly**, the best real-life backtest:

| book | headline | EUR ledger | maxDD (EUR ledger) | status |
|---|---:|---:|---:|---|
| US monthly, IBKR 2 bps | 70.86x | 66.72x | -57.5% | recommended live market |
| TECH monthly, IBKR 2 bps | 57.01x | 53.45x | -66.0% | forward validation track |

The existing forward paper book (`scripts/paper_daily.py --market TECH`) is **not**
switched or rewritten by this CLI. Keep it running to finish the 5-date gate. Select
TECH explicitly when replaying that track:

```bash
uv run python scripts/live.py plan --book monthly                 # US default
uv run python scripts/live.py plan --book monthly --market TECH  # validation track
```

Any market in `bot.markets.MARKETS` can be selected. The planner uses the same
`strategy.Market` / `backtest.target_book` path as research; there is no live-only
signal and no special-case TECH branch.

## Commands

```bash
# Read-only target/orders, report and JSON. Writes nothing.
uv run python scripts/live.py plan --book monthly
uv run python scripts/live.py plan --book monthly --market TECH --json

# Dry run by default; --paper writes state/live/*.json and *.csv.
uv run python scripts/live.py run --book monthly
uv run python scripts/live.py run --book monthly --paper
uv run python scripts/live.py status --book monthly
uv run python scripts/live.py reconcile --book monthly

# Replay a completed session; never use --as-of in the live cron.
uv run python scripts/live.py plan --book monthly --as-of 2026-11-25
```

`--config live.toml` loads a TOML profile; explicit CLI flags win. A config change
against an existing state file fails closed until `--allow-config-change` is used
after reconciling. State and ledger paths are per `(book, market)`, e.g.
`state/live/monthly_us.json` and `state/live/monthly_us.csv`.

## Safety rails

- Market-closed and stale-data gates; no plan without a fresh benchmark close.
- Max gross, max turnover, max order count and duplicate-order keys.
- Drawdown circuit breaker and daily core trend cut with hysteresis.
- Next-open paper fills, slippage, commission, participation cap and explicit
  PARTIAL/UNFILLED status; a cap is never rounded up to a fabricated full fill.
- Atomic state replacement plus an append-only ledger keyed by
  `(book, market, session)`. Re-running a completed session is a no-op.
- `reconcile` rebuilds cash, positions, fees and realized P&L from the fill journal and
  checks every ledger row. A mismatch is an incident, not a warning to smooth over.

## Native and EUR books

The engine and orders are native-currency (USD for US/TECH). Every state/ledger row
also stores the EUR view so the EUR-ledger work can reconcile later:

- state: `currency`, `last_fx_rate`, `last_fx_date`, `last_equity_eur`,
  `last_cash_eur`, `last_gross_eur`, `initial_capital_eur`, plus cumulative
  `fees_paid_eur`, `financing_paid_eur`, `realized_pnl_eur`;
- ledger: `currency`, `fx_rate`, `fx_date`, `equity_eur`, `cash_eur`, `gross_eur`,
  `target_gross_eur`, `fees_eur`, `financing_eur`, `realized_pnl_eur`;
- each fill in state/ledger JSON: `currency`, `fx_rate`, `fx_date`, `notional_eur`,
  `commission_eur`, `realized_pnl_eur`. The fill uses the **trade-date** rate, not the
  rate on the later run that settles it.

`fx_rate` is EUR per one native unit. Missing FX is reported as blank/`None`, never
invented; native paper trading still marks, but the EUR columns stay unavailable until
the cache has the pair.

## Tests

```bash
uv run python tests/test_live.py
```

Covers the US default and explicit TECH config, FX conversion, state/ledger round-trip,
idempotent reruns, holiday/stale-data gates, report labels and partial fills.

## Files and wiring

| Path | Role |
|---|---|
| `bot/live/config.py` | immutable profile, book overrides, rails, state paths |
| `bot/live/calendar.py` | NYSE calendar and month-end rebalance dates |
| `bot/live/signal.py` | data gates, `strategy.Market`, selector target, EUR FX |
| `bot/live/models.py` | `Position`, `OrderIntent`, `Fill`, `Plan`, `Portfolio` |
| `bot/live/state.py` | atomic JSON state, flock, ledger upsert |
| `bot/live/broker.py` | `Broker` protocol and `PaperBroker` next-open fills |
| `bot/live/rails.py` | gross/turnover/duplicate checks and risk overlay |
| `bot/live/engine.py` | `plan`, `run`, `status`; settlement and idempotency |
| `bot/live/reconcile.py` | fill/order/position/cash/EUR/ledger audit |
| `bot/live/health.py` | blocking vs warning health checks |
| `bot/live/report.py` | human and JSON reports |
| `scripts/live.py` | CLI: `plan`, `run`, `status`, `reconcile` |
| `tests/test_live.py` | synthetic-panel tests; no Yahoo |

Shared edit: `bot/backtest.py` gains a public `target_book()` wrapper around the
existing selector. It has no accounting changes. `bot/eur_ledger.py` is the
research replay; the live ledger stores the same EUR-per-native convention and
trade-date fill FX so the two can be reconciled later.

## One run, step by step

1. Resolve the last completed US session (`data.target_session`, 16:30 ET data
   delay). A weekday holiday is rejected by the NYSE calendar; a missing SPY bar is
   a stale-data block.
2. Build a signal snapshot from the committed cache, truncated at that session. No
   fetch and no look-ahead.
3. Settle the prior pending plan at its intended **next open**, if that bar exists.
   A missing open leaves the plan pending; nothing is fabricated.
4. Mark positions at the session close, accrue 5%/yr financing on negative cash, and
   update the peak/drawdown/trend risk state.
5. On a month-end decision date (or the first run with no target), call the shipped
   selector and apply leverage. On other days hold the existing target; a risk
   breach can cut the current book without a rebalance.
6. Build order intents for the next session's open, run every rail, and either
   persist a plan or a BLOCKED record with zero orders.
7. Upsert exactly one ledger row for `(book, market, session)`. State is written
   atomically before the ledger; `reconcile --repair-ledger` rebuilds a lost view
   from state.

## Safety defaults

Every rail is fail-closed: a violation produces `status=BLOCKED`, zero orders, a
non-zero CLI exit (`2`), and an auditable ledger/block record. There is no partial
execution of a plan that failed a rail.

| Rail | Default | Behavior |
|---|---:|---|
| max gross | 3.0x | ceiling only; target gross above it blocks; it is never a target |
| max turnover | 3.0x | `max(buys,sells)/equity` above the cap blocks |
| max orders | 50 | more intents than this blocks |
| min order | 25 native | execution minimum only; target/current drift is always in the report |
| trade band | 0.0 | matches the shipped research book; no extra live band |
| stale data | current close | any required symbol missing today's close blocks; `max_stale_days` only labels age in the reason |
| market closed | NYSE calendar | holiday/weekend/unknown session blocks |
| duplicate order | ids + `(ticker,side,fill_session)` | any collision blocks |
| pending fill | exact fill session | an older pending plan blocks, never fills at a stale historical open |
| drawdown breaker | -35% -> 0.5x | cuts the current book; recovery at -10%; non-validated overlay |
| trend cut | core below 200d MA -> 0.5x | research daily overlay |
| leverage gap | > 2.5x (or book band) | plans a sell before any new buy |

The drawdown breaker is a **live safety overlay**, not a backtested alpha rule. It
can make the paper book differ from the research equity curve; the report always
shows `risk_off`, the drawdown, and the scale. Set `--dd-stop 0` to disable it for a
research-parity run. A trend cut clears only when the core is back above its MA; a
drawdown cut clears only at the recovery level. A cleared cut does not re-lever
until the next month-end rebalance.

If a risk cut or gap deleverage is blocked by another rail, the target is persisted
as `pending_cut` and retried on the next session; it is not silently forgotten. If
the retry is still blocked, the book stays in the old target and the block is
reported until a human reconciles.

## Idempotency and crash recovery

Keys:

- plan id = hash(book, market, signal session, fill session, target weights, equity,
  reason, config signature);
- order id = plan id + ticker + side;
- fill id = order id + fill session;
- ledger key = `(book, market, session)`.

The JSON state is replaced with one `os.replace` after an fsync, and a `flock`
serialises writers. Plan and run record are written together; the ledger row is an
audit view that can be rebuilt from state. A rerun of a completed session reconciles
the ledger and returns the stored plan without new orders. A crash before the state
write leaves the previous state and the same deterministic plan is recomputed. A
crash after the state write but before the ledger write is repaired by
`reconcile --repair-ledger` or by rerunning the same session.

A changed config refuses to reuse state unless `--allow-config-change` is explicit
after a manual reconcile. A corrupt state file fails closed; it never starts a new
book silently.

## Settlement price and paper fills

`PaperBroker` fills a day order at the next session's **open**:

- buy price = open x (1 + slippage), sell price = open x (1 - slippage);
- commission = max(min_commission, notional x commission_bps / 10,000);
- fill quantity is capped at `max_participation x open-day volume` (default 2%);
- sells execute before buys, even if a hand-built plan lists them in another order;
- a binding cap produces a `PARTIAL` order/fill, never a rounded-up full fill;
- zero/missing volume or open produces `UNFILLED`, never a synthetic price;
- a sell that partially fills cannot be hidden by a later buy using its expected
  cash; the next run sees the actual position and gross exposure;
- the residual of a day order is not retried the next day. The next month-end plan
  sizes from the actual position, so the deviation is visible and reconciled.

A pending plan can settle only on its exact `fill_session`. If the process misses
that session, the next run blocks with `overdue pending plan` and leaves the state
untouched for `reconcile`; it never fabricates a historical open.

The paper ledger is **pre-tax and average-cost**, not HIFO/PFU, and is not the
after-tax EUR ledger. It tracks native and EUR amounts for later reconciliation but
must not be compared with `results/eur_ledger.md` after-tax numbers until tax lots
and annual PFU are implemented. Prices are split/dividend-adjusted cache prices, not
raw broker statements.

## Cron / systemd

Run after the US close and after Yahoo has published the daily bar. The loop is
idempotent, so a retry is safe. Keep `--paper` until a real adapter exists.

Cron (server timezone UTC; 21:40 UTC is 16:40 ET during EST, 22:40 during EDT, so a
systemd timer with a timezone is safer):

```cron
40 21 * * 1-5 cd /home/user/projects/stonks-bot && mkdir -p state/live && \
  uv run python scripts/live.py run --book monthly --market US --paper >> state/live/cron.log 2>&1
```

`/etc/systemd/system/stonks-live.service`

```ini
[Unit]
Description=stonks-bot paper live loop
After=network-online.target

[Service]
Type=oneshot
WorkingDirectory=/home/user/projects/stonks-bot
ExecStart=/usr/bin/env uv run python scripts/live.py run --book monthly --market US --paper
User=stonks
Group=stonks
```

`/etc/systemd/system/stonks-live.timer`

```ini
[Unit]
Description=Run the paper live loop after the US close

[Timer]
OnCalendar=Mon..Fri 16:45 America/New_York
Persistent=true

[Install]
WantedBy=timers.target
```

Enable with `systemctl enable --now stonks-live.timer`. Add a separate timer for
`reconcile` and `status` if you want a daily health alert; `status` exits `2` when
blocked or stale.

## Paper-only boundary and future real broker

No API keys, account numbers or secrets belong in this repo. The only integration
point is `bot.live.broker.Broker`:

1. Implement `execute(plan, bars, now, portfolio) -> list[Fill]` against the broker's
   own fills; do not trust local prices for execution.
2. Use the plan/order ids as idempotency keys (or a broker client order id derived
   from them); re-query before retrying after a timeout.
3. Reconcile broker positions/cash/fills against `bot/live/reconcile.py` before the
   next plan; a mismatch must block, not auto-correct.
4. Use raw prices and corporate actions, integer lot sizes, venue spreads, FX
   conversion, dividends and tax lots. The paper model is not a substitute.
5. Read credentials from the environment/keyring, never from a committed file.
6. Keep a hard kill switch and a maximum order notional independent of this config.

The CLI deliberately has no `--live` or `--broker` flag. Adding one is a separate
review with a real account, a small capital cap and a manual confirmation.

## Known limitations / open risks

- Paper fills are adjusted-price approximations; no real spread, borrow, locate,
  corporate action, dividend or tax lot accounting.
- Day-order partial residuals are not retried until the next rebalance.
- The 35% drawdown breaker and the 2.5x leverage are safety/research choices; the
  breaker is not walk-forward validated and changes the research path.
- The live loop is single-process (flock) and single-account. Separate books/markets
  have separate state files.
- The TECH and US stock universes have survivorship differences from the headline
  research; the report labels the market/book but does not fix the universe.
- FX is for reporting; it does not block a native plan if stale. Treat a stale
  `fx_date` as a data-quality warning in the EUR overlay.
- The existing forward paper book remains the only verified forward track (1/5
  dates). A live ledger needs its own five verified dates before any gate claim.
- No real broker adapter, no secrets, no live order path. That is intentional.
