#!/usr/bin/env python
"""Run the deterministic operations failure drill (no network, no real state).

Exit 0 means every scenario produced the expected alert and exit code.  Exit 2
means the runbook/detection path drifted and needs review.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot.ops.drill import run_drill  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    results = run_drill()
    ok = all(r["ok"] for r in results)
    if args.json:
        print(json.dumps({"ok": ok, "scenarios": results}, indent=2, sort_keys=True))
    else:
        print(f"FAILURE DRILL {'PASS' if ok else 'FAIL'}")
        for row in results:
            alert = row["alert"]
            print(
                f"  {'OK  ' if row['ok'] else 'FAIL'} {row['scenario']}: "
                f"exit {row['actual_exit']} (expected {row['expected_exit']}) "
                f"alert={alert['kind']}/{alert['severity']}"
            )
            print(f"       {row['detail']}")
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
