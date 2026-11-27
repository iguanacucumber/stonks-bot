# v8 / v1.1 release — paper-only systematic book

**Status:** paper release candidate. The main session merges, tags and creates the
GitHub release; this branch only prepares the acceptance evidence and documentation.
This is **not** a claim of a proven forward edge: the gate is still WAIT.

## v12 production simulation boundary

`scripts/prod_sim.py` adds a long-lived **simulation** of the real-money service:
replay/live-paper clock, deterministic cached-bar ticks, the monthly book at
session close -> next tick/open, simulated exchange/ledger/OMS, durable store,
HTTP API and systemd deployment. It is simulated money only: no real broker
adapter, no credentials, no live order path. The API binds to localhost by
default. Scenarios and the 10k-tick/1k-order load check run in-process with no
network. See `results/prod_sim.md`, `docs/prod_sim_contract.md` and
`deploy/prodsim/RUNBOOK.md`.

**Data snapshot:** 2026-11-25, v10 review base `dd2890e`. No real orders, no broker
keys, no network calls in the release check.

## Headline: what a French CTO keeps

€10,000, 2015-01-02 to 2026-11-25, Interactive Brokers CTO, 2.5x gross, French PFU
and the **EUR ledger** (annual EUR tax, trade-date cost basis, exit tax):

| book | native | terminal-FX (legacy) | **EUR ledger** | CAGR | maxDD | 2022 |
|---|---:|---:|---:|---:|---:|---:|
| **US monthly 2.5x (default)** | 64.41x | 70.86x | **66.69x** | 42.4% | **-56.4%** | **-33.3%** |
| TECH monthly 2.5x (forward track) | 51.82x | 57.01x | **53.42x** | 39.7% | -65.2% | -51.9% |
| CORE SMH only | 46.26x | 50.89x | **49.27x** | 38.7% | -64.1% | -54.6% |

The terminal-FX column is the older headline method. It is an upper bound: it ignores
annual tax on FX moves and each lot's EUR basis. **Quote 66.69x only as the EUR-ledger
backtest number.** It is not a forward result and not a promise. Sources:
`results/v3_summary.md`, `results/eval_baseline.md`, `results/eur_ledger.md`.

## Profiles and commands

| profile | allocation | EUR ledger | maxDD | 2022 | command |
|---|---|---:|---:|---:|---|
| **max_growth** (default) | US monthly 2.5x | **66.69x** | -56.4% | -33.3% | `uv run python -m bot.cli backtest --country fr` |
| balanced (paper opt-in) | 70% US book + 30% SPY | 36.56x | -46.9% | -27.4% | `uv run python -m bot.cli backtest --country fr --profile balanced` |
| conservative (paper opt-in) | US monthly 1.5x | 19.27x | -40.4% | -20.3% | `uv run python -m bot.cli backtest --country fr --profile monthly --leverage 1.5` |

`max_growth` is the highest terminal wealth but can lose more than half. Balanced and
conservative are **post-hoc `paper` opt-ins**, not forward-validated. Details:
`docs/profiles.md`, `results/v3_summary.md`.

## Paper, backtest and forward evidence are different

| layer | what it is | current status |
|---|---|---|
| EUR-ledger backtest | historical simulation with French tax/FX/fees | 66.69x US, 53.42x TECH |
| live paper loop | `scripts/live.py` / `scripts/forward_daily.py`, atomic state, no real broker | running; pre-tax average-cost ledger |
| forward gate | verified live/shadow dates in `results/paper_audit.csv` | **WAIT: US 0/5, TECH 1/5, tax 0/5; next required 2026-11-27 — no forward edge proven** |
| IBKR adapter | Client Portal **paper** (DU) adapter plus read-only reconciliation | opt-in; no live-money write path |

A backtest is a consistency check, not proof. A forward gate below 5/5 must never be
presented as evidence of forward returns. The live paper ledger is not the after-tax
EUR ledger (it is pre-tax and average-cost). `results/forward_gate.md` is the current
gate report; WAIT is the honest state, BLOCKED would be a release failure.

## What is not done

- **Forward gate is 0/5 US, 1/5 TECH, 0/5 tax.** No book has five verified dates.
- **Short3 report is merged and rejected.** `results/short3.md` is present on this
  base and the release check records `short3 verdict=reject`; no short satellite.
