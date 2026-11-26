# stonks-bot

[Version française](README.md)

Paper-only systematic long-only equity portfolio (US by default, tested across 39 markets)
with a constrained LLM news overlay. No broker connection, no orders, no keys.

> **Accounting note.** For a French CTO, the real-life number is the **EUR ledger**
> (`results/eur_ledger.md`): annual PFU on EUR gains, EUR cost basis at trade-date FX,
> exit tax. It gives **66.2x for US monthly** and **53.0x for TECH monthly**, versus
> 70.9x/57.0x for the older terminal-FX headline. See `docs/accounting.md`. The default
> for a new investor is US monthly; TECH remains the forward-validation book.

## What it does

**Default: dual-momentum rotation, 2.5x gross, daily trend cut.** Hold SMH while its
6-month total return beats SPY; otherwise rotate into the S&P momentum sleeve. Month-end
check, next-open execution, 10 bps per side, 2.5x gross financed at 5%/yr with a daily
de-leverage cap. While the core is below its 200-day MA, gross is halved until it recovers
(`--trend-ma 0` disables, `--leverage 1` unlevers, `--exec close` fills at the close).
No trailing stop.

The S&P sleeve (used when SMH lags) is:
1. **Universe**: point-in-time S&P 500 membership (fja05680 history + 2026 changes), liquid names only.
2. **Math**: 12-1 momentum rank with a 10-day skip, top 6, inverse-vol weights, 6% name cap,
   40% sector cap.
3. **News**: material SEC 8-K events (earnings, M&A, CEO, guidance, delisting), plus a monthly
   GDELT macro headline digest. Filing timestamps are point-in-time; after-close filings are
   only usable next session. Optional; not in the default because it showed no edge.
4. **LLM overlay**: asks one question per name per month:
   *will this stock beat the S&P 500 over the next 63 trading days, and how confident are you —
   or is there nothing to say?* Output is strict JSON:
   `{"ticker","direction":"up|down|none","confidence":0..0.95,"reason"}`.
5. **Constraints**: the LLM can up-weight, cut, or exit a name, and add at most two names.
   Total deviation from the math portfolio is capped at 20% active share. It cannot short,
   invent tickers, exceed caps, or trade after the fact.
6. **Evaluation**: SPY, equal-weight S&P, momentum-only, LLM, placebo-LLM (same names/dates,
   no news), random-20 null, block-bootstrap CIs, calibration and Brier score.

## Which profile?

| profile | allocation | EUR ledger | maxDD | 2022 |
|---|---|---:|---:|---:|
| **max_growth** (default) | US monthly 2.5x | **66.2x** | -56.9% | -33.7% |
| balanced (paper opt-in) | 70% US book + 30% SPY | 36.6x | -46.9% | -27.4% |
| conservative (paper opt-in) | US monthly 1.5x | 19.3x | -40.4% | -20.3% |

`max_growth` maximizes terminal wealth but loses more than half at the worst
peak-to-trough. `balanced` gives up about half the terminal wealth for roughly
10 pp less drawdown; it is the profile for a human who must stay invested. Both
opt-ins are post-hoc and labelled `paper`, not forward-validated. Run
`uv run python -m bot.cli backtest --country fr --profile balanced` for the
balanced book, or `--profile monthly --leverage 1.5` for the conservative one. See `docs/profiles.md`.

## Short horizons: tested, rejected

Weekly/daily rebalancing of the same book, 1/5-day reversal, 20-day momentum and
the close-to-open overnight effect were tested with walk-forward folds, realistic
5 bps/side costs, French PFU and a 2x-cost run. The best close-to-close signal made
**2.64x after tax out-of-sample versus QQQ 5.49x**; at 2x costs it drops to 1.13x,
below BIL (1.20x). The overnight family made 1.01x under optimistic costs, 0.82x
all-in, and 0.60x at 2x costs. The always-overnight comparator loses 88% on SPY
before tax at a 10 bps per side (20 bps round trip) daily cost. Verdict: **reject**. No short-horizon satellite; the
monthly book remains the only production strategy. A market maker with sub-bp
spreads might find a gross overnight edge, but that is not a French retail CTO.
See `results/alpha_short.md` after merge.

