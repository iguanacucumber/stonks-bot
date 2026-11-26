"""Tax-loss harvesting overlay for the US monthly EUR-ledger book.

France has no wash-sale ban. A French CTO investor can sell a losing lot at the
monthly rebalance and immediately buy the same shares back. The market exposure
is unchanged; what changes is the EUR cost basis and the annual PFU:

* the realised loss enters the current year's net capital gain and can offset
  other gains (then carries forward for 10 years);
* the re-entered lot has a lower basis, so a later sale realises a larger gain
  (or a smaller loss). This is mostly a **deferral**, not a permanent tax saving;
* every round trip pays two trading fees. A harvest that costs more than the
  time value of the deferred tax is not worth doing.

The overlay therefore implements only the mechanical tax rule, not a promise of
alpha. It selects lots by EUR basis, respects the engine's post-rebalance target
weights, applies a minimum notional band, caps turnover per date, and can be run
with all fees multiplied by 2 for the cost stress test.

The accounting is the merged `bot.eur_ledger` replay. The callback runs before
the engine's own fills on the same session, so a sell/re-entry does not change
the target-weight decision. It never reads a future price: the loss test uses
the session's EUR open, which is also the baseline fill price.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math

import numpy as np
import pandas as pd

from . import eur_ledger
from .evaluation import bootstrap as bootstrap_mod
from .evaluation import metrics as metrics_mod
from .evaluation import windows as windows_mod


@dataclass(frozen=True)
class HarvestConfig:
    threshold: float = 0.10
    reenter: bool = True
    band: float = 0.0
    max_turnover: float = 0.50
    min_trade_eur: float = 1.0
    fee_multiplier: float = 1.0

    def __post_init__(self) -> None:
        if not 0.0 <= self.threshold < 1.0:
            raise ValueError("threshold must be in [0, 1)")
        if not 0.0 <= self.band < 1.0:
            raise ValueError("band must be in [0, 1)")
        if not 0.0 < self.max_turnover <= 2.0:
            raise ValueError("max_turnover must be in (0, 2]")
        if self.min_trade_eur < 0:
            raise ValueError("min_trade_eur must be >= 0")
        if self.fee_multiplier < 0:
            raise ValueError("fee_multiplier must be >= 0")

    @property
    def label(self) -> str:
        re = "reenter" if self.reenter else "cash"
        cost = f"{self.fee_multiplier:g}x"
        if self.threshold <= 0:
            return f"baseline cost {cost}"
        return f"harvest -{self.threshold:.0%} {re} band {self.band:.0%} cost {cost}"


@dataclass
class HarvestStats:
    dates: int = 0
    names: int = 0
    sells: int = 0
    buys: int = 0
    sold_notional: float = 0.0
    bought_notional: float = 0.0
    realized_loss: float = 0.0
    fees: float = 0.0
    events: list[dict] = field(default_factory=list)


class HarvestStrategy:
    """Callback for `eur_ledger.replay`; also records a decision log."""

    def __init__(
        self,
        res,
        config: HarvestConfig,
        capital: float = 10_000.0,
        target_weights: pd.DataFrame | None = None,
    ):
        self.res = res
        self.cfg = config
        self.capital = float(capital)
        self.weights = res.weights if target_weights is None else target_weights
        self.stats = HarvestStats()
        reasons = res.trades.get("reason", pd.Series(dtype=object))
        self.dates = sorted(
            pd.to_datetime(res.trades.loc[reasons == "rebalance", "date"]).unique()
        )

    def _target(self, date) -> pd.Series | None:
        if date not in self.weights.index:
            return None
        row = self.weights.loc[date]
        if isinstance(row, pd.DataFrame):
            row = row.iloc[0]
        return row

    def __call__(self, date, pos, lots, prices, cash) -> list[dict]:
        cfg = self.cfg
        target = self._target(date)
        if target is None:
            return []
        equity = float(cash)
        for ticker, shares in pos.items():
            px = prices.get(ticker, np.nan)
            if np.isfinite(px):
                equity += shares * float(px)
        if not np.isfinite(equity) or equity <= 0:
            return []

        candidates: list[tuple[float, str, float, float]] = []
        for ticker, shares in pos.items():
            if shares <= 1e-12:
                continue
            # Only harvest names the rebalance still wants. Selling a name the
            # engine is about to exit would add two pointless fees.
            if float(target.get(ticker, 0.0)) <= 1e-9:
                continue
            px = prices.get(ticker, np.nan)
            if not np.isfinite(px) or px <= 0:
                continue
            loss_shares = 0.0
            loss_amount = 0.0
            for lot in lots.get(ticker, []):
                lot_shares, basis = float(lot[0]), float(lot[1])
                if lot_shares <= 1e-12 or not np.isfinite(basis) or basis <= 0:
                    continue
                if px <= basis * (1.0 - cfg.threshold):
                    take = min(lot_shares, shares)
                    loss_shares += take
                    loss_amount += (px - basis) * take
            if loss_shares <= 1e-12:
                continue
            notional = loss_shares * px
            if notional < cfg.band * equity or notional < cfg.min_trade_eur:
                continue
            candidates.append((loss_amount, ticker, loss_shares, px))

        # Largest tax loss first, then cap the harvested notional per date.
        candidates.sort(key=lambda x: x[0])
        cap = cfg.max_turnover * equity
        used = 0.0
        out: list[dict] = []
        self.stats.dates += 1
        for loss_amount, ticker, shares, px in candidates:
            notional = shares * px
            if used + notional > cap:
                continue
            used += notional
            out.append(
                {
                    "ticker": ticker,
                    "side": "SELL",
                    "shares": shares,
                    "price": px,
                }
            )
            if cfg.reenter:
                out.append(
                    {
                        "ticker": ticker,
                        "side": "BUY",
                        "shares": shares,
                        "price": px,
                    }
                )
            self.stats.names += 1
            self.stats.sells += 1
            self.stats.sold_notional += notional
            self.stats.realized_loss += loss_amount
            if cfg.reenter:
                self.stats.buys += 1
                self.stats.bought_notional += notional
            self.stats.events.append(
                {
                    "date": date,
                    "ticker": ticker,
                    "shares": shares,
                    "price": px,
                    "notional": notional,
                    "loss": loss_amount,
                    "reenter": cfg.reenter,
                }
            )
        return out


def rebalance_dates(res) -> list[pd.Timestamp]:
    """Unique execution dates of the engine's monthly target rebalances."""
    reasons = res.trades.get("reason", pd.Series(dtype=object))
    return sorted(
        pd.to_datetime(res.trades.loc[reasons == "rebalance", "date"]).unique()
    )


