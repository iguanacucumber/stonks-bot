"""Daily paper logger for the shipped monthly book and the tax book. No orders.

One run appends a row to results/paper_ledger.csv (targets, prices) and to
results/paper_daily.csv (book move vs the prior close, gross, risk state). The
tax book writes its own pair: results/paper_ledger_tax.csv and
results/paper_daily_tax.csv. Marks are pre-tax engine marks; French tax is not
deducted here.

`--replay N` re-runs the last N sessions from panel closes and compares the
paper book's daily return with the backtest's, which is how we check that the
forward logger uses the same fill assumption (signal at close d, hold from
close d+1, marked at close d+2).

Run: uv run python scripts/paper_daily.py --market TECH
     uv run python scripts/paper_daily.py --book tax --market TECH
     uv run python scripts/paper_daily.py --replay 5
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from bot import backtest, data, markets, overlay, strategy, universe  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"
LEDGER = RESULTS / "paper_ledger.csv"
DAILY = RESULTS / "paper_daily.csv"
REPLAY = RESULTS / "paper_replay.csv"
LEDGER_TAX = RESULTS / "paper_ledger_tax.csv"
DAILY_TAX = RESULTS / "paper_daily_tax.csv"
REPLAY_TAX = RESULTS / "paper_replay_tax.csv"

# Monthly is the shipped book and must stay the default. The tax book is the
# core-only shape: no sleeve, always hold SMH, wider leverage band, HIFO lots.
# Paper rows are pre-tax marks, so no French rate is set here.
BOOKS = {
    "monthly": {},
    "tax": {
        "core": "SMH",
        "core_mode": "always",
        "leverage_band": 0.30,
        "tax_lots": "hifo",
    },
}
BOOK_FILES = {
    "monthly": (DAILY, LEDGER, REPLAY),
    "tax": (DAILY_TAX, LEDGER_TAX, REPLAY_TAX),
}


def atomic_csv(df: pd.DataFrame, path: Path) -> None:
    """Replace a whole CSV in one rename so readers never see half a file."""
    tmp = path.with_name(f"{path.stem}.{os.getpid()}.tmp{path.suffix}")
    df.to_csv(tmp, index=False)
    os.replace(tmp, path)


def book_config(book: str) -> dict:
    """Config overrides for a book; no data access, safe for tests."""
    return dict(BOOKS[book])


def book_paths(book: str) -> tuple[Path, Path, Path]:
    """(daily, ledger, replay) files for a book."""
    return BOOK_FILES[book]


def replay_file(book: str, market: str) -> Path:
    """Keep a separate replay trace per market; TECH keeps the historical name."""
    path = book_paths(book)[2]
    if market == "TECH":
        return path
    return path.with_name(f"{path.stem}_{market.lower()}{path.suffix}")


def ledger_file(book: str, market: str) -> Path:
    """Keep per-market ledgers so target rows cannot be mixed up."""
    path = book_paths(book)[1]
    if market == "TECH":
        return path
    return path.with_name(f"{path.stem}_{market.lower()}{path.suffix}")


def is_close_valid(
    quote_date: pd.Timestamp, session_date: pd.Timestamp, et: datetime
) -> bool:
    """Only a row dated on an actual completed session may count forward."""
    return et.hour >= 16 and quote_date == session_date


def refresh(market: str) -> None:
    """Advance cached bars one session; history is never refetched."""
    if market == "US":
        # US has no static UNIVERSES entry: advance the membership panel. Only
        # symbols whose last cached bar is stale are requested, and dead tickers
        # back off for 7 days.
        mem = universe.load_membership()
        data.update_panel(mem, workers=12)
        return
    spec = markets.MARKETS[market]
    syms = markets.UNIVERSES[market] + [spec["bench"]] + list(markets.CORE_SYMBOLS)
    if spec["fx"] != "USD":
        syms.append(markets.FX[spec["fx"]][0])
    data.update_symbols(sorted(set(syms)), workers=8, gate=spec["bench"])


def load(
    market: str,
    leverage: float,
    trend_ma: int,
    trend_scale: float,
    book: str = "monthly",
    core_close_fill: bool = False,
):
    old_canonical = universe.canonical
    try:
        if market != "US":
            universe.canonical = lambda t: t
        panel, mem = markets.build(market, core_close_fill=core_close_fill)
        cfg = strategy.Config(
            start="2015-01-01",
            rebalance_mode="calendar",
            exec_price="open",
            leverage=leverage,
            financing_bps=500.0,
            trend_ma=trend_ma,
            trend_scale=trend_scale,
            **book_config(book),
        )
        mkt = strategy.Market(panel, mem, cfg)
    finally:
        universe.canonical = old_canonical
    res = backtest.run(mkt)
    return panel, mkt, res, cfg


def risk_scale(mkt, date, cfg) -> float:
    if not cfg.trend_ma or cfg.core not in mkt.px.columns:
        return 1.0
    px = mkt.px[cfg.core]
    ma = px.rolling(cfg.trend_ma, min_periods=cfg.trend_ma).mean().loc[date]
    return cfg.trend_scale if pd.notna(ma) and px.loc[date] < ma else 1.0


def current_weights(res, date) -> dict[str, float]:
    """Holdings at a date; accepts a Result or a weights DataFrame."""
    frame = res.weights if hasattr(res, "weights") else res
    w = frame.loc[date].drop(labels=["CASH"], errors="ignore")
    return {t: float(x) for t, x in w.items() if x > 1e-9}


def replay(mkt, res, cfg, n: int, panel, path: Path = REPLAY):
    """Daily P&L split at the open: prior book overnight, post-fill book intraday."""
    dates = list(res.equity.index)
    now_et = datetime.now(timezone.utc).astimezone(ZoneInfo("America/New_York"))
    if len(dates) > n + 2 and dates[-1].date() == now_et.date() and now_et.hour < 16:
        dates = dates[:-1]  # today's bar is still forming
    raw_close = panel.pivot(index="date", columns="ticker", values="close")
    raw_open = (
        panel.pivot(index="date", columns="ticker", values="open")
        if "open" in panel.columns
        else raw_close
    )
    wopen = res.weights_open if res.weights_open is not None else res.weights
    rows = []
    for i in range(max(1, n), 0, -1):
        dprev, dm = dates[-i - 1], dates[-i]
        old = current_weights(res, dprev)
        new = current_weights(wopen, dm)
        overnight = 0.0
        for t, x in old.items():
            if t not in raw_close.columns:
                continue
            cp = raw_close[t].loc[dprev]
            op = raw_open[t].loc[dm] if t in raw_open.columns else float("nan")
            if pd.isna(op) or op <= 0:
                op = cp
            if pd.notna(cp) and cp > 0:
                overnight += x * (op / cp - 1.0)
        intraday = 0.0
        for t, x in new.items():
            if t not in raw_open.columns:
                continue
            op = raw_open[t].loc[dm]
            cp = raw_close[t].loc[dm] if t in raw_close.columns else float("nan")
            if pd.isna(op) or op <= 0:
                op = cp
            if pd.notna(op) and op > 0 and pd.notna(cp):
                intraday += x * (cp / op - 1.0)
        simple = 0.0
        for t, x in old.items():
            if t not in raw_close.columns:
                continue
            cp0 = raw_close[t].loc[dprev]
            cp1 = raw_close[t].loc[dm]
            if pd.notna(cp0) and pd.notna(cp1) and cp0 > 0:
                simple += x * (cp1 / cp0 - 1.0)
        total_new = sum(new.values())
        dirty = (
            total_new < 0.1
            or total_new > cfg.leverage * 1.2
            or (len(old) > 0 and len(new) < 0.6 * len(old))
        )
        paper = simple if dirty else (1.0 + overnight) * (1.0 + intraday) - 1.0
        cost_bps = 0.0
        if not dirty and res.eq_open is not None and res.eq_open_pre is not None:
            cost_f = float(res.eq_open.loc[dm] / res.eq_open_pre.loc[dm] - 1.0)
            cost_bps = cost_f * 1e4
            paper = (1.0 + paper) * (1.0 + cost_f) - 1.0
        bt = res.equity.loc[dm] / res.equity.loc[dprev] - 1.0
        rows.append(
            {
                "signal": str(dprev.date()),
                "fill": str(dm.date()),
                "mark": str(dm.date()),
                "gross": sum(new.values()),
                "risk_scale": risk_scale(mkt, dm, cfg),
                "paper_return": paper,
                "backtest_return": bt,
                "diff_bps": (paper - bt) * 1e4,
            }
        )
    df = pd.DataFrame(rows)
    df.to_csv(path, index=False)
    if len(df):
        med = float(df["diff_bps"].abs().median())
        mx = float(df["diff_bps"].abs().max())
        print(
            f"replay tracking: median {med:.2f} bps, max {mx:.2f} bps over {len(df)} sessions"
        )
        if med > 2.0 or mx > 50.0:
            print(
                "WARNING: tracking exceeds tolerance; forward comparison not trustworthy"
            )
    return df


def reconcile(
    market,
    capital,
    leverage,
    trend_ma,
    trend_scale,
    book="monthly",
    core_close_fill=False,
):
    """Recompute the last close-valid row from final bars, keeping its live prices.

    This is for a row whose log-time panel was missing closes (Yahoo publishes the
    final daily bar after the 16:00 ET close). The live prices recorded in the row
    are preserved; the weights and the shadow are rebuilt from the completed panel.
    """
    daily_path = book_paths(book)[0]
    if not daily_path.exists():
        print(f"no {daily_path.name} rows to reconcile")
        return
    panel, mkt, res, cfg = load(
        market, leverage, trend_ma, trend_scale, book, core_close_fill
    )
    df = pd.read_csv(daily_path)
    for c in ("data_complete", "reconciled"):
        if c not in df.columns:
            df[c] = False
    for c in ("shadow_move", "shadow_gross"):
        if c not in df.columns:
            df[c] = float("nan")
    m = df[(df.market == market)].copy()
    m["close_valid"] = m["close_valid"].astype(str).str.lower().isin(["true", "1"])
    m = m[m["close_valid"]]
    if m.empty:
        print("no close-valid rows to reconcile")
        return
    idx = m.index[-1]
    dm = pd.Timestamp(df.loc[idx, "quote_date"])
    dates = list(res.equity.index)
    if dm not in dates:
        print(f"{dm.date()} not in panel; nothing to reconcile")
        return
    j = dates.index(dm)
    dprev = dates[j - 1]
    old = current_weights(res, dprev)
    wopen = res.weights_open if res.weights_open is not None else res.weights
    new = current_weights(wopen, dm)
    raw_close = panel.pivot(index="date", columns="ticker", values="close")
    raw_open = panel.pivot(index="date", columns="ticker", values="open")
    rec = json.loads(df.loc[idx, "prices"])
    overnight = 0.0
    for t, x in old.items():
        cp = raw_close[t].loc[dprev] if t in raw_close.columns else float("nan")
        op = raw_open[t].loc[dm] if t in raw_open.columns else float("nan")
        if pd.isna(op) or op <= 0:
            op = cp
        if pd.notna(cp) and cp > 0 and pd.notna(op):
            overnight += x * (op / cp - 1.0)
    intraday = 0.0
    for t, x in new.items():
        op = raw_open[t].loc[dm] if t in raw_open.columns else float("nan")
        cp = raw_close[t].loc[dm] if t in raw_close.columns else float("nan")
        if pd.isna(op) or op <= 0:
            op = cp
        p1 = float(rec.get(t, cp))
        if pd.notna(op) and op > 0 and pd.notna(p1):
            intraday += x * (p1 / op - 1.0)
    move = (1.0 + overnight) * (1.0 + intraday) - 1.0
    rebalanced = set(new) != set(old) or abs(sum(new.values()) - sum(old.values())) > (
        0.05 * max(1.0, sum(old.values()))
    )
    if (
        rebalanced
        and res.eq_open is not None
        and res.eq_open_pre is not None
        and dm in res.eq_open.index
        and dm in res.eq_open_pre.index
    ):
        cost_f = float(res.eq_open.loc[dm] / res.eq_open_pre.loc[dm] - 1.0)
        move = (1.0 + move) * (1.0 + cost_f) - 1.0
    held = set(old) | set(new)
    missing = [
        t for t in held if t not in raw_close.columns or pd.isna(raw_close[t].loc[dm])
    ]
    missing_prev = [
        t for t in old if t not in raw_close.columns or pd.isna(raw_close[t].loc[dprev])
    ]
    if missing or missing_prev:
        print(
            f"cannot reconcile {dm.date()}: missing closes {missing}, "
            f"missing prior closes {missing_prev}"
        )
        return
    shadow = float(res.equity.loc[dm] / res.equity.loc[dprev] - 1.0)
    old_move = float(df.loc[idx, "day_move"])
    if "original_move" not in df.columns:
        df["original_move"] = float("nan")
    df.loc[idx, "original_move"] = old_move
    old_shadow = (
        float(df.loc[idx, "shadow_move"])
        if pd.notna(df.loc[idx, "shadow_move"])
        else float("nan")
    )
    df.loc[idx, "day_move"] = move
    df.loc[idx, "equity"] = capital * (1.0 + move)
    df.loc[idx, "gross"] = float(sum(new.values()))
    df.loc[idx, "targets"] = json.dumps({k: round(v, 6) for k, v in new.items()})
    df.loc[idx, "data_complete"] = True
    df.loc[idx, "reconciled"] = True
    df.loc[idx, "shadow_move"] = shadow
    df.loc[idx, "shadow_gross"] = float(sum(new.values()))
    atomic_csv(df, daily_path)
    print(
        f"reconciled {dm.date()}: live {old_move:+.2%} -> {move:+.2%}, "
        f"shadow {old_shadow:+.2%} -> {shadow:+.2%}, "
        f"diff {(move - shadow) * 1e4:+.1f} bps"
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--book", choices=tuple(BOOKS), default="monthly")
    ap.add_argument("--market", default="TECH")
    ap.add_argument("--leverage", type=float, default=2.5)
    ap.add_argument("--trend-ma", type=int, default=200)
    ap.add_argument("--trend-scale", type=float, default=0.5)
    ap.add_argument("--capital", type=float, default=10000.0)
    ap.add_argument(
        "--core-close-fill",
        action="store_true",
        help="old synthetic core fill (open == close) for comparison",
    )
    ap.add_argument("--replay", type=int, default=0)
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument(
        "--reconcile",
        action="store_true",
        help="recompute the last close-valid row from final bars (keeps live prices)",
    )
    args = ap.parse_args()
    if args.refresh:
        refresh(args.market)
    daily_path = book_paths(args.book)[0]
    ledger_path = ledger_file(args.book, args.market)
    replay_path = replay_file(args.book, args.market)

    panel, mkt, res, cfg = load(
        args.market,
        args.leverage,
        args.trend_ma,
        args.trend_scale,
        args.book,
        args.core_close_fill,
    )
    d = mkt.dates[-1]

    if args.reconcile:
        reconcile(
            args.market,
            args.capital,
            args.leverage,
            args.trend_ma,
            args.trend_scale,
            args.book,
            args.core_close_fill,
        )
        return

    if args.replay:
        df = replay(mkt, res, cfg, args.replay, panel, replay_path)
        print(df.round(4).to_string(index=False))
        a = df.diff_bps.abs()
        print(
            f"\n|tracking difference| median {a.median():.1f} bps, "
            f"p95 {a.quantile(0.95):.1f} bps, max {a.max():.1f} bps "
            f"across {len(df)} sessions"
        )
        print(f"wrote {replay_path}")
        return

    # the box runs at UTC+4; use UTC/ET explicitly or the quote date rolls over
    now = datetime.now(timezone.utc)
    et = now.astimezone(ZoneInfo("America/New_York"))
    quote_date = pd.Timestamp(et.date())
    # day P&L: prior book overnight, post-open book intraday (next-open fills)
    raw_close = panel.pivot(index="date", columns="ticker", values="close")
    raw_open = (
        panel.pivot(index="date", columns="ticker", values="open")
        if "open" in panel.columns
        else raw_close
    )
    last = mkt.dates[-1]
    # A row is close-valid only when today's own bar is in the panel. On a
    # holiday or an unpublished close, marking yesterday's bar under today's
    # date would invent a track-record day.
    close_valid = is_close_valid(quote_date, last, et)
    if quote_date >= last and len(mkt.dates) > 1:
        dm = last
        dprev = mkt.dates[-2] if quote_date == last else last
    else:
        dm = last
        dprev = last
    old = current_weights(res, dprev)
    wopen = res.weights_open if res.weights_open is not None else res.weights
    w = current_weights(wopen, dm)
    scale = risk_scale(mkt, dm, cfg)
    bench = markets.MARKETS[args.market]["bench"]
    prices = data.latest_prices(sorted(set(w) | set(old)) + [bench])
    overnight = 0.0
    for t, x in old.items():
        if t not in raw_close.columns:
            continue
        cp = raw_close[t].loc[dprev]
        op = raw_open[t].loc[dm] if t in raw_open.columns else float("nan")
        if pd.isna(op) or op <= 0:
            op = cp
        if pd.notna(cp) and cp > 0:
            overnight += x * (op / cp - 1.0)
    intraday = 0.0
    px, prev = {}, {}
    for t, x in w.items():
        op = raw_open[t].loc[dm] if t in raw_open.columns else float("nan")
        cp = raw_close[t].loc[dm] if t in raw_close.columns else float("nan")
        if pd.isna(op) or op <= 0:
            op = cp
        live = prices.get(t)
        p1 = float(live) if live else float(cp)
        px[t] = p1
        cp_prev = raw_close[t].loc[dprev] if t in raw_close.columns else float("nan")
        prev[t] = float(cp_prev) if pd.notna(cp_prev) else float("nan")
        if pd.notna(op) and op > 0 and pd.notna(p1):
            intraday += x * (p1 / op - 1.0)
    total_w = sum(w.values())
    dirty = total_w < 0.1 or total_w > cfg.leverage * 1.2 or len(w) < 0.6 * len(old)
    if dirty:
        w = old
        move = sum(
            x
            * (
                float(prices.get(t) or raw_close[t].loc[dprev])
                / raw_close[t].loc[dprev]
                - 1.0
            )
            for t, x in old.items()
            if t in raw_close.columns
            and pd.notna(raw_close[t].loc[dprev])
            and raw_close[t].loc[dprev] > 0
        )
        px = {
            t: float(prices.get(t) or raw_close[t].loc[dprev])
            for t in w
            if t in raw_close.columns
        }
        prev = {t: float(raw_close[t].loc[dprev]) for t in w if t in raw_close.columns}
    else:
        move = (1.0 + overnight) * (1.0 + intraday) - 1.0
    rebalanced = set(w) != set(old) or abs(sum(w.values()) - sum(old.values())) > (
        0.05 * max(1.0, sum(old.values()))
    )
    if (
        not dirty
        and rebalanced
        and res.eq_open is not None
        and res.eq_open_pre is not None
        and dm in res.eq_open.index
        and dm in res.eq_open_pre.index
    ):
        cost_f = float(res.eq_open.loc[dm] / res.eq_open_pre.loc[dm] - 1.0)
        move = (1.0 + move) * (1.0 + cost_f) - 1.0
    ref = dprev
    daily = {
        "time": str(now),
        "market": args.market,
        "signal_date": str(d.date()),
        "quote_date": str(quote_date.date()),
        "close_valid": close_valid,
        "gross": sum(w.values()),
        "risk_scale": scale,
        "day_move": move,
        "equity": args.capital * (1.0 + move),
        "targets": json.dumps({k: round(v, 6) for k, v in w.items()}),
        "prices": json.dumps({k: round(v, 4) for k, v in px.items()}),
    }
    held = set(old) | set(w)
    missing_close = [
        t for t in held if t not in raw_close.columns or pd.isna(raw_close[t].loc[dm])
    ]
    missing_open = [
        t
        for t in held
        if rebalanced
        and (
            t not in raw_open.columns
            or pd.isna(raw_open[t].loc[dm])
            or raw_open[t].loc[dm] <= 0
        )
    ]
    data_complete = not missing_close and not missing_open
    close_valid = bool(close_valid and data_complete)
    shadow_move = (
        float(res.equity.loc[dm] / res.equity.loc[dprev] - 1.0)
        if dm in res.equity.index and dprev in res.equity.index
        else float("nan")
    )
    daily["close_valid"] = close_valid
    daily["data_complete"] = data_complete
    daily["reconciled"] = False
    daily["shadow_move"] = shadow_move
    daily["shadow_gross"] = float(sum(w.values()))
    if not data_complete:
        print(
            "WARNING: incomplete bar; row flagged close_valid=False "
            f"(missing closes {missing_close}, missing opens {missing_open})"
        )

    if daily_path.exists():
        old = pd.read_csv(daily_path)
        atomic_csv(
            pd.concat([old, pd.DataFrame([daily])], ignore_index=True), daily_path
        )
    else:
        atomic_csv(pd.DataFrame([daily]), daily_path)
    row = {
        "time": str(now),
        "signal_date": str(d.date()),
        "targets": daily["targets"],
        "prices": json.dumps(
            {k: round(v, 4) for k, v in (px | {bench: prices.get(bench, 0.0)}).items()}
        ),
        "stops": json.dumps({t: False for t in w}),
    }
    pd.DataFrame([row]).to_csv(
        ledger_path, mode="a", header=not ledger_path.exists(), index=False
    )
    print(
        f"paper {args.book} {args.market} signal {d.date()} "
        f"quote {quote_date.date()} held-from {ref.date()}"
    )
    print(
        f"  gross {sum(w.values()):.0%}  risk scale {scale:.0%}  day move {move:+.2%}"
    )
    for t, x in sorted(w.items(), key=lambda kv: -kv[1]):
        print(f"  {t:6s} {x:6.2%}  {px[t]:9.2f}  vs close {px[t] / prev[t] - 1:+.2%}")
    print(f"appended {daily_path} and {ledger_path}")


if __name__ == "__main__":
    main()
