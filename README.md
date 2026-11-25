# stonks-bot

Paper-only systematic long-only US equity portfolio with a constrained LLM news overlay.
No broker connection, no orders, no keys.

## What it does

**Default: dual-momentum rotation.** Hold SOXX while its 6-month total return beats SPY;
otherwise rotate into the S&P momentum sleeve. Monthly check, next-close execution, 10 bps
per side, no leverage, no trailing stop, no regime cut.

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

## Results (paper backtest, 10 bps)

| $10,000 from | Bot | SPY | Bot max DD | SPY max DD |
|---|---:|---:|---:|---:|
| 2015-01-02 → 2026-11-24 | **$212,063** | $42,186 | -39.5% | **-33.7%** |
| 2026-01-02 → 2026-11-24 | **$12,573** | $10,534 | -40.9% | **-11.5%** |

Full-window CAGR 29.4% vs 12.9%; Sharpe 0.95 vs 0.79. Walk-forward CAGR: 18.0% / 27.2% / 47.4%
vs SPY 11.6% / 7.3% / 19.1%; beats SPY in 11 of 12 calendar years (loses 2015 by 1.1 pp).
Ten rounds of single-knob changes took $91,136 to $212,063, each richer than the last:
`results/rounds10.md`. The catch: SOXX is the semiconductor index. SOXX buy-and-hold alone made
$140,256 with a -45.8% drawdown; the rotation adds ~$72k and cuts the drawdown to -39.5%, but
this is one industry's 11-year run plus the sleeve, not stock alpha. Rounds 4-10 buy return by
removing stop/regime protection and are tuned on this path; only rounds 1-3 also improved the
held-back 2023-2026 window.

**Cross-market check:** the same bot on 54 CAC 40 names (2015-2026) returns **$15,823**
vs **$26,644** for the CAC 40 total-return ETF, and below equal weight ($29,323). The US
result is a US asset, not a portable rule: `results/cac40.md`, `scripts/cac40.py`.

## Run

```bash
uv sync
.venv/bin/python -m bot.cli ingest                 # ~8 min, cached
.venv/bin/python -m bot.cli backtest --random-seeds 50
.venv/bin/python -m bot.cli report
.venv/bin/python -m bot.cli paper --interval 60 --iterations 10
# stock + LLM sleeve instead of the QQQ core:
.venv/bin/python -m bot.cli backtest --core-weight 0
.venv/bin/python -m bot.cli study --max-names 40
```

`paper` polls last prices on a seconds-to-minutes cadence and appends target weights to
`results/paper_ledger.csv`. It never sends an order. The strategy itself rebalances monthly;
intraday polling only changes what you see, not the signals.

Timing with a warm cache (16-core laptop): `backtest --random-seeds 20` ~11 s, bot-vs-SPY
only ~2 s, `paper` ~3 s (network-bound), `ingest` ~8 min (downloads ~780 Yahoo series).
The engine is numpy-first: membership and price lookups are matrix operations, per-date
signals are cached, and panel pivots are shared across configs.

Tests:

```bash
.venv/bin/python tests/test_core.py   # accounting, determinism, no look-ahead
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
