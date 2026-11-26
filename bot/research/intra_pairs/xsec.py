"""Cross-sectional intraday reversal on a small ETF set."""

from __future__ import annotations

import numpy as np
import pandas as pd


def xsec_trades(
    bars: dict[str, pd.DataFrame],
    *,
    lookback: int = 1,
    top_n: int = 2,
    mode: str = "long_only",
    cost_bps: float = 5.0,
) -> pd.DataFrame:
    opens = pd.DataFrame({k: v.set_index("ts")["open"] for k, v in bars.items()}).sort_index()
    closes = pd.DataFrame({k: v.set_index("ts")["close"] for k, v in bars.items()}).sort_index()
    opens = opens.reindex(closes.index)
    rets = closes / opens - 1.0
    rows = []
    idx = closes.index
    for i in range(lookback, len(idx) - 1):
        if pd.Timestamp(idx[i]).normalize() != pd.Timestamp(idx[i + 1]).normalize():
            continue
        trailing = closes.iloc[i] / closes.iloc[i - lookback] - 1.0
        if trailing.notna().sum() < top_n * 2:
            continue
        worst = trailing.nsmallest(top_n).index
        best = trailing.nlargest(top_n).index
        gross = 0.0
        cost = 0.0
        for t in worst:
            gross += float(rets.iloc[i + 1][t]) / top_n
            cost += cost_bps / 1e4 / top_n
        if mode == "neutral":
            for t in best:
                gross -= float(rets.iloc[i + 1][t]) / top_n
                cost += cost_bps / 1e4 / top_n
        rows.append(
            {
                "session": pd.Timestamp(idx[i]).normalize(),
                "ts": idx[i],
                "gross": gross,
                "net": gross - 2.0 * cost,
            }
        )
    return pd.DataFrame(rows)
