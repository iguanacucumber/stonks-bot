# Corporate actions in paper/live state

Splits and cash dividends are paper-state accounting events. They are applied only
by the explicit `scripts/live.py corporate` command (or `bot/live/corporate.py`);
the broker adapter never invents them.

## Split

```bash
uv run python scripts/live.py corporate \
  --book monthly --market US --state-dir state/live \
  --kind split --ticker SMH --ratio 2 --session 2026-01-02
```

- Position quantity × ratio.
- Average cost and last price ÷ ratio.
- Cash unchanged.
- Event records `qty_before`, `qty_after`, cost before/after.

## Cash dividend

```bash
uv run python scripts/live.py corporate \
  --book monthly --market US --state-dir state/live \
  --kind dividend --ticker SMH --amount 1.00 --withholding 0.15 \
  --fx-rate 0.90 --session 2026-01-02
```

- Gross = quantity × amount per share.
- Withholding = gross × rate (0 by default; 0.15 for the US treaty example).
- Net cash = gross − withholding, added to the paper portfolio.
- State fields: `dividend_income` and `dividend_withholding` are **gross**
  native amounts; `dividend_income_eur` and `dividend_withholding_eur` are the
  same gross/withholding amounts at the supplied event `fx_rate`. Net EUR cash is
  `dividend_income_eur - dividend_withholding_eur`; do not store net in the income
  field. No later FX translation.

## Persistence and reconciliation

- Every event is stored in `LiveState.corporate_actions` (JSON) and in
  `state/<book>_<market>_corporate_actions.csv` (one row per `event_id`).
- `--read-only` refuses `corporate`; this command writes paper state.
- `reconcile` rebuilds the portfolio from fills in session order, applies
  corporate actions at the start of their session (before that session's fills),
  and compares positions/cash, gross/withholding EUR fields and event ids. It fails
  if a state event has no CSV ledger row, if a state event is malformed, or if an
  `event_id` appears twice.
- If a crash happens between the atomic state write and the corporate-action CSV
  append, `scripts/live.py corporate` repairs the CSV from state before deciding
  whether an event is a duplicate; `reconcile --repair-ledger` also syncs the
  corporate-action view. This is state-first crash recovery, never a fabricated
  event.
- Corporate actions do not change `realized_pnl`; dividends are income, splits are
  unit changes. A split is applied before same-session fills (open-of-session
  policy); it does not adjust already-open broker orders or a pending plan.

## Tests

```bash
uv run python tests/test_corporate_actions.py
```
