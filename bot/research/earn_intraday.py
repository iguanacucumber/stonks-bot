"""Intraday earnings 8-K item 2.02 continuation/fade event study.

The event is the SEC acceptance timestamp of an 8-K whose items include
``2.02`` (results of operations / earnings release).  The trade signal is the
first **completed** regular-session bar strictly after acceptance; entry is
the open of the next bar.  No same-bar fill and no entry at the 16:00 auction
print.

For the usual after-close earnings release (16:05-16:30 ET), the first
completed bar is the next session's 09:30-10:30 ET bar and entry is 10:30 ET.
That deliberately gives up the overnight gap: the study tests continuation or
fade of the first hour, not the opening print itself.

Exits are the entry-session close, the next-session close, or the close five
sessions after entry.  Costs are 5 bps/side base and 10 bps/side at 2x;
shorts pay 5%/yr borrow (10%/yr at 2x).  The portfolio is capped at five
concurrent positions, each 20% of equity, and idle cash can earn BIL.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from bot.news import _accepted_et
from bot.research.short3.intraday import _tax_equity, load_bars

ROOT = Path(__file__).resolve().parents[2]
NEWS = ROOT / "cache" / "news"
EARNINGS_ITEM = "2.02"

# Liquid 2.02 filers that have cached 1h/2y and 5m/60d bars.
DEFAULT_SYMBOLS = (
    "ALB",
    "AMAT",
    "AMD",
    "APH",
    "CF",
    "FANG",
    "FDX",
    "INTC",
    "LUV",
    "ON",
    "OXY",
    "TSLA",
)

SIGNALS = ("gap", "reaction", "move")
MODES = ("continuation", "fade")
EXITS = ("close", "next_session", "5d")
MAX_CONCURRENT = 5
BASE_COST_BPS = 5.0
DOUBLE_COST_BPS = 10.0
BASE_BORROW_BPS = 500.0
DOUBLE_BORROW_BPS = 1000.0

REGULAR_OPEN_MINUTE = 9 * 60 + 30
REGULAR_CLOSE_MINUTE = 16 * 60


@dataclass(frozen=True)
class TickerBars:
    """Numpy view of one ticker's intraday bars for fast event simulation."""

    ts: np.ndarray
    open: np.ndarray
    close: np.ndarray
    minutes: np.ndarray
    sessions: pd.DatetimeIndex
    session_idx: np.ndarray
    first_bar: np.ndarray
    last_bar: np.ndarray
    close_by_session: np.ndarray
    end_ts: np.ndarray

    def __len__(self) -> int:
        return len(self.ts)


def _items_set(value: object) -> set[str]:
    return {x.strip() for x in str(value or "").split(",") if x.strip()}


def load_earnings_events(
    symbols: tuple[str, ...] = DEFAULT_SYMBOLS,
    *,
    start: str | pd.Timestamp | None = None,
    end: str | pd.Timestamp | None = None,
    item: str = EARNINGS_ITEM,
    news_dir: Path | None = None,
) -> pd.DataFrame:
    """Cached 8-K item-2.02 events with acceptance time in naive New York time.

    A filing with no acceptance timestamp is treated as known after the
    filing-day close.  Repeated 2.02 filings on the same ticker/filing day are
    collapsed to the first one so a single earnings release is not counted
    twice.
    """
    allowed = {s.upper() for s in symbols}
    start_ts = pd.Timestamp(start).normalize() if start is not None else None
    end_ts = pd.Timestamp(end).normalize() if end is not None else None
    rows: list[dict] = []
    for path in sorted((news_dir or NEWS).glob("sub_*.json")):
        try:
            data = json.loads(path.read_text())
        except Exception:
            continue
        tickers = data.get("tickers") or []
        if not tickers:
            continue
        ticker = str(tickers[0]).upper()
        if ticker not in allowed:
            continue
        recent = data.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        items = recent.get("items", [])
        filed = recent.get("filingDate", [])
        accepted = recent.get("acceptanceDateTime", [])
        accession = recent.get("accessionNumber", [])
        for i, form in enumerate(forms):
            if form != "8-K":
                continue
            tags = _items_set(items[i] if i < len(items) else "")
            if item not in tags:
                continue
            if not filed[i]:
                continue
            filed_ts = pd.Timestamp(filed[i]).normalize()
            if start_ts is not None and filed_ts < start_ts:
                continue
            if end_ts is not None and filed_ts > end_ts:
                continue
            accepted_raw = str(accepted[i] if i < len(accepted) else "")
            accepted_et = _accepted_et(accepted_raw)
            event_time = (
                accepted_et
                if accepted_et is not None
                else filed_ts + pd.Timedelta(hours=16)
            )
            rows.append(
                {
                    "ticker": ticker,
                    "filed": filed_ts,
                    "event_time": pd.Timestamp(event_time),
                    "accepted": accepted_raw,
                    "items": ",".join(sorted(tags)),
                    "accession": str(accession[i] if i < len(accession) else ""),
                    "timestamp_source": "accepted"
                    if accepted_et is not None
                    else "filed_close",
                }
            )
    if not rows:
        return pd.DataFrame(
            columns=[
                "ticker",
                "filed",
                "event_time",
                "accepted",
                "items",
                "accession",
                "timestamp_source",
            ]
        )
    df = pd.DataFrame(rows).sort_values(["event_time", "ticker"])
    df = df.drop_duplicates(["ticker", "filed"], keep="first")
    return df.reset_index(drop=True)


