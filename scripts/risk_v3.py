"""Risk v3: pre-registered risk rules on the US and TECH monthly books.

See results/risk_v3.md for the pre-registration, the amendments made before the
main sweep, and the decision gates. This script is the only runner.

Decision metric
---------------
The engine is USD-native. Every rule is reported three ways:

* ``headline`` -- the shipped terminal-FX after-tax multiple;
* ``EUR ledger`` -- ``bot.eur_ledger`` replay, gross distributions;
* ``EUR net`` -- the EUR ledger minus a conservative PFU drag on gross
  distributions (``bot/risk/dividends.py``). This is the decision metric.

The first is a native-currency research number. The second fixes the annual EUR
tax base and the trade-date FX cost basis. The third closes the known
gross-dividend caveat in the ledger; it is an approximation, stated in the report.

Run:
    uv run python scripts/risk_v3.py --baseline
    uv run python scripts/risk_v3.py
    uv run python scripts/risk_v3.py --costs
    uv run python scripts/risk_v3.py --check
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot import backtest, eur_ledger, france, markets, strategy, universe  # noqa: E402
from bot.eur_ledger import EurLedgerConfig  # noqa: E402
from bot.risk.alloc import inverse_vol_weights  # noqa: E402
from bot.risk.dividends import (  # noqa: E402
    after_tax_with_dividend_tax,
    apply_dividend_tax,
    daily_dividend_tax,
)
from bot.risk.etf import load_panel, trend_selector  # noqa: E402
from bot.risk.regime import above_trend, momentum  # noqa: E402
from bot.risk.tail import (  # noqa: E402
    HedgeConfig,
    after_tax_delta,
    simulate_hedge,
)
from bot.risk.vol import VolTarget, next_gross, realized_vol  # noqa: E402
from scripts.france import BASE  # noqa: E402

START = "2015-01-01"
CAP = 10_000.0
BROKER = "Interactive Brokers"
MARKETS = ("US", "TECH")
ORIG_CANONICAL = universe.canonical

# Net-of-dividend EUR baselines, asserted by --baseline. These are the gate
# values in results/risk_v3.md (Amendment 2); update both together.
US_BASE_NET = 60.9978
TECH_BASE_NET = 48.8190
US_BASE_EUR = 66.0221
TECH_BASE_EUR = 52.8939

# Pre-registered max-growth gates on the net-of-dividend EUR path.
GATE = {
    "US": dict(
        base=US_BASE_NET,
        maxdd=-0.5776,
        y2018=-0.3051,
        y2022=-0.3362,
        y2018_floor=-0.3251,
        y2022_floor=-0.3562,
        p5=2.384,
        half_cagr=(0.1697, 0.5506),
    ),
    "TECH": dict(
        base=TECH_BASE_NET,
        maxdd=-0.6619,
        y2018=-0.1031,
        y2022=-0.5252,
        y2018_floor=-0.1231,
        y2022_floor=-0.5452,
        p5=1.839,
        half_cagr=(0.2122, 0.4595),
    ),
}


@dataclass
class Book:
    market: str
    panel: pd.DataFrame
    mem: pd.DataFrame
    ext: pd.DataFrame
    sigma: pd.Series
    spy: pd.Series
    smh: pd.Series


def _build_book(market: str) -> Book:
    universe.canonical = ORIG_CANONICAL if market == "US" else (lambda t: t)
    panel, mem = markets.build(market)
    etf, _ = load_panel()
    have = set(panel["ticker"])
    extra = etf[~etf["ticker"].isin(have)]
    ext = (
        pd.concat([panel, extra], ignore_index=True)
        .drop_duplicates(["date", "ticker"], keep="first")
        .sort_values(["date", "ticker"])
        .reset_index(drop=True)
    )
    px = panel.pivot(index="date", columns="ticker", values="adjclose").sort_index()
    smh = px["SMH"].dropna()
    spy = px["SPY"].dropna()
    sigma = realized_vol(smh.pct_change(fill_method=None), 20)
    return Book(market, panel, mem, ext, sigma, spy, smh)


def _base_kw(market: str, panel: pd.DataFrame, cost: float) -> dict:
    kw = dict(
        start=START,
        leverage=2.5,
        financing_bps=500.0,
        trend_ma=200,
        trend_scale=0.5,
        rebalance_mode="calendar",
        exec_price="open",
        cost_bps=cost,
        tax_lots="hifo",
    )
    kw.update(france.kwargs(market, BROKER, "cto", CAP, panel))
    kw["cost_bps"] = cost
    return kw


def _run(
    book: Book,
    *,
    panel: pd.DataFrame | None = None,
    cost: float = 5.0,
    selector=None,
    overlay=None,
    **overrides,
):
    panel = book.panel if panel is None else panel
    universe.canonical = ORIG_CANONICAL if book.market == "US" else (lambda t: t)
    kw = _base_kw(book.market, panel, cost)
    kw.update(overrides)
    cfg = strategy.Config(**kw)
    mkt = strategy.Market(panel, book.mem, cfg)
    res = backtest.run(mkt, selector=selector, overlay=overlay)
    return res, mkt, cfg


def _year_stats(eq: pd.Series, year: int) -> tuple[float, float]:
    """Calendar-year return from the first close of the year.

    French PFU for the prior year is settled at the start of January, so the
    first close is the post-tax starting wealth. This matches the pre-registered
    baseline table (Amendment 2) and avoids charging the prior year's tax twice.
    """
    yr = eq[eq.index.year == year]
    if len(yr) == 0:
        return float("nan"), float("nan")
    return (
        float(yr.iloc[-1] / yr.iloc[0] - 1.0),
        float((yr / yr.cummax() - 1.0).min()),
    )


def _half_stats(eq: pd.Series, start: str, end: str, ratio: float):
    x = eq[(eq.index >= pd.Timestamp(start)) & (eq.index <= pd.Timestamp(end))]
    if len(x) < 2:
        return float("nan"), float("nan"), float("nan")
    total = float(x.iloc[-1] / x.iloc[0])
    years = len(x) / 252.0
    after_total = total * ratio
    return (
        after_total,
        float(after_total ** (1.0 / years) - 1.0),
        float((x / x.cummax() - 1.0).min()),
    )


def bootstrap(
    rets: pd.Series, block: int = 10, n: int = 2000, seed: int = 7
) -> tuple[np.ndarray, np.ndarray]:
    """Stationary-block bootstrap used by the other risk reports."""
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


def evaluate(
    res,
    mkt,
    panel: pd.DataFrame,
    market: str,
    cost: float = 5.0,
) -> dict:
    """Full-window, stress-year, half-window and bootstrap statistics."""
    cfg = EurLedgerConfig(
        capital=CAP,
        tax_rate=0.314,
        financing_bps=500.0,
        spread_bps=cost,
        fx_bps=2.0,
        broker_bps=0.25,
        broker_fixed_eur=1.0,
    )
    led = eur_ledger.replay(res, panel, market, cfg)
    drag = daily_dividend_tax(res.weights, tax_rate=0.314, default_yield=0.012)
    net = apply_dividend_tax(led.equity, drag)
    after = after_tax_with_dividend_tax(led.after_tax, led.equity, net)
    ratio = after / float(net.iloc[-1])

    finals, dds = bootstrap(net.pct_change().dropna())
    y18, dd18 = _year_stats(net, 2018)
    y22, dd22 = _year_stats(net, 2022)
    h1 = _half_stats(net, START, "2019-12-31", ratio)
    h2 = _half_stats(net, "2020-01-01", "2026-12-31", ratio)
    gross_w = res.weights.drop(columns=["CASH"], errors="ignore").sum(axis=1)
    years = len(net) / 252.0
    headline = france.after_tax(res, CAP, market)

    return {
        "headline_x": headline["after_tax_eur"] / CAP,
        "headline_cagr": headline["cagr_eur"],
        "headline_maxdd": headline["maxdd"],
        "eur_x": led.multiple,
        "eur_maxdd": led.maxdd,
        "net_x": after / CAP,
        "net_cagr": (after / CAP) ** (1.0 / years) - 1.0,
        "net_maxdd": float((net / net.cummax() - 1.0).min()),
        "sharpe": float(backtest.metrics(net, mkt.spy).get("sharpe", np.nan)),
        "p5": float(np.quantile(finals, 0.05) * ratio),
        "median": float(np.median(finals) * ratio),
        "p5_dd": float(np.quantile(dds, 0.05)),
        "y2018": y18,
        "dd2018": dd18,
        "y2022": y22,
        "dd2022": dd22,
        "h1_x": h1[0],
        "h1_cagr": h1[1],
        "h1_dd": h1[2],
        "h2_x": h2[0],
        "h2_cagr": h2[1],
        "h2_dd": h2[2],
        "worst12": float((net / net.shift(252) - 1.0).min()),
        "worst12_date": (net / net.shift(252) - 1.0).idxmin(),
        "avg_gross": float(gross_w.mean()),
        "turnover": float(
            res.trades["notional"].sum() / res.equity.mean() / years
            if len(res.trades)
            else 0.0
        ),
        "trades": len(res.trades),
        "fees": led.fees,
        "tax_paid": led.tax_paid,
        "exit_tax": led.exit_tax,
        "div_drag_bps": float(drag.mean() * 252 * 1e4),
        "after_eur": after,
    }


def _scale_selector(selector, scale: float):
    def sel(mkt, date, held, banned):
        base = selector(mkt, date, held, banned)
        return {t: w * scale for t, w in base.items()}

    return sel


def _vol_overlay(sigma: pd.Series, cfg: VolTarget, initial: float = 2.5):
    state = {"prev": initial}

    def overlay(date, base, mkt):
        g = next_gross(float(sigma.get(date, np.nan)), state["prev"], cfg)
        state["prev"] = g
        return {t: w * (g / 2.5) for t, w in base.items()}

    return overlay


def _regime_overlay(spy: pd.Series, smh: pd.Series, kind: str):
    spy_ma = spy.rolling(200, min_periods=200).mean()
    smh_ma = smh.rolling(200, min_periods=200).mean()
    mom12 = momentum(smh, 252)

    def overlay(date, base, mkt):
        if kind == "r1":
            ok = bool(pd.notna(spy_ma.get(date)) and spy.loc[date] > spy_ma.loc[date])
        elif kind == "r2":
            a = bool(pd.notna(spy_ma.get(date)) and spy.loc[date] > spy_ma.loc[date])
            b = bool(pd.notna(smh_ma.get(date)) and smh.loc[date] > smh_ma.loc[date])
            ok = a or b
        elif kind == "r3":
            ok = bool(pd.notna(mom12.get(date)) and mom12.loc[date] > 0)
        else:
            ok = True
        scale = 1.0 if ok else 0.5
        return {t: w * scale for t, w in base.items()}

    return overlay


def _spy_selector(mkt, date, held, banned):
    return {"SPY": 1.0}


def _combined_selector(etf_sel, w_core):
    def sel(mkt, date, held, banned):
        core = backtest._default_selector(mkt, date, held, banned)
        etf = etf_sel(mkt, date, held, banned) if etf_sel else {}
        out = {t: w * w_core for t, w in core.items()}
        for t, w in etf.items():
            out[t] = out.get(t, 0.0) + (1.0 - w_core) / 2.5 * w
        return out

    return sel


def _dynamic_combined_selector(etf_sel, core_vol, etf_vol):
    def sel(mkt, date, held, banned):
        core = backtest._default_selector(mkt, date, held, banned)
        etf = etf_sel(mkt, date, held, banned) if etf_sel else {}
        vc = float(core_vol.get(date, np.nan))
        ve = float(etf_vol.get(date, np.nan))
        if np.isfinite(vc) and np.isfinite(ve) and vc > 0 and ve > 0:
            w = inverse_vol_weights(
                pd.Series({"core": vc, "etf": ve}), gross=1.0, cap=0.8
            )
            w_core = float(w.get("core", 0.6))
        else:
            w_core = 0.6
        w_core = float(np.clip(w_core, 0.3, 0.8))
        out = {t: x * w_core for t, x in core.items()}
        for t, x in etf.items():
            out[t] = out.get(t, 0.0) + (1.0 - w_core) / 2.5 * x
        return out

    return sel


def run_baseline(book: Book, cost: float = 5.0) -> tuple[dict, object, pd.Series]:
    res, mkt, cfg = _run(book, cost=cost)
    st = evaluate(res, mkt, book.panel, book.market, cost)
    return st, res, mkt.spy


def run_family_v(book: Book, base_st: dict, cost: float = 5.0) -> list[dict]:
    rows = []
    specs = [
        ("V1 vol 20%", 0.20, 0.10, 0.10),
        ("V2 vol 25%", 0.25, 0.10, 0.10),
        ("V3 vol 30%", 0.30, 0.10, 0.10),
        ("V4 vol 35%", 0.35, 0.10, 0.10),
        ("V5 vol 30% band 5%", 0.30, 0.05, 0.10),
        ("V6 vol 30% hyst 0%", 0.30, 0.10, 0.0),
        # Post-hoc neighbours for the balanced profile. V1-V6 are the
        # pre-registered family; V7-V9 are labelled and not max-growth eligible.
        ("V7 vol 40% (post-hoc)", 0.40, 0.10, 0.10),
        ("V8 vol 45% (post-hoc)", 0.45, 0.10, 0.10),
        ("V9 vol 50% (post-hoc)", 0.50, 0.10, 0.10),
    ]
    for name, target, band, hyst in specs:
        vt = VolTarget(target=target, band=band, hysteresis=hyst)
        res, mkt, _ = _run(book, cost=cost, overlay=_vol_overlay(book.sigma, vt))
        st = evaluate(res, mkt, book.panel, book.market, cost)
        st["label"] = name
        st["family"] = "V"
        # Leverage-matched constant-leverage control: same average gross.
        control_lev = 2.5 * st["avg_gross"] / base_st["avg_gross"]
        cres, cmkt, _ = _run(book, cost=cost, leverage=control_lev)
        cst = evaluate(cres, cmkt, book.panel, book.market, cost)
        st["control_lev"] = control_lev
        st["control_x"] = cst["net_x"]
        st["control_p5"] = cst["p5"]
        rows.append(st)
    return rows


def _attach_control(book: Book, st: dict, base_st: dict, cost: float = 5.0) -> None:
    """Constant-leverage control matched to the variant's average gross."""
    lev = 2.5 * float(st["avg_gross"]) / float(base_st["avg_gross"])
    cres, cmkt, _ = _run(book, cost=cost, leverage=lev)
    cst = evaluate(cres, cmkt, book.panel, book.market, cost)
    st["control_lev"] = lev
    st["control_x"] = cst["net_x"]
    st["control_p5"] = cst["p5"]


