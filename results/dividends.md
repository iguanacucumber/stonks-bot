# Dividends and withholding tax: finishing the EUR ledger

The EUR ledger now starts from the same USD trades and adjusted closes as the
research engine, then charges dividend tax explicitly. Adjusted closes reinvest
gross dividends; the ledger therefore deducts only the tax by default instead of
adding the gross dividend a second time.

## Method

- Yahoo chart `events=div` per symbol, cached under `cache/dividends/`.
- Ex-date mapped to the first ledger session on or after the event; amount is
  gross native-currency dividend per current share (Yahoo already presents it on
  the current split basis).
- US treaty withholding at 15% of gross, deducted at the event.
- French PFU on gross dividends at the year-dependent rate: 30% through 2025,
  31.4% from 2026. The 15% withholding is a credit against the PFU, so the total
  is the PFU, not PFU + 15%. Withholding above the PFU is not refunded.
- Dividend tax is separate from capital-gains PFU and does not consume the
  capital-loss carry. A loss carried forward still offsets future capital gains.
- Default `dividend_reinvest=True` keeps the gross dividend inside the adjusted
  total-return position. `dividend_reinvest=False` moves the gross amount to cash
  with a synthetic share reduction (no fee, no realized gain); the tests cover it.
- PEA wrapper skips US dividend tax entirely (a PEA cannot hold US names).

Acceptance written before this report: US/TECH/CORE should move only by a small
documented drag; a bond-heavy TLT/IEF/BIL/GLD book should show a gross yield above
3%/yr and a net PFU drag around 1.0-1.5%/yr in the current high-yield window.

## Full-window corrected results

| book | base EUR x | corrected EUR x | change | base CAGR | corrected CAGR | annual drag | gross yield (avg NAV) | tax drag (avg NAV) | gross dividends | withholding | PFU top-up | total dividend tax |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| US monthly | 66.72x | **66.17x** | -0.8% | 42.4% | 42.3% | 0.10 pp | 0.74%/yr | 0.23%/yr | €14,953 | €2,243 | €2,312 | €4,554 |
| TECH monthly | 53.45x | **52.98x** | -0.9% | 39.8% | 39.7% | 0.10 pp | 0.74%/yr | 0.23%/yr | €12,491 | €1,874 | €1,948 | €3,822 |
| CORE tax | 49.32x | **48.80x** | -1.1% | 38.9% | 38.7% | 0.12 pp | 0.75%/yr | 0.23%/yr | €13,652 | €2,048 | €2,119 | €4,167 |

`base EUR x` is the merged EUR ledger without dividend events (gross dividends
implicit in adjusted closes). `corrected EUR x` applies withholding, PFU and the
foreign-tax credit. `annual drag` is the difference in EUR CAGR, not the gross
dividend yield. Yields are divided by average daily EUR NAV, not initial capital;
a 66x book would make an initial-capital yield look artificially large. The
50/25/15/10 cross-asset book is monthly rebalanced at 1x.

## Cross-asset high-yield check

| book | base EUR x | corrected EUR x | change | base CAGR | corrected CAGR | annual drag | gross yield (avg NAV) | tax drag (avg NAV) | gross dividends | withholding | PFU top-up | total dividend tax |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Cross-asset 50/25/15/10 | 1.05x | **0.96x** | -8.4% | 0.4% | -0.3% | 0.74 pp | 2.39%/yr | 0.74%/yr | €2,838 | €426 | €456 | €882 |
| Cross-asset 2023-2026 | 1.03x | **0.99x** | -3.9% | 0.7% | -0.3% | 1.02 pp | 3.46%/yr | 1.05%/yr | €1,314 | €197 | €201 | €398 |

The full-window cross-asset drag is lower because 2015-2021 had low bond/cash
yields. In 2023-2026 the same book earns a gross distribution yield above 3%/yr
and the net PFU drag is about 1%/yr, in the expected range. BIL has Yahoo dividend
events only from December 2016; earlier cash yield is therefore understated.

## Withholding credit, not double tax

For every book below, the `total dividend tax` equals the PFU on gross dividends
when the 15% withholding is creditable. If the credit were ignored, the extra tax
would be the withholding amount again:

| book | total dividend tax | no-credit tax | extra if double-taxed | no-credit x |
|---|---:|---:|---:|---:|
| US monthly | €4,554 | €6,797 | €2,243 | 65.94x |
| TECH monthly | €3,822 | €5,696 | €1,874 | 52.79x |
| CORE tax | €4,167 | €6,215 | €2,048 | 48.59x |
| Cross-asset 50/25/15/10 | €882 | €1,307 | €426 | 0.92x |
| Cross-asset 2023-2026 | €398 | €596 | €197 | 0.97x |

The difference is the same 15% that was already withheld: the French PFU is not
charged a second time on the same gross dividend.

## Reading

- SMH/TECH and US monthly move only a little: their gross distribution yields are
  low relative to the equity volatility and the existing PFU/exit-tax drag.
- The core-only tax book is mostly one SMH position and shows the same small drag.
- The bond-heavy book is the real test. Its gross yield is material, and the
  year-dependent PFU removes roughly a third of it every year. A strategy comparison that ignores
  dividends flatters TLT/IEF/BIL and any future income sleeve.
- The exit tax still applies to unrealized capital gains; dividend tax is paid
  along the path and is not part of the terminal exit tax.

## Caveats

- Yahoo dividend events are used as-is; no dividend accrual between ex-date and
  payment date, no withholding on non-US names in this report.
- `dividend_reinvest=False` is an accounting transformation on an adjusted-close
  series. It moves the gross amount to cash at the prior adjusted mark, so the
  price-return path is approximate by the return earned on the distribution.
- The cross-asset book is a static example, not a recommended allocation. It has
  no trend filter and no leverage.
- The data cache is not committed; `--no-fetch` reproduces from an existing
  `cache/dividends/` snapshot. The report prints fetch failures instead of
  silently treating them as zero dividends.

## Reproduce

```bash
uv run python scripts/dividends.py
uv run python scripts/dividends.py --no-fetch
uv run python tests/test_dividends.py
uv run python tests/test_eur_ledger.py
```

