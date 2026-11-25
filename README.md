# stonks-bot

Paper-only systematic long-only equity portfolio (US by default, tested across 39 markets)
with a constrained LLM news overlay. No broker connection, no orders, no keys.

## What it does

**Default: dual-momentum rotation, 2x gross.** Hold SOXX while its 6-month total return
beats SPY; otherwise rotate into the S&P momentum sleeve. Monthly check, next-close
execution, 10 bps per side, 2x gross financed at 5%/yr with a daily de-leverage cap
(`--leverage 1` for the unlevered book), no trailing stop, no regime cut.

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

Known bias: ~18% of 2015–2026 index members have no Yahoo history (mostly acquired/delisted
names). They are dropped. Results are survivor-biased; treat them as a sanity check, not a
promise.

## Results (paper backtest, 10 bps, 2x gross at 5%/yr)

| $10,000 from | Bot | SPY | Bot max DD | SPY max DD |
|---|---:|---:|---:|---:|
| 2015-01-02 → 2026-11-24 | **$762,997** | $42,186 | -66.1% | **-33.7%** |
| 2026-01-02 → 2026-11-24 | **$12,340** | $10,534 | -67.3% | **-11.5%** |

Full-window CAGR 44.1% vs 12.9%; Sharpe 0.90 vs 0.79. Walk-forward CAGR: 25.8% / 37.7% / 76.3%
vs SPY 11.6% / 7.3% / 19.1%; beats SPY in 9 of 12 calendar years (loses 2015, 2018, 2022).
The leverage ladder is the whole story: 1x makes $212,063 with a -39.5% drawdown, 1.5x $461,047
(-54.4%), 2x $762,997 (-66.1%), 2.5x $980,671 (-75.1%). Sharpe is flat across the ladder, so
leverage buys money, not skill: `results/leverage.md`, `scripts/leverage.py`.

The catch: SOXX is the semiconductor index. Even at 1x, SOXX buy-and-hold made $140,256 with a
-45.8% drawdown, and the rotation is an overlay on that asset, not stock alpha. Ten rounds of
single-knob changes took the unlevered book from $91,136 to $212,063: `results/rounds10.md`.
Rounds 4-10 buy return by removing stop/regime protection and are tuned on this path; only
rounds 1-3 also improved the held-back 2023-2026 window. At 2x the drawdown reaches -66%, and
2022 is -33.7%.

**Cross-market:** the same config on **39 markets** (9 stock-level including a 49-name
NASDAQ tech book, 30 country/tech ETFs) is profitable in **39/39** and beats the local
index in **39/39** over 2015-2026, at 1x and at 2x, at 10 bps and at 30 bps:
`results/universal.md`, `results/leverage.md`, `scripts/universal.py`. The catch is the engine:
~70% of days are spent in SOXX, and the local sleeve alone loses to the CAC
(`results/cac40.md`). Universality here is the exported semiconductor core, not portable
stock alpha.

## Run

```bash
uv sync
uv run python -m bot.cli ingest                 # ~8 min, cached
uv run python -m bot.cli backtest --random-seeds 50
uv run python -m bot.cli report
uv run python -m bot.cli pnl --capital 10000    # 1h / 1d / 1w / 1m / 1y P&L
uv run python -m bot.cli paper --interval 60 --iterations 10
# stock + LLM sleeve instead of the SOXX core:
uv run python -m bot.cli backtest --core-weight 0
uv run python -m bot.cli study --max-names 40
```

**Tech / short horizons.** `--market TECH` runs the 49-name NASDAQ book (NVDA, MSFT, GOOGL,
META, AVGO, ... benchmark QQQ); it is the best 1-day/1-week book in the sample. `--profile
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
