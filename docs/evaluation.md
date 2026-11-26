# Evaluation package: reproducible evidence, not a single best path

Workstream D (`v2/data-eval`) owns the measurement layer. It does not change a
strategy default. Its job is to make every headline number reproducible, label the
accounting currency honestly, and keep the number of trials visible.

Data snapshot: **2026-11-25**. Code baseline: `master` after PR #22 (EUR ledger).
All commands below read the committed cache and never call Yahoo.

## Quick start

```bash
uv run python scripts/reproduce.py            # four baseline books, three modes
uv run python scripts/reproduce.py headline   # TECH deep dive + statistics
uv run python scripts/reproduce.py audit      # data-quality audit (read-only)
uv run python scripts/reproduce.py survivorship
uv run python scripts/reproduce.py all
```

`baseline` takes about 5 seconds on the committed cache; `headline` adds two
10,000-path bootstraps and the 5-year rolling table. The default command is
`baseline`, not the old single-book headline. Strict mode is the default for both
`baseline` and `headline`: a cache mismatch exits non-zero. Use `--no-strict` only
for an explicitly informational run.

## Currency dimension: native, terminal-FX, EUR ledger

The engine keeps signals and share quantities in the asset currency (USD for
US/TECH). A French investor's tax base and wealth are EUR. Three numbers are
therefore reported separately and must not be mixed:

| mode | meaning | use |
|---|---|---|
| `native` | after-tax result in the asset currency | research/engine comparisons |
| `terminal_fx` | native after-tax × spot EUR/USD at the end | legacy continuity only |
| `eur_ledger` | replay every trade in EUR at trade-date FX, HIFO lots, annual PFU, exit tax | **real-life French CTO result** |

`terminal_fx` ignores annual tax on FX moves and each lot's trade-date EUR cost
basis. It is an upper bound, **not** the real-life result. `bot/evaluation/currency.py`
computes the three rows; `scripts/reproduce.py` prints them and `reproduce.py
baseline` writes the table to `results/eval_baseline.md`.

The EUR ledger is not duplicated here: `bot/evaluation/currency.py` imports
`bot.eur_ledger` (merged from PR #22) and calls `replay()`. If the module is absent,
the package reports the mode as unavailable rather than silently falling back to the
terminal-FX number.

### Baseline, €10k IBKR CTO, 2015-01-02..2026-11-25

| book | native USD | terminal-FX | EUR ledger | CAGR (ledger) | maxDD (ledger) |
|---|---:|---:|---:|---:|---:|
| TECH monthly | 51.82x | 57.01x | **53.45x** | 39.8% | -65.1% |
| TECH core-only tax | 46.26x | 50.89x | **49.32x** | 38.9% | -63.9% |
| US monthly | 64.41x | 70.86x | **66.72x** | 42.4% | -56.4% |

The terminal-FX headline overstates the real-life EUR result by roughly 6.7% on the
TECH monthly book and 6.2% on US monthly (year-dependent PFU: 30% through 2025,
31.4% from 2026). Ranking is unchanged: US monthly leads on return and drawdown; the
core-only book earns less but trades far less. `results/eval_baseline.md` is the
generated table with fees, tax paid and exit tax.

## Modules

| module | contents |
|---|---|
| `bot/evaluation/metrics.py` | CAGR, multiple, vol, Sharpe, Sortino, maxDD, Calmar, hit rate, beta/alpha, after-tax helpers |
| `bot/evaluation/currency.py` | native/terminal-FX/EUR-ledger summaries and the `accounting_mode` labels |
| `bot/evaluation/bootstrap.py` | stationary and circular-block bootstrap of terminal multiple and maxDD; optional terminal-gain tax approximation |
| `bot/evaluation/cscv.py` | CSCV probability of backtest overfitting and deflated Sharpe ratio |
| `bot/evaluation/windows.py` | rolling 5-year window table on one path (use `scripts/robustness_best.py` for start-date reruns) |
| `bot/evaluation/walkforward.py` | rolling/expanding train-validate-test splits with a leakage guard and OOS concatenation |
| `bot/evaluation/attribution.py` | return-based benchmark attribution, exposure and cost/tax decomposition |
| `bot/evaluation/trials.py` | CSV trial registry with pre-registration, verdicts and `accounting_mode` |
| `bot/evaluation/dataquality.py` | read-only bar/panel/membership/alias audit, JSON + Markdown output |
| `bot/evaluation/survivorship.py` | static TECH-49 bias sensitivity and US point-in-time coverage |

All functions are pure except the explicitly named report writers. Tests use
synthetic fixtures, so `tests/test_evaluation.py` needs neither cache nor network.

## Trial registry

`bot/evaluation/trials.py` follows `docs/research-protocol.md` and adds
`accounting_mode` plus operational columns (`status`, `commit`, `source`).
`accounting_mode` is one of `native`, `terminal_fx`, `eur_ledger` (or empty for an
imported historical row whose mode is unknown). New `preregister()` calls default to
`eur_ledger`, so a result cannot be mistaken for the terminal-FX headline. CSV
columns are:

```
id, date, branch, hypothesis, universe, start, end, params_json, n_trials,
metric, value, oos_value, cost_model, accounting_mode, verdict, notes,
pass_rule, status, commit, source
```

`results/eval_trials.csv` is the branch-local snapshot. It imports the historical
v1/v3 experiments with their original accounting mode and appends the four baseline
rows reproduced by `scripts/reproduce.py baseline` in `eur_ledger` mode. Those
baseline rows are explicitly labelled "reporting view, not an independent strategy
trial" so they do not inflate the multiple-testing count for a strategy sweep.

## Statistics and honesty

- Bootstrap defaults to 10,000 stationary paths (21-day mean block) plus the
  circular-block scheme used by the older reports. The optional **exit-tax
  overlay** applies the path's effective final rate to gains above 1.0 and does
  not refund losses. The returns already include realised taxes paid along the
  path, so this is not a full after-tax path and is not comparable to the EUR
  ledger; the report labels it `exit_tax_overlay_p5`.
- `cscv_pbo` needs a T×N matrix of candidate returns. `deflated_sharpe` uses the
  registry's strategy-trial count as N. Those trials are **not independent** (same
  price path, overlapping variants, different families), and the cross-trial
  dispersion is estimated from the selected path when a trial matrix is absent.
  The DSR p-value is indicative, not definitive; use CSCV when per-trial returns
  exist.