- **No real-money broker path; live access is not approved.** IBKR remains a
  paper/DU adapter with read-only reconciliation; there is no production order
  adapter, live key or account connection. The v11 approval/preflight gate is
  present but paper-safe: `scripts/real_money_preflight.py` is read-only and
  always fails closed at `real_money_execution_path`. The user decision on
  2026-11-26 was to keep the bot paper-only; no credentials were requested.
- **Alerts/backups need operator configuration.** Delivery endpoints and off-site
  retention are not provisioned here.
- **Opt-in studies are not defaults.** Balanced/conservative, covered calls,
  assurance-vie, UCITS/PEA and futures remain paper/conditional or rejected.

## Research verdicts at release

- **Short horizon: reject.** Weekly/daily rebalancing, 1/5-day reversal, 20-day momentum
  and overnight close-to-open were tested walk-forward with costs and PFU. Best
  close-to-close OOS 2.64x after tax vs QQQ 5.49x; at 2x costs 1.13x, below BIL 1.20x.
  No short satellite. `results/alpha_short.md`.
- **Short III: reject (v8/short3).** The best 1-hour row (SPY gap-fade) made 1.04x
  after tax over two years vs QQQ 1.26x; at 2x costs it falls to 0.92x. The 5-minute
  sample is only ~60 days and is execution-quality, not evidence; daily gap-fade and
  cross-sectional reversal are below QQQ after tax. No satellite. The report is
  `results/short3.md`.
- **Earnings events: reject (v11).** 8-K item 2.02 PEAD has real event alpha
  (+0.73% per five sessions, placebo p=0.002, DSR 0.950) but only 2.73x after tax
  vs QQQ 5.94x (2.45x at 2x costs). Intraday earnings continuation/fade made 1.149x
  vs QQQ 1.170x. Both fail the QQQ/BIL hurdle; no satellite. `results/pead.md`,
  `results/earn_intraday.md`.
- **PIT Nasdaq-100: paper robustness, not a replacement.** EUR ledger 50.29x vs 53.42x
  static; retains ~95% of the static TECH terminal. US all-PIT remains the default.
  `results/pit_tech.md`.
- **Tax-loss harvesting: reject.** Mainly tax deferral plus round-trip costs; all
  variants lose to baseline. `results/tax_harvest.md`.
- **EUR/USD hedge: reject.** 100% hedge lowers US to 45.38x and deepens maxDD to -67.0%;
  USD was a 2022 cushion. `results/fx_hedge.md`.
- **UCITS core: reject as a replacement.** Irish SMH.L/SEMI.AS retained 40-46% of the US
  SMH return in the common window; the return gap exceeds the tax/estate benefit.
  `results/ucits_core.md`.
- **PEA: no pure semiconductor fund.** SMH.PA/SEMI.PA/CHIP.PA are not PEA-eligible;
  PUST.PA is broad Nasdaq-100, 5.77x after social vs 66.69x CTO. `results/pea_semis.md`.
- **US estate tax: treaty/planning, not a return sacrifice.** At €660k/2.5x the
  France-US treaty credit removes US estate tax in the worked examples, but 706-NA/8833
  filings and French inheritance tax remain; keep US-situs at/below $60k only as
  treaty-failure insurance. `results/estate_tax.md`, `docs/estate_tax.md`.
- **Futures leverage: reject.** ES/NQ 2.5x lose to ETF+margin after financing/roll/tail;
  futures won 0/88 five-year windows. `results/futures.md`.
- **Covered calls: paper income opt-in only.** The 40% IV / 10% OTM model is 75.96x vs
  66.69x and maxDD -48.5% vs -56.4%, but option quotes, assignment and the volatility
  smile are not observed. Not default. `results/covered_calls.md`.
- **Assurance-vie: conditional opt-in only.** Tax-age modelling can show 126.72x at 0.50%
  fees vs 66.69x CTO, but a standard contract cannot borrow; only use if the contract
  can hold the same exposure below the ~6.18% break-even fee. `results/assurance_vie.md`.

## New-user quick start

```bash
uv sync
uv run python -m bot.cli backtest --country fr --capital 10000 --random-seeds 0
uv run python -m bot.cli backtest --country fr --profile balanced
uv run python scripts/acceptance.py
uv run python scripts/reproduce.py baseline --no-write
uv run python scripts/live.py plan --book monthly --market US --as-of 2026-11-25
uv run python scripts/dashboard.py --check
uv run python scripts/ops.py doctor
for t in tests/test_*.py; do uv run python "$t"; done
```

`uv run python -m bot.cli ingest` refreshes data and needs network/Yahoo; the release
acceptance above uses the committed cache and never fetches. No command sends a real
order. The paper live loop is:

