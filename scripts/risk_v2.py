"""Pre-registered risk v2 tests: breadth, drawdown ladder, core/SPY mix.

See results/risk_v2.md for the pre-registration and decision rules. This script
runs every listed variant on the French TECH book and the US tax-profile book,
reports full-window, 2018, 2022 and block-bootstrap statistics, and runs a
leverage-matched baseline for any variant that changes average gross exposure.

Run: uv run python scripts/risk_v2.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot import backtest, france, markets, strategy, universe  # noqa: E402

START = "2015-01-01"
CAPITAL = 10_000.0
TR = "trade-republic"
ORIG_CANONICAL = universe.canonical

US_BASE = dict(
    start=START,
    rebalance_mode="calendar",
    exec_price="open",
    leverage=2.5,
    financing_bps=500.0,
    trend_ma=200,
    trend_scale=0.5,
    cost_bps=10.0,
    core="SMH",
    core_mode="always",
    leverage_band=0.30,
    tax_lots="hifo",
    tax_rate=0.37,
)
TECH_BASE = dict(
    start=START,
    rebalance_mode="calendar",
    exec_price="open",
    leverage=2.5,
    financing_bps=500.0,
    trend_ma=200,
    trend_scale=0.5,
    cost_bps=5.0,
    tax_lots="hifo",
    core_mode="always",
    leverage_band=0.30,
)

VARIANTS = [
    dict(name="baseline", kind="base"),
    dict(name="R1a breadth 50%", kind="breadth", params=("ramp", 0.50, 0.50)),
    dict(name="R1b breadth 40%", kind="breadth", params=("ramp", 0.40, 0.50)),
    dict(name="R1c breadth 60%", kind="breadth", params=("ramp", 0.60, 0.50)),
    dict(name="R1d breadth 3-step", kind="breadth", params=("steps",)),
    dict(
        name="R2a dd -30% 1.5x",
        kind="dd",
        kwargs=dict(dd_stop=0.30, dd_scale=0.60, dd_recover=0.15),
    ),
    dict(
        name="R2b dd -25% 1.5x",
        kind="dd",
        kwargs=dict(dd_stop=0.25, dd_scale=0.60, dd_recover=0.15),
    ),
    dict(
        name="R2c dd -35% 1.5x",
        kind="dd",
        kwargs=dict(dd_stop=0.35, dd_scale=0.60, dd_recover=0.15),
    ),
    dict(
        name="R2d dd -30% 1.75x",
        kind="dd",
        kwargs=dict(dd_stop=0.30, dd_scale=0.70, dd_recover=0.15),
    ),
    dict(name="R3a core 80/20", kind="mix", params=(0.80, 0.20)),
    dict(name="R3b core 70/30", kind="mix", params=(0.70, 0.30)),
]


def breadth_series(mkt: strategy.Market, ma: int = 200) -> pd.Series:
    """Point-in-time fraction of members above their own `ma`-day MA.

    `mkt.members` is the membership snapshot on or before each date, and the
    rolling mean uses only prices up to that date, so this is causal.
    """
    px = mkt.px
    avg = px.rolling(ma, min_periods=ma).mean()
    above = px > avg
    member = mkt.members.reindex(columns=px.columns, fill_value=False)
    valid = member & px.notna() & avg.notna()
    denom = valid.sum(axis=1)
    num = (above & valid).sum(axis=1)
    return (num / denom.replace(0, np.nan)).astype(float)


def breadth_selector(breadth: pd.Series, params: tuple):
    mode = params[0]

    def sel(mkt, date, held, banned):
        base = backtest._default_selector(mkt, date, held, banned)
        if not base:
            return base
        b = float(breadth.get(date, np.nan))
        if not np.isfinite(b):
            return base
        if mode == "ramp":
            thr, floor = float(params[1]), float(params[2])
            scale = 1.0 if b >= thr else floor + (1.0 - floor) * (b / thr)
        else:  # pre-registered three-step rule
            scale = 0.50 if b < 1.0 / 3.0 else 0.75 if b < 2.0 / 3.0 else 1.0
        return {t: w * scale for t, w in base.items()}

    return sel


def mix_selector(params: tuple):
    w_core, w_spy = float(params[0]), float(params[1])

    def sel(mkt, date, held, banned):
        base = backtest._default_selector(mkt, date, held, banned)
        if not base:
            return base
        core = mkt.cfg.core
        if core in base and len(base) == 1 and "SPY" in mkt.px.columns:
            total = base[core]
            return {core: total * w_core, "SPY": total * w_spy}
        return base

    return sel


def bootstrap(
    rets: pd.Series, block: int = 10, n: int = 2000, seed: int = 7
) -> tuple[np.ndarray, np.ndarray]:
    """Same block bootstrap as scripts/robustness.py."""
    rng = np.random.default_rng(seed)
    x = rets.to_numpy()
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


def year_stats(eq: pd.Series, year: int) -> tuple[float, float]:
    yr = eq[eq.index.year == year]
    if len(yr) == 0:
        return float("nan"), float("nan")
    before = eq[eq.index < yr.index[0]]
    base = before.iloc[-1] if len(before) else yr.iloc[0]
    ret = float(yr.iloc[-1] / base - 1.0)
    dd = float((yr / yr.cummax() - 1.0).min())
    return ret, dd


def stats(res, mkt, market: str, base_eq: pd.Series | None = None) -> dict:
    eq = res.equity
    r = eq.pct_change().dropna()
    m = backtest.metrics(eq, mkt.spy)
    finals, dds = bootstrap(r)
    gross = res.weights.drop(columns=["CASH"], errors="ignore").sum(axis=1)
    y18, dd18 = year_stats(eq, 2018)
    y22, dd22 = year_stats(eq, 2022)
    out = {
        "final": float(eq.iloc[-1]),
        "cagr": float(m.get("cagr", np.nan)),
        "maxdd": float(m.get("max_dd", np.nan)),
        "sharpe": float(m.get("sharpe", np.nan)),
        "turnover": float(m.get("turnover_ann", np.nan)),
        "avg_gross": float(gross.mean()),
        "p5": float(np.quantile(finals, 0.05)),
        "median": float(np.median(finals)),
        "p95": float(np.quantile(finals, 0.95)),
        "dd_median": float(np.median(dds)),
        "dd_p5": float(np.quantile(dds, 0.05)),
        "y2018": y18,
        "dd2018": dd18,
        "y2022": y22,
        "dd2022": dd22,
        "trades": len(res.trades),
    }
    if base_eq is not None:
        out["changed"] = bool(
            float((eq - base_eq.reindex(eq.index)).abs().max()) > 1e-9
        )
    else:
        out["changed"] = False
    if market == "TECH":
        f = france.after_tax(res, CAPITAL, "TECH")
        out.update(
            after_tax_x=f["after_tax_eur"] / CAPITAL,
            after_tax_cagr=f["cagr_eur"],
            fees_x=f["fees_eur"] / CAPITAL,
            tax_paid_x=f["tax_paid_eur"] / CAPITAL,
            exit_tax_x=f["exit_tax_eur"] / CAPITAL,
        )
    return out


def prepare_panel(market: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Set the ticker canonicalisation before building the panel."""
    universe.canonical = ORIG_CANONICAL if market == "US" else (lambda t: t)
    return markets.build(market)


