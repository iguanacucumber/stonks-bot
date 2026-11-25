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
from datetime import datetime
from pathlib import Path

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
    """What the book actually holds at the close: post-trade, post-drift."""
    w = res.weights.loc[date].drop(labels=["CASH"], errors="ignore")
    return {t: float(x) for t, x in w.items() if x > 1e-9}


def replay(mkt, res, cfg, n: int):
    dates = list(res.equity.index)
    rows = []
    for i in range(max(1, n), 0, -1):
        # signal at close d1, fill at d2 close, marked at d3 close
        d1, d2, d3 = dates[-i - 2], dates[-i - 1], dates[-i]
        w = current_weights(res, d2)
        paper = sum(
            x * (mkt.px[t].loc[d3] / mkt.px[t].loc[d2] - 1.0)
            for t, x in w.items()
            if t in mkt.px.columns and pd.notna(mkt.px[t].loc[d2])
        )
        bt = res.equity.loc[d3] / res.equity.loc[d2] - 1.0
        rows.append(
            {
                "signal": str(d1.date()),
                "fill": str(d2.date()),
                "mark": str(d3.date()),
                "gross": sum(w.values()),
                "risk_scale": risk_scale(mkt, d2, cfg),
                "paper_return": paper,
                "backtest_return": bt,
                "diff_bps": (paper - bt) * 1e4,
            }
        )
    df = pd.DataFrame(rows)
    df.to_csv(REPLAY, index=False)
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
        df = replay(mkt, res, cfg, args.replay)
        print(df.round(4).to_string(index=False))
        a = df.diff_bps.abs()
        print(
            f"\n|tracking difference| median {a.median():.1f} bps, "
            f"p95 {a.quantile(0.95):.1f} bps, max {a.max():.1f} bps "
            f"across {len(df)} sessions"
        )
        print(f"wrote {REPLAY}")
        return

    w = current_weights(res, d)
    scale = risk_scale(mkt, d, cfg)
    prices = data.latest_prices(list(w) + ["SPY"])
    raw = panel.pivot(index="date", columns="ticker", values="close")
    px, prev = {}, {}
    for t in w:
        live = prices.get(t)
        p0 = raw[t].loc[d] if t in raw.columns else float("nan")
        px[t] = float(live) if live else float(p0)
        prev[t] = float(p0)
    move = sum(
        x * (px[t] / prev[t] - 1.0)
        for t, x in w.items()
        if prev.get(t) and pd.notna(prev[t])
    )
    now = datetime.now()
    quote_date = pd.Timestamp(now.date())
    daily = {
        "time": str(now),
        "market": args.market,
        "signal_date": str(d.date()),
        "quote_date": str(quote_date.date()),
        "gross": sum(w.values()),
        "risk_scale": scale,
        "day_move": move,
        "equity": args.capital * (1.0 + move),
        "targets": json.dumps({k: round(v, 6) for k, v in w.items()}),
        "prices": json.dumps({k: round(v, 4) for k, v in px.items()}),
    }
    pd.DataFrame([daily]).to_csv(
        DAILY, mode="a", header=not DAILY.exists(), index=False
    )
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
    print(f"paper {args.market} signal {d.date()} quote {quote_date.date()}")
    print(
        f"  gross {sum(w.values()):.0%}  risk scale {scale:.0%}  day move {move:+.2%}"
    )
    for t, x in sorted(w.items(), key=lambda kv: -kv[1]):
        print(f"  {t:6s} {x:6.2%}  {px[t]:9.2f}  vs close {px[t] / prev[t] - 1:+.2%}")
    print(f"appended {DAILY} and {LEDGER}")


if __name__ == "__main__":
    main()