## US estate tax and the PEA

A French non-US investor holding SMH/SPY/QQQ directly owns **US-situs assets**.
Without the treaty the US estate-tax exemption is only $60,000 and the marginal
rate reaches 40%. The 2004 France-US treaty provides a pro-rata credit that
removes the US tax in our examples, but it requires filings (706-NA/8833) and
does not remove French inheritance tax. An Irish UCITS core removes US situs,
but SMH.L retained only 64% of SMH's return in the common window (3.61x vs
5.63x): the return cost is larger than the tax saving in this sample. A PEA
cannot hold a pure semiconductor ETF: PUST.PA (Nasdaq-100) makes 5.77x after
social tax versus 66.17x for the CTO. Details: `results/estate_tax.md`,
`results/ucits_core.md`, `results/pea_semis.md`. Not tax or legal advice.

## Data

| Source | Use | Notes |
|---|---|---|
| Yahoo chart API | daily OHLCV, adjusted close | free, no key, delayed |
| SEC submissions + 8-K index | material filings, acceptance timestamps, text | free, requires a UA |
| GDELT DOC API | macro headlines | free, ~1 request/5s |
| `fja05680/sp500` | point-in-time constituents | lags a few months; disclosed |

Data guard: names with more than 2 daily moves over 50% in a year are excluded from
selection (`max_extreme=2`). This filters Yahoo ticker collisions (old S&P symbols reused
by penny stocks) without changing the shipped result.

Known bias: ~18% of 2015–2026 index members have no Yahoo history (mostly acquired/delisted
names). They are dropped. Results are survivor-biased; treat them as a sanity check, not a
promise. A point-in-time Nasdaq-100 sleeve check retains **95%** of the static-49 TECH
EUR-ledger terminal (50.3x vs 53.0x), so the sleeve is not mainly a survivorship
artifact; the US all-PIT book is still better on return and drawdown. An EUR/USD
hedge was rejected: at 100% the US book falls to 45.4x and maxDD deepens to -67%;
USD acted as a 2022 cushion. Tax-loss harvesting was also rejected after fees:
it is mostly tax deferral plus round-trip costs.

## Results (paper backtest, 10 bps, month-end rebalance, next-open fill, 2.5x gross)

| $10,000 from | Bot | SPY | Bot max DD | SPY max DD |
|---|---:|---:|---:|---:|
| 2015-01-02 → 2026-11-24 | **$2,191,762** | $42,186 | -60.7% | **-33.7%** |
| 2026-01-02 → 2026-11-24 | **$11,765** | $10,534 | -60.7% | **-11.5%** |

Full-window CAGR 57.5% vs 12.9%; Sharpe 1.04 vs 0.79. Walk-forward CAGR: 36.0% / 49.0% / 93.4%
vs SPY 11.6% / 7.3% / 19.1%; beats SPY in 10 of 12 calendar years (loses 2018: -34.5% vs -5.2%,
and 2022: -39.3% vs -18.6%). The ladder: 1x $245,147 (-33.9%, Sharpe 1.02), 1.5x $602,051
(-47.4%), 2x $1,152,098 (-58.5%), plain 2.5x $1,744,565 (-67.5%), **2.5x + daily trend
$2,191,762 (-60.7%, Sharpe 1.04)**, 3x $3,378,185 (-68.2%). `results/leverage.md`.

Two execution choices carry most of the recent gain. **Month-end rebalancing** instead of an
arbitrary 21-day clock took the book from $1.40M to $1.84M with a slightly better Sharpe.
**Next-open fills** (signal at the month-end close, market-on-open order) added another 19%:
$2.19M. That is the turn-of-month effect; it won in all 8 start dates and all 3 sub-periods.
`--rebalance-mode days --exec close` reproduces the old 21-day/close book.

