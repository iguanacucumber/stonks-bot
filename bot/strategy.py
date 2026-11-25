"""Signal construction: momentum rank, inverse-vol weights, regime, sector cap."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import universe


@dataclass(frozen=True)
class Config:
    start: str = "2015-01-01"
    end: str | None = None
    top_n: int = 20
    rebalance_days: int = 21
    lookback: int = 126
    skip: int = 21
    vol_window: int = 20
    max_weight: float = 0.06
    sector_cap: float = 0.30
    gross: float = 0.98
    regime_ma: int = 200
    regime_gross: float = 0.50
    stop: float = 0.20
    reentry_days: int = 20
    cost_bps: float = 10.0
    min_dollar_vol: float = 5e6
    llm_active_share: float = 0.20
    # Default: dual-momentum rotation. Hold VGT while its 12-month return beats
    # SPY, otherwise run the S&P momentum sleeve. Set core_weight < 1 to blend.
    core: str | None = "VGT"
    core_weight: float = 1.0
    core_mode: str = "relative"        # "always" | "relative" | "relative_switch"
    core_lookback: int = 252
    core_alt: str | None = "SPY"
    fallback: str = "momentum"         # "momentum" or "sectors"
    fallback_sectors: tuple[str, ...] = ("XLK", "XLF", "XLE", "XLV", "XLI", "XLP", "XLU", "XLB", "XLY")
    sector_top: int = 3


class Market:
    """Aligned daily matrices for one backtest."""

    def __init__(self, panel: pd.DataFrame, membership: pd.DataFrame, cfg: Config):
        self.cfg = cfg
        px = panel.pivot(index="date", columns="ticker", values="adjclose").sort_index()
        vol = panel.pivot(index="date", columns="ticker", values="volume").sort_index()
        self.spy = px["SPY"].dropna()
        self.all_dates = px.index
        self.dates = self.all_dates
        if cfg.end:
            self.dates = self.dates[self.dates <= pd.Timestamp(cfg.end)]
        self.dates = self.dates[self.dates >= pd.Timestamp(cfg.start)]
        self.px = px
        self.raw_px = px
        self.vol = vol
        self.ret = self.px.pct_change(fill_method=None)
        self.sigma = self.ret.rolling(cfg.vol_window, min_periods=10).std() * np.sqrt(
            252
        )
        self.mom = self.px.shift(cfg.skip) / self.px.shift(cfg.lookback) - 1.0
        self.dollar = (self.px * self.vol).rolling(20, min_periods=10).median()
        self.spy_ma = self.spy.rolling(cfg.regime_ma, min_periods=cfg.regime_ma).mean()
        self.regime = self.spy > self.spy_ma
        self.members = self._membership(membership)
        self.sectors = universe.sector_map()
        self.member_dates = membership["date"].sort_values().unique()

    def _membership(self, membership: pd.DataFrame) -> pd.DataFrame:
        m = membership.copy()
        m["ticker"] = m["ticker"].map(universe.canonical)
        groups = m.groupby("date")["ticker"].apply(lambda s: sorted(set(s)))
        tickers = sorted(set(m["ticker"]))
        mat = pd.DataFrame(False, index=pd.DatetimeIndex(groups.index), columns=tickers)
        for d, names in groups.items():
            mat.loc[d, names] = True
        all_dates = mat.index.union(self.all_dates).sort_values()
        mat = mat.reindex(all_dates).ffill().reindex(self.all_dates).fillna(False)
        return mat

    def members_at(self, date: pd.Timestamp) -> set[str]:
        if date not in self.members.index:
            return set()
        return set(self.members.columns[self.members.loc[date].values])


def eligible(mkt: Market, date: pd.Timestamp) -> pd.DataFrame:
    cfg = mkt.cfg
    members = mkt.members_at(date)
    cols = [c for c in mkt.px.columns if c in members and c != "SPY"]
    if not cols:
        return pd.DataFrame()
    df = pd.DataFrame(
        {
            "mom": mkt.mom.loc[date, cols],
            "sigma": mkt.sigma.loc[date, cols],
            "dollar": mkt.dollar.loc[date, cols],
            "price": mkt.raw_px.loc[date, cols],
        }
    )
    df = df.dropna(subset=["mom", "sigma", "dollar", "price"])
    df = df[(df["sigma"] > 0) & (df["price"] > 5) & (df["dollar"] > cfg.min_dollar_vol)]
    if df.empty:
        return df
    cut = df["dollar"].quantile(0.4)
    df = df[df["dollar"] >= cut]
    df["sector"] = [mkt.sectors.get(t, "Unknown") for t in df.index]
    return df.sort_values("mom", ascending=False)


def select(mkt: Market, date: pd.Timestamp) -> list[str]:
    cfg = mkt.cfg
    df = eligible(mkt, date)
    if df.empty:
        return []
    max_per_sector = max(1, int(np.floor(cfg.sector_cap * cfg.top_n)))
    counts: dict[str, int] = {}
    picks: list[str] = []
    for ticker, row in df.iterrows():
        sec = row["sector"]
        if counts.get(sec, 0) >= max_per_sector:
            continue
        picks.append(ticker)
        counts[sec] = counts.get(sec, 0) + 1
        if len(picks) == cfg.top_n:
            break
    return picks


def inverse_vol_weights(
    mkt: Market, date: pd.Timestamp, picks: list[str], gross: float | None = None
) -> dict[str, float]:
    cfg = mkt.cfg
    if not picks:
        return {}
    gross = cfg.gross if gross is None else gross
    sig = mkt.sigma.loc[date, picks].astype(float)
    sig = sig.replace(0, np.nan).fillna(sig.median())
    raw = 1.0 / sig
    w = raw / raw.sum()
    # iterative cap, redistribute the excess
    for _ in range(10):
        over = w > cfg.max_weight
        if not over.any():
            break
        excess = (w[over] - cfg.max_weight).sum()
        w[over] = cfg.max_weight
        room = ~over
        if not room.any():
            break
        w[room] += excess * w[room] / w[room].sum()
    w = w / w.sum() * gross
    return {t: float(x) for t, x in w.items()}


def target_weights(
    mkt: Market, date: pd.Timestamp, held: list[str]
) -> dict[str, float]:
    cfg = mkt.cfg
    picks = select(mkt, date)
    w = inverse_vol_weights(mkt, date, picks)
    if not bool(mkt.regime.loc[date]):
        w = {t: x * cfg.regime_gross for t, x in w.items()}
    return w
