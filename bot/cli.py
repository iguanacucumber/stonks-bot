"""CLI: ingest | backtest | study | report | paper."""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

from . import backtest, data, france, llm, markets, overlay, report, strategy, universe

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "cache"
RESULTS = ROOT / "results"
RESULTS.mkdir(exist_ok=True)
# Shipped default: 2x gross, financed at 5%/yr. `--leverage 1` reverts.
DEFAULT_LEVERAGE = 2.5
DEFAULT_REBALANCE = "calendar"
DEFAULT_EXEC = "open"
DEFAULT_EXEC = "open"
DEFAULT_FINANCING_BPS = 500.0
# Daily risk cut: below the core's 200-day MA, hold half size. It is checked every
# day, not at the monthly rebalance; the monthly version did not work (results/rejected.md).
DEFAULT_TREND_MA = 200
DEFAULT_TREND_SCALE = 0.5
# `monthly` is the researched default. Faster rebalancing is available, but it pays
# far more in costs than it earns: see results/short.md.
PROFILES = {
    "monthly": {},
    # taxable account: no stock sleeve, wider leverage band, fewer realized gains
    "tax": {"core_mode": "always", "leverage_band": 0.30, "tax_lots": "hifo"},
    "weekly": {
        "lookback": 20,
        "skip": 2,
        "rebalance_days": 5,
        "core_lookback": 20,
        "vol_window": 10,
    },
    "daily": {
        "lookback": 10,
        "skip": 1,
        "rebalance_days": 1,
        "core_lookback": 10,
        "vol_window": 5,
    },
}