def build_ticker_bars(bars: pd.DataFrame) -> TickerBars:
    """Prepare a sorted intraday frame for event simulation."""
    if bars.empty:
        raise ValueError("empty bars")
    b = bars.sort_values("ts").reset_index(drop=True).copy()
    ts_index = pd.DatetimeIndex(b["ts"])
    sessions = pd.DatetimeIndex(sorted(b["session"].unique()))
    session_idx = sessions.get_indexer(pd.DatetimeIndex(b["session"]))
    if (session_idx < 0).any():
        raise ValueError("bar session outside session calendar")
    first_bar = np.full(len(sessions), -1, dtype=int)
    last_bar = np.full(len(sessions), -1, dtype=int)
    for i, si in enumerate(session_idx):
        if first_bar[si] < 0:
            first_bar[si] = i
        last_bar[si] = i
    close_by_session = b["close"].to_numpy(dtype=float)[last_bar]
    ts = ts_index.to_numpy(dtype="datetime64[ns]")
    minutes = (ts_index.hour * 60 + ts_index.minute).to_numpy(dtype=int)
    # Actual bar end: the next bar's start in the same session, otherwise the
    # 16:00 ET session close (the last hourly bar is only 30 minutes long).
    end_ts = np.empty(len(ts), dtype="datetime64[ns]")
    for i in range(len(ts)):
        if i + 1 < len(ts) and session_idx[i + 1] == session_idx[i]:
            end_ts[i] = ts[i + 1]
        else:
            end_ts[i] = np.datetime64(
                pd.Timestamp(sessions[session_idx[i]]) + pd.Timedelta(hours=16),
                "ns",
            )
    return TickerBars(
        ts=ts,
        open=b["open"].to_numpy(dtype=float),
        close=b["close"].to_numpy(dtype=float),
        minutes=minutes,
        sessions=sessions,
        session_idx=session_idx.astype(int),
        first_bar=first_bar,
        last_bar=last_bar,
        close_by_session=close_by_session.astype(float),
        end_ts=end_ts,
    )


def _as_ticker_bars(bars: pd.DataFrame | TickerBars) -> TickerBars:
    return bars if isinstance(bars, TickerBars) else build_ticker_bars(bars)


def reaction_index(tb: TickerBars, event_time: pd.Timestamp) -> int | None:
    """First completed regular-session bar strictly after ``event_time``.

    A 16:00 row is an auction print, not a tradable reaction bar.  The bar is
    only usable if a later regular-session bar exists for the entry fill.
    """
    stamp = np.datetime64(pd.Timestamp(event_time), "ns")
    pos = int(np.searchsorted(tb.ts, stamp, side="right"))
    n = len(tb)
    while pos < n:
        minute = int(tb.minutes[pos])
        if REGULAR_OPEN_MINUTE <= minute < REGULAR_CLOSE_MINUTE:
            # The entry is the next tradable regular-session bar; skip a
            # 16:00 auction print and continue at the next session open.
            nxt = pos + 1
            while nxt < n and int(tb.minutes[nxt]) >= REGULAR_CLOSE_MINUTE:
                nxt += 1
            return pos if nxt < n else None
        pos += 1
    return None


