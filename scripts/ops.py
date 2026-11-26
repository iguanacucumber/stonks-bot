#!/usr/bin/env python
"""Operator CLI for the paper live loop.

Commands
--------
status       last run, next session, freshness, drawdown, gate, open orders
doctor       cache/state/permission/disk/FX/missing-symbol checks
backup       atomic tar.gz + per-file SHA-256 manifest
restore      verify checksum/manifest, then restore state/ledger/config
alert-test   print a dry-run alert payload; no network unless --send
drill        deterministic stale/duplicate/broker/state/missed-session drill

Examples:
  uv run python scripts/ops.py status
  uv run python scripts/ops.py doctor --json
  uv run python scripts/ops.py backup --out state/backups
  uv run python scripts/ops.py restore state/backups/stonks-backup-....tar.gz --force
  uv run python scripts/ops.py alert-test --kind missed_session

No command sends real orders.  Alerting stays off until the STONKS_ALERT_*
environment variables are configured; --send is explicit.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot.ops.alerts import AlertManager, build_manager, sample_payload  # noqa: E402
from bot.ops.backup import create_backup, restore_backup  # noqa: E402
from bot.ops.doctor import run_doctor  # noqa: E402
from bot.ops.drill import run_drill  # noqa: E402
from bot.ops.status import collect_status, format_status  # noqa: E402
from bot.live.config import LiveConfig, load_config  # noqa: E402

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_ATTENTION = 2


def _add_common(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--book", choices=("monthly", "tax"), default=None)
    ap.add_argument("--market", default=None)
    ap.add_argument("--capital", type=float, default=None)
    ap.add_argument("--leverage", type=float, default=None)
    ap.add_argument("--dd-stop", type=float, default=None)
    ap.add_argument("--max-gross", type=float, default=None)
    ap.add_argument("--max-turnover", type=float, default=None)
    ap.add_argument("--state-dir", default=None)
    ap.add_argument("--config", default=None, help="optional live TOML")
    ap.add_argument("--now", default=None, help=argparse.SUPPRESS)
    ap.add_argument("--json", action="store_true")


def _config(args: argparse.Namespace) -> LiveConfig:
    return load_config(
        args.config,
        book=args.book,
        market=args.market,
        capital=args.capital,
        leverage=args.leverage,
        dd_stop=args.dd_stop,
        max_gross=args.max_gross,
        max_turnover=args.max_turnover,
        state_dir=args.state_dir,
    )


def _status(args: argparse.Namespace) -> int:
    payload = collect_status(
        _config(args), ROOT, now=args.now, as_of=getattr(args, "as_of", None)
    )
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    else:
        print(format_status(payload))
        if payload.get("alert"):
            print("\nalert payload (dry-run; alerting is off by default)")
            print(json.dumps(payload["alert"], indent=2, sort_keys=True))
    return int(payload["exit_code"])


def _doctor(args: argparse.Namespace) -> int:
    cfg = _config(args)
    report = run_doctor(
        ROOT,
        cfg,
        cache_dir=args.cache_dir,
        now=args.now,
        min_free_mb=args.min_free_mb,
    )
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        print("\n".join(report.lines()))
    return report.exit_code


def _backup(args: argparse.Namespace) -> int:
    cfg = _config(args)
    paths = [args.config] if args.config else None
    info = create_backup(
        ROOT,
        out_dir=args.out,
        state_dir=args.state_dir or str(cfg.state_path.parent),
        config_paths=paths,
    )
    payload = {
        "archive": str(info.path),
        "checksum": str(info.checksum_path),
        "digest": info.digest,
        "created_at": info.created_at,
        "entries": len(info.entries),
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"BACKUP OK  {info.path}")
        print(f"  sha256 {info.digest}")
        print(f"  files {len(info.entries)}  checksum {info.checksum_path}")
    return EXIT_OK


def _restore(args: argparse.Namespace) -> int:
    try:
        result = restore_backup(
            args.archive,
            ROOT,
            state_dir=args.state_dir,
            results_dir=args.results_dir,
            force=args.force,
        )
    except Exception as exc:  # noqa: BLE001 - operator-facing failure
        print(f"RESTORE FAIL: {exc}", file=sys.stderr)
        return EXIT_ERROR
    payload = {
        "archive": str(result.archive),
        "digest": result.digest,
        "restored": result.restored,
        "targets": result.targets,
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"RESTORE OK  {result.archive}")
        print(f"  digest {result.digest}")
        print(f"  files {result.restored}")
    return EXIT_OK


def _alert_test(args: argparse.Namespace) -> int:
    manager: AlertManager = build_manager()
    payload = sample_payload(args.kind)
    deliveries = manager.send(payload, dry_run=not args.send)
    out = {
        "configured_channels": manager.describe(),
        "enabled": manager.enabled,
        "dry_run": not args.send,
        "payload": payload.to_dict(),
        "deliveries": [d.to_dict() for d in deliveries],
    }
    if args.json:
        print(json.dumps(out, indent=2, sort_keys=True))
    else:
        print("ALERT TEST  dry-run" if not args.send else "ALERT TEST  send")
        print(f"  channels: {manager.describe() or ['none (off by default)']}")
        print(payload.to_json())
        print("  deliveries:")
        for d in deliveries:
            print(f"    {d.channel}: {'OK' if d.ok else 'FAIL'} - {d.detail}")
    return EXIT_OK if all(d.ok for d in deliveries) else EXIT_ERROR


def _drill(args: argparse.Namespace) -> int:
    results = run_drill()
    out = {
        "ok": all(r["ok"] for r in results),
        "scenarios": results,
    }
    if args.json:
        print(json.dumps(out, indent=2, sort_keys=True))
    else:
        print(f"FAILURE DRILL {'PASS' if out['ok'] else 'FAIL'}")
        for row in results:
            alert = row["alert"]
            print(
                f"  {'OK  ' if row['ok'] else 'FAIL'} {row['scenario']}: "
                f"exit {row['actual_exit']} (expected {row['expected_exit']}) "
                f"alert={alert['kind']}/{alert['severity']}"
            )
            print(f"       {row['detail']}")
    return EXIT_OK if out["ok"] else EXIT_ATTENTION


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("status", help="read-only live status")
    _add_common(s)
    s.add_argument("--as-of", default=None)
    s.set_defaults(func=_status)

    d = sub.add_parser("doctor", help="read-only cache/state checks")
    _add_common(d)
    d.add_argument("--cache-dir", default=None)
    d.add_argument("--min-free-mb", type=float, default=200.0)
    d.set_defaults(func=_doctor)

    b = sub.add_parser("backup", help="create an atomic checksummed backup")
    _add_common(b)
    b.add_argument("--out", default=None)
    b.set_defaults(func=_backup)

    r = sub.add_parser("restore", help="verify and restore a backup")
    r.add_argument("archive")
    r.add_argument("--state-dir", default=None)
    r.add_argument("--results-dir", default=None)
    r.add_argument("--force", action="store_true")
    r.add_argument("--json", action="store_true")
    r.set_defaults(func=_restore)

    a = sub.add_parser("alert-test", help="print a dry-run alert payload")
    a.add_argument(
        "--kind",
        default="missed_session",
        choices=(
            "missed_session",
            "stale_data",
            "duplicate_order",
            "broker_down",
            "state_corrupt",
        ),
    )
    a.add_argument(
        "--send", action="store_true", help="actually call configured channels"
    )
    a.add_argument("--json", action="store_true")
    a.set_defaults(func=_alert_test)

    dr = sub.add_parser("drill", help="run the failure drill")
    dr.add_argument("--json", action="store_true")
    dr.set_defaults(func=_drill)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        return EXIT_ERROR
    except Exception as exc:  # noqa: BLE001 - CLI should be operator friendly
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