def _load(
    start: str = "2015-01-01",
    end: str | None = None,
    core_weight: float | None = None,
    core_mode: str | None = None,
    leverage: float | None = None,
    financing_bps: float | None = None,
    market: str = "US",
    profile: str = "monthly",
    core: str | None = None,
    dd_stop: float | None = None,
    trend_ma: int | None = None,
    trend_scale: float | None = None,
    tax_rate: float | None = None,
    state_tax_rate: float | None = None,
    salt_cap: float | None = None,
    wash_sale_days: int | None = None,
    leverage_band: float | None = None,
    rebalance_mode: str | None = None,
    exec_price: str | None = None,
    country: str = "us",
    broker: str | None = None,
    wrapper: str = "cto",
    capital: float = 10000.0,
    limit_bps: float | None = None,
    core_close_fill: bool = False,
):
    profile = profile or "monthly"
    broker = broker or (france.DEFAULT_BROKER if country == "fr" else "none")
    if wrapper == "pea" and market != "FR":
        raise SystemExit("--wrapper pea is only modelled for --market FR")
    # Passing --limit-bps (even 0) opts into the raw-bar high/low panel, so the
    # k=0 baseline is the same honest next-open path as the limit runs.
    panel, mem = markets.build(
        market, with_range=limit_bps is not None, core_close_fill=core_close_fill
    )
    if market != "US":
        # Yahoo Europe/Asia keep the "." in symbols; the S&P alias map does not.
        universe.canonical = lambda t: t
    kw = dict(PROFILES[profile])
    if country == "fr":
        kw.update(france.kwargs(market, broker, wrapper, capital, panel))
    elif broker and broker != "none":
        kw.update(france.broker_kwargs(broker, capital))
    if core:
        kw["core"] = core
    if dd_stop:
        kw["dd_stop"] = dd_stop
    if tax_rate:
        kw["tax_rate"] = tax_rate
    if state_tax_rate:
        kw["state_tax_rate"] = state_tax_rate
    if salt_cap is not None:
        kw["salt_cap"] = salt_cap
    if wash_sale_days is not None:
        kw["wash_sale_days"] = wash_sale_days
    if leverage_band is not None:
        kw["leverage_band"] = leverage_band
    kw["trend_ma"] = DEFAULT_TREND_MA if trend_ma is None else trend_ma
    kw["trend_scale"] = DEFAULT_TREND_SCALE if trend_scale is None else trend_scale
    if core_weight is not None:
        kw["core_weight"] = core_weight
    if core_mode:
        kw["core_mode"] = core_mode
    if leverage is not None:
        kw["leverage"] = leverage
    kw["rebalance_mode"] = (
        DEFAULT_REBALANCE if rebalance_mode is None else rebalance_mode
    )
    kw["exec_price"] = DEFAULT_EXEC if exec_price is None else exec_price
    kw["limit_bps"] = float(limit_bps or 0.0)
    if financing_bps is not None:
        kw["financing_bps"] = financing_bps
    if wrapper == "pea":
        # A PEA cannot borrow and cannot hold the US core.
        kw.update(
            leverage=1.0,
            financing_bps=0.0,
            trend_ma=0,
            core=None,
            core_mode="always",
            leverage_band=0.0,
        )
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
    profile = args.profile or "monthly"
    broker = args.broker or (france.DEFAULT_BROKER if args.country == "fr" else "none")
    panel, mem, mkt, cfg = _load(
        args.start,
        args.end,
        args.core_weight,
        args.core_mode,
        args.leverage,
        args.financing_bps,
        args.market,
        profile,
        args.core,
        args.dd_stop,
        args.trend_ma,
        args.trend_scale,
        args.tax_rate,
        args.state_tax_rate,
        args.salt_cap,
        args.wash_sale_days,
        args.leverage_band,
        args.rebalance_mode,
        args.exec,
        args.country,
        broker,
        args.wrapper,
        args.capital,
        limit_bps=args.limit_bps,
        core_close_fill=args.core_close_fill,
    )
    print(
        f"dates {mkt.dates[0].date()} -> {mkt.dates[-1].date()}  tickers {mkt.px.shape[1]}"
    )

    res = backtest.run(mkt)
    m, _ = backtest.summarize(res, mkt)
    spy_res = backtest.spy_result(mkt)
    m_spy, _ = backtest.summarize(spy_res, mkt)

    eq_cfg = replace(
        cfg, rebalance_days=21, top_n=10000, sector_cap=1.0, stop=1.0, leverage=1.0
    )
    eq_mkt = strategy.Market(panel, mem, eq_cfg)
    res_eq = backtest.run(eq_mkt, selector=backtest.equal_weight_selector)
    m_eq, _ = backtest.summarize(res_eq, eq_mkt)

    rnd = _random_bench(mkt, args.random_seeds)
    cols = {"bot": m, "spy": m_spy, "equal_weight": m_eq}
    if rnd.empty:
        print("random-20: skipped (0 seeds)")
        for label in ("random_p50", "random_p5", "random_p95"):
            cols[label] = pd.Series(np.nan, index=pd.Index(list(m)))
    else:
        print(
            f"random-20: Sharpe p50 {rnd['sharpe'].median():.2f}  "
            f"p5 {rnd['sharpe'].quantile(0.05):.2f}  "
            f"p95 {rnd['sharpe'].quantile(0.95):.2f}  "
            f"CAGR p50 {rnd['cagr'].median():.1%}"
        )
        cols["random_p50"] = rnd.median()
        cols["random_p5"] = rnd.quantile(0.05)
        cols["random_p95"] = rnd.quantile(0.95)
    table = pd.DataFrame(cols).T
    print(table.round(4).to_string())
    if args.country == "fr":
        f = france.after_tax(res, args.capital, args.market)
        f.update(
            market=args.market,
            broker=france.broker_name(broker),
            wrapper=args.wrapper,
            profile="pea" if args.wrapper == "pea" else profile,
        )
        print(
            f"\nFrance {args.wrapper.upper()} · {f['broker']} · "
            f"€{args.capital:,.0f} · {profile} book"
        )
        print(
            f"  after tax {f['after_tax_eur'] / args.capital:.2f}x "
            f"(€{f['after_tax_eur']:,.0f}) · native "
            f"{f['after_tax_native'] / args.capital:.2f}x "
            f"({f['currency']} {f['after_tax_native']:,.0f})"
        )
        print(
            f"  tax paid €{f['tax_paid_eur']:,.0f} · exit tax "
            f"€{f['exit_tax_eur']:,.0f} · fees €{f['fees_eur']:,.0f} "
            f"(incl. FTT €{f['ftt_eur']:,.0f})"
        )
        if f["currency"] != "EUR":
            print(f"  EUR translation {f['fx'] - 1:+.1%} over the window")
        path = RESULTS / "metrics_france.csv"
        row = pd.DataFrame([f])
        if path.exists():
            old = pd.read_csv(path)
            same = pd.Series(True, index=old.index)
            for key in ("market", "wrapper", "broker", "capital", "profile"):
                if key not in old.columns:
                    old[key] = f.get(key)
                same &= old[key] == f[key]
            row = pd.concat([old[~same], row], ignore_index=True)
        row.to_csv(path, index=False)
    # the report is US-only, so a non-US run must not overwrite its inputs
    suffix = "" if args.market == "US" else f"_{args.market.lower()}"
    table.to_csv(RESULTS / f"metrics{suffix}.csv")
    res.equity.to_frame("equity").to_csv(RESULTS / f"equity{suffix}.csv")
    res.trades.to_csv(RESULTS / f"trades{suffix}.csv", index=False)
    res.weights.to_csv(RESULTS / f"weights{suffix}.csv")
    print(
        f"\nwrote {RESULTS}/metrics{suffix}.csv equity{suffix}.csv trades{suffix}.csv weights{suffix}.csv"
    )