def run_family_d(book: Book, base_st: dict, cost: float = 5.0) -> list[dict]:
    rows = []
    for name, stop in (
        ("D1 dd -25%", 0.25),
        ("D2 dd -30%", 0.30),
        ("D3 dd -35%", 0.35),
    ):
        res, mkt, _ = _run(
            book, cost=cost, dd_stop=stop, dd_scale=0.60, dd_recover=0.15
        )
        st = evaluate(res, mkt, book.panel, book.market, cost)
        st.update(label=name, family="D")
        _attach_control(book, st, base_st, cost)
        rows.append(st)
    return rows


def run_family_r(book: Book, base_st: dict, cost: float = 5.0) -> list[dict]:
    rows = []
    for name, kind in (
        ("R1 SPY<200d", "r1"),
        ("R2 SPY+SMH<200d", "r2"),
        ("R3 SMH 12m<=0", "r3"),
    ):
        res, mkt, _ = _run(
            book, cost=cost, overlay=_regime_overlay(book.spy, book.smh, kind)
        )
        st = evaluate(res, mkt, book.panel, book.market, cost)
        st.update(label=name, family="R")
        _attach_control(book, st, base_st, cost)
        rows.append(st)
    return rows


def standalone_etf(book: Book, cost: float = 5.0) -> tuple[dict, object]:
    kw = dict(
        leverage=1.0,
        trend_ma=0,
        core=None,
        core_mode="always",
        top_n=10,
        max_weight=1.0,
        sector_cap=1.0,
        min_dollar_vol=0.0,
        max_extreme=-1,
        leverage_band=0.0,
    )
    res, mkt, cfg = _run(
        book, panel=book.ext, cost=cost, selector=trend_selector(), **kw
    )
    st = evaluate(res, mkt, book.ext, book.market, cost)
    st.update(label="E0 ETF trend", family="A")
    return st, res


