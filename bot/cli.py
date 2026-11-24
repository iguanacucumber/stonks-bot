"""CLI: ingest | backtest | study | report | paper."""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

from . import backtest, data, llm, overlay, report, strategy, universe

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "cache"
RESULTS = ROOT / "results"
RESULTS.mkdir(exist_ok=True)


def _load(start: str = "2015-01-01", end: str | None = None):
    mem = universe.load_membership()
    panel = data.build_panel(mem)
    cfg = strategy.Config(start=start, end=end)
    mkt = strategy.Market(panel, mem, cfg)
    return panel, mem, mkt, cfg


def cmd_ingest(args):
    mem = universe.load_membership()
    print(f"membership rows: {len(mem)}, tickers: {mem['ticker'].nunique()}")
    panel = data.build_panel(mem, force=args.force)
    print(panel.groupby("ticker").size().describe())


def cmd_backtest(args):
    panel, mem, mkt, cfg = _load(args.start, args.end)
    print(
        f"dates {mkt.dates[0].date()} -> {mkt.dates[-1].date()}  tickers {mkt.px.shape[1]}"
    )

    res = backtest.run(mkt)
    m, _ = backtest.summarize(res, mkt)
    spy_res = backtest.spy_result(mkt)
    m_spy, _ = backtest.summarize(spy_res, mkt)

    eq_cfg = replace(cfg, rebalance_days=21, top_n=10000, sector_cap=1.0, stop=1.0)
    eq_mkt = strategy.Market(panel, mem, eq_cfg)
    res_eq = backtest.run(eq_mkt, selector=backtest.equal_weight_selector)
    m_eq, _ = backtest.summarize(res_eq, eq_mkt)

    rand = []
    for seed in range(args.random_seeds):
        r = backtest.run(mkt, selector=backtest.random_selector(seed, cfg.top_n))
        mm, _ = backtest.summarize(r, mkt)
        rand.append(mm)
    rnd = pd.DataFrame(rand)
    print(
        f"random-20: Sharpe p50 {rnd['sharpe'].median():.2f}  p5 {rnd['sharpe'].quantile(0.05):.2f}  "
        f"p95 {rnd['sharpe'].quantile(0.95):.2f}  CAGR p50 {rnd['cagr'].median():.1%}"
    )

    table = pd.DataFrame(
        {
            "momentum": m,
            "spy": m_spy,
            "equal_weight": m_eq,
            "random_p50": rnd.median(),
            "random_p5": rnd.quantile(0.05),
            "random_p95": rnd.quantile(0.95),
        }
    ).T
    print(table.round(4).to_string())
    table.to_csv(RESULTS / "metrics.csv")
    res.equity.to_frame("equity").to_csv(RESULTS / "equity.csv")
    res.trades.to_csv(RESULTS / "trades.csv", index=False)
    res.weights.to_csv(RESULTS / "weights.csv")
    print(f"\nwrote {RESULTS}/metrics.csv equity.csv trades.csv weights.csv")


def _bench_2026(panel, mem, cfg, res_llm, res_pl, judgments, cal, args):
    mkt26 = strategy.Market(panel, mem, cfg)
    math = backtest.run(mkt26)
    m_math, _ = backtest.summarize(math, mkt26)
    m_spy, _ = backtest.summarize(backtest.spy_result(mkt26), mkt26)
    m_llm, _ = backtest.summarize(res_llm, mkt26)
    m_pl, _ = backtest.summarize(res_pl, mkt26)

    eq_cfg = replace(cfg, rebalance_days=21, top_n=10000, sector_cap=1.0, stop=1.0)
    eq_mkt = strategy.Market(panel, mem, eq_cfg)
    m_eq, _ = backtest.summarize(
        backtest.run(eq_mkt, selector=backtest.equal_weight_selector), eq_mkt
    )

    rand = []
    for seed in range(args.random_seeds):
        r = backtest.run(mkt26, selector=backtest.random_selector(seed, cfg.top_n))
        mm, _ = backtest.summarize(r, mkt26)
        rand.append(mm)
    rnd = pd.DataFrame(rand)

    table = pd.DataFrame(
        {
            "momentum_2026": m_math,
            "momentum_llm": m_llm,
            "momentum_llm_placebo": m_pl,
            "spy_2026": m_spy,
            "equal_weight_2026": m_eq,
            "random_p50": rnd.median(),
            "random_p5": rnd.quantile(0.05),
            "random_p95": rnd.quantile(0.95),
        }
    ).T
    print(table.round(4).to_string())
    table.to_csv(RESULTS / "metrics_2026.csv")
    res_llm.equity.to_frame("equity").to_csv(RESULTS / "equity_2026_llm.csv")
    math.equity.to_frame("equity").to_csv(RESULTS / "equity_2026_math.csv")

    cal.to_csv(RESULTS / "calibration.csv", index=False)
    if not cal.empty:
        called = cal[cal["direction"] != "none"]
        print(
            f"\nLLM: {len(cal)} judgments, abstain {1 - len(called) / len(cal):.0%}, "
            f"hit {called['correct'].mean():.1%}, "
            f"mean conf {called['confidence'].mean():.2f}"
        )
        report.calibration_chart(cal, RESULTS / "calibration.svg")
    curves = {
        "momentum": math.equity,
        "momentum+LLM": res_llm.equity,
        "momentum+LLM placebo": res_pl.equity,
        "SPY": backtest.spy_result(mkt26).equity,
    }
    report.equity_chart(curves, RESULTS / "equity_2026.svg")
    return table


