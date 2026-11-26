# Incremental data cache

Before 2026-11-26 every paper refresh called `build_panel(force=True)` and
`fetch_all(force=True)`: the US run requested **1,195 symbols**, reported **421**
with no Yahoo data, and rewrote the 2.2M-row panel even when nothing changed.
The 22:40, 23:30 and 23:45 jobs could repeat that work three times a day.

## Current path

`scripts/update_data.py`, `paper_daily.py --refresh`, `bot.cli ingest` and the
scheduled jobs all use the same incremental functions:

- `target_session()`: last completed US session, 16:30 ET cutoff.
- `update_symbols()`: one benchmark probe, then only symbols whose cached bar is
  behind the session.
- `update_panel()`: same rule for the S&P membership; keeps old rows and writes
  the parquet only when a bar was added or restated.
- Dead tickers with no cache back off for 7 days.
- Removed index members are not retried once their membership window is covered;
  410 historical symbols with no Yahoo history are skipped permanently.
- A closed/holiday session costs **one SPY request** and writes nothing.
- Split/dividend restatements are detected on the overlap and trigger one
  full-history refetch for that symbol only.

## Measured

Warm cache, 2026-11-26:

```text
== US ==
data: 2 symbols checked (2 requested), no new bars; panel unchanged
   SPY last bar 2026-11-25
== TECH ==
data: 52 symbols, 0 fetched, 52 cached/skipped @ 2026-11-25
   QQQ last bar 2026-11-25
```

The two US requests are PSKY and SNDK: current members whose Yahoo history stops
before the session. The panel stays at 784 tickers / 3,245 dates and is not
rewritten.

Next normal session: 503 current members need their one new bar; the 410
historical no-history symbols are not requested.

## Verification

`uv run python tests/test_data.py` covers:

- 16:30 ET cutoff and weekend rollback;
- an up-to-date cache makes zero requests;
- a holiday probes SPY and stops (no universe sweep);
- a dead ticker is not retried inside the 7-day backoff;
- a stale symbol is requested from its cached date;
- a restated overlap triggers exactly one full refetch;
- unchanged bars are not rewritten;
- a removed member and a dead historical symbol are not fetched;
- a current panel does zero fetches and no parquet write.

Scheduled: incremental data at **22:40 UTC**, TECH paper at 21:15/23:30/23:35,
US paper at **23:45 UTC**, all weekdays. The paper jobs then read the warm cache.
