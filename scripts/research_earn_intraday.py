"""v11 B: intraday earnings 8-K item 2.02 continuation/fade.

Run:
    uv run python scripts/research_earn_intraday.py

Writes results/earn_intraday.md and results/earn_intraday_trials.csv.
Cached bars and cached SEC submissions only; no network, no synthetic rows.
Every variant is registered, including the failing ones.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot.research.earn_intraday import (  # noqa: E402
    BASE_BORROW_BPS,
    BASE_COST_BPS,
    DEFAULT_SYMBOLS,
    DOUBLE_BORROW_BPS,
    DOUBLE_COST_BPS,
    EXITS,
    MODES,
    SIGNALS,
    _bil_returns,
    benchmark,
    bil_benchmark,
    build_ticker_bars,
    cost_model_name,
    load_earnings_events,
    max_concurrent_uncapped,
    placebo_p,
    portfolio_daily,
    shift_events,
    summarize,
    ticker_folds,
    trades_for,
    year_folds,
    year_portfolio_returns,
)
from bot.research.registry import TrialRegistry  # noqa: E402
from bot.research.short3.intraday import load_bars  # noqa: E402

RESULTS = ROOT / "results"
OUT_MD = RESULTS / "earn_intraday.md"
OUT_CSV = RESULTS / "earn_intraday_trials.csv"

THRESHOLDS_BPS = (0.0, 200.0)
COSTS = ((BASE_COST_BPS, BASE_BORROW_BPS), (DOUBLE_COST_BPS, DOUBLE_BORROW_BPS))
BOOKS = (("1h", "2y"), ("5m", "60d"))
PLACEBO_N = 200
MAX_CONCURRENT = 5


def _load_book(
    interval: str, range_: str
) -> tuple[dict, dict, pd.DatetimeIndex, pd.DataFrame]:
    bars: dict[str, pd.DataFrame] = {}
    for sym in DEFAULT_SYMBOLS:
        try:
            b = load_bars(sym, interval, range_)
        except Exception:
            continue
        if len(b):
            bars[sym] = b
    if not bars:
        raise RuntimeError(f"no cached bars for {interval}/{range_}")
    sessions = pd.DatetimeIndex(
        sorted(set().union(*[set(b["session"]) for b in bars.values()]))
    )
    events = load_earnings_events(tuple(bars), start=sessions[0], end=sessions[-1])
    prepared = {sym: build_ticker_bars(b) for sym, b in bars.items()}
    return bars, prepared, sessions, events


def _combos() -> list[dict]:
    out = []
    for signal in SIGNALS:
        for mode in MODES:
            for threshold_bps in THRESHOLDS_BPS:
                for exit_kind in EXITS:
                    out.append(
                        {
                            "signal": signal,
                            "mode": mode,
                            "threshold_bps": float(threshold_bps),
                            "exit_kind": exit_kind,
                        }
                    )
    return out


def _placebo_grid(
    prepared: dict,
    events: pd.DataFrame,
    combos: list[dict],
    *,
    n: int,
    seed: int,
) -> dict[tuple, np.ndarray]:
    """One shared set of date-shifted events for every variant."""
    rng = np.random.default_rng(seed)
    keys = [
        (c["signal"], c["mode"], c["threshold_bps"], c["exit_kind"]) for c in combos
    ]
    out = {k: np.full(n, np.nan) for k in keys}
    for draw in range(n):
        shifted = shift_events(events, prepared, rng)
        if shifted.empty:
            continue
        for c in combos:
            key = (c["signal"], c["mode"], c["threshold_bps"], c["exit_kind"])
            tr = trades_for(
                prepared,
                shifted,
                signal=c["signal"],
                mode=c["mode"],
                threshold=c["threshold_bps"] / 1e4,
                exit_kind=c["exit_kind"],
                cost_bps=BASE_COST_BPS,
                borrow_bps=BASE_BORROW_BPS,
            )
            if not tr.empty:
                out[key][draw] = float(tr["net"].mean())
    return out


def _run_book(
    interval: str,
    range_: str,
    *,
    placebo_n: int,
    seed: int,
) -> dict:
    bars, prepared, sessions, events = _load_book(interval, range_)
    combos = _combos()
    placebo = _placebo_grid(prepared, events, combos, n=placebo_n, seed=seed)
    cash = _bil_returns(sessions)
    rows: list[dict] = []
    for c in combos:
        key = (c["signal"], c["mode"], c["threshold_bps"], c["exit_kind"])
        p5_dist = placebo[key]
        variants: dict[float, dict] = {}
        for cost_bps, borrow_bps in COSTS:
            tr = trades_for(
                prepared,
                events,
                signal=c["signal"],
                mode=c["mode"],
                threshold=c["threshold_bps"] / 1e4,
                exit_kind=c["exit_kind"],
                cost_bps=cost_bps,
                borrow_bps=borrow_bps,
            )
            port = portfolio_daily(
                tr,
                prepared,
                sessions,
                cost_bps=cost_bps,
                borrow_bps=borrow_bps,
                max_concurrent=MAX_CONCURRENT,
                cash_returns=cash,
            )
            idle = portfolio_daily(
                tr,
                prepared,
                sessions,
                cost_bps=cost_bps,
                borrow_bps=borrow_bps,
                max_concurrent=MAX_CONCURRENT,
                cash_returns=None,
            )
            # The 10 bps round trip is exactly 0.001 worse per trade.
            dist = p5_dist if cost_bps == BASE_COST_BPS else p5_dist - 0.001
            p = placebo_p(
                float(tr["net"].mean()) if not tr.empty else float("nan"), dist
            )
            s = summarize(tr, portfolio=port, sessions=sessions, placebo_p=p)
            s_idle = summarize(tr, portfolio=idle, sessions=sessions)
            variants[cost_bps] = {
                "trades": tr,
                "portfolio": port,
                "summary": s,
                "idle_multiple": s_idle["multiple"],
                "full": None,
            }
        if interval == "1h":
            for cost_bps, borrow_bps in COSTS:
                tr = variants[cost_bps]["trades"]
                full = portfolio_daily(
                    tr,
                    prepared,
                    sessions,
                    cost_bps=cost_bps,
                    borrow_bps=borrow_bps,
                    max_concurrent=MAX_CONCURRENT,
                    cash_returns=cash,
                    sizing="equal_active",
                )
                variants[cost_bps]["full"] = summarize(
                    tr, portfolio=full, sessions=sessions
                )
                variants[cost_bps]["full_years"] = year_portfolio_returns(full["daily"])
        s5 = variants[BASE_COST_BPS]["summary"]
        s10 = variants[DOUBLE_COST_BPS]["summary"]
        full5 = variants[BASE_COST_BPS]["full"]
        full10 = variants[DOUBLE_COST_BPS]["full"]
        rows.append(
            {
                **c,
                "interval": interval,
                "range": range_,
                "n": s5["n"],
                "n_traded_5": s5["n_traded"],
                "n_skipped_5": s5["n_skipped"],
                "n_traded_10": s10["n_traded"],
                "n_skipped_10": s10["n_skipped"],
                "hit5": s5["hit"],
                "mean5": s5["mean"],
                "median5": s5["median"],
                "gross_mean5": s5["gross_mean"],
                "x5": s5["multiple"],
                "cagr5": s5["cagr"],
                "dd5": s5["maxdd"],
                "p5": s5["placebo_p"],
                "x5_idle": variants[BASE_COST_BPS]["idle_multiple"],
                "hit10": s10["hit"],
                "mean10": s10["mean"],
                "median10": s10["median"],
                "gross_mean10": s10["gross_mean"],
                "x10": s10["multiple"],
                "cagr10": s10["cagr"],
                "dd10": s10["maxdd"],
                "p10": s10["placebo_p"],
                "x10_idle": variants[DOUBLE_COST_BPS]["idle_multiple"],
                "folds5": year_folds(variants[BASE_COST_BPS]["trades"]),
                "folds10": year_folds(variants[DOUBLE_COST_BPS]["trades"]),
                "ticker5": ticker_folds(variants[BASE_COST_BPS]["trades"]),
                "max_concurrent": max_concurrent_uncapped(
                    variants[BASE_COST_BPS]["trades"]
                ),
                "x5_full": full5["multiple"] if full5 else float("nan"),
                "x10_full": full10["multiple"] if full10 else float("nan"),
                "dd5_full": full5["maxdd"] if full5 else float("nan"),
                "dd10_full": full10["maxdd"] if full10 else float("nan"),
                "years5_full": variants[BASE_COST_BPS].get("full_years", {}),
                "years10_full": variants[DOUBLE_COST_BPS].get("full_years", {}),
                "full5": full5,
                "full10": full10,
            }
        )
    qqq = benchmark("QQQ", interval, range_, sessions)
    bil = bil_benchmark(sessions)
    return {
        "interval": interval,
        "range": range_,
        "bars": bars,
        "prepared": prepared,
        "sessions": sessions,
        "events": events,
        "combos": combos,
        "rows": rows,
        "qqq": qqq,
        "bil": bil,
        "cash": cash,
    }


def _fmt_pct(value: float) -> str:
    return "n/a" if not np.isfinite(value) else f"{value * 100:+.2f}%"


def _fmt_x(value: float) -> str:
    return "n/a" if not np.isfinite(value) else f"{value:.3f}x"


def _fmt_p(value: float) -> str:
    return "n/a" if not np.isfinite(value) else f"{value:.3f}"


def _classify(row: dict, qqq_after: float, bil_after: float, interval: str) -> str:
    if interval == "5m":
        return "execution only (n<30, 60d)"
    if row["n"] < 30:
        return "no (n<30)"
    if row["mean10"] <= 0:
        return "no (2x mean<=0)"
    if row["p10"] >= 0.05:
        return "no (placebo p>=0.05)"
    if row["x10"] <= max(qqq_after, bil_after):
        hurdle = "QQQ" if qqq_after >= bil_after else "BIL"
        return f"no (below {hurdle} after tax)"
    folds = row["folds10"]
    if sum(1 for f in folds.values() if f["mean"] > 0) < 2:
        return "no (fewer than 2 positive years)"
    return "candidate"


def _classify_full(row: dict, qqq_after: float, bil_after: float) -> str:
    """Mechanical check of the upper-bound full-capital sizing."""
    if row["n"] < 30:
        return "no (n<30)"
    if row["mean10"] <= 0:
        return "no (2x mean<=0)"
    if row["p10"] >= 0.05:
        return "no (placebo p>=0.05)"
    if not np.isfinite(row["x10_full"]) or row["x10_full"] <= max(qqq_after, bil_after):
        hurdle = "QQQ" if qqq_after >= bil_after else "BIL"
        return f"no (full-deploy below {hurdle})"
    years = row.get("years10_full", {})
    positive = sum(1 for v in years.values() if v > 0)
    latest = max(years) if years else None
    if positive < 2:
        return "no (full-deploy fewer than 2 positive years)"
    if latest is not None and years.get(latest, 0.0) <= 0:
        return "no (latest fold negative)"
    return "candidate: full-deploy only"


def _event_bucket(ts: pd.Timestamp) -> str:
    t = pd.Timestamp(ts).time()
    if t < pd.Timestamp("09:30").time():
        return "pre_open"
    if t < pd.Timestamp("16:00").time():
        return "intraday"
    return "after_close"


def _markdown_table(headers: list[str], rows: list[list[str]]) -> list[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join(["---"] * len(headers)) + "|",
    ]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return lines


def _write_report(
    book1h: dict, book5m: dict, sensitivity: dict, *, placebo_n: int
) -> str:
    qqq1 = book1h["qqq"]
    bil1 = book1h["bil"]
    qqq5 = book5m["qqq"]
    bil5 = book5m["bil"]
    qqq1_after = qqq1.get("after_tax", float("nan"))
    bil1_after = bil1.get("after_tax", float("nan"))
    qqq5_after = qqq5.get("after_tax", float("nan"))
    bil5_after = bil5.get("after_tax", float("nan"))

    for row in book1h["rows"]:
        row["verdict"] = _classify(row, qqq1_after, bil1_after, "1h")
        row["verdict_full"] = _classify_full(row, qqq1_after, bil1_after)
    for row in book5m["rows"]:
        row["verdict"] = _classify(row, qqq5_after, bil5_after, "5m")
        row["verdict_full"] = "not applicable (5m)"

    rows1 = sorted(book1h["rows"], key=lambda r: r["x10"], reverse=True)
    rows5 = sorted(book5m["rows"], key=lambda r: r["x5"], reverse=True)
    best1 = sensitivity["row"]
    best5 = rows5[0]

    lines: list[str] = []
    lines.append("# Intraday earnings 2.02 continuation/fade")
    lines.append("")
    lines.append(
        "**Model/data status:** cached Yahoo 1h/2y and 5m/60d bars plus cached "
        "SEC 8-K acceptance timestamps. No network, no synthetic rows, no "
        "option quotes, no broker fills, no same-bar entry. This is an "
        "event-study model, not broker-accounted P&L."
    )
    lines.append("")
    lines.append("## Method")
    lines.append("")
    lines.append(
        "- Event: 8-K whose SEC items include **2.02** (results of operations). "
        "Acceptance time is converted to naive New York time."
    )
    lines.append(
        "- Signal bar: first **completed** regular-session bar whose start is "
        "strictly after acceptance. A 16:00 auction print is never a bar."
    )
    lines.append(
        "- Entry: open of the **next** bar after the signal bar. No same-bar "
        "fill. For an after-close release this means the next session's "
        "10:30 ET open, not the opening print; the overnight gap is given up."
    )
    lines.append(
        "- Condition: opening gap (signal-bar open vs prior close), first-bar "
        "reaction (signal-bar close/open), or total move (signal-bar close vs "
        "prior close). Continuation trades the sign; fade trades against it."
    )
    lines.append(
        "- Exits: entry-session close (`close`), next-session close "
        "(`next_session`), or close five sessions after entry (`5d`)."
    )
    lines.append(
        "- Costs: 5 bps/side base and 10 bps/side at 2x. Shorts pay 5%/yr "
        "borrow (10%/yr at 2x); short proceeds earn no interest. Longs are "
        "cash-backed, so there is no leverage financing."
    )
    lines.append(
        "- Portfolio: maximum five concurrent events, 20% of equity each, "
        "earliest events first. Idle cash earns BIL (a zero-idle multiple is "
        "kept in the CSV notes/columns as a conservative check)."
    )
    lines.append(
        "- Tax: annual French PFU (31.4% from 2026, 30% earlier) on net "
        "portfolio P&L, ten-year loss carry."
    )
    lines.append(
        f"- Placebo: each event is moved to a random non-event session for the "
        f"same ticker, keeping its time of day; {placebo_n} draws, same "
        f"horizon/costs/direction. `p` is one-sided: fraction of placebo mean "
        f"net returns at least as good."
    )
    lines.append("")
    lines.append("## Sample")
    lines.append("")
    for label, book in (("1h/2y", book1h), ("5m/60d", book5m)):
        ev = book["events"]
        counts = ev.groupby("ticker").size().sort_index()
        lines.append(
            f"- **{label}:** {len(ev)} item-2.02 events across "
            f"{len(counts)} symbols, event dates "
            f"{ev['event_time'].min().date()}..{ev['event_time'].max().date()}; "
            f"bars {book['sessions'][0].date()}..{book['sessions'][-1].date()}."
        )
        lines.append(
            "  - Per symbol: "
            + ", ".join(f"{sym} {n}" for sym, n in counts.items())
            + "."
        )
        buckets = ev["event_time"].map(_event_bucket).value_counts()
        lines.append(
            "  - Acceptance buckets: "
            + ", ".join(f"{k} {int(v)}" for k, v in buckets.items())
            + "; timestamp source: "
            + ", ".join(
                f"{k} {int(v)}"
                for k, v in ev["timestamp_source"].value_counts().items()
            )
            + "."
        )
    lines.append(
        "- The screen is the SEC item code, not a hand-curated quarterly "
        "earnings list. FANG, OXY and TSLA file 2.02 more often than quarterly "
        "(16 filings each in the 2y window); the other nine names have 8-9. "
        "The 121-event 1h sample is therefore broader than quarterly earnings. "
        "A nine-name quarterly-only subset is reported below as a sensitivity."
    )
    lines.append(
        "- 5m has only 15 events in 60 days. It is an execution-quality check, "
        "never evidence for a candidate."
    )
    lines.append("")
    lines.append("## Benchmarks")
    lines.append("")
    bench_rows = []
    for label, qqq, bil in (
        ("1h_2y", qqq1, bil1),
        ("5m_60d", qqq5, bil5),
    ):
        bench_rows.append(
            [
                label,
                f"{qqq.get('range', 'n/a')}",
                _fmt_pct(qqq.get("gross", float("nan"))),
                _fmt_x(qqq.get("after_tax", float("nan"))),
                _fmt_pct(bil.get("gross", float("nan"))),
                _fmt_x(bil.get("after_tax", float("nan"))),
            ]
        )
    lines.extend(
        _markdown_table(
            [
                "book",
                "sample",
                "QQQ gross",
                "QQQ after tax",
                "BIL gross",
                "BIL after tax",
            ],
            bench_rows,
        )
    )
    lines.append("")
    lines.append("## 1h variants, all registered")
    lines.append("")
    lines.append(
        "Sorted by 2x after-tax multiple. `x` includes BIL carry on idle cash; "
        "`x_idle` is the same path with zero idle return. `n` is usable events; "
        "`traded` is after the five-concurrent cap. The trial registry has 218 "
        "rows: 72 for 1h, 72 for 5m, 72 for the full-deploy sizing check and 2 "
        "quarterly-only sensitivity rows."
    )
    lines.append("")
    table = []
    for r in rows1:
        table.append(
            [
                r["signal"],
                r["mode"],
                f"{r['threshold_bps']:.0f}",
                r["exit_kind"],
                str(r["n"]),
                str(r["n_traded_10"]),
                f"{r['hit5'] * 100:.1f}%",
                _fmt_pct(r["mean5"]),
                _fmt_pct(r["mean10"]),
                _fmt_p(r["p5"]),
                _fmt_p(r["p10"]),
                _fmt_x(r["x5"]),
                _fmt_x(r["x10"]),
                _fmt_x(r["x10_idle"]),
                f"{r['dd10'] * 100:.1f}%",
                r["verdict"],
            ]
        )
    lines.extend(
        _markdown_table(
            [
                "signal",
                "mode",
                "thr bps",
                "exit",
                "n",
                "traded",
                "hit 5bp",
                "mean 5bp",
                "mean 10bp",
                "p 5bp",
                "p 10bp",
                "x 5bp",
                "x 10bp",
                "x10 idle",
                "maxDD 10bp",
                "verdict",
            ],
            table,
        )
    )
    lines.append("")
    lines.append(
        f"The best fixed-slot 2x row is **{best1['signal']} {best1['mode']} "
        f"thr={best1['threshold_bps']:.0f}bps {best1['exit_kind']}**: "
        f"n={best1['n']}, mean net {_fmt_pct(best1['mean10'])}, "
        f"after-tax {_fmt_x(best1['x10'])} vs QQQ {_fmt_x(qqq1_after)} and "
        f"BIL {_fmt_x(bil1_after)}, placebo p={_fmt_p(best1['p10'])}, "
        f"maxDD {best1['dd10'] * 100:.1f}%."
    )
    lines.append("")
    lines.append("## Capital-utilization upper bound")
    lines.append("")
    lines.append(
        "The main portfolio gives each of the five slots 20% of equity. As a "
        "sizing sensitivity, `equal_active` gives active positions 100% of "
        "equity split equally each day, with no charge for rebalancing. This "
        "is an upper bound, not a deployable accounting result. All 36 "
        "full-deploy variants are in the registry; the top ten by 2x multiple "
        "are shown."
    )
    lines.append("")
    full_rows = sorted(book1h["rows"], key=lambda r: r["x10_full"], reverse=True)[:10]
    full_table = []
    for r in full_rows:
        years = r.get("years10_full", {})
        full_table.append(
            [
                r["signal"],
                r["mode"],
                f"{r['threshold_bps']:.0f}",
                r["exit_kind"],
                str(r["n"]),
                _fmt_pct(r["mean10"]),
                _fmt_p(r["p10"]),
                _fmt_x(r["x10"]),
                _fmt_x(r["x10_full"]),
                f"{r['dd10_full'] * 100:.1f}%",
                _fmt_pct(years.get(2025, float("nan"))),
                _fmt_pct(years.get(2026, float("nan"))),
                r["verdict_full"],
            ]
        )
    lines.extend(
        _markdown_table(
            [
                "signal",
                "mode",
                "thr bps",
                "exit",
                "n",
                "mean 10bp",
                "p 10bp",
                "x10 fixed",
                "x10 full",
                "maxDD full",
                "2025 ret full",
                "2026 ret full",
                "verdict full",
            ],
            full_table,
        )
    )
    full_above = [
        r for r in book1h["rows"] if r["x10_full"] > max(qqq1_after, bil1_after)
    ]
    if full_above:
        lines.append("")
        lines.append(
            "Full-deploy rows above QQQ: "
            + "; ".join(
                f"{r['signal']} {r['mode']} thr={r['threshold_bps']:.0f}bps "
                f"{r['exit_kind']} {_fmt_x(r['x10_full'])} "
                f"(2026 {_fmt_pct(r.get('years10_full', {}).get(2026, float('nan')))})"
                for r in full_above
            )
            + "."
        )
    lines.append("")
    lines.append("## Per-year and per-ticker folds of the leading 2x row")
    lines.append("")
    lines.append(
        f"Leading row: **{best1['signal']} {best1['mode']} "
        f"thr={best1['threshold_bps']:.0f}bps {best1['exit_kind']}**. "
        "Folds use the 5 bps trade sample; the 10 bps means are 0.10% lower "
        "per trade plus the doubled short borrow."
    )
    lines.append("")
    fold_rows = []
    for year in sorted(best1["folds5"]):
        f = best1["folds5"][year]
        fold_rows.append(
            [
                str(year),
                str(f["n"]),
                _fmt_pct(f["mean"]),
                f"{f['hit'] * 100:.1f}%",
                _fmt_pct(f["gross_mean"]),
            ]
        )
    lines.extend(
        _markdown_table(["year", "n", "mean net 5bp", "hit", "gross mean"], fold_rows)
    )
    lines.append("")
    tick_rows = []
    for ticker in sorted(best1["ticker5"]):
        f = best1["ticker5"][ticker]
        tick_rows.append(
            [
                ticker,
                str(f["n"]),
                _fmt_pct(f["mean"]),
                f"{f['hit'] * 100:.1f}%",
            ]
        )
    lines.extend(_markdown_table(["ticker", "n", "mean net 5bp", "hit"], tick_rows))
    lines.append("")
    lines.append(
        f"Concurrency: {best1['n']} candidate events, "
        f"{best1['n_traded_10']} traded and {best1['n_skipped_10']} skipped "
        f"at the five-slot cap; uncapped maximum was "
        f"{best1['max_concurrent']} concurrent positions."
    )
    lines.append("")
    lines.append("## Quarterly-only sensitivity")
    lines.append("")
    lines.append(
        "Drop FANG/OXY/TSLA, whose 2.02 filings are monthly rather than "
        "quarterly. This is a data-quality check on the leading row, not a "
        "new search."
    )
    lines.append("")
    sens_rows = []
    for cost_bps, borrow_bps in COSTS:
        key = "summary" if cost_bps == BASE_COST_BPS else "summary10"
        sens = sensitivity[key]
        sens_rows.append(
            [
                f"{cost_bps:.0f} bps",
                str(sens["n"]),
                _fmt_pct(sens["mean"]),
                _fmt_x(sens["multiple"]),
                _fmt_p(sens["placebo_p"]),
            ]
        )
    lines.extend(
        _markdown_table(
            ["cost", "n", "mean net", "after-tax x", "placebo p"], sens_rows
        )
    )
    lines.append("")
    lines.append("## 5m execution-quality check, 60 days")
    lines.append("")
    lines.append(
        "Same grid on 5m bars. The sample is 15 events or fewer; every row is "
        "**not** a candidate regardless of its multiple."
    )
    lines.append("")
    table5 = []
    for r in rows5:
        table5.append(
            [
                r["signal"],
                r["mode"],
                f"{r['threshold_bps']:.0f}",
                r["exit_kind"],
                str(r["n"]),
                _fmt_pct(r["mean5"]),
                _fmt_pct(r["mean10"]),
                _fmt_x(r["x5"]),
                _fmt_x(r["x10"]),
                _fmt_p(r["p5"]),
                r["verdict"],
            ]
        )
    lines.extend(
        _markdown_table(
            [
                "signal",
                "mode",
                "thr bps",
                "exit",
                "n",
                "mean 5bp",
                "mean 10bp",
                "x 5bp",
                "x 10bp",
                "p 5bp",
                "verdict",
            ],
            table5,
        )
    )
    lines.append("")
    lines.append(
        f"Best 5m row is **{best5['signal']} {best5['mode']} "
        f"thr={best5['threshold_bps']:.0f}bps {best5['exit_kind']}** with "
        f"n={best5['n']} and 2x after-tax {_fmt_x(best5['x10'])}; this is "
        f"execution quality only."
    )
    lines.append("")
    lines.append("## Verdict")
    lines.append("")
    candidates = [r for r in rows1 if r["verdict"] == "candidate"]
    full_leads = [
        r
        for r in rows1
        if r["n"] >= 30
        and r["mean10"] > 0
        and r["p10"] < 0.05
        and r["x10_full"] > max(qqq1_after, bil1_after)
    ]
    if candidates:
        lines.append(
            f"**{len(candidates)} 1h variant(s) pass the fixed-slot mechanical "
            "screen.** They are not merged: the screen is in-sample, the sample "
            "is small, and the author must review the folds before any live use."
        )
    elif full_leads:
        lead = full_leads[0]
        lines.append(
            "**Reject as a deployable candidate, but record one fragile "
            "upper-bound lead.** No 1h variant clears QQQ "
            f"({_fmt_x(qqq1_after)}) and BIL ({_fmt_x(bil1_after)}) after tax "
            "at 2x costs under the pre-specified 20% slot sizing. The only "
            "full-capital row above QQQ is "
            f"**{lead['signal']} {lead['mode']} "
            f"thr={lead['threshold_bps']:.0f}bps {lead['exit_kind']}**: "
            f"2x after-tax {_fmt_x(lead['x10_full'])} with placebo p="
            f"{_fmt_p(lead['p10'])}, but its 2025 full-deploy return is "
            f"{_fmt_pct(lead.get('years10_full', {}).get(2025, float('nan')))} "
            f"and 2026 is "
            f"{_fmt_pct(lead.get('years10_full', {}).get(2026, float('nan')))}, "
            f"with maxDD {lead['dd10_full'] * 100:.1f}%. It fails the year-fold "
            "rule, is one of 36 variants at p=0.035, and the full-deploy path "
            "does not charge rebalancing. Keep it only as a paper-research lead; "
            "do not merge. The best fixed-slot 2x row is "
            f"{_fmt_x(best1['x10'])}, below QQQ."
        )
    else:
        lines.append(
            "**Reject intraday earnings 2.02 continuation/fade as a candidate.** "
            f"No 1h variant with n>=30 clears QQQ ({_fmt_x(qqq1_after)}) and "
            f"BIL ({_fmt_x(bil1_after)}) after tax at 2x costs (10 bps/side, "
            f"10%/yr short borrow) with a placebo p<0.05. The best 2x row is "
            f"{_fmt_x(best1['x10'])}, below QQQ; its placebo p is "
            f"{_fmt_p(best1['p10'])}, and the 2025 fold mean is "
            f"{_fmt_pct(best1['folds10'].get(2025, {}).get('mean', float('nan')))}."
        )
    lines.append("")
    lines.append(
        "Under the pre-specified 20% risk budget the first-hour reaction does "
        "not clear QQQ. The full-capital gap-fade lead is not robust: it is one "
        "marginal p=0.035 hit among 36 variants, its 2026 return is negative, "
        "and its -20.9% drawdown buys only a two-point multiple edge; charging "
        "rebalancing would make it worse. That is not enough for a candidate. "
        "The 5m book is too small to change the verdict."
    )
    lines.append("")
    lines.append("## Limits")
    lines.append("")
    lines.append(
        "- 1h bars are Yahoo aggregates, 2 years, 12 liquid names. They are not "
        "exchange MBO/L1 and do not model queue position or partial fills."
    )
    lines.append(
        "- The after-close reaction is entered at 10:30 ET, one completed hour "
        "after the open. A faster implementation would face the opening "
        "auction, wider spreads and the same no-same-bar rule."
    )
    lines.append(
        "- Overlapping earnings and one sector cluster reduce the effective "
        "sample. The five-slot cap binds only lightly (maximum uncapped seven)."
    )
    lines.append(
        "- Placebo is a date shuffle, not a matched-volatility synthetic "
        "event; it tests whether the event date carries information beyond "
        "the ticker/time distribution."
    )
    lines.append(
        "- No deflated Sharpe/CSCV is reported. With 36 variants, the "
        "gap-fade p=0.035 is a single-test result and does not survive a "
        "family-wise correction."
    )
    lines.append("- No live/real-money code, no broker adapter, no order routing.")
    lines.append("")
    lines.append(
        "Reproduce: `uv run python scripts/research_earn_intraday.py` "
        "(cached data only; no `--fetch`)."
    )
    return "\n".join(lines) + "\n"


def _run_subset(
    book: dict,
    row: dict,
    *,
    exclude: set[str],
    cost_bps: float,
    borrow_bps: float,
    placebo_n: int = PLACEBO_N,
) -> dict:
    events = book["events"][~book["events"]["ticker"].isin(exclude)]
    prepared = {k: v for k, v in book["prepared"].items() if k not in exclude}
    tr = trades_for(
        prepared,
        events,
        signal=row["signal"],
        mode=row["mode"],
        threshold=row["threshold_bps"] / 1e4,
        exit_kind=row["exit_kind"],
        cost_bps=cost_bps,
        borrow_bps=borrow_bps,
    )
    port = portfolio_daily(
        tr,
        prepared,
        book["sessions"],
        cost_bps=cost_bps,
        borrow_bps=borrow_bps,
        max_concurrent=MAX_CONCURRENT,
        cash_returns=book["cash"],
    )
    dist = placebo_distribution_subset(
        book,
        events,
        prepared,
        row,
        cost_bps=cost_bps,
        borrow_bps=borrow_bps,
        n=placebo_n,
    )
    p = placebo_p(float(tr["net"].mean()) if not tr.empty else float("nan"), dist)
    s = summarize(tr, portfolio=port, sessions=book["sessions"], placebo_p=p)
    return {"summary": s, "trades": tr}


def placebo_distribution_subset(
    book: dict,
    events: pd.DataFrame,
    prepared: dict,
    row: dict,
    *,
    cost_bps: float,
    borrow_bps: float,
    n: int = PLACEBO_N,
    seed: int = 23,
) -> np.ndarray:
    rng = np.random.default_rng(seed)
    out = np.full(n, np.nan)
    for draw in range(n):
        shifted = shift_events(events, prepared, rng)
        if shifted.empty:
            continue
        tr = trades_for(
            prepared,
            shifted,
            signal=row["signal"],
            mode=row["mode"],
            threshold=row["threshold_bps"] / 1e4,
            exit_kind=row["exit_kind"],
            cost_bps=cost_bps,
            borrow_bps=borrow_bps,
        )
        if not tr.empty:
            out[draw] = float(tr["net"].mean())
    return out


def _register(book1h: dict, book5m: dict, sensitivity: dict) -> None:
    registry = TrialRegistry(branch="v11/short-earn")
    n1 = len(book1h["rows"]) * 2
    n5 = len(book5m["rows"]) * 2
    total = n1 + n5 + 2 + n1
    for book, family, start, end in (
        (
            book1h,
            "earn_intraday_1h",
            str(book1h["sessions"][0].date()),
            str(book1h["sessions"][-1].date()),
        ),
        (
            book5m,
            "earn_intraday_5m",
            str(book5m["sessions"][0].date()),
            str(book5m["sessions"][-1].date()),
        ),
    ):
        for r in book["rows"]:
            for cost_bps, borrow_bps in COSTS:
                value = r["x5"] if cost_bps == BASE_COST_BPS else r["x10"]
                p = r["p5"] if cost_bps == BASE_COST_BPS else r["p10"]
                mean = r["mean5"] if cost_bps == BASE_COST_BPS else r["mean10"]
                idle = r["x5_idle"] if cost_bps == BASE_COST_BPS else r["x10_idle"]
                f2025 = r["folds5"].get(2025, {}).get("mean", float("nan"))
                f2026 = r["folds5"].get(2026, {}).get("mean", float("nan"))
                notes = (
                    f"n={r['n']}; traded={r['n_traded_10']}; "
                    f"skipped={r['n_skipped_10']}; mean_net={mean:.6f}; "
                    f"placebo_p={p:.4f}; idle0_x={idle:.6f}; "
                    f"2025_mean={f2025:.6f}; 2026_mean={f2026:.6f}; "
                    f"max_concurrent_uncapped={r['max_concurrent']}; "
                    f"study_variants={total}"
                )
                registry.add(
                    family=family,
                    hypothesis=(
                        f"item 2.02 {r['signal']} {r['mode']} "
                        f"threshold {r['threshold_bps']:.0f}bps exit {r['exit_kind']} "
                        "survives costs and QQQ/BIL"
                    ),
                    universe="12 liquid US 2.02 filers"
                    if book is book1h
                    else "12 liquid US 2.02 filers, 60d",
                    start=start,
                    end=end,
                    params={
                        "interval": book["interval"],
                        "signal": r["signal"],
                        "mode": r["mode"],
                        "threshold_bps": r["threshold_bps"],
                        "exit": r["exit_kind"],
                        "cost_bps": cost_bps,
                        "borrow_bps": borrow_bps,
                        "max_concurrent": MAX_CONCURRENT,
                    },
                    metric="after_tax_multiple",
                    value=value,
                    cost_model=cost_model_name(cost_bps),
                    verdict=r["verdict"],
                    notes=notes,
                    n_trials=n1 if book is book1h else n5,
                )
    for r in book1h["rows"]:
        for cost_bps, borrow_bps in COSTS:
            full = r["full5"] if cost_bps == BASE_COST_BPS else r["full10"]
            years = (
                r.get("years5_full", {})
                if cost_bps == BASE_COST_BPS
                else r.get("years10_full", {})
            )
            p = r["p5"] if cost_bps == BASE_COST_BPS else r["p10"]
            value = r["x5_full"] if cost_bps == BASE_COST_BPS else r["x10_full"]
            registry.add(
                family="earn_intraday_1h_full_deploy",
                hypothesis=(
                    f"item 2.02 {r['signal']} {r['mode']} "
                    f"threshold {r['threshold_bps']:.0f}bps exit {r['exit_kind']} "
                    "under upper-bound equal-active sizing"
                ),
                universe="12 liquid US 2.02 filers",
                start=str(book1h["sessions"][0].date()),
                end=str(book1h["sessions"][-1].date()),
                params={
                    "interval": "1h",
                    "signal": r["signal"],
                    "mode": r["mode"],
                    "threshold_bps": r["threshold_bps"],
                    "exit": r["exit_kind"],
                    "cost_bps": cost_bps,
                    "borrow_bps": borrow_bps,
                    "max_concurrent": MAX_CONCURRENT,
                    "sizing": "equal_active",
                    "rebalance_cost": False,
                },
                metric="after_tax_multiple",
                value=value,
                cost_model=cost_model_name(cost_bps),
                verdict=r["verdict_full"],
                notes=(
                    f"n={r['n']}; mean_net="
                    f"{r['mean5'] if cost_bps == BASE_COST_BPS else r['mean10']:.6f}; "
                    f"placebo_p={p:.4f}; fixed_slot_x="
                    f"{r['x5'] if cost_bps == BASE_COST_BPS else r['x10']:.6f}; "
                    f"maxdd={full['maxdd']:.6f}; "
                    f"2025_year_ret={years.get(2025, float('nan')):.6f}; "
                    f"2026_year_ret={years.get(2026, float('nan')):.6f}; "
                    f"study_variants={total}"
                ),
                n_trials=n1,
            )
    for cost_bps, borrow_bps in COSTS:
        s = (
            sensitivity["summary"]
            if cost_bps == BASE_COST_BPS
            else sensitivity["summary10"]
        )
        registry.add(
            family="earn_intraday_1h_sensitivity",
            hypothesis="leading 2.02 variant survives on nine quarterly filers",
            universe="9 quarterly 2.02 filers (ex FANG/OXY/TSLA)",
            start=str(book1h["sessions"][0].date()),
            end=str(book1h["sessions"][-1].date()),
            params={
                "interval": "1h",
                "signal": sensitivity["row"]["signal"],
                "mode": sensitivity["row"]["mode"],
                "threshold_bps": sensitivity["row"]["threshold_bps"],
                "exit": sensitivity["row"]["exit_kind"],
                "cost_bps": cost_bps,
                "borrow_bps": borrow_bps,
                "max_concurrent": MAX_CONCURRENT,
            },
            metric="after_tax_multiple",
            value=s["multiple"],
            cost_model=cost_model_name(cost_bps),
            verdict="sensitivity",
            notes=(
                f"n={s['n']}; mean_net={s['mean']:.6f}; "
                f"placebo_p={s['placebo_p']:.4f}; ex-monthly filers; "
                f"study_variants={total}"
            ),
            n_trials=n1,
        )
    registry.write(OUT_CSV)
    # Keep the committed registry LF-only; git diff --check treats CSV CRLF
    # as trailing whitespace on every new row.
    OUT_CSV.write_bytes(OUT_CSV.read_bytes().replace(b"\r\n", b"\n"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--placebo-n", type=int, default=PLACEBO_N)
    parser.add_argument("--seed", type=int, default=11)
    args = parser.parse_args()

    book1h = _run_book("1h", "2y", placebo_n=args.placebo_n, seed=args.seed)
    book5m = _run_book("5m", "60d", placebo_n=args.placebo_n, seed=args.seed + 1)

    qqq1_after = book1h["qqq"].get("after_tax", float("nan"))
    bil1_after = book1h["bil"].get("after_tax", float("nan"))
    for row in book1h["rows"]:
        row["verdict"] = _classify(row, qqq1_after, bil1_after, "1h")
        row["verdict_full"] = _classify_full(row, qqq1_after, bil1_after)
    for row in book5m["rows"]:
        row["verdict"] = _classify(row, qqq1_after, bil1_after, "5m")
        row["verdict_full"] = "not applicable (5m)"
    best1 = max(book1h["rows"], key=lambda r: r["x10"])

    sens5 = _run_subset(
        book1h,
        best1,
        exclude={"FANG", "OXY", "TSLA"},
        cost_bps=BASE_COST_BPS,
        borrow_bps=BASE_BORROW_BPS,
        placebo_n=args.placebo_n,
    )
    sens10 = _run_subset(
        book1h,
        best1,
        exclude={"FANG", "OXY", "TSLA"},
        cost_bps=DOUBLE_COST_BPS,
        borrow_bps=DOUBLE_BORROW_BPS,
        placebo_n=args.placebo_n,
    )
    sensitivity = {
        "row": best1,
        "summary": sens5["summary"],
        "summary10": sens10["summary"],
    }

    report = _write_report(book1h, book5m, sensitivity, placebo_n=args.placebo_n)
    OUT_MD.write_text(report)
    _register(book1h, book5m, sensitivity)

    # Console summary only; the files are the deliverable.
    print(f"wrote {OUT_MD}")
    print(f"wrote {OUT_CSV}")
    print(
        f"best 1h 2x: {best1['signal']} {best1['mode']} "
        f"thr={best1['threshold_bps']:.0f}bps {best1['exit_kind']} "
        f"x={best1['x10']:.3f} p={best1['p10']:.3f} n={best1['n']}"
    )
    print(f"QQQ after tax {qqq1_after:.3f}x, BIL after tax {bil1_after:.3f}x")


if __name__ == "__main__":
    main()
