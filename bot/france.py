"""French tax/fee presets, broker tariffs, and EUR translation.

One source of truth for `bot/cli.py` and `scripts/france.py`. The backtest engine
does the accounting; this module only supplies the French configuration and the
after-tax summary.
"""

from __future__ import annotations

import pandas as pd

from . import data, markets

# Broker tariffs. `broker_grid`/`us_grid`: (max ticket in EUR, fixed fee in EUR).
# Above the last bracket the broker's bps rate applies.
BROKERS = {
    "Interactive Brokers": dict(
        # PEA: 0.05%, min 1.25 EUR; US: 0.005 USD/share, min 1 USD (~0.25 bps);
        # FX 0.002%. Lowest all-in cost for the US core.
        broker_grid=((2500, 1.25),),
        broker_bps=5.0,
        us_grid=((40000, 1.0),),
        us_bps=0.25,
        fx_bps=2.0,
        custody_bps=0.0,
    ),
    "Bourse Direct": dict(
        broker_grid=((500, 0.99), (1000, 1.90), (2000, 2.90), (4400, 3.80)),
        broker_bps=9.0,
        us_grid=((10000, 8.50),),
        us_bps=9.0,
        fx_bps=25.0,
        custody_bps=3.6,
    ),
    "Trade Republic": dict(
        broker_grid=((1e12, 1.0),),
        broker_bps=0.0,
        us_grid=((1e12, 1.0),),
        us_bps=0.0,
        fx_bps=25.0,
        custody_bps=0.0,
    ),
}

ALIASES = {
    "bourse-direct": "Bourse Direct",
    "trade-republic": "Trade Republic",
    "interactive-brokers": "Interactive Brokers",
    "ibkr": "Interactive Brokers",
}

# The CLI picks this automatically for --country fr. Other presets stay for
# research/comparison, but the default is the lowest all-in cost for the US core.
DEFAULT_BROKER = "Interactive Brokers"

CURRENCY = {
    "US": "USD",
    "TECH": "USD",
    "FR": "EUR",
    "DE": "EUR",
    "ES": "EUR",
    "IT": "EUR",
    "NL": "EUR",
    "GB": "GBP",
    "CH": "CHF",
}


def broker_name(broker: str) -> str:
    return ALIASES.get(broker, broker)


def broker_kwargs(broker: str, capital: float) -> dict:
    return dict(BROKERS[broker_name(broker)], initial_capital=float(capital))


def us_tickers(market: str, panel: pd.DataFrame | None = None) -> frozenset[str]:
    if market in ("US", "TECH"):
        if panel is not None:
            return frozenset(panel["ticker"].unique())
        return frozenset(
            set(markets.UNIVERSES["TECH"]) | set(markets.CORE_SYMBOLS) | {"SPY", "QQQ"}
        )
    # a local market's only USD holdings are the exported core ETFs
    return frozenset(markets.CORE_SYMBOLS)


def tax_kwargs(
    market: str, wrapper: str = "cto", panel: pd.DataFrame | None = None
) -> dict:
    return dict(
        tax_country="fr_pea" if wrapper.lower() == "pea" else "fr",
        tax_lots="hifo",
        wash_sale_days=0,  # no US wash-sale rule in France
        us_tickers=us_tickers(market, panel),
        ftt_tickers=(
            frozenset(markets.UNIVERSES["FR"]) if market == "FR" else frozenset()
        ),
    )


def kwargs(
    market: str,
    broker: str = DEFAULT_BROKER,
    wrapper: str = "cto",
    capital: float = 10000.0,
    panel: pd.DataFrame | None = None,
) -> dict:
    out = broker_kwargs(broker, capital)
    out.update(tax_kwargs(market, wrapper, panel))
    # 5 bps market spread; broker commissions/FX/FTT are charged separately.
    out["cost_bps"] = 5.0
    return out


def ticket_fee(broker: str, notional: float, is_us: bool) -> float:
    b = BROKERS[broker_name(broker)]
    grid = b["us_grid"] if is_us else b["broker_grid"]
    for limit, fee in grid:
        if notional <= limit:
            return float(fee)
    rate = b["us_bps"] if is_us else b["broker_bps"]
    return notional * rate / 1e4


def eur_per_unit(market: str) -> pd.Series | None:
    """EUR value of one unit of the market's currency, by date."""
    spec = markets.MARKETS[market]
    if spec["fx"] == "EUR":
        return None
    eurusd = data.fetch_one("EURUSD=X").set_index("date")["adjclose"].sort_index()
    if spec["fx"] == "USD":
        return 1.0 / eurusd
    if spec["fx"] == "GBP":
        gbpusd = data.fetch_one("GBPUSD=X").set_index("date")["adjclose"].sort_index()
        return gbpusd / eurusd
    if spec["fx"] == "CHF":
        usdchf = data.fetch_one("USDCHF=X").set_index("date")["adjclose"].sort_index()
        return eurusd / usdchf
    raise ValueError(spec["fx"])


def to_eur(panel: pd.DataFrame, market: str) -> pd.DataFrame:
    factor = eur_per_unit(market)
    if factor is None:
        return panel
    p = panel.copy()
    dates = pd.DatetimeIndex(p["date"])
    f = factor.reindex(dates).ffill().to_numpy()
    for c in ("open", "close", "adjclose"):
        if c in p.columns:
            p[c] = p[c].to_numpy() * f
    return p


def fx_factor(market: str, start, end) -> float:
    factor = eur_per_unit(market)
    if factor is None:
        return 1.0
    return float(factor.asof(end) / factor.asof(start))


def after_tax(res, capital: float, market: str) -> dict:
    """After-tax summary. Native amounts in the market currency, plus EUR."""
    fx = fx_factor(market, res.equity.index[0], res.equity.index[-1])
    pre_native = float(res.equity.iloc[-1]) * capital
    exit_native = float(res.unrealized_tax) * capital
    after_native = pre_native - exit_native
    years = len(res.equity) / 252.0
    return {
        "market": market,
        "currency": CURRENCY.get(market, markets.MARKETS[market]["fx"]),
        "capital": float(capital),
        "fx": fx,
        "years": years,
        "pre_tax_native": pre_native,
        "pre_tax_eur": pre_native * fx,
        "fees_native": float(res.fees_paid) * capital,
        "fees_eur": float(res.fees_paid) * capital * fx,
        "ftt_native": float(res.ftt_paid) * capital,
        "ftt_eur": float(res.ftt_paid) * capital * fx,
        "tax_paid_native": float(res.tax_paid) * capital,
        "tax_paid_eur": float(res.tax_paid) * capital * fx,
        "exit_tax_native": exit_native,
        "exit_tax_eur": exit_native * fx,
        "after_tax_native": after_native,
        "after_tax_eur": after_native * fx,
        "cagr_native": (after_native / capital) ** (1.0 / years) - 1.0,
        "cagr_eur": (after_native * fx / capital) ** (1.0 / years) - 1.0,
        "maxdd": float((res.equity / res.equity.cummax() - 1.0).min()),
        "trades": len(res.trades),
    }
