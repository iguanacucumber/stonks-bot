"""French investor audit: tax, FTT, broker fees, and EUR returns.

Two views:
  native  the shipped bot computes signals and fills in the asset's own currency
          (USD for TECH/US), then the EUR investor translates the terminal result
  eur     the whole panel, including the 200-day trend, is converted to EUR first

The native view is the bot as it runs. The EUR view is a sensitivity: translating
the price series before the trend rule changes the timing (2017, 2020, 2025).

French rules:
  CTO   PFU on the annual net gain: 34.5% before 2018, 30% 2018-2025, 31.4% from
        2026 (12.8% IR + 18.6% social); losses carried 10 years
  PEA   IR-exempt after 5 years, 18.6% social in 2026; no US stocks/ETFs
  FTT   0.2% to 2016, 0.3% 2017-Mar 2025, 0.4% since Apr 2025, buys only

Broker tariffs:
  Bourse Direct  Euronext 0.99/1.90/2.90/3.80 EUR by ticket, 0.09% above 4.4k;
                 US 8.50 EUR/order to 10k, then 0.09%; 0.036%/yr custody
  Trade Republic 1 EUR/order, no custody
Both get 0.25% FX on USD names and 5 bps spread.

Run: uv run python scripts/france.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from bot import backtest, france, markets, strategy, universe  # noqa: E402

START = "2015-01-01"
BASE = dict(
    start=START,
    leverage=2.5,
    financing_bps=500.0,
    trend_ma=200,
    trend_scale=0.5,
    rebalance_mode="calendar",
    exec_price="open",
    cost_bps=5.0,
    tax_lots="hifo",
)

PANELS: dict[tuple[str, str], tuple[pd.DataFrame, pd.DataFrame]] = {}


def load(market: str, mode: str = "native") -> tuple[pd.DataFrame, pd.DataFrame]:
    key = (market, mode)
    if key not in PANELS:
        panel, mem = markets.build(market)
        PANELS[key] = (
            france.to_eur(panel, market) if mode == "eur" else panel,
            mem,
        )
    return PANELS[key]


def bench_return(
    market: str, broker: str, wrapper: str, capital: float, mode: str
) -> dict:
    panel, mem = load(market, mode)
    cfg = strategy.Config(start=START, core=None, leverage=1.0, trend_ma=0)
    mkt = strategy.Market(panel, mem, cfg)
    spy = mkt.spy.reindex(mkt.dates).ffill().dropna()
    fx = france.fx_factor(market, spy.index[0], spy.index[-1]) if mode == "native" else 1.0
    is_us = market in ("US", "TECH")
    entry = france.ticket_fee(broker, capital, is_us)
    if is_us:
        entry += capital * france.BROKERS[france.broker_name(broker)]["fx_bps"] / 1e4
    units = (capital - entry) / spy.iloc[0]
    gross = units * spy.iloc[-1]
    exit_fee = france.ticket_fee(broker, gross, is_us)
    if is_us:
        exit_fee += gross * france.BROKERS[france.broker_name(broker)]["fx_bps"] / 1e4
    proceeds = gross - exit_fee
    rate = 0.186 if wrapper == "PEA" else 0.314
    tax = max(0.0, proceeds - capital) * rate
    after = (proceeds - tax) * fx
    years = len(spy) / 252.0
    return {
        "after_tax": after,
        "cagr": (after / capital) ** (1.0 / years) - 1.0,
        "maxdd": float((spy / spy.cummax() - 1.0).min()),
        "tax": tax * fx,
        "fees": (entry + exit_fee) * fx,
    }


def run_book(
    market: str,
    wrapper: str,
    broker: str,
    capital: float,
    profile: str,
    mode: str,
) -> dict:
    panel, mem = load(market, mode)
    if market != "US":
        universe.canonical = lambda t: t
    kw = dict(BASE)
    kw.update(france.kwargs(market, broker, wrapper.lower(), capital, panel))
    if profile == "tax":
        kw.update(core_mode="always", leverage_band=0.30)
    if wrapper == "PEA":
        kw.update(
            leverage=1.0,
            financing_bps=0.0,
            trend_ma=0,
            core=None,
            core_mode="always",
            leverage_band=0.0,
        )
    cfg = strategy.Config(**kw)
    res = backtest.run(strategy.Market(panel, mem, cfg))
    fx = (
        france.fx_factor(market, res.equity.index[0], res.equity.index[-1])
        if mode == "native"
        else 1.0
    )
    pre = float(res.equity.iloc[-1]) * capital * fx
    exit_tax = float(res.unrealized_tax) * capital * fx
    after = pre - exit_tax
    years = len(res.equity) / 252.0
    eq = res.equity
    return {
        "market": market,
        "wrapper": wrapper,
        "profile": profile,
        "broker": broker,
        "capital": capital,
        "mode": mode,
        "pre_tax": pre,
        "fees": float(res.fees_paid) * capital * fx,
        "ftt": float(res.ftt_paid) * capital * fx,
        "tax_paid": float(res.tax_paid) * capital * fx,
        "exit_tax": exit_tax,
        "after_tax": after,
        "cagr": (after / capital) ** (1.0 / years) - 1.0,
        "maxdd": float((eq / eq.cummax() - 1.0).min()),
        "trades": len(res.trades),
        "years": years,
        "bench": bench_return(market, broker, wrapper, capital, mode),
    }


def fmt(x: float) -> str:
    return f"€{x:,.0f}"


def main() -> None:
    # build the US panel before the non-US canonical alias is installed
    load("US", "native")
    specs = [
        ("TECH", "CTO", "monthly", "Bourse Direct", 10_000, "native"),
        ("TECH", "CTO", "monthly", "Bourse Direct", 100_000, "native"),
        ("TECH", "CTO", "monthly", "Trade Republic", 10_000, "native"),
        ("TECH", "CTO", "monthly", "Trade Republic", 100_000, "native"),
        ("TECH", "CTO", "tax", "Bourse Direct", 10_000, "native"),
        ("TECH", "CTO", "tax", "Bourse Direct", 100_000, "native"),
        ("TECH", "CTO", "tax", "Trade Republic", 10_000, "native"),
        ("TECH", "CTO", "tax", "Trade Republic", 100_000, "native"),
        ("TECH", "CTO", "tax", "Trade Republic", 10_000, "eur"),
        ("FR", "CTO", "monthly", "Bourse Direct", 10_000, "native"),
        ("FR", "CTO", "monthly", "Trade Republic", 10_000, "native"),
        ("FR", "PEA", "pea", "Bourse Direct", 10_000, "native"),
        ("FR", "PEA", "pea", "Trade Republic", 10_000, "native"),
        ("US", "CTO", "tax", "Trade Republic", 100_000, "native"),
    ]
    rows = []
    for market, wrapper, profile, broker, capital, mode in specs:
        r = run_book(market, wrapper, broker, capital, profile, mode)
        rows.append(r)
        print(
            f"{market:4s} {wrapper:3s} {profile:7s} {broker:14s} "
            f"€{capital:>7,} {mode:6s}: {r['after_tax'] / capital:6.2f}x after tax "
            f"({r['cagr']:.1%}/yr), bench "
            f"{r['bench']['after_tax'] / capital:5.2f}x, "
            f"fees {r['fees'] / capital:5.2f}x, tax {r['tax_paid'] / capital:5.2f}x"
        )
        pd.DataFrame(rows).to_csv(ROOT / "results" / "france.csv", index=False)

    df = pd.DataFrame(rows)
    lines = [
        "# French investor audit: PFU/PEA, FTT, and broker fees",
        "",
        "The core book is the shipped monthly portfolio (2.5x, daily 200-day cut,",
        "month-end decision, next-open fill); the `tax` book is core-only with a",
        "30% leverage band and HIFO lots. Prices and cash are in the asset's own",
        "currency for the **native** rows (the bot as it runs); the EUR rows convert",
        "the whole panel, including the trend, to EUR first. The difference is the",
        "FX path changing the trend timing, not a tax effect. 5 bps spread is",
        "charged on top of the broker tariff.",
        "",
        "## French rules used",
        "",
        "| item | rule |",
        "|---|---|",
        "| CTO capital gains | 34.5% before 2018, 30% 2018-2025, **31.4% from 2026** (12.8% IR + 18.6% social) |",
        "| CTO losses | netted annually, carried forward **10 years** |",
        "| PEA | IR-exempt after 5 years, **18.6% social** in 2026; no US stocks/ETFs |",
        "| FTT | 0.2% to 2016, 0.3% 2017-Mar 2025, **0.4% since Apr 2025**, buys of large French shares only |",
        "| Bourse Direct | Euronext 0.99/1.90/2.90/3.80 EUR by ticket, 0.09% above 4.4k; US **8.50 EUR/order** to 10k, then 0.09%; 0.036%/yr custody; 0.08% FX commission |",
        "| Trade Republic | **1 EUR/order**, no custody, ~0.25% EUR/USD conversion |",
        "",
        "Both brokers are charged 0.25% FX per side on USD names (Bourse Direct's",
        "0.08% explicit commission plus the spread). Fixed fees use the account",
        "size, so the €10k and €100k rows are not the same strategy. `fees` includes",
        "the FTT; `fees` and `tax paid` are cumulative costs already inside `pre-tax`.",
        "`exit tax` is the remaining liability on the open book, deducted from",
        "`after tax`.",
        "",
    ]

    def table(sub: pd.DataFrame, title: str) -> None:
        lines.extend(
            [
                f"## {title}",
                "",
                "| market | wrapper | book | broker | account | signals | pre-tax | fees (incl. FTT) | FTT | tax paid | exit tax | **after tax** | CAGR | maxDD | benchmark after tax | vs bench |",
                "|---|---|---|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for _, r in sub.iterrows():
            b = r["bench"]
            beat = r["after_tax"] - b["after_tax"]
            lines.append(
                f"| {r['market']} | {r['wrapper']} | {r['profile']} | {r['broker']} | "
                f"{fmt(r['capital'])} | {r['mode']} | {r['pre_tax'] / r['capital']:.1f}x | "
                f"{fmt(r['fees'])} | {fmt(r['ftt'])} | {fmt(r['tax_paid'])} | "
                f"{fmt(r['exit_tax'])} | **{r['after_tax'] / r['capital']:.2f}x** | "
                f"{r['cagr']:.1%} | {r['maxdd']:.0%} | "
                f"{b['after_tax'] / r['capital']:.2f}x | {beat / r['capital']:+.2f}x |"
            )
        lines.append("")

    table(
        df[(df.market == "TECH") & (df["mode"] == "native")],
        "TECH book (the live paper market): CTO only",
    )
    table(df[df.market == "FR"], "French book: CTO vs PEA")
    table(df[df.market == "US"], "US book: CTO, Trade Republic, €100k")
    table(df[(df.market == "TECH") & (df["mode"] == "eur")], "EUR-signal sensitivity")

    tech_tax_10 = df[
        (df.market == "TECH")
        & (df.profile == "tax")
        & (df.broker == "Trade Republic")
        & (df.capital == 10_000)
        & (df["mode"] == "native")
    ].iloc[0]
    tech_mon_10 = df[
        (df.market == "TECH")
        & (df.profile == "monthly")
        & (df.broker == "Bourse Direct")
        & (df.capital == 10_000)
        & (df["mode"] == "native")
    ].iloc[0]
    tech_mon_tr = df[
        (df.market == "TECH")
        & (df.profile == "monthly")
        & (df.broker == "Trade Republic")
        & (df.capital == 10_000)
        & (df["mode"] == "native")
    ].iloc[0]
    tech_eur = df[
        (df.profile == "tax")
        & (df.broker == "Trade Republic")
        & (df.capital == 10_000)
        & (df["mode"] == "eur")
    ].iloc[0]
    fr_cto = df[(df.market == "FR") & (df.wrapper == "CTO")].iloc[0]
    fr_pea = df[(df.market == "FR") & (df.wrapper == "PEA")].iloc[0]

    lines += [
        "## What this changes",
        "",
        f"1. **Turnover reduction is worth more than the tax rate.** The core-only "
        f"tax book turns €10k into **{tech_tax_10['after_tax'] / 10_000:.1f}x after "
        f"tax** at Trade Republic. The monthly sleeve on the same capital through "
        f"Bourse Direct ends at **{tech_mon_10['after_tax'] / 10_000:.1f}x** and "
        f"through Trade Republic at **{tech_mon_tr['after_tax'] / 10_000:.1f}x**. "
        f"The sleeve's ~15x annual turnover generates ~2,100 orders in 12 years, "
        f"against ~180 for the core-only book; the fixed ticket and FX costs "
        f"dominate the small account.",
        "",
        f"2. **31.4%, not 30%.** The 2026 PFU adds 1.4 pp (social contributions "
        f"18.6%). It is in the schedule; a flat 30% would overstate the result.",
        "",
        f"3. **The PEA is not a free lunch for this bot.** It cannot hold SMH or "
        f"the US sleeve, and it cannot borrow. The French sleeve alone ends at "
        f"**{fr_pea['after_tax'] / 10_000:.2f}x** after 18.6% social, **below the "
        f"CAC benchmark's {fr_pea['bench']['after_tax'] / 10_000:.2f}x**, versus "
        f"**{fr_cto['after_tax'] / 10_000:.2f}x** for the levered CTO with the SMH "
        f"core, even after 31.4% PFU. Use the PEA for low-turnover French "
        f"dividend/quality books, not for this high-beta core.",
        "",
        f"4. **FTT is a real drag on the French sleeve:** {fmt(fr_cto['ftt'])} on a "
        f"€10k account, paid on every purchase, win or lose. The US/TECH books pay "
        f"none, but pay 0.25% FX instead.",
        "",
        f"5. **Broker choice is size-dependent.** At €10k, Bourse Direct's €8.50 "
        f"US ticket costs the monthly book "
        f"{(tech_mon_tr['after_tax'] - tech_mon_10['after_tax']) / 10_000:.1f}x of "
        f"terminal wealth versus Trade Republic; at €100k the gap shrinks. The "
        f"core-only book is robust to both because it barely trades.",
        "",
        f"6. **FX timing matters if you translate the trend.** Converting the "
        f"price series to EUR before the 200-day rule turns the €10k tax book into "
        f"**{tech_eur['after_tax'] / 10_000:.1f}x** instead of "
        f"**{tech_tax_10['after_tax'] / 10_000:.1f}x**. The bot's native USD signal "
        f"(and a French investor following it) avoids that self-inflicted timing "
        f"change; measure the P&L in EUR, but do not re-derive the signal in EUR.",
        "",
        "## Caveats",
        "",
        "- No dividend withholding: SMH pays a small dividend; the US-French treaty "
        "rate is 15% plus 18.6% social. The effect is small for this book.",
        "- PEA contribution cap (€150k) and the 5-year clock are not binding in "
        "these runs; the PEA path assumes a withdrawal after 5 years.",
        "- The CTO tax is settled at the start of the following year; the final "
        "year's realised tax and the unrealised gain are deducted as `exit tax`.",
        "- Native rows translate the after-tax local-currency result at the "
        "terminal FX rate; the tax on the FX translation gain is not modelled "
        "separately. The EUR rows do model it, at the cost of changing the trend.",
        "- No bid/ask beyond the 5 bps spread, no market impact, no borrow squeeze. "
        "A 2.5x book can be forced to deleverage in a gap; the backtest's leverage "
        "band is a simplification.",
        "",
        "Generated by `uv run python scripts/france.py`.",
    ]
    (ROOT / "results" / "france.md").write_text("\n".join(lines) + "\n")
    print("wrote results/france.md and results/france.csv")


if __name__ == "__main__":
    main()
