"""v9 intraday lead-lag, pairs and cross-sectional reversal.

Run: uv run python scripts/research_intraday_pairs.py
Writes results/intraday_pairs.md and results/intraday_pairs_trials.csv.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from bot.research.intra_pairs.lead_lag import (  # noqa: E402
    lead_lag_regression_trades,
    lead_lag_trades,
)
from bot.research.intra_pairs.pairs import pair_trades  # noqa: E402
from bot.research.intra_pairs.xsec import xsec_trades  # noqa: E402
from bot.research.registry import TrialRegistry  # noqa: E402
from bot.research.short3.intraday import load_bars, summarize_trades  # noqa: E402

SYMS = ("SPY", "QQQ", "SMH", "SOXX", "XLK")


def run_lead(bars, interval, cost_bps):
    rows = []
    for src in ("SMH",):
        for tgt in ("QQQ", "SPY"):
            for lag in (1, 2, 3):
                for mode in ("momentum", "reversal"):
                    for thr in (0.0, 0.001, 0.0025):
                        t = lead_lag_trades(
                            bars[src], bars[tgt], lag=lag, threshold=thr,
                            mode=mode, cost_bps=cost_bps,
                        )
                        s = summarize_trades(t, bars[tgt]["session"].nunique())
                        rows.append({"kind": "lead", "interval": interval, "src": src,
                                     "tgt": tgt, "lag": lag, "mode": mode,
                                     "threshold": thr, "cost_bps": cost_bps, **s})
            t = lead_lag_regression_trades(bars["SMH"], bars[tgt], window=100, threshold=0.0, cost_bps=cost_bps)
            s = summarize_trades(t, bars[tgt]["session"].nunique())
            rows.append({"kind": "lead_regression", "interval": interval, "src": "SMH",
                         "tgt": tgt, "lag": 1, "mode": "regression", "threshold": 0.0,
                         "cost_bps": cost_bps, **s})
    return rows


def run_pairs(bars, interval, cost_bps):
    rows = []
    for pair in (("SMH", "SOXX"), ("QQQ", "SPY")):
        for lb in (20, 40, 80):
            for z in (1.5, 2.0):
                for mh in (1, 3):
                    for mode in ("neutral", "long_only"):
                        t = pair_trades(
                            bars[pair[0]], bars[pair[1]], lookback=lb,
                            entry_z=z, max_hold=mh, mode=mode, cost_bps=cost_bps,
                        )
                        s = summarize_trades(t, bars[pair[0]]["session"].nunique())
                        rows.append({"kind": "pair", "interval": interval,
                                     "pair": f"{pair[0]}/{pair[1]}", "lookback": lb,
                                     "entry_z": z, "max_hold": mh, "mode": mode,
                                     "cost_bps": cost_bps, **s})
    return rows


def run_xsec(bars, interval, cost_bps):
    rows = []
    for lb in (1, 3):
        for mode in ("long_only", "neutral"):
            t = xsec_trades(bars, lookback=lb, top_n=2, mode=mode, cost_bps=cost_bps)
            s = summarize_trades(t, bars["SPY"]["session"].nunique())
            rows.append({"kind": "xsec", "interval": interval, "lookback": lb,
                         "mode": mode, "cost_bps": cost_bps, **s})
    return rows


def fmt_table(rows, label):
    lines = [f"### {label}", "",
             "| variant | n | hit | mean net | after-tax | CAGR | maxDD |",
             "|---|---:|---:|---:|---:|---:|---:|"]
    for r in rows:
        name = r.get("pair") or r.get("tgt") or "xsec"
        extra = ""
        if r["kind"] == "lead":
            extra = f" lag={r['lag']} {r['mode']} thr={r['threshold']:.3%}"
        elif r["kind"] == "lead_regression":
            extra = " regression"
        elif r["kind"] == "pair":
            extra = f" lb={r['lookback']} z={r['entry_z']} hold={r['max_hold']} {r['mode']}"
        else:
            extra = f" lb={r['lookback']} {r['mode']}"
        lines.append(
            f"| {name}{extra} | {r['n']} | {r['hit']:.1%} | {r['mean']:+.3%} | "
            f"{r['multiple']:.3f}x | {r['cagr']:+.1%} | {r['maxdd']:.1%} |"
        )
    return lines


def main():
    registry = TrialRegistry()
    all_rows = {}
    for interval, rng in (("1h", "2y"), ("5m", "60d")):
        bars = {s: load_bars(s, interval, rng) for s in SYMS}
        rows = []
        rows += run_lead(bars, interval, 5.0)
        rows += run_pairs(bars, interval, 5.0)
        rows += run_xsec(bars, interval, 5.0)
        rows2 = []
        rows2 += run_lead(bars, interval, 10.0)
        rows2 += run_pairs(bars, interval, 10.0)
        rows2 += run_xsec(bars, interval, 10.0)
        all_rows[interval] = (rows, rows2, bars)
        for r in rows + rows2:
            registry.add(
                family=f"intraday_{r['kind']}",
                hypothesis="intraday lead/pair/cross-section survives costs",
                universe=r.get("pair") or r.get("tgt") or "ETF set",
                start="2024-11-26" if interval == "1h" else "2026-09-02",
                end="2026-11-25",
                params={k: v for k, v in r.items() if k not in ("n", "sessions", "multiple", "cagr", "maxdd", "hit", "mean", "tax", "kind", "interval")},
                metric="after_tax_multiple",
                value=r.get("multiple"),
                cost_model="fr_cto_5bps" if r["cost_bps"] == 5 else "fr_cto_10bps",
                notes=f"{interval}; n={r.get('n', 0)}; sample-limited",
                n_trials=len(rows) + len(rows2),
            )
    registry.write(ROOT / "results" / "intraday_pairs_trials.csv")

    # QQQ same-window gross benchmark.
    qqq_2y = all_rows["1h"][2]["QQQ"]
    qqq_5m = all_rows["5m"][2]["QQQ"]
    qqq_2y_ret = float(qqq_2y["close"].iloc[-1] / qqq_2y["close"].iloc[0])
    qqq_5m_ret = float(qqq_5m["close"].iloc[-1] / qqq_5m["close"].iloc[0])

    lines = [
        "# Intraday pairs and lead-lag",
        "",
        "Generated by `uv run python scripts/research_intraday_pairs.py`.",
        "Actual Yahoo 1h 2y and 5m 60d bars, 09:30-16:00 ET. Signals use",
        "completed bars; entry next bar open; 5/10 bps per side; annual French",
        "PFU. 5m is a 60-day execution-quality sample, not 2-year robustness.",
        "",
        "## 1-hour, 2 years",
        "",
    ]
    for label, key in (("Lead-lag", "lead"),):
        lines += fmt_table([r for r in all_rows["1h"][0] if r["kind"].startswith(key)], label)
    lines += fmt_table([r for r in all_rows["1h"][0] if r["kind"] == "pair"], "Intraday pairs")
    lines += fmt_table([r for r in all_rows["1h"][0] if r["kind"] == "xsec"], "Cross-sectional")
    lines += ["", f"QQQ same 2y window: {qqq_2y_ret:.2f}x gross buy & hold.", ""]
    lines += ["## 5-minute, 60 days", ""]
    lines += fmt_table([r for r in all_rows["5m"][0] if r["kind"].startswith("lead")], "Lead-lag")
    lines += fmt_table([r for r in all_rows["5m"][0] if r["kind"] == "pair"], "Intraday pairs")
    lines += fmt_table([r for r in all_rows["5m"][0] if r["kind"] == "xsec"], "Cross-sectional")
    lines += ["", f"QQQ same 60-day window: {qqq_5m_ret:.2f}x gross.", ""]
    lines += ["## 2x costs (best rows)", ""]
    for interval in ("1h", "5m"):
        best = max(all_rows[interval][1], key=lambda r: r["multiple"])
        lines.append(f"- {interval}: best 2x row {best['kind']} {best.get('pair', best.get('tgt', ''))} "
                     f"{best['multiple']:.3f}x, n={best['n']}.")
    lines += ["", "## Verdict", ""]
    best_1h = max(all_rows["1h"][0], key=lambda r: r["multiple"])
    best_5m = max(all_rows["5m"][0], key=lambda r: r["multiple"])
    best_1h2 = max(all_rows["1h"][1], key=lambda r: r["multiple"])
    gates = {
        "best 1h after-tax beats QQQ 2y": best_1h["multiple"] > qqq_2y_ret,
        "best 1h after-tax beats BIL 2y (1.05x)": best_1h["multiple"] > 1.05,
        "best 1h survives 2x costs": best_1h2["multiple"] > 1.0,
        "best 5m after-tax beats QQQ 60d": best_5m["multiple"] > qqq_5m_ret,
    }
    for k, v in gates.items():
        lines.append(f"- {k}: **{'PASS' if v else 'FAIL'}**")
    lines += ["", (
        "**Verdict: `reject`.** Every intraday family loses after realistic costs. "
        f"Best 1h row is {best_1h['kind']} at {best_1h['multiple']:.3f}x vs QQQ "
        f"{qqq_2y_ret:.2f}x; best 5m row is {best_5m['multiple']:.3f}x on a "
        "60-day sample. There is no implementable satellite edge; keep the "
        "monthly book."
    ), "",
    "## Sample limits", "",
    "- 1h: 501 sessions, 2024-11-26..2026-11-25, in-sample.",
    "- 5m: ~60 days, execution-quality only.",
    "- Yahoo aggregates, no queue/partial-fill/auction simulation.",
    "- French PFU annual net; intraday gains assumed realized.",
    "",
    "## Files", "",
    "- `bot/research/intra_pairs/lead_lag.py`, `pairs.py`, `xsec.py`",
    "- `scripts/research_intraday_pairs.py`",
    "- `tests/test_intraday_pairs.py`",
    "- `results/intraday_pairs_trials.csv`",
    ""]
    (ROOT / "results" / "intraday_pairs.md").write_text("\n".join(lines) + "\n")
    print(f"best 1h {best_1h['kind']} {best_1h['multiple']:.3f}x vs QQQ {qqq_2y_ret:.3f}x; "
          f"best 5m {best_5m['multiple']:.3f}x vs QQQ {qqq_5m_ret:.3f}x")


if __name__ == "__main__":
    main()
