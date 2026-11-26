#!/usr/bin/env python
"""Live autopilot CLI: plan | run | status | reconcile.

Dry-run is the default. ``run --paper`` is the only command that writes the paper
ledger. There is no real-broker flag; a future adapter must be added behind the
``Broker`` protocol and remain opt-in.

New live plans default to US (the best real-life backtest). Use ``--market TECH``
for the forward validation track that is already being logged; this CLI never
switches or rewrites the existing ``scripts/paper_daily.py`` book.

Examples:
  uv run python scripts/live.py plan --book monthly                  # US default
  uv run python scripts/live.py run --book monthly                  # dry run
  uv run python scripts/live.py run --book monthly --paper          # paper ledger
  uv run python scripts/live.py status --book monthly
  uv run python scripts/live.py reconcile --book monthly
  uv run python scripts/live.py plan --book monthly --market TECH   # validation
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from bot.live import reconcile, report  # noqa: E402
from bot.live.config import BEST_EVIDENCE_MARKET, LiveConfig, load_config  # noqa: E402
from bot.live.engine import EngineError, LiveEngine, resolve_session  # noqa: E402
from bot.live.health import health  # noqa: E402
from bot.live.signal import SnapshotError, build_snapshot  # noqa: E402
from bot.live.state import StateConfigMismatch, StateError  # noqa: E402

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_BLOCKED = 2
EXIT_RECONCILE = 3


def _add_common(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--book", choices=("monthly", "tax"), default=None)
    ap.add_argument(
        "--market",
        default=None,
        help=f"any market in bot.markets; default {BEST_EVIDENCE_MARKET} "
        "(TECH is the forward validation track)",
    )
    ap.add_argument("--capital", type=float, default=None)
    ap.add_argument("--leverage", type=float, default=None)
    ap.add_argument("--dd-stop", type=float, default=None)
    ap.add_argument("--max-gross", type=float, default=None)
    ap.add_argument("--max-turnover", type=float, default=None)
    ap.add_argument("--state-dir", default=None)
    ap.add_argument(
        "--config",
        default=None,
        help="optional TOML config; CLI flags win",
    )
    ap.add_argument(
        "--as-of",
        default=None,
        help="completed session for replay/demo; live cron must omit it",
    )
    ap.add_argument(
        "--now",
        default=None,
        help=argparse.SUPPRESS,  # deterministic tests only
    )
    ap.add_argument("--json", action="store_true")
    ap.add_argument(
        "--allow-config-change",
        action="store_true",
        help="reuse state after an intentional config change (reconcile first)",
    )


def _config(args: argparse.Namespace) -> LiveConfig:
    overrides = {
        "book": args.book,
        "market": args.market,
        "capital": args.capital,
        "leverage": args.leverage,
        "dd_stop": args.dd_stop,
        "max_gross": args.max_gross,
        "max_turnover": args.max_turnover,
        "state_dir": args.state_dir,
    }
    return load_config(args.config, **overrides)


def _forward_gate(cfg: LiveConfig) -> str:
    """Read the existing forward audit; never write or fabricate a row."""
    path = ROOT / "results" / "paper_audit.csv"
    if not path.exists():
        return f"WAIT 0/{cfg.forward_gate} verified dates (no audit file)"
    try:
        df = pd.read_csv(path)
        df = df[(df.get("book") == cfg.book) & (df.get("market") == cfg.market)]
        if df.empty:
            return (
                f"WAIT 0/{cfg.forward_gate} verified dates "
                f"(no {cfg.book}/{cfg.market} rows in paper_audit.csv)"
            )
        verified = df["verified"].astype(str).str.lower().isin(["true", "1"])
        n = int(df.loc[verified, "date"].astype(str).nunique())
        status = "PASS" if n >= cfg.forward_gate else "WAIT"
        return (
            f"{status} {n}/{cfg.forward_gate} verified dates "
            f"({cfg.book}/{cfg.market}, paper_audit.csv)"
        )
    except Exception as exc:
        return f"WAIT 0/{cfg.forward_gate} verified dates (audit unreadable: {exc})"


def _print_state(cfg: LiveConfig) -> int:
    engine = LiveEngine(cfg)
    state = engine.store.load(allow_config_change=True)
    if state is None:
        print("state: none (fresh paper book, nothing traded yet)")
        return EXIT_OK
    print(f"state: {cfg.state_path}")
    print(
        f"updated={state.updated_at}  created={state.created_at}  "
        f"last_session={state.last_session}  last_mark={state.last_mark_session}"
    )
    marks = {}
    for t, p in state.portfolio.positions.items():
        if p.last_price:
            marks[t] = p.last_price
    print(
        f"cash={state.portfolio.cash:,.2f}  equity_hint={state.peak_equity:,.2f}  "
        f"fees={state.fees_paid:,.2f}  financing={state.financing_paid:,.2f}  "
        f"realized={state.realized_pnl:,.2f}"
    )
    if state.last_fx_rate is not None:
        eq_eur = (
            state.last_equity_eur if state.last_equity_eur is not None else float("nan")
        )
        cash_eur = (
            state.last_cash_eur if state.last_cash_eur is not None else float("nan")
        )
        gross_eur = (
            state.last_gross_eur if state.last_gross_eur is not None else float("nan")
        )
        print(
            f"fx: {state.currency}->EUR {state.last_fx_rate:.6f} "
            f"({state.last_fx_date})  equity_eur={eq_eur:,.2f}  "
            f"cash_eur={cash_eur:,.2f}  gross_eur={gross_eur:,.2f}"
        )
        print(
            f"EUR ledger: fees_eur={state.fees_paid_eur:,.2f}  "
            f"financing_eur={state.financing_paid_eur:,.2f}  "
            f"realized_eur={state.realized_pnl_eur:,.2f}"
        )
    print(
        f"risk_off={state.risk_off}  reason={state.risk_reason}  "
        f"target_gross={sum(state.target_weights.values()):.3f}"
    )
    if state.portfolio.positions:
        print("\npositions")
        print(f"{'ticker':10s} {'qty':>12s} {'avg':>10s} {'last':>10s} {'value':>12s}")
        for t, p in sorted(state.portfolio.positions.items()):
            if not p.active:
                continue
            last = p.last_price or 0.0
            print(
                f"{t:10s} {p.qty:12.4f} {p.avg_cost:10.2f} {last:10.2f} "
                f"{p.qty * last:12.2f}"
            )
    if state.target_weights:
        print("\ntarget book")
        for t, w in sorted(state.target_weights.items(), key=lambda kv: -kv[1]):
            print(f"  {t:10s} {w:8.3%}")
    if state.pending_plan:
        p = state.pending_plan
        print(
            f"\npending plan {p.plan_id}  fill={p.fill_session}  "
            f"orders={len(p.orders)}  status={p.status}"
        )
        for o in p.orders:
            print(
                f"  {o.side:4s} {o.ticker:10s} {o.qty:12.4f} @ {o.ref_price:10.2f} "
                f"({o.status})"
            )
    else:
        print("\npending plan: none")
    if state.pending_cut:
        print(
            f"pending cut: {state.pending_cut.get('reason')} "
            f"scale={state.pending_cut.get('scale')} "
            f"from {state.pending_cut.get('session')} "
            f"(retrying until the rail clears)"
        )
    if state.blocked:
        print(
            f"\nlast blocked: {state.blocked.get('session')} {state.blocked.get('reason')}"
        )
    return EXIT_OK


def _status(args: argparse.Namespace) -> int:
    cfg = _config(args)
    engine = LiveEngine(cfg)
    try:
        result = engine.status(now=args.now, as_of=args.as_of, allow_config_change=True)
        state = engine.store.load(allow_config_change=True)
        snap = None
        try:
            session = resolve_session(args.now, args.as_of)
            snap = build_snapshot(cfg, session)
        except (SnapshotError, EngineError):
            snap = None
        h = health(cfg, state, snap, engine.store, result)
    except (StateError, EngineError) as exc:
        print(f"ERROR: {exc}")
        return EXIT_ERROR
    if args.json:
        print(
            json.dumps(
                {
                    "status": {
                        "session": result.session,
                        "equity": result.equity,
                        "cash": result.cash,
                        "gross": result.gross,
                        "currency": cfg.native_currency,
                        "reporting_currency": cfg.reporting_currency,
                        "fx": result.fx,
                        "blocked_reason": result.blocked_reason,
                    },
                    "health": h.to_dict(),
                    "forward_gate": _forward_gate(cfg),
                    "recommendation": cfg.recommendation,
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        print(
            report.format_report(
                result, cfg, mode="status", forward_gate=_forward_gate(cfg)
            )
        )
        _print_state(cfg)
        print("\nhealth")
        print("\n".join(h.lines()))
    return EXIT_OK if (not result.blocked and h.ok) else EXIT_BLOCKED


def _plan(args: argparse.Namespace) -> int:
    cfg = _config(args)
    engine = LiveEngine(cfg)
    try:
        result = engine.plan(
            now=args.now,
            as_of=args.as_of,
            allow_config_change=args.allow_config_change,
        )
    except (StateError, EngineError) as exc:
        print(f"ERROR: {exc}")
        return EXIT_ERROR
    gate = _forward_gate(cfg)
    if args.json:
        print(report.plan_json(result, cfg))
    else:
        print(report.format_report(result, cfg, mode="plan", forward_gate=gate))
        if args.as_of:
            print(f"replay mode: --as-of {args.as_of} (no state written)")
    return EXIT_BLOCKED if result.blocked else EXIT_OK


def _run(args: argparse.Namespace) -> int:
    cfg = _config(args)
    engine = LiveEngine(cfg)
    paper = bool(args.paper)
    try:
        if paper:
            result = engine.run(
                now=args.now,
                as_of=args.as_of,
                allow_config_change=args.allow_config_change,
            )
        else:
            result = engine.plan(
                now=args.now,
                as_of=args.as_of,
                allow_config_change=args.allow_config_change,
            )
    except (StateError, EngineError) as exc:
        print(f"ERROR: {exc}")
        return EXIT_ERROR
    gate = _forward_gate(cfg)
    if args.json:
        print(report.plan_json(result, cfg))
    else:
        print(report.format_report(result, cfg, mode="run", forward_gate=gate))
        if paper and result.persisted and not result.created:
            print("idempotent no-op: this session is already recorded")
        elif not paper:
            print("dry-run: add --paper to write the paper state/ledger")
        if args.as_of:
            print(f"replay mode: --as-of {args.as_of}")
    return EXIT_BLOCKED if result.blocked else EXIT_OK


def _reconcile(args: argparse.Namespace) -> int:
    cfg = _config(args)
    engine = LiveEngine(cfg)
    try:
        state = engine.store.load(allow_config_change=args.allow_config_change)
        rep = reconcile.reconcile(
            cfg, state, engine.store, repair_ledger=args.repair_ledger
        )
    except (StateError, EngineError) as exc:
        print(f"ERROR: {exc}")
        return EXIT_ERROR
    if args.json:
        print(
            json.dumps(
                {
                    "ok": rep.ok,
                    "issues": rep.issues,
                    "warnings": rep.warnings,
                    "metrics": rep.metrics,
                    "repaired_rows": rep.repaired_rows,
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        print("\n".join(rep.lines()))
    return EXIT_OK if rep.ok else EXIT_RECONCILE


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("plan", help="print target/orders, write nothing")
    _add_common(p)
    p.set_defaults(func=_plan)

    r = sub.add_parser("run", help="run the daily loop (dry-run unless --paper)")
    _add_common(r)
    r.add_argument(
        "--paper",
        action="store_true",
        help="write paper state and ledger (still no real broker)",
    )
    r.set_defaults(func=_run)

    s = sub.add_parser("status", help="show book, pending plan and health")
    _add_common(s)
    s.set_defaults(func=_status)

    c = sub.add_parser("reconcile", help="check fills/orders/positions/ledger")
    _add_common(c)
    c.add_argument("--repair-ledger", action="store_true")
    c.set_defaults(func=_reconcile)

    args = ap.parse_args(argv)
    try:
        return int(args.func(args))
    except StateConfigMismatch as exc:
        print(f"ERROR: {exc}")
        return EXIT_ERROR
    except (StateError, SnapshotError) as exc:
        print(f"ERROR: {exc}")
        return EXIT_ERROR
    except KeyboardInterrupt:
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