def _signal_value(tb: TickerBars, pos: int) -> tuple[float, float, float]:
    sess = int(tb.session_idx[pos])
    if sess <= 0:
        return float("nan"), float("nan"), float("nan")
    prior_close = float(tb.close_by_session[sess - 1])
    rb_open = float(tb.open[pos])
    rb_close = float(tb.close[pos])
    if prior_close <= 0 or rb_open <= 0:
        return float("nan"), float("nan"), float("nan")
    gap = rb_open / prior_close - 1.0
    reaction = rb_close / rb_open - 1.0
    move = rb_close / prior_close - 1.0
    return gap, reaction, move


def _signal_from(gap: float, reaction: float, move: float, signal: str) -> float:
    if signal == "gap":
        return gap
    if signal == "reaction":
        return reaction
    if signal == "move":
        return move
    raise ValueError(signal)


def simulate_event(
    bars: pd.DataFrame | TickerBars,
    event_time: pd.Timestamp,
    *,
    signal: str = "gap",
    mode: str = "continuation",
    threshold: float = 0.0,
    exit_kind: str = "close",
    cost_bps: float = BASE_COST_BPS,
    borrow_bps: float = BASE_BORROW_BPS,
) -> dict | None:
    """One event trade, from reaction-bar close to the chosen exit."""
    if signal not in SIGNALS:
        raise ValueError(signal)
    if mode not in MODES:
        raise ValueError(mode)
    if exit_kind not in EXITS:
        raise ValueError(exit_kind)
    tb = _as_ticker_bars(bars)
    pos = reaction_index(tb, pd.Timestamp(event_time))
    if pos is None:
        return None
    entry_pos = pos + 1
    while entry_pos < len(tb) and int(tb.minutes[entry_pos]) >= REGULAR_CLOSE_MINUTE:
        entry_pos += 1
    if entry_pos >= len(tb):
        return None
    gap, reaction, move = _signal_value(tb, pos)
    value = _signal_from(gap, reaction, move, signal)
    if not np.isfinite(value) or abs(value) < threshold or value == 0:
        return None
    direction = 1.0 if value > 0 else -1.0
    if mode == "fade":
        direction = -direction

    entry = float(tb.open[entry_pos])
    entry_time = pd.Timestamp(tb.ts[entry_pos])
    entry_sess = int(tb.session_idx[entry_pos])
    if entry <= 0 or not np.isfinite(entry):
        return None
    if exit_kind == "close":
        target_sess = entry_sess
    elif exit_kind == "next_session":
        target_sess = entry_sess + 1
    else:
        target_sess = entry_sess + 5
    if target_sess >= len(tb.sessions):
        return None
    exit_idx = int(tb.last_bar[target_sess])
    exit_px = float(tb.close[exit_idx])
    if exit_px <= 0 or not np.isfinite(exit_px):
        return None
    exit_start = pd.Timestamp(tb.ts[exit_idx])
    exit_fill = pd.Timestamp(tb.end_ts[exit_idx])
    holding_days = max((exit_fill - entry_time).total_seconds() / 86400.0, 1.0 / 24.0)

    gross = direction * (exit_px / entry - 1.0)
    cost = 2.0 * float(cost_bps) / 1e4
    borrow = float(borrow_bps) / 1e4 * holding_days / 365.25 if direction < 0 else 0.0
    return {
        "reaction_time": pd.Timestamp(tb.ts[pos]),
        "reaction_session": pd.Timestamp(tb.sessions[int(tb.session_idx[pos])]),
        "entry_time": entry_time,
        "entry_session": pd.Timestamp(tb.sessions[entry_sess]),
        "exit_time": exit_start,
        "exit_fill_time": exit_fill,
        "exit_session": pd.Timestamp(tb.sessions[target_sess]),
        "entry": entry,
        "exit": exit_px,
        "direction": float(direction),
        "signal": signal,
        "mode": mode,
        "signal_value": float(value),
        "gap": float(gap),
        "reaction": float(reaction),
        "move": float(move),
        "holding_days": float(holding_days),
        "gross": float(gross),
        "cost": float(cost),
        "borrow": float(borrow),
        "net": float(gross - cost - borrow),
        "cost_bps": float(cost_bps),
        "borrow_bps": float(borrow_bps),
    }


