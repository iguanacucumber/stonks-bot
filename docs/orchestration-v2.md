# Orchestration v2 — from backtest to real-life autopilot

Baseline: `master` after `05a8cd9` (2026-11-26). Four orchestrator agents work in
parallel, each with its own branch/worktree and its own coding/research workers.
The main session owns integration, review and merges.

Mission: increase **after-tax, real-life** wealth at both short and long horizons
without hiding risk. A result that only exists in a backtest with no forward path is
not done.

## Current baseline (do not regress)

| Book | Multiple after tax | Max drawdown | Forward status |
|---|---:|---:|---|
| TECH monthly, IBKR, 2.5x | 57.0x | -67.8% | 1/5 verified dates |
| US monthly, IBKR, 2.5x | 70.9x | -63.2% | no forward book |
| TECH core-only tax | 50.9x | -68% | tax book logging |
| QQQ buy & hold | 5.38x | — | benchmark |
| PEA PUST | 5.08x | — | benchmark |

Known weaknesses to attack, not ignore: survivorship-biased TECH stock sleeve, huge
drawdown, single strategy, no production execution loop, no short-horizon strategy,
open forward gate, and no statistical correction for the number of experiments run.

## Non-negotiable research rules

1. **No look-ahead.** Signals use data available before the execution price. State the
   decision time and the fill time for every experiment.
2. **Costs and tax are part of the strategy.** Default French CTO: 31.4% PFU, broker
   fees, FX bps, 5% financing on gross leverage. A strategy that only wins before costs
   is a rejected strategy.
3. **Walk-forward or nothing.** Fit/choose on a training window, score on the next
   unseen window, roll. Report the concatenated out-of-sample path, not the best
   in-sample parameter.
4. **Pre-register.** Write the hypothesis and the pass/fail rule before running the
   sweep. Keep every attempted variant in the report, including failures.
5. **Multiple-testing honesty.** Report the number of trials and a deflated Sharpe or
   bootstrap p-value where it matters. A 5-year window that wins by luck is not edge.
6. **Survivorship.** Any stock-level universe must be point-in-time or explicitly
   flagged as biased. ETF-only results are preferred when the stock universe cannot be
   made point-in-time.
7. **No fabricated rows.** Paper/forward logs only contain dates that actually closed
   and were reconciled. Missing data is reported as missing.
8. **Reproduce.** Every headline number needs a committed script and a command line in
   `results/*.md`.

## Live status (2026-11-26)

| workstream | issue | branch | PR | state |
|---|---|---|---|---|
| A. Live Autopilot | #17 | `v2/live-autopilot` | — | running |
| B. Alpha Lab | #18 | `v2/alpha-lab` | — | running |
| C. Risk & Allocation | #19 | `v2/risk-alloc` | — | running (primitives committed) |
| D. Data & Evaluation | #20 | `v2/data-eval` | — | running |
| EUR ledger | #21 | `v3/eur-ledger` | #22 | merged at `b64130c` |

The main session reviews and merges; agents do not merge their own PRs.

## Workstreams

### A. Live Autopilot — branch `v2/live-autopilot`

Turn the monthly research engine into a safe daily production loop.

- `bot/live/` package: config, state store, signal snapshot, order intents, broker
  protocol, paper broker, reconciliation, health, notifications.
- `scripts/live.py` with `plan`, `run`, `status`, `reconcile` subcommands.
- Idempotent daily run: rerunning the same session never double-trades or double-logs.
- Dry-run is the default. A live broker adapter is opt-in and must fail closed.
- Safety rails: max gross, max turnover, stale-data block, drawdown circuit breaker,
  market-closed gate, duplicate-order guard.
- One command produces a human report: target vs current weights, orders, estimated
  cost, risk state, data freshness, gate status.
- Tests for idempotency, stale data, market holiday, partial fills, crash recovery.

### B. Alpha Lab — branch `v2/alpha-lab`

Find return sources that survive out-of-sample, for both horizons.

- Long horizon: cross-asset dual momentum / trend portfolio (SMH, QQQ, SPY, TLT, IEF,
  GLD, BIL, etc.), ETF-only and survivorship-free; compare after-tax to the current
  SMH 2.5x book on return, drawdown and 5-year windows.
- Long horizon: test whether the TECH stock sleeve adds anything after removing
  survivorship bias; if not, recommend core-only or a rules-based universe.
- Short horizon: daily/weekly mean reversion, momentum, overnight/close effects on
  liquid ETFs, with realistic spreads/slippage and borrow/financing. Reject loudly if
  there is no net edge. A small satellite allocation is acceptable only with a
  walk-forward win and a clear capacity/cost model.
- Build `bot/research/` harness: strategy spec, walk-forward runner, cost model,
  trial registry, report generator. Do not fork the accounting engine; reuse
  `bot/backtest.py` where possible.
- Deliver at least one new candidate config with an out-of-sample report, or a
  documented rejection with the evidence.

