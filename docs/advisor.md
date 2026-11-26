# Investor profiles and withdrawal planning

`bot/advisor.py` and `scripts/advise.py` turn the research books into a concrete
allocation and a fresh-account order list. They are a planning layer, **not** a
broker adapter: no orders are sent, no API keys are needed, and no return is
promised. This is not investment advice.

## Five questions -> one profile

The CLI asks for horizon, maximum drawdown tolerance, capital, wrapper and annual
income need. The deterministic routing is:

```
horizon < 8y OR drawdown tolerance < 35%  -> conservative
drawdown tolerance < 55% OR income need>0  -> balanced
long horizon and >=55% tolerance          -> max_growth (default US)
PEA                                        -> PEA Nasdaq-100 proxy (PUST.PA, 1x)
```

An explicit `--profile` overrides the rule. A long-horizon, high-tolerance
investor defaults to **max_growth US monthly**, the best real-life backtest. The
balanced and conservative choices are explicit opt-ins, not defaults.

## Profiles (€10k, 2015-01-02..2026-11-25, IBKR CTO, EUR ledger)

| profile | allocation | EUR ledger | net | maxDD | 2022 | status |
|---|---|---:|---:|---:|---:|---|
| **max_growth** | 100% US monthly 2.5x | **66.72x** | 61.64x | -56.7% | -33.2% | backtest default |
| **balanced** | 70% US book + 30% SPY (A6 70/30), total gross 2.05 | **36.85x** | 34.36x | -46.0% | -27.4% | post-hoc paper opt-in |
| **conservative** | 100% US monthly at 1.5x | **19.44x** | 18.51x | -40.2% | -20.3% | post-hoc lower-leverage opt-in |

The source table is `docs/profiles.md`. The balanced selector is the merged
`bot/profiles.py` / `python -m bot.cli backtest --country fr --profile balanced`
implementation, not a second copy. If the risk workstream changes its final gates,
update the constants in `bot/advisor.py`, `bot/profiles.py` and the two documents
together. `EUR ledger` is the annual EUR tax-lot replay; `net` also subtracts the
modelled dividend-tax drag. Both are liquidation values, not promises.

## Commands

```bash
# Explicit profile, one-page decision + exact fresh-account order list.
uv run python scripts/advise.py --profile max_growth --horizon 12 \
  --drawdown-tolerance 0.60 --capital 10000 --wrapper cto \
  --income-need 0 --as-of 2026-11-25

# Five questions interactively.
uv run python scripts/advise.py --interactive

# Same run as JSON, with a 15-year deterministic lifecycle projection.
uv run python scripts/advise.py --profile balanced --horizon 12 \
  --drawdown-tolerance 0.45 --capital 10000 --wrapper cto \
  --income-need 1000 --as-of 2026-11-25 --json

# Show only allocation/orders, no lifecycle table.
uv run python scripts/advise.py --profile conservative --no-lifecycle
```

The current target uses the same `LiveConfig` / `build_snapshot` /
`backtest.target_book` path as `scripts/live.py`. If the committed cache is
unavailable, the CLI falls back to a clearly labelled static SMH/SPY allocation.
The daily 200-day core cut is applied to the target, so the printed gross can be
below the profile's normal gross (for example, 1.25x instead of 2.5x when SMH is
below its MA).

## Orders and tax estimate

Orders assume a **fresh account** with no existing positions. EUR capital is
converted to native currency at the current EUR-per-USD rate for US names.
Notional, share quantity and IBKR ticket estimate are printed; spread, FX and tax
are not charged by the paper CLI.

The tax estimate is an implied terminal burden, not a tax return. If the net value
is `V` and the rate is `t`, the gross gain is `(V - capital)/(1 - t)` and the
implied tax is `gross_gain * t`. Rates used:

- CTO: 31.4% PFU (12.8% income tax + 18.6% social, 2026).
- PEA: 18.6% after the five-year holding period, 30% before it.

A PEA cannot hold SMH directly or borrow at account level. The advisor therefore
uses **PUST.PA** (Amundi PEA Nasdaq-100 UCITS ETF, 1x) as a clearly labelled
proxy, with the documented 5.08x / 14.4% CAGR / -27% maxDD from
`results/pea_ucits.md`. It is not a substitute for the CTO core.

## Lifecycle simulator

`project_lifecycle()` is deterministic, not Monte Carlo. It supports accumulation
and decumulation, inflation-indexed withdrawals, a simple cost-basis/tax model,
and 15-year projections. Scenarios are:

- `base`: historical net CAGR;
- `stress`: net CAGR minus 15 pp;
- `adverse`: net CAGR minus 30 pp.

Withdrawals are taxed on the gain fraction of the account. CTO uses 31.4%; PEA
uses 18.6% after five years and 30% before. The annual return already includes
modelled annual tax and exit tax, so the withdrawal tax is a conservative extra
drag. `terminal_after_tax` also subtracts latent tax on the remaining gain.

The `safe_withdrawal_rate()` helper is a simple planning heuristic: start from 4%,
reduce it by 10% of the historical drawdown beyond 25%, floor at 2%. It is not a
Monte Carlo SWR and it is not a promise. Use the projection table to stress the
assumption explicitly.

## Tax-aware selling

`tax_aware_sell_order()` sells highest-basis lots first (HIFO) to minimise the
current taxable gain, then applies the wrapper rate. CTO and PEA both use this
planning order; actual broker lots, PEA withdrawal rules and loss carry must be
checked before selling. The function returns the sales, gross proceeds, tax, net
proceeds and any unfilled amount.

## Evidence labels and caveats

- max_growth is a **backtest**, not forward evidence. The only forward validation
  track is the existing TECH paper logger.
- balanced (A6) and conservative (1.5x) are **post-hoc / leverage choices**. They
  are not newly validated alpha.
- The balanced allocation's 70/30 blend and conservative 1.5x numbers come from
  the risk workstream; a later gate change can move them.
- Dividend tax is a modelled scalar in the risk workstream, not a point-in-time
  dividend history. Bond/cash sleeves are more exposed to this approximation.
- US membership data is incomplete for about 18% of historical members; the
  backtest is a consistency check, not proof.
- 2.5x leverage can gap through stops. The historical drawdown is -56.7% in EUR,
  and the future can be worse.
- PEA proxy PUST.PA is a different product with a much lower historical return.
- No forward evidence, no return promise, no tax advice, no investment advice.

Tests: `uv run python tests/test_advisor.py` covers profile validation, routing,
tax rates, order quantities, the PEA proxy path, lifecycle accumulation and
decumulation, inflation indexing, safe-withdrawal bounds and HIFO selling.
