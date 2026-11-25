"""Compare the live paper rows with the shadow backtest of the same book.

Reads results/paper_daily.csv, compounds the recorded day moves, and prints the
same days from the TECH backtest. With fewer than five rows the sample is too
small to conclude anything; the point is that the comparison is ready and runs
after every scheduled paper entry.

Run: uv run python scripts/paper_report.py --market TECH
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from bot import backtest, markets, strategy, universe  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DAILY = ROOT / "results" / "paper_daily.csv"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="TECH")
    ap.add_argument("--capital", type=float, default=10000.0)
    ap.add_argument("--min-rows", type=int, default=5)
    args = ap.parse_args()

    if not DAILY.exists():
        print("no paper rows yet")
        return
    live = pd.read_csv(DAILY)
    live = live[live.market == args.market].copy()
    if live.empty:
        print(f"no paper rows for {args.market}")
        return
    live["quote_date"] = pd.to_datetime(live["quote_date"])
    live = live.sort_values("time").drop_duplicates("quote_date", keep="last")
    live = live.set_index("quote_date").sort_index()

    panel, mem = markets.build(args.market)
    if args.market != "US":
        universe.canonical = lambda t: t
    cfg = strategy.Config(
        start="2015-01-01",
        leverage=2.5,
        financing_bps=500.0,
        trend_ma=200,
        trend_scale=0.5,
    )
    mkt = strategy.Market(panel, mem, cfg)
    eq = backtest.run(mkt).equity

    rows = []
    live_val, shadow_val = args.capital, args.capital
    pending = 0
    for d, r in live.iterrows():
        if d > eq.index[-1]:
            pending += 1
            continue
        pos = eq.index.searchsorted(d, side="right") - 1
        if pos < 1:
            continue
        mark, prev = eq.index[pos], eq.index[pos - 1]
        shadow = eq.loc[mark] / eq.loc[prev] - 1.0
        live_val *= 1.0 + r["day_move"]
        shadow_val *= 1.0 + shadow
        gross_bt = (
            float(pd.read_csv(DAILY).iloc[0:0].shape[0]) if False else float("nan")
        )
        rows.append(
            {
                "date": str(d.date()),
                "live": r["day_move"],
                "shadow": shadow,
                "diff_bps": (r["day_move"] - shadow) * 1e4,
                "gross_live": r["gross"],
                "risk": r["risk_scale"],
                "live_equity": live_val,
                "shadow_equity": shadow_val,
            }
        )
    df = pd.DataFrame(rows)
    if df.empty:
        print(f"no comparable rows yet ({pending} waiting for a completed close)")
        return
    print(df.round(4).to_string(index=False))
    print(
        f"\n{len(df)} live rows; live ${df.live_equity.iloc[-1]:,.0f} vs "
        f"shadow ${df.shadow_equity.iloc[-1]:,.0f}; "
        f"mean |diff| {df.diff_bps.abs().mean():.1f} bps"
    )
    if pending:
        print(f"{pending} row(s) waiting for the completed daily bar")
    gate = len(df) >= args.min_rows
    print(
        f"GATE: {'PASS' if gate else 'WAIT'} {len(df)}/{args.min_rows} distinct dates"
    )
    if not gate:
        print("not a track record yet; do not draw conclusions from this table")


if __name__ == "__main__":
    main()
