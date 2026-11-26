"""Compare the live paper rows with the shadow backtest of the same book.

Reads results/paper_daily.csv for the monthly book, or paper_daily_tax.csv for
the tax book, compounds the recorded day moves, and prints the same days from
the shadow backtest. With fewer than five rows the sample is too small to
conclude anything; the point is that the comparison is ready and runs after
every scheduled paper entry.

Run: uv run python scripts/paper_report.py --market TECH
     uv run python scripts/paper_report.py --book tax --market TECH
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import pandas as pd  # noqa: E402

from paper_daily import book_paths, load  # noqa: E402

BOOKS = ("monthly", "tax")
AUDIT = ROOT / "results" / "paper_audit.csv"
MAX_RETURN_DIFF_BPS = 5.0
MAX_WEIGHT_DIFF_BPS = 1.0


def target_diff(live: dict, shadow: dict) -> dict:
    """Compare the recorded target book with the shadow book at the mark."""
    common = set(live) & set(shadow)
    union = set(live) | set(shadow)
    return {
        "names": f"{len(common)}/{len(union)}",
        "max_w_diff": max(
            (abs(float(live.get(t, 0.0)) - float(shadow.get(t, 0.0))) for t in union),
            default=0.0,
        ),
        "gross_shadow": sum(float(x) for x in shadow.values()),
    }


def verified_mask(df: pd.DataFrame) -> pd.Series:
    """A forward date counts only when returns, weights and fills all match."""
    parts = df["fills"].astype(str).str.split("/", n=1, expand=True)
    full_fills = parts[0] == parts[1]
    return (
        (df["diff_bps"].abs() <= MAX_RETURN_DIFF_BPS)
        & (df["max_w_diff_bps"] <= MAX_WEIGHT_DIFF_BPS)
        & full_fills
    )


def audit_frame(
    rows: list[dict], exposure: list[dict], book: str, market: str
) -> pd.DataFrame:
    """Machine-readable per-date audit: live vs shadow return, gross and fills."""
    left = pd.DataFrame(rows)
    right = pd.DataFrame(exposure)[["date", "max_w_diff_bps", "fills"]]
    df = left.merge(right, on="date", how="left")
    df.insert(0, "book", book)
    df.insert(1, "market", market)
    df["verified"] = verified_mask(df)
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--book", choices=BOOKS, default="monthly")
    ap.add_argument("--market", default="TECH")
    ap.add_argument("--capital", type=float, default=10000.0)
    ap.add_argument("--min-rows", type=int, default=5)
    args = ap.parse_args()

    daily = book_paths(args.book)[0]
    if not daily.exists():
        print(f"no paper rows yet ({daily.name}, book {args.book})")
        print(f"GATE: WAIT 0/{args.min_rows} distinct dates (book {args.book})")
        return
    live = pd.read_csv(daily)
    live = live[live.market == args.market].copy()
    live["quote_date"] = pd.to_datetime(live["quote_date"])
    if "close_valid" in live.columns:
        live["close_valid"] = (
            live["close_valid"].astype(str).str.lower().isin(["true", "1"])
        )
    else:
        live["close_valid"] = False
    skipped = int((~live["close_valid"]).sum())
    live = live[live["close_valid"]]
    incomplete = 0
    if "data_complete" in live.columns:
        dc = live["data_complete"].astype(str).str.lower().isin(["true", "1"])
        rec = (
            live["reconciled"].astype(str).str.lower().isin(["true", "1"])
            if "reconciled" in live.columns
            else pd.Series(False, index=live.index)
        )
        incomplete = int((~dc & ~rec).sum())
        live = live[dc | rec]
    if live.empty:
        print(
            f"no complete close-valid paper rows for {args.market} "
            f"({skipped} intraday, {incomplete} incomplete skipped, "
            f"book {args.book})"
        )
        print(f"GATE: WAIT 0/{args.min_rows} distinct dates (book {args.book})")
        return
    live = live.sort_values("time").drop_duplicates("quote_date", keep="last")
    live = live.set_index("quote_date").sort_index()

    _, _, res, _ = load(args.market, 2.5, 200, 0.5, args.book)
    eq = res.equity
    wopen = res.weights_open if res.weights_open is not None else res.weights

    rows = []
    exposure = []
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
        if "shadow_move" in r.index and pd.notna(r["shadow_move"]):
            shadow = float(r["shadow_move"])
        live_val *= 1.0 + r["day_move"]
        shadow_val *= 1.0 + shadow
        gross_shadow = (
            float(r["shadow_gross"])
            if "shadow_gross" in r.index and pd.notna(r["shadow_gross"])
            else float("nan")
        )
        live_targets = (
            json.loads(r["targets"])
            if "targets" in r.index and isinstance(r["targets"], str)
            else {}
        )
        shadow_targets = (
            {
                t: float(x)
                for t, x in wopen.loc[mark].items()
                if t != "CASH" and pd.notna(x) and float(x) > 1e-9
            }
            if mark in wopen.index
            else {}
        )
        td = target_diff(live_targets, shadow_targets)
        exposure.append(
            {
                "date": str(d.date()),
                "gross_live": r["gross"],
                "gross_shadow": gross_shadow,
                "gross_calc": td["gross_shadow"],
                "max_w_diff_bps": td["max_w_diff"] * 1e4,
                "fills": td["names"],
            }
        )
        rows.append(
            {
                "date": str(d.date()),
                "live": r["day_move"],
                "shadow": shadow,
                "diff_bps": (r["day_move"] - shadow) * 1e4,
                "gross_live": r["gross"],
                "gross_shadow": gross_shadow,
                "risk": r["risk_scale"],
                "live_equity": live_val,
                "shadow_equity": shadow_val,
            }
        )
    df = pd.DataFrame(rows)
    if df.empty:
        print(f"no comparable rows yet ({pending} waiting for a completed close)")
        print(f"GATE: WAIT 0/{args.min_rows} verified dates (book {args.book})")
        return
    print(df.round(4).to_string(index=False))
    if exposure:
        print("\nexposure and target/fill check (live vs shadow):")
        print(pd.DataFrame(exposure).round(4).to_string(index=False))
    audit = audit_frame(rows, exposure, args.book, args.market)
    if AUDIT.exists():
        old = pd.read_csv(AUDIT)
        out = pd.concat([old, audit], ignore_index=True).drop_duplicates(
            ["book", "market", "date"], keep="last"
        )
    else:
        out = audit
    out.sort_values(["book", "market", "date"]).to_csv(AUDIT, index=False)
    verified = int(audit["verified"].sum())
    print(
        f"\n{skipped} intraday row(s) skipped (not close-valid)"
        + (f"; {incomplete} incomplete close(s) skipped" if incomplete else "")
    )
    print(
        f"{len(df)} close-valid live rows; live ${df.live_equity.iloc[-1]:,.0f} vs "
        f"shadow ${df.shadow_equity.iloc[-1]:,.0f}; "
        f"mean |diff| {df.diff_bps.abs().mean():.1f} bps"
    )
    if pending:
        print(f"{pending} row(s) waiting for the completed daily bar")
    gate = verified >= args.min_rows
    print(
        f"GATE: {'PASS' if gate else 'WAIT'} {verified}/{args.min_rows} verified "
        f"dates ({len(df)} distinct, book {args.book}, market {args.market})"
    )
    print(f"audit: {AUDIT.relative_to(ROOT)} ({verified}/{len(df)} rows verified)")
    if not gate:
        print("not a track record yet; do not draw conclusions from this table")


if __name__ == "__main__":
    main()
