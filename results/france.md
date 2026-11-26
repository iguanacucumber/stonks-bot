# French investor audit: PFU/PEA, FTT, and broker fees

The core book is the shipped monthly portfolio (2.5x, daily 200-day cut,
month-end decision, next-open fill); the `tax` book is core-only with a
30% leverage band and HIFO lots. Prices and cash are in the asset's own
currency for the **native** rows (the bot as it runs); the EUR rows convert
the whole panel, including the trend, to EUR first. The difference is the
FX path changing the trend timing, not a tax effect. 5 bps spread is
charged on top of the broker tariff.

## French rules used

| item | rule |
|---|---|
| CTO capital gains | 34.5% before 2018, 30% 2018-2025, **31.4% from 2026** (12.8% IR + 18.6% social) |
| CTO losses | netted annually, carried forward **10 years** |
| PEA | IR-exempt after 5 years, **18.6% social** in 2026; no US stocks/ETFs |
| FTT | 0.2% to 2016, 0.3% 2017-Mar 2025, **0.4% since Apr 2025**, buys of large French shares only |
| Bourse Direct | Euronext 0.99/1.90/2.90/3.80 EUR by ticket, 0.09% above 4.4k; US **8.50 EUR/order** to 10k, then 0.09%; 0.036%/yr custody; 0.08% FX commission |
| Trade Republic | **1 EUR/order**, no custody, ~0.25% EUR/USD conversion |

Both brokers are charged 0.25% FX per side on USD names (Bourse Direct's
0.08% explicit commission plus the spread). Fixed fees use the account
size, so the €10k and €100k rows are not the same strategy. `fees` includes the FTT; `fees` and `tax paid` are cumulative costs already
inside `pre-tax`. `exit tax` is the remaining liability on the open book,
deducted from `after tax`.

## TECH book (the live paper market): CTO only

| market | wrapper | book | broker | account | signals | pre-tax | fees (incl. FTT) | FTT | tax paid | exit tax | **after tax** | CAGR | maxDD | benchmark after tax | vs bench |
|---|---|---|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| TECH | CTO | monthly | Bourse Direct | €10,000 | native | 10.9x | €46,042 | €0 | €37,175 | €26,729 | **8.27x** | 19.5% | -71% | 5.38x | +2.89x |
| TECH | CTO | monthly | Bourse Direct | €100,000 | native | 29.5x | €716,008 | €0 | €823,483 | €716,961 | **22.37x** | 29.9% | -70% | 5.38x | +16.99x |
| TECH | CTO | monthly | Trade Republic | €10,000 | native | 35.7x | €63,698 | €0 | €90,953 | €85,299 | **27.18x** | 32.1% | -69% | 5.38x | +21.80x |
| TECH | CTO | monthly | Trade Republic | €100,000 | native | 38.4x | €658,470 | €0 | €967,862 | €915,908 | **29.20x** | 32.9% | -69% | 5.38x | +23.81x |
| TECH | CTO | tax | Bourse Direct | €10,000 | native | 50.4x | €38,705 | €0 | €46,743 | €164,263 | **34.02x** | 34.6% | -70% | 5.38x | +28.64x |
| TECH | CTO | tax | Bourse Direct | €100,000 | native | 51.8x | €392,359 | €0 | €479,121 | €1,687,950 | **34.96x** | 34.9% | -70% | 5.38x | +29.58x |
| TECH | CTO | tax | Trade Republic | €10,000 | native | 57.2x | €31,815 | €0 | €50,569 | €184,183 | **38.80x** | 36.1% | -69% | 5.38x | +33.42x |
| TECH | CTO | tax | Trade Republic | €100,000 | native | 57.6x | €318,367 | €0 | €508,563 | €1,853,893 | **39.06x** | 36.2% | -69% | 5.38x | +33.68x |

## French book: CTO vs PEA

