# Monitoring dashboard

`scripts/dashboard.py` renders one static page for the paper live loop and the
committed research evidence. It is read-only: no server, JavaScript, external
assets, network calls or order execution.

## Quick start

```bash
uv run python scripts/dashboard.py                 # writes results/dashboard.{html,md}
uv run python scripts/dashboard.py --check         # render in memory, no writes
uv run python scripts/dashboard.py --json          # inspect the collected model
uv run python scripts/dashboard.py --html /tmp/d.html --markdown /tmp/d.md
uv run python scripts/dashboard.py --demo          # labelled synthetic example
```

`--check` is cheap and safe on a clean checkout: missing cache/state/results
inputs become warnings, not a traceback. CI uses it as a smoke test.

The committed example is `results/dashboard.html` (and `results/dashboard.md`),
generated with `--demo`. Its yellow banner says **SYNTHETIC DEMO**; it is not
live evidence. Generate a real page after a paper run with the default command.

## What one page shows

- book/market, status, last run and next NYSE session;
- native equity, EUR equity when available, drawdown, gross exposure and peak;
- targets versus positions and any open order intents;
- forward gate (`paper_audit.csv`) and reconcile status;
- benchmark/data freshness and FX date/rate;
- warnings such as missing state/ledger, stale benchmark, missing FX, an overdue
  pending plan or corrupt state;
- research evidence: EUR-ledger baseline, data-quality verdict, survivorship
  caveat and trial-registry row count.

The EUR-ledger column is the real-life number; terminal-FX is a legacy upper
bound. The dashboard does not recompute either one.

## Inputs and degradation

| input | use | if missing/corrupt |
|---|---|---|
| `state/live/*.json` | live state, targets, risk, pending plan | warning; page falls back to `forward_status.md` |
| `state/live/*.csv` | last equity/cash/gross/gate row, ledger count | warning; values shown as `—` |
| `results/forward_status.md` | book rows, live/reconcile/gate fallback | warning; state books still render |
| `results/paper_audit.csv` | verified-date gate per book/market | warning; gate shows `WAIT 0/5` |
| `results/eval_*.json` | baseline, data quality, survivorship, headline | warning; research section is partial |
| `cache/bars/SPY.csv` | global data-freshness check | warning; page still renders |

No input is modified. A corrupt state JSON is shown as `corrupt` with the parser
error, not silently dropped. A missing state directory is normal for a fresh
paper book and is only a warning.

## Alerts

`--alert` reuses `bot.ops.alerts` and only collects findings at `WARN`/`FAIL`
level. `INFO` gate progress and `OK` checks never trigger an alert.

```bash
uv run python scripts/dashboard.py --alert              # dry-run payload
uv run python scripts/dashboard.py --alert --send       # configured channels only
```

Alerting is off by default. Configure channels through the same environment as
the ops tools (never in git):

| channel | variables |
|---|---|
| webhook | `STONKS_ALERT_WEBHOOK_URL` |
| Telegram | `STONKS_ALERT_TELEGRAM_BOT_TOKEN`, `STONKS_ALERT_TELEGRAM_CHAT_ID` |
| email | `STONKS_ALERT_EMAIL_TO`, `STONKS_ALERT_EMAIL_FROM`, `STONKS_SMTP_HOST` |

Optional SMTP variables: `STONKS_SMTP_PORT`, `STONKS_SMTP_USER`,
`STONKS_SMTP_PASSWORD`, `STONKS_SMTP_TLS`. With no channel configured the
command prints `alerting off` and sends nothing. `--send` is explicit; tests and
CI use the dry-run path only.

## HTML/Markdown safety

The HTML is self-contained CSS, no external fonts/images/scripts. Every dynamic
value (book, ticker, warning, source path) is HTML-escaped. Markdown escapes table
pipes and newlines. The page includes no secrets, broker credentials or absolute
temporary paths in the committed synthetic example.

## Tests

```bash
uv run python tests/test_dashboard.py
uv run python scripts/dashboard.py --check --demo
```

Coverage: forward-status table parsing, unique verified-date gate counts,
missing/corrupt input degradation, eval fallback, HTML escaping, synthetic
banner, WARN/FAIL alert filtering, static CLI writes and `--check` with no
network/cache/state writes.

## Limitations

- The dashboard is a view, not a health authority: `ops.py doctor` remains the
  blocking check for cache/state/permissions/FX.
- The legacy forward paper track and the live paper state are separate; the page
  shows both with their own gate and labels.
- Drawdown is the recorded live mark/drawdown, not the research EUR-ledger
  drawdown. The two are not directly comparable.
- No alert retry/queue: a failed channel is reported by `--alert`; operators should
  check the output and use the scheduler/runbook.