def run_config(
    market: str,
    panel: pd.DataFrame,
    mem: pd.DataFrame,
    extra: dict | None = None,
    kind: str = "base",
    params: tuple | None = None,
):
    """Build the market/config, attach a test selector, and run one book."""
    if market == "TECH":
        kw = dict(TECH_BASE)
        kw.update(france.kwargs("TECH", TR, "cto", CAPITAL, panel))
    else:
        kw = dict(US_BASE)
    kw.update(extra or {})
    cfg = strategy.Config(**kw)
    mkt = strategy.Market(panel, mem, cfg)
    sel = None
    if kind == "breadth":
        sel = breadth_selector(breadth_series(mkt), params)
    elif kind == "mix":
        sel = mix_selector(params)
    return cfg, mkt, backtest.run(mkt, selector=sel)


def run_market(market: str) -> pd.DataFrame:
    panel, mem = prepare_panel(market)
    rows = []
    base_res = None
    base_stats = None
    for spec in VARIANTS:
        cfg, mkt, res = run_config(
            market,
            panel,
            mem,
            extra=spec.get("kwargs"),
            kind=spec["kind"],
            params=spec.get("params"),
        )
        st = stats(res, mkt, market, base_res.equity if base_res is not None else None)
        st["variant"] = spec["name"]
        st["market"] = market
        st["leverage"] = cfg.leverage
        if base_res is None:
            base_res, base_stats = res, st
            st["match_lev"] = cfg.leverage
            st["match_final"] = st["final"]
            st["match_p5"] = st["p5"]
            st["match_median"] = st["median"]
            st["match_maxdd"] = st["maxdd"]
            if market == "TECH":
                st["match_after_tax_x"] = st["after_tax_x"]
        else:
            # Leverage-matched baseline: same average gross, same rules, no selector.
            if abs(st["avg_gross"] - base_stats["avg_gross"]) > 0.01:
                lev = 2.5 * st["avg_gross"] / base_stats["avg_gross"]
                mkw = dict(spec.get("kwargs") or {})
                mkw.update(dd_stop=0.0, dd_scale=0.5, dd_recover=0.10, leverage=lev)
                # The variant's dd/selector must not leak into the matched baseline.
                _, mmkt, mres = run_config(market, panel, mem, extra=mkw)
                mst = stats(mres, mmkt, market)
                st["match_lev"] = lev
                st["match_final"] = mst["final"]
                st["match_p5"] = mst["p5"]
                st["match_median"] = mst["median"]
                st["match_maxdd"] = mst["maxdd"]
                if market == "TECH":
                    st["match_after_tax_x"] = mst["after_tax_x"]
            else:
                st["match_lev"] = 2.5
                st["match_final"] = base_stats["final"]
                st["match_p5"] = base_stats["p5"]
                st["match_median"] = base_stats["median"]
                st["match_maxdd"] = base_stats["maxdd"]
                if market == "TECH":
                    st["match_after_tax_x"] = base_stats["after_tax_x"]
        rows.append(st)
        print(
            f"{market:4s} {spec['name']:22s} final {st['final']:9.2f}x "
            f"p5 {st['p5']:7.2f} med {st['median']:8.2f} "
            f"dd {st['maxdd']:6.1%} gross {st['avg_gross']:.2f}",
            flush=True,
        )
    return pd.DataFrame(rows)


