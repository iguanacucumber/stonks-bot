# stonks-bot

Paper-only systematic long-only equity portfolio (US by default, tested across 39 markets)
with a constrained LLM news overlay. No broker connection, no orders, no keys.

## What it does

**Default: dual-momentum rotation, 2.5x gross, daily trend cut.** Hold SMH while its
6-month total return beats SPY; otherwise rotate into the S&P momentum sleeve. Monthly
check, next-close execution, 10 bps per side, 2.5x gross financed at 5%/yr with a daily
de-leverage cap. While the core is below its 200-day MA, gross is halved until it recovers
(`--trend-ma 0` disables, `--leverage 1` unlevers). No trailing stop, no monthly regime cut.

The S&P sleeve (used when SOXX lags) is:
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
promise.

## Results (paper backtest, 10 bps, 2.5x gross + trend at 5%/yr)

| $10,000 from | Bot | SPY | Bot max DD | SPY max DD |
|---|---:|---:|---:|---:|
| 2015-01-02 → 2026-11-24 | **$1,402,314** | $42,186 | -59.9% | **-33.7%** |
| 2026-01-02 → 2026-11-24 | **$13,082** | $10,534 | -64.1% | **-11.5%** |

Full-window CAGR 51.7% vs 12.9%; Sharpe 0.98 vs 0.79. Walk-forward CAGR: 34.4% / 47.0% / 90.6%
vs SPY 11.6% / 7.3% / 19.1%; beats SPY in 9 of 12 calendar years (loses 2015, 2018, 2022).
The ladder: 1x $224,860 (-36.3%, Sharpe 0.99), 1.5x $514,342 (-49.8%), 2x $904,486 (-60.7%),
plain 2.5x $1,246,358 (-69.4%), **2.5x + daily trend $1,402,314 (-59.9%, Sharpe 0.98)**.
The trend cut is what makes 2.5x carry the same drawdown as plain 2x, with 55% more money.
A leverage band lets gross overshoot before trimming: `--leverage-band 0.10` makes $1,435,896
(-60.9% DD), `0.20` makes $1,547,284 (-63.3% DD). Off by default; more money for more tail.
`results/leverage.md`, `scripts/leverage.py`.

The catch: SMH is the semiconductor index (more NVDA-concentrated than SOXX). The rotation is
an overlay on that asset, not stock alpha. Ten rounds of single-knob changes took the
unlevered SOXX-era book from $91,136 to $212,063: `results/rounds10.md`; SMH later beat SOXX
on the US and tech books (`results/core.md`). Rounds 4-10 buy return by removing stop/regime
protection and are tuned on this path; only rounds 1-3 also improved the held-back 2023-2026
window. At 2.5x the drawdown reaches -60%, and 2022 is -32.8%.

**Financing:** at 3%/5%/8%/10% margin the US book makes $1.82M / $1.40M / $945k / $726k;
even 10% borrowing cost leaves 43.5% CAGR. The edge is not a financing-rate artifact.

**Allocation:** the bot correlates only **0.66** with SPY (vol 63% vs 18%). A 50/50 blend
rebalanced daily makes 35.7%/yr with Sharpe **0.99** and a **-41.8%** drawdown, versus 51.7%
and -59.9% for the bot alone. 100% bot is the max-money choice; 50/50 is the risk-adjusted
one. `results/allocation.md`.

**Robustness:** 84 five-year windows (every month start 2015-2021): the bot beats SPY in
**100%**, median **+889%** vs +94%, worst window +147% vs +32%. A 10-day block bootstrap
(2000 paths) gives median 139x, p5 5.6x, P(beat SPY) 95%, but median max drawdown -71% and
99% of paths breach -50%. Returns are robust to start date; the risk is not small.
`results/robustness.md`.

**Real life:** at 30 bps/side (close fill) the US book still makes **$928,859**; next-open
fills lose at every cost level; a 20% tax on realized gains leaves **$374,527**, 37% leaves
**$107,889** (22.2%/yr). The 1-day mean survives all three (+0.246% → +0.232% → +0.165%).
Paper forward run on 2026-11-25 printed 127% gross because the 200-day cut was active; a
weekday job at 21:15 UTC now logs it automatically (`results/paper.md`).

**Taxable account:** `--profile tax --tax-rate 0.37` drops the stock sleeve, widens the
leverage band, and sells the highest-basis tax lots first. It makes less pre-tax ($1.14M vs
$1.40M) but keeps more after tax: **$324,234 vs $107,839** at 37%, **$251,290 vs $67,999**
with a 9.3% state tax (SALT-deductible, wash sales enforced), and **$144,036 vs $26,540** at
54.1% (federal + NIIT + 13.3% state), at 6.1x turnover instead of 13.8x, 1-day mean +0.210% vs
+0.165%. HIFO hurts the high-turnover monthly sleeve
($108k → $87k) and helps the low-turnover tax book (+26%). A tax-sheltered account should
run the monthly book. `results/realism.md`.

**Cross-market:** the same config on **39 markets** (9 stock-level including a 48-name
NASDAQ tech book, 30 country/tech ETFs) is profitable in **39/39** and beats the local
index in **39/39** over 2015-2026, at 1x and at the shipped 2.5x + trend, at 10 bps and at 30 bps:
`results/universal.md`, `results/leverage.md`, `scripts/universal.py`. The catch is the engine:
~70% of days are spent in the semis core, and the local sleeve alone loses to the CAC
(`results/cac40.md`). Universality here is the exported semiconductor core, not portable
stock alpha.

## Run

```bash
uv sync
uv run python -m bot.cli ingest                 # ~8 min, cached
uv run python -m bot.cli backtest --random-seeds 50
uv run python -m bot.cli backtest --profile tax --tax-rate 0.37
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
only ~2 s, `paper` ~3 s (network-bound), `ingest` ~8 min (downloads ~780 Yahoo series).
The engine is numpy-first: membership and price lookups are matrix operations, per-date
signals are cached, and panel pivots are shared across configs.

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
