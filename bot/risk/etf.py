"""ETF-only panel and a causal cross-asset trend selector.

The panel is built from the committed daily bars only. No network access: every
symbol must already exist under `cache/bars/`. The universe is survivorship-free
in the weak sense that all six ETFs existed for the whole 2015-2026 window; it is
not a point-in-time selection of "best ETFs", and it deliberately excludes
international equity because EFA/EEM are not in the committed cache.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from .. import data
from .alloc import inverse_vol_weights

ETF_UNIVERSE: tuple[str, ...] = ("SPY", "QQQ", "TLT", "IEF", "GLD", "BIL")
DEFENSIVE = "BIL"
START = "2014-01-01"


def _rows(symbol: str) -> pd.DataFrame:
    path = data.BARS / f"{symbol}.csv"
    if not path.exists():
        raise FileNotFoundError(
            f"{symbol} is not in the committed cache ({path}); "
            "risk_v3 does not fetch new symbols"
        )
    df = pd.read_csv(path, parse_dates=["date"])
    if df.empty or "adjclose" not in df:
        raise ValueError(f"{symbol}: empty or malformed cached bars")
    close = df["close"].astype(float)
    adj = df["adjclose"].astype(float)
    factor = (adj / close).replace([np.inf, -np.inf], np.nan).fillna(1.0)
    out = pd.DataFrame(
        {
            "date": df["date"],
            "ticker": symbol,
            "adjclose": adj,
            "close": close,
            "open": (df["open"].astype(float) * factor) if "open" in df else adj,
            "volume": (df["volume"].astype(float) if "volume" in df else 1e9),
        }
    )
    return out.dropna(subset=["date", "adjclose", "close"]).sort_values("date")


def load_panel(
    symbols: tuple[str, ...] = ETF_UNIVERSE,
    start: str = START,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Long ETF panel plus a static membership frame.

    The panel starts before the backtest window so a 200-day MA is available on
    the first 2015 decision. The membership is static by construction; these are
    liquid, long-lived ETFs, not a stock universe.
    """
    syms = tuple(dict.fromkeys(symbols))
    parts = [_rows(s) for s in syms]
    panel = (
        pd.concat(parts, ignore_index=True)
        .drop_duplicates(["date", "ticker"])
        .sort_values(["date", "ticker"])
    )
    panel = panel[panel["date"] >= pd.Timestamp(start)].reset_index(drop=True)
    mem = pd.DataFrame({"date": pd.Timestamp(start), "ticker": list(syms)}).sort_values(
        "ticker"
    )
    return panel, mem


def trend_selector(
    universe: tuple[str, ...] = ETF_UNIVERSE,
    ma_window: int = 200,
    vol_window: int = 60,
    cap: float = 0.40,
    gross: float = 1.0,
    defensive: str = DEFENSIVE,
):
    """Return a selector: hold ETFs above their own 200-day MA, inverse-vol.

    At the decision close `t`:
      - an asset is eligible when its close is strictly above its own SMA and
        its 60-day realized vol is finite and positive;
      - eligible assets get capped inverse-vol weights summing to `gross`;
      - if none is eligible, the defensive ETF gets 100% of `gross`.
    The cap is relaxed to `gross / n` when fewer than three assets are eligible,
    because a 40% cap is infeasible for a two-asset book. The selector uses only
    data through `t`; the engine fills at the next open.
    """
    if gross <= 0:
        raise ValueError("gross must be positive")
    if not 0.0 < cap <= 1.0:
        raise ValueError("cap must be in (0, 1]")
    cache: dict[int, tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]] = {}

    def _inputs(mkt) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        key = id(mkt)
        hit = cache.get(key)
        if hit is not None:
            return hit
        cols = [t for t in universe if t in mkt.px.columns]
        px = mkt.px[cols].astype(float)
        ret = px.pct_change(fill_method=None)
        sigma = ret.rolling(vol_window, min_periods=vol_window).std(ddof=1) * np.sqrt(
            252
        )
        avg = px.rolling(ma_window, min_periods=ma_window).mean()
        if len(cache) > 4:
            cache.clear()
        cache[key] = (px, sigma, avg)
        return cache[key]

    def select(mkt, date, held, banned) -> dict[str, float]:
        px, sigma, avg = _inputs(mkt)
        if date not in px.index:
            return {}
        row = px.loc[date]
        sig = sigma.loc[date]
        average = avg.loc[date]
        picks = [
            t
            for t in px.columns
            if pd.notna(row[t])
            and pd.notna(average[t])
            and pd.notna(sig[t])
            and row[t] > average[t]
            and sig[t] > 0
        ]
        if not picks:
            if defensive in px.columns and pd.notna(row.get(defensive)):
                return {defensive: float(gross)}
            return {}
        eff_cap = max(float(cap), float(gross) / len(picks) + 1e-12)
        w = inverse_vol_weights(sig[picks], gross=gross, cap=min(eff_cap, 1.0))
        return {t: float(x) for t, x in w.items()}

    return select