def cmd_study(args):
    panel, mem, mkt26, cfg = _load("2026-01-02", None)
    math26 = backtest.run(mkt26)
    print(
        f"2026 math: {math26.equity.iloc[-1]:.3f}  holding names {len(overlay.held_names(math26, mkt26.dates[-1]))}"
    )

    print("building LLM judgments...")
    j = overlay.build_judgments(
        mkt26,
        mkt26,
        math26,
        model=args.model,
        max_names=args.max_names,
        placebo=False,
        limit_months=args.limit_months,
    )
    print(f"judgments: {len(j)}")
    ov = llm.make_overlay(j, max_active=cfg.llm_active_share)
    res_llm = backtest.run(mkt26, overlay=ov)

    print("building placebo judgments...")
    jp = overlay.build_judgments(
        mkt26,
        mkt26,
        math26,
        model=args.model,
        max_names=args.max_names,
        placebo=True,
        limit_months=args.limit_months,
    )
    ovp = llm.make_overlay(jp, max_active=cfg.llm_active_share)
    res_pl = backtest.run(mkt26, overlay=ovp)

    cal = overlay.calibration_table(j, mkt26)
    _bench_2026(
        panel, mem, replace(cfg, start="2026-01-02"), res_llm, res_pl, j, cal, args
    )
    print(f"wrote {RESULTS}/metrics_2026.csv calibration.csv equity_2026.svg")


def cmd_report(args):
    lines = ["# stonks-bot — results", "",
             "Long-only, paper-only. Point-in-time S&P 500 membership, Yahoo daily bars,",
             "SEC 8-K events, GDELT macro headlines. Costs 10 bps per side. No shorting.",
             ""]
    for fname, title in [("metrics.csv", "2015–2026, math core vs benchmarks"),
                         ("metrics_2026.csv", "2026, LLM ablation")]:
        p = RESULTS / fname
        if not p.exists():
            continue
        df = pd.read_csv(p, index_col=0)
        lines += [f"## {title}", "", report.fmt_table(df), ""]

    cal_path = RESULTS / "calibration.csv"
    if cal_path.exists():
        cal = pd.read_csv(cal_path)
        called = cal[cal["direction"] != "none"].copy()
        if len(called):
            called["p"] = called["confidence"]
            called["panel"] = np.where(called["direction"] == "up", "outperform", "underperform")
            called["correct"] = called["correct"].astype(bool)
            lines += ["## LLM calibration", "",
                      f"- judgments scored: {len(cal)}",
                      f"- abstain: {1 - len(called)/len(cal):.0%}",
                      f"- calls: {len(called)} ({called['panel'].value_counts().to_dict()})",
                      f"- hit rate: {called['correct'].mean():.1%}",
                      f"- mean confidence: {called['confidence'].mean():.2f}",
                      f"- mean / median 63d excess return on calls: {called['excess'].mean():+.2%} / {called['excess'].median():+.2%}", ""]
            bins = pd.cut(called["p"], [0.5, 0.55, 0.6, 0.65, 0.7, 0.8, 1.0])
            g = called.groupby(bins, observed=True).agg(
                n=("correct", "size"), stated=("p", "mean"), hit=("correct", "mean"))
            lines += [report.fmt_table(g.reset_index(drop=True)), ""]

    rp = RESULTS / "llm_judgments.jsonl"
    pp = RESULTS / "llm_judgments_placebo.jsonl"
    if rp.exists() and pp.exists():
        try:
            a = pd.read_json(rp, lines=True).drop_duplicates(["ticker", "asof"], keep="last")
            b = pd.read_json(pp, lines=True).drop_duplicates(["ticker", "asof"], keep="last")
            j = a.merge(b, on=["ticker", "asof"], suffixes=("_real", "_placebo"))
            if len(j):
                both = j[(j["direction_real"] != "none") & (j["direction_placebo"] != "none")]
                agree = (both["direction_real"] == both["direction_placebo"]).mean() if len(both) else float("nan")
                lines += ["## Leakage audit", "",
                          f"- paired judgments: {len(j)}",
                          f"- both called: {len(both)}",
                          f"- direction agreement: {agree:.0%}" if len(both) else "- no paired calls",
                          f"- real abstain: {(a['direction']=='none').mean():.0%}, "
                          f"placebo abstain: {(b['direction']=='none').mean():.0%}", ""]
        except Exception:
            pass

    p26 = RESULTS / "metrics_2026.csv"
    if p26.exists():
        try:
            t = pd.read_csv(p26, index_col=0)
            mm, ml, mp = t.loc["momentum_2026"], t.loc["momentum_llm"], t.loc["momentum_llm_placebo"]
            lines += ["## Verdict", "",
                      f"- 2026 CAGR: math {mm['cagr']:.1%}, +LLM {ml['cagr']:.1%}, "
                      f"placebo {mp['cagr']:.1%}.",
                      f"- 2026 Sharpe: math {mm['sharpe']:.2f}, +LLM {ml['sharpe']:.2f}, "
                      f"placebo {mp['sharpe']:.2f}.",
                      f"- LLM minus math: {ml['cagr']-mm['cagr']:+.1%} CAGR, "
                      f"{ml['sharpe']-mm['sharpe']:+.2f} Sharpe.",
                      f"- Placebo minus math: {mp['cagr']-mm['cagr']:+.1%} CAGR. "
                      "If placebo matches the real LLM, headlines are not doing the work.", ""]
        except Exception:
            pass

    lines += ["## Interpretation", "",
              "- An 11-year backtest is one path; 2026 is eleven months. Bootstrap intervals are wide.",
              "- The random-20 rows show what luck looks like. A result inside that band is not skill.",
              "- The placebo-LLM arm gets the same tickers and dates but no news. If it matches the",
              "  real LLM arm, headlines added nothing and the model is recalling 2026.",
              "- ~18% of 2015–2026 S&P members have no Yahoo history (acquired/delisted); results are",
              "  survivor-biased and are not a live expectation.",
              "- The paper ledger, run forward from today, is the only uncontaminated test.", ""]
    (RESULTS / "summary.md").write_text("\n".join(lines))
    print(f"wrote {RESULTS}/summary.md")


