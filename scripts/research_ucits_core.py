"""UCITS semiconductor core vs US-domiciled SMH.

Run: uv run python scripts/research_ucits_core.py
Writes results/ucits_core.md.

Cache-only at run time. The three UCITS lines were fetched once with
`bot.data.fetch_one`; no full refresh. All strategy runs use the same US
monthly 2.5x rules and the same signal dates; only `Config.core` changes.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from bot import (  # noqa: E402
    backtest,
    dividends as divdata,
    eur_ledger,
    france,
    markets,
    strategy,
    universe,
)
from bot.research.metrics import (  # noqa: E402
    annual_return,
    five_year_windows,
    format_multiple,
    format_pct,
    summarize_equity,
    window_summary,
    worst_12m,
)
from bot.research.ucits import (  # noqa: E402
    FUNDS,
    add_ucits_to_panel,
    common_start,
    fund_stats,
    to_usd_series,
)

CAP = 10_000.0
END = "2026-11-25"
UCITS = ("SMH.L", "SEMI.AS", "SEMI.L")
STRATEGY_UCITS = ("SMH.L", "SEMI.AS")


def ledger_config(mult: float = 1.0) -> eur_ledger.EurLedgerConfig:
    return eur_ledger.EurLedgerConfig(
        capital=CAP,
        tax_rate=None,
        financing_bps=500.0 * mult,
        spread_bps=5.0 * mult,
        fx_bps=2.0 * mult,
        broker_bps=0.25 * mult,
        broker_fixed_eur=1.0,
        withholding_rate=0.15,
    )


def run_book(
    panel: pd.DataFrame,
    mem: pd.DataFrame,
    *,
    core: str,
    start: str,
    label: str,
    mult: float = 1.0,
) -> dict:
    orig = universe.canonical
    universe.canonical = orig
    kw = france.kwargs("US", "Interactive Brokers", "cto", CAP, panel)
    kw.update(
        start=start,
        end=END,
        leverage=2.5,
        financing_bps=500.0 * mult,
        trend_ma=200,
        trend_scale=0.5,
        rebalance_mode="calendar",
        exec_price="open",
        tax_lots="hifo",
        sector_cap=0.4,
        core=core,
        core_mode="relative",
        core_alt="SPY",
        cost_bps=5.0 * mult,
        fx_bps=2.0 * mult,
        us_bps=float(kw.get("us_bps", 0.0)) * mult,
        broker_bps=float(kw.get("broker_bps", 0.0)) * mult,
    )
    cfg = strategy.Config(**kw)
    mkt = strategy.Market(panel, mem, cfg)
    res = backtest.run(mkt)
    tax = france.after_tax(res, CAP, "US")
    divs = divdata.load_dividends(
        sorted(res.trades["ticker"].unique()), fetch_missing=False
    )
    led = eur_ledger.replay(
        res, panel, "US", config=ledger_config(mult), dividends=divs
    )
    eq = res.equity.dropna()
    years = len(eq) / 252.0
    return {
        "label": label,
        "core": core,
        "res": res,
        "ledger": led,
        "headline": tax["after_tax_eur"] / CAP,
        "native": float(eq.iloc[-1] - res.unrealized_tax),
        "ledger_mult": led.multiple,
        "headline_cagr": tax["cagr_eur"],
        "ledger_cagr": led.cagr,
        "maxdd": float((eq / eq.cummax() - 1.0).min()),
        "ledger_maxdd": led.maxdd,
        "y2022": annual_return(eq, 2022),
        "ledger_2022": annual_return(led.equity, 2022),
        "worst_12m": worst_12m(eq),
        "ledger_worst_12m": worst_12m(led.equity),
        "trades": len(res.trades),
        "div_tax": led.dividend_tax_total,
        "div_gross": led.dividends_gross,
        "div_events": led.dividend_events,
        "ledger_summary": summarize_equity(led.equity),
    }


def weekly_tracking(a: pd.Series, b: pd.Series) -> tuple[float, float, float]:
    """Annualised tracking difference, tracking error and correlation (weekly)."""
    ra = (1.0 + a.pct_change()).resample("W-FRI").prod() - 1.0
    rb = (1.0 + b.pct_change()).resample("W-FRI").prod() - 1.0
    df = pd.concat([ra, rb], axis=1).dropna()
    if len(df) < 3:
        return float("nan"), float("nan"), float("nan")
    d = df.iloc[:, 0] - df.iloc[:, 1]
    return (
        float(d.mean() * 52.0),
        float(d.std() * np.sqrt(52.0)),
        float(df.corr().iloc[0, 1]),
    )


def main() -> None:
    panel0, mem = markets.build("US")
    panel = add_ucits_to_panel(panel0)
    start = common_start(("SMH",) + UCITS)
    start_s = start.date().isoformat()

    # --- Vehicle-level total return over the common window.
    smh_usd = to_usd_series("SMH").loc[start:]
    fund_rows = []
    for sym in ("SMH",) + UCITS:
        s = to_usd_series(sym).loc[start:]
        st = fund_stats(s, smh_usd)
        weekly_td, weekly_te, weekly_corr = weekly_tracking(s, smh_usd)
        meta = FUNDS[sym]
        fund_rows.append(
            {
                "symbol": sym,
                "name": meta["name"],
                "domicile": meta["domicile"],
                "currency": meta["currency"],
                "acc": "Acc" if meta["accumulating"] else "Dist",
                "ucits": "Yes" if meta["ucits"] else "No",
                "ter": meta["ter"],
                "index": meta["index"],
                "total": st.total_return,
                "cagr": st.cagr,
                "vol": st.vol,
                "maxdd": st.maxdd,
                "td_daily": st.tracking_diff,
                "te_daily": st.tracking_error,
                "td_weekly": weekly_td,
                "te_weekly": weekly_te,
                "corr_weekly": weekly_corr,
                "beta_daily": st.beta,
            }
        )

    # --- Strategy runs: same dates, same signal, only the core vehicle changes.
    books = []
    for core in ("SMH",) + STRATEGY_UCITS:
        label = f"{core} core"
        books.append(run_book(panel, mem, core=core, start=start_s, label=label))
    static2 = run_book(
        panel, mem, core="SMH", start=start_s, label="SMH core @ 2x", mult=2.0
    )
    smhl2 = run_book(
        panel, mem, core="SMH.L", start=start_s, label="SMH.L core @ 2x", mult=2.0
    )
    semi2 = run_book(
        panel, mem, core="SEMI.AS", start=start_s, label="SEMI.AS core @ 2x", mult=2.0
    )
    full = run_book(
        panel, mem, core="SMH", start="2015-01-01", label="SMH core full window"
    )

    # --- 5-year windows on the EUR ledger (common window is 5.3y, so few).
    base_led = books[0]["ledger"].equity
    windows = []
    for b in books:
        ws = five_year_windows(b["ledger"].equity)
        bw = five_year_windows(base_led)
        bmap = {(w["start"], w["end"]): w["return"] for w in bw}
        common = [
            {
                "start": w["start"],
                "end": w["end"],
                "a": w["return"],
                "b": bmap[(w["start"], w["end"])],
                "gap": w["return"] - bmap[(w["start"], w["end"])],
            }
            for w in ws
            if (w["start"], w["end"]) in bmap
        ]
        summary = window_summary([w["a"] for w in common], [w["b"] for w in common])
        windows.append({"label": b["label"], "rows": common, "summary": summary})

    lines = [
        "# UCITS semiconductor core vs US-domiciled SMH",
        "",
        "Generated by `uv run python scripts/research_ucits_core.py`.",
        "Cache-only at run time. Data snapshot 2026-11-25; UCITS lines fetched",
        "individually (SMH.L, SEMI.AS, SEMI.L), no full refresh.",
        "",
        "## Fund metadata (checked 2026-11-26)",
        "",
        "| symbol | name | domicile | quote ccy | distribution | UCITS | TER | index |",
        "|---|---|---|---|---|---:|---:|---|",
    ]
    for r in fund_rows:
        lines.append(
            f"| {r['symbol']} | {r['name']} | {r['domicile']} | {r['currency']} | "
            f"{r['acc']} | {r['ucits']} | {r['ter']:.2%} | {r['index']} |"
        )
    lines += [
        "",
        "The Irish funds are accumulating: no annual distribution at investor",
        "level. US SMH distributes; its distributions are taxed annually in the",
        "French CTO. At fund level, the Irish fund still bears US withholding on",
        "its US holdings (15% treaty, embedded in NAV), so this is not a",
        "withholding-free structure.",
        "",
        "## Vehicle total return over the common window",
        "",
        f"Common window: **{start_s} to {END}** (SMH.L starts 2020-12-01; the",
        "iShares fund starts 2021-08-05). All series converted to USD with",
        "same-date FX; SEMI.L is quoted in GBP and converted with GBPUSD.",
        "",
        "| symbol | total return | CAGR | vol | maxDD | daily TD | weekly TD | weekly TE | weekly corr |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in fund_rows:
        lines.append(
            f"| {r['symbol']} | {format_pct(r['total'])} | {format_pct(r['cagr'])} | "
            f"{format_pct(r['vol'])} | {format_pct(r['maxdd'])} | "
            f"{format_pct(r['td_daily'])} | {format_pct(r['td_weekly'])} | "
            f"{format_pct(r['te_weekly'])} | {r['corr_weekly']:.2f} |"
        )
    lines += [
        "",
        "`TD` is annualised tracking difference vs US SMH; weekly TD is the",
        "more meaningful statistic because LSE/Amsterdam closes are not",
        "synchronous with the US close. SMH.L is a 10%-capped index; US SMH is",
        "25%-capped, so the tracking gap is partly index construction, not just",
        "vehicle cost. SEMI.AS is the global iShares index and is expected to",
        "diverge more.",
        "",
        "**SEMI.L data warning:** the Yahoo GBP line has an unadjusted 5:4",
        "split-like jump on 2024-01-08 (close 6.00 -> 4.81), so its raw total",
        "return is not usable. SEMI.AS is the same ISIN's USD line and is the",
        "iShares vehicle used in the strategy test.",
        "",
        "## Strategy results (US monthly 2.5x, same signal/dates)",
        "",
        f"Common window {start_s}–{END}; full-window SMH control 2015-01-01–{END}.",
        "The only change is `Config.core`. All books use the US stock sleeve,",
        "SMH or UCITS core, 5 bps spread, 2 bps FX, 5%/yr financing, French CTO",
        "and dividend-aware EUR ledger.",
        "",
        "| book | terminal-FX | native USD | EUR ledger + div | CAGR | maxDD USD | maxDD ledger | 2022 | worst 12m | trades | div tax |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for b in books:
        lines.append(
            f"| {b['label']} | **{format_multiple(b['headline'])}** | "
            f"{format_multiple(b['native'])} | **{format_multiple(b['ledger_mult'])}** | "
            f"{format_pct(b['ledger_cagr'])} | {format_pct(b['maxdd'])} | "
            f"{format_pct(b['ledger_maxdd'])} | {format_pct(b['y2022'])} | "
            f"{format_pct(b['worst_12m'])} | {b['trades']} | €{b['div_tax']:,.0f} |"
        )
    lines += [
        "",
        f"Full-window SMH control (2015-2026): **{format_multiple(full['ledger_mult'])}** "
        f"EUR ledger / {format_multiple(full['headline'])} terminal-FX, maxDD "
        f"{format_pct(full['ledger_maxdd'])}.",
        "",
        "## 5-year windows (EUR ledger)",
        "",
        "The common window is 5.3 years, so only a handful of 5-year starts are",
        "available. That is a limitation, not a full rolling test.",
        "",
        "| book | n | median gap vs SMH | worst gap | share beats SMH |",
        "|---|---:|---:|---:|---:|",
    ]
    for w in windows:
        s = w["summary"]
        lines.append(
            f"| {w['label']} | {s.get('n', 0)} | "
            f"{format_pct(s.get('median_gap', float('nan')))} | "
            f"{format_pct(min((x['gap'] for x in w['rows']), default=float('nan')))} | "
            f"{s.get('share_beat_benchmark', 0):.0%} |"
        )
    lines += [
        "",
        "## 2x costs",
        "",
        "| book | EUR ledger + div | CAGR | maxDD ledger |",
        "|---|---:|---:|---:|",
    ]
    for b in (books[0], static2, books[1], smhl2, books[2], semi2):
        lines.append(
            f"| {b['label']} | {format_multiple(b['ledger_mult'])} | "
            f"{format_pct(b['ledger_cagr'])} | {format_pct(b['ledger_maxdd'])} |"
        )
    lines += [
        "",
        "## Verdict",
        "",
    ]
    smh = books[0]
    smhl = books[1]
    semi = books[2]
    gates = {
        "SMH.L retains >=80% of SMH EUR ledger": (
            smhl["ledger_mult"] / smh["ledger_mult"] >= 0.80
        ),
        "SEMI.AS retains >=80% of SMH EUR ledger": (
            semi["ledger_mult"] / smh["ledger_mult"] >= 0.80
        ),
        "SMH.L survives 2x costs": smhl2["ledger_mult"] > 1.0,
        "SEMI.AS survives 2x costs": semi2["ledger_mult"] > 1.0,
    }
    for k, v in gates.items():
        lines.append(f"- {k}: **{'PASS' if v else 'FAIL'}**")
    lines += [
        "",
        (
            "**Verdict: `reject` as a core replacement.** The Irish accumulating "
            "structure has real tax/estate advantages, but the return gap is "
            f"not comparable: SMH.L ends at {format_multiple(smhl['ledger_mult'])} "
            f"vs SMH {format_multiple(smh['ledger_mult'])} "
            f"({smhl['ledger_mult'] / smh['ledger_mult']:.0%} retention), and "
            f"SEMI.AS at {format_multiple(semi['ledger_mult'])} "
            f"({semi['ledger_mult'] / smh['ledger_mult']:.0%}). The 10%-capped "
            "UCITS index and the global iShares index both lag the concentrated "
            "US SMH index over this window; the dividend-tax saving is far "
            "smaller than the return gap. Keep US SMH for the core; consider "
            "UCITS only as a paper/estate-planning overlay if the investor "
            "explicitly prices US estate-tax exposure above the return gap."
        ),
        "",
        "## Caveats",
        "",
        "- SMH.L's LSE close is not synchronous with the US close; daily",
        "  tracking/correlation understate the match. Weekly/monthly statistics",
        "  are reported as the fairer comparison.",
        "- The UCITS fund bears 15% US withholding at fund level; the investor",
        "  avoids the annual distribution tax but not the underlying WHT.",
        "- SEMI.L's Yahoo GBP line has an unadjusted corporate-action jump; only",
        "  the SEMI.AS USD line is used in the strategy test.",
        "- Irish domicile removes US situs estate exposure for the fund, but",
        "  this is a qualitative benefit, not a backtest return.",
        "- Common window is 5.3 years; 2018 is outside it. The 5-year table is",
        "  therefore a small sample, and the full-window SMH control is shown",
        "  only for context.",
        "",
    ]
    (ROOT / "results" / "ucits_core.md").write_text("\n".join(lines) + "\n")
    for b in books:
        print(
            f"{b['label']:20s} terminal {b['headline']:6.2f}x  "
            f"ledger {b['ledger_mult']:6.2f}x  DD {b['ledger_maxdd']:6.1%}"
        )
    print("wrote results/ucits_core.md")


if __name__ == "__main__":
    main()