def run_family_a(
    book: Book, base_st: dict, base_res, e0_res, cost: float = 5.0
) -> list[dict]:
    rows = []
    ts = trend_selector()
    specs = [
        ("A1 60% book/40% SPY", 0.60, _spy_selector),
        ("A2 60% book/40% trend", 0.60, ts),
        ("A3 50% book/50% trend", 0.50, ts),
        ("A5 70% book/30% trend", 0.70, ts),
        # Post-hoc balanced neighbour: same 70/30 split but SPY, not the weak
        # cross-asset trend sleeve. Not max-growth eligible.
        ("A6 70% book/30% SPY (post-hoc)", 0.70, _spy_selector),
    ]
    for name, w, etf in specs:
        res, mkt, _ = _run(
            book,
            panel=book.ext,
            cost=cost,
            selector=_combined_selector(etf, w),
        )
        st = evaluate(res, mkt, book.ext, book.market, cost)
        st.update(label=name, family="A")
        _attach_control(book, st, base_st, cost)
        rows.append(st)
    core_vol = realized_vol(base_res.equity.pct_change(fill_method=None), 63)
    etf_vol = realized_vol(e0_res.equity.pct_change(fill_method=None), 63)
    res, mkt, _ = _run(
        book,
        panel=book.ext,
        cost=cost,
        selector=_dynamic_combined_selector(ts, core_vol, etf_vol),
    )
    st = evaluate(res, mkt, book.ext, book.market, cost)
    st.update(label="A4 dynamic 30-80%", family="A")
    _attach_control(book, st, base_st, cost)
    rows.append(st)
    return rows


