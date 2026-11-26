"""Limit-at-open execution study for the French core book.

Baseline: shipped TECH tax book (SMH core, 2.5x, daily 200-day cut, month-end
decision, intended next-open fill, 10k Trade Republic CTO). US/TECH now use the
real adjusted SMH open/high/low by default; `core_close_fill=True` restores the
old synthetic close-fill path for comparison. Non-US markets keep the quirk.

Run: uv run python scripts/execution.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from bot import backtest, france, markets, strategy  # noqa: E402

START = "2015-01-01"
CAPITAL = 10_000.0
KS = (5, 10, 20, 30)
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


def load(range_: bool, core_close_fill: bool = False):
    panel, mem = markets.build(
        "TECH", with_range=range_, core_close_fill=core_close_fill
    )
    return panel, mem


def config(panel: pd.DataFrame, limit_bps: float) -> strategy.Config:
    kw = dict(BASE)
    kw.update(france.kwargs("TECH", "trade-republic", "cto", CAPITAL, panel))
    kw.update(core_mode="always", leverage_band=0.30, limit_bps=float(limit_bps))
    return strategy.Config(**kw)


def run(panel: pd.DataFrame, mem: pd.DataFrame, limit_bps: float):
    res = backtest.run(strategy.Market(panel, mem, config(panel, limit_bps)))
    return res, france.after_tax(res, CAPITAL, "TECH")


def bars(tickers: list[str]) -> pd.DataFrame:
    """Adjusted daily range from the raw cache. Read-only."""
    parts = []
    for ticker in sorted(set(tickers)):
        df = pd.read_csv(
            ROOT / "cache" / "bars" / f"{ticker.replace('/', '_')}.csv",
            parse_dates=["date"],
        )
        f = df["adjclose"] / df["close"]
        parts.append(
            pd.DataFrame(
                {
                    "date": df["date"],
                    "ticker": ticker,
                    "open_": df["open"] * f,
                    "high_": df["high"] * f,
                    "low_": df["low"] * f,
                    "close_": df["close"] * f,
                }
            )
        )
    return pd.concat(parts, ignore_index=True)


def simulate(trades: pd.DataFrame, k: int) -> pd.DataFrame:
    """Per-order limit fill on the baseline trades, holding share counts fixed.

    `deleverage` is excluded: the engine trims margin at the close, not at the
    open, so an open limit cannot apply. The baseline open price is the actual
    adjusted open; a missed limit falls back to the same day's close.
    """
    d = trades[trades.reason != "deleverage"].copy()
    b = bars(list(d.ticker.unique()))
    d = d.merge(b, on=["date", "ticker"], how="left")
    d = d[d.open_.notna()].copy()
    # The baseline trade price is the open for these orders; use it as the anchor
    # so the comparison is exactly against the book that shipped.
    d["open_px"] = d["price"]
    d["limit"] = np.where(
        d.side == "BUY", d.open_px * (1 - k / 1e4), d.open_px * (1 + k / 1e4)
    )
    d["filled"] = np.where(d.side == "BUY", d.low_ <= d.limit, d.high_ >= d.limit)
    d["fill"] = np.where(d.filled, d.limit, d.close_)
    d["sign"] = np.where(d.side == "BUY", 1.0, -1.0)
    d["saving_bps"] = d.sign * (d.open_px - d.fill) / d.open_px * 1e4
    d["notional"] = d.shares * d.open_px
    return d


def agg(d: pd.DataFrame) -> dict:
    n = len(d)
    filled = d[d.filled]
    missed = d[~d.filled]
    return {
        "n": n,
        "fills": int(d.filled.sum()),
        "fill_rate": d.filled.mean() if n else np.nan,
        "fill_notional": (
            filled.notional.sum() / d.notional.sum() if d.notional.sum() else np.nan
        ),
        "avg_filled": filled.saving_bps.mean() if len(filled) else np.nan,
        "avg_missed": missed.saving_bps.mean() if len(missed) else np.nan,
        "net_bps": (d.saving_bps * d.notional).sum() / d.notional.sum(),
        "missed": int((~d.filled).sum()),
    }


def next_open_fallback(trades: pd.DataFrame, k: int) -> float:
    d = trades[trades.reason != "deleverage"].copy()
    b = bars(list(d.ticker.unique()))
    b["next_open"] = b.groupby("ticker")["open_"].shift(-1)
    d = d.merge(b, on=["date", "ticker"], how="left")
    d = d[d.open_.notna()].copy()
    d["limit"] = np.where(
        d.side == "BUY", d.price * (1 - k / 1e4), d.price * (1 + k / 1e4)
    )
    d["filled"] = np.where(d.side == "BUY", d.low_ <= d.limit, d.high_ >= d.limit)
    fallback = d["next_open"].where(d["next_open"].notna(), d["close_"])
    d["fill"] = np.where(d.filled, d.limit, fallback)
    d["sign"] = np.where(d.side == "BUY", 1.0, -1.0)
    d["saving_bps"] = d.sign * (d.price - d.fill) / d.price * 1e4
    d["notional"] = d.shares * d.price
    return float((d.saving_bps * d.notional).sum() / d.notional.sum())


def pct(x: float) -> str:
    return f"{x:.1%}"


def bps(x: float) -> str:
    return "—" if pd.isna(x) else f"{x:+.1f}"


def main() -> None:
    # old synthetic core close-fill path, kept only for comparison
    p_ship, m_ship = load(False, core_close_fill=True)
    r_ship, f_ship = run(p_ship, m_ship, 0.0)
    # new default: real adjusted SMH open/high/low
    p, m = load(True)
    r_base, f_base = run(p, m, 0.0)

    ship_mult = f_ship["after_tax_eur"] / CAPITAL
    base_mult = f_base["after_tax_eur"] / CAPITAL
    assert abs(ship_mult - 38.80) < 0.02, (
        f"old close-fill baseline moved: {ship_mult:.2f}x"
    )

    def monthly(close_fill: bool):
        panel, mem = load(False, core_close_fill=close_fill)
        kw = dict(BASE)
        kw.update(france.kwargs("TECH", "trade-republic", "cto", CAPITAL, panel))
        res = backtest.run(strategy.Market(panel, mem, strategy.Config(**kw)))
        return res, france.after_tax(res, CAPITAL, "TECH")

    r_mon, f_mon = monthly(False)
    r_mon_old, f_mon_old = monthly(True)

    rows = []
    path_rows = []
    robust_rows = []
    for k in (0, *KS):
        if k == 0:
            d = simulate(r_base.trades, 0)
        else:
            d = simulate(r_base.trades, k)
        a = agg(d)
        rows.append({"k": k, **a})
        for side in ("BUY", "SELL"):
            a2 = agg(d[d.side == side])
            rows.append({"k": k, "side": side, **a2})
        if k > 0:
            res, f = run(p, m, k)
            path_rows.append(
                {
                    "k": k,
                    "pre": f["pre_tax_eur"] / CAPITAL,
                    "fees": f["fees_eur"],
                    "tax": f["tax_paid_eur"],
                    "exit": f["exit_tax_eur"],
                    "after": f["after_tax_eur"] / CAPITAL,
                    "delta": f["after_tax_eur"] / CAPITAL - base_mult,
                    "est_ship": ship_mult * (f["after_tax_eur"] / CAPITAL) / base_mult,
                    "trades": len(res.trades),
                }
            )
            d["year"] = d["date"].dt.year
            yr = d.groupby("year").apply(
                lambda g: (g.saving_bps * g.notional).sum() / g.notional.sum(),
                include_groups=False,
            )
            d["cost"] = -(d.saving_bps * d.notional)
            drop = d.sort_values("cost", ascending=False).iloc[3:]
            robust_rows.append(
                {
                    "k": k,
                    "net": a["net_bps"],
                    "net3": (drop.saving_bps * drop.notional).sum()
                    / drop.notional.sum(),
                    "pos_years": int((yr > 0).sum()),
                    "years": len(yr),
                    "worst_year": int(yr.idxmin()),
                    "worst": float(yr.min()),
                    "next_open": next_open_fallback(r_base.trades, k),
                }
            )

    lines = [
        "# Limit-at-open execution: fill rates and after-tax cost",
        "",
        "The shipped book is meant to fill at the next open. This study asks",
        "whether a limit a few basis points away from that open improves the real",
        "fill, using the daily high/low that the built panel drops. The answer is",
        "not a free lunch: a 5 bps limit is roughly neutral-to-slightly-positive,",
        "and wider limits lose money through adverse selection on missed buys.",
        "",
        "## Method",
        "",
        "- Book: the shipped French `tax` book: SMH core, 2.5x, 500 bps financing,",
        "  daily 200-day cut to 0.5x, month-end decision, intended next-open fill,",
        "  5 bps spread + Trade Republic €1/order + 25 bps FX, French CTO HIFO, €10k.",
        "- Signal: unchanged. The order is decided at the prior close; the limit is",
        "  set from today's open and the fill is checked against today's high/low.",
        "  A miss falls back to a market-on-close order, which is executable without",
        "  using the signal's future. No same-day close feeds the signal.",
        "- Buy limit = `open * (1 - k/1e4)`. If `low <= limit`, fill at the limit.",
        "  Sell limit = `open * (1 + k/1e4)`. If `high >= limit`, fill at the limit.",
        "  Otherwise fill at the close. `deleverage` trades are excluded because the",
        "  engine executes those at the close, not at the open.",
        "- Price improvement on a fill is `+k` bps by construction. The interesting",
        "  quantities are the fill rate and the signed cost of the misses.",
        "- The full path is rerun with `Config.limit_bps=k`; the estimate on the",
        "  old close-fill 38.8x book is the measured relative change applied to it.",
        "",
        "## Data note: real core open is now the default",
        "",
        "`markets.local_core` still has only adjusted closes, so the old synthetic",
        "core rows used `open == close` and filled SMH at the adjusted close. The",
        "default US/TECH panel now splices the real adjusted SMH/SOXX open/high/low",
        "from `cache/bars`; `markets.build(..., core_close_fill=True)` or",
        "`--core-close-fill` restores the old path for comparison. Non-US markets",
        "still keep the `open == close` core quirk.",
        "",
        "The default TECH tax book therefore fills SMH at the real open: **39.50x**",
        "after tax. The old close-fill path is **38.80x**; the difference is execution",
        "data, not a strategy change.",
        "",
        "## Baseline",
        "",
        "| book | after tax (€10k) | multiple | trades | pending open orders | deleverage | fees | tax paid | exit tax |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        f"| TECH tax, real SMH open (**default**) | €{f_base['after_tax_eur']:,.0f} | "
        f"**{base_mult:.2f}x** | {len(r_base.trades)} | "
        f"{int((r_base.trades.reason != 'deleverage').sum())} | "
        f"{int((r_base.trades.reason == 'deleverage').sum())} | "
        f"€{f_base['fees_eur']:,.0f} | €{f_base['tax_paid_eur']:,.0f} | "
        f"€{f_base['exit_tax_eur']:,.0f} |",
        f"| TECH tax, old close-fill (`--core-close-fill`) | €{f_ship['after_tax_eur']:,.0f} | "
        f"**{ship_mult:.2f}x** | {len(r_ship.trades)} | "
        f"{int((r_ship.trades.reason != 'deleverage').sum())} | "
        f"{int((r_ship.trades.reason == 'deleverage').sum())} | "
        f"€{f_ship['fees_eur']:,.0f} | €{f_ship['tax_paid_eur']:,.0f} | "
        f"€{f_ship['exit_tax_eur']:,.0f} |",
        "",
        "The default baseline has one more order and one more deleverage than the",
        "old close-fill path; the fill study uses its 174 open orders.",
        "",
        "## TECH monthly book, same comparison",
        "",
        "| book | after tax (€10k) | multiple | trades | fees | exit tax |",
        "|---|---:|---:|---:|---:|---:|",
        f"| TECH monthly, real SMH open (**default**) | €{f_mon['after_tax_eur']:,.0f} | "
        f"**{f_mon['after_tax_eur'] / CAPITAL:.2f}x** | {len(r_mon.trades)} | "
        f"€{f_mon['fees_eur']:,.0f} | €{f_mon['exit_tax_eur']:,.0f} |",
        f"| TECH monthly, old close-fill | €{f_mon_old['after_tax_eur']:,.0f} | "
        f"**{f_mon_old['after_tax_eur'] / CAPITAL:.2f}x** | "
        f"{len(r_mon_old.trades)} | €{f_mon_old['fees_eur']:,.0f} | "
        f"€{f_mon_old['exit_tax_eur']:,.0f} |",
        "",
        "The monthly book is more sensitive than the core-only book: it rotates",
        "between the SMH core and the stock sleeve, so the real core open changes",
        "the path and the trade count too. The old close-fill row is the "
        f"{f_mon_old['after_tax_eur'] / CAPITAL:.2f}x in the existing `france.md`; "
        f"the corrected default is {f_mon['after_tax_eur'] / CAPITAL:.2f}x.",
        "",
        "## Fill rates and cost, baseline orders held fixed",
        "",
        "| k (bps) | side | orders | fills | fill rate | fill rate, notional | avg improvement when filled | avg missed-fill saving | notional-weighted cost change |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        side = row.get("side", "ALL")
        lines.append(
            f"| {row['k']} | {side} | {row['n']} | {row['fills']} | "
            f"{pct(row['fill_rate'])} | {pct(row['fill_notional'])} | "
            f"{bps(row['avg_filled'])} bps | {bps(row['avg_missed'])} bps | "
            f"{bps(row['net_bps'])} bps |"
        )
    lines += [
        "",
        "Positive cost change = cheaper than the open. A missed buy that closes",
        "higher costs about 130 bps, roughly 25x a 5 bps limit; that is the",
        "adverse selection the strategy pays for waiting.",
        "",
        "## Robustness of the per-order result",
        "",
        "| k (bps) | net cost change | net after dropping 3 worst misses | years positive | worst year | next-open fallback, net |",
        "|---:|---:|---:|---:|---|---:|",
    ]
    for row in robust_rows:
        lines.append(
            f"| {row['k']} | {row['net']:+.1f} bps | {row['net3']:+.1f} bps | "
            f"{row['pos_years']}/{row['years']} | {row['worst_year']} "
            f"({row['worst']:+.1f} bps) | {row['next_open']:+.1f} bps |"
        )
    lines += [
        "",
        "The 5 bps result is positive in 11 of 12 years. At 10-30 bps the",
        "per-order sign is carried by a few trend-day misses; removing the three",
        "worst flips it positive, so it is not a stable edge. The next-open",
        "fallback is worse than the close fallback at every k.",
        "",
        "## Full path after tax (€10k Trade Republic CTO)",
        "",
        "| k (bps) | pre-tax | fees | tax paid | exit tax | **after tax** | vs honest baseline | estimate on shipped 38.8x |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|",
        f"| 0 (honest baseline) | {f_base['pre_tax_eur'] / CAPITAL:.2f}x | "
        f"€{f_base['fees_eur']:,.0f} | €{f_base['tax_paid_eur']:,.0f} | "
        f"€{f_base['exit_tax_eur']:,.0f} | **{base_mult:.2f}x** | — | "
        f"{ship_mult:.2f}x (shipped) |",
    ]
    for row in path_rows:
        lines.append(
            f"| {row['k']} | {row['pre']:.2f}x | €{row['fees']:,.0f} | "
            f"€{row['tax']:,.0f} | €{row['exit']:,.0f} | **{row['after']:.2f}x** | "
            f"{row['delta']:+.2f}x | {row['est_ship']:.2f}x |"
        )
    lines += [
        "",
        "The full path is the decision metric. The per-order table holds the",
        "baseline share counts fixed; the engine rerun changes cash, size and the",
        "trend-cut path, so the two views need not line up exactly.",
        "",
        "## What the numbers say",
        "",
        f"1. **5 bps is the only candidate, and it is small.** {agg(simulate(r_base.trades, 5))['fill_rate']:.1%} "
        "of orders fill, the notional-weighted cost change is positive, and the",
        f"full path gains {path_rows[0]['delta']:+.2f}x ({path_rows[0]['after'] / base_mult - 1:+.1%}). "
        "That is inside the gap between the shipped and honest baselines and is",
        "not enough to change the default.",
        "",
        f"2. **10 bps and wider lose.** The full path is "
        f"{path_rows[1]['delta']:+.2f}x at 10 bps, {path_rows[2]['delta']:+.2f}x at "
        f"20 bps and {path_rows[3]['delta']:+.2f}x at 30 bps. Fill rates fall to "
        f"{agg(simulate(r_base.trades, 30))['fill_rate']:.0%} at 30 bps, and the",
        "missed buys close 100-300 bps above the open on trend days. Dropping the",
        "three worst misses flips the per-order net positive at 10/20/30 bps, which",
        "means the sign is driven by a handful of large adverse days, not a stable",
        "edge.",
        "",
        "3. **The fallback is a choice, not free.** The close fallback is generous:",
        "a next-open fallback is worse at every k tested (robustness table). A trader",
        "who does not chase a miss keeps the old position and may miss a risk-off",
        "cut, which is a larger risk than the few bps of spread saved.",
        "",
        "4. **The old 38.8x was close-filled; the default is now 39.5x.** The",
        "default US/TECH panel uses the real SMH open. The old close-fill path is",
        "only a `--core-close-fill` comparison and is not the shipped default.",
        "",
        "## Recommendation",
        "",
        "**Keep the default next-open fill. Do not enable limit orders on the core",
        "book.** The only positive case is 5 bps, worth roughly +0.3x after tax on the",
        "39.5x default baseline (+0.7%), with 95% fills. It is not robust enough to",
        "pay for the operational risk: a missed buy on a trend day costs ~130 bps,",
        "and the strategy's edge is precisely to be long on those days. If the book",
        "is paper-traded, log `--limit-bps 5` as a shadow next to the default; promote",
        "it only if the live fill-rate and missed-fill P&L confirm the backtest.",
        "",
        "## Risks and caveats",
        "",
        "- Daily high/low is not a tick path. A limit touched only at the low may",
        "  not have filled for size; the model assumes the whole order fills at the",
        "  limit when the low touches it. This is optimistic on the fill rate.",
        "- The close fallback assumes a market-on-close order is available and fills",
        "  at the close. Real MOC cutoffs and closing auctions can be worse.",
        "- Missed-fill P&L is dominated by a few trend days (2023-02, 2024-03,",
        "  2025-06, 2025-10). A different sample or a different start date can flip",
        "  the 5 bps sign.",
        "- **Non-US markets still have the core quirk.** `local_core` remains",
        "  close-filled for DE/FR/GB/etc.; only US/TECH use real core opens. Do not",
        "  apply the 39.5x US/TECH correction to the French or 39-market tables.",
        "- No borrow squeeze, no market impact, no queue position, no partial fills.",
        "",
        "## Downstream docs to regenerate",
        "",
        "- `results/france.md`: core-only tax 38.80x -> **39.50x**, monthly 27.18x ->",
        "  **31.90x**; `results/france.csv` and `metrics_france.csv` follow.",
        "- `README.md`: the 38.8x/27.2x headline rows and the next-open wording.",
        "- `results/pea_ucits.md`: the CTO 38.80x comparator.",
        "- `results/risk_v2.md`: its 38.80x baseline and all rule deltas.",
        "- `results/summary.md`: the 38.80x summary row.",
        "- Forward paper shadow: `paper_daily_tax.csv`, `paper_ledger_tax.csv` and",
        "  `paper_replay_tax.csv` were logged on the old close-fill path. Re-baseline",
        "  the shadow; do not rewrite historical rows.",
        "- 39-market cost tables (`results/costs_tax.md`, `tax_markets.md`,",
        "  `universal.md`): only US/TECH rows move; non-US cores are still",
        "  close-filled until `local_core` is fixed.",
        "",
        "## Reproduce",
        "",
        "```",
        "uv run python scripts/execution.py",
        "uv run python -m bot.cli backtest --market TECH --country fr --profile tax \\",
        "  --broker trade-republic --capital 10000 --limit-bps 5 --random-seeds 0",
        "uv run python -m bot.cli backtest --market TECH --country fr --profile tax \\",
        "  --broker trade-republic --capital 10000 --core-close-fill --random-seeds 0",
        "```",
        "",
        "The first CLI run uses the real core open and the opt-in 5 bps limit. The",
        "second reproduces the old 38.8x close-fill path. `--limit-bps 0` gives the",
        "default 39.5x baseline with the raw high/low panel loaded.",
        "",
        "Generated by `uv run python scripts/execution.py`.",
    ]
    (ROOT / "results" / "execution.md").write_text("\n".join(lines) + "\n")
    print("wrote results/execution.md")
    for row in path_rows:
        print(
            f"k={row['k']:2d}: after {row['after']:.2f}x "
            f"(delta {row['delta']:+.2f}x, estimate {row['est_ship']:.2f}x)"
        )


if __name__ == "__main__":
    main()
