"""Cost and tax wrapper for Alpha Lab.

Base model is the French CTO at Interactive Brokers, the cheapest researched
retail route: 5 bps market spread, USD 1 minimum / 0.25 bps US commission,
2 bps FX, 5%/yr financing on negative cash, 31.4% PFU (30% before 2026), losses
carried ten years. `multiplier=2` is the mandatory 2x-cost sensitivity: it
doubles spread, commission bps, FX bps and financing, but not the tax rate and
not the fixed ticket fees.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from bot import france


@dataclass(frozen=True)
class CostModel:
    name: str = "fr_cto_ibkr"
    broker: str = "Interactive Brokers"
    wrapper: str = "cto"
    spread_bps: float = 5.0
    financing_bps: float = 500.0
    multiplier: float = 1.0

    def scaled(self, multiplier: float) -> "CostModel":
        return CostModel(
            name=f"{self.name}_x{multiplier:g}",
            broker=self.broker,
            wrapper=self.wrapper,
            spread_bps=self.spread_bps,
            financing_bps=self.financing_bps,
            multiplier=multiplier,
        )

    def engine_kwargs(self, panel: pd.DataFrame, market: str = "TECH") -> dict:
        """Config kwargs for `bot.strategy.Config`, including French tax."""
        kw = france.kwargs(market, self.broker, self.wrapper, 10000.0, panel)
        m = float(self.multiplier)
        kw["cost_bps"] = float(self.spread_bps) * m
        kw["financing_bps"] = float(self.financing_bps) * m
        for key in ("fx_bps", "broker_bps", "us_bps"):
            if key in kw:
                kw[key] = float(kw[key]) * m
        return kw

    def eur_config(
        self,
        panel: pd.DataFrame,
        market: str = "TECH",
        capital: float = 10000.0,
        tax_rate: float = 0.314,
    ):
        """EUR-ledger config with the same scaled frictions as the engine.

        Fixed ticket fees are not doubled by `scaled(2)`, matching the engine
        and the research protocol.
        """
        from bot.eur_ledger import EurLedgerConfig

        kw = self.engine_kwargs(panel, market)
        grid = kw.get("us_grid") or kw.get("broker_grid") or ()
        fixed = float(grid[0][1]) if grid else 1.0
        fx_bps = float(kw.get("fx_bps", 2.0))
        broker_bps = float(kw.get("us_bps", kw.get("broker_bps", 0.25)))
        # `engine_kwargs` already applied the multiplier to bps fields.
        return EurLedgerConfig(
            capital=float(capital),
            tax_rate=float(tax_rate),
            financing_bps=self.financing_bps * self.multiplier,
            spread_bps=self.spread_bps * self.multiplier,
            fx_bps=fx_bps,
            broker_bps=broker_bps,
            broker_fixed_eur=fixed,
            loss_carry_years=10,
        )

    def eur_replay(
        self,
        res,
        panel: pd.DataFrame,
        market: str = "TECH",
        capital: float = 10000.0,
        tax_rate: float = 0.314,
    ):
        """Replay `res.trades` in EUR with trade-date FX and annual PFU."""
        from bot.eur_ledger import replay

        return replay(
            res,
            panel,
            market,
            self.eur_config(panel, market, capital, tax_rate),
        )

    def describe(self) -> str:
        return (
            f"{self.name}: spread {self.spread_bps * self.multiplier:.1f} bps, "
            f"financing {self.financing_bps * self.multiplier:.0f} bps, "
            f"broker {self.broker}, French CTO PFU, x{self.multiplier:g} costs"
        )


def after_tax(result, market: str = "TECH", capital: float = 10000.0) -> dict:
    """After-tax summary with EUR and USD terminal multiples."""
    f = france.after_tax(result, capital, market)
    return {
        **f,
        "after_tax_multiple_eur": f["after_tax_eur"] / capital,
        "after_tax_multiple_usd": f["after_tax_native"] / capital,
        "pre_tax_multiple_usd": f["pre_tax_native"] / capital,
        "pre_tax_multiple_eur": f["pre_tax_eur"] / capital,
    }