def fmt_table(df: pd.DataFrame, tech: bool) -> str:
    if tech:
        head = (
            "| variant | after-tax x | CAGR | maxDD | Sharpe | p5 | median | "
            "2018 | 2022 | avg gross | matched after-tax x | changed |"
        )
        sep = "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|:---:|"
    else:
        head = (
            "| variant | final x | CAGR | maxDD | Sharpe | p5 | median | p5 DD | "
            "2018 | 2022 | avg gross | matched final x | matched p5 |"
        )
        sep = "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"
    lines = [head, sep]
    for _, r in df.iterrows():
        if tech:
            lines.append(
                f"| {r['variant']} | {r['after_tax_x']:.2f}x | "
                f"{r['after_tax_cagr']:.1%} | {r['maxdd']:.1%} | "
                f"{r['sharpe']:.2f} | {r['p5']:.2f}x | {r['median']:.2f}x | "
                f"{r['y2018']:+.1%} | {r['y2022']:+.1%} | "
                f"{r['avg_gross']:.2f} | {r.get('match_after_tax_x', np.nan):.2f}x | "
                f"{'yes' if r.get('changed', False) else 'no'} |"
            )
        else:
            lines.append(
                f"| {r['variant']} | {r['final']:.2f}x | {r['cagr']:.1%} | "
                f"{r['maxdd']:.1%} | {r['sharpe']:.2f} | {r['p5']:.2f}x | "
                f"{r['median']:.2f}x | {r['dd_p5']:.0%} | "
                f"{r['y2018']:+.1%} | {r['y2022']:+.1%} | "
                f"{r['avg_gross']:.2f} | {r.get('match_final', np.nan):.2f}x | "
                f"{r.get('match_p5', np.nan):.2f}x |"
            )
    return "\n".join(lines)


