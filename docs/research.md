# Alpha Lab — research protocol and pre-registration

Branch `v2/alpha-lab`. Data snapshot: cached bars through **2026-11-25**
(`cache/bars`, symlinked to the main checkout). No live downloads in any
headline run.

The purpose of this branch is not to find the best in-sample parameter. It is
to decide which return sources are real after French tax, costs, financing and
walk-forward selection, and to publish the rejections with the same rigor as
the winners.

## Baseline (reproduced, not assumed)

`uv run python scripts/research_baseline.py` reproduces the shipped book:

| book | after-tax EUR | maxDD | CAGR | trades |
|---|---:|---:|---:|---:|
| TECH monthly, SMH core, 2.5x, 200d daily cut, IBKR CTO | **57.01x** | -67.8% | 42.7% | 1,836 |
| QQQ buy & hold, same tax/cost wrapper | **5.49x** | -35.1% | 17.5% | — |

Bank €10,000, 2015-01-01 to 2026-11-25. The 57.01x is a liquidation value in
EUR: USD gains are translated at the cached EURUSD path and the final
unrealized French PFU is subtracted.

## Cost model

`bot.research.costs.CostModel`:

- 5 bps market spread per side, IBKR US commission (USD 1 minimum / 0.25 bps),
  2 bps FX, 5%/yr financing on negative cash.
- French CTO: 30% PFU through 2025, 31.4% from 2026, annual net realized gains,
  ten-year loss carry, HIFO lots, final unrealized tax deducted.
- `CostModel.scaled(2)` doubles spread, commission bps, FX and financing. It
  does **not** double the tax rate or the fixed ticket fee. Every headline
  family is re-run at 2x costs.

## Walk-forward

Long horizon: annual expanding folds. Train 2015 through year Y, test year
Y+1, roll Y from 2018 to 2025. Test books get a 400-calendar-day warm-up so a
month-end signal and carried positions exist on day one; equity is sliced from
the test start. Parameters are selected on the training window only by Calmar
(CAGR / |maxDD|). The concatenated test path and a chain of per-fold
liquidation multiples are reported. Fold exit tax is paid at every fold
boundary, which is conservative.

Short horizon: the same annual folds, but at least four test years; signals are
daily or weekly and every variant is selected on the train fold only.

Statistics: 10,000-path block bootstrap (21-day blocks) for p5 terminal wealth
and p5 max drawdown; deflated Sharpe ratio using every training variant's
Sharpe. The trial registry `results/alpha_trials.csv` contains every attempted
variant, including losers.

## Pre-registered hypotheses

These rules were fixed before the formal walk-forward runs. A small exploratory
single-window prototype of cross-asset momentum was run before this file was
written; those variants are counted in the registry and are **not** used as
evidence. The formal OOS run is the evidence.

### H1 — ETF-only cross-asset dual momentum (long horizon)

Universe: SMH, QQQ, SPY, TLT, IEF, GLD, defensive BIL. Long-only. Rank on
12-1 or 6-1 momentum; require absolute momentum above BIL and optionally price
above the 200-day MA; hold top 1-3, equal or inverse-vol. Monthly rebalance,
signal at month-end close, fill next open.

Pass rule (balanced diversifier, not a TECH replacement):
1. Walk-forward OOS after-tax multiple beats QQQ buy & hold (5.49x) with a
   smaller max drawdown, and
2. beats QQQ in at least 60% of common 5-year windows, and
3. survives 2x costs with a positive after-tax multiple, and
4. has no single fold carrying more than half of the OOS terminal wealth.

If it fails (1) or (2), verdict `reject` as a core replacement. If it passes
but does not beat TECH 2.5x on after-tax return, verdict `paper` as a balanced
sleeve, not the default book.

### H2 — leveraged cross-asset variant

Same signal, 1.5x and 2.0x gross, 5% financing. Pass only if after-tax return
beats the 1x variant by more than the extra financing cost and maxDD stays
inside the TECH 2.5x drawdown (-67.8%). Otherwise reject leverage as a return
source; the signal must stand on its own.

### H3 — TECH stock sleeve after removing survivorship bias

Compare, with identical rules:
1. static 49-name TECH book (baseline),
2. core-only SMH book,
3. PIT S&P 500 membership filtered to current GICS Information Technology,
4. static 49 names filtered to PIT S&P membership.

The PIT-sector run still uses current sector labels, which is a residual bias;
it is flagged, not hidden. Pass: the PIT book retains at least 80% of the
static book's after-tax multiple and beats core-only on at least 60% of common
5-year windows. If not, recommend core-only and retire the stock sleeve from
the candidate config.

### H4 — short-horizon signals

Families: daily/weekly short-term reversal, daily/weekly momentum, and
close-to-open overnight conditional on the prior day's move. Liquid ETFs only
(SPY, QQQ, SMH, TLT, IEF, GLD). Signal at close t-1, fill at close t or next
open; costs at least 5 bps per side and a 2x-cost table. No shorting.

Pass: at least one family has positive net after-tax OOS return, a fold-level
hit rate above 50%, and beats a cash/BIL alternative after 2x costs. A family
that only wins in-sample or dies at 2x costs is `reject`, with the numbers kept
in `results/alpha_short.md`.

## File ownership

- `bot/research/`: harness (orchestrator).
- `scripts/research_cross_asset.py`, `results/alpha_cross_asset.md`: worker A.
- `scripts/research_tech_sleeve.py`, `results/alpha_tech_sleeve.md`: worker B.
- `scripts/research_short.py`, `results/alpha_short.md`: worker C.
- `scripts/research_baseline.py`, `tests/test_research.py`, `docs/research.md`,
  `results/alpha_summary.md`: orchestrator.

Workers do not edit shared earnings code (`bot/backtest.py`, `bot/strategy.py`)
and do not commit. The orchestrator reviews and commits.
