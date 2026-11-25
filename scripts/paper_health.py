"""Health check for the forward paper job.

Fails (exit 2) when a weekday has closed and there is still no close-valid row for
that date. Reads only local files, so it works even if the automation API is down.

Run: uv run python scripts/paper_health.py
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DAILY = ROOT / "results" / "paper_daily.csv"
ET = ZoneInfo("America/New_York")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", default=None, help="ISO timestamp, for testing")
    args = ap.parse_args()
    now = (
        datetime.fromisoformat(args.as_of)
        if args.as_of
        else datetime.now(timezone.utc)
    )
    et = now.astimezone(ET)
    if not DAILY.exists():
        print("HEALTH STALE: no paper_daily.csv")
        return 2
    df = pd.read_csv(DAILY)
    if df.empty:
        print("HEALTH STALE: empty paper_daily.csv")
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
        f"HEALTH {'STALE' if stale else 'OK'}  rows={len(df)}  close_valid={int(valid.sum())}  "
        f"valid_dates={n_valid_dates}  last_valid={last_valid_et}  now_et={et:%Y-%m-%d %H:%M}"
    )
    if stale:
        print("no close-valid row for today's session; check results/paper_daily.log")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
