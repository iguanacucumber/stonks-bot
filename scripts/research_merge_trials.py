"""Merge the per-family Alpha Lab trial registries into one file.

Run: uv run python scripts/research_merge_trials.py
Writes results/alpha_trials.csv.

Every attempted variant is kept, including losers and training-only attempts.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FILES = (
    "alpha_trials_cross_asset.csv",
    "alpha_trials_tech.csv",
    "alpha_trials_short.csv",
)
FIELDS = [
    "id",
    "date",
    "branch",
    "family",
    "hypothesis",
    "universe",
    "start",
    "end",
    "params_json",
    "n_trials",
    "metric",
    "value",
    "oos_value",
    "cost_model",
    "verdict",
    "notes",
]


def main() -> None:
    rows: list[dict] = []
    for name in FILES:
        path = ROOT / "results" / name
        if not path.exists():
            print(f"missing {path}; skip")
            continue
        with path.open() as fh:
            part = list(csv.DictReader(fh))
        print(f"{name}: {len(part)} rows")
        rows.extend(part)
    for i, row in enumerate(rows, 1):
        row["id"] = f"alpha-{i:05d}"
    out = ROOT / "results" / "alpha_trials.csv"
    with out.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {out} with {len(rows)} rows")


if __name__ == "__main__":
    main()
