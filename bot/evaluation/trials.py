"""Trial registry: pre-registration, every attempted variant, verdicts.

The schema follows ``docs/research-protocol.md`` and adds operational columns
(``accounting_mode``, ``status``, ``commit``, ``source``) so a report can be
audited without re-reading prose.  One row is one experiment; ``n_trials`` is
the number of variants actually evaluated inside that experiment.  The registry
is CSV so it can be reviewed in a PR and loaded by other branches without a
database.

``accounting_mode`` records which currency/tax view produced ``value``:

* ``native``      — asset-currency after-tax result (research);
* ``terminal_fx`` — native result translated once at the terminal EUR/USD rate
  (legacy headline, explicitly an upper bound);
* ``eur_ledger``  — annual EUR tax/FX replay (real-life French CTO view).

New pre-registrations default to ``eur_ledger`` so a result cannot be mistaken
for the optimistic terminal-FX headline.  Historical rows may be imported with
``accounting_mode=""`` when the original mode is unknown.

``results/eval_trials.csv`` is the committed historical snapshot for workstream
D.  New branches should use ``results/trials.csv`` (the protocol's name) or a
branch-local path; pass ``path=`` explicitly to avoid clobbering another branch.
"""

from __future__ import annotations

import csv
import json
import os
from pathlib import Path
import tempfile
from typing import Iterable, Mapping

import pandas as pd

FIELDS = [
    "id",
    "date",
    "branch",
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
    "accounting_mode",
    "verdict",
    "notes",
    "pass_rule",
    "status",
    "commit",
    "source",
]

VERDICTS = {"", "accept", "paper", "reject", "preregistered", "inconclusive"}
STATUSES = {"preregistered", "running", "complete", "abandoned"}


