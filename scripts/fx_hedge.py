"""EUR/USD hedge study on the EUR ledger.

Run:
    uv run python scripts/fx_hedge.py

Pre-registration and results: results/fx_hedge.md. This is a reporting overlay;
it does not change any trading default.
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
from bot.fx_hedge import FxHedgeConfig  # noqa: E402
from scripts.france import BASE  # noqa: E402

CAP = 10_000.0
BROKER = "Interactive Brokers"
ORIG_CANONICAL = universe.canonical
RATIOS = (0.0, 0.25, 0.50, 0.75, 1.0)
ROLLS = (2.0, 5.0, 10.0)
CARRIES = (0.0, 200.0, 400.0)

_PANELS: dict[str, tuple[pd.DataFrame, pd.DataFrame]] = {}
_DIVS: dict[str, pd.DataFrame | None] = {}


def _balanced_selector(mkt, date, held, banned):
    base = backtest._default_selector(mkt, date, held, banned)
    out = {t: w * 0.70 for t, w in base.items()}
    out["SPY"] = out.get("SPY", 0.0) + 0.30 / float(mkt.cfg.leverage)
    return out


CASES = {
    "US monthly": ("US", None),
    "TECH monthly": ("TECH", None),
    "Balanced A6": ("US", _balanced_selector),
}


def _panel(market: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    if market not in _PANELS:
        universe.canonical = ORIG_CANONICAL if market == "US" else (lambda t: t)
        _PANELS[market] = markets.build(market)
    return _PANELS[market]


def _config(
    market: str, panel: pd.DataFrame, cost_mult: float = 1.0
) -> strategy.Config:
    kw = dict(BASE)
    kw.update(france.kwargs(market, BROKER, "cto", CAP, panel))
    kw["cost_bps"] = 5.0 * cost_mult
    kw["fx_bps"] = 2.0 * cost_mult
    kw["broker_bps"] = float(kw.get("broker_bps", 0.0)) * cost_mult
    kw["us_bps"] = float(kw.get("us_bps", 0.0)) * cost_mult
    kw["broker_grid"] = tuple(
        (limit, fee * cost_mult) for limit, fee in kw.get("broker_grid", ())
    )
    kw["us_grid"] = tuple(
        (limit, fee * cost_mult) for limit, fee in kw.get("us_grid", ())
    )
    return strategy.Config(**kw)


def _ledger_config(cost_mult: float = 1.0) -> eur_ledger.EurLedgerConfig:
    return eur_ledger.EurLedgerConfig(
        capital=CAP,
        tax_rate=None,
        financing_bps=500.0,
        spread_bps=5.0 * cost_mult,
        fx_bps=2.0 * cost_mult,
        broker_bps=0.25 * cost_mult,
        broker_fixed_eur=1.0 * cost_mult,
        withholding_rate=0.15,
    )


def _bootstrap(
    rets: pd.Series, block: int = 10, n: int = 2000, seed: int = 7
) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    x = rets.to_numpy(dtype=float)
    m = len(x)
    nb = int(np.ceil(m / block))
    finals = np.empty(n)
    dds = np.empty(n)
    for k in range(n):
        starts = rng.integers(0, m, nb)
        idx = (starts[:, None] + np.arange(block)[None, :]).ravel() % m
        path = np.cumprod(1.0 + x[idx][:m])
        finals[k] = path[-1]
        peak = np.maximum.accumulate(path)
        dds[k] = (path / peak - 1.0).min()
    return finals, dds


def _year(eq: pd.Series, year: int) -> float:
    e = eq.loc[str(year)]
    return float(e.iloc[-1] / e.iloc[0] - 1.0) if len(e) > 2 else float("nan")


def _five_year(eq: pd.Series) -> pd.Series:
    monthly = eq.resample("ME").last().dropna()
    return (monthly / monthly.shift(60) - 1.0).dropna()


def run_case(
    case: str,
    hedge: FxHedgeConfig,
    cost_mult: float = 1.0,
) -> dict:
    market, selector = CASES[case]
    panel, mem = _panel(market)
    cfg = _config(market, panel, cost_mult)
    res = backtest.run(strategy.Market(panel, mem, cfg), selector=selector)
    if case not in _DIVS:
        tickers = sorted(set(res.trades["ticker"])) if len(res.trades) else []
        events = divdata.load_dividends(tickers, fetch_missing=False)
        _DIVS[case] = events if len(events) else None
    hcfg = replace(hedge, roll_bps=hedge.roll_bps * cost_mult)
    led = eur_ledger.replay(
        res,
        panel,
        market,
        _ledger_config(cost_mult),
        dividends=_DIVS[case],
        fx_hedge=hcfg,
    )
    eq = led.equity
    r = eq.pct_change().dropna()
    finals, dds = _bootstrap(r)
    ratio = led.after_tax / float(eq.iloc[-1]) if eq.iloc[-1] > 0 else np.nan
    five = _five_year(eq)
    row = {
        "case": case,
        "ratio": hedge.ratio if hedge.mode == "fixed" else np.nan,
        "mode": hedge.mode,
        "low_ratio": hedge.low_ratio,
        "high_ratio": hedge.high_ratio,
        "roll_bps": hcfg.roll_bps,
        "carry_bps": hedge.carry_bps,
        "cost_mult": cost_mult,
        "multiple": led.multiple,
        "after_tax": led.after_tax,
        "cagr": led.cagr,
        "vol": float(r.std() * np.sqrt(252)),
        "maxdd": led.maxdd,
        "y2018": _year(eq, 2018),
        "y2022": _year(eq, 2022),
        "worst12": float(eq.pct_change(252).min()),
        "p5": float(np.quantile(finals, 0.05) * ratio),
        "median": float(np.median(finals) * ratio),
        "p5dd": float(np.quantile(dds, 0.05)),
        "five_median": float(five.median()) if len(five) else np.nan,
        "five_worst": float(five.min()) if len(five) else np.nan,
        "five_positive": float((five > 0).mean()) if len(five) else np.nan,
        "fees": led.fees,
        "hedge_pnl": led.fx_hedge_pnl,
        "hedge_cost": led.fx_hedge_cost,
        "hedge_periods": led.fx_hedge_periods,
        "hedge_avg_notional": led.fx_hedge_avg_notional,
        "div_tax": led.dividend_tax_total,
        "tax_paid": led.tax_paid,
        "exit_tax": led.exit_tax,
        "_five": five,
    }
    return row


def _fmt(value, spec: str) -> str:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return "n/a"
    return "n/a" if not np.isfinite(x) else format(x, spec)


def main() -> None:
    rows: list[dict] = []
    for case in CASES:
        print(f"\n== {case} ==", flush=True)
        base = run_case(case, FxHedgeConfig(ratio=0.0), 1.0)
        base["variant"] = "unhedged"
        rows.append(base)
        five_base = base["_five"]
        print(
            f"{case:16s} unhedged {base['multiple']:7.2f}x dd {base['maxdd']:6.1%} "
            f"p5 {base['p5']:5.2f}x",
            flush=True,
        )
        for ratio in RATIOS[1:]:
            r = run_case(case, FxHedgeConfig(ratio=ratio), 1.0)
            r["variant"] = f"fixed {ratio:.0%}"
            rows.append(r)
            print(
                f"{case:16s} {r['variant']:10s} {r['multiple']:7.2f}x "
                f"dd {r['maxdd']:6.1%} p5 {r['p5']:5.2f}x "
                f"pnl €{r['hedge_pnl']:,.0f} cost €{r['hedge_cost']:,.0f}",
                flush=True,
            )
        # Roll and carry sensitivities for the full hedge.
        for roll in (2.0, 10.0):
            r = run_case(case, FxHedgeConfig(ratio=1.0, roll_bps=roll), 1.0)
            r["variant"] = f"full roll {roll:.0f}bps"
            rows.append(r)
        for carry in (0.0, 400.0):
            r = run_case(case, FxHedgeConfig(ratio=1.0, carry_bps=carry), 1.0)
            r["variant"] = f"full carry {carry:.0f}bps"
            rows.append(r)
        for low in (0.25, 0.50):
            hedge = FxHedgeConfig(
                mode="threshold", low_ratio=low, high_ratio=1.0, roll_bps=5.0
            )
            r = run_case(case, hedge, 1.0)
            r["variant"] = f"threshold {low:.0%}/{100:.0f}%"
            rows.append(r)
        # 2x costs for the unhedged baseline and the full hedge (pre-registered).
        for ratio in (0.0, 1.0):
            r = run_case(case, FxHedgeConfig(ratio=ratio), 2.0)
            r["variant"] = f"fixed {ratio:.0%} 2x"
            rows.append(r)
        # Attach the 5-year comparison and the 2x-cost ratio to the base rows.
        for r in rows:
            if r["case"] != case:
                continue
            five = r.get("_five")
            if five is not None and len(five) and len(five_base):
                a, b = five.align(five_base, join="inner")
                r["five_beat"] = float((a > b).mean()) if len(a) else np.nan
                r["five_diff"] = float((a - b).median()) if len(a) else np.nan
            else:
                r["five_beat"] = np.nan
                r["five_diff"] = np.nan
        base2 = next(
            r for r in rows if r["case"] == case and r["variant"] == "fixed 0% 2x"
        )
        full2 = next(
            r for r in rows if r["case"] == case and r["variant"] == "fixed 100% 2x"
        )
        base["multiple_2x"] = base2["multiple"]
        for r in rows:
            if r["case"] != case:
                continue
            if r["variant"] == "fixed 100%" and r["cost_mult"] == 1.0:
                r["multiple_2x"] = full2["multiple"]
        for r in rows:
            if r["case"] != case:
                continue
            r["dd_improve"] = r["maxdd"] - base["maxdd"]
            r["y22_improve"] = r["y2022"] - base["y2022"]
            r["ret_ratio"] = r["multiple"] / base["multiple"]
            r["p5_ok"] = r["p5"] >= base["p5"]
            r["five_ok"] = (
                r["five_median"] >= 0.95 * base["five_median"]
                and r["five_beat"] >= 0.40
            )
            r["cost_ok"] = (
                np.isfinite(r.get("multiple_2x", np.nan))
                and r["multiple_2x"] >= 0.85 * base["multiple_2x"]
            )

    df = pd.DataFrame(rows)
    df = df.drop(columns=["_five"], errors="ignore")
    out = ROOT / "results" / "fx_hedge.csv"
    df.to_csv(out, index=False)
    print(f"\nwrote {out}")

    # Report tables.
    lines = []
    for case in CASES:
        sub = df[df.case == case]
        lines += [f"\n### {case}\n"]
        lines.append(
            "| variant | after-tax | CAGR | vol | maxDD | 2018 | 2022 | p5 | "
            "5y median | 5y beat | hedge P&L | roll cost | 2x after |"
        )
        lines.append(
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"
        )
        for _, r in sub.iterrows():
            lines.append(
                f"| {r['variant']} | {r['multiple']:.2f}x | {r['cagr']:.1%} | "
                f"{r['vol']:.1%} | {r['maxdd']:.1%} | {r['y2018']:+.1%} | "
                f"{r['y2022']:+.1%} | {r['p5']:.2f}x | {r['five_median']:.1%} | "
                f"{r['five_beat']:.0%} | €{r['hedge_pnl']:,.0f} | "
                f"€{r['hedge_cost']:,.0f} | {_fmt(r.get('multiple_2x'), '.2f')}x |"
            )
    (ROOT / "results" / "fx_hedge_tables.md").write_text("\n".join(lines) + "\n")
    print(f"wrote {ROOT / 'results' / 'fx_hedge_tables.md'}")


if __name__ == "__main__":
    main()
