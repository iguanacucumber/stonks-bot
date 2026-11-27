#!/usr/bin/env python
"""CLI for the v12 production simulation.

Commands::

    prod_sim.py run      --mode replay --speed 1000 --dev --start 2026-01-26
    prod_sim.py status   --state-dir state/prodsim
    prod_sim.py scenario partial_fill
    prod_sim.py load     --ticks 10000 --orders 1000

This is simulated real money only. There is no real broker adapter, no
credentials and no live order path. ``--dev`` uses in-process reference fakes
until the sibling v12 exchange/store modules merge.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from datetime import date
from typing import Any, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.prodsim import scenarios  # noqa: E402
from bot.prodsim.clock import SimulationClock  # noqa: E402
from bot.prodsim.contract import RiskLimits, RunConfig, jsonable  # noqa: E402
from bot.prodsim.fakes import MemoryStore, PaperExchange  # noqa: E402
from bot.prodsim.replay import LivePaperFeed, ReplayFeed, load_bars  # noqa: E402
from bot.prodsim.simulation import Simulation  # noqa: E402
from bot.prodsim.strategy import CachedMonthlyTargets, MonthlyBookStrategy  # noqa: E402

DEFAULT_SYMBOLS = "SPY,SMH,QQQ,SOXX"


def _symbols(value: str) -> tuple[str, ...]:
    out = tuple(dict.fromkeys(s.strip().upper() for s in value.split(",") if s.strip()))
    if not out:
        raise argparse.ArgumentTypeError("at least one symbol is required")
    return out


def _token(args: argparse.Namespace) -> str:
    return str(
        args.api_token
        or os.environ.get("STONKS_PRODSIM_API_TOKEN", "")
        or os.environ.get("PRODSIM_API_TOKEN", "")
    )


def _build_store(config: RunConfig, dev: bool) -> Any:
    """Real path: durable SQLite journal. ``--dev`` keeps the in-memory fake."""
    if dev:
        return MemoryStore()
    try:
        from bot.prodsim.store import SQLiteStore  # lazy; merged v12 component
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"v12 SQLiteStore unavailable: {exc}") from exc
    return SQLiteStore(Path(config.state_dir) / "prodsim.sqlite3")


def _build_exchange(config: RunConfig, symbols: Sequence[str], dev: bool) -> Any:
    """Real path: merge-verified SimulatedExchange; ``--dev``: reference fake."""
    if not dev:
        try:
            from bot.prodsim.exchange import SimulatedExchange
        except Exception as exc:  # noqa: BLE001
            raise SystemExit(f"v12 SimulatedExchange unavailable: {exc}") from exc
        return SimulatedExchange(config=config, instruments=tuple(symbols))
    return PaperExchange(
        capital=config.capital,
        currency=config.currency,
        symbols=tuple(symbols),
        limits=config.limits,
        commission_bps=config.commission_bps,
        min_commission=config.min_commission,
        slippage_bps=config.slippage_bps,
        participation_cap=config.participation_cap,
    )


def _build_oms(
    config: RunConfig, exchange: Any, store: Any, dev: bool = False
) -> Any | None:
    """Real path: SimulatedOMS owns identity/idempotency/journaling.

    ``--dev`` deliberately keeps the direct fake path; that path is for local
    demos and scenario tests, not for the deployable service.
    """
    if dev:
        return None
    try:
        from bot.prodsim.oms import SimulatedOMS
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"v12 SimulatedOMS unavailable: {exc}") from exc
    return SimulatedOMS(exchange, store, config)


def _make_strategy(config: RunConfig, args: argparse.Namespace) -> Any:
    if args.no_strategy:
        return None
    provider = CachedMonthlyTargets(
        market=args.market,
        book=args.book,
        capital=config.capital,
        start="2015-01-01",
    )
    return MonthlyBookStrategy(
        provider,
        always_rebalance=bool(args.rebalance_every_tick),
        min_order_notional=25.0,
        max_orders=args.max_orders,
        limits=config.limits,
    )


def _strategy_symbols(args: argparse.Namespace, start: str) -> tuple[str, ...] | None:
    """Pick a feed universe that can actually satisfy the monthly target.

    The default CSPX/SMH symbols are only a demo universe; the selector usually
    holds different names. Ask the provider for the target at the replay start
    and feed those names (the panel is cached on the provider, so this costs one
    snapshot, not a second full load).
    """
    if args.symbols is not None or args.no_strategy:
        return None
    try:
        provider = CachedMonthlyTargets(
            market=args.market,
            book=args.book,
            capital=args.capital,
            start="2015-01-01",
        )
        if args.mode == "live-paper":
            from bot.live import calendar  # lazy

            session = calendar.target_session().date()
        else:
            session = date.fromisoformat(start)
        target = provider(session, {})
        names = tuple(sorted(str(s) for s in target if s))
        if names:
            return names
    except Exception as exc:  # noqa: BLE001
        print(f"warning: could not preview strategy universe: {exc}", file=sys.stderr)
    return None


def cmd_run(args: argparse.Namespace) -> int:
    symbols = (
        args.symbols
        or _strategy_symbols(args, args.start)
        or _symbols(DEFAULT_SYMBOLS)
    )
    symbols = tuple(dict.fromkeys(symbols))
    limits = RiskLimits(
        max_gross=args.max_gross,
        max_order_notional=args.max_order_notional,
        max_position_pct=args.max_position_pct,
    )
    config = RunConfig(
        capital=args.capital,
        mode=args.mode,
        speed=args.speed,
        start=args.start,
        end=args.end,
        symbols=tuple(symbols),
        seed=args.seed,
        commission_bps=args.commission_bps,
        min_commission=args.min_commission,
        slippage_bps=args.slippage_bps,
        participation_cap=args.participation_cap,
        limits=limits,
        state_dir=args.state_dir,
        api_token=_token(args),
    )
    if args.api_port and not args.dev and not config.api_token:
        raise SystemExit("refusing to start API outside --dev without a token")
    feed: Any
    if args.mode == "live-paper":
        feed = LivePaperFeed(
            symbols,
            cache_dir=args.cache_dir,
            start=None,
            poll_seconds=args.poll_seconds,
            ticks_per_bar=args.ticks_per_bar,
            seed=config.seed,
            max_stale_days=args.max_stale_days,
        )
    else:
        try:
            bars = load_bars(
                symbols,
                config.start,
                config.end,
                cache_dir=args.cache_dir,
                ticks_per_bar=args.ticks_per_bar,
            )
        except Exception as exc:  # noqa: BLE001
            raise SystemExit(f"replay data error: {exc}") from exc
        feed = ReplayFeed(bars, seed=config.seed, ticks_per_bar=args.ticks_per_bar)
    store = _build_store(config, args.dev)
    exchange = _build_exchange(config, symbols, args.dev)
    oms = _build_oms(config, exchange, store, args.dev)
    strategy = _make_strategy(config, args)
    realtime = bool(args.paced or args.mode == "live-paper")
    clock = SimulationClock(
        mode=args.mode,
        speed=args.speed,
        realtime=realtime,
        max_sleep_seconds=min(2.0, max(0.05, args.max_sleep)),
    )
    sim = Simulation(
        config,
        exchange=exchange,
        store=store,
        oms=oms,
        clock=clock,
        strategy=strategy,
        snapshot_interval=args.snapshot_interval,
    )
    sim.start()
    api = None
    if args.api_port:
        api = sim.start_api(
            host=args.api_host,
            port=args.api_port,
            token=config.api_token,
            dev=args.dev,
        )
        if api is None:
            print(
                "warning: HTTP API unavailable on this branch; continuing",
                file=sys.stderr,
            )
    if args.mode == "live-paper":
        detail = f"polling every {args.poll_seconds:g}s, {len(symbols)} symbols"
    else:
        detail = f"{len(feed.sessions)} sessions, {len(feed.bars)} bars"
    print(
        f"starting {args.mode} mode: {detail}, speed={config.speed:g}"
        + ("" if realtime else " (unpaced)"),
        file=sys.stderr,
    )
    try:
        sim.run_feed(feed, max_ticks=args.max_ticks)
    finally:
        sim.stop()
    out = sim.status_dict()
    if isinstance(feed, ReplayFeed):
        out["feed"] = {
            "sessions": len(feed.sessions),
            "bars": len(feed.bars),
            "outages": feed.outages(),
        }
    else:
        out["feed"] = {"mode": "live-paper", "symbols": list(symbols)}
    out["api"] = None if api is None else type(api).__name__
    print(json.dumps(jsonable(out), indent=2, sort_keys=True))
    return 0 if sim.state in ("STOPPED", "RUNNING") else 2


def cmd_status(args: argparse.Namespace) -> int:
    path = Path(args.state_dir) / "status.json"
    if not path.exists():
        print(f"no status file at {path}", file=sys.stderr)
        return 1
    try:
        data = json.loads(path.read_text())
    except Exception as exc:  # noqa: BLE001
        print(f"unreadable status file: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(data, indent=2, sort_keys=True))
    else:
        status = data.get("status", {})
        health = data.get("health", {})
        strategy = data.get("strategy") or {}
        print(f"state      {status.get('state')}")
        print(f"mode       {status.get('mode')}")
        print(f"ready      {health.get('ready')}")
        print(f"clock      {status.get('clock')}")
        print(f"session    {status.get('session')}")
        print(f"ticks      {status.get('processed_ticks')}")
        print(f"orders     {status.get('orders')}")
        print(f"fills      {status.get('fills')}")
        print(f"errors     {status.get('errors')}")
        print(
            f"targets    {strategy.get('targets')} @ {strategy.get('pending_session')}"
        )
        if health.get("issues"):
            print("issues     " + "; ".join(health["issues"]))
    return 0


def cmd_scenario(args: argparse.Namespace) -> int:
    try:
        if args.name == "all":
            results = scenarios.run_all(include_load=not args.skip_load)
        else:
            results = [scenarios.run(args.name)]
    except Exception as exc:  # noqa: BLE001
        print(f"scenario failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(jsonable(results), indent=2, sort_keys=True))
    return 0


def cmd_load(args: argparse.Namespace) -> int:
    try:
        result = scenarios.load(ticks=args.ticks, orders=args.orders)
    except Exception as exc:  # noqa: BLE001
        print(f"load failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(jsonable(result), indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="v12 production simulation (simulated real money only)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run a replay or live-paper simulation")
    run.add_argument("--mode", choices=("replay", "live-paper"), default="replay")
    run.add_argument("--speed", type=float, default=1.0, help="replay speed multiplier")
    run.add_argument("--start", default="2026-01-02")
    run.add_argument("--end", default="2026-11-25")
    run.add_argument("--symbols", type=_symbols, default=None)
    run.add_argument("--capital", type=float, default=100_000.0)
    run.add_argument("--state-dir", default="state/prodsim")
    run.add_argument("--cache-dir", default=None)
    run.add_argument("--seed", type=int, default=7)
    run.add_argument("--ticks-per-bar", type=int, default=12)
    run.add_argument("--max-ticks", type=int, default=None)
    run.add_argument("--poll-seconds", type=float, default=30.0)
    run.add_argument("--max-stale-days", type=int, default=3)
    run.add_argument("--snapshot-interval", type=int, default=100)
    run.add_argument("--book", default="monthly")
    run.add_argument("--market", default="US")
    run.add_argument("--no-strategy", action="store_true")
    run.add_argument("--rebalance-every-tick", action="store_true")
    run.add_argument("--max-orders", type=int, default=50)
    run.add_argument("--commission-bps", type=float, default=1.0)
    run.add_argument("--min-commission", type=float, default=0.0)
    run.add_argument("--slippage-bps", type=float, default=2.0)
    run.add_argument("--participation-cap", type=float, default=0.10)
    run.add_argument("--max-gross", type=float, default=3.0)
    run.add_argument("--max-order-notional", type=float, default=100_000.0)
    run.add_argument("--max-position-pct", type=float, default=0.50)
    run.add_argument(
        "--dev", action="store_true", help="use in-process reference fakes"
    )
    run.add_argument("--paced", action="store_true", help="sleep in wall-clock time")
    run.add_argument("--max-sleep", type=float, default=1.0)
    run.add_argument("--api-host", default="127.0.0.1")
    run.add_argument("--api-port", type=int, default=0)
    run.add_argument("--api-token", default="")
    run.set_defaults(func=cmd_run)

    status = sub.add_parser("status", help="read the last status file")
    status.add_argument("--state-dir", default="state/prodsim")
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=cmd_status)

    scenario = sub.add_parser("scenario", help="run a required scenario")
    scenario.add_argument(
        "name",
        choices=tuple(scenarios.SCENARIOS) + ("all",),
        default="all",
        nargs="?",
    )
    scenario.add_argument("--skip-load", action="store_true")
    scenario.set_defaults(func=cmd_scenario)

    load = sub.add_parser("load", help="run the 10k-tick / 1k-order load test")
    load.add_argument("--ticks", type=int, default=10_000)
    load.add_argument("--orders", type=int, default=1_000)
    load.set_defaults(func=cmd_load)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
