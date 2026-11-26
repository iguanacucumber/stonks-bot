"""Live configuration and the strategy adapter.

The live book is the researched book plus execution rails. Strategy overrides mirror
``scripts/paper_daily.py``; a test asserts the two tables stay equal so the live loop
cannot quietly trade a different book. There are no secrets here: the only broker is
the paper adapter.
"""

from __future__ import annotations

import hashlib
import json
import tomllib
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path
from typing import Any

from bot import markets, strategy

ROOT = Path(__file__).resolve().parent.parent.parent

# Mirrors scripts/paper_daily.BOOKS. tests/test_live.py checks equality.
BOOK_OVERRIDES: dict[str, dict[str, Any]] = {
    "monthly": {},
    "tax": {
        "core": "SMH",
        "core_mode": "always",
        "leverage_band": 0.30,
        "tax_lots": "hifo",
    },
}


BOOK_LABELS = {
    "monthly": "monthly momentum",
    "tax": "core-only tax",
}

# Best real-life evidence (2026-11-26): US monthly is 70.86x headline / 66.72x
# EUR-ledger at maxDD -57.5%, ahead of TECH monthly at 57.01x / 53.45x. The TECH
# forward paper book keeps running as the validation track; it is not reset here.
# Any market can be selected explicitly, so this is a recommendation, not a branch.
BEST_EVIDENCE_MARKET = "US"


def book_overrides(book: str) -> dict[str, Any]:
    if book not in BOOK_OVERRIDES:
        raise ValueError(f"unknown book {book!r}; choose from {sorted(BOOK_OVERRIDES)}")
    return dict(BOOK_OVERRIDES[book])


