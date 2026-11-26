"""Trial registry.

Every variant that is run gets a row, not just the winner. The CSV is the
branch-local equivalent of `results/trials.csv` from the research protocol.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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


@dataclass
class TrialRegistry:
    branch: str = "v2/alpha-lab"
    rows: list[dict[str, Any]] = field(default_factory=list)

    def add(
        self,
        *,
        family: str,
        hypothesis: str,
        universe: str,
        start: str,
        end: str,
        params: dict | str,
        metric: str,
        value: float | None,
        oos_value: float | None = None,
        cost_model: str = "fr_cto_ibkr",
        verdict: str = "",
        notes: str = "",
        n_trials: int | None = None,
    ) -> dict:
        row = {
            "id": f"alpha-{len(self.rows) + 1:04d}",
            "date": pd_today(),
            "branch": self.branch,
            "family": family,
            "hypothesis": hypothesis,
            "universe": universe,
            "start": start,
            "end": end,
            "params_json": (
                params
                if isinstance(params, str)
                else json.dumps(params, sort_keys=True)
            ),
            "n_trials": n_trials if n_trials is not None else len(self.rows) + 1,
            "metric": metric,
            "value": "" if value is None else f"{value:.6g}",
            "oos_value": "" if oos_value is None else f"{oos_value:.6g}",
            "cost_model": cost_model,
            "verdict": verdict,
            "notes": notes,
        }
        self.rows.append(row)
        return row

    def extend(self, rows: list[dict]) -> None:
        for row in rows:
            self.add(
                family=row.get("family", ""),
                hypothesis=row.get("hypothesis", ""),
                universe=row.get("universe", ""),
                start=row.get("start", ""),
                end=row.get("end", ""),
                params=row.get("params_json", "{}"),
                metric=row.get("metric", ""),
                value=row.get("value"),
                oos_value=row.get("oos_value"),
                cost_model=row.get("cost_model", "fr_cto_ibkr"),
                verdict=row.get("verdict", ""),
                notes=row.get("notes", ""),
                n_trials=row.get("n_trials"),
            )

    @property
    def n_trials(self) -> int:
        return len(self.rows)

    def write(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(self.rows)
        return path


def pd_today() -> str:
    import pandas as pd

    return pd.Timestamp.today().date().isoformat()
