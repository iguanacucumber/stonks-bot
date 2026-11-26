"""US estate-tax exposure for a French investor: pure calculator.

This is a planning model, not tax or legal advice. It separates three things
that are often conflated:

* **US federal estate tax** on US-situs assets of a nonresident non-citizen
  (NRNC), with the statutory $13,000 unified credit (equivalent to a $60,000
  exemption) or the France-US treaty's pro-rata credit.
* **French droits de succession** on the worldwide estate of a French-domiciled
  decedent, with the 2026 direct-line scale and allowances.
* **Wrapper treatment** (CTO, PEA, assurance-vie, UCITS). The wrapper changes
  US-situs exposure and/or the French inheritance-tax base; it does not change
  the underlying market risk.

2026 constants are explicit and cited in ``docs/estate_tax.md``. The calculator
does not fetch data and does not depend on the repo's price cache.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# IRS 2026 basic exclusion amount and the credit equivalent (IRS Form 706
# Instructions, Rev. July 2026; IRS "What's new — Estate and gift tax").
BASIC_EXCLUSION_2026 = 15_000_000.0
CITIZEN_CREDIT_2026 = 5_945_800.0
NRA_UNIFIED_CREDIT = 13_000.0
NRA_FILING_THRESHOLD = 60_000.0
TOP_ESTATE_RATE = 0.40
DEFAULT_EUR_USD = 1.0989  # 2026-11-24 cache close, USD per EUR

# IRS Form 706 Table A / IRC 2001(c): (over, not_over, tax_on_over, rate).
# The table is not inflation-indexed.
_ESTATE_BRACKETS = (
    (0.0, 10_000.0, 0.0, 0.18),
    (10_000.0, 20_000.0, 1_800.0, 0.20),
    (20_000.0, 40_000.0, 3_800.0, 0.22),
    (40_000.0, 60_000.0, 8_200.0, 0.24),
    (60_000.0, 80_000.0, 13_000.0, 0.26),
    (80_000.0, 100_000.0, 18_200.0, 0.28),
    (100_000.0, 150_000.0, 23_800.0, 0.30),
    (150_000.0, 250_000.0, 38_800.0, 0.32),
    (250_000.0, 500_000.0, 70_800.0, 0.34),
    (500_000.0, 750_000.0, 155_800.0, 0.37),
    (750_000.0, 1_000_000.0, 248_300.0, 0.39),
    (1_000_000.0, float("inf"), 345_800.0, 0.40),
)

# French direct-line inheritance scale, 2026 (CGI art. 777).
_FRENCH_CHILD_BRACKETS = (
    (0.0, 8_072.0, 0.05),
    (8_072.0, 12_109.0, 0.10),
    (12_109.0, 15_932.0, 0.15),
    (15_932.0, 552_324.0, 0.20),
    (552_324.0, 902_838.0, 0.30),
    (902_838.0, 1_805_677.0, 0.40),
    (1_805_677.0, float("inf"), 0.45),
)
FRENCH_CHILD_ALLOWANCE = 100_000.0
FRENCH_OTHER_ALLOWANCE = 1_594.0
FRENCH_SPOUSE_EXEMPT = True
AV_ABATEMENT_PER_BENEFICIARY = 152_500.0
AV_ABATEMENT_AFTER_70 = 30_500.0


def tentative_us_estate_tax(amount: float) -> float:
    """IRC 2001(c) tentative tax before the unified credit."""
    value = max(0.0, float(amount))
    for over, not_over, base, rate in _ESTATE_BRACKETS:
        if value <= not_over:
            return base + (value - over) * rate
    raise AssertionError("unreachable estate bracket")


def nra_us_estate_tax(
    us_situs: float,
    *,
    treaty: bool = False,
    worldwide: float | None = None,
    citizen_credit: float = CITIZEN_CREDIT_2026,
) -> dict[str, float]:
    """US federal estate tax for a nonresident non-citizen.

    Without a treaty the estate gets the statutory $13,000 credit. With the
    France-US treaty (Art. 12(3), 2004 protocol) a French-domiciled decedent
    gets the greater of $13,000 or the citizen credit prorated by US-situs /
    worldwide gross estate, subject to the information and domicile conditions.
    """
    us = max(0.0, float(us_situs))
    tentative = tentative_us_estate_tax(us)
    if not treaty:
        credit_formula = NRA_UNIFIED_CREDIT
        credit = min(credit_formula, tentative)
        basis = "statutory NRA credit ($13,000 / $60,000 equivalent)"
    else:
        if worldwide is None or worldwide <= 0:
            raise ValueError("worldwide estate is required for the treaty credit")
        if us > worldwide + 1e-9:
            raise ValueError("US-situs value cannot exceed the worldwide estate")
        credit_formula = max(
            NRA_UNIFIED_CREDIT,
            citizen_credit * (us / float(worldwide)),
        )
        credit = min(tentative, credit_formula)
        basis = "France-US treaty pro-rata unified credit (Art. 12(3))"
    tax = max(0.0, tentative - credit)
    return {
        "us_situs": us,
        "tentative_tax": tentative,
        "credit_formula": credit_formula,
        "credit": credit,
        "tax": tax,
        "effective_rate": tax / us if us > 0 else 0.0,
        "credit_basis": basis,
    }


def french_direct_line_tax(taxable: float) -> float:
    """French inheritance tax on one direct-line heir's taxable share."""
    value = max(0.0, float(taxable))
    tax = 0.0
    for over, not_over, rate in _FRENCH_CHILD_BRACKETS:
        if value <= over:
            break
        tax += (min(value, not_over) - over) * rate
        if value <= not_over:
            break
    return tax


