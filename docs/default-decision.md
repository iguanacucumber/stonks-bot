# Default book decision: US monthly vs TECH monthly

Status: proposed for the post-v2 integration. Not yet applied to the CLI.

## Decision

Make **US monthly** the default for new real-life use (`--country fr`), while keeping
the **TECH monthly** forward paper book as the validation track. The two are different
jobs: one is the best-evidence allocation, the other is the longest-running forward
audit.

## Evidence (€10k, IBKR CTO, 2015-01-02..2026-11-25)

| book | EUR ledger | CAGR | maxDD | 2018 | 2022 | worst 12m |
|---|---:|---:|---:|---:|---:|---:|
| US monthly | **66.69x** | 42.4% | -56.4% | -31.0% | -33.3% | -49.3% |
| TECH monthly | 53.42x | 39.7% | -65.2% | -10.4% | -51.9% | -64.0% |
| CORE tax (SMH only) | 49.27x | 38.7% | -64.1% | -31.4% | -54.6% | -63.6% |

Terminal-FX headlines are higher (US 70.86x, TECH 57.01x). Use the EUR ledger for a
French investor; see `docs/accounting.md`.

Prior evidence also says:
- US wins 7/7 five-year starts after tax; median 6.83x vs SPY 1.74x
  (`results/us_robustness.md`).
- US full-window after tax 70.86x vs TECH 57.01x; US maxDD -63.2% vs -67.8% (USD).
- US uses historical S&P 500 membership; TECH uses a static 49-name NASDAQ list and
  is survivorship-biased. US is the more trustworthy universe.

## Why not wait for the TECH forward gate

The forward gate is a research control on the TECH book, not a reason to put new money
in the inferior book. The gate is 1/5 dates; a new user has no forward history on
either book. Keep the TECH logger running; do not force new users onto it.

## Risks / caveats

- US has more names and slightly higher fees (€28.8k vs €26.4k terminal-FX headline),
  but wins after tax.
- US suffers in 2018 (-30.1%) more than TECH (-9.8%); the 2022 and full-window numbers
  favor US strongly.
- Both books are 2.5x with a daily 200-day cut. A -57% drawdown is still possible.
- Survivorship in the US panel: ~18% of historical members lack Yahoo history; the
  backtest is a consistency check, not proof.

## Implementation checklist

1. `france.DEFAULT_MARKET = "US"`; update `tests/test_core.py`.
2. Keep `scripts/paper_daily.py --market TECH` as the forward-validation book; add a
   US forward logger separately.
3. Update `README.md` / `README.en.md`: headline 666,936 € / 66.2x EUR ledger; warning
   -56.4%; explain TECH is the validation book.
4. Re-run all tests and the EUR ledger report.
5. Do not change the existing TECH paper rows or the forward audit.

## Open question

If the Risk workstream delivers a balanced profile with materially lower drawdown at
acceptable return, expose it as `--profile balanced`; keep `US monthly 2.5x` as
`max_growth`. The default should be the profile that matches the user's stated
drawdown tolerance, but the best single historical book is US monthly.
