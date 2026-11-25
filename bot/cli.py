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


def _load(
    start: str = "2015-01-01",
    end: str | None = None,
    core_weight: float | None = None,
    core_mode: str | None = None,
):
    mem = universe.load_membership()
    panel = data.build_panel(mem)
    kw = {}
    if core_weight is not None:
        kw["core_weight"] = core_weight
    if core_mode:
        kw["core_mode"] = core_mode
    cfg = strategy.Config(start=start, end=end, **kw)
    mkt = strategy.Market(panel, mem, cfg)
    return panel, mem, mkt, cfg


def cmd_ingest(args):
    mem = universe.load_membership()
    print(f"membership rows: {len(mem)}, tickers: {mem['ticker'].nunique()}")
    panel = data.build_panel(mem, force=args.force)
    print(panel.groupby("ticker").size().describe())


def _seed_metrics(seed: int, mkt):
    r = backtest.run(mkt, selector=backtest.random_selector(seed, mkt.cfg.top_n))
    m, _ = backtest.summarize(r, mkt)
    return m


def _random_bench(mkt, seeds: int) -> pd.DataFrame:
    """Serial: a warm run is ~0.2 s, and forking a large pandas process costs more
    than the work it saves (measured: 20 seeds 3.8 s serial vs 7.7 s on 16 workers)."""
    if seeds <= 0:
        return pd.DataFrame()
    return pd.DataFrame([_seed_metrics(s, mkt) for s in range(seeds)])


