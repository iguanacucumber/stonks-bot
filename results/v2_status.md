# v2 status — 2026-11-26

## Shipped to master

- Baseline consolidated and re-tested: TECH 57.01x terminal-FX / 52.89x EUR ledger;
  US 70.86x terminal-FX / 66.02x EUR ledger; core-only 50.89x / 49.02x.
- `bot/eur_ledger.py` + `scripts/eur_ledger.py` + `results/eur_ledger.md` (PR #22).
- Default real-life book switched to US monthly; TECH stays the forward-validation
  book (`bot/france.py`, `tests/test_core.py`, READMEs).
- CLI `backtest`/`pnl` print the EUR ledger alongside terminal-FX.
- US forward paper preview row started (not close-valid; does not count toward gate).
- All master tests pass: `test_core`, `test_data`, `test_brokers`, `test_pea`,
  `test_news_llm`, `test_eur_ledger`.

## Four v2 workstreams (issues #17-#20)

| issue | branch | state |
|---|---|---|
| #17 live autopilot | `v2/live-autopilot` | code + tests committed (`bff38c6`), reviewer running |
| #18 alpha lab | `v2/alpha-lab` | cross-asset reject + sleeve report drafted; short study running |
| #19 risk & allocation | `v2/risk-alloc` | pre-registered; primitives committed; experiments running |
| #20 data & evaluation | `v2/data-eval` | package + reports committed; CI/currency finalizing |

Each branch is in its own worktree; they merge after main-session review. Four
independent reviewer sessions are auditing idempotency, methodology, risk math and
metrics before merge.

## Key real-life findings so far

- The terminal-FX headline overstates the EUR result by ~7%; use the EUR ledger.
- US monthly dominates TECH on return, drawdown and 2022: 66.0x vs 52.9x, -57.5%
  vs -66.0%, 2022 -33.3% vs -52.3%.
- Cross-asset ETF dual momentum failed its pre-registered OOS gate (1x OOS 2.23x
  vs QQQ 3.15x); reject, despite lower drawdown.
- PIT S&P IT sleeve retains ~107% of the static-49 headline, so the TECH sleeve is
  not mainly a survivorship artifact; US all-PIT is still the better book.
- Short-horizon daily/weekly trading remains unproven; the existing evidence says
  faster trading of the monthly book is a cost pump.

## Next

1. Review and merge #17-#20.
2. Integrate risk profiles (`max_growth`, `balanced`) and alpha verdicts.
3. Launch wave 3 (#23 dividends, #24 IBKR paper, #25 advisor, #26 ops).
