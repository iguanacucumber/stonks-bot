"""Markdown + SVG report."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"

PCT = [
    "total_return",
    "cagr",
    "vol",
    "max_dd",
    "alpha",
    "tracking_error",
    "exposure",
    "hit_rate",
]


def fmt_table(df: pd.DataFrame) -> str:
    out = df.copy()
    for c in out.columns:
        if c in PCT:
            out[c] = out[c].map(lambda x: f"{x:.1%}" if pd.notna(x) else "—")
        elif c in ("sharpe", "sortino", "calmar", "beta", "turnover_ann"):
            out[c] = out[c].map(lambda x: f"{x:.2f}" if pd.notna(x) else "—")
        elif c == "trades":
            out[c] = out[c].map(lambda x: f"{int(x)}" if pd.notna(x) else "—")
    return out.to_markdown()


def equity_chart(curves: dict[str, pd.Series], path: Path):
    fig, ax = plt.subplots(figsize=(11, 5.2), dpi=140)
    fig.patch.set_facecolor("#111214")
    ax.set_facecolor("#111214")
    for name, s in curves.items():
        s = s.dropna()
        if s.empty:
            continue
        ax.plot(s.index, s / s.iloc[0], label=name, linewidth=1.4)
    ax.set_yscale("log")
    ax.grid(True, color="#2a2d31", linewidth=0.6)
    ax.tick_params(colors="#9aa0a6", labelsize=8)
    for spine in ax.spines.values():
        spine.set_color("#2a2d31")
    ax.set_ylabel("growth of $1 (log)", color="#c9cdd2", fontsize=9)
    leg = ax.legend(frameon=False, fontsize=8, loc="upper left")
    for t in leg.get_texts():
        t.set_color("#c9cdd2")
    fig.tight_layout()
    fig.savefig(path, facecolor=fig.get_facecolor())
    plt.close(fig)


def calibration_chart(df: pd.DataFrame, path: Path):
    called = df[df["direction"] != "none"].copy()
    if called.empty:
        return
    called["p"] = np.where(
        called["direction"] == "up", called["confidence"], 1 - called["confidence"]
    )
    called["bin"] = pd.cut(called["p"], [0.5, 0.55, 0.6, 0.65, 0.7, 0.8, 1.0])
    g = called.groupby("bin", observed=True).agg(
        p=("p", "mean"), hit=("correct", "mean"), n=("correct", "size")
    )
    fig, ax = plt.subplots(figsize=(4.6, 4.2), dpi=140)
    fig.patch.set_facecolor("#111214")
    ax.set_facecolor("#111214")
    ax.plot([0.5, 1], [0.5, 1], "--", color="#555b61", linewidth=1)
    ax.scatter(g["p"], g["hit"], s=np.clip(g["n"] * 3, 10, 120), color="#d9a441")
    ax.set_xlabel("stated probability", color="#c9cdd2", fontsize=8)
    ax.set_ylabel("realized hit rate", color="#c9cdd2", fontsize=8)
    ax.tick_params(colors="#9aa0a6", labelsize=8)
    for spine in ax.spines.values():
        spine.set_color("#2a2d31")
    ax.grid(True, color="#2a2d31", linewidth=0.5)
    fig.tight_layout()
    fig.savefig(path, facecolor=fig.get_facecolor())
    plt.close(fig)