The catch: SMH is the semiconductor index (more NVDA-concentrated than SOXX). The rotation is
an overlay on that asset, not stock alpha. Ten rounds of single-knob changes took the
historical SOXX-era chain from $34,531 to $214,182 (regenerated after the 2026-11 panel rebuild): `results/rounds10.md`; SMH later beat SOXX
on the US and tech books (`results/core.md`). Rounds 4-10 buy return by removing stop/regime
protection and are tuned on this path; only rounds 1-3 also improved the held-back 2023-2026
window. At 2.5x the full-window drawdown reaches -60.7%, and 2022 is -39.3% (the older 21-day
clock had a shallower 2022 at -32.8% but made 22% less money).

**Financing:** at 3%/5%/8%/10% margin the US book makes $2.81M / $2.19M / $1.51M / $1.17M;
even 10% borrowing cost leaves 49.4% CAGR. The edge is not a financing-rate artifact.

**Allocation:** the bot correlates only **0.66** with SPY (vol 63% vs 18%). The best Sharpe is
a **60/40 bot/SPY blend** rebalanced monthly: 42.7%/yr, Sharpe **1.05**, drawdown **-43.3%**,
versus 57.5% and -60.7% for the bot alone. 50/50 monthly gives 38.2%/1.05/-41.6%; 100% bot is
the max-money choice. `results/allocation.md`.

**Robustness:** 84 five-year windows (every month start 2015-2021): the bot beats SPY in
**100%**, median **+1055%** vs +94%. A 10-day block bootstrap (2000 paths) gives median 139x,
p5 5.6x, P(beat SPY) 95%, but median max drawdown -71% and 99% of paths breach -50%. Returns
are robust to start date; the risk is not small. `results/robustness.md`.

**Real life:** at 30 bps/side with next-open fills the US book still makes **$1,456,491**
(52.2%/yr); at 10 bps it is $2,191,762. A 20% tax on realized gains leaves **$579,284**
(40.8%/yr), 37% leaves **$164,988** (26.7%/yr). The 1-day mean survives costs and tax
(+0.260% → +0.246% → +0.179%). A weekday job at 21:15 UTC logs the forward paper book and
shadow comparison (`results/paper.md`). `paper_report.py` writes
`results/paper_audit.csv` and counts only dates whose return, weights and fills
all match the shadow; the gate needs five such dates.

**Taxable account:** `--profile tax --tax-rate 0.37` drops the stock sleeve, widens the
leverage band to 30%, and sells the highest-basis tax lots first. It makes less pre-tax
($1.04M vs $2.19M) but keeps more after tax: **$300,753 vs $164,988** at 37%,
**$235,505 vs $103,541** with a 9.3% state tax (SALT-deductible, wash sales enforced), and
**$138,336 vs $39,976** at 54.1% (federal + NIIT + 13.3% state), at 6.2x turnover instead of
15.1x, 1-day mean +0.207% vs +0.179%. HIFO hurts the high-turnover monthly sleeve and helps
the low-turnover tax book. A tax-sheltered account should run the monthly book.
`results/realism.md`, `results/tax_markets.md`.

**France (2026 rules):** `--country fr` models the CTO: PFU on the annual net
gain (34.5% before 2018, 30% 2018-2025, **31.4% in 2026**: 12.8% IR + 18.6% social),
10-year loss carry, 0.4% FTT on French purchases since April 2025, real broker ticket
grids, EUR/USD conversion (2 bps at Interactive Brokers, 25 bps at the retail
brokers), and EUR translation. The $2,191,762 headline is pre-tax, pre-fee, USD.
On €10k after tax and real tariffs (`results/france.md`, `results/brokers.md`):

