# Accounting modes: native, terminal-FX, EUR ledger

The backtest engine works in the asset currency. A French investor needs a EUR view.
There are three numbers in this repo, and they are not interchangeable.

| mode | what it does | where used |
|---|---|---|
| native | after-tax result in the asset currency (USD for US/TECH) | research comparisons, engine tests |
| terminal-FX | native after-tax multiplied by spot EUR/USD at the end | legacy README headline |
| EUR ledger | replay every trade in EUR at trade-date FX, EUR cost basis, annual PFU | real-life French CTO view |

## Why terminal-FX is not enough

French tax is assessed on EUR gains each year. FX gains and losses are part of the
taxable base, and each lot's cost basis is set at the trade-date FX. Translating only
the final amount at spot ignores the annual tax timing and the path of the EUR basis.

The EUR ledger (`bot/eur_ledger.py`, `scripts/eur_ledger.py`) keeps the USD signals and
share quantities unchanged, then re-settles the trades in EUR with HIFO lots, 31.4% PFU,
10-year loss carry, 5% financing on negative EUR cash, and IBKR-like fees.

## Baseline (€10k, 2015-01-02..2026-11-25, IBKR CTO)

| book | native USD | terminal-FX | EUR ledger | CAGR | maxDD |
|---|---:|---:|---:|---:|---:|
| TECH monthly | 51.82x | 57.01x | **52.89x** | 39.7% | -66.0% |
| TECH core-only tax | 46.26x | 50.89x | **49.02x** | 38.8% | -64.2% |
| US monthly | 64.41x | 70.86x | **66.02x** | 42.3% | -57.5% |

The terminal-FX headline overstates the EUR-ledger result by about 7% on the monthly
books. Ranking is unchanged: US monthly is ahead of TECH on return and drawdown; the
core-only tax book is behind on terminal wealth but cheaper to run.

## What to quote

- Real-life French CTO: **EUR ledger**.
- Cross-market/research comparisons: native, clearly labelled.
- Terminal-FX: only for continuity with older reports, never as "what you keep".

## Caveats

- Dividends are still gross (adjusted closes); dividend and withholding tax are a
  separate backlog item. Small for SMH, material for bond/gold books.
- The fee model approximates IBKR at €10k; the engine's exact fee total can differ by a
  few hundred euros. The tax/FX effect dominates.
- The overlay does not change `bot/backtest.py`, the CLI defaults, or the forward book.

See `results/eur_ledger.md` for the full table and reproduction command.

## Deferred exit tax (do not compare pre-exit wealth to a liquidated benchmark)

The EUR-ledger multiple is a **liquidation** value: it subtracts the tax due if the
whole book were sold at the end. A long-term holder does not pay that tax today.

| book | EUR ledger (liquidated) | pre-exit equity | latent exit tax |
|---|---:|---:|---:|
| US monthly | 66.02x (€660,221) | 87.07x (€870,714) | €210,493 |
| TECH monthly | 52.89x (€528,939) | 68.99x (€689,919) | €160,980 |
| CORE tax | 49.02x (€490,218) | 70.11x (€701,125) | €210,907 |

`pre-exit equity` already includes all tax paid in prior years; it is not "tax-free".
The latent exit tax is a real liability when you sell. Deferral is valuable, but
concentrating a portfolio to avoid tax is not free: it keeps the same market risk.
Do not quote pre-exit wealth as an after-tax result.