def _bench_2026(panel, mem, cfg, res_llm, res_pl, judgments, cal, args):
    mkt26 = strategy.Market(panel, mem, cfg)
    math = backtest.run(mkt26)
    m_math, _ = backtest.summarize(math, mkt26)
    m_spy, _ = backtest.summarize(backtest.spy_result(mkt26), mkt26)
    m_llm, _ = backtest.summarize(res_llm, mkt26)
    m_pl, _ = backtest.summarize(res_pl, mkt26)

    eq_cfg = replace(
        cfg, rebalance_days=21, top_n=10000, sector_cap=1.0, stop=1.0, leverage=1.0
    )
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


def _metrics_2026() -> pd.DataFrame:
    mem = universe.load_membership()
    panel = data.build_panel(mem)
    cfg = strategy.Config(
        start="2026-01-02",
        leverage=DEFAULT_LEVERAGE,
        financing_bps=DEFAULT_FINANCING_BPS,
        trend_ma=DEFAULT_TREND_MA,
        trend_scale=DEFAULT_TREND_SCALE,
        rebalance_mode=DEFAULT_REBALANCE,
        exec_price=DEFAULT_EXEC,
    )
    mkt = strategy.Market(panel, mem, cfg)
    m_bot, _ = backtest.summarize(backtest.run(mkt), mkt)
    m_spy, _ = backtest.summarize(backtest.spy_result(mkt), mkt)
    out = pd.DataFrame({"bot": m_bot, "spy": m_spy}).T
    out.to_csv(RESULTS / "metrics_2026.csv")
    return out


