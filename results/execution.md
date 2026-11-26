# Limit-at-open execution: fill rates and after-tax cost

The shipped book is meant to fill at the next open. This study asks
whether a limit a few basis points away from that open improves the real
fill, using the daily high/low that the built panel drops. The answer is
not a free lunch: a 5 bps limit is roughly neutral-to-slightly-positive,
and wider limits lose money through adverse selection on missed buys.

## Method

- Book: the shipped French `tax` book: SMH core, 2.5x, 500 bps financing,
  daily 200-day cut to 0.5x, month-end decision, intended next-open fill,
  5 bps spread + Trade Republic €1/order + 25 bps FX, French CTO HIFO, €10k.
- Signal: unchanged. The order is decided at the prior close; the limit is
  set from today's open and the fill is checked against today's high/low.
  A miss falls back to a market-on-close order, which is executable without
  using the signal's future. No same-day close feeds the signal.
- Buy limit = `open * (1 - k/1e4)`. If `low <= limit`, fill at the limit.
  Sell limit = `open * (1 + k/1e4)`. If `high >= limit`, fill at the limit.
  Otherwise fill at the close. `deleverage` trades are excluded because the
  engine executes those at the close, not at the open.
- Price improvement on a fill is `+k` bps by construction. The interesting
  quantities are the fill rate and the signed cost of the misses.
- The full path is rerun with `Config.limit_bps=k`; the estimate on the
  old close-fill 38.8x book is the measured relative change applied to it.

## Data note: real core open is now the default

`markets.local_core` still has only adjusted closes, so the old synthetic
core rows used `open == close` and filled SMH at the adjusted close. The
default US/TECH panel now splices the real adjusted SMH/SOXX open/high/low
from `cache/bars`; `markets.build(..., core_close_fill=True)` or
`--core-close-fill` restores the old path for comparison. Non-US markets
still keep the `open == close` core quirk.

The default TECH tax book therefore fills SMH at the real open: **39.50x**
after tax. The old close-fill path is **38.80x**; the difference is execution
data, not a strategy change.

## Baseline

| book | after tax (€10k) | multiple | trades | pending open orders | deleverage | fees | tax paid | exit tax |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| TECH tax, real SMH open (**default**) | €394,989 | **39.50x** | 179 | 174 | 5 | €33,533 | €53,050 | €186,819 |
| TECH tax, old close-fill (`--core-close-fill`) | €388,029 | **38.80x** | 177 | 173 | 4 | €31,815 | €50,569 | €184,183 |

The default baseline has one more order and one more deleverage than the
old close-fill path; the fill study uses its 174 open orders.

## TECH monthly book, same comparison

| book | after tax (€10k) | multiple | trades | fees | exit tax |
|---|---:|---:|---:|---:|---:|
| TECH monthly, real SMH open (**default**) | €319,044 | **31.90x** | 1917 | €68,349 | €105,267 |
| TECH monthly, old close-fill | €271,811 | **27.18x** | 2061 | €63,698 | €85,299 |

The monthly book is more sensitive than the core-only book: it rotates
between the SMH core and the stock sleeve, so the real core open changes
the path and the trade count too. The old close-fill row is the 27.18x in the existing `france.md`; the corrected default is 31.90x.

## Fill rates and cost, baseline orders held fixed

| k (bps) | side | orders | fills | fill rate | fill rate, notional | avg improvement when filled | avg missed-fill saving | notional-weighted cost change |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 0 | ALL | 174 | 174 | 100.0% | 100.0% | +0.0 bps | — bps | +0.0 bps |
| 0 | BUY | 102 | 102 | 100.0% | 100.0% | +0.0 bps | — bps | +0.0 bps |
| 0 | SELL | 72 | 72 | 100.0% | 100.0% | +0.0 bps | — bps | +0.0 bps |
| 5 | ALL | 174 | 166 | 95.4% | 98.8% | +5.0 bps | -128.4 bps | +3.5 bps |
| 5 | BUY | 102 | 95 | 93.1% | 98.0% | +5.0 bps | -131.5 bps | +2.6 bps |
| 5 | SELL | 72 | 71 | 98.6% | 99.9% | +5.0 bps | -106.6 bps | +4.9 bps |
| 10 | ALL | 174 | 157 | 90.2% | 92.3% | +10.0 bps | -131.0 bps | -5.0 bps |
| 10 | BUY | 102 | 91 | 89.2% | 89.2% | +10.0 bps | -155.3 bps | -13.3 bps |
| 10 | SELL | 72 | 66 | 91.7% | 96.9% | +10.0 bps | -86.4 bps | +7.3 bps |
| 20 | ALL | 174 | 149 | 85.6% | 88.3% | +20.0 bps | -133.4 bps | -4.3 bps |
| 20 | BUY | 102 | 85 | 83.3% | 83.9% | +20.0 bps | -152.7 bps | -16.5 bps |
| 20 | SELL | 72 | 64 | 88.9% | 94.8% | +20.0 bps | -92.5 bps | +13.7 bps |
| 30 | ALL | 174 | 137 | 78.7% | 86.2% | +30.0 bps | -129.4 bps | -0.9 bps |
| 30 | BUY | 102 | 77 | 75.5% | 80.7% | +30.0 bps | -148.2 bps | -16.7 bps |
| 30 | SELL | 72 | 60 | 83.3% | 94.3% | +30.0 bps | -90.2 bps | +22.5 bps |

Positive cost change = cheaper than the open. A missed buy that closes
higher costs about 130 bps, roughly 25x a 5 bps limit; that is the
adverse selection the strategy pays for waiting.

## Robustness of the per-order result

