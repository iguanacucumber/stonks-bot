#!/usr/bin/env python
"""Investor profile advisor: questions -> allocation -> next orders (paper only).

This CLI is deliberately not connected to a broker. It reuses the researched
live selector for the current target and prints a deterministic planning path.
No orders are sent and no return is promised.

Examples:
  uv run python scripts/advise.py --profile max_growth --horizon 12 \
      --drawdown-tolerance 0.60 --capital 10000 --wrapper cto \
      --income-need 0 --as-of 2026-11-25

  uv run python scripts/advise.py --interactive
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot import advisor  # noqa: E402


def _ask_float(label: str, default: float, *, percent: bool = False) -> float:
    raw = input(f"{label} [{default:g}]: ").strip()
    if not raw:
        return float(default)
    value = float(raw.replace("%", "").replace("€", "").replace(",", ""))
    if percent and value > 1.0:
        value /= 100.0
    return value


def _ask_choice(label: str, choices: tuple[str, ...], default: str) -> str:
    raw = input(f"{label} ({'/'.join(choices)}) [{default}]: ").strip().lower()
    return raw or default


def _interactive(args: argparse.Namespace) -> None:
    print("Answer five questions. This is a planning tool, not investment advice.\n")
    if args.horizon is None:
        args.horizon = _ask_float("1. Investment horizon in years", 10.0)
    if args.drawdown_tolerance is None:
        args.drawdown_tolerance = _ask_float(
            "2. Maximum drawdown you can tolerate, in percent", 45.0, percent=True
        )
    if args.capital is None:
        args.capital = _ask_float("3. Investable capital in EUR", 10_000.0)
    if args.wrapper is None:
        args.wrapper = _ask_choice("4. Wrapper", ("cto", "pea"), "cto")
    if args.income_need is None:
        args.income_need = _ask_float(
            "5. Annual income need in EUR (0 while accumulating)", 0.0
        )


def _investor(args: argparse.Namespace) -> advisor.InvestorProfile:
    wrapper = (args.wrapper or "cto").lower()
    broker = args.broker
    if broker is None:
        broker = "Trade Republic" if wrapper == "pea" else advisor.DEFAULT_BROKER
    elif wrapper == "pea" and broker.lower() == "degiro":
        raise SystemExit("DEGIRO does not offer a PEA")
    return advisor.InvestorProfile(
        horizon_years=args.horizon,
        drawdown_tolerance=args.drawdown_tolerance,
        capital=args.capital,
        wrapper=wrapper,
        broker=broker,
        income_need=args.income_need,
    )


def _print_decision(rec: advisor.Recommendation) -> None:
    print("PROFILE DECISION TREE")
    for line in advisor.decision_tree():
        print(f"  - {line}")
    print()
    print(advisor.format_recommendation(rec))


def _print_lifecycle(
    rec: advisor.Recommendation,
    args: argparse.Namespace,
    *,
    stress: bool,
) -> None:
    swr = advisor.safe_withdrawal_rate(
        rec.profile, horizon_years=max(1, int(args.years)), wrapper=rec.wrapper
    )
    scenario = "stress" if stress else args.scenario
    annual_withdrawal = (
        args.withdrawal
        if args.withdrawal is not None
        else (args.income_need or rec.capital * swr)
    )
    projection = advisor.project_lifecycle(
        rec.profile,
        rec.capital,
        years=args.years,
        annual_contribution=args.contribution,
        annual_withdrawal=annual_withdrawal,
        inflation=args.inflation,
        wrapper=rec.wrapper,
        scenario=scenario,
    )
    print()
    print(advisor.format_lifecycle(projection, swr))


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--profile",
        choices=advisor.PROFILE_NAMES,
        default=None,
        help="explicit profile; otherwise choose from the five answers",
    )
    ap.add_argument("--horizon", type=float, default=None, help="years")
    ap.add_argument(
        "--drawdown-tolerance",
        type=float,
        default=None,
        help="fraction (0.45) or percent (45)",
    )
    ap.add_argument("--capital", type=float, default=None, help="EUR")
    ap.add_argument("--wrapper", choices=advisor.WRAPPERS, default=None)
    ap.add_argument("--broker", default=None)
    ap.add_argument("--income-need", type=float, default=None, help="EUR/year")
    ap.add_argument("--withdrawal", type=float, default=None, help="EUR/year")
    ap.add_argument("--contribution", type=float, default=0.0, help="EUR/year")
    ap.add_argument("--years", type=int, default=15)
    ap.add_argument("--inflation", type=float, default=0.025)
    ap.add_argument(
        "--scenario",
        choices=("base", "stress", "adverse"),
        default="base",
    )
    ap.add_argument("--as-of", default=None, help="completed session for the target")
    ap.add_argument("--json", action="store_true")
    ap.add_argument(
        "--interactive",
        action="store_true",
        help="ask the five questions (also implied when no profile/answers are given)",
    )
    ap.add_argument(
        "--no-lifecycle",
        action="store_true",
        help="print only the allocation and orders",
    )
    return ap


def main(argv: list[str] | None = None) -> int:
    ap = _parser()
    args = ap.parse_args(argv)
    if args.drawdown_tolerance is not None and args.drawdown_tolerance > 1.0:
        args.drawdown_tolerance /= 100.0
    missing = (
        args.horizon is None
        or args.drawdown_tolerance is None
        or args.capital is None
        or args.wrapper is None
        or args.income_need is None
    )
    if args.interactive or (args.profile is None and missing):
        try:
            _interactive(args)
        except (EOFError, KeyboardInterrupt):
            print("\nERROR: interactive input cancelled", file=sys.stderr)
            return 1
    # Defaults if a profile was explicit and only some answers were given.
    args.horizon = 10.0 if args.horizon is None else args.horizon
    args.drawdown_tolerance = (
        0.45 if args.drawdown_tolerance is None else args.drawdown_tolerance
    )
    args.capital = advisor.DEFAULT_CAPITAL if args.capital is None else args.capital
    args.wrapper = args.wrapper or "cto"
    args.income_need = 0.0 if args.income_need is None else args.income_need
    try:
        investor = _investor(args)
        rec = advisor.recommend(
            investor,
            args.profile,
            as_of=args.as_of,
        )
    except (ValueError, FileNotFoundError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if args.json:
        payload = {"recommendation": rec.to_dict()}
        if not args.no_lifecycle:
            projection = advisor.project_lifecycle(
                rec.profile,
                rec.capital,
                years=args.years,
                annual_contribution=args.contribution,
                annual_withdrawal=(
                    args.withdrawal
                    if args.withdrawal is not None
                    else (
                        args.income_need
                        if args.income_need
                        else rec.capital
                        * advisor.safe_withdrawal_rate(
                            rec.profile, horizon_years=args.years, wrapper=rec.wrapper
                        )
                    )
                ),
                inflation=args.inflation,
                wrapper=rec.wrapper,
                scenario=args.scenario,
            )
            payload["lifecycle"] = projection.to_dict()
            payload["safe_withdrawal_rate"] = advisor.safe_withdrawal_rate(
                rec.profile, horizon_years=args.years, wrapper=rec.wrapper
            )
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
        return 0
    _print_decision(rec)
    if not args.no_lifecycle:
        _print_lifecycle(rec, args, stress=False)
        if not args.income_need and args.withdrawal is None:
            print(
                "\nIllustrative decumulation at the planning SWR uses the stress "
                "scenario; it is a stress test, not a forecast."
            )
            _print_lifecycle(rec, args, stress=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
