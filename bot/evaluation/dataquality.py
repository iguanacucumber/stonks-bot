"""Data-quality audit for the price cache and the constructed panel.

The audit is deliberately read-only.  It never calls Yahoo and never rewrites a
bar file.  It returns a JSON-serialisable report and a boolean ``ok``; the CLI
wrapper in ``scripts/reproduce.py`` exits non-zero when ``ok`` is false.

Hard failures (must block a reproducible headline):
  * duplicate dates inside a symbol file;
  * missing required columns;
  * non-positive close/adjusted close;
  * high < low, or high/low inconsistent with open/close;
  * duplicate (date, ticker) rows in the panel;
  * alias cycles;
  * duplicate membership snapshots.

Warnings (review, not automatically fatal):
  * stale bars at the snapshot date (normal for delisted names);
  * long calendar gaps inside a symbol's history;
  * split-like unadjusted price jumps or adjustment-factor restatements;
  * membership snapshots more than ~13 months apart.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

REQUIRED_BAR_COLUMNS = ("date", "close", "adjclose")
MAX_EXAMPLES = 40


def _hard(report: dict, check: str, symbol: str, detail: str) -> None:
    report["hard_failures"].append({"check": check, "symbol": symbol, "detail": detail})


def _warn(report: dict, check: str, symbol: str, detail: str) -> None:
    report["warnings"].append({"check": check, "symbol": symbol, "detail": detail})


def _info(report: dict, check: str, symbol: str, detail: str) -> None:
    report["info"].append({"check": check, "symbol": symbol, "detail": detail})


def _cap_examples(report: dict) -> None:
    for key in ("hard_failures", "warnings", "info"):
        if len(report[key]) > MAX_EXAMPLES:
            extra = len(report[key]) - MAX_EXAMPLES
            del report[key][MAX_EXAMPLES:]
            report["truncated"] = report.get("truncated", {})
            report["truncated"][key] = extra


def audit_aliases(aliases: dict[str, str]) -> dict:
    """Detect cycles and self-referential/duplicate alias targets."""
    report = {"hard_failures": [], "warnings": [], "info": [], "cycles": []}
    for start in aliases:
        seen: list[str] = []
        cur = start
        while cur in aliases and aliases[cur] != cur:
            if cur in seen:
                cycle = seen[seen.index(cur) :] + [cur]
                report["cycles"].append(cycle)
                _hard(
                    report,
                    "alias_cycle",
                    start,
                    " -> ".join(cycle),
                )
                break
            seen.append(cur)
            cur = aliases[cur]
    targets = Counter(aliases.values())
    for target, n in targets.items():
        if n > 1:
            _info(
                report,
                "alias_many_to_one",
                target,
                f"{n} tickers map to this canonical symbol",
            )
    for a, b in aliases.items():
        if a == b:
            _warn(report, "alias_self", a, "identity alias is redundant")
    # canonical(canonical(t)) == canonical(t): every chain must end at a symbol
    # that is not an outgoing alias (a self-alias is the terminal marker).
    for start in aliases:
        cur, seen = start, set()
        while cur in aliases and aliases[cur] != cur:
            if cur in seen:
                break  # already reported as a cycle
            seen.add(cur)
            cur = aliases[cur]
        if cur in aliases and aliases[cur] != cur:
            _hard(report, "alias_not_idempotent", start, f"chain ends in {cur}")
    report["ok"] = not report["hard_failures"]
    return report


def _check_ohlc(
    report: dict,
    symbol: str,
    df: pd.DataFrame,
    max_gap_days: int,
    required: bool = False,
) -> None:
    """OHLC scars are fatal only for symbols used by the headline."""

    def issue(check: str, detail: str) -> None:
        if required:
            _hard(report, check, symbol, detail)
        else:
            _warn(report, check, symbol, detail)

    dates = pd.to_datetime(df["date"], errors="coerce")
    if dates.isna().any():
        _hard(report, "invalid_date", symbol, f"{int(dates.isna().sum())} rows")
        return
    dates = dates.dt.normalize()
    dup = int(dates.duplicated().sum())
    if dup:
        _hard(report, "duplicate_date", symbol, f"{dup} duplicate dates")
    order = np.argsort(dates.to_numpy())
    if not np.array_equal(order, np.arange(len(dates))):
        _warn(report, "unsorted_dates", symbol, "rows are not date-sorted")
    close = pd.to_numeric(df["close"], errors="coerce")
    adj = pd.to_numeric(df["adjclose"], errors="coerce")
    if (close <= 0).any() or (adj <= 0).any():
        _hard(
            report,
            "nonpositive_price",
            symbol,
            f"close<=0: {int((close <= 0).sum())}, adjclose<=0: {int((adj <= 0).sum())}",
        )
    for col in ("open", "high", "low"):
        if col not in df.columns:
            continue
        x = pd.to_numeric(df[col], errors="coerce")
        if (x <= 0).any():
            issue(f"nonpositive_{col}", f"{int((x <= 0).sum())} rows")
    if {"high", "low"} <= set(df.columns):
        high = pd.to_numeric(df["high"], errors="coerce")
        low = pd.to_numeric(df["low"], errors="coerce")
        bad = (high < low).fillna(False)
        if bad.any():
            issue("high_below_low", f"{int(bad.sum())} rows")
        if "open" in df.columns and "close" in df.columns:
            op = pd.to_numeric(df["open"], errors="coerce")
            bad_hi = (high < np.maximum(op, close) * 0.999).fillna(False)
            bad_lo = (low > np.minimum(op, close) * 1.001).fillna(False)
            if bad_hi.any() or bad_lo.any():
                issue(
                    "ohlc_inconsistent",
                    f"high<max(open,close): {int(bad_hi.sum())}, "
                    f"low>min(open,close): {int(bad_lo.sum())}",
                )

    # calendar gaps (holidays/weekends are short; a long hole is suspicious)
    gaps = dates.sort_values().diff().dt.days
    long_gaps = gaps[gaps > max_gap_days]
    if len(long_gaps):
        _warn(
            report,
            "date_gap",
            symbol,
            f"{len(long_gaps)} gap(s) > {max_gap_days}d; max {int(long_gaps.max())}d",
        )
    # split-like raw-close jump with a smooth adjusted series is normal; both
    # jumping together is an unadjusted split / restatement risk.
    raw_ret = close.pct_change(fill_method=None)
    adj_ret = adj.pct_change(fill_method=None)
    split_like = raw_ret <= -0.45
    if split_like.any():
        smooth = adj_ret[split_like].abs() < 0.25
        if smooth.any():
            _info(
                report,
                "split_adjusted",
                symbol,
                f"{int(smooth.sum())} split-like raw close jump(s), adjusted series smooth",
            )
        if (~smooth).any():
            _warn(
                report,
                "unadjusted_split",
                symbol,
                f"{int((~smooth).sum())} split-like raw jump(s) also move adjusted close",
            )
    factor = adj / close
    factor_chg = factor.pct_change(fill_method=None)
    big_factor = (factor_chg.abs() > 0.20) & (raw_ret.abs() < 0.20)
    if big_factor.any():
        _info(
            report,
            "adjustment_factor_change",
            symbol,
            f"{int(big_factor.sum())} adjustment-factor change(s) without a raw price jump",
        )


def audit_bars(
    bars_dir: str | Path,
    snapshot_date: str | pd.Timestamp | None = None,
    stale_days: int = 5,
    max_gap_days: int = 30,
    symbols: Iterable[str] | None = None,
    require_fresh: Iterable[str] | None = None,
) -> dict:
    """Audit every cached symbol CSV (or a supplied subset)."""
    bars_dir = Path(bars_dir)
    report: dict = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "bars_dir": str(bars_dir),
        "hard_failures": [],
        "warnings": [],
        "info": [],
        "symbols_checked": 0,
        "rows_checked": 0,
        "stale": [],
        "gap_symbols": 0,
        "split_adjusted": 0,
        "unadjusted_split": 0,
    }
    if not bars_dir.exists():
        _hard(report, "missing_bars_dir", str(bars_dir), "cache/bars does not exist")
        report["ok"] = False
        return report
    wanted = {s.replace("/", "_") for s in symbols} if symbols is not None else None
    required_fresh = set(require_fresh or ())
    last_dates: dict[str, pd.Timestamp] = {}
    files = sorted(bars_dir.glob("*.csv"))
    if wanted is not None:
        files = [p for p in files if p.stem in wanted]
    snapshot = pd.Timestamp(snapshot_date) if snapshot_date is not None else None
    if snapshot is None:
        spy = bars_dir / "SPY.csv"
        if spy.exists():
            try:
                snapshot = pd.to_datetime(
                    pd.read_csv(spy, usecols=["date"])["date"]
                ).max()
            except Exception:
                snapshot = None
    for path in files:
        symbol = path.stem
        try:
            df = pd.read_csv(path)
        except Exception as exc:
            _hard(report, "unreadable_file", symbol, str(exc))
            continue
        if df.empty:
            _hard(report, "empty_file", symbol, "no rows")
            continue
        missing = [c for c in REQUIRED_BAR_COLUMNS if c not in df.columns]
        if missing:
            _hard(report, "missing_columns", symbol, ",".join(missing))
            continue
        report["symbols_checked"] += 1
        report["rows_checked"] += len(df)
        _check_ohlc(report, symbol, df, max_gap_days, required=symbol in required_fresh)
        dates = pd.to_datetime(df["date"], errors="coerce").dropna()
        if len(dates):
            last = dates.max().normalize()
            last_dates[symbol] = last
            if snapshot is not None and last < snapshot - pd.Timedelta(days=stale_days):
                report["stale"].append(
                    {"symbol": symbol, "last": last.date().isoformat()}
                )
                if symbol in required_fresh:
                    _hard(
                        report,
                        "stale_required_symbol",
                        symbol,
                        f"last bar {last.date()} vs snapshot {snapshot.date()}",
                    )
                else:
                    _warn(
                        report,
                        "stale_bar",
                        symbol,
                        f"last bar {last.date()} vs snapshot {snapshot.date()}",
                    )
    report["snapshot_date"] = (
        snapshot.date().isoformat() if snapshot is not None else None
    )
    report["warning_counts"] = dict(Counter(w["check"] for w in report["warnings"]))
    report["info_counts"] = dict(Counter(i["check"] for i in report["info"]))
    report["hard_failure_counts"] = dict(
        Counter(h["check"] for h in report["hard_failures"])
    )
    report["gap_symbols"] = int(report["warning_counts"].get("date_gap", 0))
    report["split_adjusted"] = int(report["info_counts"].get("split_adjusted", 0))
    report["unadjusted_split"] = int(
        report["warning_counts"].get("unadjusted_split", 0)
    )
    report["fresh_symbols"] = int(
        sum(
            1
            for d in last_dates.values()
            if snapshot is not None and d >= snapshot - pd.Timedelta(days=stale_days)
        )
    )
    report["stale_symbols"] = int(len(report["stale"]))
    _cap_examples(report)
    report["ok"] = not report["hard_failures"]
    return report


def audit_panel(panel: pd.DataFrame, snapshot_date=None) -> dict:
    report = {"hard_failures": [], "warnings": [], "info": []}
    required = {"date", "ticker", "adjclose"}
    missing = required - set(panel.columns)
    if missing:
        _hard(report, "panel_missing_columns", "panel", ",".join(sorted(missing)))
        report["ok"] = False
        return report
    dup = int(panel.duplicated(["date", "ticker"]).sum())
    if dup:
        _hard(report, "panel_duplicate_rows", "panel", f"{dup} duplicate (date,ticker)")
    bad = int((pd.to_numeric(panel["adjclose"], errors="coerce") <= 0).sum())
    if bad:
        _hard(report, "panel_nonpositive_price", "panel", f"{bad} rows")
    dates = pd.to_datetime(panel["date"], errors="coerce")
    if dates.isna().any():
        _hard(report, "panel_invalid_date", "panel", f"{int(dates.isna().sum())} rows")
    report.update(
        {
            "rows": int(len(panel)),
            "tickers": int(panel["ticker"].nunique()),
            "dates": int(dates.nunique()),
            "start": dates.min().date().isoformat() if len(dates) else None,
            "end": dates.max().date().isoformat() if len(dates) else None,
        }
    )
    if snapshot_date is not None and len(dates):
        snap = pd.Timestamp(snapshot_date)
        if dates.max().normalize() < snap - pd.Timedelta(days=5):
            _warn(
                report,
                "panel_stale",
                "panel",
                f"panel ends {dates.max().date()} vs snapshot {snap.date()}",
            )
    report["ok"] = not report["hard_failures"]
    return report


def audit_membership(membership: pd.DataFrame) -> dict:
    report = {"hard_failures": [], "warnings": [], "info": []}
    required = {"date", "ticker"}
    missing = required - set(membership.columns)
    if missing:
        _hard(report, "membership_missing_columns", "membership", ",".join(missing))
        report["ok"] = False
        return report
    dup = int(membership.duplicated(["date", "ticker"]).sum())
    if dup:
        _hard(report, "membership_duplicate_rows", "membership", f"{dup} rows")
    dates = pd.to_datetime(membership["date"], errors="coerce").dropna().unique()
    dates = np.sort(dates)
    if len(dates):
        gaps = pd.Series(dates).diff().dt.days.dropna()
        long = gaps[gaps > 400]
        if len(long):
            _warn(
                report,
                "membership_snapshot_gap",
                "membership",
                f"{len(long)} gaps > 400d; max {int(long.max())}d",
            )
    report.update(
        {
            "rows": int(len(membership)),
            "tickers": int(membership["ticker"].nunique()),
            "snapshots": int(len(dates)),
            "start": pd.Timestamp(dates.min()).date().isoformat()
            if len(dates)
            else None,
            "end": pd.Timestamp(dates.max()).date().isoformat() if len(dates) else None,
        }
    )
    report["ok"] = not report["hard_failures"]
    return report


def audit_cache(
    cache_dir: str | Path = "cache",
    panel: pd.DataFrame | None = None,
    membership: pd.DataFrame | None = None,
    aliases: dict[str, str] | None = None,
    required_fresh: Iterable[str] | None = None,
) -> dict:
    """Full cache audit; merges bar, panel, membership and alias reports."""
    cache_dir = Path(cache_dir)
    bars = audit_bars(
        cache_dir / "bars",
        require_fresh=required_fresh,
    )
    out = dict(bars)
    out["panel"] = audit_panel(panel) if panel is not None else {"skipped": True}
    out["membership"] = (
        audit_membership(membership) if membership is not None else {"skipped": True}
    )
    alias = audit_aliases(aliases or {})
    out["aliases"] = alias
    for key in ("hard_failures", "warnings", "info"):
        out[key] = list(out.get(key, [])) + list(alias.get(key, []))
    for part in ("panel", "membership"):
        sub = out[part]
        if isinstance(sub, dict):
            for key in ("hard_failures", "warnings", "info"):
                out[key] += [{**item, "source": part} for item in sub.get(key, [])]
    # Bar counts were computed before the example cap; add the other sources
    # rather than recounting the truncated example lists.
    warning_counts = Counter(out.get("warning_counts", {}))
    info_counts = Counter(out.get("info_counts", {}))
    hard_counts = Counter(out.get("hard_failure_counts", {}))
    for src in (alias, out["panel"], out["membership"]):
        if not isinstance(src, dict):
            continue
        warning_counts.update(Counter(w["check"] for w in src.get("warnings", [])))
        info_counts.update(Counter(i["check"] for i in src.get("info", [])))
        hard_counts.update(Counter(h["check"] for h in src.get("hard_failures", [])))
    out["warning_counts"] = dict(warning_counts)
    out["info_counts"] = dict(info_counts)
    out["hard_failure_counts"] = dict(hard_counts)
    out["unadjusted_split"] = int(warning_counts.get("unadjusted_split", 0))
    out["split_adjusted"] = int(info_counts.get("split_adjusted", 0))
    out["gap_symbols"] = int(warning_counts.get("date_gap", 0))
    out["ok"] = not out["hard_failures"]
    return out


def write_report(report: dict, path: str | Path) -> Path:
    import json

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n")
    return path


def markdown_summary(report: dict) -> str:
    hard = report.get("hard_failures", [])
    warn = report.get("warnings", [])
    warn_counts = report.get("warning_counts") or dict(
        Counter(x["check"] for x in warn)
    )
    hard_counts = report.get("hard_failure_counts") or dict(
        Counter(x["check"] for x in hard)
    )
    lines = [
        "# Data-quality audit",
        "",
        f"- Snapshot: **{report.get('snapshot_date', 'unknown')}**",
        f"- Symbols checked: **{report.get('symbols_checked', 0)}** "
        f"({report.get('rows_checked', 0):,} bar rows)",
        f"- Fresh symbols: **{report.get('fresh_symbols', 0)}**; "
        f"stale: **{report.get('stale_symbols', 0)}**",
        f"- Hard failures: **{sum(hard_counts.values())}**; "
        f"warnings: **{sum(warn_counts.values())}** "
        f"({len(warn)} example rows stored in JSON)",
        f"- Verdict: **{'PASS' if report.get('ok') else 'FAIL'}**",
        "",
    ]
    if hard:
        lines += [
            "## Hard failures",
            "",
            "| check | symbol | detail |",
            "|---|---|---|",
        ]
        for x in hard[:MAX_EXAMPLES]:
            lines.append(f"| {x['check']} | {x['symbol']} | {x['detail']} |")
        lines.append("")
    if warn:
        counts = report.get("warning_counts") or dict(Counter(x["check"] for x in warn))
        lines += ["## Warnings by check", "", "| check | count |", "|---|---:|"]
        for check, n in sorted(counts.items()):
            lines.append(f"| {check} | {n} |")
        lines.append("")
    stale = report.get("stale", [])
    if stale:
        lines += [
            "## Stale symbols",
            "",
            f"{len(stale)} symbols end before the snapshot. Delisted names are "
            "expected; a fresh required symbol would have been a hard failure.",
            "",
            "| symbol | last bar |",
            "|---|---|",
        ]
        for x in stale[:25]:
            lines.append(f"| {x['symbol']} | {x['last']} |")
        lines.append("")
    lines += [
        f"Unadjusted split-like anomalies: **{report.get('unadjusted_split', 0)}**. "
        f"Split-adjusted jumps (informational): **{report.get('split_adjusted', 0)}**.",
        "",
        "Full machine-readable report: `results/eval_data_quality.json`.",
        "",
        "Generated by `uv run python scripts/reproduce.py audit`.",
    ]
    return "\n".join(lines) + "\n"
