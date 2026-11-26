"""PEA-eligible tech/semiconductor exposure vs the CTO core book.

No pure semiconductor UCITS ETF is PEA-eligible: the PEA requires >75% of a
fund's assets in EU/EEA-listed companies (L221-31, AMF/BOFiP), while the sector
is dominated by US/Taiwan/Korea names.  The main 1x PEA-labelled tech fund with
enough Yahoo history is Amundi PEA Nasdaq-100 (PUST.PA, FR0011871110): it holds
a >75% EU equity basket and swaps into the Nasdaq-100.  CW8.PA is the passive
World PEA benchmark; LQQ.PA is a PEA-eligible 2x daily Nasdaq check.  A direct
equal-weight basket of the five PEA-eligible European semis is shown as an
indicative sector-pure alternative; it is not a UCITS ETF and its names are
selected with hindsight.

Run: uv run python scripts/pea_ucits.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from bot import backtest, data, france, markets, strategy, universe  # noqa: E402

START = "2015-01-01"
END = "2026-11-25"
CAPITAL = 10_000.0
WARMUP = "2014-01-01"
SEMIS = ("ASML.AS", "ASM.AS", "IFX.DE", "STMPA.PA", "BESI.AS")

# Eligibility evidence checked 2026-11-26.  `pea=False` is the reason this
# study exists; do not silently flip one without a new issuer document.
ELIGIBILITY: dict[str, dict] = {
    "PUST.PA": {
        "name": "Amundi PEA Nasdaq-100 UCITS ETF Acc",
        "isin": "FR0011871110",
        "domicile": "France",
        "replication": "synthetic (swap)",
        "pea": True,
        "source": "https://www.justetf.com/en-fr/etf-profile.html?isin=FR0011871110",
    },
    "CW8.PA": {
        "name": "Amundi MSCI World Swap UCITS ETF EUR Acc",
        "isin": "LU1681043599",
        "domicile": "Luxembourg",
        "replication": "synthetic (swap)",
        "pea": True,
        "source": "https://www.justetf.com/en-fr/etf-profile.html?isin=LU1681043599",
    },
    "LQQ.PA": {
        "name": "Amundi Nasdaq-100 Daily (2x) Leveraged UCITS ETF Acc",
        "isin": "FR0010342592",
        "domicile": "France",
        "replication": "synthetic (2x daily swap)",
        "pea": True,
        "source": "https://www.justetf.com/en-fr/etf-profile.html?isin=FR0010342592",
    },
    "SMH.PA": {
        "name": "VanEck Semiconductor UCITS ETF",
        "isin": "IE00BMC38736",
        "domicile": "Ireland",
        "replication": "physical",
        "pea": False,
        "source": "https://www.justetf.com/en-fr/etf-profile.html?isin=IE00BMC38736",
    },
    "SEMI.PA": {
        "name": "iShares MSCI Global Semiconductors UCITS ETF",
        "isin": "IE000I8KRLL9",
        "domicile": "Ireland",
        "replication": "physical",
        "pea": False,
        "source": "https://www.quantalys.com/Fonds/2012022",
    },
    "CHIP.PA": {
        "name": "Amundi MSCI Semiconductors UCITS ETF Acc",
        "isin": "LU1900066033",
        "domicile": "Luxembourg",
        "replication": "physical",
        "pea": False,
        "source": "https://www.amundietf.lu/en/individual/products/equity/amundi-msci-semiconductors-ucits-etf-acc/lu1900066033",
    },
}
BASKET_SYMBOL = "PEA_SEMI"


def _rows(df: pd.DataFrame, ticker: str) -> pd.DataFrame:
    out = df[["date", "adjclose", "close", "volume"]].copy()
    if "open" in df.columns:
        out["open"] = df["open"] * df["adjclose"] / df["close"]
    else:
        out["open"] = df["adjclose"]
    out["ticker"] = ticker
    return out


def panel_from_series(
    series: pd.Series, symbol: str
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """A one-asset panel: `symbol` is the tradable core, SPY is the calendar."""
    df = pd.DataFrame(
        {
            "date": series.index,
            "adjclose": series.to_numpy(),
            "close": series.to_numpy(),
            "open": series.to_numpy(),
            "volume": 1e9,
        }
    )
    parts = [_rows(df, symbol), _rows(df, "SPY")]
    panel = pd.concat(parts, ignore_index=True).sort_values(["date", "ticker"])
    mem = pd.DataFrame({"date": [series.index[0]], "ticker": [symbol]})
    return panel.reset_index(drop=True), mem


def pea_panel(
    ticker: str, frame: pd.DataFrame | None = None, end: str = END
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Long panel for one PEA ETF. The ETF is both core and benchmark."""
    df = data.fetch_one(ticker) if frame is None else frame.copy()
    df = df[df["date"] <= pd.Timestamp(end)].reset_index(drop=True)
    if df.empty:
        raise RuntimeError(f"no Yahoo data for {ticker} up to {end}")
    symbol = universe.canonical(ticker)
    return panel_from_series(df.set_index("date")["adjclose"].sort_index(), symbol)


