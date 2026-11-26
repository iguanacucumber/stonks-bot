# Advisor results: profiles, orders and lifecycle

Generated from the committed cache with **no network and no orders sent**. All
profile multiples are the documented EUR-ledger values from `docs/profiles.md`
(€10k, 2015-01-02..2026-11-25, IBKR CTO, French tax, net of the risk workstream's
dividend-tax scalar). They are backtests, not forward evidence and not promises.

## Profile evidence

| profile | allocation | EUR ledger | net | maxDD | 2022 | evidence label |
|---|---|---:|---:|---:|---:|---|
| **max_growth** | USD US monthly 2.5x | **66.72x** | 61.64x | -56.7% | -33.2% | backtest default |
| **balanced** | 70% US book + 30% SPY, A6 70/30 | **36.85x** | 34.36x | -46.0% | -27.4% | post-hoc paper opt-in |
| **conservative** | US monthly 1.5x | **19.44x** | 18.51x | -40.2% | -20.3% | post-hoc lower-leverage opt-in |

`net` subtracts the modelled dividend-tax drag. The balanced and conservative
profiles are not validated alpha; they are explicit risk choices. The max_growth
profile is the best historical backtest, not a forward track record.

## Current illustrative targets (as of 2026-11-25)

Command pattern:

```bash
uv run python scripts/advise.py --profile PROFILE --horizon 12 \
  --drawdown-tolerance 0.60 --capital 10000 --wrapper cto \
  --income-need 0 --as-of 2026-11-25
```

On this session SMH was below its 200-day MA, so the live selector halved the
target. The normal gross and the current gross therefore differ:

| profile | normal gross | current target gross | orders |
|---|---:|---:|---:|
| max_growth | 2.50x | **1.25x** | 6 buys |
| balanced | 2.05x | **1.025x** | 6 US buys + SPY |
| conservative | 1.50x | **0.75x** | 6 buys |

Example max_growth order list (fresh €10k account, last close, paper only):

| ticker | side | qty | last | notional € | fee € |
|---|---:|---:|---:|---:|---:|
| MRNA | BUY | 10.366678 | 220.84 | 2,083.33 | 1.00 |
| OKTA | BUY | 11.346469 | 201.77 | 2,083.33 | 1.00 |
| CRL | BUY | 6.894053 | 332.08 | 2,083.33 | 1.00 |
| MPC | BUY | 6.611537 | 346.27 | 2,083.33 | 1.00 |
| ROP | BUY | 4.739028 | 483.09 | 2,083.33 | 1.00 |
| VLO | BUY | 7.087854 | 323.00 | 2,083.33 | 1.00 |

The balanced list adds SPY at 30% of capital and scales the US book to 70%; the
conservative list uses the same names at 1.5x instead of 2.5x. These are not
orders and no broker is connected.

## Tax estimate (12-year horizon, €10k)

The CLI estimate is an implied terminal burden from the net multiple:
`tax = (V - capital)/(1 - rate) * rate`.

| profile | wrapper | rate | projected after tax | implied tax |
|---|---|---:|---:|---:|
| max_growth | CTO | 31.4% | €639,023 | €287,920 |
| balanced | CTO | 31.4% | €354,395 | €157,638 |
| conservative | CTO | 31.4% | €189,885 | €82,338 |

These are long-horizon extrapolations of the historical CAGR, not forecasts.
PEA uses 18.6% after five years (30% before) and the PUST proxy, not these rows.

## Lifecycle projection (€10k, 15 years)

Deterministic scenarios: base = historical net CAGR; stress = CAGR minus 15 pp;
adverse = CAGR minus 30 pp. `real` is deflated by 2.5% inflation. The decumulation
row withdraws the planning SWR each year under the stress scenario.

| profile | base nominal | base real | stress nominal | stress real | SWR | stress + SWR withdrawal |
|---|---:|---:|---:|---:|---:|---:|
| max_growth | €1,806,738 | €1,247,491 | €336,030 | €232,017 | 2.0% | €297,233 |
| balanced | €864,687 | €597,036 | €146,994 | €101,494 | 2.0% | €125,907 |
| conservative | €396,382 | €273,688 | €60,927 | €42,068 | 2.5% | €46,713 |

The decumulation paths do not deplete within 15 years under these assumptions,
which is why a stress test is required rather than treating the SWR as safe.

## Caveats

- No forward evidence exists for these advisor profiles. The only forward logger is
  the TECH paper book.
- Balanced (A6) and conservative (1.5x) are post-hoc / lower-leverage choices.
- Dividend tax is a modelled scalar, not point-in-time dividend history.
- The PEA path is a PUST.PA proxy at 1x; it is not the CTO book. PEA assets are
  EUR-denominated and cannot use SMH or account-level leverage.
- US membership has missing historical names; the backtest is a consistency check.
- 2.5x leverage can gap through stops; historical maxDD was -56.7% in EUR.
- Not investment advice, tax advice, or a return promise.

Run: `uv run python tests/test_advisor.py` and
`uv run python scripts/advise.py --interactive`.
