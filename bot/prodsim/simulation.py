"""Simulation facade: wires the clock, replay feed, strategy, exchange and store.

``Simulation`` is the durable service used by the HTTP layer and the CLI. It owns
no broker logic and no HTTP internals: exchange/store/server are injected and
only their contract methods are used. Concrete imports are lazy so this branch
is testable before the sibling v12 components merge.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

from .clock import (
    ClockKilled,
    ClockPaused,
    ClockStopped,
    OutOfOrderTick,
    SimulationClock,
)
from .contract import (
    AccountSnapshot,
    ExchangeProtocol,
    OrderRequest,
    OrderState,
    PositionSnapshot,
    RunConfig,
    SimulationStatus,
    StoreProtocol,
    Tick,
    iso,
    jsonable,
    order_event,
    utc_now,
)
from .strategy import StrategyProtocol

__all__ = ["Simulation", "SimulationError"]


class SimulationError(RuntimeError):
    """The facade cannot perform the requested operation in its current state."""


class Simulation:
    """Long-lived in-process simulation implementing ``SimulationProtocol``."""

    def __init__(
        self,
        config: RunConfig,
        *,
        exchange: ExchangeProtocol | None = None,
        store: StoreProtocol | None = None,
        oms: Any | None = None,
        clock: SimulationClock | None = None,
        strategy: StrategyProtocol | None = None,
        feed: Iterable[Tick] | None = None,
        snapshot_interval: int = 100,
        stale_after_seconds: float | None = None,
    ) -> None:
        if oms is not None:
            exchange = exchange or getattr(oms, "exchange", None)
            store = store or getattr(oms, "store", None)
        if exchange is None or store is None:
            raise ValueError("exchange and store are required (directly or via oms)")
        self.config = config
        self.exchange = exchange
        self.store = store
        self.oms = oms
        self.clock = clock or SimulationClock(
            mode=config.mode,
            speed=config.speed,
            realtime=False,
        )
        self.strategy = strategy
        self._feed = feed
        self._feed_source = feed
        self.snapshot_interval = max(1, int(snapshot_interval))
        # Live-paper polls completed daily sessions; three days covers a
        # weekend and a holiday. The feed itself raises after max_stale_days.
        default_stale = 3 * 86_400.0 if config.mode == "live-paper" else 86_400.0
        self.stale_after_seconds = float(
            default_stale if stale_after_seconds is None else stale_after_seconds
        )
        self._lock = threading.RLock()
        self._state = "STARTING"
        self._started_at = utc_now()
        self._stopped_at: datetime | None = None
        self._kill_reason: str | None = None
        self._kill_actor: str | None = None
        self._processed_ticks = 0
        self._orders = 0
        self._fills = 0
        self._errors = 0
        self._rejected_ticks = 0
        self._last_tick: Tick | None = None
        self._last_session: date | None = None
        self._last_prices: dict[str, float] = {}
        self._expected_session: date | None = None
        self._last_error: str | None = None
        self._last_health: dict[str, Any] = {}
        self._by_client: dict[str, str] = {}
        self._seen_oms_orders: set[str] = set()
        self._oms_recovery: dict[str, Any] | None = None
        self._restored = False
        self._thread: threading.Thread | None = None
        self._max_ticks: int | None = None
        self._api: Any = None
        self._session_ticks = 0
        self._sessions_seen = 0
        self._last_status_write = 0

    # -- state ---------------------------------------------------------
    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    def _set_state(self, state: str) -> None:
        self._state = state
        self._persist_status(force=True)

    def _require_active(self) -> None:
        if self._state in ("KILLED", "STOPPED", "ERROR"):
            raise SimulationError(f"simulation is {self._state.lower()}")
        if self._state == "PAUSED":
            raise SimulationError("simulation is paused")

    def start(self) -> None:
        with self._lock:
            if self._state in ("RUNNING", "PAUSED"):
                return
            if self._state in ("KILLED", "ERROR"):
                raise SimulationError(f"cannot start simulation in state {self._state}")
            self._restore()
            self.clock.start()
            self._started_at = utc_now()
            self._state = "RUNNING"
            self._append(
                "run.started",
                {
                    "mode": self.config.mode,
                    "speed": self.config.speed,
                    "capital": self.config.capital,
                },
            )
            self._persist_status(force=True)
            if self._feed is not None and self._thread is None:
                self._thread = threading.Thread(
                    target=self._loop,
                    name="prodsim-feed",
                    daemon=True,
                )
                self._thread.start()
        if self._thread is not None and self._thread is not threading.current_thread():
            # ``start`` is non-blocking; ``run_feed`` joins explicitly.
            pass

    def stop(self) -> None:
        with self._lock:
            if self._state in ("STOPPED", "KILLED"):
                self._stop_api()
                return
            if self._state == "ERROR":
                self.clock.stop()
                self._snapshot()
                self._persist_status(force=True)
                self._stop_api()
                return
            thread = self._thread
        self.clock.stop()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=10.0)
        with self._lock:
            if self._state == "KILLED":
                return
            self._state = "STOPPED"
            self._stopped_at = utc_now()
            self._snapshot()
            self._append("run.stopped", {"ticks": self._processed_ticks})
            self._persist_status(force=True)
            self._stop_api()

    def pause(self) -> None:
        with self._lock:
            if self._state == "KILLED":
                raise SimulationError("simulation is killed")
            if self._state == "STOPPED":
                raise SimulationError("simulation is stopped")
            self.clock.pause()
            self._state = "PAUSED"
            self._append("clock.paused", {})
            self._persist_status(force=True)

    def resume(self) -> None:
        with self._lock:
            if self._state == "KILLED":
                raise SimulationError("simulation is killed")
            if self._state == "STOPPED":
                raise SimulationError("simulation is stopped")
            self.clock.resume()
            self._state = "RUNNING"
            self._append("clock.resumed", {})
            self._persist_status(force=True)

    def kill(self, reason: str, actor: str) -> dict[str, Any]:
        with self._lock:
            if self._state == "KILLED":
                return {
                    "ok": True,
                    "state": "KILLED",
                    "already": True,
                    "reason": self._kill_reason,
                    "actor": self._kill_actor,
                    "canceled": 0,
                }
            self._kill_reason = str(reason or "kill switch")
            self._kill_actor = str(actor or "unknown")
            self.clock.kill(self._kill_reason)
            canceled = 0
            if self.oms is not None:
                try:
                    report = self.oms.cancel_all(self._kill_reason)
                    canceled = len(report.get("canceled", []))
                    for item in report.get("errors", []):
                        self._errors += 1
                        self._append(
                            "error",
                            {"scope": "kill.cancel", **item},
                        )
                except Exception as exc:  # noqa: BLE001
                    self._errors += 1
                    self._last_error = f"{type(exc).__name__}: {exc}"
                    self._append(
                        "error",
                        {"scope": "kill.cancel", "error": self._last_error},
                    )
            else:
                for order in list(self.exchange.open_orders()):
                    try:
                        updated = self.exchange.cancel(order.order_id)
                        self.store.save_order(updated)
                        self._append(
                            "order.canceled",
                            {
                                **order_event(updated, "order.canceled"),
                                "actor": self._kill_actor,
                            },
                        )
                        canceled += 1
                    except Exception as exc:  # noqa: BLE001
                        self._errors += 1
                        self._append(
                            "error",
                            {
                                "scope": "kill.cancel",
                                "order_id": order.order_id,
                                "error": f"{type(exc).__name__}: {exc}",
                            },
                        )
            self._snapshot()
            self._state = "KILLED"
            self._stopped_at = utc_now()
            self._append(
                "run.killed",
                {
                    "reason": self._kill_reason,
                    "actor": self._kill_actor,
                    "canceled": canceled,
                },
            )
            self._persist_status(force=True)
            self._stop_api()
            return {
                "ok": True,
                "state": "KILLED",
                "already": False,
                "reason": self._kill_reason,
                "actor": self._kill_actor,
                "canceled": canceled,
                "at": iso(utc_now()),
            }

    def health(self) -> dict[str, Any]:
        with self._lock:
            # Replay is judged on simulated time. Live-paper must notice a feed
            # that has gone quiet while the process still looks alive.
            now = utc_now() if self.config.mode == "live-paper" else self.clock.now()
            last = self._last_tick.ts if self._last_tick else None
            issues: list[str] = []
            if self._state in ("KILLED", "STOPPED", "ERROR"):
                issues.append(f"state={self._state}")
            if last is None:
                issues.append("no ticks processed")
            else:
                age = max(0.0, (now - last).total_seconds())
                if age > self.stale_after_seconds:
                    issues.append(
                        f"stale data: {age:.0f}s since last tick "
                        f"(limit {self.stale_after_seconds:.0f}s)"
                    )
            if self._expected_session is not None and (
                last is None or last.date() < self._expected_session
            ):
                issues.append(
                    f"data outage: no tick for session "
                    f"{self._expected_session.isoformat()}"
                )
            if self._last_error:
                issues.append(f"last error: {self._last_error}")
            if self._oms_recovery and self._oms_recovery.get("errors"):
                issues.append(
                    "OMS recovery errors: "
                    + "; ".join(str(x) for x in self._oms_recovery["errors"])
                )
            ready = not issues and self._state in ("RUNNING", "PAUSED")
            out = {
                "ready": bool(ready),
                "state": self._state,
                "mode": self.config.mode,
                "clock": iso(now),
                "last_tick": iso(last) if last else None,
                "session": (
                    self._last_session.isoformat() if self._last_session else None
                ),
                "expected_session": (
                    self._expected_session.isoformat()
                    if self._expected_session
                    else None
                ),
                "processed_ticks": self._processed_ticks,
                "errors": self._errors,
                "issues": issues,
                "components": {
                    "exchange": type(self.exchange).__name__,
                    "store": type(self.store).__name__,
                    "strategy": None
                    if self.strategy is None
                    else type(self.strategy).__name__,
                    "oms": None if self.oms is None else type(self.oms).__name__,
                    "clock": type(self.clock).__name__,
                },
            }
            self._last_health = out
            return out

    def note_session(self, session: date | str) -> None:
        """Supervisor signal: this session must produce at least one tick."""
        d = date.fromisoformat(str(session)[:10])
        with self._lock:
            self._expected_session = d

    # -- reads ---------------------------------------------------------
    def status(self) -> SimulationStatus:
        with self._lock:
            session = self._last_session or self._expected_session
            return SimulationStatus(
                state=self._state,  # type: ignore[arg-type]
                mode=self.config.mode,
                clock=self.clock.now(),
                session=session.isoformat() if session else None,
                speed=self.clock.speed,
                started_at=self._started_at,
                last_tick=self._last_tick.ts if self._last_tick else None,
                processed_ticks=self._processed_ticks,
                orders=self._orders,
                fills=self._fills,
                errors=self._errors,
                kill_reason=self._kill_reason,
            )

    def account(self) -> AccountSnapshot:
        if self.oms is not None:
            return self.oms.account()
        return self.exchange.account()

    def positions(self) -> list[PositionSnapshot]:
        if self.oms is not None:
            return self.oms.positions()
        return self.exchange.positions()

    def orders(self, status: str | None = None, limit: int = 100) -> list[OrderState]:
        if self.oms is not None:
            return self.oms.orders(status=status, limit=limit)
        return self.store.list_orders(status=status, limit=limit)

    def order(self, order_id: str) -> OrderState | None:
        if self.oms is not None:
            return self.oms.order(order_id)
        order = self.store.load_order(order_id)
        if order is not None:
            return order
        return self.exchange.order(order_id)

    def events(self, since: int = 0, limit: int = 100) -> list[dict[str, Any]]:
        if self.oms is not None:
            return self.oms.events(since=since, limit=limit)
        return self.store.events(since=since, limit=limit)

    def _open_orders(self) -> list[OrderState]:
        if self.oms is not None:
            return self.oms.open_orders()
        return self.exchange.open_orders()

    def strategy_status(self) -> dict[str, Any] | None:
        if self.strategy is None:
            return None
        try:
            return self.strategy.status()
        except Exception:  # noqa: BLE001
            return None

    # -- writes --------------------------------------------------------
    def submit(self, request: OrderRequest) -> OrderState:
        with self._lock:
            self._restore()
            self._require_active()
            if self.oms is not None:
                try:
                    order = self.oms.submit(request)
                except Exception as exc:  # noqa: BLE001
                    if type(exc).__name__ in (
                        "OrderConflict",
                        "DuplicateOrderError",
                        "IdempotencyConflict",
                    ):
                        raise
                    self._errors += 1
                    self._last_error = f"{type(exc).__name__}: {exc}"
                    raise
                if order.order_id not in self._seen_oms_orders:
                    self._seen_oms_orders.add(order.order_id)
                    self._orders += 1
                self._persist_status()
                return order
            existing_id = self._by_client.get(request.client_order_id)
            if existing_id:
                existing = self.store.load_order(existing_id)
                if existing is not None:
                    self._append(
                        "order.duplicate",
                        {
                            "client_order_id": request.client_order_id,
                            "order_id": existing.order_id,
                        },
                    )
                    return existing
            # Journal intent first: if the process dies around broker ack, the
            # durable intent still identifies the client order id. Resubmitting
            # it after restart is safe because the broker deduplicates.
            self._append(
                "order.intent",
                {
                    "client_order_id": request.client_order_id,
                    "symbol": request.symbol,
                    "side": request.side,
                    "qty": request.qty,
                    "order_type": request.order_type,
                    "limit_price": request.limit_price,
                    "tif": request.tif,
                    "strategy": request.strategy,
                },
            )
            try:
                order = self.exchange.submit(request)
            except Exception as exc:  # noqa: BLE001
                self._errors += 1
                self._last_error = f"{type(exc).__name__}: {exc}"
                self._append(
                    "error",
                    {
                        "scope": "submit",
                        "client_order_id": request.client_order_id,
                        "error": self._last_error,
                    },
                )
                raise SimulationError(self._last_error) from exc
            self._orders += 1
            self._by_client[order.client_order_id] = order.order_id
            self.store.save_order(order)
            kind = "order.rejected" if order.status == "REJECTED" else "order.submitted"
            self._append(kind, order_event(order, kind))
            self._persist_status()
            return order

    def cancel(self, order_id: str) -> OrderState:
        with self._lock:
            self._restore()
            if self._state in ("KILLED", "STOPPED", "ERROR"):
                raise SimulationError(f"simulation is {self._state.lower()}")
            try:
                order = (
                    self.oms.cancel(order_id)
                    if self.oms is not None
                    else self.exchange.cancel(order_id)
                )
            except KeyError as exc:
                raise SimulationError(f"unknown order {order_id}") from exc
            except Exception as exc:  # noqa: BLE001
                self._errors += 1
                self._last_error = f"{type(exc).__name__}: {exc}"
                raise SimulationError(self._last_error) from exc
            if self.oms is not None:
                self._persist_status()
                return order
            self.store.save_order(order)
            self._by_client[order.client_order_id] = order.order_id
            self._append("order.canceled", order_event(order, "order.canceled"))
            self._persist_status()
            return order

    # -- feed ----------------------------------------------------------
    def on_tick(self, tick: Tick) -> list[OrderState]:
        """Process exactly one tick. The clock rejects duplicates first."""
        with self._lock:
            if self._state in ("KILLED", "STOPPED", "ERROR"):
                raise SimulationError(f"simulation is {self._state.lower()}")
            if self._state == "STARTING":
                self.start()
            session = tick.ts.date()
            first_of_session = session != self._last_session
            previous = self._last_session
            # Validate monotonicity *before* running the close hook: a
            # duplicate tick must not recompute or re-emit a month-end target.
            self.clock.advance(tick.ts)
            if first_of_session and previous is not None:
                self._close_session(previous)
            result: list[OrderState] = []
            try:
                if self.oms is not None:
                    fills = self.oms.on_tick(tick)
                    self._fills += len(fills)
                    self.oms.mark({tick.symbol: tick.last})
                else:
                    fills = self.exchange.on_tick(tick)
                    for fill in fills:
                        self._fills += 1
                        order = self.exchange.order(fill.order_id)
                        if order is not None:
                            self.store.save_order(order)
                        self._append(
                            "fill",
                            {
                                "fill_id": fill.fill_id,
                                "order_id": fill.order_id,
                                "symbol": fill.symbol,
                                "side": fill.side,
                                "qty": fill.qty,
                                "price": fill.price,
                                "commission": fill.commission,
                                "slippage": fill.slippage,
                                "ts": iso(fill.ts),
                            },
                        )
                    self.exchange.mark({tick.symbol: tick.last})
            except Exception as exc:  # noqa: BLE001
                self._errors += 1
                self._last_error = f"{type(exc).__name__}: {exc}"
                self._append(
                    "error",
                    {"scope": "exchange.on_tick", "error": self._last_error},
                )
            self._last_tick = tick
            self._last_session = session
            self._last_prices[tick.symbol] = float(tick.last)
            self._processed_ticks += 1
            self._session_ticks += 1
            if first_of_session:
                self._sessions_seen += 1
                self._session_ticks = 1
            # The strategy deduplicates per symbol/session; calling it on every
            # tick lets each symbol execute on its own first next-session tick.
            result = self._strategy_orders(tick, session)
            if first_of_session or self._processed_ticks % self.snapshot_interval == 0:
                self._snapshot()
            self._persist_status()
            return result

    def run_feed(
        self,
        feed: Iterable[Tick] | None = None,
        *,
        max_ticks: int | None = None,
        join_timeout: float | None = None,
    ) -> None:
        """Start the service and drive ``feed``; blocks until completion."""
        with self._lock:
            if feed is not None:
                self._feed = feed
                self._feed_source = feed
            self._max_ticks = max_ticks
            if self._feed is None:
                raise SimulationError("run_feed needs a feed")
            self.start()
            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._loop,
                    name="prodsim-feed",
                    daemon=True,
                )
                self._thread.start()
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=join_timeout)

    def _loop(self) -> None:
        failed = False
        try:
            for index, tick in enumerate(self._feed or ()):
                if self._max_ticks is not None and index >= self._max_ticks:
                    break
                try:
                    self.clock.wait_until(tick.ts)
                    self.on_tick(tick)
                except (ClockKilled, ClockStopped):
                    break
                except OutOfOrderTick as exc:
                    self._rejected_ticks += 1
                    self._append("tick.rejected", {"error": str(exc)})
                    continue
                except ClockPaused:
                    # wait_until handles pause; a direct race is retried.
                    continue
                except SimulationError:
                    failed = True
                    break
                except Exception as exc:  # noqa: BLE001
                    failed = True
                    self._errors += 1
                    self._last_error = f"{type(exc).__name__}: {exc}"
                    self._append("error", {"scope": "feed", "error": self._last_error})
                    break
        except (ClockKilled, ClockStopped):
            pass
        except SimulationError:
            failed = True
        except Exception as exc:  # noqa: BLE001 - feed/generator failed
            failed = True
            self._errors += 1
            self._last_error = f"{type(exc).__name__}: {exc}"
            self._append("error", {"scope": "feed.source", "error": self._last_error})
        finally:
            with self._lock:
                if self._state not in ("KILLED", "STOPPED", "ERROR"):
                    self._state = "ERROR" if failed else "STOPPED"
                    self._stopped_at = utc_now()
                    self._snapshot()
                    self._append(
                        "run.error" if failed else "run.completed",
                        {
                            "ticks": self._processed_ticks,
                            "orders": self._orders,
                            "fills": self._fills,
                            "errors": self._errors,
                            "last_error": self._last_error,
                        },
                    )
                    self._persist_status(force=True)

    def _close_session(self, session: date) -> None:
        if self.strategy is None:
            return
        account = self.exchange.account()
        try:
            self.strategy.on_session_close(
                session,
                dict(self._last_prices),
                account,
            )
            weights = getattr(self.strategy, "pending_weights", lambda: {})()
            self._append(
                "strategy.targets",
                {
                    "session": session.isoformat(),
                    "weights": {k: round(float(v), 8) for k, v in weights.items()},
                    "equity": round(float(account.equity), 4),
                },
            )
        except Exception as exc:  # noqa: BLE001 - no target on bad data
            self._errors += 1
            self._last_error = f"{type(exc).__name__}: {exc}"
            self._append(
                "error",
                {
                    "scope": "strategy.on_session_close",
                    "session": session.isoformat(),
                    "error": self._last_error,
                },
            )

    def _strategy_orders(self, tick: Tick, session: date) -> list[OrderState]:
        if self.strategy is None:
            return []
        result: list[OrderState] = []
        try:
            requests = self.strategy.orders_for_tick(
                tick,
                session,
                self.exchange.account(),
                self.exchange.positions(),
            )
        except Exception as exc:  # noqa: BLE001
            self._errors += 1
            self._last_error = f"{type(exc).__name__}: {exc}"
            self._append(
                "error",
                {"scope": "strategy.orders_for_tick", "error": self._last_error},
            )
            return result
        for request in requests:
            try:
                result.append(self.submit(request))
            except Exception:  # noqa: BLE001 - one bad order must not stop the feed
                continue
        return result

    # -- persistence / restore ----------------------------------------
    def _snapshot(self) -> None:
        try:
            if self.oms is not None:
                self.oms.snapshot()
            else:
                self.store.save_snapshot(self.exchange.account())
        except Exception as exc:  # noqa: BLE001
            self._errors += 1
            self._last_error = f"{type(exc).__name__}: {exc}"

    def _append(self, kind: str, payload: Mapping[str, Any]) -> int | None:
        try:
            return self.store.append(kind, payload)
        except Exception as exc:  # noqa: BLE001
            self._errors += 1
            self._last_error = f"{type(exc).__name__}: {exc}"
            return None

    def _restore(self) -> None:
        if self._restored:
            return
        self._restored = True
        try:
            if self.oms is not None:
                self._oms_recovery = self.oms.recover()
                return
            # A pragmatic cap: long-lived stores should page, but 100k keeps a
            # restart idempotent for the load envelope (1k orders/day).
            for order in self.store.list_orders(limit=100_000):
                self._by_client.setdefault(order.client_order_id, order.order_id)
            snapshot = self.store.last_snapshot()
            if snapshot is not None:
                self._last_health["restored_snapshot"] = iso(snapshot.ts)
        except Exception as exc:  # noqa: BLE001
            self._errors += 1
            self._last_error = f"restore failed: {type(exc).__name__}: {exc}"
            self._append("error", {"scope": "restore", "error": self._last_error})

    def _status_path(self) -> Path | None:
        if not self.config.state_dir:
            return None
        return Path(self.config.state_dir) / "status.json"

    def _persist_status(self, *, force: bool = False) -> None:
        path = self._status_path()
        if path is None:
            return
        if not force and self._processed_ticks - self._last_status_write < 20:
            self._last_status_write = self._processed_ticks
            return
        payload = {
            "status": jsonable(self.status()),
            "health": jsonable(self.health()),
            "strategy": jsonable(self.strategy_status()),
            "account": jsonable(self.account()),
            "last_error": self._last_error,
            "kill_reason": self._kill_reason,
            "kill_actor": self._kill_actor,
            "updated_at": iso(utc_now()),
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
            tmp.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")))
            os.replace(tmp, path)
            self._last_status_write = self._processed_ticks
        except Exception as exc:  # noqa: BLE001
            self._errors += 1
            self._last_error = f"status write failed: {type(exc).__name__}: {exc}"

    # -- metrics -------------------------------------------------------
    def metrics(self) -> str:
        status = self.status()
        account = self.account()
        health = self.health()
        lines = [
            "# HELP prodsim_ready Simulation ready (data+store+clock, not killed).",
            "# TYPE prodsim_ready gauge",
            f"prodsim_ready {1 if health['ready'] else 0}",
            "# HELP prodsim_state Current run state as labels.",
            "# TYPE prodsim_state gauge",
        ]
        for state in ("STARTING", "RUNNING", "PAUSED", "KILLED", "STOPPED", "ERROR"):
            lines.append(
                f'prodsim_state{{state="{state}"}} {1 if status.state == state else 0}'
            )
        lines.extend(
            [
                "# HELP prodsim_ticks_total Processed ticks.",
                "# TYPE prodsim_ticks_total counter",
                f"prodsim_ticks_total {status.processed_ticks}",
                "# HELP prodsim_orders_total Submitted orders.",
                "# TYPE prodsim_orders_total counter",
                f"prodsim_orders_total {status.orders}",
                "# HELP prodsim_fills_total Fills.",
                "# TYPE prodsim_fills_total counter",
                f"prodsim_fills_total {status.fills}",
                "# HELP prodsim_errors_total Simulation errors.",
                "# TYPE prodsim_errors_total counter",
                f"prodsim_errors_total {status.errors}",
                "# HELP prodsim_equity Account equity.",
                "# TYPE prodsim_equity gauge",
                f"prodsim_equity {account.equity:.6f}",
                "# HELP prodsim_cash Account cash.",
                "# TYPE prodsim_cash gauge",
                f"prodsim_cash {account.cash:.6f}",
                "# HELP prodsim_buying_power Buying power.",
                "# TYPE prodsim_buying_power gauge",
                f"prodsim_buying_power {account.buying_power:.6f}",
                "# HELP prodsim_gross_exposure Gross exposure.",
                "# TYPE prodsim_gross_exposure gauge",
                f"prodsim_gross_exposure {account.gross_exposure:.6f}",
                "# HELP prodsim_realized_pnl Realised P&L.",
                "# TYPE prodsim_realized_pnl gauge",
                f"prodsim_realized_pnl {account.realized_pnl:.6f}",
                "# HELP prodsim_fees_paid Fees paid.",
                "# TYPE prodsim_fees_paid gauge",
                f"prodsim_fees_paid {account.fees_paid:.6f}",
                "# HELP prodsim_open_orders Open orders.",
                "# TYPE prodsim_open_orders gauge",
                f"prodsim_open_orders {len(self._open_orders())}",
            ]
        )
        return "\n".join(lines) + "\n"

    # -- HTTP API hand-off --------------------------------------------
    def attach_api(self, api: Any) -> None:
        """Attach a server object supplied by ``v12/sim-api``."""
        self._api = api

    def start_api(
        self,
        host: str = "127.0.0.1",
        port: int = 8080,
        token: str = "",
        *,
        dev: bool = False,
    ) -> Any:
        """Start the HTTP API if the sibling module is merged; otherwise warn.

        The concrete factory name is owned by ``v12/sim-api``. We probe the
        documented candidates and pass only contract-level arguments.
        """
        if self._api is not None:
            return self._api
        try:
            from bot.prodsim.api import ApiConfig
            from bot.prodsim.server import create_server
        except Exception as exc:  # noqa: BLE001
            self._append(
                "api.unavailable",
                {"reason": f"{type(exc).__name__}: {exc}", "host": host, "port": port},
            )
            return None
        try:
            cfg = ApiConfig(
                host=host,
                port=int(port),
                api_token=token,
                admin_token=os.environ.get("STONKS_PRODSIM_ADMIN_TOKEN", ""),
                dev=dev,
            )
            server = create_server(self, cfg)
            server.start_background()
            self._api = server
            self._append(
                "api.started",
                {"host": host, "port": int(port), "base_url": server.base_url},
            )
            return server
        except Exception as exc:  # noqa: BLE001
            self._errors += 1
            self._last_error = f"api start failed: {type(exc).__name__}: {exc}"
            self._append("error", {"scope": "api", "error": self._last_error})
            return None

    # -- misc ----------------------------------------------------------
    def status_dict(self) -> dict[str, Any]:
        return {
            "status": jsonable(self.status()),
            "health": jsonable(self.health()),
            "strategy": jsonable(self.strategy_status()),
            "account": jsonable(self.account()),
            "last_error": self._last_error,
        }

    def _stop_api(self) -> None:
        api = self._api
        self._api = None
        if api is None:
            return
        try:
            shutdown = getattr(api, "shutdown", None)
            if callable(shutdown):
                shutdown()
        except Exception as exc:  # noqa: BLE001
            self._append("error", {"scope": "api.shutdown", "error": str(exc)})

    def close(self) -> None:
        try:
            self.stop()
        finally:
            try:
                if self.oms is not None:
                    self.oms.close()
                else:
                    self.store.close()
            except Exception:  # noqa: BLE001
                pass
