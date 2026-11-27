"""Clock tests: monotonic replay time, speed, pause/resume and kill."""

from __future__ import annotations

import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot.prodsim.clock import (  # noqa: E402
    ClockKilled,
    ClockPaused,
    ClockStopped,
    OutOfOrderTick,
    SimulationClock,
    drain,
)

T0 = datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc)


def test_monotonic_replay() -> None:
    clock = SimulationClock(realtime=False)
    clock.start(T0)
    assert clock.advance(T0) == T0
    assert clock.advance(T0 + timedelta(seconds=1)) > T0
    for bad in (T0, T0 - timedelta(seconds=1)):
        try:
            clock.advance(bad)
        except OutOfOrderTick:
            pass
        else:
            raise AssertionError(f"accepted non-monotonic {bad}")
    assert clock.stats.accepted_ticks == 2
    assert clock.stats.rejected_ticks == 2
    assert clock.state() == "RUNNING"


def test_naive_timestamp_is_utc() -> None:
    clock = SimulationClock(realtime=False)
    clock.start(datetime(2026, 1, 2, 14, 30))
    got = clock.advance(datetime(2026, 1, 2, 14, 30, 1))
    assert got.tzinfo is timezone.utc


def test_pause_resume_and_kill() -> None:
    clock = SimulationClock(realtime=False)
    clock.start(T0)
    clock.pause()
    assert clock.state() == "PAUSED"
    try:
        clock.advance(T0 + timedelta(seconds=1))
    except ClockPaused:
        pass
    else:
        raise AssertionError("paused clock accepted a tick")
    clock.resume()
    clock.advance(T0 + timedelta(seconds=1))
    clock.kill("operator")
    assert clock.state() == "KILLED"
    try:
        clock.advance(T0 + timedelta(seconds=2))
    except ClockKilled:
        pass
    else:
        raise AssertionError("killed clock accepted a tick")
    try:
        clock.resume()
    except ClockKilled:
        pass
    else:
        raise AssertionError("killed clock resumed")


def test_stop_and_reset() -> None:
    clock = SimulationClock(realtime=False)
    clock.start(T0)
    clock.stop()
    assert clock.state() == "STOPPED"
    try:
        clock.advance(T0 + timedelta(seconds=1))
    except ClockStopped:
        pass
    else:
        raise AssertionError("stopped clock accepted a tick")
    clock.reset()
    clock.start(T0)
    assert clock.state() == "RUNNING"


def test_speed_and_wait() -> None:
    clock = SimulationClock(speed=2.0, realtime=False)
    assert clock.set_speed(5.0) == 5.0
    clock.start(T0)
    started = time.monotonic()
    clock.wait_until(T0 + timedelta(hours=1))
    # realtime=False never sleeps
    assert time.monotonic() - started < 0.2
    try:
        clock.set_speed(0)
    except ValueError:
        pass
    else:
        raise AssertionError("accepted zero speed")


def test_wait_until_kill_interrupts() -> None:
    clock = SimulationClock(speed=1.0, realtime=True, max_sleep_seconds=0.05)
    clock.start(T0)
    result = []

    def waiter() -> None:
        try:
            clock.wait_until(T0 + timedelta(seconds=30))
        except ClockKilled:
            result.append("killed")

    thread = threading.Thread(target=waiter)
    thread.start()
    time.sleep(0.1)
    clock.kill("stop test")
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert result == ["killed"]


def test_pause_waits_at_feed_boundary() -> None:
    clock = SimulationClock(realtime=True, max_sleep_seconds=0.02)
    clock.start(T0)
    clock.pause()
    done = []

    def waiter() -> None:
        clock.wait_if_paused()
        done.append(True)

    thread = threading.Thread(target=waiter)
    thread.start()
    time.sleep(0.1)
    assert thread.is_alive() and not done
    clock.resume()
    thread.join(timeout=2)
    assert done == [True]


def test_drain_and_dict() -> None:
    clock = SimulationClock(realtime=False)
    clock.start(T0)
    assert drain((T0 + timedelta(seconds=i) for i in range(1, 4)), clock) == 3
    out = clock.as_dict()
    assert out["accepted_ticks"] == 3
    assert out["session"] == "2026-01-02"
    assert out["state"] == "RUNNING"


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok: {name}")
    print("ok: prodsim clock")


if __name__ == "__main__":
    main()