_TRADE_COLUMNS = [
    "ticker",
    "filed",
    "event_time",
    "items",
    "timestamp_source",
    "reaction_time",
    "reaction_session",
    "entry_time",
    "entry_session",
    "exit_time",
    "exit_fill_time",
    "exit_session",
    "entry",
    "exit",
    "direction",
    "signal",
    "mode",
    "signal_value",
    "gap",
    "reaction",
    "move",
    "holding_days",
    "gross",
    "cost",
    "borrow",
    "net",
    "cost_bps",
    "borrow_bps",
]


def trades_for(
    bars_by_symbol: dict[str, pd.DataFrame | TickerBars],
    events: pd.DataFrame,
    *,
    signal: str = "gap",
    mode: str = "continuation",
    threshold: float = 0.0,
    exit_kind: str = "close",
    cost_bps: float = BASE_COST_BPS,
    borrow_bps: float = BASE_BORROW_BPS,
) -> pd.DataFrame:
    """All usable event trades, sorted by entry time."""
    prepared = {sym: _as_ticker_bars(bars) for sym, bars in bars_by_symbol.items()}
    rows: list[dict] = []
    for ev in events.itertuples(index=False):
        tb = prepared.get(str(ev.ticker))
        if tb is None:
            continue
        tr = simulate_event(
            tb,
            pd.Timestamp(ev.event_time),
            signal=signal,
            mode=mode,
            threshold=threshold,
            exit_kind=exit_kind,
            cost_bps=cost_bps,
            borrow_bps=borrow_bps,
        )
        if tr is None:
            continue
        tr.update(
            {
                "ticker": str(ev.ticker),
                "filed": pd.Timestamp(ev.filed),
                "event_time": pd.Timestamp(ev.event_time),
                "items": getattr(ev, "items", EARNINGS_ITEM),
                "timestamp_source": getattr(ev, "timestamp_source", "accepted"),
            }
        )
        rows.append(tr)
    if not rows:
        return pd.DataFrame(columns=_TRADE_COLUMNS)
    return pd.DataFrame(rows).sort_values("entry_time").reset_index(drop=True)


def select_concurrent(
    trades: pd.DataFrame, max_concurrent: int = MAX_CONCURRENT
) -> tuple[pd.DataFrame, int]:
    """Keep the earliest events until the concurrency cap would be exceeded."""
    if trades.empty:
        return trades.copy(), 0
    accepted: list[pd.DataFrame] = []
    open_exits: list[pd.Timestamp] = []
    skipped = 0
    for _, row in trades.sort_values("entry_time").iterrows():
        entry = pd.Timestamp(row["entry_time"])
        open_exits = [x for x in open_exits if x > entry]
        if len(open_exits) >= max_concurrent:
            skipped += 1
            continue
        accepted.append(pd.DataFrame([row]))
        open_exits.append(pd.Timestamp(row["exit_fill_time"]))
    if not accepted:
        return trades.iloc[0:0].copy(), skipped
    return pd.concat(accepted, ignore_index=True), skipped


def _bil_returns(sessions: pd.DatetimeIndex, path: Path | None = None) -> pd.Series:
    """BIL total-return daily series aligned to ``sessions``."""
    p = path or (ROOT / "cache" / "bars" / "BIL.csv")
    if not p.exists():
        return pd.Series(0.0, index=sessions)
    b = pd.read_csv(p, parse_dates=["date"]).sort_values("date")
    col = "adjclose" if "adjclose" in b.columns else "close"
    r = b.set_index("date")[col].pct_change().fillna(0.0)
    return r.reindex(sessions).fillna(0.0)


