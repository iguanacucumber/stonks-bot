"""Start-date robustness under different IBKR FX assumptions.

Same 84 five-year windows as `robustness_best.py`, but across monthly/tax books
and 2/3/25 bps FX. Answers: does the 84/84 claim survive the FX caveat, and is
the 25 bps tax-book fallback actually robust?

Run: uv run python scripts/robustness_fx.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from bot import backtest, france, markets, strategy, universe  # noqa: E402

BASE = dict(
    leverage=2.5,
    financing_bps=500.0,
    trend_ma=200,
    trend_scale=0.5,
    rebalance_mode="calendar",
    exec_price="open",
    cost_bps=5.0,
    tax_lots="hifo",
)
STARTS = pd.date_range("2015-01-01", "2021-12-01", freq="MS")
COMBOS = [
    ("monthly", 2.0),
    ("monthly", 3.0),
    ("monthly", 25.0),
    ("tax", 2.0),
    ("tax", 25.0),
]


def bootstrap(rets: np.ndarray, block: int = 21, n: int = 1000, seed: int = 11):
    rng = np.random.default_rng(seed)
    m = len(rets)
    nb = int(np.ceil(m / block))
    finals = np.empty(n)
    dds = np.empty(n)
    for k in range(n):
        starts = rng.integers(0, m, nb)
        idx = (starts[:, None] + np.arange(block)[None, :]).ravel() % m
        path = np.cumprod(1.0 + rets[idx][:m])
        finals[k] = path[-1]
        peak = np.maximum.accumulate(path)
        dds[k] = (path / peak - 1.0).min()
    return finals, dds


def main() -> None:
    panel, mem = markets.build("TECH")
    universe.canonical = lambda t: t
    dates = pd.DatetimeIndex(panel["date"].drop_duplicates().sort_values())

    windows = []
    for s in STARTS:
        s0 = dates[dates >= s]
        if len(s0) == 0:
            continue
        start = s0[0]
        end = min(start + pd.DateOffset(years=5), dates[-1])
        bcfg = strategy.Config(
            start=str(start.date()), end=str(end.date()), core=None, leverage=1.0
        )
        bmkt = strategy.Market(panel, mem, bcfg)
        spy = backtest.spy_result(bmkt)
        gain = float(spy.equity.iloc[-1] / spy.equity.iloc[0] - 1.0)
        fx = france.fx_factor("TECH", spy.equity.index[0], spy.equity.index[-1])
        windows.append((start, end, (1.0 + max(0.0, gain) * 0.686) * fx))

    rows = []
    for profile, fx_bps in COMBOS:
        kw = dict(BASE)
        kw.update(france.kwargs("TECH", "Interactive Brokers", "cto", 10000, panel))
        kw["fx_bps"] = fx_bps
        if profile == "tax":
            kw.update(core_mode="always", leverage_band=0.30)
        bots, dds = [], []
        for start, end, bench in windows:
            cfg = strategy.Config(start=str(start.date()), end=str(end.date()), **kw)
            res = backtest.run(strategy.Market(panel, mem, cfg))
            f = france.after_tax(res, 10000, "TECH")
            bots.append(f["after_tax_eur"] / 10000)
            dds.append(f["maxdd"])
        full = backtest.run(
            strategy.Market(panel, mem, strategy.Config(start="2015-01-01", **kw))
        )
        ffull = france.after_tax(full, 10000, "TECH")
        rets = full.equity.pct_change().dropna().to_numpy()
        finals, bdds = bootstrap(rets)
        bots = np.array(bots)
        dds = np.array(dds)
        rows.append(
            {
                "book": profile,
                "fx_bps": fx_bps,
                "win": float((bots > np.array([w[2] for w in windows])).mean()),
                "median": float(np.median(bots)),
                "worst": float(bots.min()),
                "p5": float(np.quantile(bots, 0.05)),
                "full": ffull["after_tax_eur"] / 10000,
                "boot_p5": float(np.quantile(finals, 0.05)),
                "p5_dd": float(np.quantile(bdds, 0.05)),
                "maxdd": ffull["maxdd"],
            }
        )
        print(
            f"{profile:7s} {fx_bps:4.0f}bps win {rows[-1]['win']:.0%} "
            f"median {rows[-1]['median']:.2f}x worst {rows[-1]['worst']:.2f}x "
            f"full {rows[-1]['full']:.2f}x boot_p5 {rows[-1]['boot_p5']:.2f}x"
        )

    df = pd.DataFrame(rows)
    lines = [
        "# Best book robustness under FX assumptions",
        "",
        "84 five-year windows (monthly starts 2015-2021), TECH, after French CTO",
        "tax and IBKR fees. Benchmark: QQQ after the same 31.4% tax and EUR",
        "translation. `monthly` is the default book; `tax` is the core-only",
        "fallback used when FX is expensive.",
        "",
        "| book | FX bps | windows beat QQQ | median | worst | p5 window | full after tax | bootstrap p5 | p5 maxDD |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for _, r in df.iterrows():
        lines.append(
            f"| {r['book']} | {r['fx_bps']:.0f} | **{r['win']:.0%}** | "
            f"{r['median']:.2f}x | {r['worst']:.2f}x | {r['p5']:.2f}x | "
            f"{r['full']:.2f}x | {r['boot_p5']:.2f}x | {r['p5_dd']:.1%} |"
        )
    lines += [
        "",
        "Read: the monthly book's 84/84 claim is an FX claim. At 2 bps it is",
        "84/84; at 25 bps the win rate and median fall, and the core-only tax book",
        "is the better fallback. The tail max drawdown stays near -90% in the",
        "bootstrap regardless of book; FX changes the level, not the crash risk.",
        "",
        "Generated by `uv run python scripts/robustness_fx.py`.",
    ]
    (ROOT / "results" / "robustness_fx.md").write_text("\n".join(lines) + "\n")
    print("wrote results/robustness_fx.md")


if __name__ == "__main__":
    main()
