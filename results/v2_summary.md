# v2 summary — four workstreams merged

Date: 2026-11-26. Base before the wave: `05a8cd9`. Final master after the four
merges: see `git log --oneline`.

## Merged workstreams

| workstream | issue | branch | PR | merge commit |
|---|---|---|---|---|
| Live Autopilot | #17 | `v2/live-autopilot` | #27 | `25bd661` |
| Alpha Lab | #18 | `v2/alpha-lab` | #29 | `c7e9638` |
| Risk & Allocation | #19 | `v2/risk-alloc` | #30 | `ea0a83a` |
| Data & Evaluation | #20 | `v2/data-eval` | #28 | `f95fb1e` |
| EUR ledger (main session) | #21 | `v3/eur-ledger` | #22 | `b64130c` |

Each workstream had its own worktree/branch, worker agents, an independent
reviewer, and was merged only after tests and CI. The reviewers found and fixed:
overdue live fills, lost risk-cuts, sell-before-buy, gate-direction bugs, a flat
tax-rate bug in the EUR ledger, a spurious survivorship lower bound, and tests
that hit the cache/network in CI.

## Real-life result

€10k, 2015-01-02..2026-11-25, IBKR CTO, French tax, EUR ledger:

| book | native | terminal-FX | EUR ledger | CAGR | maxDD | 2018 | 2022 |
|---|---:|---:|---:|---:|---:|---:|---:|
| **US monthly 2.5x (default)** | 64.41x | 70.86x | **66.17x** | 42.3% | -56.9% | -31.1% | -33.7% |
| TECH monthly 2.5x (forward) | 51.82x | 57.01x | **52.98x** | 39.7% | -65.6% | -10.5% | -52.4% |
| CORE SMH only | 46.26x | 50.89x | **48.80x** | 38.7% | -64.5% | -31.4% | -55.0% |
| QQQ buy & hold | — | — | 4.65x | — | — | — | — |

The terminal-FX headline overstates the EUR-ledger result by ~6.6%. The default
was switched from TECH to US because US wins on return, drawdown and 2022; TECH
remains the forward-validation book.

### Profiles

| profile | allocation | EUR ledger | net | maxDD | 2022 |
|---|---|---:|---:|---:|---:|
| max_growth (default) | US monthly 2.5x | 66.17x | 61.64x* | -56.9% | -33.7% |
| balanced (paper) | 70% US book + 30% SPY | 36.56x | 34.36x* | -46.9% | -27.4% |
| conservative (paper) | US monthly 1.5x | 19.27x | 18.51x* | -40.4% | -20.3% |

`balanced` is runnable: `uv run python -m bot.cli backtest --country fr --profile balanced`.
It is post-hoc and paper-only; it gives up about half the terminal wealth for
~10 pp less drawdown.

## Research verdicts

- **Cross-asset ETF dual momentum: reject.** 1x OOS 2.23x headline / 1.97x EUR
  ledger vs QQQ 3.15x / 2.88x; maxDD -40.2% vs QQQ -35.1%. Lower drawdown, no
  return premium.
- **Short-horizon reversal/momentum/overnight: reject.** Best close-to-close OOS
  2.64x; at 2x costs 1.13x, below BIL 1.20x. Overnight 1.01x optimistic, 0.82x
  all-in, 0.60x doubled. No satellite allocation.
- **TECH sleeve survivorship: robust, not default.** The later PIT Nasdaq-100
  check retains **95%** of the static TECH EUR ledger (50.29x vs 52.98x), but US
  all-PIT is better on return and drawdown. TECH remains forward-only; PIT
  membership is `paper`. See `results/v4_summary.md`, `results/pit_tech.md`.
- **Risk rules: no max-growth pass.** Vol targeting, drawdown ladders, VIX/trend
  overlays, trend 250/300 and tail hedges all fail the return/drawdown/control
  gates. The 200-day trend stays. Balanced A6/V8 pass but are post-hoc `paper`.

## Live and evaluation

- `scripts/live.py` (`plan`, `run`, `status`, `reconcile`) is a dry-run default,
  idempotent paper loop with atomic state, sell-first fills, stale/holiday gates,
  duplicate/gross/turnover rails, a drawdown breaker, and a pre-tax average-cost
  ledger. No real broker, no secrets.
- `scripts/reproduce.py all --paths 10000 --strict` regenerates the baseline,
  bootstraps, windows, data audit and survivorship report from the committed
  cache. CI runs every `tests/test_*.py` without Yahoo.
- Data audit: 1,196 symbols, 3.5M rows, 0 hard failures, 196 warnings.
  Survivorship is an explicit sensitivity, not a lower bound.

## Tests (all pass on final master)

```bash
for t in tests/test_*.py; do uv run python "$t"; done
```

`test_core`, `test_data`, `test_brokers`, `test_pea`, `test_news_llm`,
`test_eur_ledger`, `test_live`, `test_evaluation`, `test_risk_v3`,
`test_research`, `test_profiles`.

## Remaining risks

- Static-49 TECH is survivor-biased; US PIT drops 16.2% of 2014+ members with no
  Yahoo history. Backtests are consistency checks, not proof.
- The EUR ledger models dividends as a scalar drag in risk work; a true dividend
  ledger is wave 3 (#23).
- Live paper ledger is pre-tax/average-cost, not the after-tax EUR ledger.
- 2.5x gap risk remains; -56% drawdowns are possible.
- No real broker adapter yet (wave 3 #24), no ops runbook yet (#26).

* The net column is the risk workstream scalar stress; the EUR-ledger column uses exact cached dividend events and is the real-life number.
