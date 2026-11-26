# v10 data cache / point-in-time review

Date: 2026-11-26. Base `c690df5`. Cache snapshot: **2026-11-25**.
Scope: `bot/data.py`, `bot/markets.py`, `bot/universe.py`, cache metadata,
`tests/test_data.py`, `scripts/update_data.py`, survivorship evidence.
No network was used for the audit; no data row was fabricated.

## Verdict

**PASS after fixes.** The committed cache is internally consistent at the
2026-11-25 snapshot. The main data-session bug was `target_session` ignoring NYSE
holidays and half-day closes; it is fixed and tested. Survivorship is still a
disclosed upper-bound/bias issue, not a clean estimate.

## Current cache / PIT snapshot

- Membership history: **1,353,267 rows, 2,724 snapshots, 1,214 unique tickers**,
  source dates 1996-01-02..2026-10-30. No future membership snapshot is used.
- `members_on(2014-12-31)` returns **499** names; `members_on(2026-10-30)` returns
  **503** names.
- Data-quality audit (committed snapshot): **1,196 symbols, 3,515,514 rows, 0 hard
  failures, 196 warnings**, verdict PASS for required symbols. The 21 stale symbols
  are delisted/foreign names; no required benchmark is stale.
- Survivorship: TECH **28/49** names were S&P members at the 2015 start, 15 joined
  later, 6 never; US **128/789** unique 2014+ members (16.2%) have no usable price.
  The report explicitly calls the S&P-dated sensitivity a sensitivity, not a lower
  bound. No number was upgraded to forward evidence.

## Findings

### F1 — HIGH — `target_session` ignored holidays and half-day closes

`bot.data.target_session` only subtracted a business day before 16:30 ET. At
2026-11-26 22:00 UTC (holiday) it returned **2026-11-26** instead of 11-25; at
2026-11-27 13:30 ET (half-day close) it returned **11-26** instead of 11-27. Data
updates could therefore target a holiday/old session and skip the half-day close.

Repro (before fix):

```text
2026-11-26T22:00Z -> 2026-11-26
2026-11-27T18:30Z -> 2026-11-26
```

Fix: `target_session` now delegates at call time to
`bot.live.calendar.target_session`, which knows NYSE holidays, 13:00 half-day
closes and the 30-minute publication delay. It keeps a defensive fallback for
standalone data use. Tests cover holiday 11-26 and half-day 13:29/13:30.

### F2 — MEDIUM — incremental restatement check only compared close/adjclose

`_overlap_changed` ignored corrected open/high/low/volume on overlapping dates.
Those values could remain stale outside the recent fetch window even though
`_bars_equal` knows how to compare them.

Repro: take identical old/new bars with only `high`/`volume` changed;
`_overlap_changed(old, corrected)` returned `False` before the fix.

Fix: compare all OHLCV fields (volume with a 1-share tolerance, prices with
relative tolerance). A restated overlap now triggers the full refetch path, which
keeps old/new history consistent. Regression added to `tests/test_data.py`.

### F3 — MEDIUM — zero raw close could create infinite adjusted OHLC

`_parse` dropped non-positive adjusted closes but allowed `close == 0` with a
positive `adjclose`. `_panel_rows` divides OHLC by `close`, producing infinite
adjusted open/high/low.

Repro: parse a two-row response with one `close=0` and `adjclose>0`; the bad row
was retained.

Fix: require **both** raw close and adjusted close to be positive. Regression
tests the parser and keeps only the valid row.

### F4 — INFO — survivorship remains explicit, not fixed

The static TECH-49 list is chosen today and is still survivor-biased; the US PIT
panel drops 16.2% of 2014+ members because Yahoo has no delisted history. The
audit report already labels both. This is a data-coverage limitation, not a
fabrication; do not quote the TECH stock-sleeve result as an expected return.

## Cache invariants verified

- Current symbol cache: no fetch when last bar is at the target; one fetch only
  for stale symbols; closed/holiday market probes SPY once and holds the panel.
- Incremental overlap: new values win on overlapping dates; old rows fill dates
  the recent request did not return.
- Restatement: full refetch once; failed/truncated/empty full responses keep the
  old consistent cache rather than splicing or deleting history.
- Missing tickers: 7-day backoff; delisted names are not retried forever.
- Writes: per-symbol CSV, panel parquet, missing JSON/text and membership cache
  use temporary files + `os.replace`; unchanged content is not rewritten.
- Duplicate dates are dropped on parse; the panel drops duplicate
  `(date,ticker)` rows; gaps/stale bars are reported by the evaluation audit.

## Commands / tests

```bash
uv run python tests/test_data.py
uv run python tests/test_evaluation.py
uv run python tests/test_core.py
uv run python scripts/reproduce.py baseline --no-write
```

All `tests/test_*.py` pass locally after the fixes. `tests/test_data.py` uses
temporary cache directories and injected fetch/parse functions; no Yahoo/network
was touched.

## Residual risks

- The cache snapshot is 2026-11-25; later sessions need an explicit updater run,
  never a fabricated bar.
- Yahoo historical data can be restated again; the full-refetch path protects
  consistency but cannot recover names Yahoo never served.
- `_download` for membership/current metadata is not checksummed; the local
  stamp detects source-file changes, not remote-tag drift.
- Survivorship remains material for the static TECH sleeve and the US PIT price
  coverage; it is disclosed, not solved.