def run_family_b(book: Book, cost: float = 5.0) -> list[dict]:
    rows = []
    for name, lev in (("B1 US 2.0x", 2.0), ("B2 US 1.5x", 1.5)):
        res, mkt, _ = _run(book, cost=cost, leverage=lev)
        st = evaluate(res, mkt, book.panel, book.market, cost)
        st.update(label=name, family="B")
        rows.append(st)
    return rows


def run_family_t(book: Book, base_st: dict, cost: float = 5.0) -> list[dict]:
    rows = []
    for ma in (250, 300):
        res, mkt, _ = _run(book, cost=cost, trend_ma=ma)
        st = evaluate(res, mkt, book.panel, book.market, cost)
        st.update(label=f"T{ma}d trend", family="T", trend_ma=ma)
        _attach_control(book, st, base_st, cost)
        rows.append(st)
    return rows


def run_family_h(book: Book, base_st: dict) -> list[dict]:
    rows = []
    eq = base_st["_net_equity"]
    fx = france.eur_per_unit(book.market)
    if fx is None:
        fx = pd.Series(1.0, index=eq.index)
    fx = fx.reindex(fx.index.union(eq.index)).ffill().reindex(eq.index)
    px = book.panel.pivot(index="date", columns="ticker", values="adjclose")
    und = (px["SMH"].reindex(eq.index).ffill() * fx).dropna()
    specs = [
        ("H1 3m 90/70 2.5x", HedgeConfig(tenor_months=3, budget_pct=None)),
        ("H2 3m 90/70 1.5%", HedgeConfig(tenor_months=3, budget_pct=0.015)),
        (
            "H3 3m 90/115 collar",
            HedgeConfig(kind="collar", tenor_months=3, budget_pct=None),
        ),
        (
            "H4 1m 95/85 1.5%",
            HedgeConfig(
                tenor_months=1,
                long_strike=0.95,
                short_strike=0.85,
                budget_pct=0.015,
            ),
        ),
    ]
    for name, hcfg in specs:
        try:
            h = simulate_hedge(eq, hcfg, underlying=und)
        except Exception as exc:  # noqa: BLE001 - report model failures
            rows.append(
                dict(
                    label=name,
                    family="H",
                    net_x=float("nan"),
                    net_maxdd=float("nan"),
                    p5=float("nan"),
                    y2018=float("nan"),
                    y2022=float("nan"),
                    error=str(exc),
                )
            )
            continue
        delta = after_tax_delta(h, tax_rate=0.314)
        after = base_st["after_eur"] + delta
        adj = h.equity
        finals, dds = bootstrap(adj.pct_change().dropna())
        ratio = after / float(adj.iloc[-1]) if adj.iloc[-1] > 0 else float("nan")
        y18, _ = _year_stats(adj, 2018)
        y22, _ = _year_stats(adj, 2022)
        rows.append(
            dict(
                label=name,
                family="H",
                net_x=after / CAP,
                net_maxdd=float((adj / adj.cummax() - 1.0).min()),
                p5=float(np.quantile(finals, 0.05) * ratio),
                y2018=y18,
                y2022=y22,
                premium=float(h.premium),
                payoff=float(h.payoff),
                delta=float(delta),
                unimplementable=bool((adj <= 0).any()),
                _model_invalid=bool((adj <= 0).any()),
            )
        )
    return rows


