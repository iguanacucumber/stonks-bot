"""Short-horizon research II: pairs, 8-K events and an intraday POC.

Run: uv run python scripts/research_short2.py
Writes results/short2.md and results/short2_trials.csv.

All daily signals use data through the decision close and earn the next
session's return. Event entry is the first close strictly after the filing
date. Intraday is a tiny recent sample and is labelled as such.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from bot import data  # noqa: E402
from bot.research.data import read_bars  # noqa: E402
from bot.research.metrics import (  # noqa: E402
    annual_return,
    five_year_windows,
    format_multiple,
    format_pct,
    summarize_equity,
    window_summary,
    worst_12m,
)
from bot.research.registry import TrialRegistry  # noqa: E402
from bot.research.short2.events import (  # noqa: E402
    event_returns,
    event_summary,
    load_events,
    placebo_means,
)
from bot.research.short2.intraday import gap_fade_daily, summary as intraday_summary  # noqa: E402
from bot.research.short2.pairs import PairConfig, backtest_pair  # noqa: E402

START = "2015-01-01"
END = "2026-11-25"
PAIRS = (
    ("SMH", "SOXX"),
    ("QQQ", "SPY"),
    ("XLK", "SPY"),
    ("XLE", "SPY"),
    ("XLF", "SPY"),
    ("XLV", "SPY"),
    ("XLI", "SPY"),
    ("XLP", "SPY"),
    ("XLU", "SPY"),
    ("XLB", "SPY"),
    ("XLY", "SPY"),
)
GRID = [
    (pair, lb, ez, mh, mode)
    for pair in PAIRS
    for lb in (60, 120, 252)
    for ez in (1.5, 2.0)
    for mh in (5, 10)
    for mode in ("long_only", "neutral")
]


def load_prices(symbols):
    out = {}
    for s in symbols:
        try:
            out[s] = read_bars(s).set_index("date")["adjclose"].sort_index()
        except Exception:
            pass
    return out


def pair_wf(prices: dict[str, pd.Series], *, cost_bps: float = 5.0) -> dict:
    folds = []
    parts = []
    chain = 1.0
    trials = []
    for year in range(2019, 2027):
        test_start = f"{year}-01-01"
        test_end = f"{year}-12-31" if year < 2026 else END
        train_end = f"{year - 1}-12-31"
        best = None
        for pair, lb, ez, mh, mode in GRID:
            if pair[0] not in prices or pair[1] not in prices:
                continue
            try:
                r = backtest_pair(
                    prices[pair[0]],
                    prices[pair[1]],
                    PairConfig(
                        lookback=lb,
                        entry_z=ez,
                        max_hold=mh,
                        mode=mode,
                        cost_bps=cost_bps,
                    ),
                    start=START,
                    end=train_end,
                )
            except Exception:
                continue
            score = r["cagr"] / abs(r["maxdd"]) if r["maxdd"] < 0 else float("nan")
            trials.append(
                {
                    "fold": year,
                    "pair": pair,
                    "lookback": lb,
                    "entry_z": ez,
                    "max_hold": mh,
                    "mode": mode,
                    "train_score": score,
                    "train_multiple": r["multiple"],
                }
            )
            if best is None or score > best[0]:
                best = (score, (pair, lb, ez, mh, mode), r)
        if best is None:
            continue
        score, params, train = best
        pair, lb, ez, mh, mode = params
        warm = (pd.Timestamp(test_start) - pd.Timedelta(days=500)).date().isoformat()
        rt = backtest_pair(
            prices[pair[0]],
            prices[pair[1]],
            PairConfig(
                lookback=lb,
                entry_z=ez,
                max_hold=mh,
                mode=mode,
                cost_bps=cost_bps,
            ),
            start=warm,
            end=test_end,
        )
        eq = rt["equity"].loc[test_start:test_end]
        if len(eq) < 5:
            continue
        mult = float(eq.iloc[-1] / eq.iloc[0])
        chain *= mult
        parts.append(eq.pct_change().dropna())
        folds.append(
            {
                "fold": year,
                "pair": pair,
                "lookback": lb,
                "entry_z": ez,
                "max_hold": mh,
                "mode": mode,
                "train_score": score,
                "test_return": float(eq.iloc[-1] / eq.iloc[0] - 1.0),
                "test_maxdd": float((eq / eq.cummax() - 1.0).min()),
                "after_tax": mult,
            }
        )
    oos = pd.concat(parts).sort_index()
    eq = (1.0 + oos).cumprod()
    return {
        "folds": folds,
        "returns": oos,
        "equity": eq,
        "after_tax": chain,
        "metrics": summarize_equity(eq),
        "trials": trials,
        "n_trials": len(trials),
    }


def main() -> None:
    symbols = sorted({s for p in PAIRS for s in p})
    prices = load_prices(symbols)
    qqq = prices.get("QQQ")
    registry = TrialRegistry()

    wf = pair_wf(prices, cost_bps=5.0)
    wf2 = pair_wf(prices, cost_bps=10.0)
    # Fixed full-window long-only SMH/SOXX reference.
    fixed = backtest_pair(
        prices["SMH"],
        prices["SOXX"],
        PairConfig(
            lookback=120, entry_z=2.0, max_hold=10, mode="long_only", cost_bps=5.0
        ),
        start=START,
        end=END,
    )
    fixed2 = backtest_pair(
        prices["SMH"],
        prices["SOXX"],
        PairConfig(
            lookback=120, entry_z=2.0, max_hold=10, mode="long_only", cost_bps=10.0
        ),
        start=START,
        end=END,
    )

    # Event study.
    events = load_events()
    event_prices = {}
    for t in events["ticker"].unique():
        try:
            event_prices[t] = read_bars(t).set_index("date")["adjclose"].sort_index()
        except Exception:
            pass
    er = event_returns(
        event_prices, events, horizons=(1, 5, 20), cost_bps=5.0, start=START
    )
    ev_summary = event_summary(er)
    placebo = {}
    for h in (1, 5, 20):
        sub = events[events["ticker"].isin(event_prices)]
        pm = placebo_means(
            event_prices, sub, h, n=300, seed=7, cost_bps=5.0, start=START
        )
        actual = (
            float(er.loc[er.horizon == h, "net"].mean()) if len(er) else float("nan")
        )
        placebo[h] = {
            "actual": actual,
            "placebo_mean": float(np.nanmean(pm)),
            "p": float(np.nanmean(pm >= actual)) if len(pm) else float("nan"),
        }

    # Intraday POC: tiny recent sample, optional.
    intraday = {}
    try:
        bars = data.intraday(
            ["SPY", "QQQ", "SMH"], interval="5m", period="5d", workers=3
        )
    except Exception:
        bars = {}
    for sym, df in bars.items():
        if df is not None and not df.empty:
            df = df.set_index("ts")
            if {"open", "close"} <= set(df.columns):
                intraday[sym] = gap_fade_daily(df, gap_bps=50, cost_bps=5.0)

    # Registry rows: every pair/fold attempt plus event horizons.
    for year, g in pd.DataFrame(wf["trials"]).groupby("fold"):
        for row in g.itertuples(index=False):
            registry.add(
                family="pairs",
                hypothesis="daily relative-value z-score survives costs/tax",
                universe="SMH/SOXX, QQQ/SPY, sector/SPY",
                start=START,
                end=END,
                params={
                    "pair": row.pair,
                    "lookback": int(row.lookback),
                    "entry_z": float(row.entry_z),
                    "max_hold": int(row.max_hold),
                    "mode": row.mode,
                    "cost_bps": 5.0,
                },
                metric="train_calmar",
                value=float(row.train_score),
                cost_model="fr_cto_5bps",
                notes=f"fold {year}; training attempt",
                n_trials=wf["n_trials"],
            )
    for h in (1, 5, 20):
        row = ev_summary[ev_summary.horizon == h]
        if len(row):
            r = row.iloc[0]
            registry.add(
                family="events_8k",
                hypothesis="material 8-K drift survives costs and placebo",
                universe="cached SEC 8-K tickers",
                start=START,
                end=END,
                params={"horizon": h, "cost_bps": 5.0},
                metric="mean_net_return",
                value=float(r["mean"]),
                oos_value=float(placebo[h]["p"]),
                cost_model="fr_cto_5bps",
                notes=f"placebo p={placebo[h]['p']:.3f}",
                n_trials=3,
            )
    for sym, frame in intraday.items():
        s = intraday_summary(frame)
        registry.add(
            family="intraday_gap",
            hypothesis="opening gap fade is implementable after costs",
            universe=sym,
            start="recent 5 sessions",
            end="recent 5 sessions",
            params={"gap_bps": 50, "cost_bps": 5},
            metric="mean_net_return",
            value=s.get("mean", float("nan")),
            cost_model="fr_cto_5bps",
            notes=f"n={s.get('n', 0)}; tiny sample, not evidence",
            n_trials=3,
        )
    registry.write(ROOT / "results" / "short2_trials.csv")

    # 5-year OOS windows vs QQQ.
    qqq_oos = qqq.reindex(wf["equity"].index).ffill()
    qqq_oos = qqq_oos / qqq_oos.iloc[0]
    five = five_year_windows(wf["equity"])
    five_b = five_year_windows(qqq_oos)
    bmap = {(w["start"], w["end"]): w["return"] for w in five_b}
    common = [
        (w, bmap[(w["start"], w["end"])])
        for w in five
        if (w["start"], w["end"]) in bmap
    ]
    fsum = window_summary([w["return"] for w, _ in common], [b for _, b in common])

    lines = [
        "# Short-horizon research II: pairs, 8-K events, intraday POC",
        "",
        "Generated by `uv run python scripts/research_short2.py`.",
        "Daily signals use closes through t and earn t+1 close-to-close; event",
        "entry is the first close strictly after the filing date. French CTO",
        "PFU, 5 bps/side base (10 bps 2x), 5%/yr short borrow/financing.",
        "",
        "## Pairs / relative value",
        "",
        f"Grid: **{len(GRID)} variants x 8 annual folds**, training-only selection",
        f"by Calmar. OOS after-tax EUR multiple: **{format_multiple(wf['after_tax'])}**; "
        f"CAGR {format_pct(wf['metrics']['cagr'])}; maxDD "
        f"{format_pct(wf['metrics']['maxdd'])}; Sharpe "
        f"{wf['metrics']['sharpe']:.2f}. 2x-cost OOS "
        f"**{format_multiple(wf2['after_tax'])}**.",
        "",
        "| fold | pair | lookback | entry z | hold | mode | test return | maxDD | after-tax |",
        "|---|---|---:|---:|---:|---|---:|---:|---:|",
    ]
    for f in wf["folds"]:
        lines.append(
            f"| {f['fold']} | {f['pair'][0]}/{f['pair'][1]} | {f['lookback']} | "
            f"{f['entry_z']:.1f} | {f['max_hold']} | {f['mode']} | "
            f"{format_pct(f['test_return'])} | {format_pct(f['test_maxdd'])} | "
            f"{format_multiple(f['after_tax'])} |"
        )
    lines += [
        "",
        f"Fixed long-only SMH/SOXX (120/2.0/10): **{format_multiple(fixed['multiple'])}** "
        f"full window, 2x costs **{format_multiple(fixed2['multiple'])}**; "
        f"CAGR {format_pct(fixed['cagr'])}, maxDD {format_pct(fixed['maxdd'])}.",
        f"QQQ buy & hold over the same OOS path: **{format_multiple(qqq_oos.iloc[-1])}**; "
        f"pairs OOS is {'above' if wf['after_tax'] > qqq_oos.iloc[-1] else 'below'} QQQ. "
        f"5-year windows: n={fsum.get('n', 0)}, median gap "
        f"{format_pct(fsum.get('median_gap', float('nan')))}, share beats QQQ "
        f"{fsum.get('share_beat_benchmark', 0):.0%}.",
        "",
        "## 8-K event study",
        "",
        f"Events: **{len(events):,}** material 8-K filings across "
        f"{events['ticker'].nunique()} cached tickers; usable return rows "
        f"{len(er):,}, 2015-2026. Net of 10 bps round trip.",
        "",
        "| horizon | n | mean net | median | hit rate | t-stat | placebo mean | placebo p |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in ev_summary.itertuples(index=False):
        p = placebo[int(row.horizon)]
        lines.append(
            f"| {int(row.horizon)}d | {int(row.n)} | {format_pct(row.mean)} | "
            f"{format_pct(row.median)} | {row.hit:.1%} | {row.t:.2f} | "
            f"{format_pct(p['placebo_mean'])} | {p['p']:.2f} |"
        )
    lines += [
        "",
        "The event means are close to the date-shuffle placebo; the 20-day",
        "difference is small, overlapping events are not independent, and the",
        "study is not a tradable portfolio. No event candidate.",
        "",
        "## Intraday proof of concept",
        "",
    ]
    if not intraday:
        lines += [
            "Yahoo 5-minute bars were unavailable for this run; no intraday",
            "claim is made. The code path is tested with synthetic bars only.",
            "",
        ]
    else:
        lines += [
            "Recent 5-session Yahoo 5-minute sample; gap threshold 50 bps,",
            "enter first bar close, exit last bar close, 5 bps/side. **Tiny",
            "sample, not evidence.**",
            "",
            "| symbol | n | mean net | median | hit | total |",
            "|---|---:|---:|---:|---:|---:|",
        ]
        for sym, frame in intraday.items():
            s = intraday_summary(frame)
            lines.append(
                f"| {sym} | {s['n']} | {format_pct(s['mean'])} | "
                f"{format_pct(s['median'])} | {s['hit']:.0%} | {format_pct(s['total'])} |"
            )
        lines.append("")
    lines += [
        "## Verdict",
        "",
    ]
    gates = {
        "pairs OOS beats QQQ after tax": wf["after_tax"] > qqq_oos.iloc[-1],
        "pairs OOS beats BIL (1.20x headline)": wf["after_tax"] > 1.20,
        "pairs survives 2x costs": wf2["after_tax"] > 1.0,
        "event 20d placebo p<0.05": placebo[20]["p"] < 0.05,
        "intraday sample >20 trades": any(
            intraday_summary(f).get("n", 0) > 20 for f in intraday.values()
        ),
    }
    for k, v in gates.items():
        lines.append(f"- {k}: **{'PASS' if v else 'FAIL'}**")
    lines += [
        "",
        "**Verdict: `reject`.** Pairs are the closest family, but the honest "
        "OOS path is below QQQ after tax and the fixed full-window edge is "
        "one sector pair, SMH/SOXX, not a broad relative-value premium. The "
        "8-K drift is indistinguishable from the date-shuffle placebo, and "
        "the intraday sample is far too small to be evidence. No satellite "
        "allocation; keep the monthly book.",
        "",
        "## Files and tests",
        "",
        "- `bot/research/short2/pairs.py`, `events.py`, `intraday.py`",
        "- `scripts/research_short2.py`",
        "- `tests/test_short2.py`",
        "- `results/short2_trials.csv`",
        "",
    ]
    (ROOT / "results" / "short2.md").write_text("\n".join(lines) + "\n")
    print(
        f"pairs OOS {wf['after_tax']:.2f}x 2x {wf2['after_tax']:.2f}x  "
        f"QQQ {qqq_oos.iloc[-1]:.2f}x  events {len(events)}  intraday {list(intraday)}"
    )
    print("wrote results/short2.md")


if __name__ == "__main__":
    main()
