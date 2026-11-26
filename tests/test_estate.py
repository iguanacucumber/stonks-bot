"""US/French estate-tax calculator: math, credits, wrappers and boundaries."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot import estate  # noqa: E402


def close(actual: float, expected: float, tol: float = 1e-9) -> None:
    assert abs(float(actual) - float(expected)) <= tol, (actual, expected)


def test_tentative_rate_schedule() -> None:
    close(estate.tentative_us_estate_tax(60_000), 13_000)
    close(estate.tentative_us_estate_tax(100_000), 23_800)
    close(estate.tentative_us_estate_tax(200_000), 54_800)
    close(estate.tentative_us_estate_tax(1_000_000), 345_800)
    close(estate.tentative_us_estate_tax(1_100_000), 385_800)
    close(estate.tentative_us_estate_tax(15_000_000), estate.CITIZEN_CREDIT_2026)
    close(estate.tentative_us_estate_tax(-1), 0)


def test_nra_credit_and_boundary() -> None:
    # The $13,000 credit equals the tax on the first $60,000.
    assert estate.nra_us_estate_tax(60_000)["tax"] == 0
    out = estate.nra_us_estate_tax(100_000)
    close(out["tax"], 10_800)
    out = estate.nra_us_estate_tax(200_000)
    close(out["tax"], 41_800)  # matches the Form 706-NA worked example
    out = estate.nra_us_estate_tax(60_001)
    assert out["tax"] > 0
    # Credit cannot exceed tentative tax.
    assert estate.nra_us_estate_tax(1)["tax"] == 0


def test_france_treaty_pro_rata_credit() -> None:
    # French-domiciled estate: a $660k US-situs slice inside a $725k worldwide
    # estate gets a huge pro-rata credit and owes no US estate tax.
    out = estate.nra_us_estate_tax(660_000, treaty=True, worldwide=725_000)
    close(out["tax"], 0)
    assert out["credit"] >= out["tentative_tax"]

    # A 50% US-situs slice of a $20m worldwide estate: half the citizen credit.
    out = estate.nra_us_estate_tax(10_000_000, treaty=True, worldwide=20_000_000)
    close(out["credit"], estate.CITIZEN_CREDIT_2026 / 2)
    close(out["tax"], 972_900)

    # A fully US $20m estate pays the citizen tax above the $15m exclusion.
    out = estate.nra_us_estate_tax(20_000_000, treaty=True, worldwide=20_000_000)
    close(out["tax"], 2_000_000)

    try:
        estate.nra_us_estate_tax(1_000, treaty=True, worldwide=None)
        raise AssertionError("treaty credit needs a worldwide value")
    except ValueError:
        pass


def test_french_direct_line_and_spouse() -> None:
    close(estate.french_inheritance_tax(100_000, "child"), 0)
    close(estate.french_inheritance_tax(200_000, "child"), 18_194.35)
    close(estate.french_inheritance_tax(200_000, "child", heirs=2), 0)
    assert estate.french_inheritance_tax(5_000_000, "spouse") == 0
    close(
        estate.french_inheritance_tax(100_000, "other"),
        (100_000 - estate.FRENCH_OTHER_ALLOWANCE) * 0.60,
    )
    try:
        estate.french_inheritance_tax(100_000, "nephew")
        raise AssertionError("unmodelled heir must be rejected")
    except ValueError:
        pass


def test_assurance_vie_abatement() -> None:
    assert estate.assurance_vie_tax(100_000, "child") == 0
    close(estate.assurance_vie_tax(200_000, "child"), 9_500)
    assert estate.assurance_vie_tax(500_000, "spouse") == 0
    # Two beneficiaries each get their own €152,500 allowance.
    close(estate.assurance_vie_tax(400_000, "child", beneficiaries=2), 19_000)
    try:
        estate.assurance_vie_tax(100_000, premiums_before_70=False)
        raise AssertionError("after-70 base cannot be capital")
    except ValueError:
        pass


def test_wrappers_remove_us_situs_in_model() -> None:
    cto = estate.assess(
        estate.EstateInputs(
            capital_eur=660_000, us_situs_share=1.0, wrapper="cto", treaty=False
        )
    )
    assert cto["net_us_situs_usd"] > 0
    assert cto["us_tax_usd"] > 0
    for wrapper in ("ucits", "pea", "assurance_vie"):
        row = estate.assess(
            estate.EstateInputs(
                capital_eur=660_000,
                us_situs_share=1.0,
                wrapper=wrapper,
                treaty=False,
            )
        )
        assert row["net_us_situs_usd"] == 0
        assert row["us_tax_usd"] == 0


def test_treaty_zero_for_current_book() -> None:
    row = estate.assess(
        estate.EstateInputs(
            capital_eur=660_000,
            us_situs_share=1.0,
            wrapper="cto",
            treaty=True,
            leverage=2.5,
        )
    )
    close(row["us_tax_usd"], 0)
    assert row["gross_us_situs_usd"] > row["net_us_situs_usd"]
    assert row["french_tax_eur"] > 0
    assert row["combined_tax_eur"] >= row["french_tax_eur"]


def test_worked_examples_and_scenarios() -> None:
    rows = estate.worked_examples()
    assert len(rows) == 6
    by = {r["label"]: r for r in rows}
    close(by["€10,000 treaty"]["us_tax_usd"], 0)
    close(by["€10,000 no treaty"]["us_tax_usd"], 0)
    close(by["€100,000 treaty"]["us_tax_usd"], 0)
    assert by["€100,000 no treaty"]["us_tax_usd"] > 0
    close(by["€660,000 treaty"]["us_tax_usd"], 0)
    assert (
        by["€660,000 no treaty"]["us_tax_usd"] > by["€100,000 no treaty"]["us_tax_usd"]
    )
    scenarios = estate.mitigation_scenarios(
        estate.EstateInputs(capital_eur=660_000, us_situs_share=1.0)
    )
    assert len(scenarios) == 6
    assert scenarios[0]["scenario"] == "current CTO / US-situs"
    assert any(x["scenario"] == "UCITS core" for x in scenarios)


def test_input_validation() -> None:
    for bad in (
        dict(capital_eur=-1),
        dict(us_situs_share=1.1),
        dict(wrapper="life_insurance"),
        dict(heir="cousin"),
        dict(heirs=0),
        dict(leverage=0.5),
        dict(eur_usd=0),
    ):
        try:
            estate.EstateInputs(**bad)
            raise AssertionError(f"accepted bad inputs {bad}")
        except ValueError:
            pass


def main() -> None:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"ok: {fn.__name__}")
    print(f"ok: {len(tests)} estate-tax tests")


if __name__ == "__main__":
    main()
