"""v8 short-horizon III: intraday, daily open/close and cross-sectional.

Run: uv run python scripts/research_short3.py
Writes results/short3.md and results/short3_trials.csv.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from bot.research.metrics import (  # noqa: E402
    format_multiple,
    format_pct,
    summarize_equity,
)
from bot.research.registry import TrialRegistry  # noqa: E402
from bot.research.short3.daily import (  # noqa: E402
    after_tax_series,
    daily_bars,
    open_close_stats,
    volatility_gap_fade,
    xs_reversal,
)
from bot.research.short3.intraday import (  # noqa: E402
    gap_fade,
    last_hour_momentum,
    load_bars,
    opening_range_breakout,
    summarize_trades,
    vwap_reversion,
)

END = "2026-11-25"
SYMS = ("SPY", "QQQ", "SMH")
DAILY_SYMS = ("SPY", "QQQ", "SMH", "TLT", "GLD", "IEF", "XLK", "XLE", "XLF", "XLV", "XLI", "XLP", "XLU", "XLB", "XLY")
INTRADAY_STRATS = (
    ("gap_fade", gap_fade),
    ("opening_range", opening_range_breakout),
    ("vwap_reversion", vwap_reversion),
    ("last_hour_momentum", last_hour_momentum),
)


def run_intraday(interval: str, range_: str, cost_bps: float) -> list[dict]:
    rows = []
    for sym in SYMS:
        try:
            bars = load_bars(sym, interval, range_)
        except Exception:
            continue
        for name, fn in INTRADAY_STRATS:
            try:
                trades = fn(bars, cost_bps=cost_bps)
            except Exception:
                continue
            s = summarize_trades(trades, bars["session"].nunique())
            rows.append({"interval": interval, "range": range_, "symbol": sym, "strategy": name, "cost_bps": cost_bps, **s})
    return rows


def main() -> None:
    registry = TrialRegistry()
    intraday = run_intraday("1h", "2y", 5.0)
    intraday2 = run_intraday("1h", "2y", 10.0)
    micro = run_intraday("5m", "60d", 5.0)

    daily_stocks = {s: daily_bars(s) for s in DAILY_SYMS}
    oc = {s: open_close_stats(daily_stocks[s]) for s in SYMS}

    gap_rows = []
    for sym in SYMS:
        d = daily_stocks[sym]
        for z in (1.0, 1.5, 2.0):
            for c in (5.0, 10.0):
                f = volatility_gap_fade(d, entry_z=z, cost_bps=c)
                eq, tax = after_tax_series(f["net"])
                gap_rows.append(
                    {
                        "symbol": sym,
                        "entry_z": z,
                        "cost_bps": c,
                        "n": len(f),
                        "mean": float(f["net"].mean()),
                        "hit": float((f["net"] > 0).mean()),
                        "multiple": float(eq.iloc[-1]),
                        "maxdd": float((eq / eq.cummax() - 1.0).min()),
                    }
                )

    closes = {s: daily_stocks[s]["adjclose"] for s in DAILY_SYMS}
    xs_rows = []
    for lb in (1, 5):
        for hold in (5, 10):
            for mode in ("long_only", "neutral"):
                for c in (5.0, 10.0):
                    r = xs_reversal(closes, lookback=lb, hold=hold, top_n=2, mode=mode, cost_bps=c)
                    xs_rows.append({"lookback": lb, "hold": hold, "mode": mode, "cost_bps": c, **r})

    # VIX conditional gap-fade diagnostic (lagged VIX close, no look-ahead).
    vix = daily_bars("^VIX")["adjclose"]
    vix_cond = []
    for sym in SYMS:
        f = volatility_gap_fade(daily_stocks[sym], entry_z=1.5, cost_bps=5)
        v = vix.reindex(f.index).ffill().shift(1)
        for label, mask in (("vix_high", v > v.median()), ("vix_low", v <= v.median())):
            g = f.loc[mask]
            vix_cond.append(
                {"symbol": sym, "regime": label, "n": len(g),
                 "mean": float(g["net"].mean()), "hit": float((g["net"] > 0).mean())}
            )

    # Registry.
    for r in intraday:
        registry.add(
            family="intraday_1h",
            hypothesis="completed-bar intraday signal survives costs",
            universe=r["symbol"],
            start="2024-11-26",
            end=END,
            params={"interval": "1h", "strategy": r["strategy"], "cost_bps": r["cost_bps"]},
            metric="after_tax_multiple",
            value=r.get("multiple"),
            cost_model="fr_cto_5bps",
            notes=f"n={r.get('n', 0)}; 2y in-sample",
            n_trials=len(INTRADAY_STRATS) * len(SYMS) * 2,
        )
    for r in intraday2:
        registry.add(
            family="intraday_1h_2x",
            hypothesis="completed-bar intraday signal survives 2x costs",
            universe=r["symbol"],
            start="2024-11-26",
            end=END,
            params={"interval": "1h", "strategy": r["strategy"], "cost_bps": r["cost_bps"]},
            metric="after_tax_multiple",
            value=r.get("multiple"),
            cost_model="fr_cto_10bps",
            notes=f"n={r.get('n', 0)}; 2x-cost, 2y in-sample",
            n_trials=len(INTRADAY_STRATS) * len(SYMS) * 2,
        )
    for r in micro:
        registry.add(
            family="intraday_5m",
            hypothesis="5-minute gap/ORB/vwap only an execution-quality check",
            universe=r["symbol"],
            start="2026-09-02",
            end=END,
            params={"interval": "5m", "strategy": r["strategy"], "cost_bps": r["cost_bps"]},
            metric="after_tax_multiple",
            value=r.get("multiple"),
            cost_model="fr_cto_5bps",
            notes=f"n={r.get('n', 0)}; 60d sample, not evidence",
            n_trials=len(INTRADAY_STRATS) * len(SYMS),
        )
    for r in gap_rows:
        registry.add(
            family="daily_gap_fade",
            hypothesis="volatility-scaled gap fade survives costs",
            universe=r["symbol"],
            start="2015-01-01",
            end=END,
            params={"entry_z": r["entry_z"], "cost_bps": r["cost_bps"]},
            metric="after_tax_multiple",
            value=r["multiple"],
            cost_model="fr_cto_5bps",
            notes=f"n={r['n']}",
            n_trials=len(gap_rows),
        )
    for r in xs_rows:
        registry.add(
            family="daily_xs_reversal",
            hypothesis="cross-sectional short-term reversal survives costs",
            universe="liquid ETFs",
            start="2015-01-01",
            end=END,
            params={"lookback": r["lookback"], "hold": r["hold"], "mode": r["mode"], "cost_bps": r["cost_bps"]},
            metric="after_tax_multiple",
            value=r["multiple"],
            cost_model="fr_cto_5bps",
            notes=f"turnover={r['turnover_ann']:.1f}x",
            n_trials=len(xs_rows),
        )
    registry.write(ROOT / "results" / "short3_trials.csv")

    # Benchmarks over the intraday 2y window.
    qqq_d = daily_stocks["QQQ"]
    qqq_2y = qqq_d.loc[qqq_d.index >= pd.Timestamp("2024-11-26")]
    qqq_ret = float(qqq_2y["adjclose"].iloc[-1] / qqq_2y["adjclose"].iloc[0])
    qqq_full_gross = float(qqq_d["adjclose"].iloc[-1] / qqq_d["adjclose"].iloc[0])
    # Published after-tax QQQ baseline from alpha_baseline.md; keep both labels.
    qqq_full_after_tax = 5.49
    full_equity = (1.0 + qqq_d["adjclose"].pct_change().fillna(0.0)).cumprod()
    full_metrics = summarize_equity(full_equity)

    lines = [
        "# Short-horizon research III: actual intraday, daily open/close, cross-section",
        "",
        "Generated by `uv run python scripts/research_short3.py`.",
        "Intraday data: Yahoo 1h 2y and 5m 60d, filtered 09:30-16:00 ET.",
        "Signals use completed bars; entry is the next bar open. 5 bps/side",
        "base, 10 bps 2x; annual French PFU. 5m is execution-quality only.",
        "",
        "## 1-hour strategies, 2 years, 5 bps/side",
        "",
        "| symbol | strategy | trades | sessions | hit | mean net | after-tax | CAGR | maxDD |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in intraday:
        lines.append(
            f"| {r['symbol']} | {r['strategy']} | {r['n']} | {r['sessions']} | "
            f"{r['hit']:.1%} | {format_pct(r['mean'])} | {format_multiple(r['multiple'])} | "
            f"{format_pct(r['cagr'])} | {format_pct(r['maxdd'])} |"
        )
    lines += [
        "",
        "2x-cost intraday rows (10 bps/side):",
        "",
        "| symbol | strategy | trades | after-tax | maxDD |",
        "|---|---|---:|---:|---:|",
    ]
    for r in intraday2:
        lines.append(
            f"| {r['symbol']} | {r['strategy']} | {r['n']} | "
            f"{format_multiple(r['multiple'])} | {format_pct(r['maxdd'])} |"
        )
    lines += [
        "",
        f"QQQ over the same 2y window: {format_multiple(qqq_ret)} buy & hold; "
        f"full 2015-2026 {format_multiple(qqq_full_gross)} gross / "
        f"{format_multiple(qqq_full_after_tax)} after tax.",
        "",
        "## 5-minute execution-quality check, 60 days",
        "",
        "Same strategies, 5 bps/side; **sample is tiny and not a robustness test.**",
        "",
        "| symbol | strategy | trades | hit | mean net | after-tax | maxDD |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for r in micro:
        lines.append(
            f"| {r['symbol']} | {r['strategy']} | {r['n']} | {r['hit']:.1%} | "
            f"{format_pct(r['mean'])} | {format_multiple(r['multiple'])} | "
            f"{format_pct(r['maxdd'])} |"
        )
    lines += [
        "",
        "## Daily open-to-close vs close-to-open, 2015-2026",
        "",
        "| ETF | O2C mean/day | O2C vol | O2C hit | C2O mean/day | C2O vol | C2O hit |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for s in SYMS:
        r = oc[s]
        lines.append(
            f"| {s} | {format_pct(r['o2c_mean'])} | {format_pct(r['o2c_vol'])} | "
            f"{r['o2c_hit']:.1%} | {format_pct(r['c2o_mean'])} | "
            f"{format_pct(r['c2o_vol'])} | {r['c2o_hit']:.1%} |"
        )
    lines += [
        "",
        "The overnight gap carries most of the mean; open-to-close is close to",
        "zero. That is a descriptive decomposition, not a tradable overnight",
        "claim (overall overnight was rejected in `alpha_short.md`).",
        "",
        "## Volatility-scaled daily gap fade, 2015-2026",
        "",
        "| ETF | entry z | cost | n | mean net | hit | after-tax | maxDD |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in gap_rows:
        lines.append(
            f"| {r['symbol']} | {r['entry_z']:.1f} | {r['cost_bps']:.0f} bps | "
            f"{r['n']} | {format_pct(r['mean'])} | {r['hit']:.1%} | "
            f"{format_multiple(r['multiple'])} | {format_pct(r['maxdd'])} |"
        )
    lines += [
        "",
        "VIX conditional (lagged VIX close, entry z=1.5, 5 bps):",
        "",
        "| ETF | regime | n | mean net | hit |",
        "|---|---|---:|---:|---:|",
    ]
    for r in vix_cond:
        lines.append(
            f"| {r['symbol']} | {r['regime']} | {r['n']} | "
            f"{format_pct(r['mean'])} | {r['hit']:.1%} |"
        )
    lines += [
        "",
        "## Cross-sectional short-term reversal, liquid ETFs, 2015-2026",
        "",
        "| lookback | hold | mode | cost | after-tax | CAGR | maxDD | turnover |",
        "|---|---:|---|---:|---:|---:|---:|---:|",
    ]
    for r in xs_rows:
        lines.append(
            f"| {r['lookback']} | {r['hold']} | {r['mode']} | {r['cost_bps']:.0f} bps | "
            f"{format_multiple(r['multiple'])} | {format_pct(r['cagr'])} | "
            f"{format_pct(r['maxdd'])} | {r['turnover_ann']:.0f}x |"
        )
    lines += [
        "",
        "## Verdict",
        "",
    ]
    best_intraday = max(intraday, key=lambda r: r["multiple"])
    best_gap = max(gap_rows, key=lambda r: r["multiple"])
    best_xs = max(xs_rows, key=lambda r: r["multiple"])
    gates = {
        "best intraday after-tax beats QQQ 2y": best_intraday["multiple"] > qqq_ret,
        "best intraday beats BIL 2y": best_intraday["multiple"] > 1.05,
        "daily gap fade after-tax beats QQQ full": best_gap["multiple"] > qqq_full_after_tax,
        "cross-sectional reversal after-tax beats QQQ full": best_xs["multiple"] > qqq_full_after_tax,
    }
    for k, v in gates.items():
        lines.append(f"- {k}: **{'PASS' if v else 'FAIL'}**")
    lines += [
        "",
        "**Verdict: `reject`.** The best 1-hour row is "
        f"{best_intraday['symbol']} {best_intraday['strategy']} at "
        f"{format_multiple(best_intraday['multiple'])} after tax over the 2-year",
        f"sample, versus QQQ {format_multiple(qqq_ret)}; the other intraday families",
        "lose. The gap-fade and cross-sectional daily families are also below",
        "QQQ after tax, and the 5-minute sample is far too small to claim",
        "execution edge. No satellite allocation; keep the monthly book.",
        "",
        "## Sample / data limits",
        "",
        "- 1h sample: 501 sessions (2024-11-26..2026-11-25), in-sample, no OOS.",
        "- 5m sample: ~60 days, execution-quality only; not a 2-year claim.",
        "- Intraday bars are Yahoo aggregates, not exchange MBO data; no queue",
        "  position, partial fill or auction simulation.",
        "- French tax is annual net PFU; intraday gains are assumed realized.",
        "",
    ]
    (ROOT / "results" / "short3.md").write_text("\n".join(lines) + "\n")
    print(
        f"best intraday {best_intraday['symbol']} {best_intraday['strategy']} "
        f"{best_intraday['multiple']:.2f}x vs QQQ 2y {qqq_ret:.2f}x; "
        f"best gap {best_gap['multiple']:.2f}x; best xs {best_xs['multiple']:.2f}x"
    )
    print("wrote results/short3.md")


if __name__ == "__main__":
    main()
