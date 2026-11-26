# Alpha Lab — research protocol and pre-registration

Branch `v2/alpha-lab`. Data snapshot: cached bars through **2026-11-25**
(`cache/bars`, symlinked to the main checkout). No live downloads in any
headline run.

The purpose of this branch is not to find the best in-sample parameter. It is
to decide which return sources are real after French tax, costs, financing and
walk-forward selection, and to publish the rejections with the same rigor as
the winners.

## Results at a glance (2026-11-26)

- **H1 cross-asset:** `reject`. 1x OOS 2.23x headline / 2.07x EUR ledger vs
  QQQ 3.15x / 2.88x; OOS maxDD -40.2% vs QQQ -35.1%. Full fixed-config 1x is
  2.81x headline / 2.46x ledger.
- **H2 leverage:** `reject` as a return source. 2x OOS 2.96x headline /
  3.12x ledger, still below US 2.5x, with a -66.8% drawdown.
- **H3 TECH sleeve:** `accept` as a robustness finding, `reject` as a new
  default. PIT S&P IT retains 107% of the static headline (61.07x vs 57.01x;
  EUR ledger 57.90x vs 52.98x). This is consistent with, not proof against,
  survivorship bias; the static-49 PIT row is cleaner but still a survivor
  list. US all-PIT is better at 70.86x / 66.17x and a -56.9% ledger drawdown.
  Keep US monthly primary; TECH is forward validation.
- **H4 short horizon:** `reject`. Close-to-close OOS 2.64x vs QQQ 5.49x
  full-window; 2x-cost 1.13x is below BIL 1.20x. Overnight 1.01x at
  an optimistic 5 bps/side, 0.82x at 7.25 bps/side all-in, 0.60x at
  14.5 bps/side. No satellite.
- **H5 PIT Nasdaq-100:** `paper` (terminal robustness, weak rolling premium).
  PIT retains **95%** of the static TECH EUR ledger (50.29x vs 52.98x;
  terminal-FX 55.14x vs 57.01x), survives 2x costs (20.78x), but beats the
  static core-only book in only 18% of 5-year windows. US all-PIT remains
  primary at 66.17x EUR ledger / -56.9% maxDD. 110/266 PIT tickers lack cached
  bars: residual delisting bias makes the PIT number an upper bound.
- **Candidate config:** no Alpha Lab allocation; keep US monthly 2.5x
  (70.86x headline / 66.17x EUR ledger, -56.9% ledger maxDD). If the TECH
  sleeve is kept, use PIT Nasdaq-100 membership (`paper`, not default).

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

### H5 — PIT Nasdaq-100 TECH universe (v4)

The static 49-name TECH list is all-always and survivor-selected. Build a
point-in-time Nasdaq-100 membership from public complete snapshots
(`bot/research/data/nasdaq100_history.csv`, source documented in
`bot/research/data/README.md`) and re-run the same monthly 2.5x book.

Pass: PIT retains at least 80% of the static book's dividend-corrected EUR
ledger, beats its own core-only version, survives 2x costs, and does not
overtake US all-PIT. If PIT collapses, the static sleeve was largely a
survivorship artifact; if it survives, keep US primary and use PIT membership
for any retained TECH sleeve. Residual delisting bias is reported explicitly.

### H6 — UCITS semiconductor core (v5)

Test whether an Irish-domiciled accumulating semiconductor UCITS ETF can
replace US-domiciled SMH for a French CTO. Vehicles: SMH.L (VanEck, IE,
USD line), SEMI.AS (iShares, IE, USD line), SEMI.L (same iShares ISIN, GBP
line), control SMH (US). Convert all to USD with same-date FX; use the common
window 2021-08-05..2026-11-25; same US monthly 2.5x signal and engine.

Pass: UCITS retains at least 80% of SMH's dividend-corrected EUR ledger and
survives 2x costs. Accept as preferred vehicle only if returns are comparable
and the tax/estate benefit is clear; otherwise reject the vehicle change while
documenting the qualitative estate benefit. No look-ahead in FX conversion;
tests use synthetic bars.

### H7 — Futures leverage (v6)