def cmd_report(args):
    _metrics_2026()
    lines = [
        "# stonks-bot — results",
        "",
        "Default: dual-momentum rotation. Hold SMH while its 6-month total return beats SPY;",
        "otherwise rotate into the S&P momentum sleeve (6 names, 10-day skip). Month-end check,",
        "next-open execution, 10 bps per side, 2.5x gross with 5%/yr margin interest,",
        "half size when the core is below its 200-day MA (`--trend-ma 0` disables),",
        "no trailing stop.",
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

    frp = RESULTS / "metrics_france.csv"
    if frp.exists():
        fdf = pd.read_csv(frp)
        lines += ["## France (after tax, real broker tariffs)", ""]
        lines += [
            "| market | wrapper | book | broker | account | after tax (EUR) | native | tax paid | exit tax | fees (incl. FTT) |",
            "|---|---|---|---|---:|---:|---:|---:|---:|---:|",
        ]
        for _, r in fdf.iterrows():
            cap = float(r["capital"])
            lines.append(
                f"| {r['market']} | {r['wrapper']} | {r['profile']} | {r['broker']} | "
                f"€{cap:,.0f} | **{r['after_tax_eur'] / cap:.2f}x** | "
                f"{r['after_tax_native'] / cap:.2f}x | "
                f"€{r['tax_paid_eur']:,.0f} | €{r['exit_tax_eur']:,.0f} | "
                f"€{r['fees_eur']:,.0f} |"
            )
        lines += [
            "",
            "The US table above is pre-tax. This section is after French PFU/PEA, "
            "FTT, broker tickets and EUR translation; see `results/france.md`.",
            "",
        ]

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
        "- SMH is a semiconductor ETF: most of the return is one industry's 11-year run, not alpha.",
        f"- Leverage: {DEFAULT_LEVERAGE:.1f}x gross at {DEFAULT_FINANCING_BPS / 100:.1f}%/yr.",
        f"  The full-window drawdown is {b['max_dd']:.1%} vs {s0['max_dd']:.1%} for SPY;",
        "  leverage multiplies the losses as well as the wins.",
        "- Daily de-leveraging when gross drifts above target adds turnover:",
        f"  {b['turnover_ann']:.1f}x NAV/yr, which is why costs matter.",
        "- A daily 200-day core cut halves exposure below the MA; there is no trailing stop.",
        "  2018 was -34.5% and 2022 -39.3% (SPY -5.2% and -18.6%).",
        "- Ten rounds were tuned on this one price path; 2023-2026 was held back as a check and won.",
        "- Taxes are not in the headline (0% rate). At 37% on realized gains the",
        "  book makes $164,988, still 26.7%/yr: results/realism.md.",
        "- Paper only. No orders, no broker, no keys.",
        "",
    ]
    (RESULTS / "summary.md").write_text("\n".join(lines))
    print(f"wrote {RESULTS}/summary.md")


def _live_moves(targets: dict[str, float], mkt, d):
    """Hour and day P&L for the current book from 1-minute bars."""
    frames = data.intraday(list(targets) + ["SPY"])
    hour: dict[str, float] = {}
    day: dict[str, float] = {}
    last_ts = None
    for t in targets:
        df = frames.get(t)
        if df is None or df.empty:
            continue
        session = df.ts.dt.date.max()
        bars = df[df.ts.dt.date == session]
        last = bars.iloc[-1]
        cutoff = last.ts - pd.Timedelta(minutes=60)
        base = bars[bars.ts <= cutoff]
        if len(base):
            hour[t] = float(last.close / base.close.iloc[-1] - 1)
        prev = mkt.px[t].loc[d] if t in mkt.px.columns else float("nan")
        if pd.notna(prev) and prev > 0 and session > d.date():
            day[t] = float(last.close / prev - 1)
        if last_ts is None or last.ts > last_ts:
            last_ts = last.ts
    return (
        sum(w * hour.get(t, 0.0) for t, w in targets.items()),
        sum(w * day.get(t, 0.0) for t, w in targets.items()),
        last_ts,
        len(hour),
    )


def _paint(text: str, value: float) -> str:
    if not sys.stdout.isatty():
        return text
    color = "\033[32m" if value >= 0 else "\033[31m"
    return f"{color}{text}\033[0m"


