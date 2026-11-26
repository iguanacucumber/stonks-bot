# Roadmap v3 — after the four v2 workstreams

The v2 wave (issues #17–20) builds the production loop, alpha research, risk/allocation
and trustworthy evaluation. This file records the next backlog so the program keeps
moving instead of stopping after one merge.

## Wave 3 candidates

### 1. Multi-currency, real-tax ledger (done — PR #22)

Current model computes tax in the asset's native currency (USD) and translates the
terminal result at spot EUR/USD. A French investor is taxed on EUR gains at realization,
and FX gains/losses are part of the taxable base. This can move after-tax results
materially. Build a EUR-denominated ledger with per-lot EUR cost basis, FX conversion
fees, and annual PFU settlement. Acceptance: reproduce the current USD-native number as
a cross-check, then publish the EUR-ledger number and the difference.

Status: `bot/eur_ledger.py`, `scripts/eur_ledger.py`, `results/eur_ledger.md` merged.
TECH 57.01x terminal-FX -> 52.98x EUR ledger; US 70.86x -> 66.17x. Remaining work:
dividend/withholding tax and CLI integration.

### 2. Dividend and withholding-tax drag (done — v3/dividends)

Adjusted closes reinvest gross dividends. The model ignores PFU on dividends, the
15% US treaty withholding, and the French credit. For SMH the drag is small; for
TLT/GLD/SPY/cross-asset books it is not. `bot/dividends.py` stores Yahoo
`events=div` under `cache/dividends/`; `bot/eur_ledger.py` can replay the schedule
with withholding, the year-dependent PFU credit and an optional cash mode.
`results/dividends.md` documents the small equity drag and the ~1%/yr high-yield
bond drag. Acceptance: SMH/TECH/US move only a little; the bond-heavy example is
re-evaluated with the drag.

### 3. Real broker adapter (IBKR paper first)

Implement the `Broker` protocol from the Live Autopilot branch with an IBKR paper
adapter (ib_insync or the Web API), order state machine, retries, partial fills,
cancel/replace, and reconciliation. Keep secrets out of the repo. Acceptance: one
end-to-end paper order cycle against IBKR paper, with a recorded reconciliation.

### 4. Robo-advisor / investor profiles

Translate the research into a user-facing allocation: risk questionnaire, horizon,
capital, wrapper (CTO/PEA), broker, max drawdown tolerance. Output a target portfolio,
expected range, tax estimate, and the exact order list. Acceptance: three profiles
(conservative/balanced/aggressive) with after-tax backtests and a one-page decision tree.

### 5. Ops and monitoring

Daily health dashboard, alerting (email/Telegram), backup/restore of state, systemd
unit, log rotation, and a runbook for data outages, broker rejects, and drawdown
breaches. Acceptance: a simulated failure drill (stale data, duplicate order, broker
down) with alerts and recovery steps.

### 6. Point-in-time NASDAQ tech universe

The TECH sleeve is a static 49-name list chosen today. Build a point-in-time
NASDAQ-100/semiconductor membership table (annual or quarterly snapshots) or replace
the sleeve with a rules-based liquid universe. Acceptance: re-run the TECH book with
the point-in-time universe and report the survivorship delta.

### 7. Options / volatility risk premium satellite

Only if the data and cost model are credible: covered calls or put spreads on the core
ETF, sized as a small satellite. Pre-register, model spreads and assignment, after tax.
Acceptance: walk-forward after-tax win or a documented rejection.

### 8. Withdrawal and lifecycle planning

For real-life use: accumulation vs decumulation, safe withdrawal rate, tax-aware
selling order, PEA/CTO sequencing, and a 15-year projection with inflation. Acceptance:
a deterministic withdrawal simulator with tax and a report, no return promises.

## Rules

- One issue per workstream, one branch/worktree per orchestrator.
- No default change without the research protocol (`docs/research-protocol.md`).
- Every wave ends with a merged PR, a test run, and an updated README/decision tree.
- Keep four orchestrators active; when one merges, launch the next backlog item.

## Backlog issues

- #23 dividends and withholding tax
- #24 IBKR paper broker adapter
- #25 investor profiles and withdrawal simulator
- #26 ops dashboard, backups, alerts, runbook


## Completed wave 3 (2026-11-26)

- #22 EUR-native ledger: merged.
- #23 dividends/withholding: merged #34; exact ledger US 66.17x / TECH 52.98x / CORE 48.80x.
- #24 IBKR paper adapter: merged #33; DU paper only, fake-transport evidence.
- #25 investor profiles + withdrawal: merged #32; `scripts/advise.py`.
- #26 ops/backups/alerts/runbook: merged #31; `scripts/ops.py`.

## Active wave 4

- #35 point-in-time NASDAQ tech universe
- #36 tax-loss harvesting
- #37 forward paper loop
- #38 EUR/USD hedge study
