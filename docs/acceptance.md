# End-to-end acceptance harness

One command proves the full paper stack on a temporary state directory:

```bash
uv run python scripts/acceptance.py
uv run python scripts/acceptance.py --json
```

Exit code is `0` only when every check passes. It never calls a broker, never
fetches Yahoo, never writes the real `state/` or `results/` directories, and
never uses secrets.

## What it checks

| check | proof |
|---|---|
| `cache_freshness` | required bars/panel/membership exist and the snapshot is 2026-11-25 |
| `backtest_eur_ledger` | US monthly EUR ledger **66.17x** and TECH **52.98x** within 0.05x |
| `live_plan_us` / `live_plan_tech` | live planner returns orders, no state written |
| `paper_replay_us` / `paper_replay_tech` | `live.py run --paper` writes state+ledger |
| `paper_idempotent_rerun` | second identical run adds no ledger row and no state run |
| `reconcile_ledger` | live reconcile is OK for US and TECH |
| `ops_doctor` | cache/state/FX/disk checks pass on the temp state |
| `ops_backup_restore` | checksummed backup restores byte-identical state |
| `advisor_order_list` | advisor returns a non-empty paper order list |
| `reproduce_baseline` | committed `scripts/reproduce.py baseline --no-write` is strict-PASS |

The live/paper checks use `--as-of 2026-11-25`; this is a deterministic replay,
not a request to trade. The engine writes only under the temp directory
`/tmp/opencode/stonks-acceptance-*` (or the system temp dir), removed on exit
unless `--keep` is used.

## JSON and CI

`--json` emits `ok`, `cache_missing`, `snapshot_expected` and one object per
check. CI has no 459 MB cache; the workflow calls:

```bash
uv run python scripts/acceptance.py --allow-missing-cache --json
```

Without the cache and without that flag, the harness fails loudly. With the flag,
all checks are `SKIP` and the command exits 0; it does not fabricate a run. On a
cache-bearing checkout the same command runs the full ~15-second acceptance.

## Scope

This is a plumbing/acceptance test, not a new strategy result. It asserts the
committed baseline (US 66.17x / TECH 52.98x EUR ledger) and the operational loop
(plan → paper → reconcile → backup/restore → advisor → reproduce). It does not
prove forward edge; the forward gate remains in `results/forward_status.md`.

## Tests

```bash
uv run python tests/test_acceptance.py
uv run python tests/test_live.py
uv run python tests/test_ops.py
uv run python tests/test_forward.py
```
