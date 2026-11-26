# Research protocol (v2)

This is the minimum bar for any strategy claim in this repo. If a report does not
follow it, it is a hypothesis, not evidence.

## Experiment record

Every experiment writes one row to `results/trials.csv` (or a branch-local equivalent):

`id, date, branch, hypothesis, universe, start, end, params_json, n_trials, metric,
value, oos_value, cost_model, verdict, notes`

Pre-register the hypothesis and the pass/fail rule before the sweep. The trial count
includes every variant you looked at, not just the winner.

## Splits

- Long horizon: train 2015-2019, validate 2020-2021, test 2022-2026. Then roll the
  whole window by one year and repeat. Report the concatenated test path.
- Short horizon: at least 3 expanding walk-forward folds; never tune on the test fold.
- Report full-window, 2018, 2022, and the worst 12-month window separately.
- 5-year rolling windows: report all windows, the median, the worst, and the share that
  beat the benchmark after tax.

## Costs

Default French CTO model: 31.4% PFU after 2026, broker commission, FX bps, 5%/yr
financing on gross leverage, 5 bps spread on stock trades, ETF spread as configured.
Short-horizon experiments must include at least 5 bps round-trip cost per trade and a
sensitivity table at 2x costs. A strategy that dies at 2x costs is not real.

## Statistics

- Bootstrap the daily returns (stationary or block bootstrap, 10k paths) and report the
  5th percentile terminal multiple and max drawdown.
- Deflated Sharpe ratio or CSCV probability of backtest overfitting when more than ~10
  variants were tried.
- Do not report a single best path as the expectation.
- Any rule selected because it fixes 2022 is overfit unless it also helps the full
  window and at least one independent crisis.

## Verdicts

`accept` — walk-forward after-tax win, 2x-cost survival, acceptable drawdown, clear
capacity/execution path.
`paper` — promising but forward evidence missing; log it, do not make it default.
`reject` — fails any gate; keep the report so nobody repeats it.
