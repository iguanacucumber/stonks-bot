# v10 audit: rejected short-horizon studies

Date: 2026-11-26. Branch `v10/review-short`, base `dd2890e`.
Scope: `bot/research/short2/`, `short3/`, `short4/`, `intra_pairs/`;
reports `results/short2.md`, `short3.md`, `short_daily.md`,
`intraday_pairs.md`.

**Bottom line: no candidate survives.** The two high-severity bugs below change
reported CAGRs and one intraday terminal, but not any verdict. The cross-session
and no-lookahead fixes are committed in this branch.

## Findings

### H1 — CAGR annualised over trade days, not calendar span (high, fixed)

`bot/research/short3/intraday.py::summarize_trades` used
`years = len(daily_trade_days) / 252`. A strategy with 149 trades over 501
sessions looked like a 0.59-year path and its CAGR was overstated by roughly
3x. Intraday-pairs reports used the same helper.

Repro:

```bash
uv run python - <<'PY'
import pandas as pd
from bot.research.short3.intraday import summarize_trades
t = pd.DataFrame({"session": [pd.Timestamp("2020-01-02"), pd.Timestamp("2022-01-03")],
                  "net": [0.01, 0.01]})
print(summarize_trades(t, 500))
PY
```

Fix: use the first-to-last trade-date span (`days / 365.25`) and expose
`years`. Tests: `tests/test_short_audit.py::test_summarize_trades_uses_calendar_span`.

Corrected examples:

| study | before CAGR | after CAGR | after-tax multiple (unchanged) |
|---|---:|---:|---:|
| short3 SPY gap fade | +6.9% | **+2.1%** | 1.04x |
| intra_pairs best 1h pair | +7.6% | **+2.7%** | 1.053x |

### H2 — Intraday pairs/lead-lag could carry a last-bar signal overnight (high, fixed)

`intra_pairs.pairs.pair_trades` checked session end one bar too late and could
exit at the *next* session's close. `intra_pairs.lead_lag.lead_lag_trades` could
use the next session's first bar as the target of a last-bar signal. Both are
outside the stated intraday design.

Repro: synthetic last-bar signal test in
`tests/test_short_audit.py::test_pair_does_not_enter_from_last_bar_into_next_session`
and `test_lead_lag_does_not_use_next_session_bar`.

Fix: skip entries whose next bar is a new session; exit at the current session
close when a time-stop is reached. No look-ahead remains.

Corrected best rows:

| study | before | after | QQQ same sample |
|---|---:|---:|---:|
| intra_pairs best 1h pair (SMH/SOXX, lb 80, z 2.0, hold 3, long-only) | 1.078x | **1.053x** | 1.247x |
| intra_pairs best 5m | 0.821x | **0.875x** | 0.897x |

Both remain below QQQ and the 1h best is **0.912x at 2x costs**.

### M1 — Intraday short borrow/financing is not charged (medium, open)

`intra_pairs/lead_lag.py`, `pairs.py` and `xsec.py` can short, but the trade
returns only include 2x cost bps. At 5%/yr a three-hour short costs roughly
0.002% of notional, far below the 5 bps round trip; it cannot rescue a
1.05x-vs-1.25x gap. It is still missing from the model and should be added
before any future intraday execution claim. The audit does not change the
verdict on this omission.

### M2 — Multiple-testing correction is absent (medium, open)

The branch contains 2,112 short2 pair attempts, 70 short3 variants and 360
v9 intraday variants. None of the reports report a deflated Sharpe/CSCV or a
family-wise error adjustment. The best 1h pair (1.053x, +2.7% CAGR) is well
inside the noise expected from 360 variants and is below QQQ.

### L1 — 8-K event study attributes a CIK to its first ticker (low, open)

`short2/events.py` takes `tickers[0]`. Multi-class CIKs can be misattributed.
The event study is rejected anyway: 20-day net +1.91% vs placebo +1.70%,
p=0.07, with overlapping events and no tradable portfolio.

### L2 — Duplicate intraday timestamps were not dropped (low, fixed)

`short3.intraday.load_bars` now keeps the last row per timestamp. The current
cache has no duplicate timestamps, so historical numbers are unaffected.

## Data and timing audit

- 1h/5m bars are converted UTC->America/New_York, filtered 09:30-16:00 ET,
  and sorted; no duplicate timestamps in the cache.
- Signals use completed bars; next-bar-open entries are explicit. Event entry
  is the first close strictly after the filing date.
- Daily gap-z uses trailing sigma shifted one session; no future data.
- 1h sample is 501 sessions, 2024-11-26..2026-11-25, in-sample.
- 5m sample is 60 days; execution-quality only. No claim of 2-year robustness.
- French PFU is annual net with loss carry; no leverage in these studies.

## Re-run of best candidates

| candidate | corrected after-tax | corrected CAGR | maxDD | QQQ same sample | verdict |
|---|---:|---:|---:|---:|---|
| short4 sector reversal OOS (lb5/h10/n5 long-only) | **1.29x** | 3.3% | -35.9% | 2.80x OOS | reject |
| short2 pairs OOS (SMH/SOXX 120/2.0/10 long-only) | **1.88x** | 9.2% | -36.8% | 4.44x OOS | reject |
| short3 SPY 1h gap fade | **1.04x** | 2.1% | -12.2% | 1.26x 2y | reject |
| v9 intraday pair SMH/SOXX lb80 z2 hold3 long-only | **1.053x** | 2.7% | -13.0% | 1.247x 2y | reject |

All best candidates are below QQQ after tax; the 1h pair is also below QQQ at
2x costs (0.912x). No candidate is eligible for `paper` as a satellite.

## Reproduce

```bash
cd /home/user/projects/stonks-wt-v10-review-short
uv run python tests/test_short_audit.py
uv run python tests/test_short3.py
uv run python tests/test_intraday_pairs.py
uv run python scripts/research_short3.py
uv run python scripts/research_intraday_pairs.py
uv run python scripts/research_short2.py
uv run python scripts/research_short_daily.py
```

## Verdict

`reject` stands for every family. The audit found and fixed two real
implementation bugs (CAGR span and cross-session intraday fills), but corrected
returns remain below QQQ/BIL after costs. No satellite allocation.
