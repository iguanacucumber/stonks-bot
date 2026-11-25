"""Signal construction: momentum rank, inverse-vol weights, regime, sector cap."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import universe

# Rebuilding the pivots and rolling signals is the single most expensive step and
# does not depend on start/end/stop/etc. Cache per panel object, keeping a strong
# reference so the id() cannot be reused while the entry is alive.
_DERIVED: dict[tuple, tuple] = {}
_MEMBERS: dict[tuple, tuple] = {}


def _derived(panel: pd.DataFrame, cfg: Config) -> tuple:
    key = (id(panel), cfg.vol_window, cfg.lookback, cfg.skip, cfg.regime_ma)
    hit = _DERIVED.get(key)
    if hit is not None and hit[0] is panel:
        return hit[1]
    px = panel.pivot(index="date", columns="ticker", values="adjclose").sort_index()
    vol = panel.pivot(index="date", columns="ticker", values="volume").sort_index()
    spy = px["SPY"].dropna()
    ret = px.pct_change(fill_method=None)
    out = {
        "px": px,
        "vol": vol,
        "spy": spy,
        "ret": ret,
        "sigma": ret.rolling(cfg.vol_window, min_periods=min(10, cfg.vol_window)).std()
        * np.sqrt(252),
        "mom": px.shift(cfg.skip) / px.shift(cfg.lookback) - 1.0,
        "dollar": (px * vol).rolling(20, min_periods=10).median(),
        "spy_ma": spy.rolling(cfg.regime_ma, min_periods=cfg.regime_ma).mean(),
    }
    if len(_DERIVED) > 4:
        _DERIVED.clear()
    _DERIVED[key] = (panel, out)
    return out


@dataclass(frozen=True)
class Config:
    start: str = "2015-01-01"
    end: str | None = None
    top_n: int = 6
    rebalance_days: int = 21
    lookback: int = 126
    skip: int = 10
    vol_window: int = 20
    max_weight: float = 0.06
    sector_cap: float = 0.40
    gross: float = 1.00
    regime_ma: int = 200
    regime_gross: float = 1.00
    stop: float = 1.00  # 1.0 = no trailing stop (ten-round optimum)
    reentry_days: int = 20
    cost_bps: float = 10.0
    min_dollar_vol: float = 5e6
    exec_price: str = "close"  # "close" or "open" (signal at close, fill next open)
    tax_rate: float = 0.0  # tax on net realized gains, with loss carryforward
    trade_band: float = 0.0  # skip rebalancing a name if target drift is below this
    leverage_band: float = 0.0  # only trim gross when it exceeds target by this much
    llm_active_share: float = 0.20
    # Margin: leverage 1.0 = no borrowing. Above 1.0 target weights are scaled and
    # negative cash pays `financing_bps` a year, accrued daily.
    leverage: float = 1.0
    financing_bps: float = 300.0
    leverage_mode: str = "constant"  # "constant" | "trend"
    leverage_down: float = 1.0  # exposure when the regime filter is off
    # Drawdown circuit breaker: at -dd_stop from the equity peak, cut gross to
    # dd_scale until the drawdown recovers to -dd_recover. 0 disables it.
    dd_stop: float = 0.0
    dd_scale: float = 0.5
    dd_recover: float = 0.10
    # Daily trend cut on the core ETF: below its trend_ma, scale to trend_scale.
    # 0 disables it. Unlike the monthly overlay, this is checked every day.
    trend_ma: int = 0
    trend_scale: float = 0.5
    # Default: dual-momentum rotation. Hold SMH while its 6-month return beats the
    # benchmark, otherwise run the momentum sleeve. SMH beat SOXX on the US and tech
    # books (results/core.md); SOXX still edges it in most of Europe, use --core SOXX.
    core: str | None = "SMH"
    core_weight: float = 1.0
    core_mode: str = "relative"  # "always" | "relative" | "relative_switch"
    core_lookback: int = 126
    core_alt: str | None = "SPY"
    fallback: str = "momentum"  # "momentum" or "sectors"
    fallback_sectors: tuple[str, ...] = (
        "XLK",
        "XLF",
        "XLE",
        "XLV",
        "XLI",
        "XLP",
        "XLU",
        "XLB",
        "XLY",
    )
    sector_top: int = 3


class Market:
    """Aligned daily matrices for one backtest."""

    def __init__(self, panel: pd.DataFrame, membership: pd.DataFrame, cfg: Config):
        self.cfg = cfg
        d = _derived(panel, cfg)
        px = d["px"]
        self.spy = d["spy"]
        # trading calendar = days SPY actually traded (drops partial/holiday rows)
        self.all_dates = px.index[px["SPY"].notna()]
        self.dates = self.all_dates
        if cfg.end:
            self.dates = self.dates[self.dates <= pd.Timestamp(cfg.end)]
        self.dates = self.dates[self.dates >= pd.Timestamp(cfg.start)]
        self.px = px
        self.raw_px = px
        self.vol = d["vol"]
        self.ret = d["ret"]
        self.sigma = d["sigma"]
        self.mom = d["mom"]
        self.dollar = d["dollar"]
        self.spy_ma = d["spy_ma"]
        core = cfg.core
        self.core_ma = (
            px[core].rolling(cfg.trend_ma, min_periods=cfg.trend_ma).mean()
            if cfg.trend_ma and core in px.columns
            else None
        )
        self.regime = self.spy > self.spy_ma
        self.members = self._membership(membership, panel)
        self.sectors = universe.sector_map()
        self.member_dates = membership["date"].sort_values().unique()
        self._elig: dict[pd.Timestamp, pd.DataFrame] = {}
        # numpy view for the inner accounting loop; date -> row
        self._px_arr = px.to_numpy(dtype=float, copy=False)
        if "open" in panel.columns:
            op = (
                panel.pivot(index="date", columns="ticker", values="open")
                .reindex(index=px.index, columns=px.columns)
            )
            self._px_open = op.to_numpy(dtype=float)
        else:
            self._px_open = self._px_arr
        self._px_row = {d: i for i, d in enumerate(px.index)}
        self._px_col = {t: i for i, t in enumerate(px.columns)}

    def _membership(
        self, membership: pd.DataFrame, panel: pd.DataFrame
    ) -> pd.DataFrame:
        key = (id(membership), id(panel))
        hit = _MEMBERS.get(key)
        if hit is not None and hit[0] is membership and hit[1] is panel:
            return hit[2]
        if membership.empty:
            return pd.DataFrame(False, index=self.all_dates, columns=[])
        # numpy, not pivot: the long frame has >1M (date, ticker) rows.
        # pandas factorize, not np.unique: sorting 1M strings dominates the build.
        dcode, dvals = pd.factorize(membership["date"], sort=True)
        tcode, tvals = pd.factorize(membership["ticker"])
        canon = np.array([universe.canonical(t) for t in tvals])
        cinv, cuniq = pd.factorize(canon)
        arr = np.zeros((len(dvals), len(cuniq)), dtype=bool)
        arr[dcode, cinv[tcode]] = True
        # every snapshot row is a complete constituent list, so no accumulation:
        # each panel date takes the latest snapshot on or before it
        pos = (
            np.searchsorted(np.asarray(dvals), self.all_dates.to_numpy(), side="right")
            - 1
        )
        out = np.zeros((len(self.all_dates), cuniq.size), dtype=bool)
        ok = pos >= 0
        out[ok] = arr[pos[ok]]
        mat = pd.DataFrame(out, index=self.all_dates, columns=cuniq)
        if len(_MEMBERS) > 4:
            _MEMBERS.clear()
        _MEMBERS[key] = (membership, panel, mat)
        return mat

    def members_at(self, date: pd.Timestamp) -> set[str]:
        if date not in self.members.index:
            return set()
        return set(self.members.columns[self.members.loc[date].values])


def eligible(mkt: Market, date: pd.Timestamp) -> pd.DataFrame:
    """Point-in-time eligible names, ranked by momentum. Cached per date: treat as read-only."""
    cache = getattr(mkt, "_elig", None)
    if cache is not None and date in cache:
        return cache[date]
    out = _eligible(mkt, date)
    if cache is not None:
        cache[date] = out
    return out


def _eligible(mkt: Market, date: pd.Timestamp) -> pd.DataFrame:
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