def portfolio_daily(
    trades: pd.DataFrame,
    bars_by_symbol: dict[str, pd.DataFrame | TickerBars],
    sessions: pd.DatetimeIndex,
    *,
    cost_bps: float = BASE_COST_BPS,
    borrow_bps: float = BASE_BORROW_BPS,
    max_concurrent: int = MAX_CONCURRENT,
    cash_returns: pd.Series | None = None,
    sizing: str = "fixed_slots",
) -> dict:
    """Daily equity path with max-concurrent slots and optional BIL carry.

    ``sizing="fixed_slots"`` gives each accepted trade 20% of equity at entry.
    ``sizing="equal_active"`` is an upper-bound capital-utilization check:
    active positions share 100% of equity equally each day, with no charge for
    rebalancing.  A long is cash-backed; a short pays borrow and does not earn
    interest on the short proceeds.  If ``cash_returns`` is supplied,
    uninvested cash earns that daily return.
    """
    if sizing not in ("fixed_slots", "equal_active"):
        raise ValueError(sizing)
    if trades.empty:
        daily = pd.Series(0.0, index=sessions)
        eq, tax = _tax_equity(daily)
        return {
            "daily": daily,
            "equity": eq,
            "accepted": trades.copy(),
            "n_skipped": 0,
            "tax": tax,
        }
    prepared = {sym: _as_ticker_bars(bars) for sym, bars in bars_by_symbol.items()}
    accepted, n_skipped = select_concurrent(trades, max_concurrent)
    cash = (
        cash_returns.reindex(sessions).fillna(0.0)
        if cash_returns is not None
        else pd.Series(0.0, index=sessions)
    )

    # Build the daily signed-return schedule of every accepted trade.
    schedule: dict[pd.Timestamp, list[tuple[int, float]]] = {}
    meta: list[dict] = []
    for i, row in accepted.iterrows():
        tb = prepared.get(str(row["ticker"]))
        if tb is None:
            continue
        entry_sess = pd.Timestamp(row["entry_session"])
        exit_sess = pd.Timestamp(row["exit_session"])
        si = int(tb.sessions.searchsorted(entry_sess))
        ei = int(tb.sessions.searchsorted(exit_sess))
        if si < 0 or ei >= len(tb.sessions) or si > ei:
            continue
        direction = float(row["direction"])
        entry_px = float(row["entry"])
        exit_px = float(row["exit"])
        days: list[tuple[pd.Timestamp, float]] = []
        ok = True
        for k in range(si, ei + 1):
            sess = pd.Timestamp(tb.sessions[k])
            start_px = entry_px if k == si else float(tb.close_by_session[k - 1])
            end_px = exit_px if k == ei else float(tb.close_by_session[k])
            if start_px <= 0 or end_px <= 0:
                ok = False
                break
            days.append((sess, direction * (end_px / start_px - 1.0)))
        if not ok or not days:
            continue
        trade_i = len(meta)
        meta.append(
            {
                "entry_session": entry_sess,
                "exit_session": exit_sess,
                "direction": direction,
                "holding_days": float(row["holding_days"]),
                "entry_fill_time": pd.Timestamp(row["entry_time"]),
                "exit_fill_time": pd.Timestamp(row["exit_fill_time"]),
            }
        )
        for sess, ret in days:
            schedule.setdefault(sess, []).append((trade_i, ret))

    equity = 1.0
    allocations: dict[int, float] = {}
    daily_returns: list[float] = []
    for sess in sessions:
        sess = pd.Timestamp(sess)
        equity_start = equity
        cash_ret = float(cash.get(sess, 0.0))
        active = schedule.get(sess, [])
        if sizing == "equal_active" and active:
            weight = 1.0 / len(active)
            n_long = sum(1 for trade_i, _ in active if meta[trade_i]["direction"] > 0)
            # Short proceeds earn no interest; the original cash does.
            cash_weight = 1.0 - n_long * weight
            pnl = equity_start * cash_weight * cash_ret
        else:
            pnl = equity_start * cash_ret
        for trade_i, signed_ret in active:
            m = meta[trade_i]
            if sizing == "equal_active":
                alloc = equity_start / len(active)
                pnl += alloc * signed_ret
            else:
                if trade_i not in allocations:
                    allocations[trade_i] = equity_start / max_concurrent
                alloc = allocations[trade_i]
                if m["direction"] > 0:
                    # Long: the cash used to buy the stock no longer earns BIL.
                    pnl += alloc * (signed_ret - cash_ret)
                else:
                    # Short: original cash still earns BIL; short proceeds do not.
                    pnl += alloc * signed_ret
            if sess == m["entry_session"]:
                pnl -= alloc * float(cost_bps) / 1e4
            if sess == m["exit_session"]:
                pnl -= alloc * float(cost_bps) / 1e4
                if m["direction"] < 0:
                    pnl -= alloc * float(borrow_bps) / 1e4 * m["holding_days"] / 365.25
        equity = equity_start + pnl
        daily_returns.append(pnl / equity_start if equity_start > 0 else 0.0)
    daily = pd.Series(daily_returns, index=sessions, dtype=float)
    eq, tax = _tax_equity(daily)
    return {
        "daily": daily,
        "equity": eq,
        "accepted": accepted,
        "n_skipped": n_skipped,
        "tax": tax,
    }