- CAGR uses `years = (n_days - 1) / 252` in `bot/evaluation` (the number of daily
  returns). `bot/france.after_tax` and `bot/eur_ledger` retain the repo's older
  `n_days / 252` convention; the difference is one trading day and is explicit in
  the reports. Multiples and drawdowns are unaffected.
- Five-year windows in `headline` are **path slices** of one full run. They are
  cheap and comparable, but a start-date rerun with fresh tax lots remains the gold
  standard (`results/robustness_best.md`).
- No number here is a forward return promise. The forward paper gate remains the
  only uncontaminated validation.

## Data-quality audit

`uv run python scripts/reproduce.py audit` reads `cache/bars`, the panel and the
membership table, and writes:

- `results/eval_data_quality.json` — machine-readable hard failures, warnings and
  info entries (generated, ignored by git);
- `results/eval_data_quality.md` — committed summary.

Hard failures block the headline: duplicate dates, missing required columns,
non-positive close/adjusted close, alias cycles, duplicate panel rows, duplicate
membership snapshots. Structural OHLC scars on the long tail of foreign/delisted
bars are warnings unless the symbol is required by the headline, so one bad
foreign ticker cannot hide the rest of the cache. The 2026-11-25 snapshot is
**PASS for required symbols** with 0 hard failures and 196 non-blocking warnings
(122 OHLC scars, 40 split-like anomalies, 21 stale delisted/renamed symbols,
5 gaps, 2 alias self-maps); the report does not claim the whole cache is clean.

## Survivorship

The TECH sleeve is a hand-written list of today's NASDAQ winners, applied as one
2014-01-01 snapshot. The audit measures what can be measured from the committed
cache:

- 28/49 TECH names were S&P 500 members at the 2015 start, 15 joined later, 6 were
  never members;
- the S&P-dated sensitivity drops the terminal-FX multiple from 57.01x to 23.22x
  (EUR ledger: 53.45x to the variant's ledger number in the report). This is a
  **non-nested sensitivity, not a lower bound**: it removes non-S&P names and
  starts others at their index addition, so it is not the static-49 counterfactual;
- the US point-in-time book still misses 16.2% of its 2014+ members because Yahoo
  has no price history for them.

`results/eval_survivorship.md` has the full table and the safe/unsafe claims. Treat
the stock-sleeve alpha as unproven until a point-in-time NASDAQ membership file
with delisted names is added. ETF-only results are the survivorship-free evidence.

## Files and CI

Owned here: `bot/evaluation/`, `scripts/reproduce.py`, `tests/test_evaluation.py`,
`.github/workflows/tests.yml`, `docs/evaluation.md`, `results/eval_*.md` and
`results/eval_trials.csv`.

CI runs every `tests/test_*.py`, including the synthetic evaluation tests, then a
cache-free `scripts/reproduce.py --help` smoke test. The reproducibility commands
themselves need the committed cache and are intentionally not run on a clean CI
checkout.

## Known limitations

- Dividends are still gross (adjusted closes); dividend/withholding tax is a
  separate backlog item.
- The EUR ledger fee model approximates IBKR at €10k; the tax/FX effect dominates.
- The TECH universe is survivorship-biased; the US book is less biased but not
  complete.
- Walk-forward and CSCV are tools, not proof: report the number of trials and do
  not quote a single best path as the expectation.
