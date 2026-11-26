#!/usr/bin/env python
"""Paper-safe real-money readiness preflight.

This command is read-only. It does not contact a broker, read a password, print a
secret, write state or send an order. It verifies the human approval artifact,
forward gate 5/5, release check, real-account environment shape, read-only flag,
risk rails and pending blockers for one exact config.

In this paper-only build the final execution-path check is a deliberate blocker:
there is no real-money adapter. The command therefore exits 2 today even if the
other evidence is supplied.

Examples::

    uv run python scripts/real_money_preflight.py --book monthly --market US
    uv run python scripts/real_money_preflight.py --book monthly --market TECH --json
    uv run python scripts/real_money_preflight.py --plan state/live/plan.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot.live.approval import DEFAULT_APPROVAL_PATH, config_sha256  # noqa: E402
from bot.live.config import (  # noqa: E402
    BEST_EVIDENCE_MARKET,
    BOOK_OVERRIDES,
    load_config,
)
from bot.live.preflight import (  # noqa: E402
    DEFAULT_AUDIT,
    DEFAULT_FORWARD_GATE,
    DEFAULT_RELEASE_CHECK,
    run_preflight,
)

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_BLOCKED = 2


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--book", choices=tuple(BOOK_OVERRIDES), default=None)
    ap.add_argument("--market", default=None, help=f"default {BEST_EVIDENCE_MARKET}")
    ap.add_argument("--capital", type=float, default=None)
    ap.add_argument("--config", default=None, help="optional live TOML; CLI flags win")
    ap.add_argument("--state-dir", default=None)
    ap.add_argument(
        "--approval",
        default=str(DEFAULT_APPROVAL_PATH),
        help="approval JSON (default state/live/APPROVAL.json)",
    )
    ap.add_argument("--forward-gate", default=str(DEFAULT_FORWARD_GATE))
    ap.add_argument("--audit", default=str(DEFAULT_AUDIT))
    ap.add_argument("--release-check", default=str(DEFAULT_RELEASE_CHECK))
    ap.add_argument(
        "--plan",
        default=None,
        help="optional plan JSON; required when the approval pins a plan hash",
    )
    ap.add_argument(
        "--now", default=None, help=argparse.SUPPRESS
    )  # deterministic tests
    ap.add_argument(
        "--print-config-hash",
        action="store_true",
        help="print the full SHA-256 for this exact config and exit (no checks)",
    )
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    try:
        cfg = load_config(
            args.config,
            book=args.book,
            market=args.market,
            capital=args.capital,
            state_dir=args.state_dir,
        )
        if args.print_config_hash:
            print(config_sha256(cfg))
            return EXIT_OK
        report = run_preflight(
            cfg,
            approval_path=args.approval,
            forward_gate_path=args.forward_gate,
            audit_path=args.audit,
            release_check_path=args.release_check,
            plan=args.plan,
            now=args.now,
        )
    except Exception as exc:  # noqa: BLE001 - config/file failure is a hard error
        print(f"ERROR: {type(exc).__name__}: {exc}")
        return EXIT_ERROR

    if args.json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        print("\n".join(report.lines()))
    return report.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