def summarize(
    trades: pd.DataFrame,
    *,
    portfolio: dict,
    sessions: pd.DatetimeIndex,
    placebo_p: float = float("nan"),
) -> dict:
    """Trade-level and after-tax portfolio summary."""
    if trades.empty:
        return {
            "n": 0,
            "n_traded": 0,
            "n_skipped": 0,
            "hit": float("nan"),
            "mean": float("nan"),
            "median": float("nan"),
            "gross_mean": float("nan"),
            "multiple": float("nan"),
            "cagr": float("nan"),
            "maxdd": float("nan"),
            "tax": 0.0,
            "placebo_p": float("nan"),
        }
    accepted = portfolio["accepted"]
    eq = portfolio["equity"]
    years = (
        (pd.Timestamp(sessions[-1]) - pd.Timestamp(sessions[0])).days / 365.25
        if len(sessions) > 1
        else 0.0
    )
    net = trades["net"].to_numpy(dtype=float)
    traded_net = accepted["net"].to_numpy(dtype=float) if len(accepted) else net
    return {
        "n": len(trades),
        "n_traded": len(accepted),
        "n_skipped": int(portfolio["n_skipped"]),
        "hit": float((net > 0).mean()),
        "mean": float(np.mean(net)),
        "median": float(np.median(net)),
        "traded_mean": float(np.mean(traded_net)),
        "gross_mean": float(trades["gross"].mean()),
        "multiple": float(eq.iloc[-1]) if len(eq) else float("nan"),
        "cagr": float(eq.iloc[-1] ** (1.0 / years) - 1.0)
        if years > 0 and len(eq)
        else float("nan"),
        "maxdd": float((eq / eq.cummax() - 1.0).min()) if len(eq) else float("nan"),
        "tax": float(portfolio.get("tax", 0.0)),
        "placebo_p": float(placebo_p),
    }


def shift_events(
    events: pd.DataFrame,
    bars_by_symbol: dict[str, pd.DataFrame | TickerBars],
    rng: np.random.Generator,
    *,
    min_offset: int = 10,
    max_offset: int = 120,
) -> pd.DataFrame:
    """Placebo events: move each event to a random non-event session, same time."""
    prepared = {sym: _as_ticker_bars(bars) for sym, bars in bars_by_symbol.items()}
    out = events.copy()
    for i, row in out.iterrows():
        tb = prepared.get(str(row["ticker"]))
        if tb is None or len(tb.sessions) <= 2 * min_offset:
            out.at[i, "event_time"] = pd.NaT
            continue
        event = pd.Timestamp(row["event_time"])
        sessions = tb.sessions
        actual = int(sessions.searchsorted(pd.Timestamp(event).normalize()))
        lo = max(0, actual - max_offset)
        hi = min(len(sessions) - 1, actual + max_offset)
        candidates = [j for j in range(lo, hi + 1) if abs(j - actual) >= min_offset]
        if not candidates:
            out.at[i, "event_time"] = pd.NaT
            continue
        target = sessions[int(rng.choice(candidates))]
        out.at[i, "event_time"] = pd.Timestamp(target) + (event - event.normalize())
    return out.dropna(subset=["event_time"]).reset_index(drop=True)


def placebo_means(
    bars_by_symbol: dict[str, pd.DataFrame | TickerBars],
    events: pd.DataFrame,
    *,
    signal: str,
    mode: str,
    threshold: float,
    exit_kind: str,
    cost_bps: float,
    borrow_bps: float,
    n: int = 200,
    seed: int = 11,
) -> np.ndarray:
    """Mean-net distribution from date-shifted placebo events."""
    rng = np.random.default_rng(seed)
    out = np.full(n, np.nan)
    prepared = {sym: _as_ticker_bars(bars) for sym, bars in bars_by_symbol.items()}
    for draw in range(n):
        shifted = shift_events(events, prepared, rng)
        tr = trades_for(
            prepared,
            shifted,
            signal=signal,
            mode=mode,
            threshold=threshold,
            exit_kind=exit_kind,
            cost_bps=cost_bps,
            borrow_bps=borrow_bps,
        )
        if not tr.empty:
            out[draw] = float(tr["net"].mean())
    return out


