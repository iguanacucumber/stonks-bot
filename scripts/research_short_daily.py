"""v9 daily short-horizon research: sector reversal, calendar, breakout.

Run: uv run python scripts/research_short_daily.py
Writes results/short_daily.md, results/short_daily_trials.csv and
results/short_daily_tables.md. Cache-only; no network.
"""

from __future__ import annotations

import itertools
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from bot.research.short4.daily import (  # noqa: E402
    SECTOR_ETFS,
    breakout_returns,
    buy_hold_returns,
    calendar_mask,
    calendar_placebo,
    calendar_returns,
    daily_bars,
    sector_closes,
    sector_reversal_returns,
    stats,
)

START = "2015-01-01"
OOS_START = "2019-01-01"
END = "2026-11-25"
COSTS = (5.0, 10.0)
SECTOR_ASSETS = SECTOR_ETFS + ("SPY",)
CAL_ASSETS = ("SPY", "QQQ", "SMH")


def _key(family: str, params: dict) -> str:
    return family + "|" + json.dumps(params, sort_keys=True)


def _params(label: str) -> str:
    return json.dumps(label, sort_keys=True)


def build_variants() -> dict[str, list[dict]]:
    sector = [
        {
            "lookback": lb,
            "hold": hold,
            "top_n": top_n,
            "mode": mode,
        }
        for lb, hold, top_n, mode in itertools.product(
            (1, 5), (1, 5, 10), (2, 3, 5), ("long_only", "neutral")
        )
    ]
    calendar = []
    for asset in CAL_ASSETS:
        for k in (1, 2, 3):
            calendar.append({"asset": asset, "mode": "tom", "k": k})
        calendar.append({"asset": asset, "mode": "month_end", "k": 1})
        for dow in range(5):
            calendar.append({"asset": asset, "mode": "dow", "weekday": dow})
        calendar.append({"asset": asset, "mode": "pre_holiday", "k": 1})
        calendar.append({"asset": asset, "mode": "post_holiday", "k": 1})
    breakout = [
        {"asset": asset, "window": window, "hold": hold, "vol_filter": vol}
        for asset, window, hold, vol in itertools.product(
            CAL_ASSETS, (20, 50), (1, 3, 5), (True, False)
        )
    ]
    return {"sector": sector, "calendar": calendar, "breakout": breakout}


_CLOSES = sector_closes(START)
_CAL_RET = {s: daily_bars(s)["adjclose"].pct_change().fillna(0.0) for s in CAL_ASSETS}
_BREAKOUT_DF = {s: daily_bars(s) for s in CAL_ASSETS}


def run_variant(family: str, params: dict, cost_bps: float):
    if family == "sector":
        return sector_reversal_returns(
            _CLOSES,
            lookback=int(params["lookback"]),
            hold=int(params["hold"]),
            top_n=int(params["top_n"]),
            mode=str(params["mode"]),
            cost_bps=cost_bps,
        )
    if family == "calendar":
        asset = str(params["asset"])
        mask = calendar_mask(
            _CAL_RET[asset].index,
            str(params["mode"]),
            k=int(params.get("k", 1)),
            weekday=(None if params.get("weekday") is None else int(params["weekday"])),
        )
        return calendar_returns(_CAL_RET[asset], mask, cost_bps=cost_bps)
    if family == "breakout":
        return breakout_returns(
            _BREAKOUT_DF[str(params["asset"])],
            window=int(params["window"]),
            hold=int(params["hold"]),
            vol_filter=bool(params["vol_filter"]),
            cost_bps=cost_bps,
        )
    raise ValueError(family)


def label(family: str, p: dict) -> str:
    if family == "sector":
        return f"lb{p['lookback']} h{p['hold']} n{p['top_n']} {p['mode']}"
    if family == "calendar":
        if p["mode"] == "dow":
            return f"{p['asset']} dow{int(p['weekday'])}"
        return f"{p['asset']} {p['mode']} k{p.get('k', 1)}"
    return (
        f"{p['asset']} w{p['window']} h{p['hold']} "
        f"vol{'on' if p['vol_filter'] else 'off'}"
    )


def build_series(
    variants: dict[str, list[dict]],
) -> dict[tuple[str, float], tuple]:
    out = {}
    for family, plist in variants.items():
        for p in plist:
            key = _key(family, p)
            for c in COSTS:
                res = run_variant(family, p, c)
                if family == "breakout":
                    net, active, trades = res
                else:
                    # sector and calendar return (net, turnover, active)
                    net, _, active = res
                    trades = 0
                out[(key, c)] = (net, active, trades)
    return out


