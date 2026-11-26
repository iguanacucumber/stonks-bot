# v10 F: audit of the balanced/risk profile and after-tax gates

Scope: `bot/profiles.py`, `bot/risk/*`, `scripts/risk_v3.py`, `docs/profiles.md`,
`results/risk_v3.md`, A6/V8 and `--profile balanced`. This is a review, not a
default change: A6 remains a post-hoc `paper` opt-in.

## Verdict

**No default change.** A6's target weights and 2.05x pre-trend gross are correct,
the CLI reproduces the gross EUR-ledger result, and the 2x-cost gate passes. The
only real defect is a **documentation mixing of gross and net bases** in
`docs/profiles.md`; it is fixed in this branch. A6 is not leverage alpha: it
loses terminal wealth to a constant-leverage control and buys a better lower tail
and drawdown instead.

## Reproduced numbers

| path | multiple | maxDD | 2018 | 2022 | p5 |
|---|---:|---:|---:|---:|---:|
| A6 **gross-distribution EUR ledger** (CLI, cached dividends) | **36.56x** | **-46.87%** | -19.52% | -27.64% | — |
| A6 **net of scalar dividend tax** (risk_v3 decision) | **34.36x** | **-45.98%** | -19.42% | -27.36% | **2.79x** |
| A6 **2x costs, net** | **31.99x** | **-45.77%** | -20.20% | -27.76% | 2.61x |
| A6 2x costs, gross distributions | 34.31x | -45.74% | — | — | — |
| constant-leverage control 2.13x US book, net | 42.64x | — | — | — | 2.69x |

- A6 average gross is **1.739x**, not 2.05x, because the daily 200-day SMH cut
  reduces exposure for part of the window. The **target** before the cut is
  70% × 2.5 + 30% = **2.05x**; at a normal rebalance SPY is 30% and the core
  sleeve 1.75x gross. The test in `tests/test_profiles.py` now asserts both the
  unscaled selector weights and the 2.05x scaled target.
- The 2x net A6 figure clears the pre-registered 25x balanced gate; the 5 bps
  net figure clears the 28x gate. The 2022 net loss is -27.36% (gross -27.64%).
- A6 beats the 2.13x constant-leverage control only on p5 (2.79x vs 2.69x), not
  on terminal wealth (34.36x vs 42.64x). That is a tail/drawdown preference, not
  an alpha claim. The report already says this; the audit confirms it.

## Findings

### 1. Medium — `docs/profiles.md` mixed gross and net bases (fixed)

The table said "All numbers ... EUR ledger" and showed balanced `36.56x`,
`maxDD -46.9%`, `2022 -27.4%`. Those are not one path:

- `36.56x` and `-46.9%` are the CLI **gross-distribution EUR ledger** path
  (cached dividend events included as gross; dividend tax is not in this base).
- `-27.4%` is the **net-of-scalar-dividend** path (`risk_v3` decision base);
  the gross-ledger 2022 is **-27.64%**.
- The net path's maxDD is **-45.98%**, not -46.9%.

`docs/profiles.md` now has separate `maxDD (gross)`, `maxDD (net)`, `2022 gross`
and `2022 net` columns. No strategy input changed.

### 2. Low — `results/risk_v3.md` `eur_x` is not the CLI dividend-event ledger

A6 in `results/risk_v3_main.csv` shows `eur_x = 36.85x`; the CLI shows **36.56x**.
The difference is a base difference, not a calculation break: `risk_v3.evaluate`
calls `eur_ledger.replay` without the cached dividend events, so its `eur_x` is
gross distributions, while the CLI passes `dividend_events` and taxes/credits
them. Use 36.56x for the CLI gross ledger and 34.36x for the scalar-net decision;
do not compare 36.85x to 36.56x as if they were the same path.

### 3. Low — `docs/profiles.md` called the net column "EUR ledger"

The `net` column is a conservative scalar dividend-tax stress from
`bot/risk/dividends.py`, not the exact dividend-event EUR ledger. The table
header now says so. The exact-dividend CLI ledger is the 36.56x column.

### 4. Info — costs and double tax

A6 includes the engine's 5 bps spread/IBKR fees, financing, annual French PFU
and exit tax. The net decision path applies the scalar dividend drag to the
ledger equity and scales the exit tax proportionally; it does not tax the same
capital gain twice. The 2x-cost rerun changes only `cost_bps` (5 → 10 bps), not
tax rates; A6 is 31.99x net and still passes the balanced 2x gate.

## Reproduction

```bash
# CLI gross EUR ledger (cached dividends): 36.56x, -46.9%
uv run python -m bot.cli backtest --country fr --profile balanced --random-seeds 0

# Decision table, net dividend scalar, 2x costs, controls, p5
uv run python scripts/risk_v3.py --baseline
uv run python scripts/risk_v3.py --costs

# Weight/leverage invariant
uv run python tests/test_profiles.py
```

The CLI prints the gross EUR ledger and maxDD; the 2022 annual return is computed
from `bot.cli._eur_ledger(...).equity` (the audit command above) or from the
committed `results/risk_v3_main.csv` for the net path.

## Tests

- `tests/test_profiles.py` now asserts 70% core + 30% SPY unscaled, and 1.75x
  core + 0.30x SPY = **2.05x** after the engine's 2.5x leverage.
- `tests/test_risk_v3.py` covers the trend/scale primitives and gates.
- Full local `tests/test_*.py` suite passes.

## Recommendation

Keep A6 as a **post-hoc paper opt-in** with the corrected labels. Do not present
it as a default or as leverage alpha: the control earns more terminal wealth;
A6 buys p5/drawdown. The 2x-cost and estate/tail caveats remain unchanged.
