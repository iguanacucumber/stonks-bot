# Live autopilot (paper only)

`scripts/live.py` turns the monthly research book into an idempotent daily loop. It
writes no orders: the only execution adapter is the deterministic paper broker. Real
trading would require a new adapter behind `bot.live.broker.Broker` and an explicit
opt-in flag; neither exists here.

## Market choice

New live plans default to **US monthly**, the best real-life backtest:

| book | headline | EUR ledger | maxDD (EUR ledger) | status |
|---|---:|---:|---:|---|
| US monthly, IBKR 2 bps | 70.86x | 66.02x | -57.5% | recommended live market |
| TECH monthly, IBKR 2 bps | 57.01x | 52.89x | -66.0% | forward validation track |

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
