# Assurance-vie wrapper: US monthly and balanced A6

> **Not tax or legal advice.** A planning model with explicit assumptions.
> The insurer's tariff, contract annex, fund domicile, leverage implementation
> and beneficiary clause decide the real result.

## What is compared

- **CTO:** merged EUR ledger with annual French PFU, loss carry, dividend
  withholding/PFU, 5 bps spread, IBKR-like tickets and 5%/yr financing.
- **Assurance-vie:** the same gross strategy return, but the contract is the
  tax wrapper: annual unit-linked fee 0.50/0.60/0.80%, 10 bps per trade,
  no annual PFU; at 12 years the gain is taxed at 24.7% (7.5% + 17.2%)
  after the €4,600 single abattement. Before 8 years, 30% PFU applies.
- **Estate:** 990 I for AV versus CTO French inheritance tax and the US
  estate-tax tail. US-situs exposure is removed in the AV model, but the
  contract can only implement 2.5x leverage if the insurer/fund allows it;
  this is not a free option.

## Results (€10k, 2015-01-02..2026-11-25, 12-year tax age)

| book | wrapper | annual fee | after-tax multiple | CAGR | maxDD | 2018 | 2022 | 5y median | 5y worst |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| max_growth | CTO | — | **66.17x** | 42.3% | -56.9% | -31.1% | -33.7% | 5.42x | 1.30x |
| max_growth | assurance-vie | 0.50% | **126.72x** | 49.7% | -61.1% | -36.6% | -40.4% | 7.83x | 1.88x |
| max_growth | CTO | — | **66.17x** | 42.3% | -56.9% | -31.1% | -33.7% | 5.42x | 1.30x |
| max_growth | assurance-vie | 0.60% | **125.24x** | 49.6% | -61.1% | -36.6% | -40.5% | 7.79x | 1.87x |
| max_growth | CTO | — | **66.17x** | 42.3% | -56.9% | -31.1% | -33.7% | 5.42x | 1.30x |
| max_growth | assurance-vie | 0.80% | **122.33x** | 49.3% | -61.1% | -36.8% | -40.6% | 7.71x | 1.85x |
| balanced | CTO | — | **36.56x** | 35.4% | -46.9% | -19.5% | -27.6% | 4.02x | 1.36x |
| balanced | assurance-vie | 0.50% | **66.48x** | 41.9% | -50.4% | -26.5% | -33.7% | 5.76x | 1.84x |
| balanced | CTO | — | **36.56x** | 35.4% | -46.9% | -19.5% | -27.6% | 4.02x | 1.36x |
| balanced | assurance-vie | 0.60% | **65.70x** | 41.7% | -50.4% | -26.6% | -33.8% | 5.73x | 1.83x |
| balanced | CTO | — | **36.56x** | 35.4% | -46.9% | -19.5% | -27.6% | 4.02x | 1.36x |
| balanced | assurance-vie | 0.80% | **64.18x** | 41.5% | -50.5% | -26.7% | -33.9% | 5.67x | 1.81x |

AV CAGR is after the terminal AV tax, not an annual tax; the CTO CAGR is
the merged EUR-ledger after-tax path. The 5-year columns are separate
window slices with a terminal-gain tax overlay (31.4% CTO, 30% AV before
8 years); they are comparable as approximations, not a second tax engine.

## Break-even fee

- **max_growth:** break-even annual fee **6.18%**. Above this, AV after tax is below the CTO.
- **balanced:** break-even annual fee **5.73%**. Above this, AV after tax is below the CTO.

## Estate at €660k

| beneficiaries | AV 990 I | CTO French inheritance | AV advantage | CTO US tax, treaty | CTO US tax, no treaty |
|---:|---:|---:|---:|---:|---:|
| 1 | **€101,500** | €110,962 | €9,462 | €0 | €226,151 |
| 2 | **€71,000** | €88,389 | €17,389 | €0 | €226,151 |

The AV estate advantage grows with the number of beneficiaries because
the €152,500 990 I allowance is per beneficiary. The CTO's French
inheritance tax uses €100,000 per child; its US estate tax is zero at this
size **only if** the France-US treaty pro-rata credit applies. Without the
treaty the CTO carries a material US-situs tail; AV removes it in the model.

## Verdict

**ACCEPT as an opt-in, not a default.** All three modelled AV fees beat the CTO after terminal AV tax over 12 years, and the 990 I estate route removes the US-situs tail. The gain is tax deferral and estate structure, not a better underlying strategy; it depends on the contract implementing the same gross return at the stated fee.

The break-even fee is the useful decision number: if the insurer's all-in
annual fee (including fund fees and any leverage wrapper) is above it, the
AV wrapper is not competitive on terminal wealth. Estate benefits can
still justify a smaller allocation, but they are a separate objective.

## Caveats and implementation constraints

- A standard French assurance-vie cannot borrow at contract level. The
  2.5x US monthly book can only go inside the wrapper through a leveraged
  fund/structured note or not at all; the model applies the wrapper to the
  same gross path and does not price that implementation. The balanced A6
  (2.05x before the trend cut) has the same issue.
- Annual fees compound; the insurer's fund-level fees may be additional.
  Transaction fees are modelled at 10 bps per trade; check the contract
  for arbitrage fees, minimum tickets and fund entry/exit costs.
- AV gains are taxed only on withdrawal; the model assumes one terminal
  withdrawal after 12 years. Partial withdrawals use the abattement each
  year and may be more tax-efficient; before 8 years the 30% PFU applies.
- 990 I applies to premiums paid before age 70; after 70 the €30,500 global
  regime taxes premiums, not accumulated gains. A spouse/PACS beneficiary
  is exempt. The beneficiary clause controls.
- The US-situs removal assumes the contract/underlying fund is not US-situs.
  Confirm the fund domicile and insurer structure; a French wrapper does not
  automatically cure a US-domiciled underlying.
- French inheritance tax, the €100k child allowance and the US treaty
  credit are as in `docs/estate_tax.md`; this is not legal advice.

## Reproduce

```bash
uv run python scripts/assurance_vie.py
uv run python tests/test_assurance_vie.py
```

Generated by `uv run python scripts/assurance_vie.py`.
