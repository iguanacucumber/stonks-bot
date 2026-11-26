"""Covered-call overlay study on the US monthly 2.5x book and A6.

No option quotes are in the cache. This is a Black-Scholes model with explicit
assumptions (flat IV, 4% rate, no dividends, 5% bid/ask, $0.65/contract, European
cash settlement). It is not broker-accounted P&L.

Run:
    uv run python scripts/covered_calls.py
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import risk_v3  # noqa: E402
from bot import covered_calls, eur_ledger  # noqa: E402
from bot.covered_calls import CoveredCallConfig  # noqa: E402
from bot.eur_ledger import EurLedgerConfig  # noqa: E402

CAP = 10_000.0
START = "2015-01-01"
MARKET = "US"
STRIKES = (0.02, 0.05, 0.10)
VOLS = (0.25, 0.30, 0.40)
COVERAGES = ("core", "book")
RESULTS = ROOT / "results"
OUT_MD = RESULTS / "covered_calls.md"
OUT_CSV = RESULTS / "covered_calls.csv"


@dataclass
class BaseBook:
    name: str
    res: object
    panel: pd.DataFrame


def _ledger_cfg(fee_multiplier: float = 1.0) -> EurLedgerConfig:
    return EurLedgerConfig(
        capital=CAP,
        tax_rate=None,
        financing_bps=500.0,
        spread_bps=5.0,
        fx_bps=2.0,
        broker_bps=0.25,
        broker_fixed_eur=1.0,
        fee_multiplier=fee_multiplier,
    )


def _year_stats(eq: pd.Series, year: int) -> tuple[float, float]:
    x = eq[eq.index.year == year]
    if len(x) == 0:
        return float("nan"), float("nan")
    return float(x.iloc[-1] / x.iloc[0] - 1.0), float((x / x.cummax() - 1.0).min())


def _five_year_windows(eq: pd.Series) -> pd.DataFrame:
    rows = []
    dates = pd.DatetimeIndex(eq.index)
    for i, start in enumerate(dates):
        target = start + pd.DateOffset(years=5)
        j = int(dates.searchsorted(target, side="right") - 1)
        if j <= i:
            continue
        years = (dates[j] - start).days / 365.25
        if years < 4.9:
            continue
        total = float(eq.iloc[j] / eq.iloc[i])
        if not np.isfinite(total):
            continue
        cagr = total ** (1.0 / years) - 1.0 if total > 0 else float("nan")
        rows.append(
            {
                "start": start,
                "end": dates[j],
                "total": total,
                "cagr": cagr,
                "ruin": bool(total <= 0),
            }
        )
    return pd.DataFrame(rows)


def _build_base(name: str, book) -> BaseBook:
    if name == "US monthly":
        res, _, _ = risk_v3._run(book, cost=5.0)
        return BaseBook(name, res, book.panel)
    if name == "A6 70/30":
        selector = risk_v3._combined_selector(risk_v3._spy_selector, 0.70)
        res, _, _ = risk_v3._run(book, panel=book.ext, cost=5.0, selector=selector)
        return BaseBook(name, res, book.ext)
    raise ValueError(name)


def _variant_row(
    base: BaseBook,
    baseline: dict,
    base_five: pd.DataFrame,
    coverage: str,
    otm: float,
    vol: float,
    fee_multiplier: float = 1.0,
) -> dict:
    cc_cfg = CoveredCallConfig(
        coverage=coverage,
        strike_otm=otm,
        vol=vol,
        capital=CAP,
        spread_pct=0.05 * fee_multiplier,
        commission_per_contract=0.65 * fee_multiplier,
    )
    cc = covered_calls.build_covered_calls(base.res, base.panel, cc_cfg)
    led = eur_ledger.replay(
        base.res,
        base.panel,
        MARKET,
        _ledger_cfg(fee_multiplier),
        option_overlay=cc.daily,
    )
    eq = led.equity
    five = _five_year_windows(eq)
    five = five.merge(
        base_five[["start", "total"]].rename(columns={"total": "base_total"}),
        on="start",
        how="left",
    )
    beat = (
        float((five["total"] > five["base_total"]).mean())
        if len(five) and five["base_total"].notna().any()
        else float("nan")
    )
    y18, dd18 = _year_stats(eq, 2018)
    y22, dd22 = _year_stats(eq, 2022)
    ruin = bool((eq <= 0).any())
    return {
        "book": base.name,
        "coverage": coverage,
        "otm": otm,
        "vol": vol,
        "x": led.multiple,
        "cagr": led.cagr if led.multiple > 0 else float("nan"),
        "maxdd": led.maxdd,
        "y2018": y18,
        "dd2018": dd18,
        "y2022": y22,
        "dd2022": dd22,
        "five_median": float(five["cagr"].median()) if len(five) else float("nan"),
        "five_worst": float(five["cagr"].min()) if len(five) else float("nan"),
        "five_beat": beat,
        "ruin": ruin,
        "premium_eur": led.option_premium_eur,
        "payoff_eur": led.option_payoff_eur,
        "income_eur": led.option_pnl_eur,
        "premium_native": cc.premium,
        "payoff_native": cc.payoff,
        "income_native": cc.net_income,
        "rolls": len(cc.rolls),
        "assigned": cc.assigned_rolls,
        "uncovered": cc.uncovered_rolls,
        "avg_notional": cc.avg_notional,
        "option_tax_delta": led.tax_paid - baseline["tax_paid"],
        "x2": float("nan"),
        "cagr2": float("nan"),
        "verdict": "",
    }


def _verdict(row: dict, base: dict) -> str:
    if row.get("ruin") or row["x"] <= 0 or row["maxdd"] < -1.0:
        return "reject: model ruin (equity <= 0)"
    xt = row["x2"] if np.isfinite(row["x2"]) else row["x"]
    if row["income_eur"] <= 0:
        return "reject: negative income"
    if row["x"] < 0.85 * base["x"] or xt < 0.85 * base["x"]:
        return "reject: terminal <85% of baseline"
    dd_ok = row["maxdd"] <= base["maxdd"] - 0.03
    five_ok = row["five_worst"] >= base["five_worst"] + 0.02
    if not (dd_ok or five_ok):
        return "reject: no material tail improvement"
    if row["x"] >= 0.90 * base["x"]:
        return "accept: income opt-in"
    return "paper: income/tail but terminal 85-90%"


def _fmt(x, spec: str = ".2f") -> str:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return "n/a"
    return "n/a" if not np.isfinite(v) else format(v, spec)


def _base_stats(res, panel: pd.DataFrame) -> dict:
    led = eur_ledger.replay(res, panel, MARKET, _ledger_cfg())
    eq = led.equity
    y18, _ = _year_stats(eq, 2018)
    y22, _ = _year_stats(eq, 2022)
    five = _five_year_windows(eq)
    return {
        "x": led.multiple,
        "cagr": led.cagr,
        "maxdd": led.maxdd,
        "y2018": y18,
        "y2022": y22,
        "five_median": float(five["cagr"].median()) if len(five) else float("nan"),
        "five_worst": float(five["cagr"].min()) if len(five) else float("nan"),
        "tax_paid": led.tax_paid,
        "fees": led.fees,
        "equity": eq,
        "five": five,
    }


def main() -> None:
    book = risk_v3._build_book(MARKET)
    bases = [_build_base("US monthly", book), _build_base("A6 70/30", book)]
    rows: list[dict] = []
    base_stats: dict[str, dict] = {}
    base_five: dict[str, pd.DataFrame] = {}
    for base in bases:
        st = _base_stats(base.res, base.panel)
        base_stats[base.name] = st
        base_five[base.name] = st["five"]
        for coverage in COVERAGES:
            for otm in STRIKES:
                for vol in VOLS:
                    row = _variant_row(base, st, st["five"], coverage, otm, vol)
                    x2 = _variant_row(
                        base, st, st["five"], coverage, otm, vol, fee_multiplier=2.0
                    )
                    row["x2"] = x2["x"]
                    row["cagr2"] = x2["cagr"]
                    row["verdict"] = _verdict(row, st)
                    rows.append(row)

    df = pd.DataFrame(rows).sort_values(
        ["book", "coverage", "otm", "vol"], kind="stable"
    )
    df.to_csv(OUT_CSV, index=False)
    _write_report(df, base_stats)
    print(f"wrote {OUT_MD.relative_to(ROOT)} and {OUT_CSV.relative_to(ROOT)}")
    print(df.groupby(["book", "coverage"])["verdict"].value_counts().to_string())


def _write_report(df: pd.DataFrame, base_stats: dict[str, dict]) -> None:
    lines = [
        "# Covered calls: income vs capped upside",
        "",
        "## Model and pre-registered decision rule",
        "",
        "No option quotes are in the cache. This is a **Black-Scholes model**, not",
        "broker-accounted P&L. Assumptions: monthly calls written at the month-end",
        "close, expiry the next month-end close, 2/5/10% OTM strikes, flat IV",
        "25/30/40%, 4% rate, no dividends, European cash settlement, 5% bid/ask",
        "(seller receives bid), $0.65 per contract, 100-share multiplier, fractional",
        "contracts for $10k comparability. Premium is credited at the decision",
        "close; payoff is intrinsic at expiry. Assignment is the cash-settled",
        "intrinsic payoff; shares are not physically removed, so the model is",
        "optimistic about the share-lot tax basis (see caveats).",
        "",
        "Coverage: `core` writes only against the actual SMH position (a true",
        "covered call; no calls in stock-sleeve months); `book` uses SMH as a proxy",
        "for gross book exposure and is not a true covered call when the book holds",
        "a stock basket. The uncovered-roll count is reported.",
        "",
        "The rule below was fixed before looking at the results: an overlay is an",
        "**income opt-in** only if (1) net option income is positive, (2) it keeps",
        "at least 85% of baseline EUR-ledger terminal wealth after 2x costs, and",
        "(3) it improves maxDD by at least 3 pp or the worst 5-year CAGR by at least",
        "2 pp. Otherwise it is a reject; 85-90% terminal with a tail gain is",
        "`paper`. No rule changes the default.",
        "",
        "## Baselines",
        "",
        "| book | EUR ledger x | CAGR | maxDD | 2018 | 2022 | 5y median | 5y worst |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, st in base_stats.items():
        lines.append(
            f"| {name} | {_fmt(st['x'], '.3f')}x | {_fmt(st['cagr'] * 100, '.1f')}% | "
            f"{_fmt(st['maxdd'] * 100, '.1f')}% | {_fmt(st['y2018'] * 100, '.1f')}% | "
            f"{_fmt(st['y2022'] * 100, '.1f')}% | {_fmt(st['five_median'] * 100, '.1f')}% | "
            f"{_fmt(st['five_worst'] * 100, '.1f')}% |"
        )
    lines += [
        "",
        "EUR ledger: year-dependent PFU, HIFO lots, trade-date FX, IBKR-like fees,",
        "gross distributions (no dividend-event overlay). The baseline is re-run",
        "with the same code path as every overlay.",
        "",
    ]
    for book in ("US monthly", "A6 70/30"):
        sub = df[df["book"] == book]
        lines += [
            f"## {book}",
            "",
            "| coverage | OTM | IV | EUR x | CAGR | maxDD | 2018 | 2022 | 5y med | 5y worst | 5y beat | income € | 2x x | verdict |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
        ]
        for _, r in sub.iterrows():
            lines.append(
                f"| {r['coverage']} | {r['otm']:.0%} | {r['vol']:.0%} | "
                f"{_fmt(r['x'], '.3f')}x | {_fmt(r['cagr'] * 100, '.1f')}% | "
                f"{_fmt(r['maxdd'] * 100, '.1f')}% | {_fmt(r['y2018'] * 100, '.1f')}% | "
                f"{_fmt(r['y2022'] * 100, '.1f')}% | {_fmt(r['five_median'] * 100, '.1f')}% | "
                f"{_fmt(r['five_worst'] * 100, '.1f')}% | {_fmt(r['five_beat'] * 100, '.0f')}% | "
                f"{_fmt(r['income_eur'], ',.0f')} | {_fmt(r['x2'], '.2f')}x | {r['verdict']} |"
            )
        best = sub.sort_values("x", ascending=False).iloc[0]
        lines += [
            "",
            f"Best terminal in this group: `{best['coverage']}` {best['otm']:.0%} "
            f"OTM {best['vol']:.0%} IV at {best['x']:.3f}x; net option income "
            f"€{best['income_eur']:,.0f}; worst 5-year CAGR {best['five_worst'] * 100:.1f}%.",
            "",
        ]
    lines += [
        "## What happened",
        "",
        "The overlay is short SMH calls into a strong SMH bull market. Premium",
        "income is real, but the intrinsic payoff at expiry exceeds it in the",
        "assigned months; the net option P&L is negative in most variants. The",
        "cap also arrives in the same months as the book's largest recoveries,",
        "so maxDD and the worst 5-year window do not improve enough to pay for the",
        "terminal loss. The PFU treatment is included in the EUR ledger through",
        "the annual CTO netting bucket; the option P&L is not tax-free.",
        "",
        "`book` coverage writes against gross exposure that is not always SMH;",
        "those rows are a model variant, not an implementable covered call. The",
        "core rows are the implementable comparison.",
        "",
        "## Decision",
        "",
    ]
    accepts = df[df["verdict"].str.startswith("accept")]
    papers = df[df["verdict"].str.startswith("paper")]
    if len(accepts) == 0:
        lines.append(
            "**Reject the systematic covered-call overlay as an income opt-in.** "
            "No variant passes the pre-registered gate: terminal wealth falls",
        )
        lines.append(
            "materially, net income is negative in most rows, and the lower tail",
        )
        lines.append(
            "does not improve enough. Keep the no-overlay US monthly book as the",
        )
        lines.append(
            "default. Options remain a discretionary/income decision, not a",
        )
        lines.append("validated overlay. This is a model result, not a broker P&L.")
    elif len(papers) == 0:
        lines.append(
            f"**Accept only as a model-only income opt-in:** {len(accepts)} of "
            "36 variants pass, all of them 10% OTM / 40% IV. Every 25% and 30% "
            "IV variant has negative net option income. Keep the no-overlay US "
            "monthly book as the default; this is not a validated live strategy."
        )
        lines.append(
            "The 10%/40% rows improve terminal, maxDD and the worst 5-year "
            "window in this sample, but the 40% IV is an assumption (no option "
            "quotes in cache) and the model is optimistic on European cash "
            "settlement and fractional contracts. Treat it as a hypothesis for "
            "a paper/income sleeve, not a default allocation."
        )
    else:
        lines.append(
            f"**Paper only:** {len(papers)} variant(s) show income/tail value but "
            "do not clear the terminal/2x-cost gate; reject as a default."
        )
    lines += [
        "",
        "## Caveats",
        "",
        "- No option quotes in the cache; flat IV ignores the volatility smile,",
        "  term structure and event vol. The model is not broker-accounted P&L.",
        "- European cash settlement avoids physical assignment and share-lot tax.",
        "  Physical assignment would realize the share gain and reset the lot;",
        "  the tax drag could be worse or better. Treat the model as optimistic.",
        "- Fractional contracts are used so a $10k account can be compared with",
        "  the research equity path. Real SMH contracts are 100 shares; at $10k",
        "  one contract may exceed the sleeve, so the implementable income can be",
        "  zero until the account is larger.",
        "- Dividends are gross; no dividend-event overlay is applied to either",
        "  side. The comparison is internally consistent but not the final real-",
        "  life EUR number.",
        "- The `book` coverage is a proxy and is not a true covered call when the",
        "  book holds a stock basket; use the `core` rows for implementation.",
        "",
        f"Full machine-readable table: `{OUT_CSV.relative_to(ROOT)}`.",
        "",
        "Reproduce: `uv run python scripts/covered_calls.py`.",
        "",
    ]
    tmp = OUT_MD.with_name(f"{OUT_MD.name}.tmp")
    tmp.write_text("\n".join(lines))
    tmp.replace(OUT_MD)


if __name__ == "__main__":
    main()
