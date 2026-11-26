# US vs TECH monthly at IBKR, after French tax

Full window 2015-2026, 10k, 2.5x, SMH core, HIFO, French CTO at IBKR 2 bps,
5 bps spread.

| market | after tax | maxDD | fees | PFU paid | exit tax | trades |
|---|---:|---:|---:|---:|---:|---:|
| **US** | **70.86x** | **-63.2%** | €28,786 | €167,977 | €218,579 | 1,692 |
| TECH | 57.01x | -67.8% | €26,423 | €135,224 | €179,830 | 1,836 |
| US core-only tax | 51.64x | -68.3% | €9,952 | €60,651 | €235,215 | 178 |
| TECH core-only tax | 50.89x | -68.3% | €9,952 | €60,651 | €231,799 | 178 |

Five-year windows, after tax:

| start | US | TECH | US maxDD | TECH maxDD |
|---|---:|---:|---:|---:|
| 2015 | 2.95x | **3.58x** | -63.2% | -50.1% |
| 2018 | **3.35x** | 2.81x | -57.1% | -67.8% |
| 2021 | **7.29x** | 3.54x | -59.0% | -67.6% |

US wins the full window and 2 of 3 five-year windows, with lower full-window
drawdown. The 2015 window favours TECH. The US book is the original pre-tax headline, now re-priced after French tax
and IBKR fees. Re-run `scripts/us_vs_tech.py` after any panel change; the
2026-11-26 cache completion (784 tickers) moved it from 71.14x to 70.86x.

US also wins **7/7 five-year starts** (2015-2021) after tax, median 6.83x vs SPY 1.74x; `results/us_robustness.md`.

**Decision:** US is the best backtest. The default stays **TECH** until the
current 1/5 forward gate completes; switching now would reset the only live
validation. Use `--market US` for the best book today. Switch after 5/5.

Run: `uv run python scripts/us_vs_tech.py` (full window; the 5-year table is
from the same engine/config with start/end).