def _pnl_box(rows: list[tuple[str, float | None]], capital: float, head: list[str]):
    widths = [10, 11, 14, 16]
    probe = "│ " + " │ ".join("x" * w for w in widths) + " │"
    inner = max(len(probe) - 2, max(len(h) for h in head) + 2)
    rule = "├" + "─" * inner + "┤"
    out = ["╭" + "─" * inner + "╮"]
    for h in head:
        out.append("│ " + h.ljust(inner - 2)[: inner - 2] + " │")
    out += [rule]
    head_row = " │ ".join(
        f"{h:>{w}}" for h, w in zip(("horizon", "return", "P&L", "end value"), widths)
    )
    out.append("│ " + head_row.ljust(inner - 2) + " │")
    out.append(rule)
    for label, pct in rows:
        if pct is None or pd.isna(pct):
            cells = [
                f"{label:<{widths[0]}}",
                f"{'n/a':>{widths[1]}}",
                f"{'n/a':>{widths[2]}}",
                f"{'n/a':>{widths[3]}}",
            ]
        else:
            pnl = capital * pct
            end = capital * (1 + pct)
            cells = [
                f"{label:<{widths[0]}}",
                _paint(f"{pct:+.2%}".rjust(widths[1]), pct),
                _paint(
                    f"{'+' if pnl >= 0 else '-'}${abs(pnl):,.0f}".rjust(widths[2]), pct
                ),
                f"${end:,.0f}".rjust(widths[3]),
            ]
        out.append("│ " + " │ ".join(cells).ljust(inner - 2) + " │")
    out.append("╰" + "─" * inner + "╯")
    return "\n".join(out)


def _risk_scale(mkt, date, cfg) -> float:
    """Half size when the core is below its trend MA, as the backtest does daily."""
    if not cfg.trend_ma or cfg.core not in mkt.px.columns:
        return 1.0
    px = mkt.px[cfg.core]
    ma = px.rolling(cfg.trend_ma, min_periods=cfg.trend_ma).mean().loc[date]
    return cfg.trend_scale if pd.notna(ma) and px.loc[date] < ma else 1.0


def cmd_pnl(args):
    profile = args.profile or "monthly"
    broker = args.broker or (france.DEFAULT_BROKER if args.country == "fr" else "none")
    panel, mem, mkt, cfg = _load(
        "2015-01-01",
        None,
        None,
        None,
        args.leverage,
        args.financing_bps,
        args.market,
        profile,
        args.core,
        args.dd_stop,
        args.trend_ma,
        args.trend_scale,
        args.tax_rate,
        args.state_tax_rate,
        args.salt_cap,
        args.wash_sale_days,
        args.leverage_band,
        args.rebalance_mode,
        args.exec,
        args.country,
        broker,
        args.wrapper,
        args.capital,
    )
    res = backtest.run(mkt)
    d = mkt.dates[-1]
    targets = backtest._default_selector(mkt, d, overlay.held_names(res, d), set())
    scale = _risk_scale(mkt, d, cfg)
    targets = {t: w * cfg.leverage * scale for t, w in targets.items()}
    hour_pct, day_pct, last_ts, quoted = _live_moves(targets, mkt, d)
    live = 1.0 + day_pct if last_ts is not None and last_ts.date() > d.date() else 1.0
    daily = backtest.horizon_returns(res.equity, live)
    rows = [
        ("1 hour", hour_pct if quoted else None),
        ("1 day", daily["1 day"]),
        ("1 week", daily["1 week"]),
        ("1 month", daily["1 month"]),
        ("1 year", daily["1 year"]),
    ]
    stamp = last_ts.strftime("%Y-%m-%d %H:%M ET") if last_ts is not None else "no quote"
    tag = ""
    if args.country == "fr":
        tag = f"  ·  {args.country}/{france.broker_name(broker)}/{args.wrapper}"
    head = [
        f"STONKS-BOT  ·  {markets.MARKETS[args.market]['label']}  ·  paper P&L",
        f"${args.capital:,.0f}  ·  {cfg.leverage:.1f}x  ·  {profile}{tag}  ·  "
        f"signal {d.date()}  ·  {stamp}",
    ]
    print(_pnl_box(rows, args.capital, head))
    book = "  ".join(
        f"{t} {w:.0%}" for t, w in sorted(targets.items(), key=lambda x: -x[1])
    )
    print(f"  book: {book or 'cash'}")
    if scale != 1.0:
        print(f"  risk: half size (core below its {cfg.trend_ma}d MA)")
    print(f"  hour quotes: {quoted}/{len(targets)} names")
    if args.country == "fr":
        f = france.after_tax(res, args.capital, args.market)
        print(
            f"  France {args.wrapper} via {france.broker_name(broker)}: "
            f"after tax {f['after_tax_eur'] / args.capital:.2f}x "
            f"(€{f['after_tax_eur']:,.0f}), exit tax "
            f"€{f['exit_tax_eur']:,.0f}"
        )
        print("  horizons above are pre-exit-tax")


