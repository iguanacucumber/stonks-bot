# Risk profiles (v2 evidence)

All numbers: €10k, 2015-01-02..2026-11-25, IBKR CTO, French tax, EUR ledger.
`net` subtracts the risk workstream's dividend-tax scalar; the EUR-ledger column
is the annual tax-lot replay. These are liquidation values, not promises.

| profile | allocation | EUR ledger | net | maxDD | 2022 |
|---|---|---:|---:|---:|---:|
| **max_growth** (default) | 100% US monthly 2.5x | **66.17x** | 61.64x* | -56.9% | -33.7% |
| **balanced** (paper opt-in) | 70% US monthly book + 30% SPY, total gross 2.05 before the trend cut | **36.56x** | 34.36x* | -46.9% | -27.4% |
| **conservative** (paper opt-in) | 100% US monthly at 1.5x | **19.27x** | 18.51x* | -40.4% | -20.3% |

## Read this before choosing

* Net column is the risk workstream scalar dividend-tax stress; the EUR-ledger column uses exact cached dividend events. Prefer the EUR-ledger column.

- `max_growth` maximizes terminal wealth and is the default. It still loses 56.9%
  at the worst peak-to-trough in EUR terms. A -57% drawdown is not unusual; do not
  use this if you would sell at the bottom.
 - `balanced` gives up roughly half the terminal wealth for about 10 pp less
  drawdown and a better 2022. It is the better fit for a real human who needs to
  stay invested. It is **post-hoc** in the v2 sweep (A6), so label it `paper` and
  do not present it as a validated default.
- `conservative` is mostly a lower leverage dial: 1.5x US. It is simple and
  transparent, but it is not diversifying; it still holds the same SMH core.
- The equity-only cross-asset trend alternative was rejected (1x OOS 1.97x EUR
  ledger vs QQQ 2.88x). It is not a balanced substitute.
- TECH monthly remains the forward-validation book (EUR ledger 52.98x, -65.6%
  maxDD). It is not the default.

## Implementation

- `max_growth`: current CLI default (`--country fr`, US, monthly, 2.5x).
- `balanced`: `python -m bot.cli backtest --country fr --profile balanced`
  (`bot/profiles.py`). It blends 70% of the primary book with 30% SPY and scales
  the total to 2.05 gross before the daily 200-day cut. Post-hoc and `paper`;
  not a default.
- `conservative`: `--leverage 1.5` on the US monthly book. It is a leverage choice,
  not a new strategy.

Open risks: post-hoc balanced selection, dividend-tax scalar, static-49 TECH
survivorship, US 16.2% missing price history, and the usual 2.5x gap risk.