| book (€10k) | Bourse Direct | Trade Republic | Interactive Brokers | index after tax |
|---|---:|---:|---:|---:|
| **US monthly sleeve (default)** | — | — | **66.2x EUR ledger** | 5.4x QQQ |
| TECH monthly sleeve (forward) | 11.7x | 31.9x | **53.0x EUR ledger** | 5.4x QQQ |
| TECH core-only tax book | 34.7x | 39.5x | 48.8x EUR ledger | 5.4x QQQ |
| French CTO (CAC + SMH core) | 6.4x | 12.5x | — | 2.1x CAC |
| French PEA (sleeve only) | 1.47x | 1.61x | — | 2.3x CAC |
| PEA Nasdaq-100 proxy (PUST.PA) | 4.96x | 5.08x | — | 5.4x QQQ |

At €10k the best book+broker is **monthly US at Interactive Brokers: 66.2x in the
EUR ledger** (70.9x terminal-FX headline, €28.8k fees, €132k PFU + €210k exit tax).
The TECH monthly book is 53.0x EUR ledger (57.0x terminal-FX) and remains the
forward-validation track. The core-only tax book at IBKR is 48.8x EUR ledger; at Trade Republic and Bourse Direct the tax
book wins (39.5x/34.7x versus 31.9x/11.7x monthly), because their 25 bps FX makes
~1,800 sleeve trades expensive. At 25 bps IBKR monthly falls to 31.9x, so the
core-only tax book at Trade Republic (39.5x) is the fallback if spot FX is not
available.

Start-date check: **84/84 five-year windows beat QQQ after tax** (median **5.67x**
vs 1.97x, worst 2.03x); full-window block bootstrap p5 **3.38x**. The tail is still
brutal: p5 max drawdown **-90.9%**. `results/robustness_best.md`.

FX stress: at **3 bps** still **84/84** (median 5.60x, full 55.6x); at **25 bps**
the monthly book wins **99%** of windows (median 4.38x, full 31.9x) and the tax
fallback wins **96%** (median 4.98x, full 39.5x). The win rate is robust to FX;
the level is not. `results/robustness_fx.md`.

Leverage after French tax at IBKR: 1.5x **20.2x** (DD -50%), 2.0x **37.2x**
(-60%), 2.5x **57.0x** (-68%), 3.0x **75.2x** (-74%). The 3x lever passes the
return bar but fails the tail: 2018/2022 worse and bootstrap p5 **1.71x** vs
2.39x at 2.5x. Keep 2.5x; 3x is a risk-on option, not the default.
`results/leverage_fr.md`.

Sleeve breadth: 4 names reaches **75.3x** after tax (p5 3.27x) but fails the
2022 test by 0.2 pp and doubles concentration; 5 names falls to 53.4x. Keep the
6-name sleeve for the forward book; 4 is a risk-on candidate, not the default.
`results/topn_fr.md`.

Market choice: **US monthly at IBKR is 66.2x in the EUR ledger** (70.9x terminal-FX),
maxDD -56.9%, versus TECH 53.0x (57.0x terminal-FX) / -65.6%. US wins the full window
and **7/7 five-year starts** (median **6.83x** vs SPY 1.74x, worst 2.95x), with lower
drawdown. It is now the default for a new investor; TECH remains the forward-validation
book because it has the only live paper history. `--market TECH` restores it.
`results/us_vs_tech.md`, `results/us_robustness.md`, `results/eur_ledger.md`.

The optimum depends on the broker's cost structure. `--country fr` now defaults to
**US + monthly + IBKR**; `--profile tax` selects the core-only book. Pass your actual
`--fx-bps` (per side) to let the CLI choose: at **≥15 bps** it switches the default to
the core-only tax book (39.47x at 25 bps).