def cmd_paper(args):
    profile = args.profile or "monthly"
    broker = args.broker or (france.DEFAULT_BROKER if args.country == "fr" else "none")
    panel, mem, mkt, cfg = _load(
        "2026-01-02",
        None,
        args.core_weight,
        args.core_mode,
        args.leverage,
        args.financing_bps,
        args.market,
        profile,
        args.core,
        args.dd_stop,
        args.trend_ma,
        args.trend_scale,
        args.tax_rate,
        args.state_tax_rate,
        args.salt_cap,
        args.wash_sale_days,
        args.leverage_band,
        args.rebalance_mode,
        args.exec,
        args.country,
        broker,
        args.wrapper,
        args.capital,
    )
    d = mkt.dates[-1]
    res = backtest.run(mkt)
    held = overlay.held_names(res, d)
    targets = backtest._default_selector(mkt, d, held, set())
    if args.llm and (RESULTS / "llm_judgments.jsonl").exists():
        j = llm.load_judgments(RESULTS / "llm_judgments.jsonl")
        targets = llm.make_overlay(j, max_active=cfg.llm_active_share)(d, targets, mkt)
        print("paper: LLM overlay applied")
    if cfg.leverage != 1.0:
        targets = {t: w * cfg.leverage for t, w in targets.items()}
    scale = _risk_scale(mkt, d, cfg)
    if scale != 1.0:
        targets = {t: w * scale for t, w in targets.items()}
        print(f"paper: risk cut to {scale:.0%} (core below its {cfg.trend_ma}d MA)")
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