class TrialRegistry:
    def __init__(self, path: str | Path = "results/trials.csv"):
        self.path = Path(path)

    # -- persistence -----------------------------------------------------
    def read(self) -> pd.DataFrame:
        if not self.path.exists():
            return pd.DataFrame(columns=FIELDS)
        df = pd.read_csv(self.path, dtype=str, keep_default_na=False)
        # Compatibility with the first draft of this branch, which used the
        # shorter column name in an uncommitted registry.
        if "accounting_mode" not in df.columns and "accounting" in df.columns:
            df = df.rename(columns={"accounting": "accounting_mode"})
        for col in FIELDS:
            if col not in df.columns:
                df[col] = ""
        return df[FIELDS]

    def _write(self, df: pd.DataFrame) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = tempfile.NamedTemporaryFile(
            "w", newline="", delete=False, dir=self.path.parent, prefix=".trials-"
        )
        try:
            df.to_csv(tmp.name, index=False, lineterminator="\n")
            tmp.close()
            os.replace(tmp.name, self.path)
        finally:
            if not tmp.closed:
                tmp.close()
            if os.path.exists(tmp.name):
                os.unlink(tmp.name)

    def _upsert(self, row: Mapping[str, object], replace: bool) -> pd.Series:
        row = dict(row)
        if "accounting" in row and "accounting_mode" not in row:
            row["accounting_mode"] = row.pop("accounting")
        clean = {k: "" if v is None else str(v) for k, v in row.items()}
        unknown = set(clean) - set(FIELDS)
        if unknown:
            raise ValueError(f"unknown trial fields: {sorted(unknown)}")
        missing = [k for k in FIELDS if k not in clean]
        for k in missing:
            clean[k] = ""
        self._validate(clean)
        df = self.read()
        hit = df.index[df["id"] == clean["id"]].tolist()
        if hit and not replace:
            raise ValueError(f"trial id already registered: {clean['id']}")
        if hit:
            df.loc[hit[0]] = [clean[k] for k in FIELDS]
        else:
            df = pd.concat([df, pd.DataFrame([clean])[FIELDS]], ignore_index=True)
        self._write(df)
        return pd.Series(clean)

    @staticmethod
    def _validate(row: Mapping[str, str]) -> None:
        if not row["id"].strip():
            raise ValueError("trial id is required")
        if row["verdict"] not in VERDICTS:
            raise ValueError(f"verdict must be one of {sorted(VERDICTS)}")
        if row["status"] and row["status"] not in STATUSES:
            raise ValueError(f"status must be one of {sorted(STATUSES)}")
        if row["accounting_mode"] and row["accounting_mode"] not in {
            "native",
            "terminal_fx",
            "eur_ledger",
        }:
            raise ValueError(
                "accounting_mode must be native, terminal_fx or eur_ledger"
            )
        try:
            n = int(row["n_trials"] or 1)
        except ValueError as exc:
            raise ValueError("n_trials must be an integer") from exc
        if n < 1:
            raise ValueError("n_trials must be >= 1 (include failures)")
        row["n_trials"] = str(n)  # type: ignore[index]
        if row["params_json"]:
            try:
                json.loads(row["params_json"])
            except json.JSONDecodeError as exc:
                raise ValueError("params_json is not valid JSON") from exc

    # -- API -------------------------------------------------------------
    def preregister(
        self,
        id: str,
        hypothesis: str,
        pass_rule: str,
        *,
        branch: str = "",
        universe: str = "",
        start: str = "",
        end: str = "",
        params: Mapping[str, object] | None = None,
        n_trials: int = 1,
        metric: str = "",
        cost_model: str = "French CTO 31.4% PFU + 5 bps spread",
        accounting_mode: str = "eur_ledger",
        date: str | None = None,
        commit: str = "",
        source: str = "",
        notes: str = "",
    ) -> pd.Series:
        """Write the hypothesis and pass/fail rule before running the sweep."""
        row = dict(
            id=id,
            date=date or pd.Timestamp.today().date().isoformat(),
            branch=branch,
            hypothesis=hypothesis,
            universe=universe,
            start=start,
            end=end,
            params_json=json.dumps(params or {}, sort_keys=True),
            n_trials=n_trials,
            metric=metric,
            value="",
            oos_value="",
            cost_model=cost_model,
            accounting_mode=accounting_mode,
            verdict="preregistered",
            notes=notes,
            pass_rule=pass_rule,
            status="preregistered",
            commit=commit,
            source=source,
        )
        return self._upsert(row, replace=False)

    def complete(
        self,
        id: str,
        *,
        value: float | str,
        oos_value: float | str = "",
        verdict: str,
        n_trials: int | None = None,
        metric: str | None = None,
        accounting_mode: str | None = None,
        notes: str | None = None,
        commit: str | None = None,
    ) -> pd.Series:
        """Fill in the result of a pre-registered trial."""
        if verdict not in {"accept", "paper", "reject", "inconclusive"}:
            raise ValueError("complete() needs accept/paper/reject/inconclusive")
        df = self.read()
        hit = df.index[df["id"] == id].tolist()
        if not hit:
            raise KeyError(f"unknown trial id: {id}")
        i = hit[0]
        df.loc[i, "value"] = str(value)
        if oos_value != "":
            df.loc[i, "oos_value"] = str(oos_value)
        df.loc[i, "verdict"] = verdict
        df.loc[i, "status"] = "complete"
        if n_trials is not None:
            df.loc[i, "n_trials"] = str(int(n_trials))
        if metric is not None:
            df.loc[i, "metric"] = metric
        if accounting_mode is not None:
            if accounting_mode not in {"native", "terminal_fx", "eur_ledger"}:
                raise ValueError(
                    "accounting_mode must be native, terminal_fx or eur_ledger"
                )
            df.loc[i, "accounting_mode"] = accounting_mode
        if notes is not None:
            df.loc[i, "notes"] = notes
        if commit is not None:
            df.loc[i, "commit"] = commit
        self._write(df)
        return df.loc[i]

    def record(self, **kwargs) -> pd.Series:
        """Insert/update one complete row, used to import historical evidence."""
        replace = bool(kwargs.pop("replace", False))
        kwargs.setdefault("status", "complete")
        kwargs.setdefault("verdict", "inconclusive")
        kwargs.setdefault("accounting_mode", "")
        return self._upsert(kwargs, replace=replace)

    def import_records(
        self, records: Iterable[Mapping[str, object]], replace: bool = False
    ) -> int:
        n = 0
        for rec in records:
            self.record(**dict(rec), replace=replace)
            n += 1
        return n

    # -- reporting -------------------------------------------------------
    def count(self, branch: str | None = None, completed_only: bool = True) -> int:
        df = self.read()
        if branch is not None:
            df = df[df["branch"] == branch]
        if completed_only:
            df = df[df["verdict"].isin(["accept", "paper", "reject", "inconclusive"])]
        return int(len(df))

    def trial_count(
        self, branch: str | None = None, exclude_branch: str | None = None
    ) -> int:
        """Sum of ``n_trials`` over completed experiments (the DSR N).

        ``exclude_branch`` lets reports omit accounting-mode/reporting rows that
        are not independent strategy trials (for example the ``v2/data-eval``
        baseline reproduction rows).
        """
        df = self.read()
        if branch is not None:
            df = df[df["branch"] == branch]
        if exclude_branch is not None:
            df = df[df["branch"] != exclude_branch]
        df = df[df["verdict"].isin(["accept", "paper", "reject", "inconclusive"])]
        return int(pd.to_numeric(df["n_trials"], errors="coerce").fillna(1).sum())

    def summary(self) -> pd.DataFrame:
        df = self.read()
        if df.empty:
            return pd.DataFrame(columns=["branch", "verdict", "experiments", "trials"])
        out = (
            df.assign(trials=pd.to_numeric(df["n_trials"], errors="coerce").fillna(1))
            .groupby(["branch", "verdict"], dropna=False)
            .agg(experiments=("id", "count"), trials=("trials", "sum"))
            .reset_index()
            .sort_values(["branch", "verdict"])
        )
        return out