def _sharpe(net: pd.Series) -> float:
    sd = float(net.std())
    return float(net.mean() / sd * np.sqrt(252)) if sd > 0 else -1e9


def folds() -> list[dict]:
    out = []
    for year in range(2019, 2027):
        train_end = f"{year - 1}-12-31"
        test_end = min(f"{year}-12-31", END)
        out.append(
            {
                "label": str(year),
                "train_start": START,
                "train_end": train_end,
                "test_start": f"{year}-01-01",
                "test_end": test_end,
            }
        )
    return out


def walk_forward(
    family: str,
    variants: list[dict],
    series: dict,
) -> tuple[pd.Series, pd.Series, pd.Series, list[dict]]:
    oos5, oos10, oos_active = [], [], []
    records = []
    for fold in folds():
        best = None
        for p in variants:
            key = _key(family, p)
            net, _, _ = series[(key, 5.0)]
            train = net.loc[fold["train_start"] : fold["train_end"]]
            if len(train) < 30:
                continue
            score = _sharpe(train)
            if best is None or score > best[0]:
                best = (score, p)
        if best is None:
            continue
        score, p = best
        key = _key(family, p)
        net5, act, _ = series[(key, 5.0)]
        net10, _, _ = series[(key, 10.0)]
        test5 = net5.loc[fold["test_start"] : fold["test_end"]]
        test10 = net10.loc[fold["test_start"] : fold["test_end"]]
        test_act = act.loc[fold["test_start"] : fold["test_end"]]
        oos5.append(test5)
        oos10.append(test10)
        oos_active.append(test_act)
        eq = (1.0 + test5).cumprod()
        records.append(
            {
                "fold": fold["label"],
                "params": p,
                "label": label(family, p),
                "train_sharpe": score,
                "test_return": float(eq.iloc[-1] - 1.0) if len(eq) else float("nan"),
                "test_maxdd": float((eq / eq.cummax() - 1.0).min())
                if len(eq)
                else float("nan"),
                "test_n": int(test_act.sum()) if len(test_act) else 0,
            }
        )
    if not oos5:
        return pd.Series(dtype=float), pd.Series(dtype=float), pd.Series(dtype=bool), []
    return (
        pd.concat(oos5).sort_index(),
        pd.concat(oos10).sort_index(),
        pd.concat(oos_active).sort_index(),
        records,
    )


def benchmark(symbol: str, start: str, end: str = END) -> dict:
    r = buy_hold_returns(symbol, start, end)
    # one round trip, 5 bps each side, charged at the first/last observation
    r = r.copy()
    if len(r):
        r.iloc[0] -= 5.0 / 1e4
        r.iloc[-1] -= 5.0 / 1e4
    return stats(r, pd.Series(True, index=r.index), cost_bps=5.0, label=symbol)


def fmt(x, spec: str) -> str:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return "n/a"
    return "n/a" if not np.isfinite(v) else format(v, spec)