Test whether 2.5x index exposure is cheaper through equity-index futures
(ES/NQ) than through an ETF plus 5%/yr margin. Use continuous ES=F/NQ=F and
MES=F/MNQ=F histories; model quarterly roll at 1 bp + 0.5 bp commission,
0.5 bp monthly rebalance, 4.50%/yr implied futures financing, 0%/3% cash
collateral interest, and annual French PFU with ten-year loss carry. No
look-ahead: position set at the prior close.

Pass: futures after-tax EUR wealth and maxDD must both be better than the
same-index ETF+margin control, and futures must win a majority of 5-year
windows. Otherwise reject and keep ETF+margin.

### H8 — Short-horizon II (v7)

New families only: daily pairs/relative value (SMH/SOXX, QQQ/SPY, sector/SPY),
material SEC 8-K drift with date-shuffle placebo, and a tiny intraday gap-fade
proof of concept. No repeat of daily/weekly reversal, 20-day momentum,
overnight, faster rebalancing or covered calls.

Pass: after-tax OOS beats QQQ/BIL, survives 2x costs, event drift beats the
placebo, and intraday has enough trades to be implementable. Otherwise reject
and keep the monthly book.

### H9 — Short-horizon III (v8)

Actual Yahoo 1h 2y and 5m 60d bars, filtered 09:30-16:00 ET. Test 1-hour
opening-range breakout, gap fade, VWAP reversion and last-hour momentum;
5-minute execution-quality check only. Daily 2015-2026: open-to-close vs
close-to-open decomposition, volatility-scaled gap fade with lagged VIX
conditioning, and cross-sectional short-term reversal on liquid ETFs. Signals
use completed bars; entry is next bar open; 5/10 bps per side; annual French
PFU. Pass only if after-tax beats BIL and QQQ with 2x-cost survival and no
single-year dependence; intraday sample limits are stated.

### H10 — Intraday pairs and lead-lag (v9)

Actual Yahoo 1h 2y and 5m 60d bars (SPY/QQQ/SMH/SOXX/XLK), 09:30-16:00 ET.
Test SMH->QQQ/SPY lead-lag at lags 1-3 in both directions (threshold and
rolling-beta regression), intraday log-spread pairs (SMH/SOXX, QQQ/SPY) with
z-score entry/time stop, and cross-sectional reversal on the liquid ETF set.
Signals use completed bars; entry is the next bar open; 5/10 bps per side;
annual French PFU. Pass only if after-tax beats BIL and QQQ on the same 2-year
sample, survives 2x costs and has an implementable order path; otherwise
reject. 5m is a 60-day execution-quality sample, not robustness evidence.

## File ownership

- `bot/research/`: harness (orchestrator).
- `scripts/research_cross_asset.py`, `results/alpha_cross_asset.md`: worker A.
- `scripts/research_tech_sleeve.py`, `results/alpha_tech_sleeve.md`: worker B.
- `scripts/research_short.py`, `results/alpha_short.md`: worker C.
- `scripts/research_baseline.py`, `tests/test_research.py`, `docs/research.md`,
  `results/alpha_summary.md`: orchestrator.
- v4 PIT work: `bot/research/pit_universe.py`, `bot/research/data/`,
  `scripts/research_pit_tech.py`, `tests/test_pit_universe.py`,
  `results/pit_tech.md`.
- v5 UCITS work: `bot/research/ucits.py`, `scripts/research_ucits_core.py`,
  `tests/test_ucits.py`, `results/ucits_core.md`.
- v6 futures work: `bot/futures.py`, `scripts/research_futures.py`,
  `tests/test_futures.py`, `results/futures.md`.
- v7 short-horizon II: `bot/research/short2/`, `scripts/research_short2.py`,
  `tests/test_short2.py`, `results/short2.md`, `results/short2_trials.csv`.
- v8 short-horizon III: `bot/research/short3/`, `scripts/research_short3.py`,
  `tests/test_short3.py`, `results/short3.md`, `results/short3_trials.csv`.
- v9 intraday pairs: `bot/research/intra_pairs/`,
  `scripts/research_intraday_pairs.py`, `tests/test_intraday_pairs.py`,
  `results/intraday_pairs.md`, `results/intraday_pairs_trials.csv`.

Workers do not edit shared earnings code (`bot/backtest.py`, `bot/strategy.py`)
and do not commit. The orchestrator reviews and commits.
