# Alpha Lab — final summary

Branch `v2/alpha-lab`. Data snapshot 2026-11-25. Headline is USD-native after
tax translated at terminal FX; **EUR ledger** is the annual EUR tax-lot replay
(year-dependent French PFU: 30% through 2025, 31.4% from 2026) and is the
real-life number. Max drawdown is the pre-tax USD path unless labelled
EUR-ledger.

## One-screen answer

| question | answer | evidence |
|---|---|---|
| Does ETF-only cross-asset dual momentum replace the stock book? | **No — reject** | 1x OOS 2.23x vs QQQ 3.15x; OOS maxDD -40.2% vs QQQ -35.1%; full 2.81x headline / 2.46x EUR ledger |
| Does the static TECH sleeve survive survivorship correction? | **Consistent with robustness, not proof; not the best book** | PIT S&P IT 61.07x headline / 57.90x EUR ledger = 107%/108% of static; US all-PIT 70.86x / 66.72x with -56.4% ledger maxDD |
| Is there a short-horizon edge after costs? | **No — reject** | close-to-close OOS 2.64x vs QQQ 5.49x; 2x-cost 1.13x below BIL 1.20x; overnight 1.01x at 5 bps/side, 0.82x all-in |
| What should the main orchestrator integrate? | **Keep US monthly 2.5x as primary; no new Alpha Lab allocation** | US 66.72x EUR ledger, maxDD -56.4%; see baseline report |

## Long horizon: baselines

| book | headline | div-tax adj | EUR ledger | ledger+div | maxDD USD | maxDD ledger |
|---|---:|---:|---:|---:|---:|---:|
| US monthly 2.5x (primary) | 70.86x | 59.59x | **66.72x** | 61.71x | -63.2% | -56.4% |
| TECH monthly 2.5x (forward continuity) | 57.01x | 51.19x | **53.45x** | 52.81x | -67.8% | -65.1% |
| QQQ buy & hold | 5.49x | 4.80x | **4.65x** | 4.48x | -35.1% | — |

Do not quote 72.04x: that was an earlier draft with a broken canonical-symbol
state before the US panel was rebuilt. The reproducible US number is 70.86x
headline / 66.72x EUR ledger.

## Long horizon: cross-asset ETF dual momentum — REJECT

Universe SMH, QQQ, SPY, TLT, IEF, GLD, defensive BIL; 24 pre-registered
variants, 8 annual expanding folds, 2019-2026 OOS, 2x-cost run.

| book | OOS headline | OOS div-tax adj | OOS EUR ledger | OOS maxDD |
|---|---:|---:|---:|---:|
| 1x | 2.23x | 2.18x | 2.07x | -40.2% |
| 1.5x | 2.75x | 2.66x | 2.74x | -55.2% |
| 2x | 2.96x | 2.82x | 3.12x | -66.8% |
| 1x @ 2x costs | 2.12x | 2.07x | 2.00x | -40.2% |

QQQ over the same OOS window: 3.15x headline / 2.88x EUR ledger, maxDD -35.1%.
The full fixed-config 1x book is 2.81x headline / 2.46x EUR ledger. It fails
the pre-registered return gate, the maxDD gate (worse than QQQ), and the 5-year
window gate (16% beat QQQ). Max single-fold contribution to the OOS log return
is 31%, reported as a diagnostic, not a gate. **Verdict: reject as a core
replacement or satellite; do not allocate.** See `alpha_cross_asset.md`.

## Long horizon: TECH sleeve survivorship — ACCEPT ROBUSTNESS, NOT DEFAULT

Identical monthly 2.5x rules, QQQ benchmark:

| book | headline | div-tax adj | EUR ledger | maxDD ledger |
|---|---:|---:|---:|---:|
| Static 49 all-always (shipped) | 57.01x | 51.19x | 53.45x | -65.1% |
| Static 49, PIT S&P membership | 59.05x | 52.71x | 54.11x | -68.6% |
| PIT S&P IT universe | 61.07x | 54.54x | 57.90x | -67.1% |
| US all PIT | 70.86x | 59.59x | 66.72x | -56.4% |
| Core-only SMH | 45.33x | 41.20x | 43.01x | -64.1% |

