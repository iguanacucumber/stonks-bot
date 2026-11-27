"""Simulation clock: replay speed, pause/resume and the kill switch.

The clock owns *simulated* time. It never reads the wall clock for decisions and
never sleeps unless ``realtime`` is enabled, so tests and load runs are fully
deterministic.

Invariants:

* simulated time is strictly monotonic;
* a duplicate or out-of-order tick raises :class:`OutOfOrderTick` and is never
  applied to the exchange;
* pause blocks new ticks at the feed boundary; resume does not rewind time;
* kill is terminal for the process lifetime and blocks new ticks.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Callable, Iterable

from .contract import RunMode, RunState, utc_now

__all__ = [
    "ClockError",
    "ClockKilled",
    "ClockPaused",
    "ClockStopped",
    "OutOfOrderTick",
    "SimulationClock",
]


class ClockError(RuntimeError):
    """Base class for clock rejections."""


class OutOfOrderTick(ClockError):
    """A tick timestamp is not strictly after the last accepted tick."""

    def __init__(self, previous: datetime | None, incoming: datetime) -> None:
        self.previous = previous
        self.incoming = incoming
        super().__init__(
            "tick is not monotonic: "
            f"previous={previous.isoformat() if previous else None} "
            f"incoming={incoming.isoformat()}"
        )


class ClockPaused(ClockError):
    """A tick arrived while the clock is paused."""


class ClockKilled(ClockError):
    """The kill switch is engaged."""


class ClockStopped(ClockError):
    """The clock was stopped (normal shutdown)."""


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError("timestamp must be a datetime")
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@dataclass
class ClockStats:
    accepted_ticks: int = 0
    rejected_ticks: int = 0
    paused_seconds: float = 0.0


class SimulationClock:
    """A monotonic simulated clock with pause, resume, speed and kill.

    ``realtime=False`` is the default: :meth:`wait_until` returns immediately.
    Replay callers that want wall-clock pacing pass ``realtime=True``; the delay
    between two simulated timestamps is divided by ``speed``.
    """

    def __init__(
        self,
        mode: RunMode = "replay",
        speed: float = 1.0,
        *,
        realtime: bool = False,
        max_sleep_seconds: float = 60.0,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        if mode not in ("replay", "live-paper"):
            raise ValueError(f"invalid mode {mode!r}")
        if speed <= 0:
            raise ValueError("speed must be positive")
        self.mode = mode
        self._speed = float(speed)
        self.realtime = bool(realtime)
        if max_sleep_seconds <= 0:
            raise ValueError("max_sleep_seconds must be positive")
        self.max_sleep_seconds = float(max_sleep_seconds)
        self._wall_clock = clock
        self._condition = threading.Condition()
        self._now: datetime | None = None
        self._last_tick: datetime | None = None
        self._session: date | None = None
        self._paused = False
        self._killed = False
        self._killed_reason: str | None = None
        self._stopped = False
        self._speed_changes = 0
        self.stats = ClockStats()

    # -- state ---------------------------------------------------------
    @property
    def speed(self) -> float:
        return self._speed

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def killed(self) -> bool:
        return self._killed

    @property
    def stopped(self) -> bool:
        return self._stopped

    @property
    def last_tick(self) -> datetime | None:
        return self._last_tick

    @property
    def session(self) -> date | None:
        return self._session

    @property
    def kill_reason(self) -> str | None:
        return self._killed_reason

    def state(self) -> RunState:
        if self._killed:
            return "KILLED"
        if self._stopped:
            return "STOPPED"
        if self._paused:
            return "PAUSED"
        if self._now is None:
            return "STARTING"
        return "RUNNING"

    def now(self) -> datetime:
        with self._condition:
            return self._now or _utc(self._wall_clock())

    def start(self, now: datetime | None = None) -> datetime:
        with self._condition:
            if self._killed:
                raise ClockKilled(self._killed_reason or "killed")
            if self._stopped:
                raise ClockStopped("clock is stopped")
            if self._now is None:
                self._now = _utc(now) if now is not None else _utc(self._wall_clock())
            self._condition.notify_all()
            return self._now

    def set_speed(self, speed: float) -> float:
        if speed <= 0:
            raise ValueError("speed must be positive")
        with self._condition:
            self._speed = float(speed)
            self._speed_changes += 1
            return self._speed

    def advance(self, ts: datetime) -> datetime:
        """Accept one tick timestamp, strictly increasing.

        Raises :class:`ClockPaused`, :class:`ClockKilled` or
        :class:`OutOfOrderTick`; none of these mutate simulated time.
        """
        value = _utc(ts)
        with self._condition:
            if self._killed:
                self.stats.rejected_ticks += 1
                raise ClockKilled(self._killed_reason or "killed")
            if self._stopped:
                self.stats.rejected_ticks += 1
                raise ClockStopped("clock is stopped")
            if self._paused:
                self.stats.rejected_ticks += 1
                raise ClockPaused("clock is paused")
            if self._last_tick is not None and value <= self._last_tick:
                self.stats.rejected_ticks += 1
                raise OutOfOrderTick(self._last_tick, value)
            self._last_tick = value
            self._now = value
            self._session = value.date()
            self.stats.accepted_ticks += 1
            return value

    # -- pacing --------------------------------------------------------
    def wait_until(self, ts: datetime) -> None:
        """Block until the next simulated timestamp is due.

        Pause waits here, at the feed boundary. A kill/stop wakes the wait and
        raises so the feed loop can exit without advancing.
        """
        target = _utc(ts)
        with self._condition:
            while self._paused and not (self._killed or self._stopped):
                self._condition.wait(timeout=0.25)
            if self._killed:
                raise ClockKilled(self._killed_reason or "killed")
            if self._stopped:
                raise ClockStopped("clock is stopped")
            if self._now is None or not self.realtime:
                return
            if self.mode == "live-paper":
                # Live paper is tied to wall time: catch up on cached ticks and
                # sleep only until a genuinely future session tick. Replay
                # remains driven by simulated time and ``speed``.
                delay = (target - _utc(self._wall_clock())).total_seconds() / self._speed
            else:
                delay = (target - self._now).total_seconds() / self._speed
        if delay <= 0:
            return
        # Use a wall-clock deadline: simulated ``_now`` only moves when a tick
        # is accepted, so comparing against it would sleep forever.
        deadline = time.monotonic() + delay
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(remaining, self.max_sleep_seconds))
            with self._condition:
                if self._killed:
                    raise ClockKilled(self._killed_reason or "killed")
                if self._stopped:
                    raise ClockStopped("clock is stopped")
                if self._paused:
                    paused_at = time.monotonic()
                    while self._paused and not (self._killed or self._stopped):
                        self._condition.wait(timeout=0.25)
                    if self._killed:
                        raise ClockKilled(self._killed_reason or "killed")
                    if self._stopped:
                        raise ClockStopped("clock is stopped")
                    # Paused wall time does not consume the replay budget.
                    deadline += time.monotonic() - paused_at

    def wait_if_paused(self) -> None:
        with self._condition:
            while self._paused and not (self._killed or self._stopped):
                self._condition.wait(timeout=0.25)
            if self._killed:
                raise ClockKilled(self._killed_reason or "killed")
            if self._stopped:
                raise ClockStopped("clock is stopped")

    # -- controls ------------------------------------------------------
    def pause(self) -> None:
        with self._condition:
            if self._killed:
                raise ClockKilled(self._killed_reason or "killed")
            self._paused = True
            self._condition.notify_all()

    def resume(self) -> None:
        with self._condition:
            if self._killed:
                raise ClockKilled(self._killed_reason or "killed")
            if self._stopped:
                raise ClockStopped("clock is stopped")
            self._paused = False
            self._condition.notify_all()

    def kill(self, reason: str = "") -> None:
        with self._condition:
            if self._killed:
                return
            self._killed = True
            self._killed_reason = reason or "kill switch"
            self._paused = False
            self._condition.notify_all()

    def stop(self) -> None:
        with self._condition:
            if self._killed:
                return
            self._stopped = True
            self._condition.notify_all()

    def reset(self) -> None:
        """Reset for a fresh run. Only valid after stop; never clears kill."""
        with self._condition:
            if self._killed:
                raise ClockKilled(self._killed_reason or "killed")
            self._now = None
            self._last_tick = None
            self._session = None
            self._paused = False
            self._stopped = False
            self.stats = ClockStats()
            self._condition.notify_all()

    # -- diagnostics ---------------------------------------------------
    def as_dict(self) -> dict:
        with self._condition:
            return {
                "mode": self.mode,
                "state": self.state(),
                "now": self._now.isoformat() if self._now else None,
                "last_tick": self._last_tick.isoformat() if self._last_tick else None,
                "session": self._session.isoformat() if self._session else None,
                "speed": self._speed,
                "realtime": self.realtime,
                "paused": self._paused,
                "killed": self._killed,
                "kill_reason": self._killed_reason,
                "accepted_ticks": self.stats.accepted_ticks,
                "rejected_ticks": self.stats.rejected_ticks,
            }


def drain(ticks: Iterable[datetime], clock: SimulationClock) -> int:
    """Small helper: advance a clock through timestamps; return accepted count."""
    n = 0
    for ts in ticks:
        clock.advance(ts)
        n += 1
    return n
