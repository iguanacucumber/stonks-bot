"""One-command reproduction of the committed stonks-bot headline evidence.

Usage::

    uv run python scripts/reproduce.py                 # TECH baseline + stats
    uv run python scripts/reproduce.py audit           # data-quality report
    uv run python scripts/reproduce.py survivorship    # static-49 bias study

The headline path reads only `cache/bars` and the committed code/config.  It
never calls Yahoo: a preflight checks every required file before the engine is
allowed to run, and a missing file is a loud exit-2 error.  The audit and
survivorship commands are read-only too.

Expected baseline (snapshot 2026-11-25): TECH monthly 2.5x at Interactive
Brokers, €10k, French CTO: 57.01x after terminal-FX tax, maxDD -67.83%, 1,836
trades, fees €26,423, tax paid €135,224, exit tax €179,830.  Strict mode fails
the command if the cache moves those numbers.
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

CACHE = ROOT / "cache"
RESULTS = ROOT / "results"
CAPITAL = 10_000.0
EXPECTED_SNAPSHOT = "2026-11-25"
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
    return sorted(set(tech) | {"QQQ", "SMH", "SOXX", "EURUSD=X"})


def preflight() -> tuple[pd.Timestamp, list[str]]:
    """Check the bar files before any engine code can reach the network."""
    missing = []
    for sym in required_symbols():
        path = CACHE / "bars" / f"{sym.replace('/', '_')}.csv"
        if not path.exists() or path.stat().st_size == 0:
            missing.append(sym)
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
        f"CAGR {f['cagr_eur']:.1%} · maxDD {f['maxdd']:.2%} · "
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
            "PR #22; documented 52.89x in results/eur_ledger.md"
        )

    # Bootstrap: 10k stationary and circular-block paths.
    t_boot = time.perf_counter()
    station = bootstrap.bootstrap(
        rets,
        n_paths=args.paths,
        method="stationary",
        block=21,
        expected_block=21,
        seed=7,
        tax_rate=0.314,
    )
    block = bootstrap.bootstrap(
        rets,
        n_paths=args.paths,
        method="block",
        block=21,
        seed=7,
        tax_rate=0.314,
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
    n_trials = trials.TrialRegistry(reg_path).trial_count() if reg_path.exists() else 1
    dsr = cscv.deflated_sharpe(rets, n_trials=max(1, n_trials))

    lines = [
        "# Reproduced headline — TECH monthly 2.5x, French CTO",
        "",
        f"- Command: `uv run python scripts/reproduce.py`",
        f"- Data snapshot: **{snapshot.date()}**",
        f"- Runtime: **{runtime:.1f}s** for the baseline, "
        f"{boot_runtime:.1f}s for two {args.paths:,}-path bootstraps",
        f"- Generated: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        "",
        "## Baseline",
        "",
        "| metric | value |",
        "|---|---:|",
        f"| Terminal-FX after-tax multiple | **{actual['multiple']:.2f}x** |",
        f"| After-tax value on €10k | {_fmt_money(f['after_tax_eur'])} |",
        f"| CAGR (EUR) | {f['cagr_eur']:.2%} |",
        f"| Max drawdown | {f['maxdd']:.2%} |",
        f"| Trades | {int(actual['trades']):,} |",
        f"| Fees (incl. FTT) | {_fmt_money(f['fees_eur'])} |",
        f"| Tax paid | {_fmt_money(f['tax_paid_eur'])} |",
        f"| Exit tax | {_fmt_money(f['exit_tax_eur'])} |",
        f"| Native after-tax multiple | {f['after_tax_native'] / CAPITAL:.2f}x |",
        f"| QQQ after tax (same CTO, terminal FX) | {bench['after_tax'] / CAPITAL:.2f}x |",
        f"| Pre-tax Sharpe / Sortino / Calmar | {m['sharpe']:.2f} / "
        f"{m['sortino']:.2f} / {m['calmar']:.2f} |",
        f"| Pre-tax vol / hit rate | {m['vol']:.1%} / {m['hit_rate']:.1%} |",
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
            "`eur_ledger` is not computed on this branch: `bot/eur_ledger.py` lands "
            "with PR #22. Its committed result is **52.89x** "
            "(`results/eur_ledger.md`); do not use the terminal-FX headline as the "
            "real-life EUR number.",
        ]

    lines += [
        "",
        "## Bootstrap",
        "",
        f"{args.paths:,} paths, 21-day mean block, seed 7. After-tax paths tax only "
        "gains above 1.0 at 31.4% (losses are not refunded).",
        "",
        "| method | terminal p5 | terminal p50 | terminal p95 | maxDD p5 | after-tax p5 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for res_boot in (station, block):
        s = res_boot.summary()
        lines.append(
            f"| {res_boot.method} | {s['terminal_p5']:.2f}x | {s['terminal_p50']:.2f}x | "
            f"{s['terminal_p95']:.2f}x | {s['max_dd_p5']:.1%} | "
            f"{s['after_tax_p5']:.2f}x |"
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
        "Additive terminal view; costs compound, so read the shares not the "
        "individual points as independent haircuts.",
        "",
        "| item | multiple points | amount | share of gross |",
        "|---|---:|---:|---:|",
        f"| Fees ex-FTT | {decomp['fees_ex_ftt']:.2f} | {_fmt_money(decomp['fees_ex_ftt_amount'])} | {decomp['fee_share']:.1%} |",
        f"| FTT | {decomp['ftt']:.2f} | {_fmt_money(decomp['ftt_amount'])} | — |",
        f"| Realised tax | {decomp['tax_paid']:.2f} | {_fmt_money(decomp['tax_paid_amount'])} | {decomp['tax_share']:.1%} |",
        f"| Financing | {decomp['financing']:.2f} | {_fmt_money(decomp['financing_amount'])} | — |",
        f"| Exit tax | {decomp['exit_tax']:.2f} | {_fmt_money(decomp['exit_tax_amount'])} | — |",
        f"| **After tax** | **{decomp['after_tax_multiple']:.2f}** | **{_fmt_money(decomp['after_tax_value'])}** | **{1 - decomp['cost_drag_pct']:.1%}** |",
        "",
        f"Gross (add-back) multiple {decomp['gross_multiple']:.2f}x; "
        f"total cost drag {decomp['cost_drag_pct']:.1%} of gross.",
        "",
        "## Multiple-testing check",
        "",
        f"Deflated Sharpe with N={int(dsr['n_trials'])} historical trials: "
        f"daily SR {dsr['sharpe_daily']:.3f}, expected max under the null "
        f"{dsr['expected_max_sharpe_daily']:.3f}, **DSR {dsr['deflated_sharpe']:.3f}** "
        f"(one-sided p {dsr['p_value']:.3f}). CSCV PBO needs the per-trial return "
        "matrix; use `bot.evaluation.cscv.cscv_pbo` when that exists.",
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
        "Generated by `uv run python scripts/reproduce.py`.",
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
    if not bars.exists():
        if args.allow_missing:
            print("cache/bars absent; audit skipped (--allow-missing)")
            return 0
        print("ERROR: cache/bars absent; cannot audit", file=sys.stderr)
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
        f"data audit: {'PASS' if report['ok'] else 'FAIL'} · "
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
    snapshot, _ = preflight()
    panel, static_mem = load_tech()
    membership = universe.load_membership()
    tech = list(markets.UNIVERSES["TECH"])

    baseline = survivorship.run_tech_variant(panel, static_mem)
    baseline["multiple"] = baseline["after_tax_multiple"]

    sp500_mem = survivorship.dated_sp500_membership(tech, membership)
    sp500_dated = survivorship.run_tech_variant(panel, sp500_mem)
    public_mem = survivorship.public_at_start_membership(tech, panel)
    public_start = survivorship.run_tech_variant(panel, public_mem)

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
        f"- Missing Yahoo histories: **{len(missing)}** symbols in "
        "`cache/missing_symbols.txt` (delisted, acquired or renamed); the US panel "
        "drops them. That is residual survivorship bias even in the US book.",
        "",
        "## Bias estimate",
        "",
        "| variant | after-tax multiple | maxDD | trades | what it removes |",
        "|---|---:|---:|---:|---|---|",
        f"| static-49 baseline | **{baseline['after_tax_multiple']:.2f}x** | "
        f"{baseline['maxdd']:.1%} | {baseline['trades']} | nothing |",
        f"| S&P-dated TECH | **{sp500_dated['after_tax_multiple']:.2f}x** | "
        f"{sp500_dated['maxdd']:.1%} | {sp500_dated['trades']} | look-ahead for "
        "names added to the S&P after 2015; still only today's winners |",
        f"| public-at-2015 | **{public_start['after_tax_multiple']:.2f}x** | "
        f"{public_start['maxdd']:.1%} | {public_start['trades']} | later IPOs "
        "(ARM, CRWD, DDOG, OKTA, PYPL...) |",
        "",
        f"The S&P-dated sensitivity is **{gap:.2f}x lower** "
        f"({gap_pct:.0%} of the baseline). That is a *lower bound* on the selection "
        "effect: it still contains only names that survived to 2026, and it drops "
        "the non-S&P names from the static list. The true NASDAQ-2015 universe is "
        "not reconstructible from this cache because failed and acquired names have "
        "no Yahoo history.",
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
        "**Bottom line:** the 57.01x TECH headline is an upper bound on a biased "
        "universe, not an expected return. Report ETF-only (QQQ/SMH) results as "
        "survivorship-free evidence; treat stock-sleeve alpha as unproven until a "
        "point-in-time NASDAQ membership file with delisted names is added.",
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
    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.command in (None, "headline"):
        if not hasattr(args, "paths"):
            args.paths = 10_000
            args.strict = True
            args.expected_snapshot = EXPECTED_SNAPSHOT
        return headline(args)
    if args.command == "audit":
        return audit_command(args)
    if args.command == "survivorship":
        return survivorship_command(args)
    parser.error(f"unknown command {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
