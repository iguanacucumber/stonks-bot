# v10 H — acceptance/CI/release-check audit

Base `c690df5`, branch `v10/review-ci`, issue #94. Scope: `scripts/acceptance.py`,
`scripts/release_check.py`, `.github/workflows/tests.yml`,
`tests/test_acceptance.py`, `tests/test_release_check.py`, `docs/acceptance.md`.
No network, no real orders, no repo state/results writes.

Verdict: **PASS after fixes**. The audit found two false-PASS paths, a CI
documentation gap, a partial-cache false full-run risk, secret-scan false
positives, and a temp-state leak. All are fixed and covered by tests.

## Findings

| # | Severity | Finding | Reproduction (before fix) | Fix |
|---|---|---|---|---|
| 1 | **High** | `release_check.check_forward_gate` accepted a missing `tax/TECH` row as WAIT. `EXPECTED_GATES` included tax, but missing expected rows were only fatal for monthly US/TECH. | Delete the `tax/TECH` row from a synthetic `forward_gate.md`; `check_forward_gate(..., EXPECTED_GATES)` returned `('WAIT', ...)` instead of FAIL. | Require every expected gate key (US, TECH, tax) and test the deleted-row case. |
| 2 | **High** | CI did not run `acceptance.py` at all, although `docs/acceptance.md` said it did. The reproduction guard checked only `SPY.csv`, `SMH.csv` and `panel.parquet`. | Read `.github/workflows/tests.yml`: no acceptance step; guard passed with only three files, then `headline` needed the full TECH set and could fail or run on a partial cache. | Added `acceptance.py --check-cache` (full required bars + panel + membership + snapshot 2026-11-25), a cache-aware acceptance step, and a strict full-cache-only reproduction step. |
| 3 | **High** | `release_check` treated an acceptance payload with `ok=true` and `SKIP` rows as a full pass; it only rejected `FAIL` rows. A skipped acceptance could therefore produce a release PASS. | `validate_acceptance_payload({"ok": True, "checks": [{"status": "SKIP"}]})` was not rejected by the old inline validator. | New `validate_acceptance_payload` requires every check to PASS; regression test. |
| 4 | **Medium** | Secret scan false positives: documented placeholders such as `STONKS_SMTP_PASSWORD=changeme` and `<token>` were reported as secrets. Source literals containing `\n` could also swallow the next assignment. | `scan_for_secrets` on a file with `STONKS_SMTP_PASSWORD=changeme` returned the path. | Added placeholder detection (`changeme`, `example`, `<...>`, `${...}`, etc.) and stop assignment values at backslashes; tests cover clean placeholders and a real token. |
| 5 | **Medium** | `release_check.run_checks` hardcoded `/tmp/opencode` for its state dir and never removed the directory when it created one. | A full run left `stonks-release-*` directories behind; on a host without `/tmp/opencode`, `mkdtemp` raises. | Added `_make_state_dir` with a system-temp fallback and `finally` cleanup; contract test. |
| 6 | **Medium** | Workflow push CI did not cover `v10/**` (the push filter listed v2/v3 but not v10); branch pushes had no CI until a PR existed. | `.github/workflows/tests.yml` `on.push.branches` omitted `v10/**`. | Push filter changed to `v*/**`, `agent/**`, `fix/**`, `master`. |
| 7 | **Low** | Gate detail ordering used a set union, so hard-failure note order could vary with hash seed. | `check_forward_gate` iterated `{("monthly","US"), ...} | set(expected)`. | Ordered `dict.fromkeys` key list; output is stable. |

## Cache-less evidence

Run with the cache symlink temporarily removed, then restored:

| command | exit | result |
|---|---:|---|
| `acceptance.py --check-cache --json` | **3** | `ok=false`, 56 missing files |
| `acceptance.py --allow-missing-cache --json` | **0** | `ok=true`, all 12 checks `SKIP`, `cache_missing` populated |
| `acceptance.py --json` (no flag) | **1** | `ok=false`, all 12 checks `FAIL` |

This proves the three paths are distinct: readiness guard, intentional CI skip,
and hard failure. The skip path does not touch the engine or write state/results.
A partial cache is also rejected: `cache_status` requires every symbol returned
by `reproduce.required_symbols()`, `panel.parquet`, `membership.parquet`, and the
exact QQQ snapshot.

Observed residual: importing `bot.data`/`bot.llm`/`bot.news` can recreate empty
`cache/` subdirectories in a cache-less checkout. They are gitignored and contain
no bars or state; the harness itself writes no state/results.

## Acceptance isolation

`run_checks` writes state, ledgers, backups and restore targets only under the
`TemporaryDirectory`/`--state-dir` passed by `main`; the no-cache path creates no
files. `main` removes the temp tree unless `--keep` is set. The `release_check`
plan state dir now follows the same ownership rule and is cleaned in `finally`.

## Release semantics

- `PASS`: every check passes; forward gate counts may be below 5 but must be
  internally consistent.
- `WAIT`: forward gate is incomplete but honest (current US 0/5, TECH 1/5,
  tax 0/5), or short3 is pending/absent. WAIT is not a release failure.
- `BLOCKED`/`FAIL`/unknown gate status, unreadable tables, denominator != 5,
  count/audit mismatch, or a missing expected gate row is a hard FAIL (exit 2).
- `SKIP` is only allowed when the caller explicitly allows a missing cache; a
  full release-check run rejects acceptance SKIPs.

## Evidence run

`uv run python scripts/release_check.py --no-write` after the fixes:

- overall **PASS**, **36/36** test scripts, acceptance 12/12
- EUR ledger US 66.69x / TECH 53.42x / CORE 49.27x (after the merged dividend-basis fix; the audit ran on 66.17/52.98/48.80)
- forward gate **WAIT** US 0/5, TECH 1/5, tax 0/5
- short3 **PASS** verdict=reject
- secret scan **PASS** over tracked text files

Local only: GitHub Actions was not run from this worktree. No tag, no release,
no merge.

## Residual risks

- The CI cache is not committed; cache-bearing runs remain local evidence.
- `uv sync --frozen` still needs package-index access on a cold runner; that is
  environment setup, not strategy/network access by the harness.
- The secret scan is a lightweight regex check, not a full entropy scanner.
- Forward evidence is still 0/5 US, 1/5 TECH, 0/5 tax; this audit changes no
  strategy and makes no forward-edge claim.