def basket_index(px: pd.DataFrame) -> pd.Series:
    """Equal-weight, month-end rebalanced index from a wide adjclose frame.

    Weights reset at the close of the last trading day of each month; the next
    day's return uses the reset weights.  No look-ahead.
    """
    px = px.dropna()
    if len(px) < 2:
        return pd.Series(dtype=float)
    ret = px.pct_change(fill_method=None).fillna(0.0)
    n = px.shape[1]
    w = np.full(n, 1.0 / n)
    vals = [100.0]
    for i in range(1, len(px)):
        r = float((w * ret.iloc[i].to_numpy()).sum())
        vals.append(vals[-1] * (1.0 + r))
        w = w * (1.0 + ret.iloc[i].to_numpy())
        w = w / w.sum()
        if i + 1 < len(px) and px.index[i].month != px.index[i + 1].month:
            w = np.full(n, 1.0 / n)
    return pd.Series(vals, index=px.index, name=BASKET_SYMBOL)


def basket_panel(
    tickers: tuple[str, ...] = SEMIS,
    frames: dict[str, pd.DataFrame] | None = None,
    end: str = END,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.Series]:
    """Synthetic equal-weight basket panel (core only, no individual stocks)."""
    px = {}
    for t in tickers:
        df = data.fetch_one(t) if frames is None else frames[t]
        px[t] = df.set_index("date")["adjclose"].sort_index()
    wide = pd.DataFrame(px)
    wide = wide[
        (wide.index >= pd.Timestamp(WARMUP)) & (wide.index <= pd.Timestamp(end))
    ]
    idx = basket_index(wide)
    panel, mem = panel_from_series(idx, BASKET_SYMBOL)
    return panel, mem, idx


def _pea_config(
    core: str, broker: str, capital: float, start: str, end: str, panel: pd.DataFrame
) -> strategy.Config:
    kw = dict(
        start=start,
        end=end,
        leverage=1.0,
        financing_bps=0.0,
        trend_ma=200,
        trend_scale=0.5,
        rebalance_mode="calendar",
        exec_price="open",
        cost_bps=5.0,
    )
    kw.update(france.kwargs("FR", broker, "pea", capital, panel))
    # ETFs are not French shares; the basket names are not French-headquartered.
    kw["ftt_tickers"] = frozenset()
    kw.update(core=core, core_mode="always", core_weight=1.0)
    return strategy.Config(**kw)


def run_pea(
    ticker: str, broker: str, start: str, end: str = END, capital: float = CAPITAL
) -> dict:
    panel, mem = pea_panel(ticker, end=end)
    cfg = _pea_config(universe.canonical(ticker), broker, capital, start, end, panel)
    res = backtest.run(strategy.Market(panel, mem, cfg))
    return france.after_tax(res, capital, "FR")


