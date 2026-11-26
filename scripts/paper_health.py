"""Health check for the forward paper job.

Fails (exit 2) when a weekday has closed and there is still no close-valid row for
that date. Reads only local files, so it works even if the automation API is down.
Each book has its own file and is checked separately.

Run: uv run python scripts/paper_health.py
     uv run python scripts/paper_health.py --book tax
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import pandas as pd  # noqa: E402

from paper_daily import BOOKS, book_paths  # noqa: E402

ET = ZoneInfo("America/New_York")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--book", choices=tuple(BOOKS), default="monthly")
    ap.add_argument("--as-of", default=None, help="ISO timestamp, for testing")
    args = ap.parse_args()
    daily = book_paths(args.book)[0]
    now = (
        datetime.fromisoformat(args.as_of) if args.as_of else datetime.now(timezone.utc)
    )
    et = now.astimezone(ET)
    if not daily.exists():
        print(f"HEALTH STALE: no {daily.name} (book {args.book})")
        return 2
    df = pd.read_csv(daily)
    if df.empty:
        print(f"HEALTH STALE: empty {daily.name} (book {args.book})")
        return 2
    if "close_valid" in df.columns:
        valid = df["close_valid"].astype(str).str.lower().isin(["true", "1"])
    else:
        valid = pd.Series(False, index=df.index)
    times = pd.to_datetime(df["time"], utc=True, format="mixed")
    last = times.max()
    last_valid = times[valid].max() if valid.any() else None
    n_valid_dates = (
        pd.to_datetime(df.loc[valid, "quote_date"]).dt.date.nunique()
        if valid.any()
        else 0
    )

    after_close = et.hour >= 16
    weekday = et.weekday() < 5
    last_valid_et = last_valid.astimezone(ET).date() if last_valid is not None else None
    stale = bool(weekday and after_close and (last_valid_et != et.date()))

    print(
        f"HEALTH {'STALE' if stale else 'OK'}  book={args.book}  rows={len(df)}  "
        f"close_valid={int(valid.sum())}  valid_dates={n_valid_dates}  "
        f"last_valid={last_valid_et}  now_et={et:%Y-%m-%d %H:%M}"
    )
    if stale:
        print("no close-valid row for today's session; check results/paper_daily.log")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
