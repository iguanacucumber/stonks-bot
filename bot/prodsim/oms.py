"""Simulated order management system for the v12 production simulation.

``SimulatedOMS`` is the durable layer between a strategy/replay loop and an
``ExchangeProtocol``.  It owns order identity, idempotency, the order cache and
the journal/order writes.  It never talks to a real broker.

Key rules
---------
* ``OrderRequest.client_order_id`` is the idempotency key.  Repeating exactly
  the same request returns the original order and never submits again.  Reusing
  the key with a different payload raises :class:`OrderConflict` and journals
  the conflict.
* Every order transition is saved together with its ``contract.order_event``
  inside one SQLite transaction when the store supports
  ``save_order_with_events``.
* Fills are de-duplicated by ``Fill.fill_id``.  A fill that is replayed after a
  restart is merged once, never added twice.
* ``recover()`` reloads orders and journal events, adopts orders that reached
  the exchange before a crash, and reconciles open orders with the exchange.
  Unacknowledged submit intents are *not* resubmitted blindly; they are marked
  ``unconfirmed`` and later submits with that client id fail closed.
* Cancel is idempotent and keeps the exchange's filled quantity.  A cancel race
  (partial fill, then cancel) therefore never loses the filled part.

The module is stdlib-only apart from ``contract`` and the local store.
"""

from __future__ import annotations

import copy
import json
import threading
import uuid
from dataclasses import fields
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from .contract import (
    AccountSnapshot,
    ExchangeProtocol,
    Fill,
    OrderRequest,
    OrderState,
    PositionSnapshot,
    RiskLimits,
    StoreProtocol,
    Tick,
    jsonable,
    order_event,
    utc_now,
)

OPEN_STATUSES = frozenset({"NEW", "ACKNOWLEDGED", "PARTIAL"})
TERMINAL_STATUSES = frozenset({"FILLED", "CANCELED", "REJECTED", "EXPIRED"})
# Recovery is a startup operation, not a per-tick one.  A million rows is far
# beyond the 1k-order load scenario and keeps the list_orders call bounded.
MAX_RECOVERY_ORDERS = 1_000_000
EVENT_PAGE = 1_000


class OMSError(RuntimeError):
    """Base class for OMS failures."""


class OrderConflict(OMSError):
    """The client order id was already used by a different payload."""

    def __init__(self, request: OrderRequest, existing: OrderState) -> None:
        self.request = request
        self.existing = existing
        super().__init__(
            f"client_order_id {request.client_order_id!r} is already assigned to "
            f"order {existing.order_id!r} with a different payload"
        )


# Names an HTTP/facade layer may reasonably look for.
DuplicateOrderError = OrderConflict
IdempotencyConflict = OrderConflict


class OrderSubmitError(OMSError):
    """The exchange could not confirm a submit."""


class OrderUnconfirmedError(OrderSubmitError):
    """A restart found an intent with no broker acknowledgement.

    The order is deliberately not resubmitted because the first submit may have
    reached the broker before the crash.  An operator can reconcile/clear it.
    """

    def __init__(self, client_order_id: str, detail: str = "") -> None:
        self.client_order_id = client_order_id
        self.detail = detail
        message = f"order {client_order_id!r} is unconfirmed after restart"
        if detail:
            message += f": {detail}"
        super().__init__(message)


class OrderNotFound(OMSError, KeyError):
    """No such order exists at the OMS or the exchange."""


class OMSRecoveryError(OMSError):
    """A durable order/journal row could not be replayed safely."""


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _max_ts(*values: datetime | None) -> datetime:
    stamps = [_utc(v) for v in values if v is not None]
    return max(stamps) if stamps else utc_now()


