# Agent orchestration

Orchestrator: main session. Base commit: `7db10cc` (master, clean).
Goal: improve real-life after-tax returns on both short and long horizons.

## Workstreams

| # | branch | worktree | owner | task | merge |
|---|---|---|---|---|---|
| 1 | `agent/forward` | `../stonks-wt-forward` | subagent | forward-log the core-only tax book next to the monthly TECH book; close-valid gate for both | 1 |
| 2 | `agent/pea-ucits` | `../stonks-wt-pea` | subagent | PEA-eligible UCITS semiconductor core at 1x; after-tax PEA vs CTO vs World | 2 |
| 3 | `agent/execution` | `../stonks-wt-exec` | subagent | limit-order fill model from daily high/low; cost/fill-rate evidence | 3 |
| 4 | `agent/risk` | `../stonks-wt-risk` | subagent | drawdown/tail improvement without overfitting; breadth/DD ladder/core mix | 5 (merged early) |
| 5 | `agent/brokers` | `../stonks-wt-brokers` | subagent | French broker matrix beyond Bourse Direct/Trade Republic; PEA eligibility; after-tax | 4 |

Merge order is deliberate: scripts first, new market second, execution engine third.
Risk was expected last because it could touch the engine; in practice it added only
`scripts/risk_v2.py` + `results/risk_v2.md`, so it merged early without conflicts.

## Rules for agents

- Work only in your worktree, on your branch. Never write `master`, never merge.
- The venv and cache are symlinked from `../stonks-bot`; do **not** run `--refresh`
  or `data.fetch_all(..., force=True)`. Read the cache only.
- Every claim needs a command output or a committed file under `results/`.
- Do not fabricate paper rows or forward dates.
- Commit in your branch with `git -c user.name=stonks-agent -c user.email=agent@local`.
  Do not push; the orchestrator pushes.
- Run `uv run python tests/test_core.py` before the final commit.
- Final report: branch, commit hash, files changed, tests, headline numbers, open risks.

## Acceptance

1. Forward: `scripts/paper_daily.py --book tax` logs the core-only book to separate
   CSVs; `paper_report.py --book tax` prints the live-vs-shadow gate; the 2026-11-25
   row stays reconciled, not fabricated. Existing TECH monthly path unchanged.
2. PEA: a concrete PEA-eligible UCITS ticker with Yahoo history; 1x core-only book
   under `tax_country="fr_pea"`, Trade Republic/Bourse Direct; after-tax terminal,
   FTT/FX/fees, CAGR, maxDD; compared with the CTO 2.5x SMH and a World PEA benchmark.
3. Execution: baseline next-open vs limit-at-open-minus-k bps using daily high/low;
   fill rate, missed-fill cost, final after-tax wealth for k in 5/10/20/30 bps; a clear
   recommendation, opt-in only.
4. Risk: 2-3 pre-registered rules, full-window and 2018/2022, bootstrap p5, maxDD,
   CAGR, after French tax/fees; reject anything that only helps one year or one path.
   Default config changes only if the evidence is unambiguous.

## GitHub

Private repo `iguanacucumber/stonks-bot`, one issue per workstream. Orchestrator pushes
`master` and the four agent branches, then merges after review.

## Status

- [x] forward — PR #6 merged at `0c26f1c`; tax-book automation `aut_217921c67001Ab2ef18eRqknWO` at 23:35 UTC
- [x] pea-ucits — PR #8 merged at `0993f87`; no pure semis UCITS is PEA-eligible; PUST 5.08x vs CTO 38.80x
- [x] execution — PR #10 merged at `896b401`; real core opens default; limit orders stay off
- [x] risk — PR #7 merged at `9ff23b7`; docs/scripts only, merged early because it does not touch the engine; verdict: keep defaults
- [x] brokers — PR #11 merged at `d04787f`; 8 presets, best monthly+IBKR 57.01x at 2 bps, FX caveat; PEA field spans 0.056x
- [x] merged and re-tested — master `d04787f`; `test_core`, `test_brokers`, `test_pea` pass; default CLI = TECH + monthly + IBKR