| k (bps) | net cost change | net after dropping 3 worst misses | years positive | worst year | next-open fallback, net |
|---:|---:|---:|---:|---|---:|
| 5 | +3.5 bps | +4.8 bps | 11/12 | 2015 (-18.0 bps) | +3.2 bps |
| 10 | -5.0 bps | +7.2 bps | 7/12 | 2025 (-34.6 bps) | -9.8 bps |
| 20 | -4.3 bps | +8.5 bps | 7/12 | 2025 (-26.6 bps) | -13.5 bps |
| 30 | -0.9 bps | +12.6 bps | 6/12 | 2023 (-91.3 bps) | -11.4 bps |

The 5 bps result is positive in 11 of 12 years. At 10-30 bps the
per-order sign is carried by a few trend-day misses; removing the three
worst flips it positive, so it is not a stable edge. The next-open
fallback is worse than the close fallback at every k.

## Full path after tax (€10k Trade Republic CTO)

| k (bps) | pre-tax | fees | tax paid | exit tax | **after tax** | vs honest baseline | estimate on shipped 38.8x |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 0 (honest baseline) | 58.18x | €33,533 | €53,050 | €186,819 | **39.50x** | — | 38.80x (shipped) |
| 5 | 58.58x | €33,584 | €53,230 | €188,023 | **39.78x** | +0.28x | 39.08x |
| 10 | 57.24x | €33,130 | €53,314 | €183,097 | **38.93x** | -0.57x | 38.24x |
| 20 | 57.09x | €33,028 | €53,310 | €182,465 | **38.84x** | -0.66x | 38.16x |
| 30 | 54.41x | €31,519 | €50,316 | €174,065 | **37.01x** | -2.49x | 36.35x |

The full path is the decision metric. The per-order table holds the
baseline share counts fixed; the engine rerun changes cash, size and the
trend-cut path, so the two views need not line up exactly.

## What the numbers say

1. **5 bps is the only candidate, and it is small.** 95.4% of orders fill, the notional-weighted cost change is positive, and the
full path gains +0.28x (+0.7%). That is inside the gap between the shipped and honest baselines and is
not enough to change the default.

2. **10 bps and wider lose.** The full path is -0.57x at 10 bps, -0.66x at 20 bps and -2.49x at 30 bps. Fill rates fall to 79% at 30 bps, and the
missed buys close 100-300 bps above the open on trend days. Dropping the
three worst misses flips the per-order net positive at 10/20/30 bps, which
means the sign is driven by a handful of large adverse days, not a stable
edge.

3. **The fallback is a choice, not free.** The close fallback is generous:
a next-open fallback is worse at every k tested (robustness table). A trader
who does not chase a miss keeps the old position and may miss a risk-off
cut, which is a larger risk than the few bps of spread saved.

4. **The old 38.8x was close-filled; the default is now 39.5x.** The
default US/TECH panel uses the real SMH open. The old close-fill path is
only a `--core-close-fill` comparison and is not the shipped default.

## Recommendation

**Keep the default next-open fill. Do not enable limit orders on the core
book.** The only positive case is 5 bps, worth roughly +0.3x after tax on the
39.5x default baseline (+0.7%), with 95% fills. It is not robust enough to
pay for the operational risk: a missed buy on a trend day costs ~130 bps,
and the strategy's edge is precisely to be long on those days. If the book
is paper-traded, log `--limit-bps 5` as a shadow next to the default; promote
it only if the live fill-rate and missed-fill P&L confirm the backtest.

## Risks and caveats

- Daily high/low is not a tick path. A limit touched only at the low may
  not have filled for size; the model assumes the whole order fills at the
  limit when the low touches it. This is optimistic on the fill rate.
- The close fallback assumes a market-on-close order is available and fills
  at the close. Real MOC cutoffs and closing auctions can be worse.
- Missed-fill P&L is dominated by a few trend days (2023-02, 2024-03,
  2025-06, 2025-10). A different sample or a different start date can flip
  the 5 bps sign.
- **Non-US markets still have the core quirk.** `local_core` remains
  close-filled for DE/FR/GB/etc.; only US/TECH use real core opens. Do not
  apply the 39.5x US/TECH correction to the French or 39-market tables.
- No borrow squeeze, no market impact, no queue position, no partial fills.

## Downstream docs to regenerate

- `results/france.md`: core-only tax 38.80x -> **39.50x**, monthly 27.18x ->
  **31.90x**; `results/france.csv` and `metrics_france.csv` follow.
- `README.md`: the 38.8x/27.2x headline rows and the next-open wording.
- `results/pea_ucits.md`: the CTO 38.80x comparator.
- `results/risk_v2.md`: its 38.80x baseline and all rule deltas.
- `results/summary.md`: the 38.80x summary row.
- Forward paper shadow: `paper_daily_tax.csv`, `paper_ledger_tax.csv` and
  `paper_replay_tax.csv` were logged on the old close-fill path. Re-baseline
  the shadow; do not rewrite historical rows.
- 39-market cost tables (`results/costs_tax.md`, `tax_markets.md`,
  `universal.md`): only US/TECH rows move; non-US cores are still
  close-filled until `local_core` is fixed.

## Reproduce

```
uv run python scripts/execution.py
uv run python -m bot.cli backtest --market TECH --country fr --profile tax \
  --broker trade-republic --capital 10000 --limit-bps 5 --random-seeds 0
uv run python -m bot.cli backtest --market TECH --country fr --profile tax \
  --broker trade-republic --capital 10000 --core-close-fill --random-seeds 0
```

The first CLI run uses the real core open and the opt-in 5 bps limit. The
second reproduces the old 38.8x close-fill path. `--limit-bps 0` gives the
default 39.5x baseline with the raw high/low panel loaded.

Generated by `uv run python scripts/execution.py`.