def cmd_paper(args):
    panel, mem, mkt, cfg = _load("2026-01-02", None)
    d = mkt.dates[-1]
    res = backtest.run(mkt)
    held = overlay.held_names(res, d)
    targets = backtest._default_selector(mkt, d, held, set())
    if args.llm and (RESULTS / "llm_judgments.jsonl").exists():
        j = llm.load_judgments(RESULTS / "llm_judgments.jsonl")
        targets = llm.make_overlay(j, max_active=cfg.llm_active_share)(d, targets, mkt)
        print("paper: LLM overlay applied")
    last_close = mkt.px.loc[d]
    path = RESULTS / "paper_ledger.csv"
    for it in range(args.iterations):
        prices = data.latest_prices(list(targets) + ["SPY"])
        print(f"\n[{pd.Timestamp.now():%H:%M:%S}] signal {d.date()}  "
              f"SPY {prices.get('SPY', float('nan')):.2f}  (paper, no orders)")
        stops = {}
        for t, w in sorted(targets.items(), key=lambda x: -x[1]):
            p = prices.get(t)
            chg = (p / last_close[t] - 1) if p and t in last_close and pd.notna(last_close[t]) else float("nan")
            peak = mkt.px[t].loc[:d].tail(60).max() if t in mkt.px else float("nan")
            hit = bool(p and pd.notna(peak) and p < peak * (1 - cfg.stop))
            stops[t] = hit
            flag = " STOP" if hit else ""
            if p:
                print(f"  {t:6s} target {w:6.2%}  last {p:9.2f}  vs close {chg:+6.2%}{flag}")
            else:
                print(f"  {t:6s} target {w:6.2%}  no quote")
        print(f"  CASH   {1-sum(targets.values()):6.2%}")
        row = {"time": str(pd.Timestamp.now()), "signal_date": str(d.date()),
               "targets": json.dumps({k: round(v, 6) for k, v in targets.items()}),
               "prices": json.dumps({k: round(v, 4) for k, v in prices.items()}),
               "stops": json.dumps(stops)}
        pd.DataFrame([row]).to_csv(path, mode="a", header=not path.exists(), index=False)
        if it < args.iterations - 1:
            time.sleep(args.interval)
    print(f"\nappended {path}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="stonks-bot")
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("ingest")
    pi.add_argument("--force", action="store_true")
    pi.set_defaults(func=cmd_ingest)

    pb = sub.add_parser("backtest")
    pb.add_argument("--start", default="2015-01-01")
    pb.add_argument("--end", default=None)
    pb.add_argument("--random-seeds", type=int, default=100)
    pb.set_defaults(func=cmd_backtest)

    ps = sub.add_parser("study")
    ps.add_argument("--model", default=llm.DEFAULT_MODEL)
    ps.add_argument("--max-names", type=int, default=40)
    ps.add_argument("--limit-months", type=int, default=None)
    ps.add_argument("--random-seeds", type=int, default=50)
    ps.set_defaults(func=cmd_study)

    pr = sub.add_parser("report")
    pr.set_defaults(func=cmd_report)

    pp = sub.add_parser("paper")
    pp.add_argument("--interval", type=int, default=60)
    pp.add_argument("--iterations", type=int, default=1)
    pp.add_argument("--llm", action="store_true")
    pp.set_defaults(func=cmd_paper)

    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