def cmd_backtest(args):
    panel, mem, mkt, cfg = _load(args.start, args.end, args.core_weight, args.core_mode)
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

    rnd = _random_bench(mkt, args.random_seeds)
    print(
        f"random-20: Sharpe p50 {rnd['sharpe'].median():.2f}  p5 {rnd['sharpe'].quantile(0.05):.2f}  "
        f"p95 {rnd['sharpe'].quantile(0.95):.2f}  CAGR p50 {rnd['cagr'].median():.1%}"
    )

    table = pd.DataFrame(
        {
            "bot": m,
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

    rnd = _random_bench(mkt26, args.random_seeds)

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
    panel, mem, mkt26, cfg = _load("2026-01-02", None, 0.0, "always")
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
    lines = [
        "# stonks-bot — results",
        "",
        "Default: dual-momentum rotation. Hold SOXX while its 6-month total return beats SPY;",
        "otherwise rotate into the S&P momentum sleeve (6 names, 10-day skip). Monthly check,",
        "next-close execution, 10 bps per side, no leverage, no trailing stop, no regime cut.",
        "",
    ]
    for fname, title in [
        ("metrics.csv", "2015-01-02 to 2026-11-24"),
        ("metrics_2026.csv", "2026 only"),
    ]:
        p = RESULTS / fname
        if p.exists():
            df = pd.read_csv(p, index_col=0)
            lines += [f"## {title}", "", report.fmt_table(df), ""]
            dollars = (10000 * (1 + df["total_return"])).round(0)
            lines += ["| strategy | $10,000 becomes |", "|---|---:|"]
            for name, val in dollars.items():
                lines.append(f"| {name} | ${val:,.0f} |")
            lines.append("")

    full = pd.read_csv(RESULTS / "metrics.csv", index_col=0)
    y26 = pd.read_csv(RESULTS / "metrics_2026.csv", index_col=0)
    b, s0 = full.loc["bot"], full.loc["spy"]
    b26, s26 = y26.loc["bot"], y26.loc["spy"]
    lines += [
        "## Verdict",
        "",
        f"- Full window: bot ${10000 * (1 + b['total_return']):,.0f} vs SPY "
        f"${10000 * (1 + s0['total_return']):,.0f} "
        f"({10000 * (b['total_return'] - s0['total_return']):+,.0f}).",
        f"- 2026: bot ${10000 * (1 + b26['total_return']):,.0f} vs SPY "
        f"${10000 * (1 + s26['total_return']):,.0f} "
        f"({10000 * (b26['total_return'] - s26['total_return']):+,.0f}, "
        f"{(b26['total_return'] - s26['total_return']):+.2%}).",
        f"- Max drawdown: bot {b['max_dd']:.1%} vs SPY {s0['max_dd']:.1%} (full); "
        f"{b26['max_dd']:.1%} vs {s26['max_dd']:.1%} (2026).",
        f"- Sharpe: bot {b['sharpe']:.2f} vs SPY {s0['sharpe']:.2f} (full); "
        f"{b26['sharpe']:.2f} vs {s26['sharpe']:.2f} (2026).",
        f"- Paired excess return: {b['diff_ann']:+.1%}/yr, 95% CI "
        f"{b['diff_lo']:+.1%} to {b['diff_hi']:+.1%}. The 95% CI excludes zero on one",
        "  path; treat it as evidence, not a guarantee.",
        "",
        "## Caveats",
        "",
        "- SOXX is a semiconductor ETF: most of the return is one industry's 11-year run, not alpha.",
        "- SOXX buy-and-hold made $140,256 with a -45.8% drawdown; the rotation adds ~$72k",
        "  and cuts the drawdown to -39.5%, but the core is still one industry.",
        "- 2022 was still -13.3% and the full-window drawdown is -39.5% vs SPY -33.7%.",
        "- No trailing stop and no regime cut: the ten rounds traded crash protection for return.",
        "- Ten rounds were tuned on this one price path; 2023-2026 was held back as a check and won.",
        "- Exact costs still leave $181,466 at 30 bps; turnover is 5.4x NAV/yr.",
        "- Paper only. No orders, no broker, no keys.",
        "",
    ]
    (RESULTS / "summary.md").write_text("\n".join(lines))
    print(f"wrote {RESULTS}/summary.md")


def cmd_paper(args):
    panel, mem, mkt, cfg = _load("2026-01-02", None, args.core_weight, args.core_mode)
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
        print(
            f"\n[{pd.Timestamp.now():%H:%M:%S}] signal {d.date()}  "
            f"SPY {prices.get('SPY', float('nan')):.2f}  (paper, no orders)"
        )
        stops = {}
        for t in targets:
            p = prices.get(t)
            peak = mkt.px[t].loc[:d].tail(60).max() if t in mkt.px else float("nan")
            protected = {cfg.core, *cfg.fallback_sectors}
            stops[t] = bool(
                p
                and pd.notna(peak)
                and p < peak * (1 - cfg.stop)
                and t not in protected
            )
        shown = {t: (0.0 if stops[t] else w) for t, w in targets.items()}
        for t, w in sorted(shown.items(), key=lambda x: -x[1]):
            p = prices.get(t)
            chg = (
                (p / last_close[t] - 1)
                if p and t in last_close and pd.notna(last_close[t])
                else float("nan")
            )
            flag = " STOP -> exit" if stops[t] else ""
            if p:
                print(
                    f"  {t:6s} target {w:6.2%}  last {p:9.2f}  vs close {chg:+6.2%}{flag}"
                )
            else:
                print(f"  {t:6s} target {w:6.2%}  no quote")
        print(f"  CASH   {1 - sum(shown.values()):6.2%}")
        row = {
            "time": str(pd.Timestamp.now()),
            "signal_date": str(d.date()),
            "targets": json.dumps({k: round(v, 6) for k, v in shown.items()}),
            "prices": json.dumps({k: round(v, 4) for k, v in prices.items()}),
            "stops": json.dumps(stops),
        }
        pd.DataFrame([row]).to_csv(
            path, mode="a", header=not path.exists(), index=False
        )
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
    pb.add_argument("--core-weight", type=float, default=None)
    pb.add_argument("--core-mode", default=None)
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
    pp.add_argument("--core-weight", type=float, default=None)
    pp.add_argument("--core-mode", default=None)
    pp.set_defaults(func=cmd_paper)

    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
