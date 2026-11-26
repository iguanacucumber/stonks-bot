# v6 summary — wrapper, futures, acceptance, dashboard

Date: 2026-11-26. Built on the merged v2–v5 stack. Research/paper only.

## Merged

| workstream | issue | branch | PR | merge commit |
|---|---|---|---|---|
| Assurance-vie wrapper | #50 | `v6/av` | #55 | `7995217` |
| Futures leverage | #51 | `v6/futures` | #57 | `a262365` |
| Acceptance harness | #52 | `v6/acceptance` | #56 | `e0dfd23` |
| Monitoring dashboard | #53 | `v6/dashboard` | #58 | `f355f2b` |
| Covered calls (v5) | #46 | `v5/covered` | #54 | `f878c7c` |

## Assurance-vie: conditional opt-in, not a drop-in

Same gross path, 12-year tax age, after terminal AV tax:

| book | CTO | AV 0.50% | AV 0.80% | break-even fee |
|---|---:|---:|---:|---:|
| US monthly 2.5x | 66.17x | **126.72x** | 122.33x | 6.18% |
| balanced A6 | 36.56x | **66.48x** | 64.18x | 5.73% |

AV maxDD is worse (-61.1% vs -56.9% US; -50.4% vs -46.9% balanced) and
2018/2022 are worse, but the 5-year median/worst improve. Estate 990 I at
€660k: €101,500 vs €110,962 CTO French inheritance (one beneficiary); two
beneficiaries €71,000 vs €88,389. AV removes US-situs in the model.

The catch: a standard French assurance-vie cannot borrow. Implementing 2.5x
needs a leveraged fund/structured note with its own fees and risks; otherwise
it is a different, lower-return product. Accept only as an opt-in after the
operator confirms the contract can hold the same exposure at a fee below the
break-even. `results/assurance_vie.md`.

## Futures leverage: reject

Continuous ES/NQ/MES/MNQ data, quarterly roll, 4.5% implied financing, 0/3%
cash interest, annual PFU assumption. 2015-2026 after-tax EUR:

| book | after-tax | CAGR | maxDD |
|---|---:|---:|---:|
| SPY ETF+margin 2.5x | **5.34x** | 15.1% | -61.4% |
| ES futures 2.5x, 0% cash | 4.63x | 13.8% | -71.0% |
| QQQ ETF+margin 2.5x | **11.09x** | 22.5% | -60.7% |
| NQ futures 2.5x, 0% cash | 8.61x | 19.9% | -75.7% |

Futures won 0/88 five-year windows. Financing the full 2.5x notional is worse
than ETF margin on 1.5x borrowed after roll/slippage; the tail is worse. Keep
ETF+margin. `results/futures.md`.

## Covered calls: model-only income opt-in

At 40% IV / 10% OTM the US book model is 75.96x vs the 66.72x gross-dividend
baseline (66.17x exact), maxDD -48.5% vs -56.4%; at 25/30% IV most variants have
negative option P&L. European cash settlement, fractional contracts and a flat
smile are optimistic. Not default; paper income experiment only.
`results/covered_calls.md`.

## Acceptance and dashboard

- `uv run python scripts/acceptance.py` -> **12/12 PASS in ~14s**: cache,
  backtest/EUR ledger (66.17/52.98), live plan US/TECH, paper replay/idempotency,
  reconcile, ops doctor, backup/restore, advisor orders, strict reproduce.
- `uv run python scripts/dashboard.py` -> static HTML/Markdown cockpit:
  equity/risk/data/gate/orders, warnings, no server. `--check` for CI.
- No network, no secrets, no real orders.

## Net recommendation (unchanged)

Keep **US monthly 2.5x CTO** as the growth default; **balanced 70/30** as the
lower-drawdown opt-in. Do not switch to PEA/UCITS/futures for return reasons.
Use assurance-vie only if the contract can implement the exposure below the
break-even fee, or for estate planning. Covered calls only as a paper income
experiment. Handle US estate tax with treaty filings/beneficiary planning.

## Tests

All 27 `tests/test_*.py` suites pass on master.
