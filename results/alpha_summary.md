# Alpha Lab — final summary

Branch `v2/alpha-lab`. Data snapshot 2026-11-25. Headline is USD-native after
tax translated at terminal FX; **EUR ledger** is the annual EUR tax-lot replay
from `bot/eur_ledger.py` and is the real-life number. Max drawdown is the
pre-tax daily path unless labelled EUR-ledger.

## One-screen answer

| question | answer | evidence |
|---|---|---|
| Does ETF-only cross-asset dual momentum replace the stock book? | **No — reject** | 1x OOS 2.23x vs QQQ 3.15x; full 2.81x headline / 2.44x EUR ledger; maxDD -40% |
| Does the static TECH sleeve survive survivorship correction? | **Yes, but it is not the best book** | PIT S&P IT 61.07x headline / 57.27x EUR ledger, 107% of static; US all-PIT 70.86x / 66.02x and a smaller drawdown |
| Is there a short-horizon edge after costs? | **No — reject** | close-to-close OOS 2.64x vs QQQ 5.49x; 1.13x at 2x costs; overnight 1.01x and 0.70x at 2x |
| What should the main orchestrator integrate? | **Keep US monthly 2.5x as primary; no new Alpha Lab allocation** | US 66.02x EUR ledger, maxDD -57.5%; see baseline report |

## Long horizon: baselines

| book | headline | div-tax adj | EUR ledger | ledger+div | maxDD USD | maxDD ledger |
|---|---:|---:|---:|---:|---:|---:|
| US monthly 2.5x (primary) | 70.86x | 59.59x | **66.02x** | 61.05x | -63.2% | -57.5% |
| TECH monthly 2.5x (forward continuity) | 57.01x | 51.19x | **52.89x** | 52.25x | -67.8% | -66.0% |
| QQQ buy & hold | 5.49x | 4.80x | **4.65x** | — | -35.1% | — |

Do not quote 72.04x: that was an earlier draft with a broken canonical-symbol
state before the US panel was rebuilt. The reproducible US number is 70.86x
headline / 66.02x EUR ledger.

## Long horizon: cross-asset ETF dual momentum — REJECT

Universe SMH, QQQ, SPY, TLT, IEF, GLD, defensive BIL; 24 pre-registered
variants, 8 annual expanding folds, 2019-2026 OOS, 2x-cost run.

| book | OOS headline | OOS div-tax adj | OOS EUR ledger | OOS maxDD |
|---|---:|---:|---:|---:|
| 1x | 2.23x | 2.18x | 1.97x | -40.2% |
| 1.5x | 2.75x | 2.66x | 2.54x | -55.2% |
| 2x | 2.96x | 2.82x | 2.84x | -66.8% |
| 1x @ 2x costs | 2.12x | 2.07x | 1.90x | -40.2% |

QQQ over the same OOS window: 3.15x headline / 2.88x EUR ledger. The full
fixed-config 1x book is 2.81x headline / 2.44x EUR ledger. It beats QQQ only
16% of full-window 5-year windows and never beats US 2.5x. The lower drawdown
is real; the return premium is not. **Verdict: reject as a core replacement or
satellite; do not allocate.** See `alpha_cross_asset.md`.

## Long horizon: TECH sleeve survivorship — ACCEPT ROBUSTNESS, NOT DEFAULT

Identical monthly 2.5x rules, QQQ benchmark:

| book | headline | div-tax adj | EUR ledger | maxDD ledger |
|---|---:|---:|---:|---:|
| Static 49 all-always (shipped) | 57.01x | 51.19x | 52.89x | -66.0% |
| Static 49, PIT S&P membership | 59.05x | 52.71x | 53.69x | -69.7% |
| PIT S&P IT universe | 61.07x | 54.54x | 57.27x | -68.3% |
| US all PIT | 70.86x | 59.59x | 66.02x | -57.5% |
| Core-only SMH | 45.33x | 41.20x | 42.78x | -64.5% |

PIT IT retains **107%** of the static headline and beats core-only in 58% of
5-year windows. Survivorship is not the main driver of the sleeve's return.
But US all-PIT beats it on return and drawdown, so TECH remains a
forward-validation book, not the primary. If the sleeve is kept, use PIT S&P
membership rather than the static 49-name list. **Verdict: accept as a
robustness finding; reject as a new default.** See `alpha_tech_sleeve.md`.

## Short horizon — REJECT

32 close-to-close variants and 27 overnight variants, 8 expanding folds,
5 bps/side base and 10 bps/side 2x stress.

| family | OOS 1x | OOS 2x | OOS CAGR | OOS maxDD | fold hit rate |
|---|---:|---:|---:|---:|---:|
| close-to-close (next open) | 2.64x | 1.13x | +21.8% | -36.2% | 75% |
| overnight (close-to-open) | 1.01x | 0.70x | +1.7% | -26.0% | 50% |

QQQ is 5.49x full-window / 3.15x OOS. The close-to-close family is positive
but dominated by buy & hold and nearly dies at 2x costs; the overnight book
loses money at 10 bps round trip. The always-overnight comparator at 5 bps/
side is 0.12-0.71x depending on the ETF, which is the cost wall in one number.
**Verdict: reject; no satellite allocation.** See `alpha_short.md`. Faster
trading of the monthly book was already rejected in `results/short.md` and
`results/cadence.md` and was not re-tested.

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
expected     70.86x headline / 66.02x EUR ledger
maxDD        -57.5% EUR ledger
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
TECH sleeve 7, short horizon 544; merged 1,351 rows.

## Open risks

- The ETF and stock samples are one US-tech bull decade; 2018/2022 are the
  only independent crises in the window.
- PIT sector labels are current GICS, not historical; residual look-ahead in
  the PIT IT run is flagged.
- Dividend tax is inferred from `adjclose/close`, not exact withholding;
  bond/cash ETF legs are the material uncertainty.
- Short-horizon daily bars cannot prove intraday execution; the rejection is
  robust because the cost wall is already visible at 10 bps round trip.
- The EUR ledger is the merged implementation from PR #22; this branch uses it
  directly and does not fork the accounting.