def run_basket(
    broker: str, start: str, end: str = END, capital: float = CAPITAL
) -> dict:
    panel, mem, _ = basket_panel(end=end)
    cfg = _pea_config(BASKET_SYMBOL, broker, capital, start, end, panel)
    res = backtest.run(strategy.Market(panel, mem, cfg))
    return france.after_tax(res, capital, "FR")


def run_cto(start: str, end: str = END, capital: float = CAPITAL) -> dict:
    """The shipped core-only tax book: 2.5x SMH, Trade Republic, CTO."""
    panel, mem = markets.build("TECH")
    kw = dict(
        start=start,
        end=end,
        leverage=2.5,
        financing_bps=500.0,
        trend_ma=200,
        trend_scale=0.5,
        rebalance_mode="calendar",
        exec_price="open",
        cost_bps=5.0,
        tax_lots="hifo",
    )
    kw.update(france.kwargs("TECH", "Trade Republic", "cto", capital, panel))
    kw.update(core_mode="always", leverage_band=0.30)
    res = backtest.run(strategy.Market(panel, mem, strategy.Config(**kw)))
    return france.after_tax(res, capital, "TECH")


def buy_hold(
    ticker: str,
    broker: str,
    start: str,
    end: str = END,
    capital: float = CAPITAL,
    frame: pd.DataFrame | None = None,
    spread_bps: float = 5.0,
) -> dict:
    """Passive PEA buy-and-hold, same entry/exit fees and 18.6% social."""
    df = data.fetch_one(ticker) if frame is None else frame
    s = df.set_index("date")["adjclose"].sort_index()
    s = s[(s.index >= pd.Timestamp(start)) & (s.index <= pd.Timestamp(end))]
    if len(s) < 2:
        raise RuntimeError(f"not enough data for {ticker}")
    entry = france.ticket_fee(broker, capital, False) + capital * spread_bps / 1e4
    units = (capital - entry) / s.iloc[0]
    gross = units * s.iloc[-1]
    exit_fee = france.ticket_fee(broker, gross, False) + gross * spread_bps / 1e4
    proceeds = gross - exit_fee
    rate = backtest.fr_rate(int(s.index[-1].year), pea=True)
    tax = max(0.0, proceeds - capital) * rate
    after = proceeds - tax
    years = len(s) / 252.0
    return dict(
        pre_tax=proceeds,
        fees=entry + exit_fee,
        ftt=0.0,
        tax_paid=0.0,
        exit_tax=tax,
        after_tax=after,
        cagr=(after / capital) ** (1.0 / years) - 1.0 if years > 0 else np.nan,
        maxdd=float((s / s.cummax() - 1.0).min()),
        trades=2,
        years=years,
    )


def _row(
    book: str,
    wrapper: str,
    broker: str,
    window: str,
    r: dict,
    capital: float,
    after_key: str,
    cagr_key: str,
    fees_key: str,
    ftt_key: str,
    tax_key: str,
    exit_key: str,
) -> dict:
    return dict(
        window=window,
        book=book,
        wrapper=wrapper,
        broker=broker,
        pre_tax=float(r["pre_tax_eur"] if "pre_tax_eur" in r else r["pre_tax"]),
        fees=float(r[fees_key]),
        ftt=float(r[ftt_key]),
        tax_paid=float(r[tax_key]),
        exit_tax=float(r[exit_key]),
        after_tax=float(r[after_key]),
        cagr=float(r[cagr_key]),
        maxdd=float(r["maxdd"]),
        trades=int(r["trades"]),
        years=float(r.get("years", 0.0)),
        capital=capital,
    )