### C. Risk & Allocation — branch `v2/risk-alloc`

Make the path survivable without killing compounding.

- Volatility targeting and dynamic leverage: scale gross exposure by realized vol,
  with bands and hysteresis; test 1.5x/2x/2.5x/3x equivalents after tax.
- Drawdown control: pre-registered circuit breakers, trend filters, recovery rules.
  No rule that only helps one year.
- Tail hedge study: put spreads/collars/VIX-like proxies, cost drag vs drawdown
  reduction, after tax. Reject if the drag is not paid back in compounding.
- Multi-strategy allocator: combine core momentum, cross-asset trend and any Alpha Lab
  candidate with risk-parity/vol-parity weights; produce a "balanced" profile next to
  the "max growth" profile.
- Deliver `bot/risk/` with pure functions, `scripts/risk_v3.py`, and a report with
  full-window + 2018/2022 + bootstrap p5 + maxDD + CAGR after tax.

### D. Data & Evaluation — branch `v2/data-eval`

Make the evidence trustworthy and the repo reproducible.

- Point-in-time audit: document exactly where membership data comes from, which
  symbols are missing, and the survivorship impact. Build a repeatable audit report.
- Evaluation package `bot/evaluation/`: CAGR, after-tax multiple, maxDD, Calmar,
  Sortino, bootstrap confidence intervals, CSCV/probability of backtest overfitting,
  deflated Sharpe, 5-year window table, benchmark attribution.
- Walk-forward splitter and trial registry usable by the other three branches.
- CI: run all `tests/*.py` on push; cache-aware smoke test that does not hit Yahoo.
- Reproducibility: a single `scripts/reproduce.py` that regenerates headline tables
  from committed cache/config; document runtime and data snapshot date.
- Data quality: duplicate dates, gaps, splits/restatements, stale bars, symbol aliases;
  fail loudly and write a machine-readable report.

## File ownership (avoid merge conflicts)

Each branch owns new paths. Do not edit another branch's paths. Integration edits to
`bot/cli.py`, `bot/backtest.py`, `bot/strategy.py`, `bot/data.py`, `README.md` are done
by the main orchestrator after review, not by workstream branches, unless the
workstream explicitly needs a small adapter and says so in its PR.

| Branch | Owns |
|---|---|
| live | `bot/live/`, `scripts/live.py`, `tests/test_live.py`, `docs/live.md`, `results/live_*.md` |
| alpha | `bot/research/`, `scripts/research_*.py`, `tests/test_research.py`, `docs/research.md`, `results/alpha_*.md` |
| risk | `bot/risk/`, `scripts/risk_v3.py`, `tests/test_risk_v3.py`, `docs/risk_v3.md`, `results/risk_v3*.md` |
| eval | `bot/evaluation/`, `scripts/reproduce.py`, `tests/test_evaluation.py`, `.github/workflows/tests.yml`, `docs/evaluation.md`, `results/eval_*.md` |

Shared read-only: `cache/`, `bot/backtest.py`, `bot/strategy.py`, `bot/data.py`,
`bot/markets.py`, `bot/universe.py`. If a shared file must change, keep the diff
minimal, add a test, and flag it in the PR description.

## Worktree and git protocol

- Work only in your assigned worktree; never write `master`.
- Branch from `master` at `05a8cd9` or later. Commit with
  `git -c user.name=stonks-agent -c user.email=agent@local`.
- `.venv` and `cache` are symlinks to the main checkout. Use
  `../stonks-bot/.venv/bin/python` or `uv run` (it reuses the venv).
- Do not run `--refresh` or a full `fetch_all(force=True)`. New symbols may be fetched
  individually; never rewrite the whole cache.
- Run all relevant `tests/*.py` before the final commit.
- Push your branch and open a PR against `master` with:
  branch, commit, files, tests, headline numbers, reproduction commands, open risks.
- The main orchestrator reviews and merges. Do not merge your own PR.

## Worker-agent protocol

Each orchestrator may spawn coding/research workers. Workers inherit the same rules.
The orchestrator must:

1. Split work into disjoint file sets before spawning workers.
2. Give each worker a precise deliverable and acceptance test.
3. Review worker commits before merging them into the workstream branch.
4. Keep a short status file in `docs/` or the PR body.
5. Report back to the main session with branch, commit, PR number, tests, numbers.

## Definition of done for v2

1. Four PRs reviewed and merged with no regression in `tests/test_core.py`.
2. A single `python -m bot.cli live --dry-run`-style path that works end-to-end on the
   committed cache (or a documented `scripts/live.py` equivalent).
3. At least one candidate improvement with a walk-forward, after-tax, out-of-sample
   win, or a documented rejection with the same rigor.
4. A balanced/defensive profile with lower drawdown and a max-growth profile with the
   current or better return.
5. A forward plan: what runs daily, what alerts, what is still paper-only.