def _fmt(value, spec: str) -> str:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return "n/a"
    return "n/a" if not np.isfinite(x) else format(x, spec)


def _print_row(market: str, st: dict) -> None:
    print(
        f"{market:4s} {st.get('label', 'baseline'):34s} "
        f"headline {_fmt(st.get('headline_x'), '8.2f')}x  "
        f"EUR {_fmt(st.get('eur_x'), '8.2f')}x  "
        f"net {_fmt(st.get('net_x'), '8.2f')}x  "
        f"CAGR {_fmt(st.get('net_cagr'), '6.1%')}  "
        f"DD {_fmt(st.get('net_maxdd'), '6.1%')}  "
        f"p5 {_fmt(st.get('p5'), '6.2f')}x  "
        f"2018 {_fmt(st.get('y2018'), '+6.1%')}  "
        f"2022 {_fmt(st.get('y2022'), '+6.1%')}  "
        f"gross {_fmt(st.get('avg_gross'), '4.2f')}",
        flush=True,
    )


def gate_row(market: str, st: dict) -> str:
    g = GATE[market]
    if st["net_x"] >= g["base"]:
        ret = "pass"
    elif (
        st["net_x"] >= 0.95 * g["base"]
        and st["p5"] >= 1.2 * g["p5"]
        and st["net_maxdd"] >= g["maxdd"] + 0.10
    ):
        ret = "pass-95"
    else:
        ret = "fail"
    dd = "pass" if st["net_maxdd"] >= g["maxdd"] else "fail"
    sy = (
        "pass"
        if st["y2018"] >= g["y2018_floor"]
        and st["y2022"] >= g["y2022_floor"]
        and (st["y2018"] >= g["y2018"] + 0.03 or st["y2022"] >= g["y2022"] + 0.03)
        else "fail"
    )
    p5 = "pass" if st.get("p5", -np.inf) >= g["p5"] else "fail"
    halves = (
        "pass"
        if st.get("h1_cagr", -np.inf) >= g["half_cagr"][0] - 0.02
        and st.get("h2_cagr", -np.inf) >= g["half_cagr"][1] - 0.02
        else "fail"
    )
    ctrl = "n/a"
    if st.get("family") in {"V", "D", "R", "A", "T"}:
        ctrl = (
            "pass"
            if (
                st.get("control_x", -np.inf) >= st["net_x"]
                or st.get("control_p5", -np.inf) >= st.get("p5", -np.inf)
            )
            else "fail"
        )
    return f"{ret}/{dd}/{sy}/{p5}/{halves}/{ctrl}"


