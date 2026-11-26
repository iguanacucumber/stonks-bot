# v3 summary — real-life layer merged

Date: 2026-11-26. Built on the four v2 workstreams. All five v3 PRs merged and
the full `tests/test_*.py` suite passes.

## Merged

| workstream | issue | branch | PR | merge commit |
|---|---|---|---|---|
| Dividends + withholding | #23 | `v3/dividends` | #34 | `89444d1` |
| IBKR paper adapter | #24 | `v3/broker` | #33 | `d2434a9` |
| Investor profiles + withdrawal | #25 | `v3/advisor` | #32 | `bb32e76` |
| Ops / backups / alerts / runbook | #26 | `v3/ops` | #31 | `6682896` |
| EUR ledger (earlier) | #21 | `v3/eur-ledger` | #22 | `b64130c` |

## Exact real-life baseline (€10k, IBKR CTO, EUR ledger + dividends)

| book | native | terminal-FX | EUR ledger | CAGR | maxDD | 2018 | 2022 |
|---|---:|---:|---:|---:|---:|---:|---:|
| **US monthly 2.5x (default)** | 64.41x | 70.86x | **66.17x** | 42.3% | -56.9% | -31.1% | -33.7% |
| TECH monthly 2.5x (forward) | 51.82x | 57.01x | **52.98x** | 39.7% | -65.6% | -10.5% | -52.4% |
| CORE SMH only | 46.26x | 50.89x | **48.80x** | 38.7% | -64.5% | -31.4% | -55.0% |

Dividend withholding/PFU is included where cached events exist. The exact
dividend drag is small for the equity books: US 66.72x -> 66.17x, TECH 53.45x ->
52.98x, CORE 49.32x -> 48.80x. A bond/cash sleeve loses ~1%/yr in 2023-2026.

## Profiles (post-hoc `paper` opt-ins)

| profile | command | EUR ledger | maxDD | 2022 |
|---|---|---:|---:|---:|
| max_growth (default) | `--country fr` | 66.17x | -56.9% | -33.7% |
| balanced | `--profile balanced` | 36.56x | -46.9% | -27.4%* |
| conservative | `--profile monthly --leverage 1.5` | 19.27x | -40.4% | -20.3%* |

\* 2022 from the risk workstream's net-of-scalar-dividend run; the EUR-ledger
multiple is the exact dividend-event number. Both opt-ins are post-hoc and not
forward-validated.

## Live, broker, advisor, ops

- `scripts/live.py` dry-run/`--paper` loop is idempotent, atomic, sell-first,
  stale/holiday gated, with gross/turnover/duplicate rails and a drawdown
  breaker. The paper ledger is pre-tax/average-cost, not the EUR ledger.
- `--broker ibkr-paper` adds an opt-in IBKR Client Portal paper adapter (DU
  accounts only), idempotent `cOID`, retries, partial/reject handling and broker
  reconciliation. Fake-transport evidence; no real gateway in this environment.
- `scripts/advise.py` maps horizon/drawdown/capital/wrapper to a profile, paper
  order list and 15-year withdrawal projection.
- `scripts/ops.py` provides status, doctor, checksummed backup/restore, alert
  routing and a deterministic failure drill. `deploy/` has example systemd/cron
  units; nothing is enabled automatically.

## Tests

All 15 suites pass on master:

```bash
for t in tests/test_*.py; do uv run python "$t"; done
```

Plus `scripts/reproduce.py baseline --no-write` -> 52.98/48.80/66.17.

## Remaining risks

- No real money, no live broker orders; IBKR adapter is paper-only.
- Forward evidence is still thin: TECH 1/5 verified dates, US just starting.
- Balanced/conservative are post-hoc; 2.5x gap risk and -57% drawdowns remain.
- Dividend events cover the traded US/TECH tickers; foreign/PEA dividends are
  not fully modeled.
- Ops alerts need operator secrets and a real endpoint test.