def main() -> None:
    variants = build_variants()
    series = build_series(variants)
    rows = []
    tables = []

    qqq_oos = benchmark("QQQ", OOS_START)
    bil_oos = benchmark("BIL", OOS_START)
    qqq_full = benchmark("QQQ", START)
    bil_full = benchmark("BIL", START)

    wf = {}
    for family, plist in variants.items():
        oos5, oos10, oos_active, recs = walk_forward(family, plist, series)
        wf[family] = {
            "oos5": stats(oos5, oos_active, cost_bps=5.0, label=family),
            "oos10": stats(oos10, oos_active, cost_bps=10.0, label=family),
            "folds": recs,
            "positive_folds": int(sum(1 for r in recs if r["test_return"] > 0)),
        }
        for p in plist:
            key = _key(family, p)
            s5 = stats(
                series[(key, 5.0)][0],
                series[(key, 5.0)][1],
                cost_bps=5.0,
                label=label(family, p),
            )
            s10 = stats(
                series[(key, 10.0)][0],
                series[(key, 10.0)][1],
                cost_bps=10.0,
                label=label(family, p),
            )
            row = {
                "family": family,
                "variant": label(family, p),
                "params_json": json.dumps(p, sort_keys=True),
                "n": s5["n"],
                "full_5x": s5["multiple"],
                "full_10x": s10["multiple"],
                "full_cagr": s5["cagr"],
                "full_maxdd": s5["maxdd"],
                "full_hit": s5["hit"],
                "full_sharpe": s5["sharpe"],
                "full_tax": s5["tax"],
            }
            if family == "calendar":
                net = series[(key, 5.0)][0]
                active = series[(key, 5.0)][1]
                pl = calendar_placebo(net, int(active.sum()), cost_bps=5.0)
                row["placebo_mean"] = pl["mean"]
                row["placebo_p95"] = pl["p95"]
                actual = float(net[active].mean()) if active.any() else float("nan")
                means = pl.get("means")
                row["placebo_p"] = (
                    float((means >= actual).mean())
                    if means is not None
                    else float("nan")
                )
            rows.append(row)
        tables.append((family, plist))

    df = pd.DataFrame(rows)
    df.to_csv(ROOT / "results" / "short_daily_trials.csv", index=False)

    # Full variant tables.
    lines = []
    for family, _ in tables:
        sub = df[df.family == family]
        lines += [
            f"\n## {family}: all {len(sub)} variants (full 2015-2026)\n",
            "| variant | n | 5bps x | 10bps x | CAGR | maxDD | hit | Sharpe | placebo p |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for _, r in sub.iterrows():
            lines.append(
                f"| {r['variant']} | {int(r['n'])} | {r['full_5x']:.2f}x | "
                f"{r['full_10x']:.2f}x | {r['full_cagr']:.1%} | "
                f"{r['full_maxdd']:.1%} | {r['full_hit']:.1%} | "
                f"{r['full_sharpe']:.2f} | {fmt(r.get('placebo_p'), '.2f')} |"
            )
    (ROOT / "results" / "short_daily_tables.md").write_text("\n".join(lines) + "\n")

    # Console/regression summary.
    print(
        f"QQQ OOS {qqq_oos['multiple']:.2f}x  BIL OOS {bil_oos['multiple']:.2f}x  "
        f"QQQ full {qqq_full['multiple']:.2f}x  BIL full {bil_full['multiple']:.2f}x"
    )
    for family, w in wf.items():
        print(
            f"{family:9s} OOS 5bps {w['oos5']['multiple']:.2f}x "
            f"10bps {w['oos10']['multiple']:.2f}x  "
            f"maxDD {w['oos5']['maxdd']:.1%}  hit {w['oos5']['hit']:.1%}  "
            f"positive folds {w['positive_folds']}/{len(w['folds'])}"
        )
        for r in w["folds"]:
            print(
                f"  {r['fold']} {r['label']:24s} test {r['test_return']:+.1%} "
                f"dd {r['test_maxdd']:.1%} trainSR {r['train_sharpe']:.2f}"
            )

    # Append a compact result block to the pre-registered report.
    report = ROOT / "results" / "short_daily.md"
    text = report.read_text().split("# Results", 1)[0]
    out = [text.rstrip(), "", "# Results", ""]
    out += [
        "## Benchmarks and walk-forward summary",
        "",
        f"QQQ OOS (2019-2026): **{qqq_oos['multiple']:.2f}x** after tax; "
        f"BIL **{bil_oos['multiple']:.2f}x**. Full window QQQ "
        f"**{qqq_full['multiple']:.2f}x**, BIL **{bil_full['multiple']:.2f}x**.",
        "",
        "| family | OOS 5bps | OOS 10bps | OOS CAGR | OOS maxDD | OOS hit | positive folds |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for family, w in wf.items():
        out.append(
            f"| {family} | **{w['oos5']['multiple']:.2f}x** | "
            f"**{w['oos10']['multiple']:.2f}x** | {w['oos5']['cagr']:.1%} | "
            f"{w['oos5']['maxdd']:.1%} | {w['oos5']['hit']:.1%} | "
            f"{w['positive_folds']}/{len(w['folds'])} |"
        )
    out += ["", "## Walk-forward folds", ""]
    for family, w in wf.items():
        out += [
            f"### {family}",
            "",
            "| fold | selected | test return | test maxDD | active days | train Sharpe |",
            "|---|---|---:|---:|---:|---:|",
        ]
        for r in w["folds"]:
            out.append(
                f"| {r['fold']} | {r['label']} | {r['test_return']:+.1%} | "
                f"{r['test_maxdd']:.1%} | {r['test_n']} | {r['train_sharpe']:.2f} |"
            )
        out.append("")
    out += [
        "## All variants",
        "",
        "See `results/short_daily_tables.md` for all "
        f"{len(df)} variants, including failures. The CSV is "
        "`results/short_daily_trials.csv`.",
        "",
    ]
    report.write_text("\n".join(out) + "\n")
    print(
        f"wrote {report}, results/short_daily_tables.md, results/short_daily_trials.csv"
    )


if __name__ == "__main__":
    main()