def _add_fr_args(p, with_capital: bool = True):
    p.add_argument("--country", choices=["us", "fr"], default="us")
    p.add_argument(
        "--broker",
        choices=["none", "bourse-direct", "trade-republic", "interactive-brokers"],
        default=None,
    )
    p.add_argument("--wrapper", choices=["cto", "pea"], default="cto")
    if with_capital:
        p.add_argument("--capital", type=float, default=10000.0)


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
    pb.add_argument("--market", choices=sorted(markets.MARKETS), default="US")
    pb.add_argument("--core", default=None, help="core ETF, default SMH")
    pb.add_argument("--dd-stop", type=float, default=0.0, help="drawdown stop, 0=off")
    pb.add_argument("--trend-ma", type=int, default=DEFAULT_TREND_MA)
    pb.add_argument("--trend-scale", type=float, default=DEFAULT_TREND_SCALE)
    pb.add_argument("--tax-rate", type=float, default=0.0)
    pb.add_argument("--state-tax-rate", type=float, default=0.0)
    pb.add_argument("--salt-cap", type=float, default=10000.0)
    pb.add_argument("--wash-sale-days", type=int, default=None)
    pb.add_argument("--leverage-band", type=float, default=None)
    pb.add_argument(
        "--rebalance-mode", choices=["days", "calendar"], default=DEFAULT_REBALANCE
    )
    pb.add_argument("--exec", choices=["close", "open"], default=DEFAULT_EXEC)
    pb.add_argument(
        "--limit-bps",
        type=float,
        default=None,
        help="opt-in limit-at-open: buy open*(1-k), sell open*(1+k), k in bps",
    )
    pb.add_argument(
        "--core-close-fill",
        action="store_true",
        help="old synthetic core fill (open == close) for US/TECH comparison",
    )
    pb.add_argument("--profile", choices=sorted(PROFILES), default=None)
    pb.add_argument("--leverage", type=float, default=DEFAULT_LEVERAGE)
    pb.add_argument("--financing-bps", type=float, default=DEFAULT_FINANCING_BPS)
    _add_fr_args(pb)
    pb.set_defaults(func=cmd_backtest)

    ps = sub.add_parser("study")
    ps.add_argument("--model", default=llm.DEFAULT_MODEL)
    ps.add_argument("--max-names", type=int, default=40)
    ps.add_argument("--limit-months", type=int, default=None)
    ps.add_argument("--random-seeds", type=int, default=50)
    ps.set_defaults(func=cmd_study)

    pr = sub.add_parser("report")
    pr.set_defaults(func=cmd_report)

    pn = sub.add_parser("pnl")
    pn.add_argument("--capital", type=float, default=10000.0)
    pn.add_argument("--market", choices=sorted(markets.MARKETS), default="US")
    pn.add_argument("--core", default=None, help="core ETF, default SMH")
    pn.add_argument("--dd-stop", type=float, default=0.0, help="drawdown stop, 0=off")
    pn.add_argument("--trend-ma", type=int, default=DEFAULT_TREND_MA)
    pn.add_argument("--trend-scale", type=float, default=DEFAULT_TREND_SCALE)
    pn.add_argument("--tax-rate", type=float, default=0.0)
    pn.add_argument("--state-tax-rate", type=float, default=0.0)
    pn.add_argument("--salt-cap", type=float, default=10000.0)
    pn.add_argument("--wash-sale-days", type=int, default=None)
    pn.add_argument("--leverage-band", type=float, default=None)
    pn.add_argument(
        "--rebalance-mode", choices=["days", "calendar"], default=DEFAULT_REBALANCE
    )
    pn.add_argument("--exec", choices=["close", "open"], default=DEFAULT_EXEC)
    pn.add_argument("--profile", choices=sorted(PROFILES), default=None)
    pn.add_argument("--leverage", type=float, default=DEFAULT_LEVERAGE)
    pn.add_argument("--financing-bps", type=float, default=DEFAULT_FINANCING_BPS)
    _add_fr_args(pn, with_capital=False)
    pn.set_defaults(func=cmd_pnl)

    pp = sub.add_parser("paper")
    pp.add_argument("--interval", type=int, default=60)
    pp.add_argument("--iterations", type=int, default=1)
    pp.add_argument("--llm", action="store_true")
    pp.add_argument("--core-weight", type=float, default=None)
    pp.add_argument("--core-mode", default=None)
    pp.add_argument("--market", choices=sorted(markets.MARKETS), default="US")
    pp.add_argument("--core", default=None, help="core ETF, default SMH")
    pp.add_argument("--dd-stop", type=float, default=0.0, help="drawdown stop, 0=off")
    pp.add_argument("--trend-ma", type=int, default=DEFAULT_TREND_MA)
    pp.add_argument("--trend-scale", type=float, default=DEFAULT_TREND_SCALE)
    pp.add_argument("--tax-rate", type=float, default=0.0)
    pp.add_argument("--state-tax-rate", type=float, default=0.0)
    pp.add_argument("--salt-cap", type=float, default=10000.0)
    pp.add_argument("--wash-sale-days", type=int, default=None)
    pp.add_argument("--leverage-band", type=float, default=None)
    pp.add_argument(
        "--rebalance-mode", choices=["days", "calendar"], default=DEFAULT_REBALANCE
    )
    pp.add_argument("--exec", choices=["close", "open"], default=DEFAULT_EXEC)
    pp.add_argument("--profile", choices=sorted(PROFILES), default=None)
    pp.add_argument("--leverage", type=float, default=DEFAULT_LEVERAGE)
    pp.add_argument("--financing-bps", type=float, default=DEFAULT_FINANCING_BPS)
    _add_fr_args(pp)
    pp.set_defaults(func=cmd_paper)

    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