Bourse Direct's €8.50 US ticket still makes it the wrong broker for a €10k US book.
No pure semiconductor UCITS is PEA-eligible (the fund must hold >75% EU/EEA
companies), so the best 1x PEA tech proxy (PUST.PA) makes 5.08x after tax, and even
the 2x Nasdaq PEA ETF (LQQ) is 14.4x, versus 66.2x for the best CTO. The PEA is not
a substitute for the US core. Keep the 200-day signal in the asset's own currency:
translating the price series to EUR before the rule changes the trend timing and
hurts. `results/france.md`, `results/brokers.md`, `results/pea_ucits.md`,
`results/eur_ledger.md`, `scripts/france.py`, `scripts/brokers.py`,
`scripts/pea_ucits.py`.
CLI: `backtest --country fr` defaults to US + monthly + Interactive Brokers and
prints both the terminal-FX and EUR-ledger blocks; `--market TECH` restores the
forward-validation book; `--profile tax` selects the core-only book; `--fx-bps`
sets your real conversion cost and auto-selects the book at ≥15 bps; `pnl` adds the
same line; `report` appends the France section.

## Paper autopilot

```bash
uv run python scripts/live.py plan  --book monthly --market US
uv run python scripts/live.py run   --book monthly --market US --paper
uv run python scripts/live.py status --book monthly --market US
uv run python scripts/live.py reconcile --book monthly --market US
uv run python scripts/forward_daily.py --paper --tax
```

Dry-run is the default; `--paper` is the only writer. `forward_daily.py` runs US + TECH (+ tax) after the close, refreshes only stale bars, reconciles, and updates the gate; a weekday automation runs at 21:45 UTC. State is atomic under
`state/live/`, orders are idempotent, and rails block stale data, holidays,
excess gross/turnover and duplicate orders. The paper ledger is **pre-tax,
average-cost**: it is not the after-tax EUR ledger. No real broker, no keys.
See `docs/live.md` and `results/live_demo.md`.

## Advisor and operations

```bash
uv run python scripts/advise.py --profile max_growth --horizon 12 \
  --drawdown-tolerance 0.60 --capital 10000 --wrapper cto --as-of 2026-11-25
uv run python scripts/ops.py status
uv run python scripts/ops.py doctor
uv run python scripts/ops.py backup --out /var/backups/stonks-bot
```

`advise.py` turns a profile, horizon, capital and drawdown tolerance into an
allocation and a paper order list, with a 15-year withdrawal simulator. `ops.py`
adds status, diagnostics, checksummed backups and the runbook. No real orders.

**Cross-market:** the same config on **39 markets** (9 stock-level including a 49-name
NASDAQ tech book, 30 country/tech ETFs) is profitable in **39/39** and beats the local
index in **39/39** over 2015-2026, at 1x and at the shipped 2.5x + trend, at 10 bps and at
30 bps: `results/universal.md`, `scripts/universal.py`. The catch is the engine: the book
spends most days in the semis core, and the local sleeve alone loses to the CAC
(`results/cac40.md`). Universality here is the exported semiconductor core, not portable
stock alpha.

## Run

```bash
uv sync
uv run python -m bot.cli ingest                 # ~8 min first run, cached
uv run python scripts/update_data.py            # advance US+TECH one session
uv run python -m bot.cli backtest --random-seeds 50
uv run python -m bot.cli backtest --profile tax --tax-rate 0.37
uv run python -m bot.cli backtest --rebalance-mode days --exec close  # old clock
uv run python scripts/paper_health.py
uv run python -m bot.cli report
uv run python -m bot.cli pnl --capital 10000    # 1h / 1d / 1w / 1m / 1y P&L
uv run python -m bot.cli paper --interval 60 --iterations 10
uv run python scripts/paper_daily.py --market TECH  # one forward paper row
uv run python scripts/paper_daily.py --replay 5     # reconcile with the backtest
uv run python scripts/paper_daily.py --market TECH --refresh
uv run python scripts/paper_report.py --market TECH  # live rows vs shadow
uv run python scripts/paper_health.py               # exit 2 if the job is stale
# stock + LLM sleeve instead of the SOXX core:
uv run python -m bot.cli backtest --core-weight 0
uv run python -m bot.cli study --max-names 40
```

