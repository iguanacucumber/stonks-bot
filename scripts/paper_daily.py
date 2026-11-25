"""Daily paper logger for the shipped book. No orders, no broker.

One run appends a row to results/paper_ledger.csv (targets, prices) and to
results/paper_daily.csv (book move vs the prior close, gross, risk state).

`--replay N` re-runs the last N sessions from panel closes and compares the
paper book's daily return with the backtest's, which is how we check that the
forward logger uses the same fill assumption (signal at close d, hold from
close d+1, marked at close d+2).

Run: uv run python scripts/paper_daily.py --market TECH
     uv run python scripts/paper_daily.py --replay 5
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from bot import backtest, data, markets, overlay, strategy, universe  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "results" / "paper_ledger.csv"
DAILY = ROOT / "results" / "paper_daily.csv"
REPLAY = ROOT / "results" / "paper_replay.csv"


def refresh(market: str) -> None:
    """Force-refresh the small ETF/stock set so the panel advances daily."""
    spec = markets.MARKETS[market]
    syms = markets.UNIVERSES[market] + [spec["bench"]] + list(markets.CORE_SYMBOLS)
    data.fetch_all(syms, force=True)


def load(market: str, leverage: float, trend_ma: int, trend_scale: float):
    panel, mem = markets.build(market)
    if market != "US":
        universe.canonical = lambda t: t
    cfg = strategy.Config(
        start="2015-01-01",
        rebalance_mode="calendar",
        exec_price="open",
        leverage=leverage,
        financing_bps=500.0,
        trend_ma=trend_ma,
        trend_scale=trend_scale,
    )
    mkt = strategy.Market(panel, mem, cfg)
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


def replay(mkt, res, cfg, n: int, panel):
    """Daily P&L split at the open: prior book overnight, post-fill book intraday."""
    dates = list(res.equity.index)
    now_et = datetime.now(timezone.utc).astimezone(ZoneInfo("America/New_York"))
    if (
        len(dates) > n + 2
        and dates[-1].date() == now_et.date()
        and now_et.hour < 16
    ):
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
    df.to_csv(REPLAY, index=False)
    if len(df):
        med = float(df["diff_bps"].abs().median())
        mx = float(df["diff_bps"].abs().max())
        print(f"replay tracking: median {med:.2f} bps, max {mx:.2f} bps over {len(df)} sessions")
        if med > 2.0 or mx > 50.0:
            print("WARNING: tracking exceeds tolerance; forward comparison not trustworthy")
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="TECH")
    ap.add_argument("--leverage", type=float, default=2.5)
    ap.add_argument("--trend-ma", type=int, default=200)
    ap.add_argument("--trend-scale", type=float, default=0.5)
    ap.add_argument("--capital", type=float, default=10000.0)
    ap.add_argument("--replay", type=int, default=0)
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()

    panel, mkt, res, cfg = load(
        args.market, args.leverage, args.trend_ma, args.trend_scale
    )
    d = mkt.dates[-1]

    if args.replay:
        df = replay(mkt, res, cfg, args.replay, panel)
        print(df.round(4).to_string(index=False))
        a = df.diff_bps.abs()
        print(
            f"\n|tracking difference| median {a.median():.1f} bps, "
            f"p95 {a.quantile(0.95):.1f} bps, max {a.max():.1f} bps "
            f"across {len(df)} sessions"
        )
        print(f"wrote {REPLAY}")
        return

    # the box runs at UTC+4; use UTC/ET explicitly or the quote date rolls over
    now = datetime.now(timezone.utc)
    et = now.astimezone(ZoneInfo("America/New_York"))
    quote_date = pd.Timestamp(et.date())
    close_valid = et.hour >= 16
    # day P&L: prior book overnight, post-open book intraday (next-open fills)
    raw_close = panel.pivot(index="date", columns="ticker", values="close")
    raw_open = (
        panel.pivot(index="date", columns="ticker", values="open")
        if "open" in panel.columns
        else raw_close
    )
    last = mkt.dates[-1]
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
    prices = data.latest_prices(sorted(set(w) | set(old)) + ["SPY"])
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
    if total_w < 0.1 or total_w > cfg.leverage * 1.2 or len(w) < 0.6 * len(old):
        w = old
        move = sum(
            x * (float(prices.get(t) or raw_close[t].loc[dprev]) / raw_close[t].loc[dprev] - 1.0)
            for t, x in old.items()
            if t in raw_close.columns
            and pd.notna(raw_close[t].loc[dprev])
            and raw_close[t].loc[dprev] > 0
        )
        px = {t: float(prices.get(t) or raw_close[t].loc[dprev]) for t in w if t in raw_close.columns}
        prev = {t: float(raw_close[t].loc[dprev]) for t in w if t in raw_close.columns}
    else:
        move = (1.0 + overnight) * (1.0 + intraday) - 1.0
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
    if DAILY.exists():
        old = pd.read_csv(DAILY)
        pd.concat([old, pd.DataFrame([daily])], ignore_index=True).to_csv(
            DAILY, index=False
        )
    else:
        pd.DataFrame([daily]).to_csv(DAILY, index=False)
    row = {
        "time": str(now),
        "signal_date": str(d.date()),
        "targets": daily["targets"],
        "prices": json.dumps(
            {k: round(v, 4) for k, v in (px | {"SPY": prices.get("SPY", 0.0)}).items()}
        ),
        "stops": json.dumps({t: False for t in w}),
    }
    pd.DataFrame([row]).to_csv(
        LEDGER, mode="a", header=not LEDGER.exists(), index=False
    )
    print(f"paper {args.market} signal {d.date()} quote {quote_date.date()} held-from {ref.date()}")
    print(
        f"  gross {sum(w.values()):.0%}  risk scale {scale:.0%}  day move {move:+.2%}"
    )
    for t, x in sorted(w.items(), key=lambda kv: -kv[1]):
        print(f"  {t:6s} {x:6.2%}  {px[t]:9.2f}  vs close {px[t] / prev[t] - 1:+.2%}")
    print(f"appended {DAILY} and {LEDGER}")


if __name__ == "__main__":
    main()
