# v10 EUR ledger audit

Branch `v10/review-eur`. Audit of `bot/eur_ledger.py`, `bot/dividends.py`,
`bot/france.py`, tax/FX helpers and the two EUR reports. No strategy or default
changed. All numbers are €10k, IBKR CTO, 2015-01-02..2026-11-25, EUR ledger.

## Verdict

The year-dependent PFU, exit tax, HIFO lots, 15% withholding credit, 10-year loss
carry, trade-date FX and dividend/capital-loss separation all reproduce. One
material accounting bug was found and fixed: **reinvested dividends were taxed
once as dividends and again as future capital gains**.

## Findings

### H1 — reinvested dividend double tax (high, fixed)

Adjusted closes already reinvest the gross dividend. `bot/eur_ledger.py` deducted
the dividend withholding/PFU from cash but did not increase the lot basis. A later
sale therefore counted the same gross dividend again as price gain.

Synthetic proof (`tests/test_dividends.py::test_reinvested_dividend_basis`):
€10 capital, buy 1 share at €9, €1 gross dividend, exit at €10.

| treatment | exit tax | after tax |
|---|---:|---:|
| before: no basis increase | €0.314 | €10.372 |
| after: basis +€1 | €0 | €10.686 |

The fixed path charges only the €0.314 dividend tax. Before the fix the same €1
was taxed twice: once at the dividend event and once through the capital gain.

Fix: when `dividend_reinvest=True`, add the gross dividend to the held lots'
tax basis (`dividend_basis_added` is exposed on the result). `dividend_reinvest=False`
keeps the existing synthetic cash/share-reduction treatment. Regression test added.

### Corrected full-window numbers

| book | base, no dividend events | before fix | **after fix** | change vs before |
|---|---:|---:|---:|---:|
| US monthly | 66.72x | 66.17x | **66.69x** | +0.52x |
| TECH monthly | 53.45x | 52.98x | **53.42x** | +0.44x |
| CORE tax | 49.32x | 48.80x | **49.27x** | +0.47x |

`base` is the EUR ledger without dividend events. `before fix` is the previous
report. `after fix` is the corrected dividend report (`results/dividends.md`).
The dividend-only drag is now small for the equity books because the basis
increase offsets most of the future capital-gain effect; the tax is shifted from
exit tax to an annual cash cost, not duplicated.

### M1 — downstream hard-coded baselines updated (medium, fixed)

`scripts/reproduce.py`, `scripts/acceptance.py`, `scripts/release_check.py` and
their checks still expected 66.17/52.98/48.80. They now expect the corrected
66.69/53.42/49.27 so release checks do not fail on the fixed accounting.

### L1 — duplicated French rate helper (low, verified)

`bot/eur_ledger.french_rate` duplicates `bot.backtest.fr_rate`. Values agree
(34.5% pre-2018, 30% 2018-2025, 31.4% 2026); a test asserts the schedule. Keeping
the helper avoids importing the full backtest engine into the reporting overlay.

## Checks performed

- PFU by realization year: 2015/2017 34.5%, 2018-2025 30%, 2026 31.4%; final-year
  realized gains are settled in the exit tax at the final year's rate.
- Exit tax: final unrealized + final-year realized gains − remaining loss carry,
  at the final-year rate. New test covers a 2026-only realization.
- HIFO: highest-basis lots sold first; regression test.
- Dividend withholding: 15% US treaty withheld, credited against the PFU; total
  dividend tax = max(15%, PFU), never PFU + 15%; excess withholding is not refunded.
- Dividend vs capital loss: dividend PFU is paid even while a capital loss is
  carried; losses offset future capital gains only.
- Loss carry: 10-year expiry, annual netting, remaining carry applied to exit.
- FX: trade-date EUR basis, EUR cash, EUR marks; FX gains/losses enter the same
  annual capital-gains bucket. Synthetic FX tests cover 0.5→0.25 marks.
- Fees/financing/leverage/cash: per-side spread + FX + broker + fixed ticket,
  5% financing on negative EUR cash; no change.
- Cross-asset dividend sensitivity: gross yield 2.39%/yr full window and
  3.46%/yr in 2023-2026; dividend drag 0.55 pp and 0.78 pp after basis fix.
  Bond/cash sleeves remain the material dividend-tax case.

## Reproduction

```bash
uv run python scripts/eur_ledger.py
uv run python scripts/dividends.py --no-fetch
uv run python tests/test_eur_ledger.py
uv run python tests/test_dividends.py
```

Updated reports: `results/eur_ledger.md`, `results/dividends.md`.

## Files

- `bot/eur_ledger.py` — basis increase; `dividend_basis_added`.
- `tests/test_eur_ledger.py` — PFU schedule/exit-year checks.
- `tests/test_dividends.py` — reinvested-dividend basis regression.
- `scripts/eur_ledger.py`, `scripts/dividends.py` — method text and regenerated reports.
- `scripts/reproduce.py`, `scripts/acceptance.py`, `scripts/release_check.py` — corrected baseline expectations.

No default strategy change. No merge.
