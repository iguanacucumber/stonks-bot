"""French tax/fee presets, broker tariffs, and EUR translation.

One source of truth for `bot/cli.py` and `scripts/france.py`. The backtest engine
does the accounting; this module only supplies the French configuration and the
after-tax summary.
"""

from __future__ import annotations

import pandas as pd

from . import data, markets

# Broker tariffs, Config fields only. `broker_grid`/`us_grid`: (max ticket in
# EUR, fixed fee in EUR). Above the last bracket the broker's bps rate applies.
# Tariffs checked 2026-11-26; sources and caveats live in BROKER_INFO, not here.
BROKERS = {
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
    # PEA and CTO. Tiered plan: 0.05% (min 1.25 EUR, max 29 EUR) on Euronext.
    # US fixed plan: USD 0.005/share, min USD 1; at USD 200/share = 0.25 bps.
    # FX: 0.002% manual conversion (min USD 2); 0.03% if auto-converted in-trade.
    # The 2 bps model is a conservative middle ground, not the published rate.
    "Interactive Brokers": dict(
        broker_grid=((2500, 1.25),),
        broker_bps=5.0,
        us_grid=((40000, 1.0),),
        us_bps=0.25,
        fx_bps=2.0,
        custody_bps=0.0,
    ),
    # Classic tariff; 2026 promo (0% on 70 PEA stocks) deliberately ignored.
    "Saxo Banque": dict(
        broker_grid=((2500, 2.0),),
        broker_bps=8.0,
        us_grid=((1250, 1.0),),
        us_bps=8.0,
        fx_bps=25.0,
        custody_bps=0.0,
    ),
    # PEA: 0% to EUR 100k/month, then 0.2%. CTO US: 0% commission, 0.5% FX.
    "XTB": dict(
        broker_grid=(),
        broker_bps=0.0,
        us_grid=(),
        us_bps=0.0,
        fx_bps=50.0,
        custody_bps=0.0,
    ),
    # PEA Starter: 0.35%, one order <= EUR 500 free each month (not modelled).
    # US Starter: 0.20%, min EUR 20, plus EUR 30 foreign-broker fee per order.
    "Fortuneo": dict(
        broker_grid=(),
        broker_bps=35.0,
        us_grid=((10000, 50.0),),
        us_bps=20.0,
        fx_bps=25.0,
        custody_bps=0.0,
    ),
    # Decouverte: EUR 1.99 to EUR 500, then 0.60%; PEA capped at 0.50% by law.
    # US CTO: 0.60%.
    "BoursoBank": dict(
        broker_grid=((500, 1.99),),
        broker_bps=50.0,
        us_grid=(),
        us_bps=60.0,
        fx_bps=25.0,
        custody_bps=0.0,
    ),
    # CTO only. EUR 1 brokerage + EUR 1 handling = EUR 2/order on Euronext/US.
    # Source checked 2026-11-26 says AutoFX 0.25%; the 10 bps below is the
    # study's requested assumption and is optimistic. See results/brokers.md.
    "DEGIRO": dict(
        broker_grid=((1e12, 2.0),),
        broker_bps=0.0,
        us_grid=((1e12, 2.0),),
        us_bps=0.0,
        fx_bps=10.0,
        custody_bps=0.0,
    ),
}

# Registry metadata: never merged into strategy.Config. `pea` gates --wrapper pea.
BROKER_INFO = {
    "Bourse Direct": dict(
        pea=True,
        source="https://www.boursedirect.fr/fr/tarifs",
        note="Euronext 0.99/1.90/2.90/3.80 EUR, 0.09% above 4.4k; "
        "US 8.50 EUR to 10k then 0.09%; 0.036%/yr custody on foreign names; "
        "0.25% FX modelled.",
    ),
    "Trade Republic": dict(
        pea=True,
        source="https://traderepublic.com/fr-fr/pea",
        note="EUR 1/order, no custody, ~0.25% FX on USD names.",
    ),
    "Interactive Brokers": dict(
        pea=True,
        source="https://www.interactivebrokers.ie/fr/pricing/commissions-stocks.php",
        note="Euronext 0.05%, min 1.25 EUR (tiered plan; fixed plan min 3 EUR); "
        "US USD 0.005/share, min USD 1; FX 0.002% manual / 0.03% auto, "
        "modelled at 2 bps; no custody.",
    ),
    "Saxo Banque": dict(
        pea=True,
        source="https://www.home.saxo/fr-fr/accounts/pea",
        note="Classic 0.08%, min EUR 2 Euronext / USD 1 US; 0.25% FX; "
        "no custody. 2026 promo (0% on 70 PEA stocks) ignored.",
    ),
    "XTB": dict(
        pea=True,
        source="https://www.xtb.com/fr/pea",
        note="PEA 0% to EUR 100k/month, then 0.2%; US CTO 0% commission, "
        "0.5% FX conversion; no custody.",
    ),
    "Fortuneo": dict(
        pea=True,
        source="https://www.fortuneo.fr/bourse",
        note="PEA Starter 0.35%, first order <= EUR 500 each month free "
        "(not modelled); US CTO 0.20%, min EUR 20, + EUR 30 foreign-broker "
        "fee (modelled as EUR 50 to 10k; the EUR 30 is omitted above 10k); "
        "0.25% FX; no custody.",
    ),
    "BoursoBank": dict(
        pea=True,
        source="https://www.boursobank.com/aide-en-ligne/bourse/comment-investir-en-bourse/fonctionnement-de-la-bourse/question/comment-sont-calcules-les-frais-de-courtage-43063140",
        note="Decouverte EUR 1.99 to EUR 500 then 0.60%, PEA capped at 0.50%; "
        "US CTO 0.60%; 0.25% FX; no custody.",
    ),
    "DEGIRO": dict(
        pea=False,
        source="https://degiro.fr/data/pdf/fr/Tarifs.pdf",
        note="CTO only, no PEA. EUR 2/order (1 EUR brokerage + 1 EUR "
        "handling) on Euronext and US; AutoFX is 0.25% in the 2026 tariff, "
        "but the study models 10 bps and reports a 25 bps sensitivity.",
    ),
}

PEA_BROKERS = tuple(name for name, info in BROKER_INFO.items() if info["pea"])

ALIASES = {
    "bourse-direct": "Bourse Direct",
    "trade-republic": "Trade Republic",
    "interactive-brokers": "Interactive Brokers",
    "ibkr": "Interactive Brokers",
    "saxo": "Saxo Banque",
    "xtb": "XTB",
    "fortuneo": "Fortuneo",
    "boursobank": "BoursoBank",
    "degiro": "DEGIRO",
}

# The CLI picks this automatically for --country fr. Other presets stay for
# research/comparison, but the default is the lowest all-in cost for the US core.
DEFAULT_BROKER = "Interactive Brokers"
DEFAULT_MARKET = "TECH"

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


def require_pea_broker(broker: str) -> str:
    """Return the canonical broker name or raise if it has no PEA."""
    name = broker_name(broker)
    if name not in PEA_BROKERS:
        allowed = ", ".join(sorted(PEA_BROKERS))
        raise ValueError(f"{name} does not offer a PEA. PEA brokers: {allowed}.")
    return name


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
