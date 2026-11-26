# Feature matrix

| need | command | status | caveat |
|---|---|---|---|
| Research the default US book | `uv run python -m bot.cli backtest --country fr` | ready | paper/backtest; EUR ledger printed |
| Research TECH forward book | add `--market TECH` | ready | forward-validation only |
| Lower-drawdown profile | add `--profile balanced` | ready | post-hoc `paper` |
| Conservative 1.5x | `--profile monthly --leverage 1.5` | ready | lower leverage, same core |
| EUR tax + dividend ledger | `uv run python scripts/eur_ledger.py` | ready | cache-only dividends |
| Reproduce evaluation | `uv run python scripts/reproduce.py all --strict` | ready | needs 459 MB cache |
| Daily paper plan | `uv run python scripts/live.py plan --book monthly` | ready | dry-run |
| Paper ledger | `uv run python scripts/live.py run --book monthly --paper` | ready | pre-tax/average-cost |
| IBKR paper adapter | add `--broker ibkr-paper` | ready | DU paper account, gateway required |
| Investor questionnaire | `uv run python scripts/advise.py --interactive` | ready | not investment advice |
| Withdrawal projection | `uv run python scripts/advise.py --profile balanced ...` | ready | deterministic scenarios |
| Ops status/doctor | `uv run python scripts/ops.py status` / `doctor` | ready | paper state |
| Backup/restore | `uv run python scripts/ops.py backup --out DIR` | ready | local archive |
| Daily forward loop | `uv run python scripts/forward_daily.py --paper --tax` | ready | weekday automation 21:45 UTC |
| PIT NASDAQ tech universe | `uv run python scripts/research_pit_tech.py` | wave 4 | pending merge |
| Tax-loss harvesting | `uv run python scripts/tax_harvest.py` | wave 4 | pending merge |
| EUR/USD hedge study | `uv run python scripts/fx_hedge.py` | wave 4 | pending merge |
| Full tests | `for t in tests/test_*.py; do uv run python "$t"; done` | ready | no network |

## Read first

1. `docs/accounting.md` — native vs terminal-FX vs EUR ledger.
2. `docs/profiles.md` — max_growth / balanced / conservative.
3. `docs/live.md` — paper autopilot and rails.
4. `docs/ops.md` — runbook, backups, alerts.
5. `results/v2_summary.md` and `results/v3_summary.md` — evidence and verdicts.

## Non-negotiables

- No real orders, no keys in the repo.
- Backtests are not forward evidence; the TECH forward gate is 1/5.
- The 2.5x book can lose more than half; balanced is a risk preference, not a free lunch.
- Short-horizon trading was tested and rejected after costs.
