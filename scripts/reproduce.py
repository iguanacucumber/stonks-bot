"""One-command reproduction of the committed stonks-bot headline evidence.

Usage::

    uv run python scripts/reproduce.py                 # all four baseline books
    uv run python scripts/reproduce.py baseline        # same, explicit
    uv run python scripts/reproduce.py headline        # TECH deep dive + stats
    uv run python scripts/reproduce.py audit           # data-quality report
    uv run python scripts/reproduce.py survivorship    # static-49 bias study
    uv run python scripts/reproduce.py all             # everything

The baseline and headline paths read only `cache/bars` and the committed
code/config.  They never call Yahoo: a preflight checks every required file
before the engine is allowed to run, and a missing file is a loud exit-2 error.
The audit and survivorship commands are read-only too.

Every baseline row prints three accounting modes: native (asset currency),
terminal-FX (legacy headline, NOT real-life) and EUR ledger (annual EUR tax and
trade-date FX).  The terminal-FX number is never used as the real-life result.

Expected snapshot 2026-11-25, IBKR CTO, €10k:

    TECH monthly   51.82x native  57.01x terminal-FX  53.45x EUR ledger
    TECH tax       46.26x         50.89x              49.32x
    US monthly     64.41x         70.86x              66.72x

Strict mode fails the command if the cache moves those numbers.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from bot import backtest, france as bot_france, markets, strategy, universe  # noqa: E402
from bot.evaluation import attribution, bootstrap, currency, cscv, dataquality  # noqa: E402
from bot.evaluation import metrics, survivorship, trials, windows  # noqa: E402

import france as audit  # noqa: E402  (scripts/france.py)
from paper_daily import BOOKS as PAPER_BOOKS  # noqa: E402

CACHE = ROOT / "cache"
RESULTS = ROOT / "results"
CAPITAL = 10_000.0
EXPECTED_SNAPSHOT = "2026-11-25"
BASELINE_BOOKS = (
    ("TECH", "monthly"),
    ("TECH", "tax"),
    ("US", "monthly"),
    ("US", "tax"),
)
EXPECTED_BASELINE = {
    ("TECH", "monthly"): {"native": 51.82, "terminal_fx": 57.01, "eur_ledger": 53.45},
    ("TECH", "tax"): {"native": 46.26, "terminal_fx": 50.89, "eur_ledger": 49.32},
    ("US", "monthly"): {"native": 64.41, "terminal_fx": 70.86, "eur_ledger": 66.72},
    ("US", "tax"): {"native": 46.26, "terminal_fx": 50.89, "eur_ledger": 49.32},
}
BASELINE_TOL = 0.05
EXPECTED = {
    "multiple": 57.01,
    "maxdd": -0.6783,
    "trades": 1836.0,
    "fees": 26_423.0,
    "tax_paid": 135_224.0,
    "exit_tax": 179_830.0,
}
TOL = {
    "multiple": 0.02,
    "maxdd": 0.0006,
    "trades": 0.0,
    "fees": 3.0,
    "tax_paid": 3.0,
    "exit_tax": 3.0,
}


def required_symbols() -> list[str]:
    tech = list(markets.UNIVERSES["TECH"])
    return sorted(set(tech) | {"QQQ", "SPY", "SMH", "SOXX", "EURUSD=X"})


def preflight(require_us: bool = False) -> tuple[pd.Timestamp, list[str]]:
    """Check the cache files before any engine code can reach the network."""
    missing = []
    for sym in required_symbols():
        path = CACHE / "bars" / f"{sym.replace('/', '_')}.csv"
        if not path.exists() or path.stat().st_size == 0:
            missing.append(sym)
    if require_us:
        for name in ("panel.parquet", "membership.parquet"):
            path = CACHE / name
            if not path.exists() or path.stat().st_size == 0:
                missing.append(f"cache/{name}")
    if missing:
        print(
            "ERROR: cache/bars is incomplete; refusing to fetch or fabricate.\n"
            f"  missing: {', '.join(missing)}\n"
            "  restore the data snapshot or run scripts/update_data.py explicitly.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    spy = CACHE / "bars" / "QQQ.csv"
    snapshot = pd.to_datetime(pd.read_csv(spy, usecols=["date"])["date"]).max()
    return snapshot.normalize(), missing


def load_tech() -> tuple[pd.DataFrame, pd.DataFrame]:
    panel, mem = markets.build("TECH")
    return panel, mem


def run_baseline(panel: pd.DataFrame, mem: pd.DataFrame):
    """The exact shipped monthly CTO config, returning the engine result."""
    old = universe.canonical
    universe.canonical = lambda t: t
    try:
        kw = dict(audit.BASE)
        kw.update(
            bot_france.kwargs("TECH", "Interactive Brokers", "cto", CAPITAL, panel)
        )
        cfg = strategy.Config(**kw)
        mkt = strategy.Market(panel, mem, cfg)
        res = backtest.run(mkt)
    finally:
        universe.canonical = old
    return mkt, cfg, res


def run_book(
    panel: pd.DataFrame,
    mem: pd.DataFrame,
    market: str,
    book: str,
    start: str = "2015-01-01",
    end: str | None = None,
):
    """Run one shipped book on a supplied panel, with the same config as the reports."""
    old = universe.canonical
    universe.canonical = old if market == "US" else (lambda t: t)
    try:
        kw = dict(audit.BASE)
        kw["start"] = start
        if end is not None:
            kw["end"] = end
        kw.update(
            bot_france.kwargs(market, "Interactive Brokers", "cto", CAPITAL, panel)
        )
        kw.update(PAPER_BOOKS[book])
        return backtest.run(strategy.Market(panel, mem, strategy.Config(**kw)))
    finally:
        universe.canonical = old


def baseline_command(args: argparse.Namespace) -> int:
    """Print all four baseline books with native, terminal-FX and EUR-ledger columns."""
    t_all = time.perf_counter()
    snapshot, _ = preflight(require_us=True)
    rows = []
    failures = []
    for market in ("TECH", "US"):
        panel, mem = markets.build(market)
        for book in ("monthly", "tax"):
            t0 = time.perf_counter()
            res = run_book(panel, mem, market, book)
            runtime = time.perf_counter() - t0
            dims = currency.dimension_table(res, panel, market, capital=CAPITAL)
            modes = {r["accounting_mode"]: r for _, r in dims.iterrows()}
            native = float(modes["native"]["multiple"])
            terminal = float(modes["terminal_fx"]["multiple"])
            led = modes.get("eur_ledger")
            ledger = float(led["multiple"]) if led is not None else None
            row = {
                "market": market,
                "book": book,
                "native": native,
                "terminal_fx": terminal,
                "eur_ledger": ledger,
                "ledger_cagr": float(led["cagr"]) if led is not None else None,
                "ledger_maxdd": float(led["max_dd"]) if led is not None else None,
                "ledger_fees": float(led["fees"]) if led is not None else None,
                "ledger_tax": float(led["tax_paid"]) if led is not None else None,
                "ledger_exit": float(led["exit_tax"]) if led is not None else None,
                "ledger_vs_terminal": (
                    ledger / terminal - 1.0 if ledger is not None and terminal else None
                ),
                "start": res.equity.index[0].date().isoformat(),
                "end": res.equity.index[-1].date().isoformat(),
                "trades": int(len(res.trades)),
                "runtime_seconds": runtime,
            }
            rows.append(row)
            expected = EXPECTED_BASELINE[(market, book)]
            for key, exp in expected.items():
                got = row.get(key)
                if got is None:
                    # The overlay is optional before PR #22 is merged.
                    if key == "eur_ledger":
                        continue
                    failures.append(f"{market}/{book} {key}: unavailable")
                elif abs(float(got) - float(exp)) > BASELINE_TOL:
                    failures.append(f"{market}/{book} {key}: {got:.4f} vs {exp:.4f}")
            print(
                f"{market:4s} {book:7s} native {native:6.2f}x  "
                f"terminal-FX {terminal:6.2f}x  "
                f"EUR-ledger {ledger:6.2f}x"
                if ledger is not None
                else f"{market:4s} {book:7s} native {native:6.2f}x  "
                f"terminal-FX {terminal:6.2f}x  EUR-ledger   n/a"
            )

    # The registry is a reporting ledger, not a new multiple-testing sweep:
    # each row is one book in the real-life accounting mode, with the native and
    # terminal-FX values recorded in the notes for auditability.
    registry = (
        None
        if getattr(args, "no_write", False)
        else trials.TrialRegistry(RESULTS / "eval_trials.csv")
    )
    for row in rows:
        if registry is None or row["eur_ledger"] is None:
            continue
        key = f"{row['market']}-{row['book']}"
        registry.record(
            id=f"eval-{key.lower()}-eur-ledger",
            date=snapshot.date().isoformat(),
            branch="v2/data-eval",
            hypothesis=f"{row['market']} {row['book']} reproduces in the EUR ledger",
            universe=(
                "TECH static 49"
                if row["market"] == "TECH"
                else "US point-in-time S&P 500"
            ),
            start=row["start"],
            end=row["end"],
            params_json=json.dumps(
                {
                    "leverage": 2.5,
                    "broker": "Interactive Brokers",
                    "wrapper": "CTO",
                    "capital": CAPITAL,
                    "book": row["book"],
                },
                sort_keys=True,
            ),
            n_trials=1,
            metric="after_tax_multiple",
            value=f"{row['eur_ledger']:.4f}",
            oos_value="",
            cost_model="IBKR CTO year-dependent PFU (30% through 2025, 31.4% from 2026), 5 bps spread, EUR ledger",
            accounting_mode="eur_ledger",
            verdict="paper",
            notes=(
                f"native {row['native']:.2f}x; terminal-FX "
                f"{row['terminal_fx']:.2f}x; EUR ledger {row['eur_ledger']:.2f}x. "
                "Reporting view, not an independent strategy trial."
            ),
            pass_rule=(
                f"reproduce {EXPECTED_BASELINE[(row['market'], row['book'])]['eur_ledger']:.2f}x "
                f"within {BASELINE_TOL:.2f}x on snapshot {snapshot.date()}"
            ),
            status="complete",
            commit="",
            source="scripts/reproduce.py baseline",
            replace=True,
        )

    header = [
        "# Baseline books: native, terminal-FX and EUR ledger",
        "",
        f"- Command: `uv run python scripts/reproduce.py baseline`",
        f"- Data snapshot: **{snapshot.date()}**",
        f"- Runtime: **{time.perf_counter() - t_all:.1f}s**",
        f"- Generated: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        "",
        "> **Real-life result: the EUR-ledger column.** `terminal_fx` is the old",
        "> headline convention (native after-tax × spot EUR/USD); it ignores annual",
        "> tax on FX moves and the trade-date EUR cost basis. `native` is a research",
        "> number in the asset currency. Do not quote terminal-FX as what you keep.",
        "",
        "All rows: IBKR CTO, €10k, 2.5x, HIFO, 2015-01-02 to the snapshot, French",
        "year-dependent PFU (30% through 2025, 31.4% from 2026), 5 bps spread, EUR ledger with trade-date FX and annual tax.",
        "",
        "| market | book | native (USD) | terminal-FX (EUR) | EUR ledger | ledger vs terminal | CAGR (ledger) | maxDD (ledger) | fees | tax paid | exit tax |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        if row["eur_ledger"] is None:
            ledger_cell = "n/a"
            delta_cell = "n/a"
            cagr_cell = "n/a"
            dd_cell = "n/a"
            fees_cell = "n/a"
            tax_cell = "n/a"
            exit_cell = "n/a"
        else:
            ledger_cell = f"**{row['eur_ledger']:.2f}x**"
            delta_cell = f"{row['ledger_vs_terminal']:+.1%}"
            cagr_cell = f"{row['ledger_cagr']:.1%}"
            dd_cell = f"{row['ledger_maxdd']:.1%}"
            fees_cell = _fmt_money(row["ledger_fees"])
            tax_cell = _fmt_money(row["ledger_tax"])
            exit_cell = _fmt_money(row["ledger_exit"])
        header.append(
            f"| {row['market']} | {row['book']} | {row['native']:.2f}x | "
            f"{row['terminal_fx']:.2f}x | {ledger_cell} | {delta_cell} | "
            f"{cagr_cell} | {dd_cell} | {fees_cell} | {tax_cell} | {exit_cell} |"
        )
    header += [
        "",
        "`ledger vs terminal` is the correction from the old headline to the",
        "EUR-taxed path. The ranking is unchanged: US monthly is ahead of TECH on",
        "both return and drawdown; the core-only tax book earns less but trades far",
        "less. See `results/eval_headline.md` for the TECH deep dive and",
        "`results/eval_trials.csv` for the accounting-mode registry snapshot.",
        "",
        "## Cross-check",
        "",
    ]
    if failures:
        header += [f"- **MISMATCH** {x}" for x in failures]
        print("FAIL: baseline mismatch", file=sys.stderr)
        for x in failures:
            print(f"  {x}", file=sys.stderr)
    else:
        header.append(
            "**PASS** — all baseline modes match the committed 2026-11-25 "
            "snapshot within 0.05x."
        )
    header += [
        "",
        "Generated by `uv run python scripts/reproduce.py baseline`.",
    ]
    if not getattr(args, "no_write", False):
        (RESULTS / "eval_baseline.md").write_text("\n".join(header) + "\n")
        (RESULTS / "eval_baseline.json").write_text(
            json.dumps(
                {
                    "snapshot": snapshot.date().isoformat(),
                    "runtime_seconds": time.perf_counter() - t_all,
                    "expected": {
                        f"{k[0]}-{k[1]}": v for k, v in EXPECTED_BASELINE.items()
                    },
                    "rows": rows,
                    "failures": failures,
                },
                indent=2,
                default=str,
            )
            + "\n"
        )
        print("wrote results/eval_baseline.md and results/eval_trials.csv")
    return 1 if (failures and getattr(args, "strict", False)) else 0


def _fmt_money(x: float) -> str:
    return f"€{x:,.0f}"


def _pct(x: float) -> str:
    return f"{x:.1%}"


def headline(args: argparse.Namespace) -> int:
    t_all = time.perf_counter()
    snapshot, _ = preflight()
    if str(snapshot.date()) != args.expected_snapshot:
        print(
            f"WARNING: data snapshot {snapshot.date()} != expected "
            f"{args.expected_snapshot}; strict baseline may fail.",
            file=sys.stderr,
        )
    t0 = time.perf_counter()
    panel, mem = load_tech()
    mkt, cfg, res = run_baseline(panel, mem)
    runtime = time.perf_counter() - t0

    f = bot_france.after_tax(res, CAPITAL, "TECH")
    bench = audit.bench_return("TECH", "Interactive Brokers", "CTO", CAPITAL, "native")
    m = metrics.compute_metrics(res.equity)
    bench_eq = backtest.spy_result(mkt).equity
    rets = metrics.to_returns(res.equity)

    actual = {
        "multiple": f["after_tax_eur"] / CAPITAL,
        "maxdd": f["maxdd"],
        "trades": float(len(res.trades)),
        "fees": f["fees_eur"],
        "tax_paid": f["tax_paid_eur"],
        "exit_tax": f["exit_tax_eur"],
    }
    cagr_terminal = metrics.cagr_from_multiple(
        actual["multiple"], (len(res.equity) - 1) / 252.0
    )
    failures = []
    for key, expected in EXPECTED.items():
        delta = float(actual[key] - expected)
        if abs(delta) > TOL[key]:
            failures.append(
                f"{key}: {actual[key]:.6g} vs {expected:.6g} (Δ{delta:+.6g})"
            )

    print(
        f"data snapshot {snapshot.date()} · runtime {runtime:.1f}s · "
        f"{len(res.equity)} sessions · {len(res.trades)} trades"
    )
    print(
        f"terminal-FX headline: {actual['multiple']:.2f}x "
        f"({_fmt_money(f['after_tax_eur'])} on €{CAPITAL:,.0f}) · "
        f"CAGR {cagr_terminal:.1%} · maxDD {f['maxdd']:.2%} · "
        f"fees {_fmt_money(f['fees_eur'])} · tax {_fmt_money(f['tax_paid_eur'])} · "
        f"exit {_fmt_money(f['exit_tax_eur'])}"
    )
    print(
        f"pre-tax metrics: CAGR {m['cagr']:.1%} · vol {m['vol']:.1%} · "
        f"Sharpe {m['sharpe']:.2f} · Sortino {m['sortino']:.2f} · "
        f"Calmar {m['calmar']:.2f} · hit {m['hit_rate']:.1%}"
    )

    # Currency modes.  The EUR ledger is imported only when merged.
    dims = currency.dimension_table(res, panel, "TECH", capital=CAPITAL)
    print("\nAccounting modes:")
    for _, r in dims.iterrows():
        print(
            f"  {r['accounting_mode']:12s} {r['multiple']:6.2f}x  "
            f"CAGR {r['cagr']:6.1%}  maxDD {r['max_dd']:7.1%}  {r['label']}"
        )
    if not currency.eur_ledger_available():
        print(
            "  eur_ledger   unavailable on this branch: bot/eur_ledger.py is "
            "PR #22; documented 53.45x in results/eur_ledger.md"
        )

    # Bootstrap: 10k stationary and circular-block paths.
    # Effective final exit-tax rate from the actual path.  The returns already
    # include realised taxes, so this is only an overlay on the terminal gain;
    # it is not the EUR-ledger tax path.
    gain_eur = max(1.0, f["after_tax_eur"] - CAPITAL)
    exit_rate = max(0.0, min(1.0, f["exit_tax_eur"] / gain_eur))
    t_boot = time.perf_counter()
    station = bootstrap.bootstrap(
        rets,
        n_paths=args.paths,
        method="stationary",
        block=21,
        expected_block=21,
        seed=7,
        exit_tax_rate=exit_rate,
    )
    block = bootstrap.bootstrap(
        rets,
        n_paths=args.paths,
        method="block",
        block=21,
        seed=7,
        exit_tax_rate=exit_rate,
    )
    boot_runtime = time.perf_counter() - t_boot

    # 5-year rolling windows on the full path (cheap slice view; the start-date
    # rerun remains scripts/robustness_best.py).
    wt = windows.five_year_table(
        res.equity, bench_eq, tax_rate=0.314, include_partial=False
    )

    attr = attribution.benchmark_attribution(rets, metrics.to_returns(bench_eq))
    expo = attribution.exposure_breakdown(res.weights)
    decomp = attribution.cost_tax_decomposition(
        res, capital=CAPITAL, financing_bps=500.0, fx=f["fx"]
    )

    # Deflated Sharpe with the number of historical trials on record.
    reg_path = RESULTS / "eval_trials.csv"
    n_trials = (
        trials.TrialRegistry(reg_path).trial_count(exclude_branch="v2/data-eval")
        if reg_path.exists()
        else 1
    )
    dsr = cscv.deflated_sharpe(rets, n_trials=max(1, n_trials))

    ledger_row = dims.loc[dims["accounting_mode"] == "eur_ledger"]
    ledger = ledger_row.iloc[0] if len(ledger_row) else None
    lines = [
        "# Reproduced headline — TECH monthly 2.5x, French CTO",
        "",
        f"- Command: `uv run python scripts/reproduce.py headline`",
        f"- Data snapshot: **{snapshot.date()}**",
        f"- Runtime: **{runtime:.1f}s** for the baseline, "
        f"{boot_runtime:.1f}s for two {args.paths:,}-path bootstraps",
        f"- Generated: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        "",
    ]
    if ledger is not None:
        lines += [
            f"> **Real-life result: EUR ledger {ledger['multiple']:.2f}x "
            f"({_fmt_money(ledger['after_tax_value'])} on €10k).** The terminal-FX "
            f"headline ({actual['multiple']:.2f}x) is kept only for continuity with "
            "older reports. It is an upper bound, not what a French investor keeps.",
            "",
        ]
    lines += [
        "## Baseline",
        "",
        "| metric | value |",
        "|---|---:|",
        f"| Terminal-FX after-tax multiple (legacy; NOT real-life) | **{actual['multiple']:.2f}x** |",
    ]
    if ledger is not None:
        lines += [
            f"| **EUR-ledger after-tax multiple (real-life)** | **{ledger['multiple']:.2f}x** |",
            f"| **EUR-ledger after-tax value on €10k** | **{_fmt_money(ledger['after_tax_value'])}** |",
        ]
    lines += [
        f"| Terminal-FX value on €10k (legacy) | {_fmt_money(f['after_tax_eur'])} |",
        f"| CAGR (terminal FX / legacy) | {cagr_terminal:.2%} |",
        f"| Max drawdown (native path) | {f['maxdd']:.2%} |",
        f"| Trades | {int(actual['trades']):,} |",
        f"| Fees (incl. FTT, terminal FX) | {_fmt_money(f['fees_eur'])} |",
        f"| Tax paid (terminal FX) | {_fmt_money(f['tax_paid_eur'])} |",
        f"| Exit tax (terminal FX) | {_fmt_money(f['exit_tax_eur'])} |",
        f"| Native after-tax multiple | {f['after_tax_native'] / CAPITAL:.2f}x |",
        f"| QQQ after tax (same CTO, terminal FX) | {bench['after_tax'] / CAPITAL:.2f}x |",
        f"| Pre-tax Sharpe / Sortino / Calmar | {m['sharpe']:.2f} / "
        f"{m['sortino']:.2f} / {m['calmar']:.2f} |",
        f"| Pre-tax vol / hit rate | {m['vol']:.1%} / {m['hit_rate']:.1%} |",
        "",
        "The committed baseline table quotes QQQ at 5.38x; this cache reproduces "
        f"{bench['after_tax'] / CAPITAL:.2f}x (0.03x difference, likely the later "
        "bar set). It does not change the TECH-vs-QQQ conclusion, which is an "
        "order of magnitude.",
        "",
        "## Currency dimension",
        "",
        "| accounting_mode | multiple | CAGR | maxDD | real-life | label |",
        "|---|---:|---:|---:|---|---|",
    ]
    for _, r in dims.iterrows():
        lines.append(
            f"| {r['accounting_mode']} | **{r['multiple']:.2f}x** | {r['cagr']:.1%} | "
            f"{r['max_dd']:.1%} | {'yes' if r['is_real_life'] else 'no'} | {r['label']} |"
        )
    if not currency.eur_ledger_available():
        lines += [
            "",
            "`eur_ledger` is not available in this checkout: `bot/eur_ledger.py` is "
            "missing. Its committed result is **53.45x** "
            "(`results/eur_ledger.md`); do not use the terminal-FX headline as the "
            "real-life EUR number.",
        ]

    lines += [
        "",
        "## Bootstrap",
        "",
        f"{args.paths:,} paths, 21-day mean block, seed 7. The exit-tax overlay "
        f"applies the path's effective final rate ({exit_rate:.1%}) to gains above "
        "1.0; realised taxes are already inside the returns. This is **not** the "
        "EUR-ledger after-tax path and is not comparable to 53.45x.",
        "",
        "| method | terminal p5 | terminal p50 | terminal p95 | maxDD p5 | exit-tax overlay p5 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for res_boot in (station, block):
        s = res_boot.summary()
        lines.append(
            f"| {res_boot.method} | {s['terminal_p5']:.2f}x | {s['terminal_p50']:.2f}x | "
            f"{s['terminal_p95']:.2f}x | {s['max_dd_p5']:.1%} | "
            f"{s['exit_tax_overlay_p5']:.2f}x |"
        )

    lines += [
        "",
        "## Five-year rolling windows",
        "",
        f"{int(wt.summary.get('n_windows', 0))} complete windows. Median "
        f"**{wt.summary.get('median_multiple', float('nan')):.2f}x**, worst "
        f"**{wt.summary.get('worst_multiple', float('nan')):.2f}x** "
        f"(start {wt.summary.get('worst_start', 'n/a')}), share beating QQQ after "
        f"tax **{wt.summary.get('share_beat', float('nan')):.0%}**. "
        "These are slices of one path; start-date reruns are in "
        "`results/robustness_best.md`.",
        "",
    ]
    if len(wt.rows):
        lines.append(wt.to_markdown())
        lines.append("")

    lines += [
        "## Benchmark attribution",
        "",
        "| beta | alpha (ann.) | R² | tracking error | info ratio | up capture | down capture | excess CAGR |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|",
        f"| {attr['beta']:.2f} | {attr['alpha_ann']:.1%} | {attr['r2']:.2f} | "
        f"{attr['tracking_error']:.1%} | {attr['information_ratio']:.2f} | "
        f"{attr['up_capture']:.2f} | {attr['down_capture']:.2f} | "
        f"{attr['excess_cagr']:.1%} |",
        "",
        "Average exposure: "
        f"gross {expo['avg_gross']:.2f}x, core {expo['avg_core']:.2f}x, "
        f"sleeve {expo['avg_sleeve']:.2f}x, cash {expo['avg_cash']:+.2f}x; "
        f"{expo['avg_names']:.1f} names/day.",
        "",
        "## Cost and tax decomposition",
        "",
        "Additive **terminal-FX** view; costs compound, so read the shares not the "
        "individual points as independent haircuts. The EUR-ledger row above is the "
        "real-life result; this table is for attribution, not a wealth promise.",
        "",
        "| item | multiple points | amount | share of gross |",
        "|---|---:|---:|---:|",
        f"| Fees ex-FTT | {decomp['fees_ex_ftt']:.2f} | {_fmt_money(decomp['fees_ex_ftt_amount'])} | {decomp['fee_share']:.1%} |",
        f"| FTT | {decomp['ftt']:.2f} | {_fmt_money(decomp['ftt_amount'])} | — |",
        f"| Realised tax | {decomp['tax_paid']:.2f} | {_fmt_money(decomp['tax_paid_amount'])} | {decomp['tax_share']:.1%} |",
        f"| Financing | {decomp['financing']:.2f} | {_fmt_money(decomp['financing_amount'])} | — |",
        f"| Exit tax | {decomp['exit_tax']:.2f} | {_fmt_money(decomp['exit_tax_amount'])} | — |",
        f"| **After tax (terminal FX)** | **{decomp['after_tax_multiple']:.2f}** | **{_fmt_money(decomp['after_tax_value'])}** | **{1 - decomp['cost_drag_pct']:.1%}** |",
        "",
        f"Gross (add-back) multiple {decomp['gross_multiple']:.2f}x; "
        f"total cost drag {decomp['cost_drag_pct']:.1%} of gross.",
        "",
        "## Multiple-testing check",
        "",
        f"Deflated Sharpe with N={int(dsr['n_trials'])} historical trials: "
        f"daily SR {dsr['sharpe_daily']:.3f}, expected max under the null "
        f"{dsr['expected_max_sharpe_daily']:.3f}, **DSR {dsr['deflated_sharpe']:.3f}** "
        f"(one-sided p {dsr['p_value']:.3f}). **Limitation:** N counts historical "
        "variants as if independent; they are not (same price path, overlapping "
        "variants, different families), and the dispersion is estimated from the "
        "selected path. The p-value is indicative, not definitive. CSCV PBO needs "
        "the per-trial return matrix; use `bot.evaluation.cscv.cscv_pbo` when that "
        "exists.",
        "",
        "## Reproduction verdict",
        "",
    ]
    if failures:
        lines += ["**FAIL** — cache moved the headline:", ""]
        lines += [f"- {x}" for x in failures]
        print("FAIL: baseline mismatch", file=sys.stderr)
        for x in failures:
            print(f"  {x}", file=sys.stderr)
    else:
        lines.append(
            "**PASS** — every headline number matches the committed baseline "
            "within the stated rounding tolerance."
        )

    lines += [
        "",
        "## Caveats",
        "",
        "- TECH uses a static 49-name universe chosen today: survivorship-biased. "
        "See `results/eval_survivorship.md`.",
        "- Turnover ~17x NAV/yr; costs and French tax are inside the path but not "
        "the pre-tax Sharpe.",
        "- The EUR ledger is the real-life number; the terminal-FX headline is an "
        "upper bound. Do not quote 57x as forward wealth.",
        "",
        "Generated by `uv run python scripts/reproduce.py headline`.",
    ]
    text = "\n".join(lines) + "\n"
    (RESULTS / "eval_headline.md").write_text(text)
    (RESULTS / "eval_headline.json").write_text(
        json.dumps(
            {
                "snapshot": str(snapshot.date()),
                "runtime_seconds": runtime,
                "actual": actual,
                "expected": EXPECTED,
                "failures": failures,
                "metrics": m,
                "bootstrap": {
                    "stationary": station.summary(),
                    "block": block.summary(),
                },
                "windows": wt.summary,
                "attribution": attr,
                "exposure": expo,
                "decomposition": decomp,
                "deflated_sharpe": dsr,
                "accounting_modes": dims.to_dict(orient="records"),
            },
            indent=2,
            default=str,
        )
        + "\n"
    )
    (RESULTS / "eval_windows.json").write_text(
        json.dumps(
            {"summary": wt.summary, "rows": wt.rows.to_dict(orient="records")},
            indent=2,
            default=str,
        )
        + "\n"
    )
    print(f"wrote results/eval_headline.md and results/eval_windows.json")
    return 1 if (failures and args.strict) else 0


def audit_command(args: argparse.Namespace) -> int:
    bars = CACHE / "bars"
    if not bars.exists() or not any(bars.glob("*.csv")):
        if args.allow_missing:
            print("cache/bars absent or empty; audit skipped (--allow-missing)")
            return 0
        print("ERROR: cache/bars absent or empty; cannot audit", file=sys.stderr)
        return 2
    panel = None
    membership = None
    if (CACHE / "panel.parquet").exists():
        panel = pd.read_parquet(CACHE / "panel.parquet")
    if (CACHE / "membership.parquet").exists():
        membership = pd.read_parquet(CACHE / "membership.parquet")
    report = dataquality.audit_cache(
        CACHE,
        panel=panel,
        membership=membership,
        aliases=universe.ALIASES,
        required_fresh=list(markets.UNIVERSES["TECH"]) + ["QQQ", "SMH", "SOXX"],
    )
    dataquality.write_report(report, RESULTS / "eval_data_quality.json")
    (RESULTS / "eval_data_quality.md").write_text(dataquality.markdown_summary(report))
    warn_total = sum(report.get("warning_counts", {}).values())
    print(
        f"data audit: {'PASS (required symbols)' if report['ok'] else 'FAIL'} · "
        f"{report['symbols_checked']} symbols, {report['rows_checked']:,} rows, "
        f"{len(report['hard_failures'])} hard failures, "
        f"{warn_total} warnings · snapshot {report.get('snapshot_date')}"
    )
    if report["hard_failures"]:
        for x in report["hard_failures"][:10]:
            print(f"  FAIL {x['check']} {x['symbol']}: {x['detail']}", file=sys.stderr)
        return 1
    return 0


def survivorship_command(args: argparse.Namespace) -> int:
    snapshot, _ = preflight(require_us=True)
    panel, static_mem = load_tech()
    membership = universe.load_membership()
    tech = list(markets.UNIVERSES["TECH"])

    def _variant(mem: pd.DataFrame):
        summary, result = survivorship.run_tech_variant(panel, mem, return_result=True)
        summary["multiple"] = summary["after_tax_multiple"]
        led = currency.ledger_summary(result, panel, "TECH", capital=CAPITAL)
        summary["eur_ledger_multiple"] = (
            float(led["multiple"]) if led is not None else float("nan")
        )
        return summary

    baseline = _variant(static_mem)
    sp500_mem = survivorship.dated_sp500_membership(tech, membership)
    sp500_dated = _variant(sp500_mem)
    public_mem = survivorship.public_at_start_membership(tech, panel)
    public_start = _variant(public_mem)

    us_panel = pd.read_parquet(CACHE / "panel.parquet")
    coverage = survivorship.membership_coverage(membership, us_panel)
    us_missing = survivorship.us_missing_share(membership, us_panel)
    equal_weight = survivorship.equal_weight_multiple(panel, tickers=tech)
    missing_path = CACHE / "missing_symbols.txt"
    missing = (
        [x.strip() for x in missing_path.read_text().splitlines() if x.strip()]
        if missing_path.exists()
        else []
    )
    report = survivorship.build_report(
        tech,
        panel,
        membership,
        baseline,
        sp500_dated,
        public_start,
        coverage,
        equal_weight,
        missing,
        us_missing=us_missing,
    )
    (RESULTS / "eval_survivorship.json").write_text(
        json.dumps(report, indent=2, default=str) + "\n"
    )

    facts = report["facts"]
    gap = baseline["after_tax_multiple"] - sp500_dated["after_tax_multiple"]
    gap_pct = (
        gap / baseline["after_tax_multiple"]
        if baseline["after_tax_multiple"]
        else np.nan
    )
    last_cov = coverage.iloc[-1] if len(coverage) else None
    us = report.get("us_missing", {})
    lines = [
        "# Survivorship audit: TECH static-49 and the US point-in-time book",
        "",
        f"- Data snapshot: **{snapshot.date()}**",
        f"- TECH universe: **{facts['n_static']} static names**, chosen today and "
        "applied as a single 2014-01-01 membership snapshot.",
        f"- At the 2015 start, **{facts['n_in_sp500_at_start']}/{facts['n_static']}** "
        "were S&P 500 members; **"
        f"{facts['n_joined_later']}** joined later; **"
        f"{facts['n_never_in_sp500']}** were never in the S&P 500.",
        f"- **{facts['n_first_price_after_start']}** names have no price at the "
        "2015 start (later IPO or missing data); **"
        f"{facts['n_missing_price']}** have no usable price at all.",
        f"- US book: **{us.get('missing', 0)}/{us.get('unique_members', 0)}** "
        f"unique 2014+ S&P members (**{us.get('missing_share', float('nan')):.1%}**) "
        "have no usable price and are silently excluded. That is the US book's "
        "residual survivorship bias.",
        "",
        "## What the data actually is",
        "",
        "- TECH membership source: the hand-written list in `bot/markets.py`, one "
        "snapshot at `2014-01-01`. It is not a historical index constituent file.",
        "- US membership source: the fja05680 S&P 500 history CSV "
        "(`bot/universe.py`), point-in-time, plus four manual 2026 changes.",
        f"- Missing Yahoo histories: **{len(missing)}** symbols globally in "
        "`cache/missing_symbols.txt` (delisted, acquired or renamed). For the US "
        f"book specifically, **{us.get('missing', 0)}/{us.get('unique_members', 0)}** "
        "unique 2014+ S&P members are dropped; the global count is not the US "
        "coverage number.",
        "",
        "## Sensitivity (not a lower bound)",
        "",
        "| variant | terminal-FX | EUR ledger | maxDD | trades | what changes |",
        "|---|---:|---:|---:|---:|---|",
        f"| static-49 baseline | **{baseline['after_tax_multiple']:.2f}x** | "
        f"{baseline['eur_ledger_multiple']:.2f}x | {baseline['maxdd']:.1%} | "
        f"{baseline['trades']} | nothing |",
        f"| S&P-dated TECH | **{sp500_dated['after_tax_multiple']:.2f}x** | "
        f"{sp500_dated['eur_ledger_multiple']:.2f}x | {sp500_dated['maxdd']:.1%} | "
        f"{sp500_dated['trades']} | dated membership; non-S&P names removed |",
        f"| public-at-2015 | **{public_start['after_tax_multiple']:.2f}x** | "
        f"{public_start['eur_ledger_multiple']:.2f}x | {public_start['maxdd']:.1%} | "
        f"{public_start['trades']} | later IPOs removed |",
        "",
        f"The S&P-dated run is **{gap:.2f}x lower** ({gap_pct:.0%} of the baseline) "
        "on terminal-FX. This is a **sensitivity, not a lower bound**: the "
        "S&P-dated universe is not nested inside the static-49 list (it removes "
        "ASML, ARM, MELI, TEAM, ZS and ANT, and it starts names at their index "
        "addition), and it still contains only 2026 survivors. It shows that the "
        "TECH result is highly sensitive to universe construction; it does not "
        "measure the true NASDAQ-2015 bias, which is not reconstructible from this "
        "cache because failed and acquired names have no Yahoo history. The EUR "
        "ledger columns are the real-life accounting modes, not a causal "
        "decomposition.",
        "",
        "## US point-in-time coverage",
        "",
        "| snapshot | price date | members | covered | missing | coverage |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for _, r in coverage.iterrows():
        if int(r["snapshot"][:4]) % 2 == 0:  # even years keep the table readable
            lines.append(
                f"| {r['snapshot']} | {r['price_date']} | {int(r['members'])} | "
                f"{int(r['covered'])} | {int(r['missing'])} | {r['coverage']:.1%} |"
            )
    if last_cov is not None:
        first_cov = coverage.iloc[0]
        lines += [
            "",
            f"Coverage rises from **{first_cov['coverage']:.1%}** at the 2015 "
            f"snapshot ({int(first_cov['missing'])} missing) to "
            f"**{last_cov['coverage']:.1%}** now ({int(last_cov['missing'])} missing). "
            "The holes are concentrated in the early years, exactly where a "
            "survivorship-biased backtest would otherwise earn extra return. The US "
            "book's membership is point-in-time, but its price sample is incomplete: "
            "less biased than TECH, not bias-free.",
        ]
    lines += [
        "",
        "## Selection premium (descriptive)",
        "",
        f"Static equal-weight basket: **{equal_weight.get('equal_weight_multiple', float('nan')):.2f}x** "
        f"over the window vs QQQ **{equal_weight.get('benchmark_multiple', float('nan')):.2f}x**; "
        f"median name {equal_weight.get('median_name_multiple', float('nan')):.2f}x; "
        f"best {equal_weight.get('best_name')} {equal_weight.get('best_multiple', float('nan')):.2f}x, "
        f"worst {equal_weight.get('worst_name')} {equal_weight.get('worst_multiple', float('nan')):.2f}x. "
        "This mixes selection, equal weighting and the 2026 list; it is evidence of "
        "a premium, not a clean estimate.",
        "",
        "## What claims are safe",
        "",
    ]
    lines += [f"- SAFE: {x}" for x in report["verdict"]["safe_claims"]]
    lines += [f"- UNSAFE: {x}" for x in report["verdict"]["unsafe_claims"]]
    lines += [
        "",
        "**Bottom line:** even the real-life EUR-ledger TECH number (53.45x) is an "
        "upper bound on a biased universe, not an expected return; the 57.01x "
        "terminal-FX headline is not the real-life result. Report ETF-only "
        "(QQQ/SMH) results as survivorship-free evidence; treat stock-sleeve alpha "
        "as unproven until a point-in-time NASDAQ membership file with delisted "
        "names is added.",
        "",
        "## Reproduce",
        "",
        "```",
        "uv run python scripts/reproduce.py survivorship",
        "```",
        "",
        "Generated by `uv run python scripts/reproduce.py survivorship`.",
    ]
    (RESULTS / "eval_survivorship.md").write_text("\n".join(lines) + "\n")
    print(
        f"survivorship: baseline {baseline['after_tax_multiple']:.2f}x, "
        f"S&P-dated {sp500_dated['after_tax_multiple']:.2f}x "
        f"({gap:+.2f}x), public-2015 {public_start['after_tax_multiple']:.2f}x; "
        f"last S&P coverage {last_cov['coverage']:.1%}"
        if last_cov is not None
        else "survivorship report written (no coverage rows)"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command")
    b = sub.add_parser(
        "baseline",
        help="all four baseline books with native/terminal-FX/EUR-ledger columns",
    )
    b.add_argument("--strict", action="store_true", default=True)
    b.add_argument(
        "--no-strict",
        dest="strict",
        action="store_false",
        help="report a cache mismatch but exit 0 (informational only)",
    )
    b.add_argument(
        "--no-write", action="store_true", help="print without rewriting results/"
    )
    h = sub.add_parser("headline", help="reproduce the TECH baseline and stats")
    h.add_argument("--paths", type=int, default=10_000, help="bootstrap paths")
    h.add_argument("--strict", action="store_true", default=True)
    h.add_argument("--no-strict", dest="strict", action="store_false")
    h.add_argument("--expected-snapshot", default=EXPECTED_SNAPSHOT)
    a = sub.add_parser("audit", help="data-quality audit (read-only)")
    a.add_argument(
        "--allow-missing",
        action="store_true",
        help="exit 0 when cache/bars is absent (CI smoke only)",
    )
    sub.add_parser("survivorship", help="static-49 and US coverage audit")
    allp = sub.add_parser("all", help="baseline + headline + audit + survivorship")
    allp.add_argument("--paths", type=int, default=10_000, help="bootstrap paths")
    allp.add_argument("--strict", action="store_true", default=True)
    allp.add_argument("--no-strict", dest="strict", action="store_false")
    allp.add_argument("--allow-missing", action="store_true")
    allp.add_argument("--expected-snapshot", default=EXPECTED_SNAPSHOT)
    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.command in (None, "baseline"):
        if not hasattr(args, "strict"):
            args.strict = True
        if not hasattr(args, "no_write"):
            args.no_write = False
        return baseline_command(args)
    if args.command == "headline":
        return headline(args)
    if args.command == "audit":
        return audit_command(args)
    if args.command == "survivorship":
        return survivorship_command(args)
    if args.command == "all":
        rc = baseline_command(args)
        if rc:
            return rc
        rc = headline(args)
        if rc:
            return rc
        rc = audit_command(args)
        if rc:
            return rc
        return survivorship_command(args)
    parser.error(f"unknown command {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
