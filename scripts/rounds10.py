"""Regenerate the ten-round table in results/rounds10.md.

Each row changes one knob from the row above. Baseline spells out every knob so the
chain does not depend on Config defaults. Run:
    .venv/bin/python scripts/rounds10.py
"""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from bot import backtest, data, strategy, universe  # noqa: E402

BASE = dict(
    core="VGT",
    fallback="momentum",
    core_weight=1.0,
    core_mode="relative",
    core_lookback=252,
    top_n=20,
    lookback=126,
    skip=21,
    vol_window=20,
    max_weight=0.06,
    sector_cap=0.30,
    gross=0.98,
    regime_ma=200,
    regime_gross=0.50,
    stop=0.20,
    reentry_days=20,
    min_dollar_vol=5e6,
    cost_bps=10.0,
)

STEPS = [
    ("00 baseline", {}),
    ("01 sleeve breadth 20 -> 5", {"top_n": 5}),
    ("02 core VGT -> SMH", {"core": "SMH"}),
    ("03 core lookback 252 -> 126", {"core_lookback": 126}),
    ("04 drop 20% trailing stop", {"stop": 1.0}),
    ("05 regime gross 0.5 -> 1.0", {"regime_gross": 1.0}),
    ("06 core SMH -> SOXX", {"core": "SOXX"}),
    ("07 gross 0.98 -> 1.0", {"gross": 1.0}),
    ("08 sector cap 0.30 -> 0.40", {"sector_cap": 0.40}),
    ("09 sleeve skip 21 -> 10d", {"skip": 10}),
    ("10 sleeve breadth 5 -> 6", {"top_n": 6}),
]


def stats(panel, mem, start, end, **kw):
    cfg = strategy.Config(start=start, end=end, **kw)
    mkt = strategy.Market(panel, mem, cfg)
    res = backtest.run(mkt)
    m, _ = backtest.summarize(res, mkt)
    return res, m


def main():
    mem = universe.load_membership()
    panel = data.build_panel(mem)
    cfg = dict(BASE)
    rows = []
    for name, delta in STEPS:
        cfg.update(delta)
        full, mf = stats(panel, mem, "2015-01-01", None, **cfg)
        oos, _ = stats(panel, mem, "2023-01-02", None, **cfg)
        y26, _ = stats(panel, mem, "2026-01-02", None, **cfg)
        cost = stats(panel, mem, "2015-01-01", None, **{**cfg, "cost_bps": 30.0})[0]
        rows.append(
            {
                "name": name,
                "full$": 10000 * full.equity.iloc[-1],
                "cagr": mf["cagr"],
                "sharpe": mf["sharpe"],
                "dd": mf["max_dd"],
                "oos$": 10000 * oos.equity.iloc[-1],
                "y26$": 10000 * y26.equity.iloc[-1],
                "cost30$": 10000 * cost.equity.iloc[-1],
                "delta": json.dumps(delta),
            }
        )
    df = pd.DataFrame(rows).set_index("name")
    out = Path(__file__).resolve().parent.parent / "results" / "rounds10.csv"
    df.to_csv(out)
    print(df.round(4).to_string())
    prev = df["full$"].shift(1)
    assert (df["full$"] > prev).iloc[1:].all(), "chain is not monotone"
    print(f"\nwrote {out}; every round beats the one above")
    print(
        f"final: ${df['full$'].iloc[-1]:,.0f} vs ${df['full$'].iloc[0]:,.0f} baseline"
    )


if __name__ == "__main__":
    main()