**Tech / short horizons.** `--market TECH` runs the 49-name NASDAQ book (NVDA, MSFT, GOOGL,
META, AVGO, ..., benchmark QQQ); it is the best 1-day/1-week book in the sample. Anthropic
(`ANT`, IPO 2026-11-19) is in the universe but has only 5 bars, so it cannot pass the
126-day momentum filter until roughly May 2027 — it holds no weight today. `--profile
monthly|weekly|daily` changes the clock; faster profiles lose to costs, measured in
`results/short.md`.

`pnl` prices the current book with 1-minute bars for the last hour and today, and uses the
backtest equity for the week/month/year windows. It reads no state and places no orders.
Try `uv run python -m bot.cli pnl --market TECH`.

`paper` polls last prices on a seconds-to-minutes cadence and appends target weights to
`results/paper_ledger.csv`. It never sends an order. The strategy itself rebalances monthly;
intraday polling only changes what you see, not the signals.

Timing with a warm cache (16-core laptop): `backtest --random-seeds 20` ~11 s, bot-vs-SPY
only ~2 s, `paper` ~3 s, `ingest` ~8 min first run (downloads ~780 Yahoo series).
The engine is numpy-first: membership and price lookups are matrix operations, per-date
signals are cached, and panel pivots are shared across configs.

**Incremental data.** `update_data.py`, `paper_daily.py --refresh` and the scheduled jobs
share one cache path: only symbols whose last bar is behind the last completed US session
are requested, dead tickers back off for 7 days, delisted members are never retried once
their membership window is covered, and a closed/holiday session costs one benchmark
request instead of a universe sweep. The US panel parquet is rewritten only when a new bar
arrives; a current cache does zero network fetches. Splits and dividends are detected from
the overlap and trigger one full-history refetch for that symbol only.
Measured request counts and the test matrix: `results/cache.md`.

Tests:

```bash
uv run python tests/test_core.py   # accounting, determinism, no look-ahead
```

## LLM contract

- One batched call per 8 names, monthly, plus material-event reviews.
- Every prompt and response is cached by content hash. Reruns are free and deterministic.
- `none` is the default and is recorded, not silently converted to neutral.
- The model is told the date and to use only prior information. This is not a guarantee:
  2026 is inside model training windows. The placebo arm (no news) measures the leak.
- Model: `opencode-go/deepseek-v4.1-flash` via the local `opencode` CLI. Configurable with
  `--model`; any OpenCode-configured model works.

## Interpretation

Eleven years is one path; 2026 is eleven months. The report includes bootstrap intervals and
a random-portfolio null so a lucky run is not mistaken for skill. If the LLM arm does not beat
momentum-only, the report says so. The forward paper ledger is the only uncontaminated test.

## Agent workstreams

Private repo: `github.com/iguanacucumber/stonks-bot` (orchestration issue #5).
Branches: `agent/forward`, `agent/pea-ucits`, `agent/execution`, `agent/risk`.
Plan and merge protocol: `docs/agents.md`. Merge order: forward, PEA, execution,
risk. CI runs `tests/test_core.py` plus the CLI help on `master` and `agent/**`.

## Layout

```
bot/universe.py  membership, aliases, sectors
bot/data.py      Yahoo bars, cache, panel
bot/news.py      SEC 8-K + GDELT
bot/llm.py       prompt, subprocess, JSON validation, cache, overlay, calibration
bot/strategy.py  signals and weights
bot/backtest.py  accounting, costs, stops, metrics, bootstrap
bot/overlay.py   2026 study orchestration
bot/report.py    markdown + SVG
bot/cli.py       commands
tests/test_core.py
```


See `docs/feature_matrix.md` for the command map and `results/v2_summary.md` for the four-workstream v2 evidence, `results/v3_summary.md` for the real-life layer, `results/v4_summary.md` for PIT/tax/FX/forward, the EUR-ledger baseline, the rejected alpha/short-horizon studies, and the live/evaluation merge hashes.
