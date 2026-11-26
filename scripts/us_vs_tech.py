"""Reproduce US vs TECH monthly at IBKR after French tax.

Full window plus 2015/2018/2021 five-year starts. See results/us_vs_tech.md.

Run: uv run python scripts/us_vs_tech.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from bot import backtest, france, markets, strategy  # noqa: E402
from scripts.france import BASE, run_book  # noqa: E402


def bench(mkt, market: str) -> float:
    spy = backtest.spy_result(mkt)
    gain = float(spy.equity.iloc[-1] / spy.equity.iloc[0] - 1.0)
    fx = france.fx_factor(market, spy.equity.index[0], spy.equity.index[-1])
    return (1.0 + max(0.0, gain) * 0.686) * fx


def main() -> None:
    for market in ("US", "TECH"):
        r = run_book(market, "CTO", "Interactive Brokers", 10000, "monthly", "native")
        print(
            f"{market:4s} full after {r['after_tax'] / 10000:.2f}x "
            f"maxDD {r['maxdd']:.1%} fees {r['fees']:.0f} tax {r['tax_paid']:.0f} "
            f"exit {r['exit_tax']:.0f} trades {r['trades']}"
        )
        panel, mem = markets.build(market)
        kw = dict(BASE)
        kw.pop("start", None)
        kw.update(france.kwargs(market, "Interactive Brokers", "cto", 10000, panel))
        for start in ("2015-01-01", "2018-01-01", "2021-01-01"):
            end = min(pd.Timestamp(start) + pd.DateOffset(years=5), panel["date"].max())
            cfg = strategy.Config(start=start, end=str(end.date()), **kw)
            mkt = strategy.Market(panel, mem, cfg)
            res = backtest.run(mkt)
            f = france.after_tax(res, 10000, market)
            print(
                f"{market:4s} {start[:4]} after {f['after_tax_eur'] / 10000:.2f}x "
                f"maxDD {f['maxdd']:.1%} bench {bench(mkt, market):.2f}x "
                f"trades {f['trades']}"
            )


if __name__ == "__main__":
    main()
