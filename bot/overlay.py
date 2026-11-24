"""Orchestrates the 2026 news study: candidates, evidence, LLM calls, overlay runs."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from . import backtest, llm, news, strategy, universe

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"
RESULTS.mkdir(exist_ok=True)


def month_end_dates(dates: pd.DatetimeIndex) -> list[pd.Timestamp]:
    s = pd.Series(dates, index=dates)
    g = s.groupby([dates.year, dates.month]).max()
    return [pd.Timestamp(x) for x in g.values]


def held_names(res: backtest.Result, date: pd.Timestamp) -> list[str]:
    if date not in res.weights.index:
        return []
    row = res.weights.loc[date]
    return [t for t, w in row.items() if t != "CASH" and w > 0]


def _existing(path: Path) -> set[tuple]:
    if not path.exists():
        return set()
    out = set()
    for line in path.read_text().splitlines():
        try:
            r = json.loads(line)
            out.add(
                (
                    r["ticker"],
                    r["asof"],
                    bool(r.get("placebo", False)),
                    r.get("model", ""),
                )
            )
        except Exception:
            continue
    return out


def build_judgments(
    mkt,
    mkt26,
    math26,
    model: str,
    max_names: int = 40,
    placebo: bool = False,
    limit_months: int | None = None,
) -> pd.DataFrame:
    path = RESULTS / (
        "llm_judgments_placebo.jsonl" if placebo else "llm_judgments.jsonl"
    )
    seen = _existing(path)
    cmap = news.cik_map()
    months = month_end_dates(mkt26.dates)
    if limit_months:
        months = months[:limit_months]
    prev = pd.Timestamp("2025-12-31")
    total_calls = 0
    for d in months:
        holdings = held_names(math26, d)
        df = strategy.eligible(mkt26, d).head(max_names)
        names = sorted(set(df.index) | set(holdings))
        items = []
        for t in names:
            cik = cmap.get(t) or cmap.get(t.replace("-", "."))
            if not cik:
                continue
            evs = news.events_for(cik, t, prev + pd.Timedelta(days=1), d,
                                  cutoff=d + pd.Timedelta(hours=16))
            if not placebo and evs:
                news.attach_text(evs[-3:])
            ret20 = (
                mkt26.px.at[d, t] / mkt26.px[t].shift(20).loc[d] - 1
                if t in mkt26.px
                else float("nan")
            )
            ret60 = (
                mkt26.px.at[d, t] / mkt26.px[t].shift(60).loc[d] - 1
                if t in mkt26.px
                else float("nan")
            )
            items.append(
                {
                    "ticker": t,
                    "sector": mkt26.sectors.get(t, "Unknown"),
                    "ret20": float(ret20) if pd.notna(ret20) else 0.0,
                    "ret60": float(ret60) if pd.notna(ret60) else 0.0,
                    "vol": float(mkt26.sigma.at[d, t])
                    if t in mkt26.sigma.columns and pd.notna(mkt26.sigma.at[d, t])
                    else 0.0,
                    "events": evs[-3:],
                }
            )
        macro = [] if placebo else news.macro_digest(d - pd.Timedelta(days=25), d)
        for i in range(0, len(items), 8):
            batch = items[i : i + 8]
            todo = [
                it
                for it in batch
                if (it["ticker"], str(d.date()), placebo, model) not in seen
            ]
            if not todo:
                continue
            print(f"  llm {d.date()} batch {i // 8 + 1}: {len(todo)} names", flush=True)
            rows = llm.judge_batch(todo, d, macro, model=model, placebo=placebo)
            if rows:
                with path.open("a") as f:
                    for r in rows:
                        f.write(json.dumps(r) + "\n")
                        seen.add((r["ticker"], r["asof"], placebo, model))
            total_calls += 1
        prev = d
    print(f"llm calls this run: {total_calls}")
    return llm.load_judgments(path)


def calibration_table(judgments: pd.DataFrame, mkt, horizon: int = 63) -> pd.DataFrame:
    rows = []
    for _, r in judgments.iterrows():
        d = pd.Timestamp(r["asof"])
        t = r["ticker"]
        if d not in mkt.px.index or t not in mkt.px.columns:
            continue
        idx = mkt.px.index.get_loc(d)
        j = idx + horizon
        if j >= len(mkt.px) - 1:
            continue
        start, end = mkt.px.at[d, t], mkt.px.iat[j, mkt.px.columns.get_loc(t)]
        spy0, spy1 = mkt.spy.loc[d], mkt.spy.iloc[j]
        if pd.isna(start) or pd.isna(end) or pd.isna(spy0) or pd.isna(spy1):
            continue
        excess = (end / start - 1) - (spy1 / spy0 - 1)
        if r["direction"] == "none":
            rows.append(
                {
                    "ticker": t,
                    "asof": d,
                    "direction": "none",
                    "confidence": 0.0,
                    "excess": excess,
                    "correct": None,
                    "reason": r.get("reason", ""),
                }
            )
        else:
            correct = excess > 0 if r["direction"] == "up" else excess < 0
            rows.append(
                {
                    "ticker": t,
                    "asof": d,
                    "direction": r["direction"],
                    "confidence": float(r["confidence"]),
                    "excess": excess,
                    "correct": bool(correct),
                    "reason": r.get("reason", ""),
                }
            )
    return pd.DataFrame(rows)
