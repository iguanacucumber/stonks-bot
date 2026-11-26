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
| IBKR read-only inspection | add `--broker ibkr-paper --read-only` to `status`/`reconcile` | ready | GET-only; no submit/cancel/replace |
| Real-money approval preflight | `uv run python scripts/real_money_preflight.py --book monthly --market US` | blocked (paper-only) | read-only; exits 2, no adapter/credentials; see `results/real_money_approval.md` |
| Investor questionnaire | `uv run python scripts/advise.py --interactive` | ready | not investment advice |
| Withdrawal projection | `uv run python scripts/advise.py --profile balanced ...` | ready | deterministic scenarios |
| Ops status/doctor | `uv run python scripts/ops.py status` / `doctor` | ready | paper state |
| Backup/restore | `uv run python scripts/ops.py backup --out DIR` | ready | local archive |
| Daily forward loop | `uv run python scripts/forward_daily.py --paper --tax` | ready | weekday automation 21:45 UTC |
| PIT NASDAQ tech universe | `uv run python scripts/research_pit_tech.py` | ready | retains 95% of static; US still primary |
| Tax-loss harvesting | `uv run python scripts/tax_harvest.py` | rejected | deferral + fees; keep off |
| EUR/USD hedge study | `uv run python scripts/fx_hedge.py` | rejected | unhedged EUR path is better |
| US estate-tax calculator | `uv run python scripts/estate_tax.py --capital 660000` | ready | not tax/legal advice |
| UCITS core study | `uv run python scripts/research_ucits_core.py` | rejected | return gap > tax saving |
| PEA semis recheck | `uv run python scripts/pea_semis.py` | rejected | no pure semis PEA; PUST 5.77x |
| Covered-call study | `uv run python scripts/covered_calls.py` | paper opt-in | model-only; 40% IV conditional |
| End-to-end acceptance | `uv run python scripts/acceptance.py` | ready | 12/12 checks, temp state |
| Release acceptance | `uv run python scripts/release_check.py` | ready | writes `results/release_check.md`; forward gate WAIT |
| Dashboard | `uv run python scripts/dashboard.py` | ready | static HTML/MD |
| Assurance-vie study | `uv run python scripts/assurance_vie.py` | conditional | only if contract can hold exposure below break-even |
| Futures leverage | `uv run python scripts/research_futures.py` | rejected | worse after financing/roll |
| Full tests | `for t in tests/test_*.py; do uv run python "$t"; done` | ready | no network |

## Read first

1. `RELEASE.md` — release headline, verdicts, checklist and known risks.
2. `docs/accounting.md` — native vs terminal-FX vs EUR ledger.
3. `docs/profiles.md` — max_growth / balanced / conservative.
4. `docs/live.md` — paper autopilot and rails.
5. `docs/ops.md` — runbook, backups, alerts.
6. `results/v2_summary.md` and `results/v3_summary.md` — evidence and verdicts.

## Non-negotiables

- No real orders, no keys in the repo.
- Backtests are not forward evidence; the forward gate is US 0/5, TECH 1/5, tax 0/5.
- The 2.5x book can lose more than half; balanced is a risk preference, not a free lunch.
- Short-horizon trading was tested and rejected after costs.