```bash
uv run python scripts/live.py run --book monthly --market US --paper
uv run python scripts/forward_daily.py --paper --tax
```

## File map

| path | purpose |
|---|---|
| `README.md` / `README.en.md` | user-facing summary and quick start |
| `RELEASE.md` | this release acceptance, verdicts and checklist |
| `docs/feature_matrix.md` | command/status matrix |
| `docs/accounting.md` | native vs terminal-FX vs EUR ledger |
| `docs/profiles.md` | max_growth / balanced / conservative |
| `docs/live.md`, `docs/forward.md` | paper loop, gates, scheduler |
| `docs/ops.md`, `docs/dashboard.md` | operations and static cockpit |
| `results/v2_summary.md` … `v6_summary.md` | wave evidence and decisions |
| `results/forward_gate.md` | current US/TECH/tax WAIT gate and next required session |
| `results/short3.md` | v8 intraday/cross-section short study verdict (reject) |
| `results/release_check.md` | regenerated release acceptance table |
| `scripts/acceptance.py`, `scripts/release_check.py` | end-to-end and release checks |
| `scripts/reproduce.py` | baseline/EUR-ledger reproduction |
| `scripts/live.py`, `scripts/forward_daily.py` | paper-only planner/logger |
| `bot/live/approval.py`, `bot/live/preflight.py` | approval artifact and read-only real-money readiness gate |
| `scripts/real_money_preflight.py` | paper-safe preflight; always blocked in this build |
| `results/real_money_approval.md` | current approval/preflight status and blockers |
| `bot/live/`, `bot/ops/`, `bot/dashboard/` | state, execution, ops, dashboard |
| `tests/test_*.py` | synthetic/no-network test suite |

## Release checklist

- [x] `uv run python scripts/release_check.py` passes (tests, acceptance, reproduce,
  plans, gate, dashboard, no-secrets).
- [ ] CI green on the merge commit; no open PRs or release-blocking issues.
- [ ] Commit, tag, release notes and GitHub release created by the **main session**
  after merge; this branch does not tag.
- [x] Data snapshot 2026-11-25 and cache checksum/retention recorded.
- [x] Forward gate remains honest: **WAIT US 0/5, TECH 1/5, tax 0/5**; next required
  2026-11-27; no forward edge claimed.
- [x] Short3 report/verdict reviewed: `results/short3.md` is present; verdict is
  **reject** and release_check records PASS.
- [x] Paper-only boundary re-checked: no real broker credentials, no `.env`, no
  orders; `scripts/real_money_preflight.py` exits `2` with explicit blockers and
  the user did not approve live access.
- [x] Real-money approval/preflight gate present, read-only, fail-closed and covered
  by fake-env/no-network tests (`results/real_money_approval.md`).
- [x] README/RELEASE headline numbers and source links reviewed
  (`results/review_release.md`).
- [x] Known risks communicated: survivorship, -56.4% drawdown, pre-tax paper ledger,
  IBKR paper only, no assurance-vie/futures/UCITS switch.

## Known risks

- **Forward evidence is thin.** US 0/5, TECH 1/5, tax 0/5; next required 2026-11-27;
  no forward edge. Short3 is merged and rejected (`results/short3.md`).
- **Survivorship.** Static-49 TECH is chosen today; US PIT drops 16.2% of 2014+ members
  with no Yahoo history. Treat stock-sleeve alpha as unproven.
- **Drawdown.** The 2.5x growth book lost 56.9% peak-to-trough in the EUR ledger and
  33.7% in 2022. Gap risk can be worse than the backtest.
- **Accounting.** The live paper ledger is pre-tax/average-cost; the EUR ledger is a
  research replay. Dividends are cached events, not a full custodian statement.
- **Execution.** The only broker path is a paper adapter; a real adapter must add
  reconciliation, rejects, partial fills and secrets management. The v11
  approval/preflight gate does not add one and must keep failing closed.
- **Real money is not available.** There is no live order path, approval artifact or
  credential. The preflight gate is a readiness report, not a trading switch.
- **Alerts/ops.** Alert delivery needs operator secrets and a real endpoint test;
  backup retention/off-site policy is not included.
- **High-churn docs.** Summaries and dashboard outputs are snapshots; regenerate or
  re-check before quoting a later data vintage.

*Generated by the v8 / v1.1 release workstream. No real orders were sent; no tag or
GitHub release is created here.*
