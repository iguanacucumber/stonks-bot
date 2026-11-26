# v11 real-money approval gate and preflight

**Status: BLOCKED / paper-only.** The user decision on 2026-11-26 was to keep the
bot paper-only. No live access was approved, no credential is requested, and this
branch contains no real-money broker adapter or order path. The v11 work adds only
a read-only approval/preflight gate and documentation; it does not enable trading.

## What is now gated

A future live session would have to pass
`uv run python scripts/real_money_preflight.py` for one exact config. The command is
read-only: no network, no broker, no secret read/print, no state write, no order.
Every check fails closed:

| check | requirement |
|---|---|
| approval | `state/live/APPROVAL.json` exists, is unexpired, covers the book/market/capital and pins the exact config SHA-256 (and optional plan SHA-256) |
| forward_gate | selected book/market is `PASS 5/5` in `results/forward_gate.md` |
| forward_audit | at least 5 distinct verified dates for that book/market in `results/paper_audit.csv` |
| release_check | `results/release_check.md` overall `PASS` |
| credentials_env | `IBKR_REAL_ACCOUNT` is present and is `U...`, not `DU...`; value is never printed |
| read_only_default | `IBKR_REAL_READ_ONLY` is explicitly `true`; absence is not read-only |
| risk_rails | drawdown breaker, gross/turnover/order/participation and stale/pending limits are no looser than the shipped defaults |
| pending_blocker | state has no blocked plan, pending risk cut, overdue plan or config-signature mismatch |
| real_money_execution_path | always fails in this paper-only build: no real adapter/order path |

The approval artifact is deliberately strict. Missing file, expired or future-dated
timestamps, naive timestamps, unknown fields, out-of-scope book/market, capital above
the approved maximum, or a config/plan hash mismatch are blockers. Creating a valid
approval file still cannot pass the preflight because the execution-path check is
permanent here. The default artifact path is gitignored.

## Current preflight result

Command:

```bash
uv run python scripts/real_money_preflight.py --book monthly --market US
```

Result on 2026-11-26: **exit 2, FAIL** (six blockers).

```text
REAL-MONEY PREFLIGHT FAIL  book=monthly market=US capital=10,000.00
  [FAIL] approval: missing approval artifact: state/live/APPROVAL.json
  [FAIL] forward_gate: monthly/US forward gate is WAIT 0/5; need PASS 5/5
  [FAIL] forward_audit: monthly/US has 0/5 distinct verified dates
  [OK  ] release_check: release check PASS at c653d74 generated 2026-11-26T19:04:07+00:00
  [FAIL] credentials_env: IBKR_REAL_ACCOUNT is not set; no real-account access is approved
  [FAIL] read_only_default: IBKR_REAL_READ_ONLY must be explicitly true; absence is not read-only
  [OK  ] risk_rails: shipped rails or tighter
  [OK  ] pending_blocker: no blocked/pending/overdue state
  [FAIL] real_money_execution_path: paper-only build: no real-money broker adapter or order path exists; the user did not approve live access
```

The TECH track is also blocked: forward gate currently 1/5, and the same approval,
credential, read-only and execution-path checks fail.

## Blockers, in order

1. **No human approval artifact.** `state/live/APPROVAL.json` does not exist. It must
   name the approver, approval/expiry timestamps, maximum capital, allowed book and
   market, and the full config hash. No approval is requested for this paper-only
   release.
2. **Forward gate is not 5/5.** US is 0/5, TECH is 1/5, tax TECH is 0/5. The next
   required session is 2026-11-27; the gate is WAIT, not an edge. Do not manufacture
   audit rows.
3. **No verified forward audit count.** The US book has 0 distinct verified dates in
   `results/paper_audit.csv`, so the audit cross-check independently blocks.
4. **No real-account environment.** `IBKR_REAL_ACCOUNT` is unset. This build has no
   code that would connect with it; absence is reported rather than prompted for.
5. **Read-only is not explicitly enabled.** `IBKR_REAL_READ_ONLY` is unset; absence
   is treated as unsafe, never as a read-only default.
6. **No real-money execution path.** This is the permanent v11 boundary: paper
   broker is the default, IBKR remains DU paper/read-only, and no live adapter,
   submit/cancel/replace path or credentials exist.

The shipped safety rails and pending-state check pass today. The recorded release
check is overall PASS (`37/37` test scripts, acceptance and reproduce pass; forward
gate remains the expected WAIT), not a live-trading approval.

## User decision and inputs

The user did **not** approve live access. No credentials, account id, token or
password should be sent or committed. The paper/DU adapter remains the only broker
path and the deterministic `paper` broker remains the default.

For historical clarity, a future live change (not requested now) would require the
human to provide, out of band:

- a named approver and a short expiry window;
- a maximum capital and the allowed book/market;
- the exact `--print-config-hash` value for the intended config;
- optionally the exact plan SHA-256;
- a real `U...` account id and explicit read-only mode through the documented env
  names, with the gateway/session credentials handled outside this repository.

None of those values are present in this branch or needed for the current
paper-only release. Adding the real adapter remains a separate reviewed change with
a kill switch, small capital cap and manual confirmation.

## Tests and safety evidence

`uv run python tests/test_real_money_approval.py` covers:

- config/plan SHA-256 stability and sensitivity (state dir excluded);
- missing, malformed, unknown-field, naive/future/expired approval artifacts;
- book/market/capital scope, config hash and plan hash mismatches;
- forward gate PASS/WAIT/BLOCKED, 5/5 and 4/5, missing files;
- audit distinct verified dates and release-check PASS/WAIT parsing;
- fake `U1234567` / `DU1234567` env ids, explicit read-only, loose rails;
- blocked/pending-cut/overdue/signature-mismatch state;
- a fully valid synthetic fixture that still fails at the paper-only execution path;
- CLI JSON output and exit code `2`.

No test opens a network connection, contacts IBKR, reads a secret or uses a real
account id. `bot/live/brokers/ibkr.py` and the paper/DU adapter are unchanged.

## Files

| path | role |
|---|---|
| `bot/live/approval.py` | strict approval schema, config/plan hashes, expiry/scope/hash checks |
| `bot/live/preflight.py` | read-only readiness checks and report |
| `scripts/real_money_preflight.py` | CLI (`--json`, `--print-config-hash`) |
| `tests/test_real_money_approval.py` | fake-env/no-network tests |
| `docs/live.md` | operator boundary and schema |
| `RELEASE.md` | release status and file map |

No real order was sent, no broker was contacted, and no approval or credential was
created by this workstream.
