"""PEA-eligible semiconductor/tech re-check, 2026.

Run: uv run python scripts/pea_semis.py

This is a research/reporting script. It does not change any trading default.
The pre-registration and verdict live in results/pea_semis.md.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from bot import (  # noqa: E402
    backtest,
    dividends as divdata,
    eur_ledger,
    france,
    markets,
    strategy,
    universe,
)
from scripts.france import BASE  # noqa: E402
import pea_ucits as base  # noqa: E402

CAP = 10_000.0
END = "2026-11-25"
ORIG_CANONICAL = universe.canonical
ELIGIBILITY_AS_OF = "2026-11-26"
WINDOWS = {
    "2015-2026": ("2015-01-01", END),
    "2019-2026": ("2019-04-25", END),
    "2022-2026": ("2022-01-01", END),
}
# Eligibility checked 2026-11-26 against issuer/justETF pages.
ELIGIBILITY = {
    "PUST.PA": dict(
        name="Amundi PEA Nasdaq-100 UCITS ETF Acc",
        isin="FR0011871110",
        domicile="France",
        replication="synthetic (swap)",
        index="Nasdaq-100",
        pea=True,
        source="https://www.justetf.com/en/etf-profile.html?isin=FR0011871110",
    ),
    "PSP5.PA": dict(
        name="Amundi PEA S&P 500 UCITS ETF Acc",
        isin="FR0011871128",
        domicile="France",
        replication="synthetic (swap)",
        index="S&P 500",
        pea=True,
        source="https://www.justetf.com/en/etf-profile.html?isin=FR0011871128",
    ),
    "PE500.PA": dict(
        name="Amundi PEA S&P 500 Screened UCITS ETF Acc",
        isin="FR0013412285",
        domicile="France",
        replication="synthetic (swap)",
        index="S&P 500 Scored & Screened+",
        pea=True,
        source="https://www.justetf.com/en/etf-profile.html?isin=FR0013412285",
    ),
    "CW8.PA": dict(
        name="Amundi MSCI World Swap UCITS ETF EUR Acc",
        isin="LU1681043599",
        domicile="Luxembourg",
        replication="synthetic (swap)",
        index="MSCI World",
        pea=True,
        source="https://www.justetf.com/en/etf-profile.html?isin=LU1681043599",
    ),
    "LQQ.PA": dict(
        name="Amundi Nasdaq-100 Daily (2x) Leveraged UCITS ETF Acc",
        isin="FR0010342592",
        domicile="France",
        replication="synthetic (2x daily swap)",
        index="Nasdaq-100 2x daily",
        pea=True,
        source="https://www.justetf.com/en/etf-profile.html?isin=FR0010342592",
    ),
    "TNO.PA": dict(
        name="Amundi STOXX Europe 600 Technology UCITS ETF Acc",
        isin="LU1834988518",
        domicile="Luxembourg",
        replication="physical",
        index="STOXX Europe 600 Technology",
        pea=True,
        source="https://www.justetf.com/en/etf-profile.html?isin=LU1834988518",
    ),
    "SMH.PA": dict(
        name="VanEck Semiconductor UCITS ETF",
        isin="IE00BMC38736",
        domicile="Ireland",
        replication="physical",
        index="MVIS US Listed Semiconductor 10% Capped",
        pea=False,
        source="https://www.justetf.com/en/etf-profile.html?isin=IE00BMC38736",
    ),
    "SEMI.PA": dict(
        name="iShares MSCI Global Semiconductors UCITS ETF",
        isin="IE000I8KRLL9",
        domicile="Ireland",
        replication="physical",
        index="MSCI ACWI IMI Semiconductors",
        pea=False,
        source="https://www.justetf.com/en/etf-profile.html?isin=IE000I8KRLL9",
    ),
    "CHIP.PA": dict(
        name="Amundi MSCI Semiconductors UCITS ETF Acc",
        isin="LU1900066033",
        domicile="Luxembourg",
        replication="physical",
        index="MSCI ACWI Semiconductors",
        pea=False,
        source="https://www.justetf.com/en/etf-profile.html?isin=LU1900066033",
    ),
}
PEA_CANDIDATES = ("PUST.PA", "PSP5.PA", "PE500.PA", "CW8.PA", "LQQ.PA")


def _year(eq: pd.Series, year: int) -> float:
    e = eq[eq.index.year == year]
    return float(e.iloc[-1] / e.iloc[0] - 1.0) if len(e) > 2 else float("nan")


def momentum_signal(px: pd.Series, date, lookback: int = 252, skip: int = 21) -> bool:
    """True when the 12-1 total return is positive; NaN history holds.

    Uses only `px` through `date`: no future observation can change the value.
    """
    try:
        p1 = float(px.shift(skip).loc[date])
        p0 = float(px.shift(lookback).loc[date])
    except KeyError:
        return True
    if not np.isfinite(p1) or not np.isfinite(p0) or p0 <= 0:
        return True
    return bool(p1 / p0 - 1.0 > 0)


def run_cto_us(start: str, end: str = END) -> dict:
    """US monthly 2.5x SMH, French CTO, EUR ledger with cached dividends."""
    panel, mem = markets.build("US")
    universe.canonical = ORIG_CANONICAL
    kw = dict(BASE)
    kw.update(start=start, end=end)
    kw.update(france.kwargs("US", "Interactive Brokers", "cto", CAP, panel))
    res = backtest.run(strategy.Market(panel, mem, strategy.Config(**kw)))
    tickers = sorted(set(res.trades["ticker"])) if len(res.trades) else []
    divs = divdata.load_dividends(tickers, fetch_missing=False)
    led = eur_ledger.replay(
        res,
        panel,
        "US",
        eur_ledger.EurLedgerConfig(
            capital=CAP,
            tax_rate=None,
            financing_bps=500.0,
            spread_bps=5.0,
            fx_bps=2.0,
            broker_bps=0.25,
            broker_fixed_eur=1.0,
            withholding_rate=0.15,
        ),
        dividends=divs if len(divs) else None,
    )
    return {
        "multiple": led.multiple,
        "cagr": led.cagr,
        "maxdd": led.maxdd,
        "pre_tax": float(led.equity.iloc[-1]),
        "fees": led.fees,
        "tax_paid": led.tax_paid,
        "exit_tax": led.exit_tax,
        "y2018": _year(led.equity, 2018),
        "y2022": _year(led.equity, 2022),
        "trades": len(res.trades),
        "kind": "CTO US monthly",
    }


def run_buy_hold(
    ticker: str,
    broker: str,
    start: str,
    end: str = END,
    cost_mult: float = 1.0,
) -> dict:
    df = base.data.fetch_one(ticker)
    s = df.set_index("date")["adjclose"].sort_index()
    s = s[(s.index >= pd.Timestamp(start)) & (s.index <= pd.Timestamp(end))]
    if len(s) < 2:
        raise RuntimeError(f"not enough data for {ticker}")
    entry = (
        france.ticket_fee(broker, CAP, False) + CAP * 5.0 * cost_mult / 1e4
    ) * cost_mult
    units = (CAP - entry) / s.iloc[0]
    gross = units * s.iloc[-1]
    exit_fee = (
        france.ticket_fee(broker, gross, False) + gross * 5.0 * cost_mult / 1e4
    ) * cost_mult
    proceeds = gross - exit_fee
    rate = backtest.fr_rate(int(s.index[-1].year), pea=True)
    tax = max(0.0, proceeds - CAP) * rate
    after = proceeds - tax
    years = len(s) / 252.0
    return {
        "multiple": after / CAP,
        "cagr": (after / CAP) ** (1.0 / years) - 1.0 if years > 0 else np.nan,
        "maxdd": float((s / s.cummax() - 1.0).min()),
        "pre_tax": proceeds,
        "fees": entry + exit_fee,
        "tax_paid": 0.0,
        "exit_tax": tax,
        "y2018": _year(s, 2018),
        "y2022": _year(s, 2022),
        "trades": 2,
        "kind": "buy & hold",
    }


def _scale_cfg(cfg: strategy.Config, cost_mult: float) -> strategy.Config:
    if cost_mult == 1.0:
        return cfg
    return replace(
        cfg,
        cost_bps=cfg.cost_bps * cost_mult,
        broker_bps=cfg.broker_bps * cost_mult,
        us_bps=cfg.us_bps * cost_mult,
        broker_grid=tuple((limit, fee * cost_mult) for limit, fee in cfg.broker_grid),
        us_grid=tuple((limit, fee * cost_mult) for limit, fee in cfg.us_grid),
    )


def run_momentum(
    ticker: str,
    broker: str,
    start: str,
    end: str = END,
    cost_mult: float = 1.0,
) -> dict:
    """Monthly 12-1 absolute momentum: hold above zero, else cash."""
    panel, mem = base.pea_panel(ticker, end=end)
    cfg = base._pea_config(universe.canonical(ticker), broker, CAP, start, end, panel)
    cfg = _scale_cfg(replace(cfg, trend_ma=0), cost_mult)
    core = cfg.core

    def overlay(date, weights, mkt):
        return weights if momentum_signal(mkt.px[core], date) else {}

    res = backtest.run(strategy.Market(panel, mem, cfg), overlay=overlay)
    f = france.after_tax(res, CAP, "FR")
    return {
        "multiple": f["after_tax_native"] / CAP,
        "cagr": f["cagr_native"],
        "maxdd": f["maxdd"],
        "pre_tax": f["pre_tax_native"],
        "fees": f["fees_native"],
        "tax_paid": f["tax_paid_native"],
        "exit_tax": f["exit_tax_native"],
        "y2018": _year(res.equity, 2018),
        "y2022": _year(res.equity, 2022),
        "trades": len(res.trades),
        "kind": "monthly momentum 12-1",
    }


def collect() -> pd.DataFrame:
    rows: list[dict] = []
    for window, (start, end) in WINDOWS.items():
        cto = run_cto_us(start, end)
        rows.append(
            dict(
                window=window,
                book="CTO US monthly 2.5x",
                wrapper="CTO",
                broker="IBKR",
                **cto,
            )
        )
        for ticker in PEA_CANDIDATES:
            if ticker == "PE500.PA" and window == "2015-2026":
                continue
            if ticker == "PE500.PA" and start < "2019-04-25":
                continue
            for kind, fn in (
                ("buy & hold", run_buy_hold),
                ("monthly momentum", run_momentum),
            ):
                try:
                    r = fn(ticker, "Trade Republic", start, end)
                except Exception as exc:  # noqa: BLE001 - report missing data
                    print(f"skip {ticker} {window} {kind}: {exc}")
                    continue
                rows.append(
                    dict(
                        window=window,
                        book=f"{ticker} {ELIGIBILITY[ticker]['name']}",
                        wrapper="PEA",
                        broker="Trade Republic",
                        **r,
                    )
                )
    # Fee sensitivities for the best PEA candidate (PUST).
    start, end = WINDOWS["2015-2026"]
    for label, fn in (
        ("buy & hold 2x cost", run_buy_hold),
        ("monthly momentum 2x cost", run_momentum),
    ):
        r = fn("PUST.PA", "Trade Republic", start, end, cost_mult=2.0)
        row = dict(
            window="2015-2026",
            book=f"PUST.PA {ELIGIBILITY['PUST.PA']['name']}",
            wrapper="PEA",
            broker="Trade Republic",
            **r,
        )
        row["kind"] = label
        rows.append(row)
    r = run_buy_hold("PUST.PA", "Bourse Direct", start, end)
    row = dict(
        window="2015-2026",
        book=f"PUST.PA {ELIGIBILITY['PUST.PA']['name']}",
        wrapper="PEA",
        broker="Bourse Direct",
        **r,
    )
    row["kind"] = "buy & hold"
    rows.append(row)
    df = pd.DataFrame(rows)
    df["vs_cto"] = df.groupby("window")["multiple"].transform(lambda x: x / x.iloc[0])
    return df


def fmt(x: float) -> str:
    return f"€{x:,.0f}"


def main() -> None:
    df = collect()
    df.to_csv(ROOT / "results" / "pea_semis.csv", index=False)
    print(
        df[["window", "book", "kind", "multiple", "cagr", "maxdd"]].to_string(
            index=False
        )
    )
    lines = []
    for window in WINDOWS:
        sub = df[df.window == window]
        lines += [
            f"\n### {window}\n",
            "| book | wrapper | variant | after tax | CAGR | maxDD | 2018 | 2022 | vs CTO | fees | exit tax | trades |",
            "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for _, r in sub.iterrows():
            lines.append(
                f"| {r['book']} | {r['wrapper']} | {r['kind']} | "
                f"**{r['multiple']:.2f}x** | {r['cagr']:.1%} | {r['maxdd']:.1%} | "
                f"{_fmt_ret(r['y2018'])} | {_fmt_ret(r['y2022'])} | "
                f"{r['vs_cto']:.2f}x | {fmt(r['fees'])} | {fmt(r['exit_tax'])} | "
                f"{int(r['trades'])} |"
            )
    (ROOT / "results" / "pea_semis_tables.md").write_text("\n".join(lines) + "\n")
    print("wrote results/pea_semis.csv and results/pea_semis_tables.md")


def _fmt_ret(x: float) -> str:
    return "n/a" if not np.isfinite(x) else f"{x:+.1%}"


if __name__ == "__main__":
    main()