def placebo_p(observed: float, distribution: np.ndarray) -> float:
    good = distribution[np.isfinite(distribution)]
    if not len(good) or not np.isfinite(observed):
        return float("nan")
    return float((1.0 + np.sum(good >= observed)) / (1.0 + len(good)))


def year_folds(trades: pd.DataFrame) -> dict[int, dict]:
    """Per-entry-year trade counts and mean net."""
    if trades.empty:
        return {}
    out: dict[int, dict] = {}
    for year, g in trades.groupby(trades["entry_session"].dt.year):
        out[int(year)] = {
            "n": len(g),
            "mean": float(g["net"].mean()),
            "hit": float((g["net"] > 0).mean()),
            "gross_mean": float(g["gross"].mean()),
        }
    return out


def year_portfolio_returns(daily: pd.Series) -> dict[int, float]:
    """Calendar-year compounded pre-tax return of a daily portfolio path."""
    if daily.empty:
        return {}
    out: dict[int, float] = {}
    for year, g in daily.groupby(daily.index.year):
        out[int(year)] = float(np.prod(1.0 + g.to_numpy(dtype=float)) - 1.0)
    return out


def ticker_folds(trades: pd.DataFrame) -> dict[str, dict]:
    if trades.empty:
        return {}
    out: dict[str, dict] = {}
    for ticker, g in trades.groupby("ticker"):
        out[str(ticker)] = {
            "n": len(g),
            "mean": float(g["net"].mean()),
            "hit": float((g["net"] > 0).mean()),
        }
    return out


def max_concurrent_uncapped(trades: pd.DataFrame) -> int:
    if trades.empty:
        return 0
    events = []
    for row in trades.itertuples(index=False):
        events.append((pd.Timestamp(row.entry_time), 1))
        events.append((pd.Timestamp(row.exit_fill_time), -1))
    events.sort()
    cur = best = 0
    for _, delta in events:
        cur += delta
        best = max(best, cur)
    return int(best)


def benchmark(
    symbol: str,
    interval: str,
    range_: str,
    sessions: pd.DatetimeIndex,
) -> dict:
    """QQQ gross/after-tax over the exact intraday sample window."""
    from bot.backtest import fr_rate

    out: dict = {}
    try:
        bars = load_bars(symbol, interval, range_)
    except Exception:
        return out
    if bars.empty:
        return out
    gross = float(bars.iloc[-1]["close"] / bars.iloc[0]["open"] - 1.0)
    year = int(pd.Timestamp(bars.iloc[-1]["ts"]).year)
    out["gross"] = gross
    out["after_tax"] = 1.0 + gross * (1.0 - fr_rate(year)) if gross > 0 else 1.0 + gross
    out["range"] = (
        f"{bars.iloc[0]['session'].date()}..{bars.iloc[-1]['session'].date()}"
    )
    return out


def bil_benchmark(sessions: pd.DatetimeIndex) -> dict:
    """BIL gross/after-tax over ``sessions`` from the daily cache."""
    from bot.backtest import fr_rate

    path = ROOT / "cache" / "bars" / "BIL.csv"
    if not path.exists():
        return {}
    b = pd.read_csv(path, parse_dates=["date"]).sort_values("date")
    b = b[(b["date"] >= sessions[0]) & (b["date"] <= sessions[-1])]
    if len(b) < 2:
        return {}
    col = "adjclose" if "adjclose" in b.columns else "close"
    gross = float(b[col].iloc[-1] / b[col].iloc[0] - 1.0)
    year = int(pd.Timestamp(b["date"].iloc[-1]).year)
    return {
        "gross": gross,
        "after_tax": 1.0 + gross * (1.0 - fr_rate(year)) if gross > 0 else 1.0 + gross,
        "range": f"{b['date'].iloc[0].date()}..{b['date'].iloc[-1].date()}",
    }


def cost_model_name(cost_bps: float) -> str:
    return "fr_cto_5bps" if float(cost_bps) <= 5.0 else "fr_cto_10bps"