def french_inheritance_tax(
    estate_eur: float,
    heir: Literal["child", "spouse", "other"] = "child",
    heirs: int = 1,
) -> float:
    """Illustrative French droits de succession, 2026.

    ``child`` splits the estate equally between ``heirs`` children and applies
    the €100,000 allowance and 5-45% scale to each share. ``spouse`` is fully
    exempt. ``other`` uses the unrelated-person €1,594 allowance and 60% rate;
    siblings/nephews have different scales and are not modelled.
    """
    value = max(0.0, float(estate_eur))
    if heir == "spouse":
        return 0.0
    n = max(1, int(heirs))
    if heir == "child":
        share = value / n
        taxable = max(0.0, share - FRENCH_CHILD_ALLOWANCE)
        return french_direct_line_tax(taxable) * n
    if heir == "other":
        return max(0.0, value - FRENCH_OTHER_ALLOWANCE) * 0.60
    raise ValueError("heir must be child, spouse or other")


def assurance_vie_tax(
    capital_eur: float,
    heir: Literal["child", "spouse", "other"] = "child",
    beneficiaries: int = 1,
    premiums_before_70: bool = True,
) -> float:
    """French assurance-vie death tax for premiums paid before age 70.

    Per beneficiary: €152,500 allowance, then 20% up to €700,000 taxable and
    31.25% above. A spouse/PACS beneficiary is exempt. Premiums paid after 70
    follow the separate €30,500 global regime (CGI art. 757 B), which this
    helper does not model because the taxable base is premiums, not capital.
    """
    if heir == "spouse":
        return 0.0
    if not premiums_before_70:
        raise ValueError("after-70 assurance-vie needs the premiums, not capital")
    n = max(1, int(beneficiaries))
    share = max(0.0, float(capital_eur)) / n
    taxable = max(0.0, share - AV_ABATEMENT_PER_BENEFICIARY)
    tax = min(taxable, 700_000.0) * 0.20 + max(0.0, taxable - 700_000.0) * 0.3125
    return tax * n


@dataclass(frozen=True)
class EstateInputs:
    capital_eur: float = 660_000.0
    us_situs_share: float = 1.0
    wrapper: Literal["cto", "pea", "assurance_vie", "ucits"] = "cto"
    treaty: bool = True
    heir: Literal["child", "spouse", "other"] = "child"
    heirs: int = 1
    leverage: float = 1.0
    eur_usd: float = DEFAULT_EUR_USD
    worldwide_eur: float | None = None
    premiums_before_70: bool = True

    def __post_init__(self) -> None:
        if self.capital_eur < 0:
            raise ValueError("capital_eur must be >= 0")
        if not 0.0 <= self.us_situs_share <= 1.0:
            raise ValueError("us_situs_share must be in [0, 1]")
        if self.wrapper not in ("cto", "pea", "assurance_vie", "ucits"):
            raise ValueError("unknown wrapper")
        if self.heir not in ("child", "spouse", "other"):
            raise ValueError("unknown heir")
        if self.heirs < 1:
            raise ValueError("heirs must be >= 1")
        if self.leverage < 1.0:
            raise ValueError("leverage must be >= 1")
        if self.eur_usd <= 0:
            raise ValueError("eur_usd must be > 0")


def _effective_us_situs_share(inp: EstateInputs) -> float:
    # PEA and assurance-vie are French wrappers, and the modelled UCITS core is
    # non-US-situs. These are structural assumptions, stated in the report.
    if inp.wrapper in ("pea", "assurance_vie", "ucits"):
        return 0.0
    return float(inp.us_situs_share)


