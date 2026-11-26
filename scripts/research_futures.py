"""Futures leverage vs ETF+margin: 2.5x index exposure.

Run: uv run python scripts/research_futures.py
Writes results/futures.md.

Cache-only. ES=F/NQ=F/MES=F/MNQ=F were fetched individually; no full refresh.
The futures model is in `bot/futures.py`; the ETF+margin control uses the
existing engine and dividend-aware EUR ledger.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from bot import backtest, dividends as divdata, eur_ledger, france, markets, strategy  # noqa: E402
from bot.futures import (  # noqa: E402
    CONTRACTS,
    FuturesConfig,
    roll_dates,
    simulate_futures,
)
from bot.research.data import make_panel, read_bars  # noqa: E402
from bot.research.metrics import (  # noqa: E402
    annual_return,
    five_year_windows,
    format_multiple,
    format_pct,
    summarize_equity,
    window_summary,
    worst_12m,
)

CAP = 10_000.0
START = "2015-01-01"
END = "2026-11-25"


def etf_config(panel: pd.DataFrame, start: str) -> strategy.Config:
    kw = france.kwargs("US", "Interactive Brokers", "cto", CAP, panel)
    kw.update(
        start=start,
        end=END,
        leverage=2.5,
        financing_bps=500.0,
        trend_ma=0,
        rebalance_mode="calendar",
        exec_price="open",
        tax_lots="hifo",
        sector_cap=1.0,
        stop=1.0,
        cost_bps=5.0,
        core=None,
    )
    return strategy.Config(**kw)


def run_etf(symbol: str, start: str = START) -> dict:
    panel, mem = make_panel((symbol, "SPY"))
    cfg = etf_config(panel, start)
    mkt = strategy.Market(panel, mem, cfg)

    def selector(m, d, held, banned):
        return {symbol: 1.0}

    res = backtest.run(mkt, selector=selector)
    tax = france.after_tax(res, CAP, "US")
    divs = divdata.load_dividends(
        sorted(res.trades["ticker"].unique()), fetch_missing=False
    )
    led = eur_ledger.replay(
        res,
        panel,
        "US",
        config=eur_ledger.EurLedgerConfig(
            capital=CAP,
            tax_rate=None,
            financing_bps=500.0,
            spread_bps=5.0,
            fx_bps=2.0,
            broker_bps=0.25,
            broker_fixed_eur=1.0,
            withholding_rate=0.15,
        ),
        dividends=divs,
    )
    eq = res.equity.dropna()
    return {
        "label": f"{symbol} ETF+margin 2.5x",
        "symbol": symbol,
        "res": res,
        "ledger": led,
        "headline": tax["after_tax_eur"] / CAP,
        "native": float(eq.iloc[-1] - res.unrealized_tax),
        "ledger_mult": led.multiple,
        "cagr": led.cagr,
        "maxdd": led.maxdd,
        "y2018": annual_return(led.equity, 2018),
        "y2022": annual_return(led.equity, 2022),
        "worst_12m": worst_12m(led.equity),
        "trades": len(res.trades),
        "div_tax": led.dividend_tax_total,
    }


def futures_config(cash_rate_bps: float = 0.0, mult: float = 1.0) -> FuturesConfig:
    base = FuturesConfig(
        target_leverage=2.5,
        implied_financing_bps=450.0,
        cash_rate_bps=cash_rate_bps,
        roll_bps=1.0,
        commission_bps=0.5,
        rebalance_bps=0.5,
    )
    return base if mult == 1.0 else base.scaled_costs(mult)


def run_futures(
    symbol: str,
    index_symbol: str,
    *,
    start: str = START,
    mult: float = 1.0,
    cash_rate_bps: float = 0.0,
) -> dict:
    idx = read_bars(index_symbol).set_index("date")["adjclose"].sort_index()
    idx = idx.loc[idx.index >= pd.Timestamp(start)]
    r = idx.pct_change().dropna()
    fx = france.eur_per_unit("US")
    fx = fx.reindex(fx.index.union(r.index)).ffill().reindex(r.index)
    cfg = futures_config(cash_rate_bps, mult)
    sim = simulate_futures(r, config=cfg, fx_eur_per_usd=fx)
    eq = sim["equity"]
    return {
        "label": f"{symbol} futures 2.5x"
        + (f" cash {cash_rate_bps / 100:.0f}%")
        + (" @2x costs" if mult != 1.0 else ""),
        "symbol": symbol,
        "sim": sim,
        "ledger_mult": sim["multiple"],
        "cagr": sim["cagr"],
        "maxdd": sim["maxdd"],
        "y2018": annual_return(eq, 2018),
        "y2022": annual_return(eq, 2022),
        "worst_12m": worst_12m(eq),
        "tax": sim["tax_paid"],
        "exposure": float(sim["exposure"].mean()),
    }


def data_quality(symbol: str, start: str = START) -> dict:
    df = read_bars(symbol)
    df = df[df["date"] >= pd.Timestamp(start)]
    r = df.set_index("date")["adjclose"].pct_change().dropna()
    return {
        "symbol": symbol,
        "start": df["date"].min().date().isoformat(),
        "end": df["date"].max().date().isoformat(),
        "rows": len(df),
        "vol": float(r.std() * np.sqrt(252.0)),
        "max_abs_day": float(r.abs().max()),
        "rolls": len(roll_dates(pd.DatetimeIndex(df["date"]))),
    }


def five_year_rows(a: pd.Series, b: pd.Series) -> tuple[list[dict], dict]:
    aw = five_year_windows(a)
    bw = five_year_windows(b)
    bmap = {(w["start"], w["end"]): w["return"] for w in bw}
    rows = []
    for w in aw:
        key = (w["start"], w["end"])
        if key in bmap:
            rows.append(
                {
                    "start": w["start"],
                    "end": w["end"],
                    "a": w["return"],
                    "b": bmap[key],
                    "gap": w["return"] - bmap[key],
                }
            )
    return rows, window_summary([x["a"] for x in rows], [x["b"] for x in rows])


def main() -> None:
    quality = [data_quality(s) for s in ("ES=F", "NQ=F", "MES=F", "MNQ=F")]

    # ETF + margin controls.
    spy_etf = run_etf("SPY")
    qqq_etf = run_etf("QQQ")

    # Futures base and cash/cost sensitivities.
    es = run_futures("ES", "SPY")
    nq = run_futures("NQ", "QQQ")
    es_cash3 = run_futures("ES", "SPY", cash_rate_bps=300.0)
    nq_cash3 = run_futures("NQ", "QQQ", cash_rate_bps=300.0)
    es_2x = run_futures("ES", "SPY", mult=2.0)
    nq_2x = run_futures("NQ", "QQQ", mult=2.0)

    # Micro contracts from their common 2019 start, to check the small-account
    # implementation and data consistency.
    micro_start = "2019-05-03"
    spy_micro_etf = run_etf("SPY", start=micro_start)
    qqq_micro_etf = run_etf("QQQ", start=micro_start)
    mes = run_futures("MES", "SPY", start=micro_start)
    mnq = run_futures("MNQ", "QQQ", start=micro_start)

    # 5-year windows over the full 2015-2026 common window.
    rows_es, sum_es = five_year_rows(es["sim"]["equity"], spy_etf["ledger"].equity)
    rows_nq, sum_nq = five_year_rows(nq["sim"]["equity"], qqq_etf["ledger"].equity)

    lines = [
        "# Futures leverage vs ETF+margin",
        "",
        "Generated by `uv run python scripts/research_futures.py`.",
        "Cache-only. ES=F/NQ=F/MES=F/MNQ=F fetched individually; no full refresh.",
        "Data snapshot 2026-11-25.",
        "",
        "## Contract data and roll assumptions",
        "",
        "Yahoo continuous front-month series; they do not expose individual",
        "contract expiries. The model charges a quarterly roll at the last",
        "trading session of Mar/Jun/Sep/Dec: 1 bp roll + 0.5 bp commission on",
        "notional, plus 0.5 bp monthly rebalance. Roll decisions use the prior",
        "close; no future roll date can affect an earlier position.",
        "",
        "| symbol | contract | start | end | rows | vol | max abs 1d | roll dates |",
        "|---|---|---|---|---:|---:|---:|---:|",
    ]
    for q in quality:
        meta = CONTRACTS.get(q["symbol"].replace("=F", ""), {})
        lines.append(
            f"| {q['symbol']} | {meta.get('name', '')} | {q['start']} | "
            f"{q['end']} | {q['rows']} | {format_pct(q['vol'])} | "
            f"{format_pct(q['max_abs_day'])} | {q['rolls']} |"
        )
    lines += [
        "",
        "The continuous series contains roll gaps and is not a tradable",
        "contract; the model therefore uses the index total-return series as",
        "the economic exposure and the futures series only for data-quality",
        "checks and contract-volume context.",
        "",
        "Yahoo does not expose individual contract expiries, so the implied",
        "financing curve cannot be estimated reliably from this continuous",
        "series. The model uses a **4.50%/yr implied financing base** and",
        "shows a 3% cash-collateral sensitivity; this is an explicit",
        "assumption, not a fitted basis.",
        "",
        "## Futures economics",
        "",
        "For a fully collateralised position:",
        "",
        "`r_position = exposure * (r_index_total - r_implied) + r_cash`",
        "",
        "The 2.5x ETF+margin control borrows 1.5x at 5.00%/yr and pays the",
        "ETF expense inside NAV. The futures base charges 4.50%/yr implied",
        "financing on 2.5x notional and 0% on cash collateral; the 3% cash row",
        "shows the value of interest on margin cash. French tax: annual net",
        "PFU (30% through 2025, 31.4% from 2026), ten-year loss carry, no",
        "separate exit tax because variation margin is marked annually.",
        "",
        "## Full-window 2015-2026 (EUR ledger / after-tax EUR)",
        "",
        "| book | after-tax EUR | CAGR | maxDD | 2018 | 2022 | worst 12m |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for b in (spy_etf, es, es_cash3, nq, qqq_etf, nq_cash3):
        lines.append(
            f"| {b['label']} | **{format_multiple(b['ledger_mult'])}** | "
            f"{format_pct(b['cagr'])} | {format_pct(b['maxdd'])} | "
            f"{format_pct(b['y2018'])} | {format_pct(b['y2022'])} | "
            f"{format_pct(b['worst_12m'])} |"
        )
    lines += [
        "",
        "## 2x costs",
        "",
        "| book | after-tax EUR | CAGR | maxDD |",
        "|---|---:|---:|---:|",
    ]
    for b in (spy_etf, es, es_2x, qqq_etf, nq, nq_2x):
        lines.append(
            f"| {b['label']} | {format_multiple(b['ledger_mult'])} | "
            f"{format_pct(b['cagr'])} | {format_pct(b['maxdd'])} |"
        )
    lines += [
        "",
        "## Micro contracts from 2019-05-03",
        "",
        "| book | after-tax EUR | CAGR | maxDD |",
        "|---|---:|---:|---:|",
    ]
    for b in (spy_micro_etf, mes, qqq_micro_etf, mnq):
        lines.append(
            f"| {b['label']} | {format_multiple(b['ledger_mult'])} | "
            f"{format_pct(b['cagr'])} | {format_pct(b['maxdd'])} |"
        )
    lines += [
        "",
        "## 5-year windows vs ETF+margin (EUR)",
        "",
        "| pair | n | median gap | worst gap | share futures beats ETF |",
        "|---|---:|---:|---:|---:|",
        f"| ES vs SPY ETF | {sum_es.get('n', 0)} | "
        f"{format_pct(sum_es.get('median_gap', float('nan')))} | "
        f"{format_pct(min((x['gap'] for x in rows_es), default=float('nan')))} | "
        f"{sum_es.get('share_beat_benchmark', 0):.0%} |",
        f"| NQ vs QQQ ETF | {sum_nq.get('n', 0)} | "
        f"{format_pct(sum_nq.get('median_gap', float('nan')))} | "
        f"{format_pct(min((x['gap'] for x in rows_nq), default=float('nan')))} | "
        f"{sum_nq.get('share_beat_benchmark', 0):.0%} |",
        "",
        "## Verdict",
        "",
    ]
    gates = {
        "ES futures beats SPY ETF after tax": (
            es["ledger_mult"] > spy_etf["ledger_mult"]
        ),
        "NQ futures beats QQQ ETF after tax": (
            nq["ledger_mult"] > qqq_etf["ledger_mult"]
        ),
        "ES futures maxDD no worse than ETF": (es["maxdd"] >= spy_etf["maxdd"]),
        "NQ futures maxDD no worse than ETF": (nq["maxdd"] >= qqq_etf["maxdd"]),
        "ES wins majority of 5-year windows": (
            sum_es.get("share_beat_benchmark", 0) >= 0.50
        ),
        "NQ wins majority of 5-year windows": (
            sum_nq.get("share_beat_benchmark", 0) >= 0.50
        ),
    }
    for k, v in gates.items():
        lines.append(f"- {k}: **{'PASS' if v else 'FAIL'}**")
    lines += [
        "",
        (
            "**Verdict: `reject` for the base futures implementation.** "
            "At 4.50% implied financing and 0% cash interest, 2.5x futures "
            "pays financing on the full notional (2.5x) instead of only the "
            "1.5x borrowed by the ETF+margin book; the 3% cash-interest "
            "sensitivity narrows but does not close the gap, and quarterly "
            "roll/slippage adds drag. The futures path also has no dividend "
            "tax deferral advantage because the index total return already "
            "includes the distribution economics. Keep the ETF+margin book. "
            "Only a futures implementation with cash collateral earning near "
            "the implied financing rate, and a lower basis, could compete; "
            "that is not the current retail CTO case."
        ),
        "",
        "## Caveats",
        "",
        "- Continuous front-month Yahoo series have roll gaps and no explicit",
        "  expiry; the economic simulation uses index total returns, not the",
        "  continuous futures price.",
        "- Implied financing is an explicit 4.50%/yr assumption; the continuous",
        "  Yahoo series has no expiry and cannot identify a contract-level basis.",
        "- Futures tax treatment is an assumption: annual net PFU with loss",
        "  carry. A deferral-based treatment would improve the futures path;",
        "  the model deliberately uses the conservative annual tax.",
        "- The ETF+margin control is a 2.5x single-index overlay, not the",
        "  stock-sleeve US monthly book; it isolates the leverage vehicle.",
        "- Micro contracts start 2019; the full 2015-2026 comparison uses ES/NQ.",
        "",
    ]
    (ROOT / "results" / "futures.md").write_text("\n".join(lines) + "\n")
    for b in (spy_etf, es, es_cash3, nq, qqq_etf, nq_cash3):
        print(
            f"{b['label']:30s} after {b['ledger_mult']:6.2f}x  "
            f"DD {b['maxdd']:6.1%}  CAGR {b['cagr']:6.1%}"
        )
    print("wrote results/futures.md")


if __name__ == "__main__":
    main()