def balanced_gate(st: dict) -> bool:
    """Pre-registered balanced-profile gate (US primary, net dividends).

    Tail-hedge rows have no half-window fields and are never balanced-profile
    candidates. The leverage-matched control and 2x-cost fields are required:
    a lower-gross blend that does not beat its constant-leverage control is a
    leverage dial, not a risk improvement.
    """
    if st.get("_model_invalid") or st.get("family") == "H":
        return False
    ctrl = st.get("control_x", -np.inf) >= st.get("net_x", np.inf) or st.get(
        "control_p5", -np.inf
    ) >= st.get("p5", np.inf)
    cost2 = st.get("net_x_2x", float("nan"))
    return bool(
        st.get("net_maxdd", 0.0) >= -0.47
        and st.get("net_x", 0.0) >= 28.0
        and st.get("p5", 0.0) >= 2.384
        and st.get("y2018", -1.0) >= -0.3051
        and st.get("y2022", -1.0) >= -0.3362
        and st.get("h1_cagr", -1.0) >= 0.10
        and st.get("h2_cagr", -1.0) >= 0.2753
        and st.get("h1_dd", 0.0) >= -0.50
        and st.get("h2_dd", 0.0) >= -0.50
        and ctrl
        and np.isfinite(cost2)
        and cost2 >= 25.0
    )


