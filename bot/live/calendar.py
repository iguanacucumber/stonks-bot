"""US equity market calendar, deterministic and dependency-free.

``data.target_session`` already rolls weekends back and the cached SPY bar is the
final authority on whether a session actually printed. This module fills the one gap
the live loop cannot infer from history: whether *today* is the last session of the
month, which decides a month-end rebalance before the next bar exists.

Holiday rules follow the NYSE: New Year, MLK, Presidents, Good Friday, Memorial,
Juneteenth (2022+), Independence, Labor, Thanksgiving, Christmas, plus known one-off
closures. Ambiguity is resolved by failing closed in the engine, never by trading.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo

import pandas as pd

# One-off full-day closures relevant to the cached window and near future.
SPECIAL_CLOSURES: frozenset[date] = frozenset(
    {
        date(2001, 9, 11),
        date(2001, 9, 12),
        date(2001, 9, 13),
        date(2001, 9, 14),
        date(2004, 6, 11),  # Reagan funeral
        date(2007, 1, 2),  # Ford funeral
        date(2012, 10, 29),  # Hurricane Sandy
        date(2012, 10, 30),
        date(2018, 12, 5),  # Bush funeral
        date(2025, 1, 9),  # Carter funeral
    }
)


def _observed(day: date) -> date:
    """NYSE observed rule: Saturday -> Friday, Sunday -> Monday."""
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    d = date(year, month, 1)
    while d.weekday() != weekday:
        d += timedelta(days=1)
    return d + timedelta(days=7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    if month == 12:
        d = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        d = date(year, month + 1, 1) - timedelta(days=1)
    while d.weekday() != weekday:
        d -= timedelta(days=1)
    return d


def _easter(year: int) -> date:
    """Anonymous Gregorian algorithm (Meeus/Jones/Butcher)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


@lru_cache(maxsize=64)
def holidays(year: int) -> frozenset[date]:
    """NYSE full-day holidays for one calendar year."""
    out = {
        _nth_weekday(year, 1, 0, 3),  # MLK
        _nth_weekday(year, 2, 0, 3),  # Presidents
        _easter(year) - timedelta(days=2),  # Good Friday
        _last_weekday(year, 5, 0),  # Memorial
        _observed(date(year, 7, 4)),
        _nth_weekday(year, 9, 0, 1),  # Labor
        _nth_weekday(year, 11, 3, 4),  # Thanksgiving
        _observed(date(year, 12, 25)),
    }
    # New Year's is the exception: when Jan 1 is a Saturday the NYSE stays open on
    # Dec 31 (unlike the federal observed rule). Sunday still closes Monday Jan 2.
    new_year = date(year, 1, 1)
    if new_year.weekday() == 6:
        out.add(date(year, 1, 2))
    elif new_year.weekday() < 5:
        out.add(new_year)
    if year >= 2022:
        out.add(_observed(date(year, 6, 19)))  # Juneteenth
    out.update(d for d in SPECIAL_CLOSURES if d.year == year)
    return frozenset(out)


def as_date(ts: str | date | pd.Timestamp) -> date:
    if isinstance(ts, date) and not isinstance(ts, pd.Timestamp):
        return ts
    return pd.Timestamp(ts).date()


def is_session(day: str | date | pd.Timestamp) -> bool:
    """True for a regular NYSE weekday session (holidays excluded)."""
    d = as_date(day)
    return d.weekday() < 5 and d not in holidays(d.year)


def _roll_forward(d: date) -> date:
    while not is_session(d):
        d += timedelta(days=1)
    return d


def _roll_back(d: date) -> date:
    while not is_session(d):
        d -= timedelta(days=1)
    return d


def next_session(day: str | date | pd.Timestamp) -> date:
    """First session strictly after ``day``."""
    return _roll_forward(as_date(day) + timedelta(days=1))


def prev_session(day: str | date | pd.Timestamp) -> date:
    """Last session strictly before ``day``."""
    return _roll_back(as_date(day) - timedelta(days=1))


def last_session_of_month(year: int, month: int) -> date:
    if month == 12:
        d = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        d = date(year, month + 1, 1) - timedelta(days=1)
    return _roll_back(d)


def is_half_day(day: str | date | pd.Timestamp) -> bool:
    """NYSE early-close session (13:00 ET).

    Known early closes: the Friday after Thanksgiving, Christmas Eve, and July 3
    when Independence Day is a weekday. A half-day is a session, but its close is
    13:00 ET; a 16:00 close-valid check would be wrong.
    """
    d = as_date(day)
    if not is_session(d):
        return False
    thanksgiving = _nth_weekday(d.year, 11, 3, 4)
    if d == thanksgiving + timedelta(days=1):
        return True
    if (d.month, d.day) == (12, 24):
        return True
    if (d.month, d.day) == (7, 3) and date(d.year, 7, 4).weekday() < 5:
        return True
    return False


def close_hour(day: str | date | pd.Timestamp) -> int:
    """Exchange close hour in New York: 13 for a half-day, else 16."""
    return 13 if is_half_day(day) else 16


def target_session(
    now: datetime | None = None, data_delay_minutes: int = 30
) -> pd.Timestamp:
    """Last completed session using the actual close, including half-days.

    ``bot.data.target_session`` uses a fixed 16:30 ET cutoff, which is correct
    for normal sessions but wrong for a 13:00 ET early close.  This helper is the
    forward-gate/daily equivalent: a half-day becomes complete at 13:30 ET.
    """
    et = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo("America/New_York"))
    d = et.date()
    if not is_session(d):
        # A holiday/weekend has no session today. Return the previous session
        # directly; do not apply the intraday cutoff to the previous day again
        # (that would regress 11-26 holiday from 11-25 to 11-24).
        return pd.Timestamp(prev_session(d))
    close = close_hour(d)
    closed = et.hour > close or (
        et.hour == close and et.minute >= int(data_delay_minutes)
    )
    if not closed:
        d = prev_session(d)
    return pd.Timestamp(d)


def is_rebalance_session(
    day: str | date | pd.Timestamp, every_months: int = 1, offset: int = 0
) -> bool:
    """Month-end calendar rebalance, matching ``strategy.Config.rebalance_mode``.

    ``every_months`` and ``offset`` mirror the backtest: month index
    ``(month - 1 - offset) % every_months == 0`` and the session is the last
    session of that month.
    """
    d = as_date(day)
    if not is_session(d):
        return False
    if every_months < 1:
        raise ValueError("every_months must be >= 1")
    if last_session_of_month(d.year, d.month) != d:
        return False
    return (d.month - 1 - offset) % every_months == 0


def sessions_between(
    start_exclusive: str | date, end_inclusive: str | date
) -> list[date]:
    """All sessions in ``(start, end]``; empty when the range is reversed."""
    a, b = as_date(start_exclusive), as_date(end_inclusive)
    out: list[date] = []
    d = a + timedelta(days=1)
    while d <= b:
        if is_session(d):
            out.append(d)
        d += timedelta(days=1)
    return out


def count_sessions(start_exclusive: str | date, end_inclusive: str | date) -> int:
    return len(sessions_between(start_exclusive, end_inclusive))
