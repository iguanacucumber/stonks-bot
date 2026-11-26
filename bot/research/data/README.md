# PIT Nasdaq-100 data

`nasdaq100_history.csv` is a committed snapshot of complete Nasdaq-100
constituent lists on effective change dates, 2007-02-01 through 2026-05-18.

Source: https://github.com/thuningxu/sp500nq100
(`nasdaq100_components_history.csv`), which parses the Wikipedia
"Nasdaq-100 yearly changes" tables. The file is used read-only by
`bot/research/pit_universe.py`; tests and reports never fetch it.

The last snapshot is 2026-05-18 and is applied through the 2026-11-25 data end.
That six-month staleness is documented in `results/pit_tech.md`; it is not a
look-ahead. Historical constituents without cached Yahoo bars are excluded and
counted in the coverage audit (residual delisting bias).