PIT IT retains **107%** of the static headline (108% on the EUR ledger) and
beats core-only in 58% of 5-year windows. This is **consistent with**, not
proof of, a robust sleeve: current GICS sector labels are look-ahead, and the
static-49 PIT row is cleaner but still a survivor list. US all-PIT beats it on
return and drawdown, so TECH remains a forward-validation book, not the
primary. If the sleeve is kept, use PIT S&P membership rather than the static
49-name list. **Verdict: accept as a robustness finding; reject as a new
default.** See `alpha_tech_sleeve.md`.

## Short horizon — REJECT

32 close-to-close variants and 27 overnight variants, 8 expanding folds,
5 bps/side base, 10 bps/side 2x stress, and all-in 7.25/14.5 bps/side overnight.

| family | OOS 1x | OOS 2x | OOS CAGR | OOS maxDD | fold hit rate |
|---|---:|---:|---:|---:|---:|
| close-to-close (next open) | 2.64x | 1.13x | +21.8% | -36.2% | 75% |
| overnight, 5 bps/side (optimistic) | 1.01x | 0.70x | +1.7% | -26.0% | 50% |
| overnight, 7.25 bps/side all-in | 0.82x | 0.60x | -1.3% | -34.0% | 50% |

QQQ is 5.49x full-window / 3.15x OOS. BIL over the same OOS window is 1.20x
headline / 1.17x EUR ledger. The close-to-close family is positive but its
2x-cost headline (1.13x) is below BIL, and it is far below QQQ buy & hold.
The overnight book loses money at all-in costs. The 5 bps/side 1.01x row is
optimistic: all-in French CTO frictions are 7.25 bps/side (0.82x), and 14.5
bps/side for the doubled case (0.60x). **Verdict: reject; no satellite
allocation.** See `alpha_short.md`. Faster trading of the monthly book was
already rejected in `results/short.md` and `results/cadence.md` and was not
re-tested.

## Candidate config

There is no Alpha Lab allocation that improves on the current primary book.
The candidate is the existing master default, with the EUR ledger as the
reporting number:

```text
market       US
profile      monthly
leverage     2.5x
financing    500 bps
trend_ma     200 (daily cut to 50%)
core         SMH
core_mode    relative
tax_lots     hifo
broker       Interactive Brokers CTO
expected     70.86x headline / 66.72x EUR ledger
maxDD        -56.4% EUR ledger
```

TECH monthly remains a forward-validation book only. If a dedicated TECH
sleeve is retained, replace the static 49-name membership with PIT S&P
membership (paper candidate, not a default).

## Reproduction

```bash
cd /home/user/projects/stonks-wt-v2-alpha
uv run python scripts/research_baseline.py
uv run python scripts/research_cross_asset.py
uv run python scripts/research_tech_sleeve.py
uv run python scripts/research_short.py
uv run python scripts/research_merge_trials.py
uv run python tests/test_research.py
```

Registry: `results/alpha_trials.csv` contains every attempted variant,
including losers and training-only attempts. Counts: cross-asset 800,
TECH sleeve 7, short horizon 560; merged 1,367 rows.

## Open risks

- The ETF and stock samples are one US-tech bull decade; 2018/2022 are the
  only independent crises in the window.
- PIT sector labels are current GICS, not historical; residual look-ahead in
  the PIT IT run is flagged. The sleeve finding is consistent with robustness,
  not a clean counterfactual.
- Dividend tax is inferred from `adjclose/close`, not exact withholding;
  bond/cash ETF legs are the material uncertainty.
- Short-horizon daily bars cannot prove intraday execution; the rejection is
  robust because the cost wall is visible at 7.25-14.5 bps/side all-in.
- The EUR ledger uses the merged PR #22 algorithm plus a research adapter for
  year-dependent PFU rates; replace the adapter when the shared module does.
