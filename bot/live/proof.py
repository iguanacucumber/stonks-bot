"""Append-only forward proof lockbox.

The gate count in ``results/paper_audit.csv`` is honest only while every
verified session can be traced back to a real paper row.  This module stores
one JSONL record per verified ``(date, book, market)``:

- the session date and the gate count at that date;
- SHA-256 hashes of ``paper_audit.csv``, the book's ``paper_daily*.csv`` and
  the live state file (state is local and may be absent on a clean checkout);
- the verification command and the last completed session at lock time;
- a hash chain (``prev_hash`` -> ``record_hash``) so an edited or reordered
  line is detectable.

It is deliberately not a second gate.  ``forward_gate.collect_gate`` remains
the only counter; the lockbox rejects any date the gate did not count, any
non-session/future date, duplicates, and audit rows with no close-valid paper
row.  ``scripts/forward_watchdog.py`` compares the two and fails closed.

The lockbox cannot prevent truncation of the file; it can make an edit,
reorder or duplicate detectable.  Commit/push it with the results so a later
run can compare against an external copy.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from . import calendar

PROOF_VERSION = 1
GENESIS = "0" * 64
DEFAULT_NAME = "forward_proof.jsonl"
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_STATE_KEYS = ("paper_audit", "paper_daily", "live_state")


class ProofError(RuntimeError):
    """A lockbox record would be fake, duplicated or unverifiable."""


def sha256_file(path: str | Path) -> str:
    """Hash a file without reading it all into memory."""
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _canonical(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


def compute_record_hash(record: dict[str, Any]) -> str:
    """Hash every field except ``record_hash`` itself."""
    body = {k: v for k, v in record.items() if k != "record_hash"}
    return hashlib.sha256(_canonical(body)).hexdigest()


def _truthy(series: pd.Series) -> pd.Series:
    return series.astype(str).str.lower().isin(["true", "1", "yes"])


def _paper_name(book: str) -> str:
    return "paper_daily.csv" if book == "monthly" else "paper_daily_tax.csv"


def _paper_path(results_dir: Path, book: str) -> Path:
    return Path(results_dir) / _paper_name(book)


def _date_text(value: Any) -> str:
    return str(pd.Timestamp(value).date())


def audit_verified_dates(
    results_dir: str | Path, book: str, market: str
) -> dict[str, bool]:
    """Map each audited date to its verified flag, rejecting duplicate dates.

    A duplicate date is not evidence: the gate excludes it and the lockbox must
    not pick one copy.
    """
    path = Path(results_dir) / "paper_audit.csv"
    if not path.exists():
        raise ProofError(f"missing {path.name}")
    df = pd.read_csv(path)
    required = {"book", "market", "date", "verified"}
    missing = required - set(df.columns)
    if missing:
        raise ProofError(f"{path.name} missing columns {sorted(missing)}")
    df = df[(df["book"].astype(str) == book) & (df["market"].astype(str) == market)]
    df = df.assign(
        date=pd.to_datetime(df["date"], errors="coerce").dt.date,
        _verified=_truthy(df["verified"]),
    )
    if df["date"].isna().any():
        raise ProofError(f"{path.name} has an unreadable date for {book}/{market}")
    dup = df["date"].duplicated(keep=False)
    if dup.any():
        days = sorted({str(d) for d in df.loc[dup, "date"]})
        raise ProofError(
            f"duplicate audit date(s) for {book}/{market}: {', '.join(days)}"
        )
    return {
        str(day): bool(flag)
        for day, flag in zip(df["date"], df["_verified"], strict=True)
    }


def paper_close_valid_dates(
    results_dir: str | Path, book: str, market: str
) -> set[str]:
    """Dates with at least one close-valid paper row for this book/market."""
    path = _paper_path(results_dir, book)
    if not path.exists():
        raise ProofError(f"missing {path.name}")
    df = pd.read_csv(path)
    if "market" not in df.columns or "quote_date" not in df.columns:
        raise ProofError(f"{path.name} missing market/quote_date")
    df = df[df["market"].astype(str) == market]
    if "close_valid" not in df.columns:
        return set()
    valid = _truthy(df["close_valid"])
    dates = pd.to_datetime(df.loc[valid, "quote_date"], errors="coerce").dt.date
    return {str(d) for d in dates.dropna()}


def _validate_record(
    record: dict[str, Any],
    *,
    prev_hash: str,
    seen: set[tuple[str, str, str]],
    counts: dict[tuple[str, str], list[tuple[str, dict]]],
    now: pd.Timestamp | None,
) -> None:
    required = {
        "version",
        "date",
        "book",
        "market",
        "gate",
        "files",
        "verification",
        "prev_hash",
        "record_hash",
    }
    missing = required - set(record)
    if missing:
        raise ProofError(f"proof record missing fields {sorted(missing)}")
    if record["version"] != PROOF_VERSION:
        raise ProofError(f"unsupported proof version {record['version']!r}")
    try:
        day = pd.Timestamp(record["date"]).normalize()
    except Exception as exc:
        raise ProofError(f"unreadable proof date {record['date']!r}") from exc
    if not calendar.is_session(day):
        raise ProofError(f"proof date {day.date()} is not an NYSE session")
    book, market = str(record["book"]), str(record["market"])
    key = (str(day.date()), book, market)
    if key in seen:
        raise ProofError(f"duplicate proof record {key}")
    seen.add(key)

    gate = record["gate"]
    files = record["files"]
    verification = record["verification"]
    if not isinstance(gate, dict) or not isinstance(files, dict):
        raise ProofError(f"malformed proof record {key}")
    if not isinstance(verification, dict):
        raise ProofError(f"malformed verification block {key}")
    try:
        verified = int(gate.get("verified", 0))
        target = int(gate.get("target", 0))
        observed = int(gate.get("observed", verified))
    except (TypeError, ValueError) as exc:
        raise ProofError(f"invalid gate count for {key}") from exc
    if target < 1 or verified < 1 or observed < verified:
        raise ProofError(f"invalid gate count for {key}")
    status = gate.get("status")
    expected_status = "PASS" if verified >= target else "WAIT"
    if status != expected_status:
        raise ProofError(
            f"gate status {status!r} != {expected_status} for {key} "
            f"({verified}/{target})"
        )
    if files.get("paper_audit") is None or files.get("paper_daily") is None:
        raise ProofError(f"missing evidence hashes for {key}")
    for name in ("paper_audit", "paper_daily"):
        block = files.get(name)
        digest = block.get("sha256") if isinstance(block, dict) else None
        if not _HASH_RE.match(str(digest or "")):
            raise ProofError(f"invalid {name} hash for {key}")
        if not str(block.get("path") or ""):
            raise ProofError(f"missing {name} path for {key}")
    state = files.get("live_state")
    if not isinstance(state, dict):
        raise ProofError(f"missing live_state block for {key}")
    state_hash = state.get("sha256")
    if state_hash is not None and not _HASH_RE.match(str(state_hash)):
        raise ProofError(f"invalid live_state hash for {key}")
    if bool(state.get("present")) != (state_hash is not None):
        raise ProofError(f"inconsistent live_state presence for {key}")
    if verification.get("close_valid") is not True:
        raise ProofError(f"audit-only record for {key}: no close-valid flag")
    if not str(verification.get("command") or ""):
        raise ProofError(f"missing verification command for {key}")
    session_text = str(verification.get("session") or "")
    try:
        session = pd.Timestamp(session_text).normalize()
    except Exception as exc:
        raise ProofError(f"invalid verification session for {key}") from exc
    if not calendar.is_session(session):
        raise ProofError(f"verification session {session.date()} is not a session")
    if day > session:
        raise ProofError(f"future proof date {day.date()} > session {session.date()}")
    try:
        recorded = pd.Timestamp(record.get("recorded_at"))
    except Exception as exc:
        raise ProofError(f"invalid recorded_at for {key}") from exc
    if pd.isna(recorded):
        raise ProofError(f"invalid recorded_at for {key}")
    if recorded.tzinfo is None:
        recorded = recorded.tz_localize("UTC")
    if now is not None and recorded > now:
        raise ProofError(f"proof record {key} is dated in the future")
    completed = calendar.target_session(recorded.to_pydatetime())
    if completed < session:
        raise ProofError(
            f"proof record {key} was made before {session.date()} completed "
            f"(last completed {completed.date()})"
        )

    if record["record_hash"] != compute_record_hash(record):
        raise ProofError(f"proof record hash mismatch for {key}")
    if record["prev_hash"] != prev_hash:
        raise ProofError(f"proof chain break before {key}")

    counts.setdefault((book, market), []).append((str(day.date()), record))
    ordered = sorted(counts[(book, market)], key=lambda x: x[0])
    for index, (_, item) in enumerate(ordered, start=1):
        if int(item["gate"]["verified"]) != index:
            raise ProofError(
                f"gate count {item['gate']['verified']} != {index} at "
                f"{item['date']} for {book}/{market}"
            )


def _now_timestamp(now: str | pd.Timestamp | None) -> pd.Timestamp | None:
    if now is None:
        return None
    now_ts = pd.Timestamp(now)
    if now_ts.tzinfo is None:
        now_ts = now_ts.tz_localize("UTC")
    return now_ts


def validate_records(
    records: Iterable[dict[str, Any]],
    *,
    now: str | pd.Timestamp | None = None,
) -> None:
    """Validate a full in-memory chain before it is written or trusted."""
    prev = GENESIS
    seen: set[tuple[str, str, str]] = set()
    counts: dict[tuple[str, str], list[tuple[str, dict]]] = {}
    now_ts = _now_timestamp(now)
    for index, record in enumerate(records, start=1):
        if not isinstance(record, dict):
            raise ProofError(f"record {index} is not an object")
        _validate_record(
            record,
            prev_hash=prev,
            seen=seen,
            counts=counts,
            now=now_ts,
        )
        prev = str(record["record_hash"])


def load_records(
    path: str | Path | None,
    *,
    now: str | pd.Timestamp | None = None,
) -> list[dict[str, Any]]:
    """Read and fully verify the hash chain; return records or raise."""
    if path is None:
        return []
    path = Path(path)
    if not path.exists():
        return []
    text = path.read_text()
    if text and not text.endswith("\n"):
        raise ProofError(f"{path.name} ends with a partial line")
    records: list[dict[str, Any]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            raise ProofError(f"{path.name}:{lineno}: blank line")
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ProofError(f"{path.name}:{lineno}: invalid JSON: {exc}") from exc
        if not isinstance(record, dict):
            raise ProofError(f"{path.name}:{lineno}: record is not an object")
        records.append(record)
    validate_records(records, now=now)
    return records


def summary(records: Iterable[dict[str, Any]], path: str | Path | None = None) -> dict:
    recs = list(records)
    last = max((str(r["date"]) for r in recs), default=None)
    books: dict[str, dict] = {}
    for rec in recs:
        key = f"{rec['book']}/{rec['market']}"
        books.setdefault(
            key,
            {"records": 0, "last": None, "verified": 0, "target": None},
        )
        item = books[key]
        item["records"] += 1
        item["last"] = max(filter(None, [item["last"], str(rec["date"])]))
        if int(rec["gate"]["verified"]) >= int(item["verified"]):
            item["verified"] = int(rec["gate"]["verified"])
            item["target"] = int(rec["gate"]["target"])
    return {
        "path": str(path) if path is not None else None,
        "records": len(recs),
        "last": last,
        "books": books,
        "chain": "OK",
    }


def _rel(path: Path, root: Path | None) -> str:
    if root is None:
        return str(path)
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def _state_path(state_dir: Path, book: str, market: str) -> Path:
    return state_dir / f"{book}_{market.lower()}.json"


def _hash_block(path: Path, root: Path | None) -> dict:
    return {
        "path": _rel(path, root),
        "sha256": sha256_file(path) if path.exists() else None,
        "present": path.exists(),
    }


@dataclass
class AppendResult:
    path: str
    appended: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    records: int = 0
    last: str | None = None

    @property
    def chain(self) -> str:
        return "OK"

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "appended": [
                f"{r['date']} {r['book']}/{r['market']}" for r in self.appended
            ],
            "skipped": [f"{r['date']} {r['book']}/{r['market']}" for r in self.skipped],
            "records": self.records,
            "last": self.last,
            "chain": self.chain,
        }


def append_from_report(
    report,
    *,
    results_dir: str | Path,
    state_dir: str | Path | None,
    lockbox: str | Path,
    repo_root: str | Path | None = None,
    now: str | pd.Timestamp | None = None,
    command: str,
    require_state: bool = False,
) -> AppendResult:
    """Append one record for each newly verified session in ``report``.

    ``report`` is the object returned by ``forward_gate.collect_gate``.  The
    gate must not be BLOCKED: a lock is evidence, not a way to bypass a failed
    check.  Existing ``(date, book, market)`` keys are skipped (idempotent),
    never duplicated.
    """
    results_dir = Path(results_dir)
    state_dir = Path(state_dir) if state_dir else None
    root = Path(repo_root) if repo_root is not None else results_dir.parent
    lockbox = Path(lockbox)
    now_ts = pd.Timestamp(now if now is not None else report.now)
    if now_ts.tzinfo is None:
        now_ts = now_ts.tz_localize("UTC")
    recorded_at = now_ts.isoformat()

    if str(report.status).upper() == "BLOCKED":
        raise ProofError("refusing to lock a BLOCKED gate report")

    records = load_records(lockbox, now=now_ts)
    existing_keys = {
        (str(r["date"]), str(r["book"]), str(r["market"])) for r in records
    }
    locked: dict[tuple[str, str], list[str]] = {}
    for rec in records:
        locked.setdefault((str(rec["book"]), str(rec["market"])), []).append(
            str(rec["date"])
        )
    prev = str(records[-1]["record_hash"]) if records else GENESIS
    appended: list[dict] = []
    skipped: list[dict] = []
    report_session = pd.Timestamp(report.session).normalize()

    for bg in report.books:
        book, market = str(bg.book), str(bg.market)
        if str(bg.status).upper() == "BLOCKED":
            raise ProofError(f"refusing to lock blocked book {book}/{market}")
        dates = sorted({str(d) for d in getattr(bg, "verified_dates", [])})
        if not dates:
            continue
        audit = audit_verified_dates(results_dir, book, market)
        close_valid = paper_close_valid_dates(results_dir, book, market)
        for date_text in dates:
            day = pd.Timestamp(date_text).normalize()
            if not calendar.is_session(day):
                raise ProofError(f"{date_text} is not an NYSE session")
            if day > report_session:
                raise ProofError(
                    f"refusing future date {date_text} > completed session "
                    f"{report_session.date()}"
                )
            if date_text not in audit or not audit[date_text]:
                raise ProofError(
                    f"audit-only/false row {date_text} for {book}/{market}"
                )
            if date_text not in close_valid:
                raise ProofError(
                    f"audit-only row {date_text} for {book}/{market}: "
                    "no close-valid paper row"
                )
            key = (date_text, book, market)
            if key in existing_keys:
                skipped.append(
                    {
                        "date": date_text,
                        "book": book,
                        "market": market,
                        "reason": "already locked",
                    }
                )
                continue
            state_path = _state_path(state_dir, book, market) if state_dir else None
            if require_state and (state_path is None or not state_path.exists()):
                raise ProofError(
                    f"{date_text} {book}/{market}: live state is required but "
                    f"missing ({state_path})"
                )
            count = (
                len([d for d in locked.get((book, market), []) if d <= date_text]) + 1
            )
            target = int(bg.target)
            record = {
                "version": PROOF_VERSION,
                "date": date_text,
                "book": book,
                "market": market,
                "recorded_at": recorded_at,
                "gate": {
                    "verified": count,
                    "observed": int(bg.verified),
                    "target": target,
                    "status": "PASS" if count >= target else "WAIT",
                },
                "files": {
                    "paper_audit": _hash_block(results_dir / "paper_audit.csv", root),
                    "paper_daily": _hash_block(_paper_path(results_dir, book), root),
                    "live_state": (
                        _hash_block(state_path, root)
                        if state_path is not None
                        else {"path": "", "sha256": None, "present": False}
                    ),
                },
                "verification": {
                    "command": command,
                    "source": (
                        f"{_rel(results_dir / 'paper_audit.csv', root)} + "
                        f"{_rel(_paper_path(results_dir, book), root)}"
                    ),
                    "session": str(report_session.date()),
                    "close_valid": True,
                },
                "prev_hash": prev,
            }
            record["record_hash"] = compute_record_hash(record)
            appended.append(record)
            existing_keys.add(key)
            locked.setdefault((book, market), []).append(date_text)
            prev = record["record_hash"]

    if appended:
        # Validate the new chain in memory before touching the file. This is
        # what rejects a half-day record built at 13:29 ET even if a caller
        # passes a synthetic report that claims the session is complete.
        validate_records(records + appended, now=now_ts)
        lockbox.parent.mkdir(parents=True, exist_ok=True)
        with lockbox.open("a") as fh:
            for record in appended:
                fh.write(json.dumps(record, sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    all_records = records + appended
    info = summary(all_records, lockbox)
    return AppendResult(
        path=str(lockbox),
        appended=appended,
        skipped=skipped,
        records=info["records"],
        last=info["last"],
    )


def verify_files(record: dict[str, Any], repo_root: str | Path) -> list[str]:
    """Re-hash the current evidence files for one record.

    Only meaningful for the newest record: paper files are append-only and the
    live state changes every run, so old records intentionally no longer match
    the current bytes.  The watchdog uses this for a warning, never as a count.
    """
    root = Path(repo_root)
    issues: list[str] = []
    for name in ("paper_audit", "paper_daily", "live_state"):
        block = record.get("files", {}).get(name, {})
        path_text = str(block.get("path") or "")
        if not path_text:
            continue
        path = root / path_text
        if not path.exists():
            if name != "live_state" and block.get("sha256"):
                issues.append(f"{name} missing now: {path_text}")
            continue
        if block.get("sha256") and sha256_file(path) != block["sha256"]:
            issues.append(f"{name} changed since lock: {path_text}")
    return issues
