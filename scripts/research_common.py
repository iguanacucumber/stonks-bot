"""Shared helpers for Alpha Lab experiment scripts.

Read-only for workers: edit `scripts/research_<family>.py`, not this file,
unless you are the orchestrator.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from bot import backtest, france, markets, strategy, universe  # noqa: E402
from bot.research.costs import CostModel, after_tax  # noqa: E402
from bot.research.data import CROSS_ASSET, make_panel  # noqa: E402
from bot.research.dividends import (  # noqa: E402
    annual_dividend_summary,
    apply_dividend_tax,
    daily_dividend_yield,
)
from bot.research.ledger import replay as ledger_replay  # noqa: E402
from bot.research.metrics import (  # noqa: E402
    annual_return,
    five_year_windows,
    format_multiple,
    format_pct,
    summarize_equity,
    window_summary,
    worst_12m,
)

START = "2015-01-01"
END = "2026-12-31"
_ORIG_CANONICAL = universe.canonical


def dividend_adjusted(
    res, panel: pd.DataFrame, ledger, capital: float = 10_000.0
) -> dict:
    """Dividend-tax overlay on the headline and EUR-ledger equity paths."""
    yields = daily_dividend_yield(panel)
    eq_div, daily_y = apply_dividend_tax(res.equity, res.weights, yields)
    led_div, _ = apply_dividend_tax(ledger.equity, res.weights, yields)
    summary = annual_dividend_summary(daily_y)
    return {
        "equity_divadj": eq_div,
        "after_tax_multiple_divadj": float(eq_div.iloc[-1] - res.unrealized_tax),
        "ledger_multiple_divadj": float((led_div.iloc[-1] - ledger.exit_tax) / capital),
        **summary,
    }


def tech_baseline(
    leverage: float = 2.5,
    cost_model: CostModel | None = None,
    start: str = START,
    end: str = END,
) -> dict:
    """Shipped TECH monthly book: 2.5x, 200d daily cut, SMH core, French CTO."""
    cm = cost_model or CostModel()
    orig = universe.canonical
    panel, mem = markets.build("TECH")
    universe.canonical = lambda t: t  # type: ignore[assignment]
    kw = cm.engine_kwargs(panel, "TECH")
    kw.update(
        start=start,
        end=end,
        leverage=float(leverage),
        financing_bps=cm.financing_bps * cm.multiplier,
        trend_ma=200,
        trend_scale=0.5,
        rebalance_mode="calendar",
        exec_price="open",
        tax_lots="hifo",
    )
    cfg = strategy.Config(**kw)
    mkt = strategy.Market(panel, mem, cfg)
    res = backtest.run(mkt)
    tax = after_tax(res, "TECH")
    eq = res.equity.dropna()
    m, _ = backtest.summarize(res, mkt)
    led = ledger_replay(res, panel, "TECH")
    div = dividend_adjusted(res, panel, led)
    universe.canonical = orig  # type: ignore[assignment]
    return {
        "result": res,
        "market": mkt,
        "panel": panel,
        "equity": eq,
        "after_tax": tax,
        "after_tax_multiple": tax["after_tax_multiple_eur"],
        "after_tax_multiple_usd": tax["after_tax_multiple_usd"],
        "ledger_multiple": led.multiple,
        "ledger_maxdd": led.maxdd,
        "after_tax_multiple_divadj": div["after_tax_multiple_divadj"],
        "ledger_multiple_divadj": div["ledger_multiple_divadj"],
        "avg_div_yield": div["avg_yield"],
        "div_tax_drag": div["tax_drag"],
        "equity_divadj": div["equity_divadj"],
        "maxdd": float((eq / eq.cummax() - 1.0).min()),
        "cagr": float((eq.iloc[-1] / eq.iloc[0]) ** (252.0 / len(eq)) - 1.0),
        "summary": summarize_equity(eq, mkt.spy.reindex(eq.index).ffill()),
        "trades": int(len(res.trades)),
        "metrics": m,
    }


def us_baseline(
    leverage: float = 2.5,
    cost_model: CostModel | None = None,
    start: str = START,
    end: str = END,
) -> dict:
    """Shipped US monthly book: PIT S&P 500 sleeve + SMH core, French CTO."""
    cm = cost_model or CostModel()
    universe.canonical = _ORIG_CANONICAL  # type: ignore[assignment]
    panel, mem = markets.build("US")
    kw = cm.engine_kwargs(panel, "US")
    kw.update(
        start=start,
        end=end,
        leverage=float(leverage),
        financing_bps=cm.financing_bps * cm.multiplier,
        trend_ma=200,
        trend_scale=0.5,
        rebalance_mode="calendar",
        exec_price="open",
        tax_lots="hifo",
    )
    cfg = strategy.Config(**kw)
    mkt = strategy.Market(panel, mem, cfg)
    res = backtest.run(mkt)
    tax = after_tax(res, "US")
    eq = res.equity.dropna()
    m, _ = backtest.summarize(res, mkt)
    led = ledger_replay(res, panel, "US")
    div = dividend_adjusted(res, panel, led)
    return {
        "result": res,
        "market": mkt,
        "panel": panel,
        "equity": eq,
        "after_tax": tax,
        "after_tax_multiple": tax["after_tax_multiple_eur"],
        "ledger_multiple": led.multiple,
        "ledger_maxdd": led.maxdd,
        "after_tax_multiple_divadj": div["after_tax_multiple_divadj"],
        "ledger_multiple_divadj": div["ledger_multiple_divadj"],
        "avg_div_yield": div["avg_yield"],
        "div_tax_drag": div["tax_drag"],
        "equity_divadj": div["equity_divadj"],
        "maxdd": float((eq / eq.cummax() - 1.0).min()),
        "cagr": float((eq.iloc[-1] / eq.iloc[0]) ** (252.0 / len(eq)) - 1.0),
        "summary": summarize_equity(eq, mkt.spy.reindex(eq.index).ffill()),
        "trades": int(len(res.trades)),
        "metrics": m,
    }


def buy_hold(
    symbol: str,
    cost_model: CostModel | None = None,
    start: str = START,
    end: str = END,
    leverage: float = 1.0,
) -> dict:
    """Buy-and-hold one cached ETF, with the same French tax/cost wrapper."""
    cm = cost_model or CostModel()
    universe.canonical = _ORIG_CANONICAL  # type: ignore[assignment]
    panel, mem = make_panel((symbol, "SPY"))
    kw = cm.engine_kwargs(panel, "TECH")
    kw.update(
        start=start,
        end=end,
        leverage=float(leverage),
        financing_bps=cm.financing_bps * cm.multiplier,
        trend_ma=0,
        rebalance_mode="calendar",
        exec_price="open",
        tax_lots="hifo",
    )
    cfg = strategy.Config(**kw)
    mkt = strategy.Market(panel, mem, cfg)

    def sel(m, d, held, banned):
        return {symbol: 1.0}

    res = backtest.run(mkt, selector=sel)
    tax = after_tax(res, "TECH")
    eq = res.equity.dropna()
    led = ledger_replay(res, panel, "TECH")
    div = dividend_adjusted(res, panel, led)
    return {
        "result": res,
        "market": mkt,
        "equity": eq,
        "after_tax": tax,
        "after_tax_multiple": tax["after_tax_multiple_eur"],
        "ledger_multiple": led.multiple,
        "ledger_maxdd": led.maxdd,
        "after_tax_multiple_divadj": div["after_tax_multiple_divadj"],
        "ledger_multiple_divadj": div["ledger_multiple_divadj"],
        "avg_div_yield": div["avg_yield"],
        "div_tax_drag": div["tax_drag"],
        "equity_divadj": div["equity_divadj"],
        "maxdd": float((eq / eq.cummax() - 1.0).min()),
        "summary": summarize_equity(eq, mkt.spy.reindex(eq.index).ffill()),
        "trades": int(len(res.trades)),
    }


def metrics_row(
    name: str, eq: pd.Series, after_mult: float, bench: pd.Series | None = None
) -> dict:
    s = summarize_equity(eq, bench)
    return {
        "name": name,
        "after": after_mult,
        "cagr": s["cagr"],
        "maxdd": s["maxdd"],
        "sharpe": s["sharpe"],
        "sortino": s["sortino"],
        "worst_12m": worst_12m(eq),
        "y2018": annual_return(eq, 2018),
        "y2022": annual_return(eq, 2022),
        "five_year": s["five_year"],
        "summary": s,
    }


def markdown_metrics_table(rows: list[dict], title: str = "") -> str:
    lines = []
    if title:
        lines += [f"## {title}", ""]
    lines += [
        "| book | after-tax | CAGR | maxDD | Sharpe | worst 12m | 2018 | 2022 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        lines.append(
            f"| {r['name']} | **{format_multiple(r['after'])}** | "
            f"{format_pct(r['cagr'])} | {format_pct(r['maxdd'])} | "
            f"{r['sharpe']:.2f} | {format_pct(r['worst_12m'])} | "
            f"{format_pct(r['y2018'])} | {format_pct(r['y2022'])} |"
        )
    return "\n".join(lines)


def five_year_table(
    strategy_eq: pd.Series,
    benchmark_eq: pd.Series,
    label: str = "strategy",
    bench_label: str = "benchmark",
) -> tuple[str, dict]:
    sw = five_year_windows(strategy_eq)
    bw = five_year_windows(benchmark_eq)
    bmap = {(w["start"], w["end"]): w["return"] for w in bw}
    common = [(w, bmap.get((w["start"], w["end"]))) for w in sw]
    common = [(w, b) for w, b in common if b is not None]
    lines = [
        f"### 5-year windows: {label} vs {bench_label}",
        "",
        f"- Windows: **{len(common)}**",
        f"- {label} median: **{np.median([w['return'] for w, _ in common]):+.1%}**"
        if common
        else "- no windows",
        f"- {bench_label} median: **{np.median([b for _, b in common]):+.1%}**"
        if common
        else "",
        f"- {label} worst: **{min(w['return'] for w, _ in common):+.1%}**"
        if common
        else "",
        f"- share beating {bench_label}: **{np.mean([w['return'] > b for w, b in common]):.0%}**"
        if common
        else "",
        "",
    ]
    if common:
        lines += [
            "| start | end | " + label + " | " + bench_label + " | gap |",
            "|---|---|---:|---:|---:|",
        ]
        for w, b in common:
            lines.append(
                f"| {w['start']} | {w['end']} | {w['return']:+.1%} | {b:+.1%} | "
                f"{w['return'] - b:+.1%} |"
            )
    summary = window_summary([w["return"] for w, _ in common], [b for _, b in common])
    return "\n".join(lines), summary


def write_report(path: str | Path, text: str) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n")
    return path