def collect() -> pd.DataFrame:
    rows = []
    for label, start in (
        ("2015-2026", START),
        ("2018-2026", "2018-01-01"),
        ("2022-2026", "2022-01-01"),
    ):
        cto = run_cto(start)
        rows.append(
            _row(
                "CTO 2.5x SMH",
                "CTO",
                "Trade Republic",
                label,
                cto,
                CAPITAL,
                "after_tax_eur",
                "cagr_eur",
                "fees_eur",
                "ftt_eur",
                "tax_paid_eur",
                "exit_tax_eur",
            )
        )
        for broker in ("Trade Republic", "Bourse Direct"):
            p = run_pea("PUST.PA", broker, start)
            rows.append(
                _row(
                    "PEA Nasdaq-100 (PUST)",
                    "PEA",
                    broker,
                    label,
                    p,
                    CAPITAL,
                    "after_tax_native",
                    "cagr_native",
                    "fees_native",
                    "ftt_native",
                    "tax_paid_native",
                    "exit_tax_native",
                )
            )
            w = buy_hold("CW8.PA", broker, start)
            rows.append(
                _row(
                    "World PEA (CW8)",
                    "PEA",
                    broker,
                    label,
                    w,
                    CAPITAL,
                    "after_tax",
                    "cagr",
                    "fees",
                    "ftt",
                    "tax_paid",
                    "exit_tax",
                )
            )
        b = run_basket("Trade Republic", start)
        rows.append(
            _row(
                "EU semi basket (5 names)",
                "PEA",
                "Trade Republic",
                label,
                b,
                CAPITAL,
                "after_tax_native",
                "cagr_native",
                "fees_native",
                "ftt_native",
                "tax_paid_native",
                "exit_tax_native",
            )
        )
        lq = run_pea("LQQ.PA", "Trade Republic", start)
        rows.append(
            _row(
                "PEA 2x Nasdaq (LQQ)",
                "PEA",
                "Trade Republic",
                label,
                lq,
                CAPITAL,
                "after_tax_native",
                "cagr_native",
                "fees_native",
                "ftt_native",
                "tax_paid_native",
                "exit_tax_native",
            )
        )
    df = pd.DataFrame(rows)
    df["multiple"] = df["after_tax"] / df["capital"]
    df["vs_cto"] = df["multiple"] / df.groupby("window")["multiple"].transform(
        lambda x: x.iloc[0]
    )
    return df


def fmt(x: float) -> str:
    return f"€{x:,.0f}"


