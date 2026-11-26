"""Walk-forward splitters with explicit, non-overlapping boundaries.

The default rolling schedule matches the research protocol's shape:

    train 5 calendar years -> validate 2 -> test 1 -> roll everything 1 year

Each returned fold stores the actual first/last trading dates (inclusive) and
the half-open calendar boundaries used to build it.  Validation starts on the
first trading day after training, and test starts on the first trading day after
validation, so no observation is shared between splits.  Concatenating the test
slices of consecutive folds gives a genuinely out-of-sample path.

For the protocol's alternative single holdout (train 2015-2019, validate
2020-2021, test 2022-2026), pass ``test_years=4`` and ``include_partial=True``.
For short-horizon expanding folds, use :func:`expanding_splits`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import pandas as pd


@dataclass(frozen=True)
class Fold:
    index: int
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    validate_start: pd.Timestamp
    validate_end: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp
    train_start_ts: pd.Timestamp
    validate_start_ts: pd.Timestamp
    test_start_ts: pd.Timestamp
    test_end_ts: pd.Timestamp

    def label(self, dates: pd.DatetimeIndex) -> pd.Series:
        """Assign 'train', 'validate', 'test' or 'unused' to every date."""
        out = pd.Series("unused", index=dates, dtype=object)
        out.loc[(dates >= self.train_start) & (dates <= self.train_end)] = "train"
        out.loc[(dates >= self.validate_start) & (dates <= self.validate_end)] = (
            "validate"
        )
        out.loc[(dates >= self.test_start) & (dates <= self.test_end)] = "test"
        return out


def _as_dates(dates) -> pd.DatetimeIndex:
    if isinstance(dates, pd.DatetimeIndex):
        d = dates
    elif isinstance(dates, pd.Series):
        d = pd.DatetimeIndex(pd.to_datetime(dates))
    else:
        d = pd.DatetimeIndex(pd.to_datetime(list(dates)))
    return d.drop_duplicates().sort_values()


def _slice_bounds(
    dates: pd.DatetimeIndex, start: pd.Timestamp, end: pd.Timestamp
) -> tuple[pd.Timestamp, pd.Timestamp] | None:
    sub = dates[(dates >= start) & (dates < end)]
    if len(sub) == 0:
        return None
    return sub[0], sub[-1]


def rolling_splits(
    dates,
    train_years: int = 5,
    validate_years: int = 2,
    test_years: int = 1,
    step_years: int = 1,
    start: str | pd.Timestamp | None = None,
    end: str | pd.Timestamp | None = None,
    include_partial: bool = True,
    expanding_train: bool = False,
) -> list[Fold]:
    """Rolling train/validate/test folds.

    ``include_partial`` keeps a final fold whose test end extends past the last
    observation.  The test slice is then shorter, and ``test_end`` records the
    last actual date.  Set it to False for only complete test windows.

    ``expanding_train`` keeps the training start fixed while the training end
    rolls; the validation/test windows remain the same width.
    """
    d = _as_dates(dates)
    if len(d) < 2:
        return []
    first = pd.Timestamp(start) if start is not None else d[0]
    last = pd.Timestamp(end) if end is not None else d[-1]
    d = d[(d >= first) & (d <= last)]
    if len(d) < 2:
        return []
    if min(train_years, validate_years, test_years, step_years) <= 0:
        raise ValueError("window widths must be positive")
    folds: list[Fold] = []
    cursor = first.normalize()
    fixed_train_start = cursor
    idx = 0
    while True:
        tr_start = fixed_train_start if expanding_train else cursor
        tr_end = cursor + pd.DateOffset(years=train_years)
        va_end = tr_end + pd.DateOffset(years=validate_years)
        te_end = va_end + pd.DateOffset(years=test_years)
        if te_end > last + pd.Timedelta(days=1) and not include_partial:
            break
        if tr_end > last + pd.Timedelta(days=1):
            break
        train = _slice_bounds(d, tr_start, tr_end)
        validate = _slice_bounds(d, tr_end, va_end)
        test = _slice_bounds(d, va_end, min(te_end, last + pd.Timedelta(days=1)))
        if train and validate and test:
            folds.append(
                Fold(
                    index=idx,
                    train_start=train[0],
                    train_end=train[1],
                    validate_start=validate[0],
                    validate_end=validate[1],
                    test_start=test[0],
                    test_end=test[1],
                    train_start_ts=pd.Timestamp(tr_start),
                    validate_start_ts=pd.Timestamp(tr_end),
                    test_start_ts=pd.Timestamp(va_end),
                    test_end_ts=pd.Timestamp(te_end),
                )
            )
            idx += 1
        cursor = cursor + pd.DateOffset(years=step_years)
        if cursor + pd.DateOffset(years=train_years) > last + pd.Timedelta(days=1):
            break
    return folds


def expanding_splits(
    dates,
    initial_train_years: int = 3,
    test_years: int = 1,
    step_years: int = 1,
    start: str | pd.Timestamp | None = None,
    end: str | pd.Timestamp | None = None,
    include_partial: bool = True,
    min_folds: int = 3,
) -> list[Fold]:
    """Expanding training window, one-year (or ``test_years``) test folds.

    Validation is not used by this short-horizon helper; the validate slice is
    empty and represented by the boundary at the test start.  The function
    raises if fewer than ``min_folds`` usable folds exist, because a two-fold
    walk-forward is not evidence.
    """
    d = _as_dates(dates)
    if len(d) < 2:
        return []
    first = pd.Timestamp(start) if start is not None else d[0]
    last = pd.Timestamp(end) if end is not None else d[-1]
    d = d[(d >= first) & (d <= last)]
    folds: list[Fold] = []
    test_start_ts = first.normalize() + pd.DateOffset(years=initial_train_years)
    idx = 0
    while test_start_ts <= last:
        test_end_ts = test_start_ts + pd.DateOffset(years=test_years)
        if test_end_ts > last + pd.Timedelta(days=1) and not include_partial:
            break
        train = _slice_bounds(d, first, test_start_ts)
        test = _slice_bounds(
            d, test_start_ts, min(test_end_ts, last + pd.Timedelta(days=1))
        )
        if train and test:
            folds.append(
                Fold(
                    index=idx,
                    train_start=train[0],
                    train_end=train[1],
                    validate_start=test[0],
                    validate_end=test[0],
                    test_start=test[0],
                    test_end=test[1],
                    train_start_ts=pd.Timestamp(first),
                    validate_start_ts=pd.Timestamp(test_start_ts),
                    test_start_ts=pd.Timestamp(test_start_ts),
                    test_end_ts=pd.Timestamp(test_end_ts),
                )
            )
            idx += 1
        test_start_ts = test_start_ts + pd.DateOffset(years=step_years)
    if len(folds) < min_folds:
        raise ValueError(
            f"need at least {min_folds} walk-forward folds, got {len(folds)}"
        )
    return folds


def assert_no_leakage(folds: Iterable[Fold]) -> None:
    """Raise if any fold overlaps in time or is out of order."""
    folds = list(folds)
    for f in folds:
        if not (f.train_start <= f.train_end < f.test_start <= f.test_end):
            raise AssertionError(f"fold {f.index} is not strictly ordered")
        if f.validate_start < f.validate_end:
            if not (f.train_end < f.validate_start and f.validate_end < f.test_start):
                raise AssertionError(f"fold {f.index} validation overlaps")
        elif not (f.validate_start == f.validate_end == f.test_start):
            raise AssertionError(
                f"fold {f.index} has an empty validation slice that does not "
                "start at the test boundary"
            )
    for a, b in zip(folds, folds[1:]):
        if b.test_start <= a.test_end:
            raise AssertionError(
                f"test windows overlap: fold {a.index} ends {a.test_end}, "
                f"fold {b.index} starts {b.test_start}"
            )
        if b.test_start <= a.test_start:
            raise AssertionError(f"fold {b.index} does not advance in time")


def oos_path(equity: pd.Series, folds: Iterable[Fold]) -> pd.Series:
    """Concatenate non-overlapping test slices from consecutive folds."""
    folds = list(folds)
    assert_no_leakage(folds)
    parts = [equity.loc[f.test_start : f.test_end] for f in folds]
    parts = [p for p in parts if len(p)]
    if not parts:
        return pd.Series(dtype=float)
    out = pd.concat(parts)
    return out[~out.index.duplicated(keep="first")].sort_index()
