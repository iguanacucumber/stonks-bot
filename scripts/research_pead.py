"""v11 A: earnings 8-K item 2.02 drift (PEAD) after costs.

Run: uv run python scripts/research_pead.py
Writes results/pead.md and results/pead_trials.csv. Cache-only; no network.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from bot.evaluation.cscv import cscv_pbo, deflated_sharpe  # noqa: E402
from bot.research.pead.events import (  # noqa: E402
    attach_timing,
    eligible,
    load_bars,
    load_events,
)
from bot.research.pead.metrics import (  # noqa: E402
    after_tax_metrics,
    cscv_matrix,
    event_stats,
    gate_table,
    paired_bootstrap,
    placebo_pvalue,
    placebo_portfolio,
    summarize_result,
    terminal_after_tax_metrics,
    trial_sharpes,
    walk_forward_variants,
    year_table,
)
from bot.research.pead.portfolio import (  # noqa: E402
    buy_hold,
    event_returns,
    simulate,
)

START = "2014-01-01"
END = "2026-11-25"
COSTS = (5.0, 10.0)
HOLDS = (3, 5, 10, 20)
FILTERS = ("gap", "close_close", "open_close", "volume_z")
N_TRIALS = 50
PLACEBO_N = 500


def pct(x: float, digits: int = 1) -> str:
    if x is None or not np.isfinite(x):
        return "—"
    return f"{x * 100:+.{digits}f}%"


def mult(x: float) -> str:
    if x is None or not np.isfinite(x):
        return "—"
    return f"{x:.2f}x"


def fmt_p(x: float) -> str:
    if x is None or not np.isfinite(x):
        return "—"
    return f"{x:.3f}"


def fmt_p_tight(x: float) -> str:
    if x is None or not np.isfinite(x):
        return "—"
    return f"{x:.4f}"


def load_all() -> tuple[pd.DataFrame, dict[str, pd.DataFrame], pd.DataFrame]:
    raw = load_events(start=START, end=END)
    bars = load_bars(set(raw["ticker"]) | {"QQQ", "BIL"})
    missing = sorted(set(raw["ticker"]) - set(bars))
    if missing:
        print(f"missing bars, dropped: {missing}")
    raw = raw[raw["ticker"].isin(bars)].reset_index(drop=True)
    timing = attach_timing(raw, bars, start=START, end=END)
    return raw, bars, timing


def benchmark(
    bars: dict[str, pd.DataFrame],
    symbol: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> dict:
    r5 = buy_hold(bars, symbol, start, end, cost_bps=5.0)
    r10 = buy_hold(bars, symbol, start, end, cost_bps=10.0)
    m5 = terminal_after_tax_metrics(r5, end_year=int(end.year), cost_bps=5.0)
    m10 = terminal_after_tax_metrics(r10, end_year=int(end.year), cost_bps=10.0)
    m5.update(
        {
            "after_tax_multiple_2x": m10["after_tax_multiple"],
            "after_tax_cagr_2x": m10["after_tax_cagr"],
            "maxdd_2x": m10["maxdd"],
            "sharpe_2x": m10["sharpe"],
            "daily_5bps": r5,
            "daily_10bps": r10,
        }
    )
    return m5


def primary_dict(result, cost_bps: float, qqq, bil) -> dict:
    m = summarize_result(result, cost_bps=cost_bps, qqq=qqq, bil=bil)
    return m


def dsr_for(series: pd.Series, sharpes: list[float]) -> float:
    r = series.dropna()
    if len(r) < 30 or float(r.std(ddof=1)) <= 0:
        return float("nan")
    try:
        return float(
            deflated_sharpe(r, n_trials=N_TRIALS, trial_sharpes=sharpes)[
                "deflated_sharpe"
            ]
        )
    except Exception:
        return float("nan")


def bh_qvalues(pvals: list[float]) -> list[float]:
    """Benjamini-Hochberg q-values; NaN stays NaN."""
    arr = np.asarray(pvals, dtype=float)
    n = len(arr)
    q = np.full(n, np.nan)
    ok = np.isfinite(arr)
    if not ok.any():
        return q.tolist()
    idx = np.flatnonzero(ok)
    order = idx[np.argsort(arr[idx])]
    m = len(order)
    prev = 1.0
    for rank in range(m, 0, -1):
        i = order[rank - 1]
        val = min(prev, arr[i] * m / rank)
        q[i] = val
        prev = val
    return q.tolist()


def apply_walk_forward(
    records: list[dict],
    daily10: dict[tuple[str, int], pd.Series],
    bil: pd.Series,
) -> pd.Series:
    parts = []
    for rec in records:
        year = rec["year"]
        chosen = rec["chosen"]
        if chosen == "BIL":
            r = bil.loc[str(year)]
        else:
            reaction, hold = chosen.split("|")
            r = daily10[(reaction, int(hold))].loc[str(year)]
        parts.append(r)
    if not parts:
        return pd.Series(dtype=float)
    return pd.concat(parts).sort_index()


def main() -> None:
    print("loading cached events and bars")
    raw, bars, timing = load_all()
    print(f"events: raw {len(raw)}, timed {len(timing)}")

    qqq_dummy = pd.Series(dtype=float)
    bil_dummy = pd.Series(dtype=float)

    # Primary and its same-window benchmarks.
    res5 = simulate(
        timing,
        bars,
        hold=5,
        cost_bps=5.0,
        reaction="gap",
        cash_yield=True,
    )
    res10 = simulate(
        timing,
        bars,
        hold=5,
        cost_bps=10.0,
        reaction="gap",
        cash_yield=True,
    )
    start = res5.equity.index[0]
    end = res5.equity.index[-1]
    qqq = benchmark(bars, "QQQ", start, end)
    bil = benchmark(bars, "BIL", start, end)
    qqq_dummy = qqq["daily_5bps"]
    bil_dummy = bil["daily_5bps"]

    primary5 = primary_dict(res5, 5.0, qqq_dummy, bil_dummy)
    primary10 = primary_dict(res10, 10.0, qqq_dummy, bil_dummy)
    primary = dict(primary5)
    primary.update(
        {
            "after_tax_multiple_2x": primary10["after_tax_multiple"],
            "after_tax_cagr_2x": primary10["after_tax_cagr"],
            "maxdd_2x": primary10["maxdd"],
            "sharpe_2x": primary10["sharpe"],
        }
    )

    ev5 = event_returns(timing, bars, hold=5, cost_bps=5.0, reaction="gap")
    ev10 = event_returns(timing, bars, hold=5, cost_bps=10.0, reaction="gap")
    evs5 = event_stats(ev5)
    evs10 = event_stats(ev10)
    print(f"primary events {evs5['n']}, trades {res5.n_entries}")

    print("placebo: primary event and portfolio")
    placebo_event = placebo_pvalue(
        timing, bars, hold=5, reaction="gap", cost_bps=5.0, n=PLACEBO_N
    )
    placebo_path = placebo_portfolio(
        timing,
        bars,
        res5,
        hold=5,
        reaction="gap",
        cost_bps=5.0,
        n=PLACEBO_N,
    )
    boot_qqq = paired_bootstrap(res5.daily_returns, qqq_dummy)
    boot_bil = paired_bootstrap(res5.daily_returns, bil_dummy)

    # Registered long-only grid.
    print("registered long-only grid")
    long_rows: list[dict] = []
    daily_long: dict[tuple[str, int, float], pd.Series] = {}
    daily_long_5: dict[tuple[str, int], pd.Series] = {}
    daily_long_10: dict[tuple[str, int], pd.Series] = {}
    event_placebo: dict[tuple[str, int], dict] = {}
    for reaction in FILTERS:
        for hold in HOLDS:
            p = placebo_pvalue(
                timing,
                bars,
                hold=hold,
                reaction=reaction,
                cost_bps=5.0,
                n=PLACEBO_N,
            )
            event_placebo[(reaction, hold)] = p
            for cost in COSTS:
                res = simulate(
                    timing,
                    bars,
                    hold=hold,
                    cost_bps=cost,
                    reaction=reaction,
                    cash_yield=True,
                )
                stats = summarize_result(
                    res, cost_bps=cost, qqq=qqq_dummy, bil=bil_dummy
                )
                ev = event_returns(
                    timing, bars, hold=hold, cost_bps=cost, reaction=reaction
                )
                es = event_stats(ev)
                row = {
                    "family": "long_only",
                    "reaction": reaction,
                    "hold": hold,
                    "cost_bps": cost,
                    "mode": "long",
                    "n_events": es["n"],
                    "n_trades": stats["n_trades"],
                    "mean_event_net": es["mean_net"],
                    "event_hit": es["hit"],
                    "after_tax_multiple": stats["after_tax_multiple"],
                    "after_tax_cagr": stats["after_tax_cagr"],
                    "maxdd": stats["maxdd"],
                    "sharpe": stats["sharpe"],
                    "placebo_p": p["p_value"],
                    "skipped_slots": stats["skipped_slots"],
                    "skipped_active": stats["skipped_active"],
                    "skipped_cash": stats["skipped_cash"],
                    "avg_positions": stats["avg_positions"],
                    "avg_invested": stats["avg_invested"],
                    "max_concurrent": stats["max_concurrent"],
                    "cash_yield": stats["cash_yield"],
                }
                long_rows.append(row)
                daily_long[(reaction, hold, cost)] = res.daily_returns
                if cost == 5.0:
                    daily_long_5[(reaction, hold)] = res.daily_returns
                else:
                    daily_long_10[(reaction, hold)] = res.daily_returns

    print("registered long-short and cash-zero grid")
    other_rows: list[dict] = []
    daily_other: dict[tuple[str, str, float], pd.Series] = {}
    for hold in HOLDS:
        for cost in COSTS:
            res = simulate(
                timing,
                bars,
                hold=hold,
                cost_bps=cost,
                reaction="all_gap",
                long_short=True,
                cash_yield=False,
            )
            stats = summarize_result(res, cost_bps=cost, qqq=qqq_dummy, bil=bil_dummy)
            ev = event_returns(
                timing,
                bars,
                hold=hold,
                cost_bps=cost,
                reaction="all_gap",
                long_short=True,
            )
            es = event_stats(ev)
            other_rows.append(
                {
                    "family": "long_short",
                    "reaction": "gap_long_short",
                    "hold": hold,
                    "cost_bps": cost,
                    "mode": "long_short",
                    "n_events": es["n"],
                    "n_trades": stats["n_trades"],
                    "mean_event_net": es["mean_net"],
                    "event_hit": es["hit"],
                    "after_tax_multiple": stats["after_tax_multiple"],
                    "after_tax_cagr": stats["after_tax_cagr"],
                    "maxdd": stats["maxdd"],
                    "sharpe": stats["sharpe"],
                    "placebo_p": float("nan"),
                    "skipped_slots": stats["skipped_slots"],
                    "skipped_active": stats["skipped_active"],
                    "skipped_cash": stats["skipped_cash"],
                    "avg_positions": stats["avg_positions"],
                    "avg_invested": stats["avg_invested"],
                    "max_concurrent": stats["max_concurrent"],
                    "cash_yield": stats["cash_yield"],
                }
            )
            daily_other[("long_short", str(hold), cost)] = res.daily_returns

    for hold in HOLDS:
        for cost in COSTS:
            res = simulate(
                timing,
                bars,
                hold=hold,
                cost_bps=cost,
                reaction="gap",
                cash_yield=False,
            )
            stats = summarize_result(res, cost_bps=cost, qqq=qqq_dummy, bil=bil_dummy)
            es = event_stats(
                event_returns(timing, bars, hold=hold, cost_bps=cost, reaction="gap")
            )
            other_rows.append(
                {
                    "family": "cash_zero",
                    "reaction": "gap",
                    "hold": hold,
                    "cost_bps": cost,
                    "mode": "long_cash0",
                    "n_events": es["n"],
                    "n_trades": stats["n_trades"],
                    "mean_event_net": es["mean_net"],
                    "event_hit": es["hit"],
                    "after_tax_multiple": stats["after_tax_multiple"],
                    "after_tax_cagr": stats["after_tax_cagr"],
                    "maxdd": stats["maxdd"],
                    "sharpe": stats["sharpe"],
                    "placebo_p": event_placebo[("gap", hold)]["p_value"],
                    "skipped_slots": stats["skipped_slots"],
                    "skipped_active": stats["skipped_active"],
                    "skipped_cash": stats["skipped_cash"],
                    "avg_positions": stats["avg_positions"],
                    "avg_invested": stats["avg_invested"],
                    "max_concurrent": stats["max_concurrent"],
                    "cash_yield": stats["cash_yield"],
                }
            )
            daily_other[("cash_zero", str(hold), cost)] = res.daily_returns

    print("walk-forward by year")
    wf5, wf_records = walk_forward_variants(daily_long_5, bil_dummy)
    wf10 = apply_walk_forward(wf_records, daily_long_10, bil_dummy)
    wf_rows = []
    for label, series, cost in (("5", wf5, 5.0), ("10", wf10, 10.0)):
        stats = after_tax_metrics(series, cost_bps=cost)
        wf_rows.append(
            {
                "family": "walk_forward",
                "reaction": "selected_by_year",
                "hold": 0,
                "cost_bps": cost,
                "mode": "long",
                "n_events": 0,
                "n_trades": 0,
                "mean_event_net": float("nan"),
                "event_hit": float("nan"),
                "after_tax_multiple": stats["after_tax_multiple"],
                "after_tax_cagr": stats["after_tax_cagr"],
                "maxdd": stats["maxdd"],
                "sharpe": stats["sharpe"],
                "placebo_p": float("nan"),
                "skipped_slots": 0,
                "skipped_active": 0,
                "skipped_cash": 0,
                "avg_positions": float("nan"),
                "avg_invested": float("nan"),
                "max_concurrent": float("nan"),
                "cash_yield": float("nan"),
            }
        )
        daily_other[("walk_forward", label, cost)] = series

    all_rows = long_rows + other_rows + wf_rows
    assert len(all_rows) == N_TRIALS, len(all_rows)

    # DSR and common multiple-testing inputs.
    all_daily: dict[tuple[str, str, float], pd.Series] = {}
    for (reaction, hold, cost), series in daily_long.items():
        all_daily[("long_only", f"{reaction}|{hold}", cost)] = series
    all_daily.update(daily_other)
    sharpes = trial_sharpes(all_daily)
    for row in all_rows:
        if row["family"] == "long_only":
            key = ("long_only", f"{row['reaction']}|{row['hold']}", row["cost_bps"])
        elif row["family"] == "long_short":
            key = ("long_short", str(row["hold"]), row["cost_bps"])
        elif row["family"] == "cash_zero":
            key = ("cash_zero", str(row["hold"]), row["cost_bps"])
        else:
            key = (
                "walk_forward",
                "5" if row["cost_bps"] == 5.0 else "10",
                row["cost_bps"],
            )
        row["dsr"] = dsr_for(all_daily[key], sharpes)

    dsr_primary = dsr_for(res5.daily_returns, sharpes)
    pbo = cscv_pbo(cscv_matrix(all_daily), n_splits=10)

    years = year_table(res5.daily_returns, qqq_dummy, bil_dummy)
    gates = gate_table(
        primary,
        qqq,
        bil,
        years,
        placebo_event_p=placebo_event["p_value"],
        placebo_portfolio_p=placebo_path["p_value"],
        dsr=dsr_primary,
        max_concurrent=res5.max_concurrent,
    )
    primary_pass = bool(gates["pass"].all()) if len(gates) else False
    verdict = "paper" if primary_pass else "reject"

    # Secondary rescue check, using the pre-registered gates.
    ps = [row["placebo_p"] for row in all_rows]
    qs = bh_qvalues(ps)
    for row, q in zip(all_rows, qs, strict=True):
        row["bh_q"] = q
    rescue = None
    for row in long_rows:
        if row["cost_bps"] != 10.0:
            continue
        if not (
            row["after_tax_multiple"] > qqq["after_tax_multiple_2x"]
            and row["after_tax_multiple"] > bil["after_tax_multiple_2x"]
        ):
            continue
        if not (
            row["placebo_p"] < 0.05 / N_TRIALS
            and row["bh_q"] < 0.05
            and row["dsr"] >= 0.95
        ):
            continue
        # Adjacent hold must also clear QQQ at 2x.
        adj = [
            r
            for r in long_rows
            if r["reaction"] == row["reaction"]
            and r["cost_bps"] == 10.0
            and r["hold"] != row["hold"]
            and abs(HOLDS.index(r["hold"]) - HOLDS.index(row["hold"])) == 1
        ]
        if not any(r["after_tax_multiple"] > qqq["after_tax_multiple_2x"] for r in adj):
            continue
        cand = simulate(
            timing,
            bars,
            hold=row["hold"],
            cost_bps=10.0,
            reaction=row["reaction"],
            cash_yield=True,
        )
        p_path = placebo_portfolio(
            timing,
            bars,
            cand,
            hold=row["hold"],
            reaction=row["reaction"],
            cost_bps=10.0,
            n=PLACEBO_N,
        )
        if p_path["p_value"] >= 0.05:
            continue
        cand_years = year_table(cand.daily_returns, qqq_dummy, bil_dummy)
        cg = gate_table(
            {
                **row,
                "after_tax_multiple_2x": row["after_tax_multiple"],
            },
            {**qqq, "after_tax_multiple": qqq["after_tax_multiple_2x"]},
            {**bil, "after_tax_multiple": bil["after_tax_multiple_2x"]},
            cand_years,
            placebo_event_p=row["placebo_p"],
            placebo_portfolio_p=p_path["p_value"],
            dsr=row["dsr"],
            max_concurrent=cand.max_concurrent,
        )
        if cg["pass"].all():
            rescue = row
            row["verdict"] = "paper"
            break

    # Long-short borrow sensitivity for a possible rescue is reported even if
    # no long-only secondary survives.
    ls_borrow = None
    if any(
        r["family"] == "long_short"
        and r["cost_bps"] == 10.0
        and r["after_tax_multiple"] > qqq["after_tax_multiple_2x"]
        for r in other_rows
    ):
        best_ls = max(
            (
                r
                for r in other_rows
                if r["family"] == "long_short" and r["cost_bps"] == 10.0
            ),
            key=lambda r: r["after_tax_multiple"],
        )
        ls = simulate(
            timing,
            bars,
            hold=best_ls["hold"],
            cost_bps=10.0,
            reaction="all_gap",
            long_short=True,
            borrow_bps=1000.0,
            cash_yield=False,
        )
        m = summarize_result(ls, cost_bps=10.0, qqq=qqq_dummy, bil=bil_dummy)
        ls_borrow = {
            "hold": best_ls["hold"],
            "after_tax_multiple": m["after_tax_multiple"],
        }

    for row in all_rows:
        row.setdefault("verdict", "reject")
        row["notes"] = (
            f"signals {row.get('n_signals', row.get('n_trades', 0))}; "
            f"avg invested {row.get('avg_invested', float('nan')):.1%}; "
            f"max concurrent {row.get('max_concurrent', 0)}"
        )
    # The primary row is the long-only gap/hold5/cost5 row.
    for row in long_rows:
        if row["reaction"] == "gap" and row["hold"] == 5 and row["cost_bps"] == 5.0:
            row["verdict"] = verdict
            row["notes"] = "pre-registered primary"

    out_trials = ROOT / "results" / "pead_trials.csv"
    fields = [
        "id",
        "family",
        "reaction",
        "hold",
        "cost_bps",
        "mode",
        "n_events",
        "n_trades",
        "mean_event_net",
        "event_hit",
        "after_tax_multiple",
        "after_tax_cagr",
        "maxdd",
        "sharpe",
        "placebo_p",
        "bh_q",
        "dsr",
        "skipped_slots",
        "skipped_active",
        "skipped_cash",
        "avg_positions",
        "avg_invested",
        "max_concurrent",
        "cash_yield",
        "verdict",
        "notes",
    ]
    with out_trials.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for i, row in enumerate(all_rows, start=1):
            clean = {k: row.get(k, "") for k in fields}
            clean["id"] = f"pead-{i:03d}"
            for k, v in list(clean.items()):
                if isinstance(v, float) and not np.isfinite(v):
                    clean[k] = ""
            writer.writerow(clean)

    write_report(
        raw=raw,
        timing=timing,
        res5=res5,
        res10=res10,
        evs5=evs5,
        evs10=evs10,
        primary=primary,
        primary5=primary5,
        qqq=qqq,
        bil=bil,
        years=years,
        gates=gates,
        placebo_event=placebo_event,
        placebo_path=placebo_path,
        boot_qqq=boot_qqq,
        boot_bil=boot_bil,
        dsr_primary=dsr_primary,
        pbo=pbo,
        long_rows=long_rows,
        other_rows=other_rows,
        wf_rows=wf_rows,
        wf_records=wf_records,
        sharpes=sharpes,
        verdict=verdict,
        rescue=rescue,
        ls_borrow=ls_borrow,
    )
    print(f"verdict: {verdict}, wrote {out_trials}")


def write_report(**kw) -> None:
    raw = kw["raw"]
    timing = kw["timing"]
    res5 = kw["res5"]
    res10 = kw["res10"]
    evs5 = kw["evs5"]
    evs10 = kw["evs10"]
    primary = kw["primary"]
    primary5 = kw["primary5"]
    qqq = kw["qqq"]
    bil = kw["bil"]
    years = kw["years"]
    gates = kw["gates"]
    placebo_event = kw["placebo_event"]
    placebo_path = kw["placebo_path"]
    boot_qqq = kw["boot_qqq"]
    boot_bil = kw["boot_bil"]
    dsr_primary = kw["dsr_primary"]
    pbo = kw["pbo"]
    long_rows = kw["long_rows"]
    other_rows = kw["other_rows"]
    wf_rows = kw["wf_rows"]
    wf_records = kw["wf_records"]
    sharpes = kw["sharpes"]
    verdict = kw["verdict"]
    rescue = kw["rescue"]
    ls_borrow = kw["ls_borrow"]

    lines: list[str] = []
    lines.append("# PEAD: 8-K item 2.02 earnings drift (v11 A)")
    lines.append("")
    lines.append(
        "Generated by `uv run python scripts/research_pead.py`. "
        "Protocol: `docs/pead_protocol.md`. Data snapshot 2026-11-25; "
        "cache-only, no network."
    )
    lines.append("")
    lines.append(f"## Verdict: **{verdict}**")
    lines.append("")
    if verdict == "reject":
        lines.append(
            "The pre-registered primary fails at least one gate. The 8-K 2.02 "
            "PEAD family is rejected unless a registered secondary clears every "
            "pre-declared rescue condition. No live or default change."
        )
    else:
        lines.append(
            "The pre-registered primary clears every gate. This is a paper "
            "candidate only; no live or default change."
        )
    if rescue is not None:
        lines.append(
            f"Rescued by secondary `{rescue['reaction']}` hold {rescue['hold']} "
            f"at 2x costs; see gates."
        )
    lines.append("")
    lines.append("## Sample")
    lines.append("")
    lines.append(
        f"- Item 2.02 events after exact/24h de-duplication (2014+): **{len(raw)}**."
    )
    lines.append(
        "- The un-deduplicated cache has 3,441 events back to 2009; the 2014+ "
        "sample starts where cached daily bars start. Companion 2.02 filings "
        "within 24 hours are one event."
    )
    lines.append(
        f"- Usable timed events with a cached entry and full history: "
        f"**{len(timing)}** across **{timing['ticker'].nunique()}** tickers."
    )
    lines.append(
        f"- Primary eligible events (positive gap): **{evs5['n']}**; "
        f"portfolio entries **{res5.n_entries}**; "
        f"skipped by slots **{res5.skipped_slots}**, overlap "
        f"**{res5.skipped_active}**, cash **{res5.skipped_cash}**."
    )
    lines.append(
        f"- Window: **{res5.equity.index[0].date()} to {res5.equity.index[-1].date()}**."
    )
    lines.append(
        "- Universe is the 86 current large-cap filers present in the SEC cache "
        "with cached bars. This is not a point-in-time index; survivorship is a "
        "stated limitation."
    )
    lines.append("")
    lines.append("## Primary result (gap > 0, 5-session hold, max 10)")
    lines.append("")
    lines.append(
        "| book | after-tax 1x | after-tax 2x | CAGR 1x | maxDD 1x | worst 12m | Sharpe 1x |"
    )
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    lines.append(
        f"| PEAD primary | **{mult(primary['after_tax_multiple'])}** | "
        f"{mult(primary['after_tax_multiple_2x'])} | "
        f"{pct(primary['after_tax_cagr'])} | {pct(primary['maxdd'])} | "
        f"{pct(primary['worst_12m'])} | {primary['sharpe']:.2f} |"
    )
    lines.append(
        f"| QQQ buy & hold | {mult(qqq['after_tax_multiple'])} | "
        f"{mult(qqq['after_tax_multiple_2x'])} | "
        f"{pct(qqq['after_tax_cagr'])} | {pct(qqq['maxdd'])} | "
        f"{pct(qqq['worst_12m'])} | {qqq['sharpe']:.2f} |"
    )
    lines.append(
        f"| BIL buy & hold | {mult(bil['after_tax_multiple'])} | "
        f"{mult(bil['after_tax_multiple_2x'])} | "
        f"{pct(bil['after_tax_cagr'])} | {pct(bil['maxdd'])} | "
        f"{pct(bil['worst_12m'])} | {bil['sharpe']:.2f} |"
    )
    lines.append("")
    lines.append(
        f"Event-level net mean: **{pct(evs5['mean_net'], 2)}** "
        f"(t {evs5['t']:.2f}, hit {evs5['hit']:.1%}, "
        f"95% CI {pct(evs5['ci_low'], 2)} to {pct(evs5['ci_high'], 2)}); "
        f"at 2x costs **{pct(evs10['mean_net'], 2)}**. "
        f"Average invested {primary['avg_invested']:.1%}, average positions "
        f"{primary['avg_positions']:.1f}, cumulative cash interest "
        f"{pct(primary['cash_yield'], 2)} of initial capital."
    )
    lines.append("")
    lines.append("### Calendar years (pre-tax net, approximate after-tax)")
    lines.append("")
    lines.append("| year | strategy | after-tax | QQQ | BIL | vs BIL |")
    lines.append("|---:|---:|---:|---:|---:|---:|")
    for row in years.itertuples(index=False):
        lines.append(
            f"| {row.year} | {pct(row.strategy_pretax)} | "
            f"{pct(row.strategy_after_tax)} | {pct(row.qqq)} | "
            f"{pct(row.bil)} | {pct(row.excess_bil)} |"
        )
    lines.append("")
    lines.append("## Placebo, DSR, CSCV")
    lines.append("")
    lines.append(
        f"- Event-level placebo p: **{fmt_p_tight(placebo_event['p_value'])}** "
        f"(actual mean {pct(placebo_event['actual_mean'], 2)} vs placebo mean "
        f"{pct(placebo_event['placebo_mean'], 2)}; 500 random entries matched "
        f"by ticker and reaction filter)."
    )
    lines.append(
        f"- Portfolio placebo p: **{fmt_p_tight(placebo_path['p_value'])}** "
        f"(actual terminal {pct(placebo_path['actual_return'])} vs placebo p95 "
        f"{pct(placebo_path['placebo_p95'])})."
    )
    lines.append(
        f"- DSR with {N_TRIALS} trials: **{dsr_primary:.3f}** "
        f"(trial Sharpe dispersion {np.std(sharpes, ddof=1):.4f} daily)."
    )
    lines.append(
        f"- CSCV PBO: **{pbo.pbo:.3f}** over {pbo.n_combinations} splits, "
        f"{pbo.n_trials} usable trials."
    )
    lines.append(
        f"- Paired block bootstrap (5,000 paths, 21-session blocks): "
        f"strategy p5 terminal **{mult(boot_qqq['p5_strategy'])}**, p5 maxDD "
        f"**{pct(boot_qqq['p5_maxdd'])}**; P(strategy > QQQ) "
        f"**{boot_qqq['prob_beat']:.3f}**, P(strategy > BIL) "
        f"**{boot_bil['prob_beat']:.3f}**."
    )
    lines.append("")
    lines.append("## Pre-registered gates")
    lines.append("")
    lines.append("| gate | value | hurdle | pass |")
    lines.append("|---|---:|---:|:---:|")
    for row in gates.to_dict("records"):
        val = row["value"]
        hur = row["hurdle"]
        if row["gate"] in (
            "event placebo p<0.05",
            "portfolio placebo p<0.05",
            "DSR>=0.95",
        ):
            vs, hs = fmt_p(val), fmt_p(hur)
        elif "years" in row["gate"]:
            vs, hs = pct(val, 0), pct(hur, 0)
        elif "after-tax" in row["gate"]:
            vs, hs = mult(val), mult(hur)
        else:
            vs, hs = f"{val:.3g}", f"{hur:.3g}"
        lines.append(
            f"| {row['gate']} | {vs} | {hs} | {'PASS' if row['pass'] else 'FAIL'} |"
        )
    lines.append("")
    lines.append("## Registered secondary grid")
    lines.append("")
    lines.append(
        "All 50 trials are in `results/pead_trials.csv`; failures included. "
        "The table shows the long-only reaction/hold grid at 2x costs plus the "
        "other families. `p` is the event-level placebo p-value; `q` is BH "
        "across all 50 trials."
    )
    lines.append("")
    lines.append(
        "| family | reaction | hold | 2x after-tax | CAGR | maxDD | p | q | DSR |"
    )
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|")
    for row in long_rows:
        if row["cost_bps"] != 10.0:
            continue
        lines.append(
            f"| long | {row['reaction']} | {row['hold']} | "
            f"{mult(row['after_tax_multiple'])} | {pct(row['after_tax_cagr'])} | "
            f"{pct(row['maxdd'])} | {fmt_p_tight(row['placebo_p'])} | "
            f"{fmt_p_tight(row['bh_q'])} | {row['dsr']:.3f} |"
        )
    for row in other_rows:
        if row["cost_bps"] != 10.0:
            continue
        lines.append(
            f"| {row['family']} | {row['reaction']} | {row['hold']} | "
            f"{mult(row['after_tax_multiple'])} | {pct(row['after_tax_cagr'])} | "
            f"{pct(row['maxdd'])} | {fmt_p_tight(row['placebo_p'])} | "
            f"{fmt_p_tight(row['bh_q'])} | {row['dsr']:.3f} |"
        )
    for row in wf_rows:
        if row["cost_bps"] != 10.0:
            continue
        lines.append(
            f"| walk_forward | selected | — | {mult(row['after_tax_multiple'])} | "
            f"{pct(row['after_tax_cagr'])} | {pct(row['maxdd'])} | — | — | "
            f"{row['dsr']:.3f} |"
        )
    lines.append("")
    lines.append("### Walk-forward selections")
    lines.append("")
    lines.append("| year | chosen | train Sharpe | test return |")
    lines.append("|---:|---|---:|---:|")
    for rec in wf_records:
        lines.append(
            f"| {rec['year']} | {rec['chosen']} | {rec['train_sharpe']:.2f} | "
            f"{pct(rec['test_return'])} |"
        )
    lines.append("")
    lines.append("## Why this is credible")
    lines.append("")
    lines.append(
        "- Event timestamps are SEC acceptance times in New York; entries are "
        "strictly after the event is known. No same-close or same-bar fill."
    )
    lines.append(
        "- Reaction measures use only bars completed before the entry open; "
        "tests mutate future bars and verify unchanged entries/reactions."
    )
    lines.append(
        "- Duplicate/companion 2.02 filings within 24 hours are collapsed before "
        "any return is computed."
    )
    lines.append(
        "- Costs are 5 bps/side base and 10 bps/side 2x on every entry and exit; "
        "PFU is year-dependent with annual netting and a 10-year loss carry."
    )
    lines.append(
        "- The strategy pays annual PFU because positions are realised within "
        "days. QQQ and BIL are buy-and-hold benchmarks, so their PFU is charged "
        "on the terminal gain only, matching the repo's `france.after_tax` "
        "convention. That is the harder (not easier) comparison for the strategy."
    )
    lines.append(
        "- Idle cash earns BIL, so the primary is not penalized for holding cash; "
        "the zero-cash-yield version is also registered."
    )
    lines.append(
        "- Every variant and failed fold is in the CSV; DSR uses 50 trials and "
        "CSCV uses the full T x 50 matrix."
    )
    lines.append("")
    lines.append("## Limitations")
    lines.append("")
    lines.append(
        "- The universe is current large-cap filers with cached bars, not a "
        "point-in-time index. Survivorship can flatter any long-only earnings "
        "strategy."
    )
    lines.append(
        "- Daily bars cannot model the opening auction, queue position or "
        "intraday execution. Entry at the printed open is an assumption."
    )
    lines.append(
        "- After-tax annual approximation for the year table ignores loss-carry "
        "path dependence; the headline after-tax multiple uses the full path."
    )
    lines.append(
        "- Long-short ignores short-dividend payments and assumes 500 bps borrow, "
        "with a 1,000 bps sensitivity only reported if it is a candidate."
    )
    if ls_borrow is not None:
        lines.append(
            f"- Best long-short at 1,000 bps borrow, hold {ls_borrow['hold']}: "
            f"{mult(ls_borrow['after_tax_multiple'])} after tax."
        )
    lines.append("")
    lines.append("## What would change the verdict")
    lines.append("")
    lines.append(
        "- A point-in-time earnings-event sample with delisted/acquired names, "
        "not the current-filer cache."
    )
    lines.append(
        "- A primary that beats QQQ and BIL after tax at 2x costs with a placebo "
        "p below 0.05, DSR above 0.95 and no single year carrying the result."
    )
    lines.append(
        "- Intraday execution data showing the opening print is fillable at the "
        "size implied by 10% positions."
    )
    lines.append("")
    lines.append("_No live/real-money code, no default change._")
    lines.append("")

    out = ROOT / "results" / "pead.md"
    out.write_text("\n".join(lines))
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