def dd_sweep() -> None:
    """Post-hoc only: is the -25% drawdown trigger a plateau or a spike?"""
    triggers = [0.20, 0.22, 0.24, 0.25, 0.26, 0.28, 0.30, 0.32, 0.35]
    for market in ("US", "TECH"):
        panel, mem = prepare_panel(market)
        print(f"\n## DD trigger sweep, {market} (cap 1.5x, recover -15%)\n")
        print(
            "| trigger | final | after-tax | p5 | median | maxDD | 2018 | 2022 | avg gross |"
        )
        print("|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
        for trig in triggers:
            cfg, mkt, res = run_config(
                market,
                panel,
                mem,
                extra=dict(dd_stop=trig, dd_scale=0.60, dd_recover=0.15),
            )
            st = stats(res, mkt, market)
            print(
                f"| -{trig:.0%} | {st['final']:.2f}x | "
                f"{st.get('after_tax_x', np.nan):.2f}x | {st['p5']:.2f}x | "
                f"{st['median']:.2f}x | {st['maxdd']:.1%} | "
                f"{st['y2018']:+.1%} | {st['y2022']:+.1%} | "
                f"{st['avg_gross']:.2f} |"
            )


def boot_sens() -> None:
    """Post-hoc only: bootstrap p5/median under other seeds and block lengths."""
    specs = [
        ("baseline", "base", None, None),
        ("R1d breadth 3-step", "breadth", ("steps",), None),
        ("R2a dd -30%", "dd", None, dict(dd_stop=0.30, dd_scale=0.60, dd_recover=0.15)),
        ("R2b dd -25%", "dd", None, dict(dd_stop=0.25, dd_scale=0.60, dd_recover=0.15)),
        ("R3a core 80/20", "mix", (0.80, 0.20), None),
    ]
    combos = [(5, 7), (10, 7), (21, 7), (10, 11), (10, 23)]
    for market in ("US", "TECH"):
        panel, mem = prepare_panel(market)
        print(f"\n## Bootstrap sensitivity, {market}\n")
        print("| variant | block | seed | p5 | median | p95 |")
        print("|---|---:|---:|---:|---:|---:|")
        for name, kind, params, extra in specs:
            cfg, mkt, res = run_config(
                market, panel, mem, extra=extra, kind=kind, params=params
            )
            r = res.equity.pct_change().dropna()
            for block, seed in combos:
                finals, _ = bootstrap(r, block=block, seed=seed)
                print(
                    f"| {name} | {block} | {seed} | {np.quantile(finals, 0.05):.2f}x | "
                    f"{np.median(finals):.2f}x | {np.quantile(finals, 0.95):.2f}x |"
                )


def check_breadth() -> None:
    """Truncating future prices must not change earlier breadth values."""
    for market in ("US", "TECH"):
        panel, mem = prepare_panel(market)
        if market == "TECH":
            kw = dict(TECH_BASE)
            kw.update(france.kwargs("TECH", TR, "cto", CAPITAL, panel))
        else:
            kw = dict(US_BASE)
        cfg = strategy.Config(**kw)
        full = breadth_series(strategy.Market(panel, mem, cfg))
        for cut in ("2018-06-01", "2022-06-01"):
            cut_ts = pd.Timestamp(cut)
            part = panel[panel["date"] <= cut_ts]
            again = breadth_series(strategy.Market(part, mem, cfg))
            a = full.loc[:cut_ts]
            b = again.loc[:cut_ts]
            assert np.allclose(a.values, b.values, equal_nan=True), (
                f"{market} breadth leaked future data at {cut}"
            )
        print(f"{market}: breadth causal check ok")


def main() -> None:
    if "--sweep" in sys.argv:
        dd_sweep()
        boot_sens()
        return
    if "--check" in sys.argv:
        check_breadth()
        return
    us = run_market("US")
    tech = run_market("TECH")
    print("\n## US tax profile (10 bps, 37% federal)\n")
    print(fmt_table(us, tech=False))
    print("\n## French TECH book (CTO, Trade Republic, $10k)\n")
    print(fmt_table(tech, tech=True))
    print("\n## French cost decomposition (per 10k EUR of initial capital)\n")
    print("| variant | fees x | tax paid x | exit tax x |")
    print("|---|---:|---:|---:|")
    for _, r in tech.iterrows():
        print(
            f"| {r['variant']} | {r['fees_x']:.3f}x | {r['tax_paid_x']:.3f}x | "
            f"{r['exit_tax_x']:.3f}x |"
        )
    out = pd.concat([us, tech], ignore_index=True)
    out.to_csv(ROOT / "results" / "risk_v2.csv", index=False)
    print(f"\nwrote {ROOT / 'results' / 'risk_v2.csv'}")


if __name__ == "__main__":
    main()