| market | wrapper | book | broker | account | signals | pre-tax | fees (incl. FTT) | FTT | tax paid | exit tax | **after tax** | CAGR | maxDD | benchmark after tax | vs bench |
|---|---|---|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| FR | CTO | monthly | Bourse Direct | €10,000 | native | 8.3x | €25,682 | €2,942 | €26,155 | €18,992 | **6.41x** | 16.6% | -70% | 2.13x | +4.28x |
| FR | CTO | monthly | Trade Republic | €10,000 | native | 16.1x | €24,923 | €4,850 | €42,941 | €35,850 | **12.47x** | 23.2% | -70% | 2.13x | +10.34x |
| FR | PEA | pea | Bourse Direct | €10,000 | native | 1.6x | €4,796 | €2,436 | €0 | €1,076 | **1.47x** | 3.2% | -59% | 2.34x | -0.87x |
| FR | PEA | pea | Trade Republic | €10,000 | native | 1.8x | €3,854 | €2,596 | €0 | €1,403 | **1.61x** | 4.0% | -58% | 2.34x | -0.73x |

## US book: CTO, Trade Republic, €100k

| market | wrapper | book | broker | account | signals | pre-tax | fees (incl. FTT) | FTT | tax paid | exit tax | **after tax** | CAGR | maxDD | benchmark after tax | vs bench |
|---|---|---|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| US | CTO | tax | Trade Republic | €100,000 | native | 59.4x | €335,547 | €0 | €533,366 | €1,907,024 | **40.35x** | 36.5% | -69% | 3.51x | +36.83x |

## EUR-signal sensitivity

| market | wrapper | book | broker | account | signals | pre-tax | fees (incl. FTT) | FTT | tax paid | exit tax | **after tax** | CAGR | maxDD | benchmark after tax | vs bench |
|---|---|---|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| TECH | CTO | tax | Trade Republic | €10,000 | eur | 35.0x | €22,387 | €0 | €45,682 | €104,118 | **24.59x** | 30.9% | -70% | 5.36x | +19.23x |

## What this changes

1. **Turnover reduction is worth more than the tax rate.** The core-only tax book turns €10k into **38.8x after tax** at Trade Republic. The monthly sleeve on the same capital through Bourse Direct ends at **8.3x** and through Trade Republic at **27.2x**. The sleeve's ~15x annual turnover generates ~2,100 orders in 12 years, against 178 for the core-only book; the fixed ticket and FX costs dominate the small account.

2. **31.4%, not 30%.** The 2026 PFU adds 1.4 pp (social contributions 18.6%). It is in the schedule; a flat 30% would overstate the result.

3. **The PEA is not a free lunch for this bot.** It cannot hold SMH or the US sleeve, and it cannot borrow. The French sleeve alone ends at **1.47x** after 18.6% social, **below the CAC benchmark's 2.34x**, versus **6.41x** for the levered CTO with the SMH core, even after 31.4% PFU. Use the PEA for low-turnover French dividend/quality books, not for this high-beta core.

4. **FTT is a real drag on the French sleeve:** €2,942 on a €10k account, paid on every purchase, win or lose. The US/TECH books pay none, but pay 0.25% FX instead.

5. **Broker choice is size-dependent.** At €10k, Bourse Direct's €8.50 US ticket leaves the monthly book 18.9x of terminal wealth behind Trade Republic; at €100k the gap shrinks. The core-only book is robust to both because it barely trades.

6. **FX timing matters if you translate the trend.** Converting the price series to EUR before the 200-day rule turns the €10k tax book into **24.6x** instead of **38.8x**. The bot's native USD signal (and a French investor following it) avoids that self-inflicted timing change; measure the P&L in EUR, but do not re-derive the signal in EUR.

## Caveats

- No dividend withholding: SMH pays a small dividend; the US-French treaty rate is 15% plus 18.6% social. The effect is small for this book.
- PEA contribution cap (€150k) and the 5-year clock are not binding in these runs; the PEA path assumes a withdrawal after 5 years.
- The CTO tax is settled at the start of the following year; the final year's realised tax and the unrealised gain are deducted as `exit tax`.
- Native rows translate the after-tax local-currency result at the terminal FX rate; the tax on the FX translation gain is not modelled separately. The EUR rows do model it, at the cost of changing the trend.
- No bid/ask beyond the 5 bps spread, no market impact, no borrow squeeze. A 2.5x book can be forced to deleverage in a gap; the backtest's leverage band is a simplification.

Generated by `uv run python scripts/france.py`.
