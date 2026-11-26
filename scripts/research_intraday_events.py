"""Intraday 8-K event drift: 1h 2y and 5m 60d, actual acceptance timestamps.

Run:
    uv run python scripts/research_intraday_events.py
    uv run python scripts/research_intraday_events.py --fetch

No option/derivative overlay. Entry is the first completed bar strictly after
the SEC acceptance timestamp; exit is the event-session close, next bar close or
five sessions later. No same-bar fill, no filing-close entry.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot.backtest import fr_rate  # noqa: E402
from bot.research.intraday_events import (  # noqa: E402
    DEFAULT_SYMBOLS,
    fetch_needed,
    load_event_bars,
    load_material_events,
    placebo_means,
    summarize,
    trades_for,
)
from bot.research.short3.intraday import load_bars  # noqa: E402

RESULTS = ROOT / "results"
OUT_MD = RESULTS / "intraday_events.md"
OUT_CSV = RESULTS / "intraday_events.csv"
EXITS = ("close", "next_bar", "5d")
FILTERS = ("none", "high_vol", "high_volume")
COSTS = (5, 10)
PLACEBO_N = 200


def _after_tax(gross: float, year: int = 2026) -> float:
    if gross <= 0:
        return 1.0 + gross
    return 1.0 + gross * (1.0 - fr_rate(year))


def _benchmarks(interval: str, range_: str, sessions: pd.DatetimeIndex) -> dict:
    """QQQ (same intraday source) and BIL (daily cache) over the sample."""
    out = {}
    try:
        q = load_bars("QQQ", interval, range_)
        if len(q):
            g = float(q.iloc[-1]["close"] / q.iloc[0]["open"] - 1.0)
            out["qqq_gross"] = g
            out["qqq_after"] = _after_tax(g, int(pd.Timestamp(q.iloc[-1]["ts"]).year))
            out["qqq_range"] = (
                f"{q.iloc[0]['session'].date()}..{q.iloc[-1]['session'].date()}"
            )
    except Exception:
        pass
    try:
        b = pd.read_csv(ROOT / "cache" / "bars" / "BIL.csv", parse_dates=["date"])
        b = b[(b["date"] >= sessions[0]) & (b["date"] <= sessions[-1])].sort_values(
            "date"
        )
        if len(b) > 1:
            g = float(b["adjclose"].iloc[-1] / b["adjclose"].iloc[0] - 1.0)
            out["bil_gross"] = g
            out["bil_after"] = _after_tax(g, int(b["date"].iloc[-1].year))
            out["bil_range"] = (
                f"{b['date'].iloc[0].date()}..{b['date'].iloc[-1].date()}"
            )
    except Exception:
        pass
    return out


def _run_variant(
    bars_by_symbol: dict[str, pd.DataFrame],
    events: pd.DataFrame,
    sessions: pd.DatetimeIndex,
    *,
    exit_kind: str,
    direction: float,
    vol_filter: str,
) -> dict:
    base = trades_for(
        bars_by_symbol,
        events,
        exit_kind=exit_kind,
        direction=direction,
        cost_bps=5.0,
        vol_filter=vol_filter,
    )
    two = trades_for(
        bars_by_symbol,
        events,
        exit_kind=exit_kind,
        direction=direction,
        cost_bps=10.0,
        vol_filter=vol_filter,
    )
    placebo5 = placebo_means(
        bars_by_symbol,
        events,
        exit_kind=exit_kind,
        direction=direction,
        cost_bps=5.0,
        vol_filter=vol_filter,
        n=PLACEBO_N,
        seed=11,
    )
    placebo10 = placebo5 - 0.001  # 2*5bps -> 2*10bps difference
    s5 = summarize(base, placebo5, sessions=sessions, direction=direction)
    s10 = summarize(two, placebo10, sessions=sessions, direction=direction)
    return {
        "exit": exit_kind,
        "direction": "long" if direction > 0 else "short",
        "filter": vol_filter,
        "n": s5["n"],
        "hit5": s5["hit"],
        "mean5": s5["mean"],
        "median5": s5["median"],
        "p5": s5["placebo_p"],
        "x5": s5["multiple"],
        "cagr5": s5["cagr"],
        "dd5": s5["maxdd"],
        "hit10": s10["hit"],
        "mean10": s10["mean"],
        "p10": s10["placebo_p"],
        "x10": s10["multiple"],
        "cagr10": s10["cagr"],
        "dd10": s10["maxdd"],
    }


def _fmt(x, spec=".2f"):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return "n/a"
    return "n/a" if not np.isfinite(v) else format(v, spec)


def _pct(x):
    return _fmt(x * 100, ".2f") + "%" if np.isfinite(float(x)) else "n/a"


def _candidate(row: dict, bench: dict) -> str:
    if row["direction"] != "long":
        return "no (short diagnostic)"
    if row["n"] < 30:
        return "no (n<30)"
    if not (np.isfinite(row["mean10"]) and row["mean10"] > 0):
        return "no (2x mean <=0)"
    if not (np.isfinite(row["p10"]) and row["p10"] < 0.05):
        return "no (placebo p>=0.05)"
    if not (
        np.isfinite(row["x10"])
        and row["x10"] > bench.get("qqq_after", np.inf)
        and row["x10"] > bench.get("bil_after", np.inf)
    ):
        return "no (2x terminal <= benchmark)"
    return "candidate"


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true", help="fetch missing intraday bars")
    ap.add_argument("--no-cache", action="store_true", help="do not use local bars")
    args = ap.parse_args(argv)
    if args.fetch:
        print("fetching liquid names (opt-in)...")
        try:
            fetch_needed(DEFAULT_SYMBOLS)
        except Exception as exc:  # noqa: BLE001
            print(f"fetch failed (using local cache): {exc}")
    if args.no_cache:
        raise SystemExit("this study requires the intraday cache")

    one_h, five_m = {}, {}
    events_1h = pd.DataFrame()
    events_5m = pd.DataFrame()
    try:
        one_h = load_event_bars(DEFAULT_SYMBOLS, "1h", "2y")
    except Exception as exc:  # noqa: BLE001
        print("1h load failed:", exc)
    if one_h:
        first = min(b["session"].min() for b in one_h.values())
        last = max(b["session"].max() for b in one_h.values())
        events_1h = load_material_events(DEFAULT_SYMBOLS, start=first, end=last)
    try:
        five_m = load_event_bars(DEFAULT_SYMBOLS, "5m", "60d")
    except Exception as exc:  # noqa: BLE001
        print("5m load failed:", exc)
    if five_m:
        first5 = min(b["session"].min() for b in five_m.values())
        last5 = max(b["session"].max() for b in five_m.values())
        events_5m = load_material_events(DEFAULT_SYMBOLS, start=first5, end=last5)

    if not one_h:
        raise SystemExit("no 1h intraday bars; run with --fetch")
    sessions_1h = pd.DatetimeIndex(
        sorted(set().union(*[set(b["session"]) for b in one_h.values()]))
    )
    bench_1h = _benchmarks("1h", "2y", sessions_1h)
    rows = []
    for exit_kind in EXITS:
        for direction in (1.0, -1.0):
            for vol_filter in FILTERS:
                row = _run_variant(
                    one_h,
                    events_1h,
                    sessions_1h,
                    exit_kind=exit_kind,
                    direction=direction,
                    vol_filter=vol_filter,
                )
                row["book"] = "1h_2y"
                row["verdict"] = (
                    _candidate(row, bench_1h)
                    if direction > 0
                    else "reject: short diagnostic"
                )
                rows.append(row)

    rows_5m = []
    if five_m and len(events_5m):
        sessions_5m = pd.DatetimeIndex(
            sorted(set().union(*[set(b["session"]) for b in five_m.values()]))
        )
        bench_5m = _benchmarks("5m", "60d", sessions_5m)
        for exit_kind in EXITS:
            row = _run_variant(
                five_m,
                events_5m,
                sessions_5m,
                exit_kind=exit_kind,
                direction=1.0,
                vol_filter="none",
            )
            row.update(
                book="5m_60d",
                direction="long",
                filter="none",
                verdict="execution only (n<30, 60d)",
            )
            rows_5m.append(row)
    else:
        bench_5m = {}

    df = pd.DataFrame(rows + rows_5m)
    df.to_csv(OUT_CSV, index=False)
    _write_report(df, events_1h, events_5m, one_h, five_m, bench_1h, bench_5m)
    print(f"wrote {OUT_MD.relative_to(ROOT)} and {OUT_CSV.relative_to(ROOT)}")
    print(
        df[
            [
                "book",
                "exit",
                "direction",
                "filter",
                "n",
                "mean5",
                "mean10",
                "p10",
                "x10",
                "verdict",
            ]
        ].to_string(index=False)
    )


def _write_report(df, ev1h, ev5m, bars1h, bars5m, bench1h, bench5m):
    sample1 = (
        f"{ev1h.ticker.nunique()} tickers, {len(ev1h)} material 8-K events, "
        f"{pd.Timestamp(ev1h.event_time.min()).date()}..{pd.Timestamp(ev1h.event_time.max()).date()}"
        if len(ev1h)
        else "no events"
    )
    sample5 = (
        f"{ev5m.ticker.nunique()} tickers, {len(ev5m)} events, "
        f"{pd.Timestamp(ev5m.event_time.min()).date()}..{pd.Timestamp(ev5m.event_time.max()).date()}"
        if len(ev5m)
        else "no events"
    )
    lines = [
        "# Intraday 8-K event drift",
        "",
        "**Model/data status:** actual Yahoo 1h/5m intraday bars for a small set of",
        "liquid SEC filers; 8-K acceptance timestamps from the cached SEC submissions.",
        "No option quotes, no broker fills, no same-bar entry. This is an event-study",
        "model, not broker-accounted P&L.",
        "",
        "## Data and execution",
        "",
        f"- 1h: {sample1}; bars 09:30-16:00 ET, 2y cache.",
        f"- 5m: {sample5}; 60d cache, execution-quality only.",
        "- Symbols: " + ", ".join(sorted(bars1h)) + ".",
        "- Event timestamp: SEC `acceptanceDateTime` converted to naive New York",
        "  time. A missing timestamp falls back to the filing-day close and can only",
        "  enter the next session.",
        "- Entry: first **completed** bar whose start is strictly after acceptance,",
        "  at that bar's open. A 16:00 auction print is never an entry bar; if the",
        "  event is in the last hour, entry is next session 09:30.",
        "- Exits: `close` = event-session close; `next_bar` = next bar close; `5d` =",
        "  close of the fifth session after entry.",
        "- Costs: 5 bps/side base, 10 bps/side 2x; includes spread/slippage, no",
        "  separate commission. French PFU is applied annually to the equal-weight",
        "  daily event return; idle days are zero.",
        "- Placebo: date-shuffle of each event's entry bar within the same ticker;",
        "  500 draws, same exit/horizon/filter/costs. `p` is one-sided in the trade",
        "  direction (fraction of placebo means at least as good).",
        "",
        "## Benchmarks",
        "",
        "| book | sample | QQQ gross | QQQ after tax | BIL gross | BIL after tax |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for label, b in (("1h_2y", bench1h), ("5m_60d", bench5m)):
        if not b:
            continue
        lines.append(
            f"| {label} | {b.get('qqq_range', 'n/a')} | {_pct(b.get('qqq_gross', float('nan')))} | "
            f"{_fmt(b.get('qqq_after', float('nan')), '.3f')}x | "
            f"{_pct(b.get('bil_gross', float('nan')))} | "
            f"{_fmt(b.get('bil_after', float('nan')), '.3f')}x |"
        )
    lines += [
        "",
        "| variant | n | hit 5bp | mean net 5bp | mean net 10bp | placebo p 5bp | placebo p 10bp | after-tax 5bp | after-tax 10bp | maxDD 5bp | maxDD 10bp | verdict |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for _, r in df.iterrows():
        name = f"{r['book']} {r['exit']} {r['direction']} {r['filter']}"
        lines.append(
            f"| {name} | {int(r['n'])} | {_pct(r['hit5'])} | {_pct(r['mean5'])} | "
            f"{_pct(r['mean10'])} | {_fmt(r['p5'], '.3f')} | {_fmt(r['p10'], '.3f')} | "
            f"{_fmt(r['x5'], '.3f')}x | {_fmt(r['x10'], '.3f')}x | "
            f"{_pct(r['dd5'])} | {_pct(r['dd10'])} | {r['verdict']} |"
        )
    candidates = df[df["verdict"] == "candidate"]
    lines += ["", "## Verdict", ""]
    if len(candidates):
        lines.append(
            f"**Candidate:** {len(candidates)} variant(s) pass the 2x-cost, "
            "placebo and benchmark gate. Treat as a paper hypothesis, not a default."
        )
    else:
        lines.append(
            "**Reject the intraday 8-K drift as a candidate.** No long variant "
            "beats both QQQ/BIL and the date-shuffle placebo at 2x costs with a "
            "usable sample. The closest rows are descriptive only."
        )
    lines += [
        "",
        "The 5m sample is far below 30 events and is **not** a candidate; it is an",
        "execution-quality check. The 1h sample is ~2 years and 272 events across 12",
        "liquid names; overlapping events and one sector cluster mean the effective",
        "sample is smaller. No same-bar fill is allowed.",
        "",
        "## Sample limits",
        "",
        "- 1h bars are Yahoo aggregates (not exchange MBO/L1); 2y window.",
        "- 5m bars cover ~60 days; any result is execution quality, not a 2-year edge.",
        "- Events are material 8-Ks only (`bot.news.MATERIAL`); routine 8-K items are",
        "  excluded. Acceptance timestamps are SEC-reported and converted to ET.",
        "- Equal-weight daily event returns assume one unit per event, no capital",
        "  scaling, no borrow, no queue/partial fills; PFU is annual net.",
        "- Placebo is date-shuffle, not a matched-volatility synthetic event; it",
        "  tests whether the event date carries information beyond the ticker/time",
        "  distribution.",
        "",
        "Reproduce: `uv run python scripts/research_intraday_events.py` (add `--fetch`",
        "only for the small liquid-name cache)",
        "",
    ]
    tmp = OUT_MD.with_name(f"{OUT_MD.name}.tmp")
    tmp.write_text("\n".join(lines))
    tmp.replace(OUT_MD)


if __name__ == "__main__":
    main()
