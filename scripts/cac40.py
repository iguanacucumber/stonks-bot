"""Cross-market check: run the same strategy on a CAC 40 universe.

Not point-in-time index membership: a fixed list of 54 large French names that were CAC 40
members at some point in 2015-2026. Benchmark is CAC.PA (Lyxor/Amundi CAC 40 UCITS ETF,
dividends reinvested, EUR). Core is EXV3.DE (STOXX Europe 600 Technology, EUR) because
France has no SOXX equivalent with a 2015 history.

Run: .venv/bin/python scripts/cac40.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from bot import backtest, data, strategy, universe  # noqa: E402

SECTORS = {
    "AI.PA": "Materials",
    "AIR.PA": "Industrials",
    "MT.AS": "Materials",
    "SAN.PA": "Health Care",
    "BNP.PA": "Financials",
    "SU.PA": "Industrials",
    "OR.PA": "Consumer Staples",
    "MC.PA": "Consumer Discretionary",
    "DG.PA": "Industrials",
    "EL.PA": "Health Care",
    "RMS.PA": "Consumer Discretionary",
    "TTE.PA": "Energy",
    "CS.PA": "Financials",
    "GLE.PA": "Financials",
    "ACA.PA": "Financials",
    "CAP.PA": "Information Technology",
    "SGO.PA": "Industrials",
    "RI.PA": "Consumer Staples",
    "KER.PA": "Consumer Discretionary",
    "LR.PA": "Industrials",
    "SAF.PA": "Industrials",
    "HO.PA": "Industrials",
    "ML.PA": "Consumer Discretionary",
    "ORA.PA": "Communication Services",
    "VIE.PA": "Utilities",
    "ENGI.PA": "Utilities",
    "STMPA.PA": "Information Technology",
    "STLAP.PA": "Consumer Discretionary",
    "RNO.PA": "Consumer Discretionary",
    "PUB.PA": "Communication Services",
    "BN.PA": "Consumer Staples",
    "CA.PA": "Consumer Staples",
    "ALO.PA": "Industrials",
    "UBI.PA": "Communication Services",
    "WLN.PA": "Information Technology",
    "ERF.PA": "Health Care",
    "TEP.PA": "Industrials",
    "SW.PA": "Consumer Discretionary",
    "AC.PA": "Consumer Discretionary",
    "BVI.PA": "Industrials",
    "EDEN.PA": "Industrials",
    "AMUN.PA": "Financials",
    "GTT.PA": "Energy",
    "NEX.PA": "Industrials",
    "FR.PA": "Consumer Discretionary",
    "ATO.PA": "Information Technology",
    "EN.PA": "Industrials",
    "FGR.PA": "Industrials",
    "CO.PA": "Consumer Discretionary",
    "SCR.PA": "Financials",
    "ELIS.PA": "Industrials",
    "GET.PA": "Industrials",
    "RXL.PA": "Industrials",
    "IPS.PA": "Health Care",
    "EXV3.DE": "Technology ETF",
}
NAMES = [t for t in SECTORS if t != "EXV3.DE"]
BENCH = "CAC.PA"
START, END = "2015-01-01", "2026-11-24"
# same config as the US default, only the market changed
CFG = dict(
    core="EXV3.DE",
    core_lookback=126,
    top_n=6,
    skip=10,
    sector_cap=0.40,
    max_weight=0.06,
    gross=1.0,
    stop=1.0,
    regime_gross=1.0,
)


def build():
    parts = []
    for t in NAMES + ["EXV3.DE", BENCH]:
        df = data.fetch_one(t)
        if df.empty:
            continue
        q = df[["date", "adjclose", "close", "volume"]].copy()
        q["ticker"] = "SPY" if t == BENCH else t
        parts.append(q)
    panel = pd.concat(parts, ignore_index=True)
    panel = panel.drop_duplicates(["date", "ticker"]).sort_values(["date", "ticker"])
    mem = pd.DataFrame(
        {"date": [pd.Timestamp("2014-01-01")] * len(NAMES), "ticker": NAMES}
    )
    return panel, mem


def main():
    # Yahoo Europe keeps the dot; the S&P alias map would rewrite "." to "-"
    universe.canonical = lambda t: t
    universe.sector_map = lambda: dict(SECTORS)
    panel, mem = build()

    def one(label, start=START, end=END, **kw):
        cfg = strategy.Config(start=start, end=end, **(CFG | kw))
        mkt = strategy.Market(panel, mem, cfg)
        res = backtest.run(mkt)
        m, _ = backtest.summarize(res, mkt)
        print(
            f"{label:26s} ${10000 * res.equity.iloc[-1]:>9,.0f}  cagr {m['cagr']:6.1%} "
            f"sharpe {m['sharpe']:5.2f}  dd {m['max_dd']:6.1%}"
        )
        return res, m, mkt

    print(f"universe {len(NAMES)} names, {panel.date.nunique()} dates\n")
    one("bot (same config)")
    one("sleeve only", core_weight=0.0)
    cfg = strategy.Config(start=START, end=END)
    mkt = strategy.Market(panel, mem, cfg)
    spy = backtest.spy_result(mkt)
    sm, _ = backtest.summarize(spy, mkt)
    print(
        f"{'CAC 40 total return':26s} ${10000 * spy.equity.iloc[-1]:>9,.0f}  cagr {sm['cagr']:6.1%} "
        f"sharpe {sm['sharpe']:5.2f}  dd {sm['max_dd']:6.1%}"
    )
    eq = backtest.run(mkt, selector=backtest.equal_weight_selector)
    em, _ = backtest.summarize(eq, mkt)
    print(
        f"{'equal weight 54':26s} ${10000 * eq.equity.iloc[-1]:>9,.0f}  cagr {em['cagr']:6.1%} "
        f"sharpe {em['sharpe']:5.2f}  dd {em['max_dd']:6.1%}"
    )

    print("\nwalk-forward")
    for a, b in [
        ("2015-01-02", "2019-12-31"),
        ("2020-01-02", "2022-12-31"),
        ("2023-01-02", None),
    ]:
        res, m, mk = one(f"{a}..{b or 'now'}", start=a, end=b)
        sp = backtest.spy_result(mk)
        smm, _ = backtest.summarize(sp, mk)
        print(f"    CAC {smm['cagr']:6.1%} ${10000 * sp.equity.iloc[-1]:,.0f}")

    print("\n2026")
    one("2026", start="2026-01-02")

    print("\ncosts per side")
    for c in (0, 10, 30, 50):
        one(f"{c} bps", cost_bps=float(c))

    print("\nrandom-6 null (20 seeds, sleeve only)")
    cfg = strategy.Config(start=START, end=END, **(CFG | {"core_weight": 0.0}))
    mkt = strategy.Market(panel, mem, cfg)
    sleeve, sm_, _ = one("sleeve", core_weight=0.0)
    rows = [
        backtest.summarize(
            backtest.run(mkt, selector=backtest.random_selector(s, 6)), mkt
        )[0]
        for s in range(20)
    ]
    rnd = pd.DataFrame(rows)
    pct = (rnd["cagr"] < sm_["cagr"]).mean()
    print(
        f"random-6 median cagr {rnd['cagr'].median():6.1%}  p5 {rnd['cagr'].quantile(0.05):.1%}  "
        f"p95 {rnd['cagr'].quantile(0.95):.1%}  sleeve percentile {pct:.0%}"
    )


if __name__ == "__main__":
    main()