@dataclass(frozen=True)
class LiveConfig:
    """One complete live profile. Immutable so a plan hash cannot drift mid-run."""

    book: str = "monthly"
    market: str = BEST_EVIDENCE_MARKET
    capital: float = 10_000.0
    currency: str | None = None  # native quote currency; inferred from market
    start: str = "2015-01-01"

    # Strategy / research book.
    leverage: float = 2.5
    financing_bps: float = 500.0
    trend_ma: int = 200
    trend_scale: float = 0.5
    rebalance_months: int = 1
    rebalance_offset: int = 0

    # Live-only safety rails. Defaults are deliberately tighter than "no rail".
    # dd_stop is a non-validated safety overlay, not a walk-forward alpha rule.
    dd_stop: float = 0.35  # 0 disables the live drawdown breaker
    dd_scale: float = 0.5
    dd_recover: float = 0.10
    # Ceiling only: max_gross never becomes a target. The target comes from the
    # research leverage (2.5x) and the risk cut.
    max_gross: float = 3.0
    max_turnover: float = 3.0
    max_orders: int = 50
    min_order_notional: float = 25.0
    # Reporting age limit for the stale reason; a missing current-session close
    # always blocks regardless of this number.
    max_stale_days: int = 3
    max_pending_sessions: int = 5
    # Trade band matches the shipped research book (0.0). ``min_order_notional`` is
    # the only live execution minimum; it is reported and never hides a target drift.
    trade_band: float = 0.0

    # Paper execution model.
    slippage_bps: float = 5.0
    commission_bps: float = 5.0
    min_commission: float = 1.0
    max_participation: float = 0.02
    fractional_shares: bool = True

    # Runtime; ``None`` resolves to <repo>/state/live.
    state_dir: str | None = None
    forward_gate: int = 5

    def __post_init__(self) -> None:
        if self.book not in BOOK_OVERRIDES:
            raise ValueError(f"unknown book {self.book!r}")
        if self.market not in markets.MARKETS:
            raise ValueError(
                f"unknown market {self.market!r}; choose from {sorted(markets.MARKETS)}"
            )
        native = markets.MARKETS[self.market]["fx"]
        if self.currency is not None and self.currency != native:
            raise ValueError(
                f"currency {self.currency!r} does not match {self.market} ({native})"
            )
        # Freeze the resolved currency so two equivalent configs do not sign
        # differently just because one left the optional field unset.
        object.__setattr__(self, "currency", native)
        if self.capital <= 0:
            raise ValueError("capital must be > 0")
        if self.leverage <= 0 or self.max_gross <= 0:
            raise ValueError("leverage and max_gross must be > 0")
        if self.max_turnover <= 0:
            raise ValueError("max_turnover must be > 0")
        if self.dd_stop and not (0 < self.dd_stop < 1):
            raise ValueError("dd_stop must be 0 or in (0, 1)")
        if self.dd_scale <= 0:
            raise ValueError("dd_scale must be > 0")
        if self.max_participation <= 0:
            raise ValueError("max_participation must be > 0")
        if self.rebalance_months < 1:
            raise ValueError("rebalance_months must be >= 1")

    @property
    def root(self) -> Path:
        return ROOT

    @property
    def native_currency(self) -> str:
        return markets.MARKETS[self.market]["fx"]

    @property
    def reporting_currency(self) -> str:
        return "EUR"

    @property
    def market_label(self) -> str:
        return str(markets.MARKETS[self.market]["label"])

    @property
    def leverage_band(self) -> float:
        """Book's researched leverage-drift band (0 monthly, 0.30 tax)."""
        return float(book_overrides(self.book).get("leverage_band", 0.0))

    @property
    def book_label(self) -> str:
        return BOOK_LABELS[self.book]

    @property
    def recommended_market(self) -> str:
        return BEST_EVIDENCE_MARKET

    @property
    def recommendation(self) -> str:
        """One-line evidence label; never changes which book actually runs."""
        if self.market == BEST_EVIDENCE_MARKET:
            return (
                f"{self.market} {self.book} is the best-evidence live book; "
                "TECH monthly remains the forward validation track"
            )
        return (
            f"{BEST_EVIDENCE_MARKET} monthly is the best-evidence live book; "
            f"this {self.market} {self.book} run is for validation/research"
        )

    @property
    def state_path(self) -> Path:
        base = Path(self.state_dir) if self.state_dir else ROOT / "state" / "live"
        return base / f"{self.book}_{self.market.lower()}.json"

    @property
    def ledger_path(self) -> Path:
        return self.state_path.with_suffix(".csv")

    @property
    def lock_path(self) -> Path:
        return self.state_path.with_suffix(".lock")

    def strategy_config(self, end: str | None = None) -> strategy.Config:
        """Backtest config for signals. Live risk is applied in the engine, so
        dd_stop is disabled here to avoid scaling the same drawdown twice."""
        kw = book_overrides(self.book)
        return strategy.Config(
            start=self.start,
            end=end,
            rebalance_mode="calendar",
            rebalance_months=self.rebalance_months,
            rebalance_offset=self.rebalance_offset,
            exec_price="open",
            leverage=self.leverage,
            financing_bps=self.financing_bps,
            trend_ma=self.trend_ma,
            trend_scale=self.trend_scale,
            dd_stop=0.0,
            **kw,
        )

    def signature(self) -> str:
        """Stable hash of everything that changes orders. State dir is excluded."""
        raw = asdict(self)
        raw.pop("state_dir", None)
        raw.pop("forward_gate", None)
        blob = json.dumps(raw, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()[:16]

    def replace(self, **kw: Any) -> "LiveConfig":
        return replace(self, **kw)


def load_config(path: str | Path | None = None, **overrides: Any) -> LiveConfig:
    """Load a TOML file (optional) then apply explicit CLI overrides."""
    raw: dict[str, Any] = {}
    if path:
        with open(path, "rb") as fh:
            raw = tomllib.load(fh)
        unknown = set(raw) - {f.name for f in fields(LiveConfig)}
        if unknown:
            raise ValueError(f"unknown live config keys: {sorted(unknown)}")
    raw.update({k: v for k, v in overrides.items() if v is not None})
    return LiveConfig(**raw)


def config_from_dict(raw: dict[str, Any]) -> LiveConfig:
    unknown = set(raw) - {f.name for f in fields(LiveConfig)}
    if unknown:
        raise ValueError(f"unknown live config keys: {sorted(unknown)}")
    return LiveConfig(**raw)
