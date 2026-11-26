# v10 release/README audit — claims vs sources

Base `dd2890e`, branch `v10/review-release`. Data snapshot 2026-11-25. This audit
checks the user-facing claims against the committed reports; it does not create a
tag or a release.

## Corrected claims

| claim / location | source of truth | audit finding | fix |
|---|---|---|---|
| README warning: US loses **56.4%**, 2022 **-32.8%** | `results/eur_ledger.md`: -56.9%, -33.7% | stale/wrong | corrected to 56.9% / 33.7% |
| README broker table: CORE **-64.2%**, 2022 **-54.2%** | `results/eur_ledger.md`: -64.5%, -55.0% | wrong | corrected |
| README risk section: bootstrap US p5 **2.58x**, median **64.4x** | `results/v4_summary.md`: p5 **2.53x**; `results/risk_v3.md` median is for the 61.64x net-dividend baseline | unsupported/conflicting | p5 corrected to 2.53x; unsourced median removed |
| README/English survivorship: **~18%** missing 2015-2026 members | `results/eval_survivorship.md`: **16.2%** of 2014+ members | stale | corrected to 16.2% |
| English PEA: PUST **5.08x**, LQQ **14.4x** | `results/pea_semis.md`: PUST **5.77x**, LQQ **13.73x** | stale v5 numbers | corrected; older 5.08 was the 200d-trend variant, not current buy-and-hold |
| RELEASE: short3 "not merged / pending" | `results/short3.md` exists; `results/release_check.md` short3 PASS verdict=reject | stale | corrected to merged, reject |
| RELEASE: base `master`/`v8/release` | branch base `dd2890e` | stale | corrected |
| RELEASE covered calls: maxDD **-56.4%** vs 66.17x | `results/eur_ledger.md`: -56.9% | wrong comparator | corrected to -56.9% |
| v2 summary: PIT retains **107%**, 57.90x | `results/v4_summary.md`, `results/pit_tech.md`: **95%**, 50.29x | stale pre-v4 claim | corrected/linked to v4 |
| v3 summary: "US just starting" | `results/forward_gate.md`: US **0/5**, TECH 1/5, tax 0/5 | vague | corrected to explicit gate |
| v5/v6 summary covered calls: **-56.4%** | current USD/A EUR baseline -56.9% | wrong comparator | corrected |
| v6 summary: "All 27 tests" | current suite **34** (`results/release_check.md`) | stale count | replaced with current command/no hard count |
| docs/accounting: base EUR ledger "still treats dividends as gross" | `results/eur_ledger.md` includes cached dividend events | contradictory caveat | rewritten to distinguish with/without a dividend schedule |
| docs/feature_matrix: gate US/TECH only | `results/forward_gate.md`: tax 0/5 too | incomplete | added tax 0/5 and read-only IBKR row |
| docs/research H3: PIT 107% | v4 PIT 95% | stale | corrected, superseded by H5 |
| README.en: "No broker connection" | optional IBKR paper/read-only adapter exists | overbroad safety claim | narrowed to "no real broker connection; optional DU paper/read-only" |
| README.en legacy table: $2,191,762 / -60.7% / 57.5% CAGR | `results/summary.md`, `results/leverage.md` | source-correct but pre-tax USD, easily confused with 66.17x EUR ledger | section relabelled "legacy gross USD backtest (pre-tax; not the EUR ledger)" |

## Checked and left unchanged

| claim | source | result |
|---|---|---|
| US **66.17x**, TECH **52.98x**, CORE **48.80x** EUR ledger | `results/eur_ledger.md`, `results/dividends.md` | correct and consistently labelled EUR ledger |
| US €661,655 on €10k | `docs/accounting.md`, `results/eur_ledger.md` | correct |
| US maxDD -56.9%, 2022 -33.7%, 2018 -31.1% | `results/eur_ledger.md` | correct |
| TECH maxDD -65.6%, 2022 -52.4%, 2018 -10.5% | `results/eur_ledger.md` | correct |
| CORE maxDD -64.5%, 2022 -55.0% | `results/eur_ledger.md` | correct |
| Profiles max_growth 66.17x / balanced 36.56x / conservative 19.27x | `docs/profiles.md`, `results/v3_summary.md` | correct; both opt-ins labelled post-hoc `paper` |
| Profile commands (`--profile balanced`, `--leverage 1.5`) | `bot/cli.py`, `bot/profiles.py` | commands exist and are the documented paths |
| Forward gate US **0/5**, TECH **1/5**, tax **0/5**; next 2026-11-27 | `results/forward_gate.md` | correct; no "validated" claim |
| Short-horizon families rejected; no satellite | `results/short_horizon_conclusion.md`, `results/short3.md`, `results/short_daily.md` | correct |
| QQQ after tax 5.38x legacy table | `results/france.md`; current cache `results/eval_headline.md` 5.41x | table is legacy-consistent; not a current EUR-ledger claim |
| PIT 95%, 50.29x | `results/pit_tech.md`, `results/v4_summary.md` | correct |
| US PIT drops 16.2% | `results/eval_survivorship.md` | correct |
| No real orders / no keys | `results/live_demo.md`, `docs/live.md`, `RELEASE.md` | correct; optional DU paper/read-only only |

## Evidence refresh

`uv run python scripts/release_check.py` on the reviewed tree:

- **PASS** overall; 10 PASS + 1 WAIT (forward gate) + 0 FAIL.
- Tests **34/34** pass; acceptance **12/12**.
- EUR ledger: TECH 52.98x, CORE 48.80x, US 66.17x.
- Forward gate: monthly/TECH WAIT 1/5, monthly/US WAIT 0/5, tax/TECH WAIT 0/5;
  next 2026-11-27.
- short3 PASS, verdict=reject.
- No secrets found in 328 tracked text files.

`results/release_check.md` is the machine-readable copy of that run.

## Tag / date / commit

- `v1.1.0-paper` points at `add46c7` (v8 final), not this v10 audit branch.
- `v1.0.0-paper` is older. No new tag is created by this branch.
- Data snapshot 2026-11-25; release check generated 2026-11-26.
- This audit does not merge or tag; the main session owns release actions.

## Remaining risks (not fixed by wording)

- **Survivorship:** static-49 TECH is chosen today; US PIT drops 16.2% of 2014+
  members with no Yahoo history. Backtests are consistency checks, not proof.
- **Estate:** US-situs exposure and French inheritance tax remain; treaty filings
  (706-NA/8833) are required and this is not tax/legal advice. See
  `results/estate_tax.md`.
- **Paper-only:** the live loop and IBKR adapter are paper/DU; no real-money order
  path, no keys, no production broker.
- **Forward evidence:** 0/5 US, 1/5 TECH, 0/5 tax. No book has five verified
  dates; do not quote the backtest as forward performance.
- **Drawdown:** -56.9% US is a real historical path; gap risk can be worse.
- **Dividends:** cached events, not a custodian statement; bond/cash sleeves are
  more sensitive than SMH.
- **CI:** this audit has not run GitHub Actions; local release check and tests pass.