def assess(inp: EstateInputs) -> dict[str, float | str]:
    """Run the full exposure calculation for one scenario."""
    share = _effective_us_situs_share(inp)
    capital_usd = inp.capital_eur * inp.eur_usd
    worldwide_eur = inp.capital_eur if inp.worldwide_eur is None else inp.worldwide_eur
    if worldwide_eur < inp.capital_eur:
        raise ValueError("worldwide_eur cannot be below capital_eur")
    worldwide_usd = worldwide_eur * inp.eur_usd

    gross_us_situs = capital_usd * share * inp.leverage
    net_us_situs = capital_usd * share
    us = nra_us_estate_tax(
        net_us_situs,
        treaty=inp.treaty,
        worldwide=worldwide_usd,
    )

    if inp.wrapper == "assurance_vie":
        french_tax = assurance_vie_tax(
            inp.capital_eur,
            heir=inp.heir,
            beneficiaries=inp.heirs,
            premiums_before_70=inp.premiums_before_70,
        )
        french_basis = "assurance-vie 990 I (before-70 premiums)"
    else:
        french_tax = french_inheritance_tax(
            inp.capital_eur, heir=inp.heir, heirs=inp.heirs
        )
        french_basis = "French droits de succession (worldwide estate)"
    french_on_non_us = french_inheritance_tax(
        max(0.0, inp.capital_eur - net_us_situs / inp.eur_usd),
        heir=inp.heir,
        heirs=inp.heirs,
    )
    french_on_us_slice = max(0.0, french_tax - french_on_non_us)
    us_tax_eur = us["tax"] / inp.eur_usd
    credit = min(us_tax_eur, french_on_us_slice)
    combined = french_tax + us_tax_eur - credit
    return {
        "capital_eur": float(inp.capital_eur),
        "capital_usd": float(capital_usd),
        "wrapper": inp.wrapper,
        "treaty": bool(inp.treaty),
        "heir": inp.heir,
        "heirs": int(inp.heirs),
        "us_situs_share": float(share),
        "gross_us_situs_usd": float(gross_us_situs),
        "net_us_situs_usd": float(net_us_situs),
        "worldwide_usd": float(worldwide_usd),
        "us_tentative_tax": float(us["tentative_tax"]),
        "us_credit_formula": float(us["credit_formula"]),
        "us_credit": float(us["credit"]),
        "us_tax_usd": float(us["tax"]),
        "us_tax_eur": float(us_tax_eur),
        "us_effective_rate": float(us["effective_rate"]),
        "us_credit_basis": str(us["credit_basis"]),
        "french_tax_eur": float(french_tax),
        "french_tax_on_us_slice_eur": float(french_on_us_slice),
        "foreign_tax_credit_eur": float(credit),
        "combined_tax_eur": float(combined),
        "after_tax_estate_eur": float(inp.capital_eur - combined),
        "combined_effective_rate": (
            float(combined / inp.capital_eur) if inp.capital_eur > 0 else 0.0
        ),
        "french_basis": french_basis,
    }


def mitigation_scenarios(inp: EstateInputs) -> list[dict[str, float | str]]:
    """Current holding and the main structural alternatives, same heir.

    The scenarios are structural illustrations, not recommendations. The UCITS
    and wrapper rows remove US-situs exposure in the model; they do not remove
    French inheritance tax and may change fees, withholding, liquidity or
    estate-law treatment. Confirm the fund domicile and the contract with a
    lawyer or tax adviser.
    """
    scenarios: list[tuple[str, EstateInputs]] = [
        ("current CTO / US-situs", inp),
        (
            "no US estate treaty",
            EstateInputs(**{**inp.__dict__, "treaty": False}),
        ),
        (
            "keep US-situs at $60k",
            EstateInputs(
                **{
                    **inp.__dict__,
                    "us_situs_share": min(
                        inp.us_situs_share,
                        NRA_FILING_THRESHOLD / max(1e-9, inp.capital_eur * inp.eur_usd),
                    ),
                }
            ),
        ),
        (
            "UCITS core",
            EstateInputs(**{**inp.__dict__, "wrapper": "ucits"}),
        ),
        (
            "PEA (EU UCITS only)",
            EstateInputs(**{**inp.__dict__, "wrapper": "pea"}),
        ),
        (
            "assurance-vie (before 70)",
            EstateInputs(
                **{
                    **inp.__dict__,
                    "wrapper": "assurance_vie",
                    "premiums_before_70": True,
                }
            ),
        ),
    ]
    out = []
    for label, cfg in scenarios:
        row = assess(cfg)
        row["scenario"] = label
        out.append(row)
    return out


def worked_examples(
    capitals: tuple[float, ...] = (10_000.0, 100_000.0, 660_000.0),
    eur_usd: float = DEFAULT_EUR_USD,
) -> list[dict[str, float | str]]:
    """Direct 100% US-situs CTO exposure, with and without the treaty."""
    rows = []
    for capital in capitals:
        for treaty in (True, False):
            row = assess(
                EstateInputs(
                    capital_eur=capital,
                    us_situs_share=1.0,
                    wrapper="cto",
                    treaty=treaty,
                    heir="child",
                    eur_usd=eur_usd,
                )
            )
            row["label"] = f"€{capital:,.0f} {'treaty' if treaty else 'no treaty'}"
            rows.append(row)
    return rows