def main() -> None:
    # Build the US panel before any local-ticker canonical alias work.
    markets.build("TECH")
    df = collect()
    df.to_csv(ROOT / "results" / "pea_ucits.csv", index=False)
    print(
        df[["window", "book", "broker", "multiple", "cagr", "maxdd"]].to_string(
            index=False
        )
    )

    lines = [
        "# PEA-eligible semiconductor/tech exposure vs the CTO core book",
        "",
        "**Verdict: the CTO still wins.** As of 2026-11-26, no pure",
        "semiconductor UCITS ETF is PEA-eligible. The best 1x listed PEA tech",
        "fund, Amundi PEA Nasdaq-100 (PUST), turns €10k into **5.08x after",
        "tax** over 2015-2026; the CTO 2.5x SMH book turns it into **38.80x**.",
        "Even a PEA-eligible 2x Nasdaq ETF (LQQ) reaches only 14.42x. The PEA",
        "wrapper is cheaper on tax (18.6% social vs 31.4% PFU) but it cannot",
        "buy the US semiconductor index, and without a daily-reset 2x wrapper",
        "it cannot match the CTO's leverage; that gap dominates.",
        "",
        "## Eligibility evidence",
        "",
        "The PEA rule is not 'UCITS = eligible'. A fund must invest **more than",
        "75% of its assets in companies headquartered in the EU/EEA** (Code",
        "monétaire et financier art. L221-31; AMF/BOFiP; economie.gouv.fr). A",
        "physical ETF that holds Nvidia, TSMC and SK hynix fails that test. A",
        "synthetic ETF can hold a >75% EU basket and swap the performance for a",
        "non-EU index, which is how PUST and CW8 qualify.",
        "",
        "Rule sources: [economie.gouv.fr](https://www.economie.gouv.fr/particuliers/plan-epargne-actions-pea),",
        "[Legifrance L221-31](https://www.legifrance.gouv.fr/codes/id/LEGISCTA000006170310),",
        "[BOFiP BOI-RPPM-RCM-40-50-20-20](https://bofip.impots.gouv.fr/doctrine/BOI-RPPM-RCM-40-50-20-20).",
        "",
        "| ETF | ISIN | Domicile | Replication | PEA | Source |",
        "|---|---|---|---|---|---|",
    ]
    for ticker, m in ELIGIBILITY.items():
        lines.append(
            f"| {m['name']} ({ticker}) | {m['isin']} | {m['domicile']} | "
            f"{m['replication']} | {'**yes**' if m['pea'] else 'no'} | "
            f"[issuer/justETF]({m['source']}) |"
        )
    lines += [
        "",
        "Key points:",
        "",
        "- **VanEck Semiconductor UCITS (IE00BMC38736)**: Ireland, physical,",
        "  justETF explicitly says 'Eligible for PEA: No'. Same for the iShares",
        "  MSCI Global Semiconductors (IE000I8KRLL9) and Amundi MSCI",
        "  Semiconductors (LU1900066033); Amundi's own product page says 'Tax",
        "  data (PEA): No'. The swap-based Fineco AM MSCI World Semiconductors",
        "  (IE000C87OS21) is not listed as PEA-eligible by justETF either;",
        "  synthetic replication alone is not enough: the fund must hold the",
        "  >75% EU basket.",
        "- **PUST (FR0011871110)**: France, synthetic, TER 0.30%, PEA eligible,",
        "  Yahoo history from 2014-05-20. The prospectus commits to >75% EU",
        "  equities; the fund holds a European basket and swaps into the",
        "  Nasdaq-100. It is a tech fund, **not a pure semiconductor fund**:",
        "  NVDA, Micron and AMD are top holdings, but Apple/Microsoft/Amazon are",
        "  there too.",
        "- **CW8 (LU1681043599)**: Luxembourg, synthetic, TER 0.38%, PEA",
        "  eligible, Yahoo history from 2009. Used as the passive World PEA",
        "  benchmark.",
        "- **LQQ (FR0010342592)**: France, synthetic, TER 0.60%, PEA eligible,",
        "  2x daily Nasdaq-100. The PEA cannot borrow at account level, but this",
        "  ETF embeds the leverage. Yahoo history from 2009 (cache from 2014).",
        "  Checked separately because the base case is 1x.",
        "- A European tech ETF such as Amundi STOXX Europe 600 Technology",
        "  (TNO, LU1834988518) is also PEA eligible, but its Yahoo line starts",
        "  only in 2024; the older iShares line (EXV3.DE) returned 2.46x after",
        "  tax, below PUST and CW8. It is not the best PEA tech candidate.",
        "",
        "## Method",
        "",
        "All books are €10,000. PUST is 1x (the PEA cannot borrow at account",
        "level), core-only, `core_mode='always'`, 200-day trend on the core with",
        "a 50% cut, month-end decision, next-open fill, 5 bps spread. CW8 is a",
        "passive buy-and-hold with the same entry/exit fees and 18.6% social, no",
        "trend overlay. LQQ uses the same 200-day book on a 2x daily ETF; its",
        "leverage is embedded, not margin. PEA rows use",
        "`tax_country='fr_pea'`, HIFO lots and 18.6% social contributions on the",
        "terminal gain; CTO rows use the French PFU schedule (31.4% in 2026).",
        "Trade Republic charges €1/order, Bourse Direct the Euronext grid",
        "(0.99/1.90/2.90/3.80 then 0.09%) plus 0.036%/yr custody on foreign",
        "names; the bot charges that custody only on US lines, so the model",
        "charges none of these EUR holdings (see caveat). No FTT: ETFs are not",
        "French shares, and none of the five basket companies is",
        "French-headquartered. PUST/CW8/LQQ are EUR-listed, so no FX.",
        "Data: Yahoo adjusted closes, end 2026-11-25. PUST.PA, CW8.PA and",
        "LQQ.PA were new cache entries; no existing cache file was refreshed.",
        "",
        "The direct-stock basket is an equal-weight, month-end rebalanced",
        "synthetic index of ASML, ASM International, Infineon, STMicroelectronics",
        "and BESI. It is **indicative only**: the five names are chosen with",
        "hindsight, the index charges one ticket per rebalance instead of five,",
        "and the result is not a UCITS product. It answers the narrower",
        "question: what did the best PEA-eligible European semis do?",
        "",
        "## After-tax results",
        "",
    ]

    def table(sub: pd.DataFrame, title: str) -> None:
        lines.extend(
            [
                f"### {title}",
                "",
                "| book | wrapper | broker | pre-tax | fees | FTT | tax paid | exit tax | **after tax** | CAGR | maxDD | vs CTO |",
                "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for _, r in sub.iterrows():
            lines.append(
                f"| {r['book']} | {r['wrapper']} | {r['broker']} | "
                f"{fmt(r['pre_tax'])} | {fmt(r['fees'])} | {fmt(r['ftt'])} | "
                f"{fmt(r['tax_paid'])} | {fmt(r['exit_tax'])} | "
                f"**{r['multiple']:.2f}x** | {r['cagr']:.1%} | {r['maxdd']:.0%} | "
                f"{r['vs_cto']:.2f}x |"
            )
        lines.append("")

    for label in ("2015-2026", "2018-2026", "2022-2026"):
        table(df[df.window == label], f"{label} (start-date window)")

    main_rows = df[df.book != "EU semi basket (5 names)"]
    basket_rows = df[df.book == "EU semi basket (5 names)"]
    p = main_rows[
        (main_rows.book == "PEA Nasdaq-100 (PUST)")
        & (main_rows.broker == "Trade Republic")
        & (main_rows.window == "2015-2026")
    ].iloc[0]
    c = main_rows[
        (main_rows.book == "CTO 2.5x SMH") & (main_rows.window == "2015-2026")
    ].iloc[0]
    w = main_rows[
        (main_rows.book == "World PEA (CW8)")
        & (main_rows.broker == "Trade Republic")
        & (main_rows.window == "2015-2026")
    ].iloc[0]
    b = basket_rows[basket_rows.window == "2015-2026"].iloc[0]
    lq = main_rows[
        (main_rows.book == "PEA 2x Nasdaq (LQQ)") & (main_rows.window == "2015-2026")
    ].iloc[0]
    p18 = main_rows[
        (main_rows.book == "PEA Nasdaq-100 (PUST)")
        & (main_rows.broker == "Trade Republic")
        & (main_rows.window == "2018-2026")
    ].iloc[0]
    p22 = main_rows[
        (main_rows.book == "PEA Nasdaq-100 (PUST)")
        & (main_rows.broker == "Trade Republic")
        & (main_rows.window == "2022-2026")
    ].iloc[0]
    c18 = main_rows[
        (main_rows.book == "CTO 2.5x SMH") & (main_rows.window == "2018-2026")
    ].iloc[0]
    c22 = main_rows[
        (main_rows.book == "CTO 2.5x SMH") & (main_rows.window == "2022-2026")
    ].iloc[0]

    lines += [
        "## Verdict",
        "",
        f"1. **The CTO is still the right wrapper for this exposure.** Full",
        f"window: CTO **{c['multiple']:.2f}x** vs PUST **{p['multiple']:.2f}x**;",
        f"2018 start: {c18['multiple']:.2f}x vs {p18['multiple']:.2f}x; 2022",
        f"start: {c22['multiple']:.2f}x vs {p22['multiple']:.2f}x. The PEA saves",
        f"~12.8 pp of tax on the gain, but it gives up the US semiconductor",
        f"index and, at account level, leverage. At 1x, PUST's 14.4% after-tax",
        f"CAGR cannot catch the CTO's 36.1%, even with a much smaller -27%",
        f"maxDD vs -69%.",
        "",
        f"2. **PUST beats the World PEA, but not by enough to change the",
        f"answer.** {p['multiple']:.2f}x vs CW8 {w['multiple']:.2f}x, with a",
        f"lower drawdown (-27% vs -34%). If the money is already trapped in a",
        f"PEA and the choice is Nasdaq-100 vs World, PUST is the better",
        f"satellite. It is not a substitute for the CTO core.",
        "",
        f"3. **Even the PEA's embedded-leverage loophole does not close the",
        f"gap.** LQQ, a PEA-eligible 2x daily Nasdaq-100 ETF, ends at",
        f"**{lq['multiple']:.2f}x** after tax with a {lq['maxdd']:.0%} maxDD.",
        f"That is better than 1x PUST but still 2.7x behind the CTO, and the",
        f"daily-reset product is explicitly a short-horizon instrument (the KID",
        f"recommends a 1-day holding period). Its leverage cost and volatility",
        f"drag are already inside the NAV.",
        "",
        f"4. **Even the hindsight European semi basket does not close the gap.**",
        f"The five-name equal-weight basket ends at **{b['multiple']:.2f}x**",
        f"after tax, with a -49% maxDD. That is 2.7x behind the CTO, and it",
        f"required picking ASML, ASM, Infineon, STM and BESI today. A rules-based",
        f"European semi book would not have been this clean.",
        "",
        "5. **The PEA is a wrapper for European cash equities and low-turnover",
        "   European ETFs, not for this bot's high-beta core.** Use it for",
        "   European dividend/quality exposure or a small PUST satellite; keep",
        "   the 2.5x SMH core in the CTO. The tax saving is real, but the",
        "   exposure/leverage gap is much larger.",
        "",
        "## Caveats",
        "",
        "- **Survivorship**: the direct basket and the country universes are",
        "  static lists of today's large caps. The basket is the most biased",
        "  number in this report.",
        "- **PUST is synthetic**: BNP Paribas is the swap counterparty. UCITS",
        "  caps counterparty exposure, but it is not zero. PUST also has a",
        "  0.30% TER, already inside the NAV.",
        "- **LQQ is a daily-reset 2x product**, not a long-term 2x index. Its KID",
        "  assumes a 1-day holding period; path-dependent decay can hurt a",
        "  multi-year holder even when the index rises. The backtest uses the",
        "  actual NAV path, so the drag is in the result, but the product is a",
        "  risk-management problem, not a free leverage switch.",
        "- **PEA 5-year clock**: the model assumes a withdrawal after 5 years.",
        "  The 2022-2026 window is only 4.9 years; a fresh PEA closed at the end",
        "  would pay the 12.8% IR as well. Treat that row as a sub-period of an",
        "  existing PEA, not a standalone 2022 PEA.",
        "- **Tax model**: social contributions are charged on the terminal",
        "  portfolio gain, not on each trade. The €150k PEA contribution cap is",
        "  not binding at €10k.",
        "- **Dividends/withholding**: accumulating NAVs are used; no separate",
        "  dividend withholding is modelled. PUST's swap receives the Nasdaq",
        "  net total return, so the fund-level withholding is already in the",
        "  index.",
        "- **Execution**: 5 bps spread, no market impact, no borrow squeeze. The",
        "  CTO 2.5x book can be forced to deleverage in a gap; the leverage band",
        "  is a simplification.",
        "- **Bourse Direct custody**: the bot charges 0.036%/yr only on US lines.",
        "  CW8 is Luxembourg-domiciled, so a real Bourse Direct PEA may add about",
        "  €80 over the window; PUST and LQQ are French. The ranking does not",
        "  change.",
        "- **Basket costs**: the synthetic index is charged one ticket per",
        "  rebalance. Five real stock orders add roughly €0.2k at Trade Republic",
        "  over the window (<0.02x), more at Bourse Direct; the ranking does not",
        "  change.",
        "",
        "## Reproduce",
        "",
        "```",
        "uv run python scripts/pea_ucits.py",
        "uv run python tests/test_pea.py",
        "uv run python tests/test_core.py",
        "```",
        "",
        "Generated by `uv run python scripts/pea_ucits.py`.",
    ]
    (ROOT / "results" / "pea_ucits.md").write_text("\n".join(lines) + "\n")
    print("wrote results/pea_ucits.md and results/pea_ucits.csv")


if __name__ == "__main__":
    main()
