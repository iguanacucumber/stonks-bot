# Risk v3: profiles and configuration

This is the implementation note for the v3 risk workstream. The evidence,
pre-registration, failures and trial count are in `results/risk_v3.md`; this file
is the operator-facing summary.

## `max_growth` (default, unchanged)

The shipped US monthly book. No v3 rule passed the max-growth gate, so the default
is unchanged: US monthly, SMH core, 2.5x gross, 500 bps financing, daily 200-day
SMH cut to 50%, monthly calendar decision at the close, next-open fill, IBKR,
French CTO, HIFO, 5 bps spread + 2 bps FX + €1/order.

- Net-of-dividend EUR ledger: **61.64x**, CAGR 41.5%, maxDD **-56.67%**,
  2018 -30.58%, 2022 -33.16%, p5 2.53x, 2x-cost 55.12x.
- Headline terminal-FX after tax: 70.86x.
- TECH secondary: net 49.33x, maxDD -65.27%; no v3 rule passed there either.

Do not change the engine default on the basis of this workstream.

## `balanced` / A6 (preferred risk preference, `paper`)

**70% US monthly max-growth book + 30% SPY**, monthly rebalance, total target
gross 2.05x. The engine runs it as one account: a custom selector scales the
normal US target by 0.70 and adds 0.30/2.5 SPY before the engine's 2.5x
multiplier. The daily SMH 200-day cut still applies at portfolio level.

- Net-of-dividend EUR: **34.36x**, CAGR 34.7%, maxDD **-46.0%**, p5 **2.79x**,
  2018 -19.4%, 2022 -27.4%, 2x-cost **31.99x**.
- Control: a constant 2.13x US book makes 42.64x with p5 2.69x. A6 wins on the
  lower tail and drawdown, not terminal wealth.
- Exact parameterisation: `w_core = 0.70`, ETF leg = `SPY`, monthly calendar,
  `leverage=2.5`, `trend_ma=200`, `trend_scale=0.5`, IBKR/CTO/HIFO, 5 bps.
- Label: `paper`. It was added after the pre-registered A1-A5 set; it needs
  forward evidence before becoming a default.

## `balanced_vol` / V8 (lower-2022 alternative, `paper`)

Annualized 45% volatility target on the US monthly book:

- 20-day realized vol of SMH adjusted close, annualized by √252;
- raw gross `clip(0.45 / sigma, 0.5, 2.5)`;
- 10% no-trade band; 10% hysteresis (re-risk only when sigma <= 0.45 × 0.90);
- maximum 0.5x gross change per monthly decision;
- applied at the monthly decision; the daily 200-day cut still applies.

- Net-of-dividend EUR: **29.78x**, CAGR 33.1%, maxDD **-46.1%**, p5 **2.90x**,
  2018 -18.8%, 2022 **-13.7%**, 2x-cost **27.53x**.
- Control: constant 1.84x US book makes 29.97x with p5 2.66x. V8 wins on p5 only.
- Label: `paper`, post-hoc neighbour of the pre-registered V1-V4.

## Pre-registered families that failed

- V1-V6: vol targets 20/25/30/35% and the band/hysteresis neighbours. All cut
  drawdown but give up too much terminal wealth; the 30% primary is 9.90x.
- D1-D3: single-step drawdown ladders -25/-30/-35%. 20.85-32.79x, maxDD still
  -54% to -58%, and all lose to their constant-leverage controls.
- R1-R3: monthly SPY/dual/12m regime gates. 29.75-42.83x, maxDD still -56% to
  -57%; no improvement over the daily 200d cut.
- A1-A5: 60/40 and 70/30 SPY/trend blends plus dynamic vol parity. A1 25.21x
  (-43.0%) fails the 28x return gate; the trend sleeve is too weak (E0 1.20x).
- H1-H4: tail hedges. H1/H3 fail drawdown, H2/H4 are flat with no p5 improvement.
- T250/T300: 250/300-day trend cuts are worse than 200d on 2022 and maxDD.

## Caveats

- The EUR ledger uses trade-date FX, HIFO EUR lots and the year-dependent French
  rate (30% through 2025, 31.4% from 2026). Net numbers subtract a flat 31.4%
  PFU on modelled gross distributions; that is conservative for 2018-2025 and
  important for bond/cash sleeves (TLT/IEF/BIL yields 2.8-4.5%).
- The dividend drag is a scalar per ticker, not a point-in-time dividend ledger.
- Tail hedges are Black-Scholes models with no option quotes in the cache; they
  are not broker-accounted P&L.
- A6/V8 are post-hoc risk preferences and have no forward record. The max-growth
  default is unchanged.

## Commands

```bash
uv run python scripts/risk_v3.py --baseline
uv run python scripts/risk_v3.py --check
uv run python scripts/risk_v3.py
uv run python scripts/risk_v3.py --costs
uv run python tests/test_risk_v3.py
```
