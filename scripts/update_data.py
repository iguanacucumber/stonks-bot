"""Advance cached Yahoo bars without refetching history.

The paper jobs call the same incremental path. This CLI is for a pre-warm job or
a manual update: only stale symbols are requested, dead tickers back off for 7
days, and a closed market costs one benchmark request (not the whole universe).

Run: uv run python scripts/update_data.py                  # US + TECH
     uv run python scripts/update_data.py --market US
     uv run python scripts/update_data.py --market FR --market DE
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import pandas as pd  # noqa: E402

from bot import data, markets  # noqa: E402
from paper_daily import refresh  # noqa: E402


def last_bar(symbol: str) -> str:
    path = data.BARS / f"{symbol.replace('/', '_')}.csv"
    if not path.exists():
        return "missing"
    try:
        return str(pd.read_csv(path, usecols=["date"])["date"].max())
    except Exception:
        return "unreadable"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--market",
        action="append",
        choices=["US", *sorted(markets.MARKETS)],
        help="repeatable; default US and TECH",
    )
    args = ap.parse_args()
    for market in args.market or ["US", "TECH"]:
        print(f"== {market} ==", flush=True)
        refresh(market)
        bench = markets.MARKETS[market]["bench"]
        print(f"   {bench} last bar {last_bar(bench)}", flush=True)


if __name__ == "__main__":
    main()
