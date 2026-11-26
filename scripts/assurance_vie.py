"""Assurance-vie wrapper study for the US monthly and balanced A6 books.

Run: uv run python scripts/assurance_vie.py

This is a planning model, not tax or legal advice. It compares the merged CTO
EUR ledger with the same gross strategy inside an assurance-vie contract:
annual unit-linked fee, per-trade contract fee, 8-year French AV tax and the
990 I death regime. No network; the price cache is only used to run the
strategy path.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import pandas as pd  # noqa: E402

from bot import (
    backtest,
    dividends as dividend_events,
    eur_ledger,
    france,
    markets,
    profiles,
    strategy,
    universe,
)  # noqa: E402
from bot import assurance_vie as av  # noqa: E402
from bot import estate  # noqa: E402
from bot.evaluation import windows  # noqa: E402
from paper_daily import BOOKS  # noqa: E402
from scripts.france import BASE  # noqa: E402

CAP = 10_000.0
ORIG_CANONICAL = universe.canonical
FEES = (0.005, 0.006, 0.008)
TRANSACTION_BPS = 10.0


def _config(profile: str, tax: bool, panel) -> strategy.Config:
    kw = dict(BASE)
    kw.update(france.kwargs("US", "Interactive Brokers", "cto", CAP, panel))
    if not tax:
        kw.update(
            tax_country="us",
            tax_rate=0.0,
            state_tax_rate=0.0,
            wash_sale_days=0,
        )
    return strategy.Config(**kw)


def run_strategy(panel, mem, profile: str, tax: bool):
    cfg = _config(profile, tax, panel)
    selector = profiles.selector_for(profile)
    mkt = strategy.Market(panel, mem, cfg)
    return backtest.run(mkt, selector=selector)


def cto_result(res, panel, market: str = "US") -> dict:
    tickers = sorted(set(res.trades["ticker"])) if len(res.trades) else []
    divs = dividend_events.load_dividends(tickers, fetch_missing=False)
    led = eur_ledger.replay(
        res,
        panel,
        market,
        eur_ledger.EurLedgerConfig(
            capital=CAP,
            financing_bps=500.0,
            spread_bps=5.0,
            fx_bps=2.0,
            broker_bps=0.25,
            broker_fixed_eur=1.0,
        ),
        dividends=divs if len(divs) else None,
    )
    return {
        "multiple": float(led.multiple),
        "cagr": float(led.cagr),
        "maxdd": float(led.maxdd),
        "tax_paid": float(led.tax_paid),
        "exit_tax": float(led.exit_tax),
        "fees": float(led.fees),
        "y2018": float(
            led.equity.loc["2018"].iloc[-1] / led.equity.loc["2018"].iloc[0] - 1
        ),
        "y2022": float(
            led.equity.loc["2022"].iloc[-1] / led.equity.loc["2022"].iloc[0] - 1
        ),
        "equity": led.equity,
        "ledger": led,
    }


def av_result_for(
    res_gross, panel, profile: str, fee: float, bps: float = TRANSACTION_BPS
) -> dict:
    cfg = av.AVConfig(
        annual_fee=fee,
        transaction_bps=bps,
        abattement=av.AV_ABATTEMENT_SINGLE,
        years=12.0,
    )
    out = av.av_result(res_gross.equity, res_gross.trades, CAP, cfg)
    nav = av.av_nav_path(res_gross.equity, res_gross.trades, cfg)
    out.update(
        {
            "maxdd": av.max_drawdown(nav),
            "y2018": av.annual_return(nav, 2018),
            "y2022": av.annual_return(nav, 2022),
            "nav": nav,
        }
    )
    return out


def five_year_summary(equity: pd.Series, tax_rate: float) -> dict:
    table = windows.five_year_table(equity, tax_rate=tax_rate, include_partial=False)
    s = table.summary
    return {
        "n": int(s.get("n_windows", 0)),
        "median": float(s.get("median_multiple", float("nan"))),
        "worst": float(s.get("worst_multiple", float("nan"))),
        "share_beat_qqq": float(s.get("share_beat", float("nan"))),
    }


def fmt_money(x: float) -> str:
    return f"€{x:,.0f}"


def pct(x: float) -> str:
    return f"{x:.1%}"


def main() -> None:
    panel, mem = markets.build("US")
    universe.canonical = ORIG_CANONICAL
    rows = []
    for profile in ("max_growth", "balanced"):
        res_cto = run_strategy(panel, mem, profile, tax=True)
        cto = cto_result(res_cto, panel)
        res_gross = run_strategy(panel, mem, profile, tax=False)
        for fee in FEES:
            a = av_result_for(res_gross, panel, profile, fee)
            rows.append(
                {
                    "profile": profile,
                    "fee": fee,
                    "cto": cto,
                    "av": a,
                    "five_cto": five_year_summary(cto["equity"], 0.314),
                    "five_av": five_year_summary(a["nav"], 0.30),
                }
            )
        break_even = av.break_even_fee(
            res_gross.equity,
            res_gross.trades,
            cto["multiple"],
            CAP,
            transaction_bps=TRANSACTION_BPS,
            abattement=av.AV_ABATTEMENT_SINGLE,
            years=12.0,
        )
        print(
            f"{profile:10s} CTO {cto['multiple']:.2f}x  "
            f"AV 0.5% {rows[-3]['av']['after_tax_multiple']:.2f}x  "
            f"0.6% {rows[-2]['av']['after_tax_multiple']:.2f}x  "
            f"0.8% {rows[-1]['av']['after_tax_multiple']:.2f}x  "
            f"break-even {break_even if break_even is None else f'{break_even:.3%}'}"
        )
    # Estate comparison at €660k, one child. CTO US-situs tail is shown
    # separately: the treaty makes it zero at this size, but it is not zero
    # without the treaty.
    estate_rows = []
    for n in (1, 2):
        e = av.estate_comparison(660_000.0, beneficiaries=n)
        cto_us = estate.assess(
            estate.EstateInputs(
                capital_eur=660_000,
                us_situs_share=1.0,
                wrapper="cto",
                treaty=False,
                heir="child",
                heirs=n,
            )
        )
        cto_treaty = estate.assess(
            estate.EstateInputs(
                capital_eur=660_000,
                us_situs_share=1.0,
                wrapper="cto",
                treaty=True,
                heir="child",
                heirs=n,
            )
        )
        estate_rows.append(
            {
                **e,
                "cto_us_tax_no_treaty": cto_us["us_tax_usd"],
                "cto_us_tax_treaty": cto_treaty["us_tax_usd"],
                "av_us_tax": 0.0,
            }
        )

    lines = [
        "# Assurance-vie wrapper: US monthly and balanced A6",
        "",
        "> **Not tax or legal advice.** A planning model with explicit assumptions.",
        "> The insurer's tariff, contract annex, fund domicile, leverage implementation",
        "> and beneficiary clause decide the real result.",
        "",
        "## What is compared",
        "",
        "- **CTO:** merged EUR ledger with annual French PFU, loss carry, dividend",
        "  withholding/PFU, 5 bps spread, IBKR-like tickets and 5%/yr financing.",
        "- **Assurance-vie:** the same gross strategy return, but the contract is the",
        "  tax wrapper: annual unit-linked fee 0.50/0.60/0.80%, 10 bps per trade,",
        "  no annual PFU; at 12 years the gain is taxed at 24.7% (7.5% + 17.2%)",
        "  after the €4,600 single abattement. Before 8 years, 30% PFU applies.",
        "- **Estate:** 990 I for AV versus CTO French inheritance tax and the US",
        "  estate-tax tail. US-situs exposure is removed in the AV model, but the",
        "  contract can only implement 2.5x leverage if the insurer/fund allows it;",
        "  this is not a free option.",
        "",
        "## Results (€10k, 2015-01-02..2026-11-25, 12-year tax age)",
        "",
        "| book | wrapper | annual fee | after-tax multiple | CAGR | maxDD | 2018 | 2022 | 5y median | 5y worst |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        lines.append(
            f"| {r['profile']} | CTO | — | **{r['cto']['multiple']:.2f}x** | "
            f"{pct(r['cto']['cagr'])} | {pct(r['cto']['maxdd'])} | "
            f"{pct(r['cto']['y2018'])} | {pct(r['cto']['y2022'])} | "
            f"{r['five_cto']['median']:.2f}x | {r['five_cto']['worst']:.2f}x |"
        )
        lines.append(
            f"| {r['profile']} | assurance-vie | {r['fee']:.2%} | "
            f"**{r['av']['after_tax_multiple']:.2f}x** | "
            f"{pct((r['av']['after_tax_multiple']) ** (1 / 12) - 1)} | "
            f"{pct(r['av']['maxdd'])} | {pct(r['av']['y2018'])} | "
            f"{pct(r['av']['y2022'])} | {r['five_av']['median']:.2f}x | "
            f"{r['five_av']['worst']:.2f}x |"
        )
    lines += [
        "",
        "AV CAGR is after the terminal AV tax, not an annual tax; the CTO CAGR is",
        "the merged EUR-ledger after-tax path. The 5-year columns are separate",
        "window slices with a terminal-gain tax overlay (31.4% CTO, 30% AV before",
        "8 years); they are comparable as approximations, not a second tax engine.",
        "",
        "## Break-even fee",
        "",
    ]
    for profile in ("max_growth", "balanced"):
        r = next(x for x in rows if x["profile"] == profile)
        be = av.break_even_fee(
            # reuse the same gross path from the first row's source by rebuilding
            # through a tiny closure is overkill; the main loop already printed it.
            # Recompute from the stored data is not possible, so report from a
            # second run below.
            run_strategy(panel, mem, profile, tax=False).equity,
            run_strategy(panel, mem, profile, tax=False).trades,
            r["cto"]["multiple"],
            CAP,
            transaction_bps=TRANSACTION_BPS,
            years=12.0,
        )
        if be is None:
            lines.append(
                f"- **{profile}:** no positive fee at which AV falls to the CTO "
                "multiple within the 0-10% search range (or AV loses at zero fee)."
            )
        else:
            lines.append(
                f"- **{profile}:** break-even annual fee **{be:.2%}**. "
                "Above this, AV after tax is below the CTO."
            )

    lines += [
        "",
        "## Estate at €660k",
        "",
        "| beneficiaries | AV 990 I | CTO French inheritance | AV advantage | CTO US tax, treaty | CTO US tax, no treaty |",
        "|---:|---:|---:|---:|---:|---:|",
    ]
    for e in estate_rows:
        lines.append(
            f"| {e['beneficiaries']} | **{fmt_money(e['av_990i_tax'])}** | "
            f"{fmt_money(e['cto_french_inheritance_tax'])} | "
            f"{fmt_money(e['av_advantage'])} | "
            f"{fmt_money(e['cto_us_tax_treaty'])} | "
            f"{fmt_money(e['cto_us_tax_no_treaty'])} |"
        )
    lines += [
        "",
        "The AV estate advantage grows with the number of beneficiaries because",
        "the €152,500 990 I allowance is per beneficiary. The CTO's French",
        "inheritance tax uses €100,000 per child; its US estate tax is zero at this",
        "size **only if** the France-US treaty pro-rata credit applies. Without the",
        "treaty the CTO carries a material US-situs tail; AV removes it in the model.",
        "",
        "## Verdict",
        "",
    ]
    # Determine acceptance: all fees must beat CTO after tax and the estate/tail
    # benefit must be explicit. If any configured fee loses, reject the wrapper
    # as a default; it can still be an opt-in for estate reasons.
    max_fee_ok = all(
        r["av"]["after_tax_multiple"] > r["cto"]["multiple"] + 1e-9 for r in rows
    )
    if max_fee_ok:
        verdict = (
            "**ACCEPT as an opt-in, not a default.** All three modelled AV fees "
            "beat the CTO after terminal AV tax over 12 years, and the 990 I "
            "estate route removes the US-situs tail. The gain is tax deferral and "
            "estate structure, not a better underlying strategy; it depends on "
            "the contract implementing the same gross return at the stated fee."
        )
    else:
        verdict = (
            "**REJECT as a default.** At least one modelled fee loses to the CTO "
            "after terminal AV tax; use AV only for a specific estate/beneficiary "
            "need, not as a return enhancement."
        )
    lines += [
        verdict,
        "",
        "The break-even fee is the useful decision number: if the insurer's all-in",
        "annual fee (including fund fees and any leverage wrapper) is above it, the",
        "AV wrapper is not competitive on terminal wealth. Estate benefits can",
        "still justify a smaller allocation, but they are a separate objective.",
        "",
        "## Caveats and implementation constraints",
        "",
        "- A standard French assurance-vie cannot borrow at contract level. The",
        "  2.5x US monthly book can only go inside the wrapper through a leveraged",
        "  fund/structured note or not at all; the model applies the wrapper to the",
        "  same gross path and does not price that implementation. The balanced A6",
        "  (2.05x before the trend cut) has the same issue.",
        "- Annual fees compound; the insurer's fund-level fees may be additional.",
        "  Transaction fees are modelled at 10 bps per trade; check the contract",
        "  for arbitrage fees, minimum tickets and fund entry/exit costs.",
        "- AV gains are taxed only on withdrawal; the model assumes one terminal",
        "  withdrawal after 12 years. Partial withdrawals use the abattement each",
        "  year and may be more tax-efficient; before 8 years the 30% PFU applies.",
        "- 990 I applies to premiums paid before age 70; after 70 the €30,500 global",
        "  regime taxes premiums, not accumulated gains. A spouse/PACS beneficiary",
        "  is exempt. The beneficiary clause controls.",
        "- The US-situs removal assumes the contract/underlying fund is not US-situs.",
        "  Confirm the fund domicile and insurer structure; a French wrapper does not",
        "  automatically cure a US-domiciled underlying.",
        "- French inheritance tax, the €100k child allowance and the US treaty",
        "  credit are as in `docs/estate_tax.md`; this is not legal advice.",
        "",
        "## Reproduce",
        "",
        "```bash",
        "uv run python scripts/assurance_vie.py",
        "uv run python tests/test_assurance_vie.py",
        "```",
        "",
        "Generated by `uv run python scripts/assurance_vie.py`.",
    ]
    (ROOT / "results" / "assurance_vie.md").write_text("\n".join(lines) + "\n")
    print("wrote results/assurance_vie.md")


if __name__ == "__main__":
    main()