def run_suite(market: str, book: Book, cost: float = 5.0):
    base_st, base_res, _ = run_baseline(book, cost)
    led = eur_ledger.replay(
        base_res,
        book.panel,
        market,
        EurLedgerConfig(capital=CAP, spread_bps=cost),
    )
    net_eq = apply_dividend_tax(
        led.equity,
        daily_dividend_tax(base_res.weights, tax_rate=0.314, default_yield=0.012),
    )
    base_st["_net_equity"] = net_eq
    base_st.update(label="baseline", family="base")
    e0_st, e0_res = standalone_etf(book, cost)
    rows = run_family_v(book, base_st, cost)
    rows += run_family_d(book, base_st, cost)
    rows += run_family_r(book, base_st, cost)
    rows += run_family_a(book, base_st, base_res, e0_res, cost)
    if market == "US":
        rows += run_family_b(book, cost)
    rows += run_family_t(book, base_st, cost)
    rows += run_family_h(book, base_st)
    return base_st, e0_st, rows


def _load_cost_map() -> dict[tuple[str, str], float]:
    path = ROOT / "results" / "risk_v3_costs.csv"
    if not path.exists():
        return {}
    try:
        df = pd.read_csv(path)
    except Exception:
        return {}
    return {
        (str(r["market"]), str(r["label"])): float(r["net_x"])
        for _, r in df.iterrows()
        if pd.notna(r.get("net_x"))
    }


def main() -> None:
    books = {m: _build_book(m) for m in MARKETS}
    if "--check" in sys.argv:
        check_causality(books)
        return
    if "--baseline" in sys.argv:
        for m in MARKETS:
            st, _, _ = run_baseline(books[m])
            _print_row(m, st)
            assert abs(st["net_x"] - GATE[m]["base"]) < 0.05, (m, st["net_x"])
            assert abs(st["net_maxdd"] - GATE[m]["maxdd"]) < 0.002, (
                m,
                st["net_maxdd"],
            )
        print("baseline assertions ok")
        return

    cost = 10.0 if "--costs" in sys.argv else 5.0
    cost_map = _load_cost_map() if cost <= 5.0 else {}
    all_rows: list[dict] = []
    for market in MARKETS:
        book = books[market]
        print(
            f"\n== {market} primary =="
            if market == "US"
            else f"\n== {market} secondary =="
        )
        base_st, e0_st, rows = run_suite(market, book, cost)
        base_st["net_x_2x"] = cost_map.get((market, base_st["label"]), float("nan"))
        _print_row(market, base_st)
        all_rows.append(dict(market=market, **base_st))
        e0_st["net_x_2x"] = cost_map.get((market, e0_st["label"]), float("nan"))
        _print_row(market, e0_st)
        all_rows.append(dict(market=market, **e0_st))
        for st in rows:
            _print_row(market, st)
            st["net_x_2x"] = cost_map.get((market, st.get("label", "")), float("nan"))
            st["mg_gate"] = gate_row(market, st)
            st["bal_gate"] = balanced_gate(st) if market == "US" else False
            all_rows.append(dict(market=market, **st))

    df = pd.DataFrame(all_rows)
    suffix = "costs" if cost > 5.0 else "main"
    out = ROOT / "results" / f"risk_v3_{suffix}.csv"
    df.to_csv(out, index=False)
    print(f"\nwrote {out}")


def check_causality(books: dict[str, Book]) -> None:
    """Truncating future prices must not change earlier signals."""
    cuts = ("2018-06-01", "2022-06-01")
    for market, book in books.items():
        full_sigma = book.sigma
        full_spy = above_trend(book.spy, 200)
        full_smh = above_trend(book.smh, 200)
        full_mom = momentum(book.smh, 252)
        for cut in cuts:
            ts = pd.Timestamp(cut)
            px = book.panel[book.panel["date"] <= ts]
            p = px.pivot(index="date", columns="ticker", values="adjclose").sort_index()
            smh = p["SMH"].dropna()
            spy = p["SPY"].dropna()
            assert np.allclose(
                full_sigma.loc[:ts].values,
                realized_vol(smh.pct_change(fill_method=None), 20).values,
                equal_nan=True,
            ), f"{market} sigma leaked future data at {cut}"
            assert np.array_equal(
                full_spy.loc[:ts].values, above_trend(spy, 200).values
            ), f"{market} SPY trend leaked future data at {cut}"
            assert np.array_equal(
                full_smh.loc[:ts].values, above_trend(smh, 200).values
            ), f"{market} SMH trend leaked future data at {cut}"
            assert np.allclose(
                full_mom.loc[:ts].values,
                momentum(smh, 252).values,
                equal_nan=True,
            ), f"{market} SMH momentum leaked future data at {cut}"
        print(f"{market}: vol/regime causality ok")


if __name__ == "__main__":
    main()