def annual_return(equity: pd.Series, year: int) -> float:
    e = equity.loc[str(year)]
    return float(e.iloc[-1] / e.iloc[0] - 1.0) if len(e) > 2 else float("nan")


def effective_exit_rate(led) -> float:
    gain = max(1.0, float(led.after_tax) - float(led.config.capital))
    return max(0.0, min(1.0, float(led.exit_tax) / gain))


def window_stats(equity: pd.Series) -> dict[str, float]:
    """5-year windows with the repo's terminal-gain tax approximation."""
    table = windows_mod.five_year_table(equity, tax_rate=0.314, include_partial=False)
    return dict(table.summary)


def summarize(
    led,
    baseline_led=None,
    strategy: HarvestStrategy | None = None,
    run_bootstrap: bool = True,
) -> dict:
    years = len(led.equity) / 252.0
    out = {
        "multiple": float(led.multiple),
        "after_tax": float(led.after_tax),
        "cagr": float(led.cagr),
        "maxdd": float(led.maxdd),
        "tax_paid": float(led.tax_paid),
        "exit_tax": float(led.exit_tax),
        "fees": float(led.fees),
        "dividend_tax": float(led.dividend_tax_total),
        "turnover_engine": float(
            led.trades["notional"].sum() / max(1.0, led.equity.mean()) / years
        )
        if len(led.trades)
        else 0.0,
        "harvest_notional": float(led.harvest_notional),
        "harvest_fees": float(led.harvest_fees),
        "harvest_count": int(led.harvest_count),
        "y2018": annual_return(led.equity, 2018),
        "y2022": annual_return(led.equity, 2022),
    }
    hs = strategy.stats if strategy is not None else HarvestStats()
    out.update(
        {
            "harvest_dates": hs.dates,
            "harvest_names": hs.names,
            "harvest_sells": hs.sells,
            "harvest_buys": hs.buys,
            "realized_loss": hs.realized_loss,
            "harvest_turnover": float(
                (led.harvest_notional / max(1.0, led.equity.mean()) / years)
            ),
        }
    )
    ws = window_stats(led.equity)
    out.update(
        {
            "windows": ws.get("n_windows", 0.0),
            "window_median": ws.get("median_multiple", float("nan")),
            "window_worst": ws.get("worst_multiple", float("nan")),
            "window_p5": ws.get("p5_multiple", float("nan")),
        }
    )
    if run_bootstrap:
        rets = led.equity.pct_change().dropna()
        boot = bootstrap_mod.bootstrap(
            rets,
            n_paths=10_000,
            method="stationary",
            block=21,
            expected_block=21,
            seed=7,
            exit_tax_rate=effective_exit_rate(led),
        )
        out["boot_p5"] = float(boot.summary()["terminal_p5"])
        out["boot_p5_overlay"] = float(boot.summary()["exit_tax_overlay_p5"])
    if baseline_led is not None:
        base = window_stats(baseline_led.equity)
        out["windows_vs_baseline"] = float(
            ws.get("median_multiple", float("nan"))
            - base.get("median_multiple", float("nan"))
        )
    return out


def run_variant(
    res,
    panel: pd.DataFrame,
    dividends: pd.DataFrame | None,
    config: HarvestConfig,
    capital: float = 10_000.0,
    baseline_led=None,
    run_bootstrap: bool = True,
) -> dict:
    cfg = eur_ledger.EurLedgerConfig(
        capital=capital,
        tax_rate=None,
        financing_bps=500.0,
        spread_bps=5.0,
        fx_bps=2.0,
        broker_bps=0.25,
        broker_fixed_eur=1.0,
        fee_multiplier=config.fee_multiplier,
    )
    strategy = HarvestStrategy(res, config, capital=capital)
    led = eur_ledger.replay(
        res,
        panel,
        "US",
        cfg,
        dividends=dividends,
        harvest=strategy if config.threshold > 0 else None,
        harvest_dates=strategy.dates,
    )
    out = summarize(
        led,
        baseline_led=baseline_led,
        strategy=strategy if config.threshold > 0 else None,
        run_bootstrap=run_bootstrap,
    )
    out["label"] = config.label
    out["threshold"] = config.threshold
    out["reenter"] = config.reenter
    out["band"] = config.band
    out["fee_multiplier"] = config.fee_multiplier
    out["ledger"] = led
    return out
