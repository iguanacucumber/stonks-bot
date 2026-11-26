"""Investor profiles, concrete allocations and a deterministic lifecycle simulator.

This is the user-facing layer above the research books. It does not run a new
backtest and it never sends orders. Every number that comes from history is
labelled as a backtest; the ``balanced`` and ``conservative`` profiles are
post-hoc / lower-leverage choices, not forward evidence.

The only live-data dependency is :func:`current_allocation`, which reuses the
same snapshot and selector as ``scripts/live.py``. Callers can inject target
weights and prices to keep the rest of the module pure and testable.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

from . import france

WINDOW_START = "2015-01-02"
WINDOW_END = "2026-11-25"
WINDOW_YEARS = (
    date.fromisoformat(WINDOW_END) - date.fromisoformat(WINDOW_START)
).days / 365.25

PROFILE_NAMES = ("max_growth", "balanced", "conservative")
WRAPPERS = ("cto", "pea")
DEFAULT_BROKER = france.DEFAULT_BROKER
DEFAULT_CAPITAL = 10_000.0


@dataclass(frozen=True)
class ProfileSpec:
    """One documented allocation and its historical evidence."""

    name: str
    label: str
    allocation: str
    market: str
    leverage: float
    core_share: float
    spy_share: float
    eur_multiple: float
    net_multiple: float
    max_drawdown: float
    return_2022: float | None
    status: str
    note: str
    cagr_override: float | None = None

    def __post_init__(self) -> None:
        if self.leverage <= 0:
            raise ValueError("leverage must be > 0")
        if self.core_share < 0 or self.spy_share < 0:
            raise ValueError("profile shares must be non-negative")
        if self.core_share + self.spy_share <= 0:
            raise ValueError("a profile needs at least one sleeve")
        if self.net_multiple <= 0 or self.eur_multiple <= 0:
            raise ValueError("profile multiples must be positive")
        if self.max_drawdown > 0:
            raise ValueError("max_drawdown must be negative or zero")

    @property
    def cagr(self) -> float:
        if self.cagr_override is not None:
            return float(self.cagr_override)
        return float(self.net_multiple ** (1.0 / WINDOW_YEARS) - 1.0)

    @property
    def cagr_range(self) -> tuple[float, float]:
        """Historical planning band: full-window CAGR down to a 15 pp stress."""
        return (max(0.0, self.cagr - 0.15), self.cagr)

    @property
    def maxdd_range(self) -> tuple[float, float]:
        """Historical maxDD to a 10 pp milder planning case (negative values)."""
        return (self.max_drawdown, min(0.0, self.max_drawdown + 0.10))

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out.update(
            cagr=self.cagr,
            cagr_range=list(self.cagr_range),
            maxdd_range=list(self.maxdd_range),
        )
        return out


# Values are copied from docs/profiles.md (EUR ledger, net-of-dividend-tax).
# If the risk workstream changes its final gates, update these constants and the
# docs/report together; no other code should hold a second copy.
MAX_GROWTH = ProfileSpec(
    name="max_growth",
    label="Maximum growth",
    allocation="100% US monthly book, 2.5x leverage",
    market="US",
    leverage=2.5,
    core_share=1.0,
    spy_share=0.0,
    eur_multiple=66.69,
    net_multiple=61.64,
    max_drawdown=-0.567,
    return_2022=-0.332,
    status="backtest (EUR ledger, net of modelled dividend tax)",
    note="Best historical terminal wealth, but a -57% drawdown is plausible.",
)
BALANCED = ProfileSpec(
    name="balanced",
    label="Balanced",
    allocation="70% US monthly book + 30% SPY, A6 70/30, total gross 2.05",
    market="US",
    leverage=2.5,
    core_share=0.70,
    spy_share=0.30,
    eur_multiple=36.85,
    net_multiple=34.36,
    max_drawdown=-0.460,
    return_2022=-0.274,
    status="post-hoc paper opt-in (A6); not forward evidence",
    note="Gives up roughly half the terminal wealth for about 11 pp less drawdown.",
)
CONSERVATIVE = ProfileSpec(
    name="conservative",
    label="Conservative",
    allocation="100% US monthly book at 1.5x leverage",
    market="US",
    leverage=1.5,
    core_share=1.0,
    spy_share=0.0,
    eur_multiple=19.44,
    net_multiple=18.51,
    max_drawdown=-0.402,
    return_2022=-0.203,
    status="post-hoc lower-leverage opt-in; not a new strategy",
    note="Mostly a leverage dial: same SMH core, less borrowing, less return.",
)
PEA_PROXY = ProfileSpec(
    name="pea_proxy",
    label="PEA Nasdaq-100 proxy",
    allocation="100% PUST.PA (Amundi PEA Nasdaq-100 UCITS ETF, 1x)",
    market="FR",
    leverage=1.0,
    core_share=1.0,
    spy_share=0.0,
    eur_multiple=5.08,
    net_multiple=5.08,
    max_drawdown=-0.27,
    return_2022=None,
    status="PEA proxy from results/pea_ucits.md; not the CTO profile",
    note="PEA cannot hold SMH or borrow at account level. This is a satellite, "
    "materially below the CTO book's historical return.",
    cagr_override=0.144,
)

PROFILES: dict[str, ProfileSpec] = {
    "max_growth": MAX_GROWTH,
    "balanced": BALANCED,
    "conservative": CONSERVATIVE,
}


def profile_spec(name: str) -> ProfileSpec:
    try:
        return PROFILES[name]
    except KeyError as exc:
        raise ValueError(f"unknown profile {name!r}; choose {PROFILE_NAMES}") from exc


def _fraction(value: float, name: str) -> float:
    if not math.isfinite(float(value)) or not 0.0 <= float(value) <= 1.0:
        raise ValueError(f"{name} must be a fraction in [0, 1], got {value!r}")
    return float(value)


@dataclass(frozen=True)
class InvestorProfile:
    """The five answers a user can actually give before investing."""

    horizon_years: float = 10.0
    drawdown_tolerance: float = 0.50
    capital: float = DEFAULT_CAPITAL
    wrapper: str = "cto"
    broker: str = DEFAULT_BROKER
    income_need: float = 0.0

    def __post_init__(self) -> None:
        if not math.isfinite(float(self.horizon_years)) or self.horizon_years <= 0:
            raise ValueError("horizon_years must be > 0")
        if self.horizon_years > 60:
            raise ValueError("horizon_years must be <= 60")
        object.__setattr__(
            self,
            "drawdown_tolerance",
            _fraction(self.drawdown_tolerance, "drawdown_tolerance"),
        )
        if self.drawdown_tolerance <= 0:
            raise ValueError("drawdown_tolerance must be > 0")
        if not math.isfinite(float(self.capital)) or self.capital <= 0:
            raise ValueError("capital must be > 0")
        wrapper = str(self.wrapper).lower()
        if wrapper not in WRAPPERS:
            raise ValueError(f"wrapper must be one of {WRAPPERS}")
        object.__setattr__(self, "wrapper", wrapper)
        broker = france.broker_name(str(self.broker))
        if broker not in france.BROKERS:
            raise ValueError(
                f"unknown broker {self.broker!r}; choose {sorted(france.BROKERS)}"
            )
        if wrapper == "pea":
            try:
                broker = france.require_pea_broker(broker)
            except ValueError as exc:
                raise ValueError(str(exc)) from exc
        object.__setattr__(self, "broker", broker)
        if not math.isfinite(float(self.income_need)) or self.income_need < 0:
            raise ValueError("income_need must be >= 0")

    @property
    def is_decumulation(self) -> bool:
        return self.income_need > 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def select_profile(investor: InvestorProfile, requested: str | None = None) -> str:
    """Map the five answers to one documented profile. Explicit choice wins."""
    if requested:
        if requested not in PROFILES:
            raise ValueError(f"unknown profile {requested!r}; choose {PROFILE_NAMES}")
        return requested
    if investor.wrapper == "pea":
        return "conservative"
    if investor.horizon_years < 8 or investor.drawdown_tolerance < 0.35:
        return "conservative"
    if investor.drawdown_tolerance < 0.55 or investor.is_decumulation:
        return "balanced"
    return "max_growth"


def decision_tree() -> list[str]:
    return [
        "horizon < 8 years OR max tolerated drawdown < 35% -> conservative",
        "drawdown tolerance < 55% OR income need > 0 -> balanced",
        "long horizon and >= 55% drawdown tolerance -> max_growth (default US)",
        "PEA -> PEA Nasdaq-100 proxy (PUST.PA, 1x), because US/SMH leverage is ineligible",
    ]


@dataclass(frozen=True)
class Order:
    ticker: str
    side: str
    qty: float
    price: float
    currency: str
    weight: float
    notional_native: float
    notional_eur: float
    estimated_fee_eur: float
    reason: str = "initial allocation"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TaxEstimate:
    wrapper: str
    rate: float
    horizon_years: float
    projected_after_tax: float
    implied_gross_gain: float
    implied_tax: float
    note: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AllocationSnapshot:
    session: str
    target_weights: dict[str, float]
    prices: dict[str, float]
    fx_rate: float
    source: str
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Recommendation:
    profile: str
    requested_profile: str | None
    label: str
    allocation: str
    market: str
    wrapper: str
    broker: str
    capital: float
    horizon_years: float
    drawdown_tolerance: float
    target_weights: dict[str, float]
    expected_cagr_range: tuple[float, float]
    maxdd_range: tuple[float, float]
    tax_estimate: TaxEstimate
    orders: tuple[Order, ...]
    warnings: tuple[str, ...]
    evidence_status: str
    as_of: str | None = None
    fx_rate: float = 1.0
    source: str = "static fallback"

    @property
    def order_list(self) -> tuple[Order, ...]:
        return self.orders

    @property
    def tax(self) -> TaxEstimate:
        return self.tax_estimate

    @property
    def gross(self) -> float:
        return float(sum(self.target_weights.values()))

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "orders": [o.to_dict() for o in self.orders],
            "tax_estimate": self.tax_estimate.to_dict(),
            "expected_cagr_range": list(self.expected_cagr_range),
            "maxdd_range": list(self.maxdd_range),
            "gross": self.gross,
            "order_list": [o.to_dict() for o in self.orders],
        }


def tax_rate_for(wrapper: str, horizon_years: float) -> float:
    wrapper = str(wrapper).lower()
    if wrapper == "cto":
        return 0.314  # PFU 2026: 12.8% IR + 18.6% social
    if wrapper == "pea":
        return 0.186 if horizon_years >= 5 else 0.30
    raise ValueError(f"unknown wrapper {wrapper!r}")


def estimate_tax(
    spec: ProfileSpec,
    capital: float,
    wrapper: str = "cto",
    horizon_years: float = 10.0,
) -> TaxEstimate:
    """Implied tax burden from a net-of-tax multiple.

    If the net value is ``V`` and the tax rate is ``t``, the gross gain is
    ``(V - capital) / (1 - t)`` and the implied tax is ``gross_gain * t``. This is
    a transparent terminal estimate, not a lot-by-lot tax return.
    """
    if capital <= 0:
        raise ValueError("capital must be positive")
    rate = tax_rate_for(wrapper, horizon_years)
    after = float(capital) * (1.0 + spec.cagr) ** float(horizon_years)
    after = max(float(capital), after)
    gross_gain = (after - float(capital)) / max(1e-12, 1.0 - rate)
    tax = gross_gain * rate
    return TaxEstimate(
        wrapper=str(wrapper).lower(),
        rate=rate,
        horizon_years=float(horizon_years),
        projected_after_tax=after,
        implied_gross_gain=gross_gain,
        implied_tax=tax,
        note="Terminal estimate implied by the net multiple; actual tax depends "
        "on lots, realized gains and the year of sale.",
    )


def _latest_cached_session() -> str:
    """Last cached SPY close, avoiding an exchange-holiday false positive."""
    from . import data

    path = data.BARS / "SPY.csv"
    if not path.exists():
        raise FileNotFoundError(f"no cached SPY bars at {path}")
    import pandas as pd

    df = pd.read_csv(path, usecols=["date"])
    if df.empty:
        raise ValueError("cached SPY bars are empty")
    return str(pd.to_datetime(df["date"]).max().date())


def _cached_close(ticker: str, as_of: str | None = None) -> float | None:
    from . import data

    path = data.BARS / f"{ticker.replace('/', '_')}.csv"
    if not path.exists():
        return None
    import pandas as pd

    try:
        df = pd.read_csv(path, usecols=["date", "adjclose"])
        if as_of:
            df = df[pd.to_datetime(df["date"]) <= pd.Timestamp(as_of)]
        if df.empty:
            return None
        return float(df["adjclose"].iloc[-1])
    except Exception:
        return None


def fallback_weights(name: str) -> dict[str, float]:
    """Static allocator used only when the committed cache is unavailable."""
    if name == "balanced":
        return {"SMH": 2.5 * 0.70, "SPY": 0.30}
    if name == "conservative":
        return {"SMH": 1.5}
    if name == "max_growth":
        return {"SMH": 2.5}
    if name == "pea_proxy":
        return {"PUST.PA": 1.0}
    raise ValueError(f"unknown profile {name!r}")


def current_allocation(
    profile: str,
    *,
    as_of: str | None = None,
    panel=None,
    membership=None,
) -> AllocationSnapshot:
    """Current target from the same snapshot/selector as the live planner.

    The selector is re-used, not reimplemented. The daily 200-day core cut is
    applied to the target exactly as the fresh live account would apply it.
    """
    from . import profiles as profile_selectors
    from .live.config import LiveConfig
    from .live.signal import (
        build_snapshot,
        eur_fx,
        strategy_targets,
        trend_breach,
    )

    spec = profile_spec(profile)
    cfg = LiveConfig(
        book="monthly",
        market="US",
        leverage=2.5,
        trend_ma=200,
        trend_scale=0.5,
        dd_stop=0.0,
    )
    session = as_of or _latest_cached_session()
    snap = build_snapshot(cfg, session, panel=panel, membership=membership)
    # Use the merged profile selector for balanced; the shipped selector is the
    # fallback for max_growth/conservative. This keeps the advisor and
    # `bot.cli backtest --profile balanced` on exactly the same weights.
    selector = profile_selectors.selector_for(spec.name)
    if selector is None:
        base = strategy_targets(snap)
    else:
        base = {
            t: float(w)
            for t, w in selector(snap.mkt, snap.session, [], set()).items()
            if float(w) > 1e-12
        }
    if not base:
        raise ValueError("the live selector returned an empty target book")
    breach, _, _ = trend_breach(snap)
    scale = float(cfg.trend_scale) if breach else 1.0
    weights = {t: w * spec.leverage * scale for t, w in base.items()}
    prices = snap.closes()
    fx = eur_fx(cfg, snap.session).get("rate")
    warnings = []
    if breach:
        warnings.append(
            "SMH is below its 200-day MA: the target is halved, matching the "
            "shipped daily risk rule."
        )
    return AllocationSnapshot(
        session=str(snap.session.date()),
        target_weights={t: float(w) for t, w in weights.items() if w > 1e-12},
        prices={t: float(p) for t, p in prices.items() if p > 0},
        fx_rate=1.0 if fx is None else float(fx),
        source=f"live selector snapshot {snap.session.date()}",
        warnings=tuple(warnings),
    )


def build_orders(
    target_weights: dict[str, float],
    capital: float,
    prices: dict[str, float],
    *,
    wrapper: str = "cto",
    broker: str = DEFAULT_BROKER,
    fx_rate: float = 1.0,
    min_notional_eur: float = 1.0,
) -> tuple[tuple[Order, ...], tuple[str, ...]]:
    """Turn target weights into a fresh-account order list.

    ``capital`` is EUR. Weights are applied to the account value in each asset's
    native currency: for USD assets the EUR capital is converted at ``fx_rate``
    (EUR per USD). No existing holdings are assumed.
    """
    if capital <= 0:
        raise ValueError("capital must be positive")
    if fx_rate <= 0 or not math.isfinite(float(fx_rate)):
        raise ValueError("fx_rate must be positive")
    wrapper = str(wrapper).lower()
    if wrapper not in WRAPPERS:
        raise ValueError(f"unknown wrapper {wrapper!r}")
    broker = france.broker_name(broker)
    if wrapper == "pea":
        france.require_pea_broker(broker)
    orders: list[Order] = []
    warnings: list[str] = []
    for ticker, weight in sorted(target_weights.items(), key=lambda kv: -kv[1]):
        weight = float(weight)
        if weight <= 0:
            continue
        px = prices.get(ticker)
        if px is None or not math.isfinite(float(px)) or float(px) <= 0:
            warnings.append(f"{ticker}: no usable current price; order omitted")
            continue
        currency = (
            "EUR" if ticker.endswith((".PA", ".AS", ".DE", ".MI", ".MC")) else "USD"
        )
        native_capital = capital if currency == "EUR" else capital / fx_rate
        notional_native = weight * native_capital
        notional_eur = notional_native * (1.0 if currency == "EUR" else fx_rate)
        if notional_eur < min_notional_eur:
            continue
        qty = notional_native / float(px)
        qty = math.floor(qty * 1e6) / 1e6
        if qty <= 0:
            continue
        fee = france.ticket_fee(broker, notional_eur, currency == "USD")
        orders.append(
            Order(
                ticker=ticker,
                side="BUY",
                qty=qty,
                price=float(px),
                currency=currency,
                weight=weight,
                notional_native=notional_native,
                notional_eur=notional_eur,
                estimated_fee_eur=float(fee),
            )
        )
    return tuple(orders), tuple(warnings)


def recommend(
    investor: InvestorProfile,
    profile: str | None = None,
    *,
    as_of: str | None = None,
    target_weights: dict[str, float] | None = None,
    prices: dict[str, float] | None = None,
    fx_rate: float | None = None,
    panel=None,
    membership=None,
) -> Recommendation:
    """Choose a profile and return allocation, tax estimate and fresh orders."""
    requested = profile
    name = select_profile(investor, profile)
    spec = profile_spec(name)
    warnings: list[str] = []
    source = "static fallback"
    actual_as_of = as_of
    actual_fx = 1.0 if fx_rate is None else float(fx_rate)

    pea = investor.wrapper == "pea"
    if pea:
        spec = PEA_PROXY
        warnings.append(
            "PEA cannot hold SMH directly or borrow at account level; the advisor "
            "uses the PEA Nasdaq-100 proxy PUST.PA at 1x, not the selected CTO book."
        )
        if target_weights is None:
            target_weights = fallback_weights("pea_proxy")
        if prices is None:
            px = _cached_close("PUST.PA", as_of)
            prices = {"PUST.PA": px} if px else {}
        actual_fx = 1.0

    if target_weights is None:
        try:
            snap = current_allocation(
                name, as_of=as_of, panel=panel, membership=membership
            )
            target_weights = snap.target_weights
            if prices is None:
                prices = snap.prices
            if fx_rate is None:
                actual_fx = snap.fx_rate
            actual_as_of = snap.session
            source = snap.source
            warnings.extend(snap.warnings)
        except Exception as exc:
            target_weights = fallback_weights(name)
            source = f"static fallback ({type(exc).__name__}: {exc})"
            warnings.append(
                "Live cache unavailable; using a static allocation. Re-run with "
                "the committed cache for today's actual target."
            )
    if target_weights is None:
        raise ValueError("no target weights available")
    if prices is None:
        prices = {}
    if fx_rate is not None:
        actual_fx = float(fx_rate)

    orders, order_warnings = build_orders(
        target_weights,
        investor.capital,
        prices,
        wrapper=investor.wrapper,
        broker=investor.broker,
        fx_rate=actual_fx,
    )
    warnings.extend(order_warnings)
    if spec.name in ("balanced", "conservative"):
        warnings.append(f"{spec.name} is {spec.status}; use it as an explicit choice.")
    if spec.name == "max_growth":
        warnings.append(
            "max_growth uses leverage; a -57% peak-to-trough loss occurred in the "
            "historical EUR ledger. Only use money you can leave invested."
        )
    if investor.drawdown_tolerance < abs(spec.max_drawdown):
        warnings.append(
            f"Your drawdown tolerance ({investor.drawdown_tolerance:.0%}) is below "
            f"this profile's historical maxDD ({spec.max_drawdown:.1%})."
        )
    if sum(target_weights.values()) > 1.0:
        warnings.append(
            f"Gross target is {sum(target_weights.values()):.2f}x capital; "
            "this requires a margin-capable CTO, not a cash account or PEA."
        )
    if not orders:
        warnings.append("No orders generated: target/prices are unavailable.")
    warnings.append(
        "Historical backtest only; no forward evidence and no return promise. "
        "This is not investment advice."
    )
    tax = estimate_tax(spec, investor.capital, investor.wrapper, investor.horizon_years)
    return Recommendation(
        profile=spec.name if pea else name,
        requested_profile=requested,
        label=spec.label,
        allocation=spec.allocation,
        market=spec.market,
        wrapper=investor.wrapper,
        broker=investor.broker,
        capital=investor.capital,
        horizon_years=investor.horizon_years,
        drawdown_tolerance=investor.drawdown_tolerance,
        target_weights={t: float(w) for t, w in target_weights.items() if w > 0},
        expected_cagr_range=spec.cagr_range,
        maxdd_range=spec.maxdd_range,
        tax_estimate=tax,
        orders=orders,
        warnings=tuple(dict.fromkeys(warnings)),
        evidence_status=spec.status,
        as_of=actual_as_of,
        fx_rate=actual_fx,
        source=source,
    )


@dataclass(frozen=True)
class LifecycleRow:
    year: int
    start_balance: float
    contribution: float
    withdrawal_gross: float
    withdrawal_tax: float
    withdrawal_net: float
    investment_return: float
    end_balance: float
    end_real: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LifecycleProjection:
    profile: str
    wrapper: str
    scenario: str
    capital: float
    years: int
    inflation: float
    annual_contribution: float
    annual_withdrawal: float
    rows: tuple[LifecycleRow, ...]
    terminal_nominal: float
    terminal_real: float
    terminal_liquidation_tax: float
    terminal_after_tax: float
    total_tax_paid: float
    total_withdrawals: float
    depletion_year: int | None
    note: str = (
        "Deterministic projection using a historical/planning return, not a "
        "forecast or a Monte Carlo result."
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "rows": [r.to_dict() for r in self.rows],
        }


def _lifecycle_return(spec: ProfileSpec, scenario: str) -> float:
    scenario = scenario.lower()
    if scenario == "base":
        return spec.cagr
    if scenario == "stress":
        return max(0.0, spec.cagr - 0.15)
    if scenario == "adverse":
        return max(0.0, spec.cagr - 0.30)
    raise ValueError("scenario must be base, stress or adverse")


def project_lifecycle(
    profile: str,
    capital: float,
    *,
    years: int = 15,
    annual_contribution: float = 0.0,
    annual_withdrawal: float = 0.0,
    inflation: float = 0.025,
    wrapper: str = "cto",
    scenario: str = "base",
    return_override: float | None = None,
    spec: ProfileSpec | None = None,
) -> LifecycleProjection:
    """Accumulation or decumulation with inflation and a simple tax basis.

    Withdrawals are gross, inflation-indexed, and taxed on the gain fraction of
    the account. The annual return is the profile's net-of-tax historical CAGR,
    optionally replaced by a deterministic scenario. This is a planning tool,
    not a promise.
    """
    if years < 1 or years > 60:
        raise ValueError("years must be in [1, 60]")
    if capital <= 0:
        raise ValueError("capital must be positive")
    if annual_contribution < 0 or annual_withdrawal < 0:
        raise ValueError("contributions and withdrawals must be >= 0")
    if not math.isfinite(float(inflation)) or inflation <= -1.0:
        raise ValueError("inflation must be > -1")
    wrapper = str(wrapper).lower()
    if wrapper not in WRAPPERS:
        raise ValueError(f"unknown wrapper {wrapper!r}")
    use_spec = spec or (PEA_PROXY if wrapper == "pea" else profile_spec(profile))
    ret = (
        float(return_override)
        if return_override is not None
        else _lifecycle_return(use_spec, scenario)
    )
    if ret <= -1.0 or not math.isfinite(ret):
        raise ValueError("return must be finite and > -100%")
    balance = float(capital)
    basis = float(capital)
    rows: list[LifecycleRow] = []
    total_tax = 0.0
    total_withdrawals = 0.0
    depletion_year: int | None = None
    for year in range(1, int(years) + 1):
        start = balance
        contrib = float(annual_contribution)
        gross_withdrawal = float(annual_withdrawal) * (1.0 + inflation) ** (year - 1)
        available = start + contrib
        if gross_withdrawal > available:
            gross_withdrawal = max(0.0, available)
            depletion_year = depletion_year or year
        gain_fraction = max(0.0, 1.0 - basis / start) if start > 0 else 0.0
        tax = gross_withdrawal * gain_fraction * tax_rate_for(wrapper, year)
        net_withdrawal = gross_withdrawal - tax
        # A withdrawal returns basis proportionally; contributions add new basis.
        if start > 0:
            basis -= gross_withdrawal * min(1.0, basis / start)
        basis = max(0.0, basis) + contrib
        balance = start + contrib - gross_withdrawal - tax
        if balance < 0:
            balance = 0.0
        balance *= 1.0 + ret
        end_real = balance / (1.0 + inflation) ** year
        rows.append(
            LifecycleRow(
                year=year,
                start_balance=start,
                contribution=contrib,
                withdrawal_gross=gross_withdrawal,
                withdrawal_tax=tax,
                withdrawal_net=net_withdrawal,
                investment_return=ret,
                end_balance=balance,
                end_real=end_real,
            )
        )
        total_tax += tax
        total_withdrawals += gross_withdrawal
        if balance <= 1e-12 and depletion_year is None:
            depletion_year = year
    terminal = balance
    terminal_tax = max(0.0, terminal - basis) * tax_rate_for(wrapper, years)
    return LifecycleProjection(
        profile=use_spec.name,
        wrapper=wrapper,
        scenario=scenario.lower(),
        capital=float(capital),
        years=int(years),
        inflation=float(inflation),
        annual_contribution=float(annual_contribution),
        annual_withdrawal=float(annual_withdrawal),
        rows=tuple(rows),
        terminal_nominal=float(terminal),
        terminal_real=float(terminal / (1.0 + inflation) ** int(years)),
        terminal_liquidation_tax=float(terminal_tax),
        terminal_after_tax=float(terminal - terminal_tax),
        total_tax_paid=float(total_tax),
        total_withdrawals=float(total_withdrawals),
        depletion_year=depletion_year,
    )


def safe_withdrawal_rate(
    profile: str,
    *,
    horizon_years: int = 30,
    wrapper: str = "cto",
    spec: ProfileSpec | None = None,
) -> float:
    """Planning SWR heuristic, expressed as a fraction of starting capital.

    A 4% baseline is reduced by 10% of the drawdown beyond 25%, floored at 2%.
    This is deliberately simple and is not a Monte Carlo safe-withdrawal result;
    the lifecycle simulator applies the tax and inflation path explicitly.
    """
    use_spec = spec or (PEA_PROXY if wrapper == "pea" else profile_spec(profile))
    if horizon_years < 1:
        raise ValueError("horizon_years must be >= 1")
    penalty = max(0.0, abs(use_spec.max_drawdown) - 0.25)
    return float(min(0.04, max(0.02, 0.04 - 0.10 * penalty)))


@dataclass(frozen=True)
class Lot:
    ticker: str
    qty: float
    cost_basis: float  # per share, in the same currency as price
    price: float
    acquired: str | None = None

    def __post_init__(self) -> None:
        if self.qty <= 0 or self.cost_basis < 0 or self.price <= 0:
            raise ValueError("lot qty/price must be positive and basis non-negative")


@dataclass(frozen=True)
class Sale:
    ticker: str
    qty: float
    price: float
    cost_basis: float
    gain: float
    tax: float
    proceeds: float
    acquired: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SellPlan:
    wrapper: str
    amount: float
    sales: tuple[Sale, ...]
    gross_proceeds: float
    tax: float
    net_proceeds: float
    unfilled: float
    note: str

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "sales": [s.to_dict() for s in self.sales],
        }


def tax_aware_sell_order(
    lots: Iterable[Lot],
    amount: float,
    *,
    wrapper: str = "cto",
) -> SellPlan:
    """Sell highest-basis lots first, then tax the realized gain.

    HIFO minimises the current taxable gain for a CTO. PEA lot choice is not
    legally required to be FIFO either; using HIFO keeps the same tax-aware
    ordering and is disclosed as a planning assumption.
    """
    if amount < 0:
        raise ValueError("amount must be >= 0")
    wrapper = str(wrapper).lower()
    if wrapper not in WRAPPERS:
        raise ValueError(f"unknown wrapper {wrapper!r}")
    rate = tax_rate_for(wrapper, 5 if wrapper == "pea" else 1)
    remaining = float(amount)
    sales: list[Sale] = []
    gross = 0.0
    tax_total = 0.0
    # Highest basis-to-price first: least gain per euro of proceeds.
    ordered = sorted(
        (lot for lot in lots if lot.qty > 0),
        key=lambda x: -(x.cost_basis / x.price),
    )
    for lot in ordered:
        if remaining <= 1e-12:
            break
        lot_value = lot.qty * lot.price
        take_value = min(remaining, lot_value)
        qty = take_value / lot.price
        gain = (lot.price - lot.cost_basis) * qty
        tax = max(0.0, gain) * rate
        proceeds = take_value - tax
        sales.append(
            Sale(
                ticker=lot.ticker,
                qty=qty,
                price=lot.price,
                cost_basis=lot.cost_basis,
                gain=gain,
                tax=tax,
                proceeds=proceeds,
                acquired=lot.acquired,
            )
        )
        gross += take_value
        tax_total += tax
        remaining -= take_value
    return SellPlan(
        wrapper=wrapper,
        amount=float(amount),
        sales=tuple(sales),
        gross_proceeds=float(gross),
        tax=float(tax_total),
        net_proceeds=float(gross - tax_total),
        unfilled=float(max(0.0, remaining)),
        note="HIFO planning order; actual broker tax lots and PEA withdrawal "
        "rules must be checked before selling.",
    )


def format_recommendation(rec: Recommendation) -> str:
    lines = [
        "INVESTOR DECISION",
        f"  horizon={rec.horizon_years:g}y  drawdown tolerance={rec.drawdown_tolerance:.0%}  "
        f"capital=€{rec.capital:,.0f}  wrapper={rec.wrapper.upper()}  broker={rec.broker}",
        f"  selected profile: {rec.profile} ({rec.label})",
        f"  allocation: {rec.allocation}",
        f"  evidence: {rec.evidence_status}",
        f"  planning CAGR range: {rec.expected_cagr_range[0]:.1%} to "
        f"{rec.expected_cagr_range[1]:.1%} (historical/scenario, not a forecast)",
        f"  historical maxDD range: {rec.maxdd_range[0]:.1%} to "
        f"{rec.maxdd_range[1]:.1%}",
        f"  tax: {rec.tax_estimate.rate:.1%} {rec.wrapper.upper()} planning rate; "
        f"implied tax €{rec.tax_estimate.implied_tax:,.0f} over "
        f"{rec.tax_estimate.horizon_years:g}y",
        "",
        "NEXT ORDERS (fresh-account paper plan, no orders sent)",
    ]
    if not rec.orders:
        lines.append("  (no usable orders; see warnings)")
    else:
        lines.append(
            f"  {'ticker':10s} {'side':4s} {'qty':>12s} {'price':>10s} "
            f"{'notional €':>12s} {'fee €':>8s}"
        )
        for o in rec.orders:
            lines.append(
                f"  {o.ticker:10s} {o.side:4s} {o.qty:12.6f} {o.price:10.2f} "
                f"{o.notional_eur:12.2f} {o.estimated_fee_eur:8.2f}"
            )
        lines.append(f"  gross target: {rec.gross:.2f}x capital")
    lines.append("")
    lines.append("WARNINGS")
    for warning in rec.warnings:
        lines.append(f"  - {warning}")
    return "\n".join(lines)


def format_lifecycle(projection: LifecycleProjection, swr: float) -> str:
    lines = [
        "LIFECYCLE PROJECTION",
        f"  profile={projection.profile}  wrapper={projection.wrapper.upper()}  "
        f"scenario={projection.scenario}  years={projection.years}  "
        f"inflation={projection.inflation:.1%}",
        f"  starting capital=€{projection.capital:,.0f}  "
        f"annual contribution=€{projection.annual_contribution:,.0f}  "
        f"annual withdrawal=€{projection.annual_withdrawal:,.0f}",
        f"  planning safe withdrawal rate: {swr:.2%} of starting capital "
        "(heuristic, not a promise)",
        f"  terminal nominal=€{projection.terminal_nominal:,.0f}  "
        f"real=€{projection.terminal_real:,.0f}  "
        f"after liquidation tax=€{projection.terminal_after_tax:,.0f}",
        f"  withdrawals=€{projection.total_withdrawals:,.0f}  "
        f"withdrawal tax=€{projection.total_tax_paid:,.0f}  "
        f"depletion={projection.depletion_year or 'none'}",
        "",
        f"  {'year':>4s} {'start':>12s} {'contrib':>10s} {'withdraw':>10s} "
        f"{'tax':>9s} {'end':>12s} {'real end':>12s}",
    ]
    for row in projection.rows:
        lines.append(
            f"  {row.year:4d} {row.start_balance:12.0f} {row.contribution:10.0f} "
            f"{row.withdrawal_gross:10.0f} {row.withdrawal_tax:9.0f} "
            f"{row.end_balance:12.0f} {row.end_real:12.0f}"
        )
    lines.append("")
    lines.append(
        "Not investment advice. Historical backtests are not forward evidence."
    )
    return "\n".join(lines)


__all__ = [
    "AllocationSnapshot",
    "BALANCED",
    "CONSERVATIVE",
    "DEFAULT_BROKER",
    "DEFAULT_CAPITAL",
    "InvestorProfile",
    "LifecycleProjection",
    "LifecycleRow",
    "Lot",
    "MAX_GROWTH",
    "Order",
    "PEA_PROXY",
    "PROFILES",
    "PROFILE_NAMES",
    "ProfileSpec",
    "Recommendation",
    "Sale",
    "SellPlan",
    "TaxEstimate",
    "build_orders",
    "current_allocation",
    "decision_tree",
    "estimate_tax",
    "fallback_weights",
    "format_lifecycle",
    "format_recommendation",
    "profile_spec",
    "project_lifecycle",
    "recommend",
    "safe_withdrawal_rate",
    "select_profile",
    "tax_aware_sell_order",
    "tax_rate_for",
]
