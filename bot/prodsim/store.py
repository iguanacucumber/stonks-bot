"""Durable SQLite store for the v12 production simulation.

The store is intentionally stdlib-only (``sqlite3`` + ``json``).  It keeps four
things:

* an append-only event journal with a monotonically increasing ``seq``;
* the latest snapshot of every order, keyed by broker ``order_id`` and unique by
  ``client_order_id`` (the idempotency key);
* account snapshots;
* a small meta/schema-version area.

The database runs in WAL mode.  This is still a simulation: the file lives on
local disk and no part of this module talks to a broker or a network.

Schema version 1
----------------
``events(seq, ts, kind, payload)``
    Append-only.  ``seq`` is the cursor used by ``GET /events?since=``.
``orders(order_id, client_order_id, status, updated_at, payload)``
    ``client_order_id`` is UNIQUE, so a duplicate id can never create a second
    durable order.
``account_snapshots(id, ts, payload)``
``meta(key, value)``

``PRAGMA user_version`` holds ``SCHEMA_VERSION``.  A newer database is refused
rather than opened with a possibly wrong schema.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import asdict, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .contract import (
    AccountSnapshot,
    Fill,
    OrderState,
    PositionSnapshot,
    iso,
    jsonable,
    utc_now,
)

SCHEMA_VERSION = 1
MAX_EVENT_LIMIT = 10_000
OPEN_STATUSES = ("NEW", "ACKNOWLEDGED", "PARTIAL")
VALID_ORDER_STATUSES = (
    "NEW",
    "ACKNOWLEDGED",
    "PARTIAL",
    "FILLED",
    "CANCELED",
    "REJECTED",
    "EXPIRED",
)


class StoreError(RuntimeError):
    """Base class for durable-store failures."""


class StoreCorruptionError(StoreError):
    """A durable row exists but cannot be decoded."""


class SchemaVersionError(StoreError):
    """The database was created by an incompatible newer version."""


class StoreConflictError(StoreError):
    """A write would violate the client-order-id uniqueness rule."""


def _parse_ts(value: Any, what: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise StoreCorruptionError(f"{what} is not an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise StoreCorruptionError(
            f"{what} is not a valid timestamp: {value!r}"
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _filtered(cls: type, raw: Mapping[str, Any], what: str) -> dict[str, Any]:
    names = {f.name for f in fields(cls)}
    if not isinstance(raw, Mapping):
        raise StoreCorruptionError(f"{what} payload is not an object")
    return {k: v for k, v in raw.items() if k in names}


def _fill_from_payload(raw: Mapping[str, Any]) -> Fill:
    data = _filtered(Fill, raw, "fill")
    try:
        data["ts"] = _parse_ts(data.get("ts"), "fill.ts")
        return Fill(**data)
    except (StoreCorruptionError, TypeError, KeyError) as exc:
        if isinstance(exc, StoreCorruptionError):
            raise
        raise StoreCorruptionError(f"invalid fill payload: {exc}") from exc


def order_to_payload(order: OrderState) -> dict[str, Any]:
    """Serialize an order and its nested fills to JSON primitives."""
    return jsonable(asdict(order))


def order_from_payload(raw: Mapping[str, Any]) -> OrderState:
    data = _filtered(OrderState, raw, "order")
    fills_raw = data.pop("fills", []) or []
    try:
        data["created_at"] = _parse_ts(data.get("created_at"), "order.created_at")
        data["updated_at"] = _parse_ts(data.get("updated_at"), "order.updated_at")
        fills = [_fill_from_payload(f) for f in fills_raw]
        return OrderState(fills=fills, **data)
    except (StoreCorruptionError, TypeError, KeyError) as exc:
        if isinstance(exc, StoreCorruptionError):
            raise
        raise StoreCorruptionError(f"invalid order payload: {exc}") from exc


def account_from_payload(raw: Mapping[str, Any]) -> AccountSnapshot:
    data = _filtered(AccountSnapshot, raw, "account")
    positions_raw = data.pop("positions", []) or []
    try:
        data["ts"] = _parse_ts(data.get("ts"), "account.ts")
        positions = [
            PositionSnapshot(**_filtered(PositionSnapshot, p, "position"))
            for p in positions_raw
        ]
        return AccountSnapshot(positions=positions, **data)
    except (StoreCorruptionError, TypeError, KeyError) as exc:
        if isinstance(exc, StoreCorruptionError):
            raise
        raise StoreCorruptionError(f"invalid account payload: {exc}") from exc


def _json_dump(value: Any, what: str) -> str:
    try:
        return json.dumps(
            jsonable(value),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise StoreError(f"{what} is not JSON serializable: {exc}") from exc


def _json_load(text: str, what: str) -> Any:
    try:
        return json.loads(text)
    except (TypeError, ValueError) as exc:
        raise StoreCorruptionError(f"{what} is not valid JSON: {exc}") from exc


class SQLiteStore:
    """Local durable store implementing :class:`contract.StoreProtocol`.

    ``SCHEMA_VERSION`` is also available as a class attribute for callers that
    want to check compatibility without importing the module constant.

    The connection is guarded by a re-entrant lock; the HTTP server may call the
    store from several threads.  Writes that need to be atomic (an order and its
    journal events) use ``save_order_with_events``.
    """

    SCHEMA_VERSION = SCHEMA_VERSION

    def __init__(self, path: str | Path = "state/prodsim/prodsim.sqlite3") -> None:
        candidate = Path(path).expanduser()
        if str(path) != ":memory:" and candidate.exists() and candidate.is_dir():
            # Friendly with RunConfig.state_dir: treat an existing directory as
            # the state directory and keep the database inside it.
            candidate = candidate / "prodsim.sqlite3"
        self.path = ":memory:" if str(path) == ":memory:" else str(candidate)
        self._memory = self.path == ":memory:"
        self._lock = threading.RLock()
        self._closed = False
        self._conn: sqlite3.Connection | None = None

        if not self._memory:
            parent = candidate.resolve().parent
            parent.mkdir(parents=True, exist_ok=True)

        conn: sqlite3.Connection | None = None
        try:
            conn = sqlite3.connect(
                self.path,
                timeout=5.0,
                check_same_thread=False,
                isolation_level=None,
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=5000")
            if not self._memory:
                mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
                if str(mode).lower() != "wal":
                    raise StoreError(
                        f"could not enable WAL on {self.path!r} (mode={mode!r})"
                    )
                conn.execute("PRAGMA synchronous=FULL")
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if version == 0:
                self._create_schema(conn)
                version = SCHEMA_VERSION
            elif version != SCHEMA_VERSION:
                raise SchemaVersionError(
                    f"database schema version {version} is not supported "
                    f"(expected {SCHEMA_VERSION})"
                )
            self._conn = conn
            self._check_integrity_header(conn)
        except (StoreError, SchemaVersionError):
            if conn is not None:
                conn.close()
            raise
        except sqlite3.DatabaseError as exc:
            if conn is not None:
                conn.close()
            raise StoreError(
                f"could not open SQLite store {self.path!r}: {exc}"
            ) from exc
        except Exception:
            if conn is not None:
                conn.close()
            raise

    # -- lifecycle ---------------------------------------------------------

    def __enter__(self) -> SQLiteStore:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self._conn is not None:
                try:
                    if not self._memory:
                        self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                except sqlite3.Error:
                    pass
                self._conn.close()
                self._conn = None

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def schema_version(self) -> int:
        with self._lock:
            conn = self._connection()
            return int(conn.execute("PRAGMA user_version").fetchone()[0])

    @property
    def journal_mode(self) -> str:
        with self._lock:
            conn = self._connection()
            return str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()

    def integrity_check(self) -> str:
        with self._lock:
            conn = self._connection()
            return str(conn.execute("PRAGMA integrity_check").fetchone()[0])

    # -- events ------------------------------------------------------------

    def append(self, kind: str, payload: Mapping[str, Any]) -> int:
        with self._lock:
            conn = self._connection()
            return self._insert_event(self._cursor(conn), kind, payload)

    def events(
        self, since: int = 0, limit: int = 100, kind: str | None = None
    ) -> list[dict[str, Any]]:
        """Return journal rows with ``seq > since``, in sequence order.

        ``seq=0`` is the beginning.  ``limit`` is capped at ``MAX_EVENT_LIMIT``
        so a hostile query cannot exhaust memory.
        """
        with self._lock:
            conn = self._connection()
            since = max(0, int(since))
            limit = max(0, min(int(limit), MAX_EVENT_LIMIT))
            if limit == 0:
                return []
            if kind:
                rows = conn.execute(
                    "SELECT seq, ts, kind, payload FROM events "
                    "WHERE seq > ? AND kind = ? ORDER BY seq LIMIT ?",
                    (since, str(kind), limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT seq, ts, kind, payload FROM events "
                    "WHERE seq > ? ORDER BY seq LIMIT ?",
                    (since, limit),
                ).fetchall()
            out: list[dict[str, Any]] = []
            for row in rows:
                out.append(
                    {
                        "seq": int(row["seq"]),
                        "ts": row["ts"],
                        "kind": row["kind"],
                        "payload": _json_load(row["payload"], f"event {row['seq']}"),
                    }
                )
            return out

    def last_event_seq(self) -> int:
        with self._lock:
            conn = self._connection()
            row = conn.execute("SELECT MAX(seq) AS seq FROM events").fetchone()
            return int(row["seq"] or 0)

    # -- orders ------------------------------------------------------------

    def save_order(self, order: OrderState) -> None:
        self.save_order_with_events(order, ())

    def save_order_and_event(
        self,
        order: OrderState,
        kind: str = "order",
        payload: Mapping[str, Any] | None = None,
    ) -> int:
        event = payload if payload is not None else {"kind": kind}
        seqs = self.save_order_with_events(order, ((kind, event),))
        return seqs[0] if seqs else 0

    def save_order_with_events(
        self,
        order: OrderState,
        events: object = (),
    ) -> list[int]:
        """Atomically upsert ``order`` and append ``events``.

        ``events`` is an iterable of ``(kind, payload)`` pairs.  Returns the new
        event sequence numbers.  This is an additive convenience above
        ``StoreProtocol`` used by the OMS so a crash cannot persist a transition
        without its audit event.
        """
        pairs = list(events)  # type: ignore[arg-type]
        with self._lock:
            conn = self._connection()
            cur = self._cursor(conn)
            try:
                cur.execute("BEGIN IMMEDIATE")
                self._upsert_order(cur, order)
                seqs = [
                    self._insert_event(cur, str(kind), payload)
                    for kind, payload in pairs
                ]
                cur.execute("COMMIT")
                return seqs
            except sqlite3.IntegrityError as exc:
                self._rollback(cur)
                raise StoreConflictError(
                    f"order {order.order_id!r} violates order uniqueness: {exc}"
                ) from exc
            except Exception:
                self._rollback(cur)
                raise

    def load_order(self, order_id: str) -> OrderState | None:
        with self._lock:
            conn = self._connection()
            row = conn.execute(
                "SELECT payload FROM orders WHERE order_id = ?", (str(order_id),)
            ).fetchone()
            if row is None:
                return None
            return order_from_payload(_json_load(row["payload"], f"order {order_id}"))

    def load_by_client_order_id(self, client_order_id: str) -> OrderState | None:
        with self._lock:
            conn = self._connection()
            row = conn.execute(
                "SELECT payload FROM orders WHERE client_order_id = ?",
                (str(client_order_id),),
            ).fetchone()
            if row is None:
                return None
            return order_from_payload(
                _json_load(row["payload"], f"order {client_order_id}")
            )

    def list_orders(
        self, status: str | None = None, limit: int = 100
    ) -> list[OrderState]:
        """List orders newest-first, optionally filtered by status.

        ``status`` accepts a single status, ``OPEN`` for the three live statuses,
        or a comma-separated list such as ``PARTIAL,FILLED``.
        """
        with self._lock:
            conn = self._connection()
            limit = max(0, int(limit))
            if limit == 0:
                return []
            clauses: list[str] = []
            params: list[Any] = []
            if status:
                wanted: list[str] = []
                for part in str(status).split(","):
                    item = part.strip().upper()
                    if not item:
                        continue
                    if item == "OPEN":
                        wanted.extend(OPEN_STATUSES)
                    else:
                        wanted.append(item)
                wanted = [w for w in dict.fromkeys(wanted) if w in VALID_ORDER_STATUSES]
                if not wanted:
                    return []
                clauses.append("status IN (" + ",".join("?" for _ in wanted) + ")")
                params.extend(wanted)
            where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
            rows = conn.execute(
                "SELECT payload FROM orders"
                + where
                + " ORDER BY updated_at DESC, rowid DESC LIMIT ?",
                (*params, limit),
            ).fetchall()
            return [
                order_from_payload(_json_load(row["payload"], "order list row"))
                for row in rows
            ]

    def open_orders(self, limit: int = 100_000) -> list[OrderState]:
        return self.list_orders(status="OPEN", limit=limit)

    # -- account snapshots -------------------------------------------------

    def save_snapshot(self, account: AccountSnapshot) -> None:
        payload = _json_dump(account, "account snapshot")
        with self._lock:
            conn = self._connection()
            conn.execute(
                "INSERT INTO account_snapshots (ts, payload) VALUES (?, ?)",
                (iso(account.ts), payload),
            )

    def last_snapshot(self) -> AccountSnapshot | None:
        with self._lock:
            conn = self._connection()
            row = conn.execute(
                "SELECT payload FROM account_snapshots ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            return account_from_payload(_json_load(row["payload"], "account snapshot"))

    # -- internals ---------------------------------------------------------

    def _connection(self) -> sqlite3.Connection:
        if self._closed or self._conn is None:
            raise StoreError("store is closed")
        return self._conn

    @staticmethod
    def _cursor(conn: sqlite3.Connection) -> sqlite3.Cursor:
        return conn.cursor()

    @staticmethod
    def _rollback(cur: sqlite3.Cursor) -> None:
        try:
            cur.execute("ROLLBACK")
        except sqlite3.Error:
            pass

    def _create_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS events (
                seq     INTEGER PRIMARY KEY AUTOINCREMENT,
                ts      TEXT NOT NULL,
                kind    TEXT NOT NULL,
                payload TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_events_kind_seq ON events(kind, seq);

            CREATE TABLE IF NOT EXISTS orders (
                order_id        TEXT PRIMARY KEY,
                client_order_id TEXT NOT NULL UNIQUE,
                status          TEXT NOT NULL,
                updated_at      TEXT NOT NULL,
                payload         TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_orders_status
                ON orders(status, updated_at);

            CREATE TABLE IF NOT EXISTS account_snapshots (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                ts      TEXT NOT NULL,
                payload TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS meta (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TRIGGER IF NOT EXISTS events_append_only_update
            BEFORE UPDATE ON events
            BEGIN
                SELECT RAISE(ABORT, 'event journal is append-only');
            END;

            CREATE TRIGGER IF NOT EXISTS events_append_only_delete
            BEFORE DELETE ON events
            BEGIN
                SELECT RAISE(ABORT, 'event journal is append-only');
            END;
            """
        )
        conn.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        conn.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES ('created_at', ?)",
            (iso(utc_now()),),
        )
        conn.execute(f"PRAGMA user_version = {int(SCHEMA_VERSION)}")

    def _check_integrity_header(self, conn: sqlite3.Connection) -> None:
        row = conn.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()
        if row is None:
            raise StoreCorruptionError("store is missing meta.schema_version")
        try:
            version = int(row["value"])
        except (TypeError, ValueError) as exc:
            raise StoreCorruptionError("store has an invalid schema_version") from exc
        if version != SCHEMA_VERSION:
            raise SchemaVersionError(
                f"meta schema version {version} does not match {SCHEMA_VERSION}"
            )

    def _insert_event(
        self, cur: sqlite3.Cursor, kind: str, payload: Mapping[str, Any]
    ) -> int:
        kind_text = str(kind).strip()
        if not kind_text:
            raise StoreError("event kind is required")
        if not isinstance(payload, Mapping):
            raise StoreError("event payload must be a mapping")
        body = _json_dump(payload, "event payload")
        cur.execute(
            "INSERT INTO events(ts, kind, payload) VALUES (?, ?, ?)",
            (iso(utc_now()), kind_text, body),
        )
        seq = cur.lastrowid
        if seq is None:
            raise StoreError("sqlite did not return an event seq")
        return int(seq)

    def _upsert_order(self, cur: sqlite3.Cursor, order: OrderState) -> None:
        body = _json_dump(order_to_payload(order), f"order {order.order_id}")
        cur.execute(
            """
            INSERT INTO orders(order_id, client_order_id, status, updated_at, payload)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(order_id) DO UPDATE SET
                client_order_id = excluded.client_order_id,
                status          = excluded.status,
                updated_at      = excluded.updated_at,
                payload         = excluded.payload
            """,
            (
                str(order.order_id),
                str(order.client_order_id),
                str(order.status),
                iso(order.updated_at),
                body,
            ),
        )


# Friendly alias for callers that do not care about the backend name.
Store = SQLiteStore


__all__ = [
    "MAX_EVENT_LIMIT",
    "SCHEMA_VERSION",
    "SQLiteStore",
    "SchemaVersionError",
    "Store",
    "StoreConflictError",
    "StoreCorruptionError",
    "StoreError",
    "account_from_payload",
    "order_from_payload",
    "order_to_payload",
]
