# v2 status — 2026-11-26

## Shipped to master

- Baseline consolidated and re-tested: TECH 57.01x terminal-FX / 53.45x EUR ledger;
  US 70.86x terminal-FX / 66.72x EUR ledger; core-only 50.89x / 49.32x.
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
| #17 live autopilot | `v2/live-autopilot` | **merged #27 at `25bd661`**; pre-tax paper ledger, rails, state |
| #18 alpha lab | `v2/alpha-lab` | PR #29 open; reviewer fixes (gates, BIL, EUR tax) in progress |
| #19 risk & allocation | `v2/risk-alloc` | sweeps done; runner/tests/gate fixes in progress |
| #20 data & evaluation | `v2/data-eval` | **merged #28 at `f95fb1e`**; EUR tax baseline, CI, audits |

Each branch is in its own worktree; they merge after main-session review. Four
independent reviewer sessions are auditing idempotency, methodology, risk math and
metrics before merge.

## Key real-life findings so far

- The terminal-FX headline overstates the EUR result by ~7%; use the EUR ledger.
- US monthly dominates TECH on return, drawdown and 2022: 66.7x vs 53.5x, -56.4%
  vs -65.1%, 2022 -32.8% vs -51.4%.
- Cross-asset ETF dual momentum failed its pre-registered OOS gate (1x OOS 2.23x
  vs QQQ 3.15x); reject, despite lower drawdown.
- PIT S&P IT sleeve retains ~107% of the static-49 headline, so the TECH sleeve is
  not mainly a survivorship artifact; US all-PIT is still the better book.
- Short-horizon daily/weekly trading is rejected: best OOS close-to-close 2.64x vs
  QQQ 5.49x; overnight 1.01x (0.70x at 2x costs); no satellite allocation.

## Next

1. Review and merge #17-#20.
2. Integrate risk profiles (`max_growth`, `balanced`) and alpha verdicts.
3. Launch wave 3 (#23 dividends, #24 IBKR paper, #25 advisor, #26 ops).