def _parse_ts(value: Any, what: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise OMSRecoveryError(f"{what} is not an ISO timestamp")
    try:
        return _utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError as exc:
        raise OMSRecoveryError(f"{what} is not a valid timestamp: {value!r}") from exc


def _filtered(cls: type, raw: Mapping[str, Any]) -> dict[str, Any]:
    names = {f.name for f in fields(cls)}
    return {k: v for k, v in raw.items() if k in names}


def request_from_payload(raw: Mapping[str, Any]) -> OrderRequest:
    data = _filtered(OrderRequest, raw)
    if "created_at" in data:
        data["created_at"] = _parse_ts(data["created_at"], "request.created_at")
    try:
        return OrderRequest(**data)
    except (TypeError, ValueError) as exc:
        raise OMSRecoveryError(f"invalid order intent payload: {exc}") from exc


def fill_from_payload(raw: Mapping[str, Any]) -> Fill:
    data = _filtered(Fill, raw)
    data["ts"] = _parse_ts(data.get("ts"), "fill.ts")
    try:
        return Fill(**data)
    except TypeError as exc:
        raise OMSRecoveryError(f"invalid fill event payload: {exc}") from exc


def _request_fingerprint(request: OrderRequest) -> tuple[Any, ...]:
    """Semantic payload identity; ``created_at`` is intentionally excluded."""
    return (
        request.symbol,
        request.side,
        float(request.qty),
        request.order_type,
        None if request.limit_price is None else float(request.limit_price),
        request.tif,
        request.strategy,
    )


def _order_fingerprint(order: OrderState) -> tuple[Any, ...]:
    return (
        order.symbol,
        order.side,
        float(order.qty),
        order.order_type,
        None if order.limit_price is None else float(order.limit_price),
        order.tif,
        order.strategy,
    )


def _state_key(order: OrderState) -> str:
    return json.dumps(
        jsonable(order), sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _fill_event(fill: Fill) -> dict[str, Any]:
    payload = jsonable(fill)
    if not isinstance(payload, dict):  # pragma: no cover - defensive
        raise OMSError("fill did not serialize to an object")
    payload["kind"] = "fill"
    return payload


class SimulatedOMS:
    """Durable order lifecycle against a simulated exchange.

    Parameters
    ----------
    exchange:
        Any object implementing ``contract.ExchangeProtocol``.
    store:
        Any object implementing ``contract.StoreProtocol``.  The bundled
        ``SQLiteStore`` additionally offers atomic order+event writes.
    limits:
        ``RiskLimits`` (or a ``RunConfig`` carrying ``.limits``).  Only local
        sanity checks use this; the exchange remains the authority.
    auto_recover:
        Replay the store/journal and reconcile the exchange on construction.
    """

    def __init__(
        self,
        exchange: ExchangeProtocol,
        store: StoreProtocol,
        limits: RiskLimits | Any | None = None,
        *,
        auto_recover: bool = True,
    ) -> None:
        self.exchange = exchange
        self.store = store
        if limits is None:
            limits = RiskLimits()
        elif not isinstance(limits, RiskLimits) and hasattr(limits, "limits"):
            limits = limits.limits
        if not isinstance(limits, RiskLimits):
            raise TypeError("limits must be RiskLimits (or a RunConfig)")
        self.limits = limits

        self._lock = threading.RLock()
        self._orders: dict[str, OrderState] = {}
        self._client_ids: dict[str, str] = {}
        self._seen_fills: set[str] = set()
        self._unconfirmed: dict[str, dict[str, Any]] = {}
        self._last_prices: dict[str, float] = {}
        self._recovery_report: dict[str, Any] = {}
        self._recovered = False

        if auto_recover:
            self._recovery_report = self.recover()

    # ------------------------------------------------------------------
    # Public order API
    # ------------------------------------------------------------------

    @property
    def recovery_report(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._recovery_report)

    def submit(self, request: OrderRequest) -> OrderState:
        """Submit (or return the original) order for ``request``."""
        if not isinstance(request, OrderRequest):
            raise TypeError("submit expects contract.OrderRequest")
        with self._lock:
            client_id = request.client_order_id

            pending = self._unconfirmed.get(client_id)
            if pending is not None:
                raise OrderUnconfirmedError(
                    client_id,
                    "the original submit was never acknowledged; refusing to resubmit",
                )

            existing = self._existing_for_client(client_id)
            if existing is not None:
                if _order_fingerprint(existing) != _request_fingerprint(request):
                    self.store.append(
                        "order_conflict",
                        {
                            "kind": "order_conflict",
                            "client_order_id": client_id,
                            "existing_order_id": existing.order_id,
                            "request": jsonable(request),
                            "existing": order_event(existing),
                        },
                    )
                    raise OrderConflict(request, copy.deepcopy(existing))
                return copy.deepcopy(existing)

            reason = self._local_reject_reason(request)
            if reason:
                return self._record_reject(request, reason)

            intent = {
                "kind": "order_intent",
                "intent_id": f"intent-{uuid.uuid4().hex}",
                "request": jsonable(request),
            }
            # Durable intent *before* the broker call.  If we crash between this
            # write and the ack, recovery knows not to submit it a second time.
            self.store.append("order_intent", intent)

            try:
                state = self.exchange.submit(request)
            except ValueError as exc:
                # Validation errors are raised before acceptance by a sane
                # exchange.  Still check the book once in case the error was
                # produced after an ack.
                found = self._find_exchange_order_by_client(client_id)
                state = (
                    self._adopt_state(found)
                    if found is not None
                    else self._record_reject(request, str(exc))
                )
            except Exception as exc:  # noqa: BLE001 - broker boundary
                state = self._reconcile_submit_exception(request, exc)

            if not isinstance(state, OrderState):
                raise OrderSubmitError(
                    f"exchange returned {type(state).__name__}, expected OrderState"
                )
            if state.client_order_id != client_id:
                # Trust the state but fail closed rather than silently binding
                # an idempotency key to another client id.
                self.store.append(
                    "recovery",
                    {
                        "kind": "client_id_mismatch",
                        "requested_client_order_id": client_id,
                        "returned_client_order_id": state.client_order_id,
                        "order_id": state.order_id,
                    },
                )
                raise OrderSubmitError(
                    f"exchange returned client_order_id "
                    f"{state.client_order_id!r}, expected {client_id!r}"
                )
            return self._adopt_state(state)

    def cancel(self, order_id: str) -> OrderState:
        """Cancel an order; idempotent for terminal orders.

        The exchange's returned state is authoritative for filled quantity, but
        the merge never lets ``filled_qty`` decrease.  This is the cancel-race
        guarantee.
        """
        with self._lock:
            order = self._get_local(order_id)
            if order is None:
                found, known = self._try_exchange_order(order_id)
                if known is not None:
                    order = self._adopt_state(known)
                    if not order.is_open:
                        return order
                try:
                    state = self.exchange.cancel(order_id)
                except KeyError as exc:
                    raise OrderNotFound(order_id) from exc
                if not isinstance(state, OrderState):
                    raise OrderSubmitError(
                        "exchange.cancel returned a non-OrderState"
                    )
                return self._adopt_state(state)

            if not order.is_open:
                return copy.deepcopy(order)

            try:
                state = self.exchange.cancel(order_id)
            except KeyError:
                found, latest = self._try_exchange_order(order_id)
                if found and latest is None:
                    return self._finalize_missing(order)
                if latest is not None:
                    return self._adopt_state(latest)
                raise OrderNotFound(order_id)
            except Exception as exc:  # noqa: BLE001 - broker boundary
                found, latest = self._try_exchange_order(order_id)
                if latest is not None and latest.status not in OPEN_STATUSES:
                    return self._adopt_state(latest)
                if found and latest is None:
                    return self._finalize_missing(order)
                raise OrderSubmitError(
                    f"cancel failed for {order_id!r}: {exc}"
                ) from exc

            if not isinstance(state, OrderState):
                raise OrderSubmitError("exchange.cancel returned a non-OrderState")
            merged, new_fills, _ = self._merge(order, state)
            self._persist_merged(order, merged, new_fills)
            return copy.deepcopy(merged)

    def cancel_all(self, reason: str = "kill switch") -> dict[str, Any]:
        """Best-effort cancel of every open order (used by a kill switch)."""
        with self._lock:
            canceled: list[str] = []
            errors: list[dict[str, str]] = []
            for order in list(self._orders.values()):
                if not order.is_open:
                    continue
                try:
                    result = self.cancel(order.order_id)
                    canceled.append(result.order_id)
                except Exception as exc:  # noqa: BLE001 - keep cancelling
                    errors.append({"order_id": order.order_id, "error": str(exc)})
            if canceled or errors:
                self.store.append(
                    "recovery",
                    {
                        "kind": "cancel_all",
                        "reason": str(reason),
                        "canceled": canceled,
                        "errors": errors,
                    },
                )
            return {"canceled": canceled, "errors": errors}

    def on_tick(self, tick: Tick) -> list[Fill]:
        """Advance the exchange by one tick and durably apply new fills.

        Returns only fills that were not already applied (so replaying a tick or
        restarting a feed cannot double-count).
        """
        if not isinstance(tick, Tick):
            raise TypeError("on_tick expects contract.Tick")
        with self._lock:
            self._last_prices[tick.symbol] = float(tick.last)
            raw_fills = self.exchange.on_tick(tick)
            fills = list(raw_fills or [])
            accepted: list[Fill] = []
            accepted_ids: set[str] = set()
            preexisting = set(self._seen_fills)
            touched: set[str] = set()

            for fill in fills:
                if not isinstance(fill, Fill):
                    raise OMSError("exchange returned a non-Fill object")
                order = self._orders.get(fill.order_id)
                _, latest = self._try_exchange_order(fill.order_id)

                if order is None and latest is None:
                    # No durable order to attach the fill to.  Do not invent one.
                    self.store.append(
                        "recovery",
                        {
                            "kind": "orphan_fill",
                            "fill": _fill_event(fill),
                            "reason": "no local order for fill",
                        },
                    )
                    self._seen_fills.add(fill.fill_id)
                    continue

                if order is None:
                    assert latest is not None
                    merged, new_fills, _ = self._merge(None, latest, [fill])
                    self._persist_merged(None, merged, new_fills, force=True)
                else:
                    merged, new_fills, _ = self._merge(order, latest, [fill])
                    self._persist_merged(order, merged, new_fills)
                touched.add(merged.order_id)
                if fill.fill_id not in preexisting and fill.fill_id not in accepted_ids:
                    accepted.append(fill)
                    accepted_ids.add(fill.fill_id)

            # An exchange can expire/cancel an order without printing a fill.
            # Refresh the open orders for this symbol, not the whole book.
            for order in list(self._orders.values()):
                if (
                    order.order_id in touched
                    or not order.is_open
                    or order.symbol != tick.symbol
                ):
                    continue
                self._refresh_order(order)

            return accepted

    # ------------------------------------------------------------------
    # Queries / delegation
    # ------------------------------------------------------------------

    def order(self, order_id: str) -> OrderState | None:
        with self._lock:
            cached = self._get_local(order_id)
            if cached is not None:
                return copy.deepcopy(cached)
            loaded = self.store.load_order(order_id)
            if loaded is not None:
                self._remember(loaded)
                return copy.deepcopy(loaded)
            return None

    def orders(self, status: str | None = None, limit: int = 100) -> list[OrderState]:
        with self._lock:
            return [copy.deepcopy(o) for o in self.store.list_orders(status, limit)]

    def list_orders(
        self, status: str | None = None, limit: int = 100
    ) -> list[OrderState]:
        return self.orders(status=status, limit=limit)

    def open_orders(self) -> list[OrderState]:
        with self._lock:
            open_orders = [o for o in self._orders.values() if o.is_open]
            open_orders.sort(key=lambda o: (_utc(o.created_at), o.order_id))
            return [copy.deepcopy(o) for o in open_orders]

    def account(self) -> AccountSnapshot:
        return self.exchange.account()

    def positions(self) -> list[PositionSnapshot]:
        return self.exchange.positions()

    def snapshot(self) -> AccountSnapshot:
        """Persist and return an account snapshot (additive convenience)."""
        account = self.exchange.account()
        with self._lock:
            self.store.save_snapshot(account)
        return account

    def mark(self, prices: Mapping[str, float]) -> None:
        self.exchange.mark(prices)
        with self._lock:
            for symbol, price in prices.items():
                try:
                    value = float(price)
                except (TypeError, ValueError):
                    continue
                if value > 0:
                    self._last_prices[str(symbol)] = value

    def events(self, since: int = 0, limit: int = 100) -> list[dict[str, Any]]:
        return self.store.events(since=since, limit=limit)

    def close(self) -> None:
        self.store.close()

    # ------------------------------------------------------------------
    # Recovery / reconciliation
    # ------------------------------------------------------------------

    def recover(self) -> dict[str, Any]:
        """Reload durable state, replay the journal and reconcile the exchange."""
        with self._lock:
            if self._recovered:
                return copy.deepcopy(self._recovery_report)

            self._load_store()
            intents, orphan_fills = self._replay_journal()

            report: dict[str, Any] = {
                "orders": len(self._orders),
                "adopted": 0,
                "reconciled": 0,
                "finalized": 0,
                "unconfirmed": 0,
                "orphan_fills": 0,
                "errors": [],
            }
            self._reconcile_report(report)
            self._apply_orphans(orphan_fills, report)
            self._resolve_intents(intents, report)
            self._recovered = True
            self._recovery_report = report
            return copy.deepcopy(report)

    def reconcile(self) -> dict[str, Any]:
        """Reconcile every local order with the exchange (startup/session-end)."""
        with self._lock:
            report: dict[str, Any] = {
                "adopted": 0,
                "reconciled": 0,
                "finalized": 0,
                "errors": [],
            }
            self._reconcile_report(report)
            return copy.deepcopy(report)

    def _load_store(self) -> None:
        self._orders = {}
        self._client_ids = {}
        self._seen_fills = set()
        self._unconfirmed = {}
        try:
            orders = self.store.list_orders(limit=MAX_RECOVERY_ORDERS)
        except OMSRecoveryError:
            raise
        except Exception as exc:  # noqa: BLE001 - store boundary
            raise OMSRecoveryError(f"could not load orders from store: {exc}") from exc
        for order in orders:
            self._remember(order)
            for fill in order.fills:
                self._seen_fills.add(fill.fill_id)

    def _replay_journal(self) -> tuple[dict[str, dict[str, Any]], list[Fill]]:
        intents: dict[str, dict[str, Any]] = {}
        orphan_fills: list[Fill] = []
        touched: dict[str, OrderState] = {}
        since = 0
        while True:
            try:
                batch = self.store.events(since=since, limit=EVENT_PAGE)
            except Exception as exc:  # noqa: BLE001 - store boundary
                raise OMSRecoveryError(f"could not read event journal: {exc}") from exc
            if not batch:
                break
            for event in batch:
                seq = int(event.get("seq", 0))
                if seq <= since:
                    raise OMSRecoveryError(
                        "event journal returned a non-increasing seq"
                    )
                since = seq
                kind = event.get("kind")
                payload = event.get("payload") or {}
                if not isinstance(payload, Mapping):
                    raise OMSRecoveryError(f"event {seq} payload is not an object")
                if kind == "order_intent":
                    raw_request = payload.get("request")
                    if not isinstance(raw_request, Mapping):
                        raise OMSRecoveryError(
                            f"order intent event {seq} has no request"
                        )
                    request = request_from_payload(raw_request)
                    intents.setdefault(request.client_order_id, dict(payload))
                elif kind == "fill":
                    fill = fill_from_payload(payload)
                    if fill.fill_id in self._seen_fills:
                        continue
                    order = self._orders.get(fill.order_id)
                    if order is None:
                        orphan_fills.append(fill)
                        continue
                    merged, _, _ = self._merge(order, None, [fill])
                    self._orders[order.order_id] = merged
                    touched[order.order_id] = merged
            if len(batch) < EVENT_PAGE:
                break

        # The fill event is the durable record; persist the repaired order
        # without writing a second fill event.
        for order in touched.values():
            self._commit_order(order, [])
            for fill in order.fills:
                self._seen_fills.add(fill.fill_id)
        return intents, orphan_fills

    def _reconcile_report(self, report: dict[str, Any]) -> None:
        try:
            open_states = list(self.exchange.open_orders() or [])
        except Exception as exc:  # noqa: BLE001 - broker boundary
            report.setdefault("errors", []).append(
                f"exchange.open_orders failed: {exc}"
            )
            return

        open_by_id = {state.order_id: state for state in open_states}

        # Orders that reached the broker before a crash but never hit the store.
        for state in open_states:
            if not isinstance(state, OrderState):
                continue
            if state.order_id in self._orders:
                continue
            local_id = self._client_ids.get(state.client_order_id)
            if local_id is not None:
                report.setdefault("errors", []).append(
                    f"client_order_id {state.client_order_id!r} exists locally as "
                    f"{local_id!r} but exchange reports {state.order_id!r}"
                )
                continue
            merged, new_fills, _ = self._merge(None, state)
            self._persist_merged(
                None,
                merged,
                new_fills,
                force=True,
                extra_events=(
                    (
                        "recovery",
                        {
                            "kind": "order_adopted",
                            "order_id": state.order_id,
                            "reason": "exchange order missing from store",
                        },
                    ),
                ),
            )
            report["adopted"] = int(report.get("adopted", 0)) + 1

        # Refresh every known order.  For terminal orders the exchange may still
        # know fills that were not persisted before the crash.
        for order in list(self._orders.values()):
            latest = open_by_id.get(order.order_id)
            if latest is None:
                found, latest = self._try_exchange_order(order.order_id)
                if not found:
                    report.setdefault("errors", []).append(
                        f"exchange.order failed for {order.order_id!r}"
                    )
                    continue
            if latest is None:
                if order.is_open:
                    self._finalize_missing(order)
                    report["finalized"] = int(report.get("finalized", 0)) + 1
                continue
            merged, new_fills, changed = self._merge(order, latest)
            if changed or new_fills:
                self._persist_merged(order, merged, new_fills)
                report["reconciled"] = int(report.get("reconciled", 0)) + 1

    def _apply_orphans(
        self, orphan_fills: Iterable[Fill], report: dict[str, Any]
    ) -> None:
        remaining: list[Fill] = []
        for fill in orphan_fills:
            order = self._orders.get(fill.order_id)
            if order is None:
                remaining.append(fill)
                continue
            merged, _, _ = self._merge(order, None, [fill])
            self._commit_order(merged, [])
            self._seen_fills.add(fill.fill_id)
        if remaining:
            report["orphan_fills"] = len(remaining)
            for fill in remaining:
                self.store.append(
                    "recovery",
                    {
                        "kind": "orphan_fill",
                        "fill": _fill_event(fill),
                        "reason": "order still unknown after recovery",
                    },
                )
                self._seen_fills.add(fill.fill_id)

    def _resolve_intents(
        self, intents: Mapping[str, Mapping[str, Any]], report: dict[str, Any]
    ) -> None:
        for client_id, intent in intents.items():
            if client_id in self._client_ids:
                continue
            state = self._find_exchange_order_by_client(client_id)
            if state is not None:
                self._adopt_state(
                    state,
                    extra_events=(
                        (
                            "recovery",
                            {
                                "kind": "intent_reconciled",
                                "client_order_id": client_id,
                                "order_id": state.order_id,
                            },
                        ),
                    ),
                )
                report["adopted"] = int(report.get("adopted", 0)) + 1
                continue
            self._unconfirmed[client_id] = dict(intent)
            report["unconfirmed"] = int(report.get("unconfirmed", 0)) + 1
            self.store.append(
                "recovery",
                {
                    "kind": "order_unconfirmed",
                    "client_order_id": client_id,
                    "intent_id": intent.get("intent_id"),
                    "reason": "no acknowledgement after restart; not resubmitted",
                },
            )

    # ------------------------------------------------------------------
    # Exchange reconciliation helpers
    # ------------------------------------------------------------------

    def _reconcile_submit_exception(
        self, request: OrderRequest, first_error: Exception
    ) -> OrderState:
        client_id = request.client_order_id
        self.store.append(
            "recovery",
            {
                "kind": "submit_uncertain",
                "client_order_id": client_id,
                "error": f"{type(first_error).__name__}: {first_error}",
            },
        )

        found = self._find_exchange_order_by_client(client_id)
        if found is not None:
            return self._adopt_state(
                found,
                extra_events=(
                    (
                        "recovery",
                        {
                            "kind": "submit_reconciled",
                            "client_order_id": client_id,
                            "order_id": found.order_id,
                        },
                    ),
                ),
            )

        # One retry is safe only because the exchange is required to enforce
        # client_order_id uniqueness.  The pre-check above already adopts an
        # order that is visibly open.
        try:
            return self.exchange.submit(request)
        except Exception as second_error:  # noqa: BLE001 - broker boundary
            found = self._find_exchange_order_by_client(client_id)
            if found is not None:
                return self._adopt_state(found)
            if isinstance(second_error, ValueError):
                return self._record_reject(request, str(second_error))
            self._unconfirmed[client_id] = {
                "kind": "order_intent",
                "client_order_id": client_id,
                "request": jsonable(request),
            }
            self.store.append(
                "recovery",
                {
                    "kind": "order_unconfirmed",
                    "client_order_id": client_id,
                    "error": f"{type(second_error).__name__}: {second_error}",
                    "reason": "submit failed twice; not resubmitted",
                },
            )
            raise OrderUnconfirmedError(
                client_id, f"submit failed twice: {second_error}"
            ) from second_error

    def _find_exchange_order_by_client(self, client_id: str) -> OrderState | None:
        for name in (
            "order_by_client_id",
            "find_by_client_order_id",
            "find_order_by_client_id",
        ):
            lookup = getattr(self.exchange, name, None)
            if callable(lookup):
                try:
                    state = lookup(client_id)
                except Exception:  # noqa: BLE001 - optional broker extension
                    state = None
                if isinstance(state, OrderState):
                    return state
        try:
            states = self.exchange.open_orders() or []
        except Exception:  # noqa: BLE001 - broker boundary
            return None
        for state in states:
            if isinstance(state, OrderState) and state.client_order_id == client_id:
                return state
        return None

    def _try_exchange_order(self, order_id: str) -> tuple[bool, OrderState | None]:
        """Return ``(call_succeeded, state)``; failure is not "not found"."""
        try:
            return True, self.exchange.order(order_id)
        except Exception:  # noqa: BLE001 - broker boundary
            return False, None

    def _refresh_order(self, order: OrderState) -> OrderState:
        found, latest = self._try_exchange_order(order.order_id)
        if not found:
            return order
        if latest is None:
            return self._finalize_missing(order)
        merged, new_fills, changed = self._merge(order, latest)
        if changed or new_fills:
            self._persist_merged(order, merged, new_fills)
        return merged

    def _finalize_missing(self, order: OrderState) -> OrderState:
        """Resolve an order the exchange no longer knows about, preserving fills."""
        merged, _, _ = self._merge(order, None)
        if merged.filled_qty >= merged.qty > 0:
            merged.status = "FILLED"
            merged.reject_reason = None
        elif merged.filled_qty > 0:
            merged.status = "CANCELED"
            merged.reject_reason = "no longer open at exchange; filled qty preserved"
        else:
            merged.status = "REJECTED"
            merged.reject_reason = "order missing from exchange"
        merged.updated_at = utc_now()
        self._persist_merged(
            order,
            merged,
            (),
            force=True,
            extra_events=(
                (
                    "recovery",
                    {
                        "kind": "order_finalized_missing",
                        "order_id": order.order_id,
                        "status": merged.status,
                        "filled_qty": merged.filled_qty,
                    },
                ),
            ),
        )
        return merged

    # ------------------------------------------------------------------
    # Merge / persistence helpers
    # ------------------------------------------------------------------

    def _merge(
        self,
        existing: OrderState | None,
        latest: OrderState | None,
        extra_fills: Iterable[Fill] = (),
        *,
        now: datetime | None = None,
    ) -> tuple[OrderState, list[Fill], bool]:
        """Merge exchange state/fills into local state without losing quantity.

        ``filled_qty`` is monotonic (max of all observations and the sum of
        distinct fills).  Fills are keyed by ``fill_id`` so a replayed broker
        response cannot double-count.
        """
        if existing is None and latest is None:
            raise OMSError("cannot merge without a state")

        base = existing if existing is not None else latest
        assert base is not None

        fills: dict[str, Fill] = {}
        if existing is not None:
            for fill in existing.fills:
                fills[fill.fill_id] = fill
        existing_fill_ids = set(fills)
        if latest is not None:
            for fill in latest.fills:
                fills.setdefault(fill.fill_id, fill)
        for fill in extra_fills:
            fills.setdefault(fill.fill_id, fill)
        # New fills are those absent from the previous local state, whether they
        # arrived through the on_tick return value or through the exchange's
        # order snapshot.  Dedupe by fill_id so a duplicate broker response is
        # never journaled twice.
        added = [
            fill for fill_id, fill in fills.items() if fill_id not in existing_fill_ids
        ]
        itemized_qty = sum(float(f.qty) for f in fills.values())
        filled_qty = max(
            float(existing.filled_qty) if existing is not None else 0.0,
            float(latest.filled_qty) if latest is not None else 0.0,
            float(itemized_qty),
        )
        filled_qty = max(0.0, filled_qty)

        if itemized_qty > 0:
            avg_price = (
                sum(float(f.qty) * float(f.price) for f in fills.values())
                / itemized_qty
            )
        elif latest is not None and latest.filled_qty > 0 and latest.avg_price:
            avg_price = float(latest.avg_price)
        elif existing is not None and existing.filled_qty > 0 and existing.avg_price:
            avg_price = float(existing.avg_price)
        else:
            avg_price = 0.0

        latest_status = latest.status if latest is not None else None
        existing_status = existing.status if existing is not None else None
        qty = float(base.qty)
        if filled_qty >= qty > 0 and latest_status != "REJECTED":
            status = "FILLED"
        elif latest_status is None:
            status = existing_status or "NEW"
            if status in OPEN_STATUSES and filled_qty > 0:
                status = "PARTIAL"
        elif existing_status in TERMINAL_STATUSES and latest_status in OPEN_STATUSES:
            status = existing_status
        else:
            status = latest_status
            if status in OPEN_STATUSES and filled_qty > 0:
                status = "PARTIAL"

        reject_reason = None
        if status in ("REJECTED", "CANCELED"):
            if latest is not None and latest.reject_reason:
                reject_reason = latest.reject_reason
            elif existing is not None:
                reject_reason = existing.reject_reason

        updated_at = _max_ts(
            existing.updated_at if existing is not None else None,
            latest.updated_at if latest is not None else None,
            *[f.ts for f in fills.values()],
            now,
        )
        created_at = (
            existing.created_at
            if existing is not None
            else (latest.created_at if latest is not None else utc_now())
        )

        merged = OrderState(
            order_id=base.order_id,
            client_order_id=base.client_order_id,
            symbol=base.symbol,
            side=base.side,
            qty=base.qty,
            order_type=base.order_type,
            limit_price=base.limit_price,
            tif=base.tif,
            strategy=base.strategy,
            status=status,  # type: ignore[arg-type]
            filled_qty=filled_qty,
            avg_price=avg_price,
            created_at=created_at,
            updated_at=updated_at,
            reject_reason=reject_reason,
            fills=sorted(fills.values(), key=lambda f: (_utc(f.ts), f.fill_id)),
        )
        changed = existing is None or _state_key(merged) != _state_key(existing)
        return merged, added, changed

    def _persist_merged(
        self,
        previous: OrderState | None,
        merged: OrderState,
        new_fills: Iterable[Fill],
        *,
        force: bool = False,
        extra_events: Iterable[tuple[str, Mapping[str, Any]]] = (),
    ) -> OrderState:
        new_fills = list(new_fills)
        unseen = [f for f in new_fills if f.fill_id not in self._seen_fills]
        changed = (
            previous is None or force or _state_key(merged) != _state_key(previous)
        )
        extras = list(extra_events)
        if not (changed or unseen or extras):
            return merged

        events: list[tuple[str, Mapping[str, Any]]] = []
        for fill in unseen:
            events.append(("fill", _fill_event(fill)))
        if changed or extras:
            events.append(("order", order_event(merged)))
        events.extend(extras)

        self._commit_order(merged, events)
        for fill in unseen:
            self._seen_fills.add(fill.fill_id)
        return merged

    def _commit_order(
        self,
        order: OrderState,
        events: Iterable[tuple[str, Mapping[str, Any]]],
    ) -> None:
        pairs = list(events)
        writer = getattr(self.store, "save_order_with_events", None)
        if callable(writer):
            writer(order, pairs)
        else:  # protocol-only store; tiny crash window but still durable
            self.store.save_order(order)
            for kind, payload in pairs:
                self.store.append(kind, payload)
        self._remember(order)

    def _remember(self, order: OrderState) -> None:
        self._orders[order.order_id] = order
        self._client_ids[order.client_order_id] = order.order_id
        # A late reconciliation can resolve an intent that was unconfirmed at
        # startup; once an order exists the client id is safe to use again.
        self._unconfirmed.pop(order.client_order_id, None)

    def _existing_for_client(self, client_id: str) -> OrderState | None:
        order_id = self._client_ids.get(client_id)
        if order_id is None:
            loaded = (
                self.store.load_by_client_order_id(client_id)
                if hasattr(self.store, "load_by_client_order_id")
                else None
            )
            if loaded is None:
                return None
            self._remember(loaded)
            order_id = loaded.order_id
        return self._orders.get(order_id)

    def _get_local(self, order_id: str) -> OrderState | None:
        return self._orders.get(str(order_id))

    def _adopt_state(
        self,
        state: OrderState,
        *,
        extra_events: Iterable[tuple[str, Mapping[str, Any]]] = (),
    ) -> OrderState:
        existing = self._orders.get(state.order_id)
        if existing is not None:
            merged, new_fills, _ = self._merge(existing, state)
            self._persist_merged(
                existing,
                merged,
                new_fills,
                extra_events=extra_events,
            )
            return copy.deepcopy(merged)
        local_id = self._client_ids.get(state.client_order_id)
        if local_id is not None and local_id != state.order_id:
            # Defensive: keep one order per client id.  Merge the broker state
            # into the local row rather than creating a duplicate.
            existing = self._orders[local_id]
            merged, new_fills, _ = self._merge(existing, state)
            self._persist_merged(
                existing,
                merged,
                new_fills,
                extra_events=extra_events,
            )
            return copy.deepcopy(merged)
        merged, new_fills, _ = self._merge(None, state)
        self._persist_merged(
            None,
            merged,
            new_fills,
            force=True,
            extra_events=extra_events,
        )
        return copy.deepcopy(merged)

    def _record_reject(self, request: OrderRequest, reason: str) -> OrderState:
        order = OrderState(
            order_id=f"oms-{uuid.uuid4().hex}",
            client_order_id=request.client_order_id,
            symbol=request.symbol,
            side=request.side,
            qty=request.qty,
            order_type=request.order_type,
            limit_price=request.limit_price,
            tif=request.tif,
            strategy=request.strategy,
            status="REJECTED",
            filled_qty=0.0,
            avg_price=0.0,
            created_at=request.created_at,
            updated_at=utc_now(),
            reject_reason=reason,
            fills=[],
        )
        self._persist_merged(None, order, (), force=True)
        return copy.deepcopy(order)

    # ------------------------------------------------------------------
    # Local risk checks
    # ------------------------------------------------------------------

    def _local_reject_reason(self, request: OrderRequest) -> str | None:
        """Small fail-fast checks; the exchange is still authoritative."""
        if request.qty <= 0:
            return "qty must be positive"
        if request.order_type == "LIMIT" and (
            request.limit_price is None or request.limit_price <= 0
        ):
            return "limit price required"

        reference: float | None
        if request.order_type == "LIMIT":
            reference = float(request.limit_price)  # type: ignore[arg-type]
        else:
            reference = self._last_prices.get(request.symbol)
        if reference is not None and reference > 0:
            notional = float(request.qty) * reference
            if notional > self.limits.max_order_notional + 1e-9:
                return (
                    "max order notional exceeded "
                    f"({notional:.2f} > {self.limits.max_order_notional:.2f})"
                )

        if request.side == "SELL" and not self.limits.allow_short:
            held = self._held_qty(request.symbol)
            if held is not None and float(request.qty) > held + 1e-9:
                return "short selling is disabled"
        return None

    def _held_qty(self, symbol: str) -> float | None:
        try:
            positions = self.exchange.positions() or []
        except Exception:  # noqa: BLE001 - exchange will make the final decision
            return None
        return sum(
            float(position.qty) for position in positions if position.symbol == symbol
        )


__all__ = [
    "DuplicateOrderError",
    "IdempotencyConflict",
    "OMSError",
    "OMSRecoveryError",
    "OrderConflict",
    "OrderNotFound",
    "OrderSubmitError",
    "OrderUnconfirmedError",
    "SimulatedOMS",
    "fill_from_payload",
    "request_from_payload",
]

