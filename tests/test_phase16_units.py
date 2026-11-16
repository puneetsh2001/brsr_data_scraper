"""
Phase 16: unit conversion and factor-model tests (master prompt section 18).

Controlled fixtures for every conversion the master prompt lists, plus a
real-data check that every applied conversion was applied exactly once.
The converter and the factor model are tested as they are; no conversion
rule is added or changed here.
"""

from decimal import Decimal

import pytest

from app.normalisation import factor_model as fm
from tests.conftest import production_values
from app.validators.unit_converter import (ENERGY_TO_GJ, FAMILIES, convert,
                                           unit_text)


def unit(*numerator, per=None):
    return {"numerator": list(numerator),
            "denominator": [per] if per else []}


ENERGY = "dtr-types:energyItemType"
GHG = "dtr-types:ghgEmissionsItemType"
VOLUME = "dtr-types:volumeItemType"
MASS = "num:massItemType"


# ---- conversions listed in the master prompt ------------------------------

@pytest.mark.parametrize("value,measure,family,dtype,expected,target", [
    ("2.5", "Non-SI:TJ", "energy", ENERGY, "2500", "GJ"),          # TJ -> GJ
    ("1500", "Non-SI:MJ", "energy", ENERGY, "1.5", "GJ"),          # MJ -> GJ
    ("10", "Non-SI:MWh", "energy", ENERGY, "36", "GJ"),            # MWh -> GJ
    ("1000", "Non-SI:kWh", "energy", ENERGY, "3.6", "GJ"),
    ("1", "Non-SI:GWh", "energy", ENERGY, "3600", "GJ"),
    ("7", "Non-SI:kJ", "energy", ENERGY, "0.000007", "GJ"),
    ("2500", "Non-SI:l", "kilolitre", VOLUME, "2.5", "kL"),        # l -> kL
    ("3", "Non-SI:ML", "kilolitre", VOLUME, "3000", "kL"),
    ("4500", "Non-SI:kg", "tonne", MASS, "4.5", "t"),              # kg -> t
    ("2", "Non-SI:kt", "tonne", MASS, "2000", "t"),
    ("3", "Non-SI:ktCO2e", "tco2e", GHG, "3000", "tCO2e"),
])
def test_rule_conversions(value, measure, family, dtype, expected, target):
    out = convert(value, unit(measure), family, dtype)
    assert out["conversion_status"] == "CONVERTED"
    assert Decimal(out["normalised_value"]) == Decimal(expected)
    assert out["normalised_unit"] == target
    # exact Decimal arithmetic: value x factor, no float rounding
    assert Decimal(value) * Decimal(out["conversion_factor"]) == \
        Decimal(out["normalised_value"])


def test_gj_and_mj_relationship_is_exact():
    """GJ is the energy target: 1 GJ = 1000 MJ exactly, in both directions
    of the table (MJ -> GJ x 0.001, GJ -> GJ x 1)."""
    assert ENERGY_TO_GJ["Non-SI:MJ"] * 1000 == ENERGY_TO_GJ["Non-SI:GJ"]
    mj = convert("1000", unit("Non-SI:MJ"), "energy", ENERGY)
    gj = convert("1", unit("Non-SI:GJ"), "energy", ENERGY)
    assert Decimal(mj["normalised_value"]) == Decimal(gj["normalised_value"])
    assert gj["conversion_status"] == "NO_CONVERSION_NEEDED"


def test_kgco2e_has_no_rule_and_is_not_converted():
    """No filing uses kgCO2e and no source defines its measure name, so the
    converter has no rule for it: the value is flagged, never guessed."""
    out = convert("4500", unit("Non-SI:kgCO2e"), "tco2e", GHG)
    assert out["conversion_status"] == "NOT_CONVERTED_UNEXPECTED_UNIT"
    assert out["normalised_value"] == "" and out["needs_review"]
    assert "Non-SI:kgCO2e" not in FAMILIES["tco2e"][0]


@pytest.mark.parametrize("measure", ["Non-SI:MtCO2e", "Non-SI:GtCO2e"])
def test_ambiguous_ghg_symbols_need_approval(measure):
    out = convert("100", unit(measure), "tco2e", GHG)
    assert out["conversion_status"] == "PENDING_APPROVAL_UNIT_ASSUMPTION"
    assert out["normalised_value"] == ""             # nothing applied
    assert out["proposed_normalised_value"] != ""     # proposal only


# ---- currency and scale (INR, crore, million) ------------------------------

def test_inr_is_kept_as_reported():
    out = convert("1234500000", unit("iso4217:INR"), "INR",
                  "xbrli:monetaryItemType")
    assert out["conversion_status"] == "NO_CONVERSION_NEEDED"
    assert out["normalised_value"] == "1234500000"
    assert out["conversion_factor"] == "1"


def test_foreign_currency_is_not_converted():
    out = convert("5", unit("iso4217:USD"), "INR", "xbrli:monetaryItemType")
    assert out["conversion_status"] == "NOT_CONVERTED_UNEXPECTED_UNIT"
    assert out["normalised_value"] == ""


def test_declared_crore_or_million_rounding_is_never_applied():
    """INR values are filed in rupees; a 'Crores' or 'Millions' rounding
    declaration, or a value that merely looks like crores, never changes
    the number (scale fixes need an approved scale decision)."""
    for value in ("12.5", "125000000"):
        out = convert(value, unit("iso4217:INR"), "INR",
                      "xbrli:monetaryItemType")
        assert out["normalised_value"] == value


@pytest.mark.parametrize("numerator,expected", [
    ("Non-SI:TJ", "0.0122"), ("Non-SI:GJ", "0.0000122"),
    ("Non-SI:MJ", "0.0000000122")])
def test_intensity_converts_numerator_only(numerator, expected):
    out = convert("0.0000122", unit(numerator, per="iso4217:INR"),
                  "energy_per_inr", ENERGY)
    assert Decimal(out["conversion_factor"]) == ENERGY_TO_GJ[numerator]
    assert out["normalised_unit"] == "GJ/INR"       # INR denominator kept
    assert Decimal(out["normalised_value"]) == Decimal(expected)


@pytest.mark.parametrize("denominator", ["iso4217:USD", "Non-SI:crore"])
def test_intensity_with_other_denominator_is_not_converted(denominator):
    out = convert("1", unit("Non-SI:GJ", per=denominator), "energy_per_inr",
                  ENERGY)
    assert out["conversion_status"] == "NOT_CONVERTED_UNEXPECTED_UNIT"
    assert out["normalised_value"] == ""


def test_intensity_reported_as_pure_is_ambiguous():
    out = convert("0.5", unit("xbrli:pure"), "energy_per_inr", ENERGY)
    assert out["conversion_status"] == "NOT_CONVERTED_UNIT_AMBIGUOUS"


# ---- reinterpretation, already-normalised values, exactly once -------------

def test_unit_reinterpretation_uses_multiplier_one():
    """Approved Non-SI:J -> GJ reinterpretation keeps the number: the
    informational 1e9 factor is recorded, never applied."""
    scale = {"approval_id": "T-1", "recommended_decision": "APPROVE",
             "approval_decision": "APPROVE", "approved_by": "test",
             "approval_date": "2026-11-13",
             "block_dependency": "BLOCK_COMPLETE_WITHIN_STRONG_SET",
             "original_value": "5000", "original_unit": "Non-SI:J",
             "actual_numeric_multiplier_on_original_value": "1",
             "proposed_corrected_value": "5000",
             "proposed_corrected_unit": "GJ",
             "unit_reinterpretation": "Non-SI:J -> GJ",
             "unit_reinterpretation_factor_informational": "1e9",
             "legacy_factor_on_current_normalised_value_only": "1e9"}
    out = fm.factor_row({"value": "5000", "unit_reference": "U",
                         "data_type": ENERGY}, unit("Non-SI:J"), "energy",
                        scale=scale)
    assert out["actual_numeric_multiplier_on_original"] == "1"
    assert out["final_value"] == "5000" and out["final_unit"] == "GJ"
    assert out["unit_reinterpretation_factor_not_applied"] == "1e9"
    assert out["legacy_factor_not_applied"] == "1e9"


@pytest.mark.parametrize("value,measure,family,dtype", [
    ("42", "Non-SI:GJ", "energy", ENERGY),
    ("42", "Non-SI:tCO2e", "tco2e", GHG),
    ("42", "in-capmkt-types:kl", "kilolitre", VOLUME),
    ("42", "Non-SI:m3", "kilolitre", VOLUME),        # 1 m3 = 1 kL exactly
    ("42", "Non-SI:t", "tonne", MASS)])
def test_already_normalised_values_are_unchanged(value, measure, family,
                                                 dtype):
    out = convert(value, unit(measure), family, dtype)
    assert out["conversion_status"] == "NO_CONVERSION_NEEDED"
    assert out["normalised_value"] == value and out["conversion_factor"] == "1"


def test_percentage_already_in_percent_is_not_scaled_again():
    frac = convert("0.25", unit("xbrli:pure"), "pure", "num:percentItemType")
    assert frac["conversion_status"] == "CONVERTED_FRACTION_TO_PERCENT"
    assert Decimal(frac["normalised_value"]) == Decimal("25")
    pct = convert("25", unit("xbrli:pure"), "pure", "num:percentItemType")
    assert pct["conversion_status"] == "NOT_CONVERTED_PERCENT_SCALE_AMBIGUOUS"
    assert pct["normalised_value"] == ""             # never x100 twice


TARGET_MEASURE = {"GJ": unit("Non-SI:GJ"), "tCO2e": unit("Non-SI:tCO2e"),
                  "t": unit("Non-SI:t"), "kL": unit("in-capmkt-types:kl"),
                  "GJ/INR": unit("Non-SI:GJ", per="iso4217:INR"),
                  "tCO2e/INR": unit("Non-SI:tCO2e", per="iso4217:INR"),
                  "kL/INR": unit("in-capmkt-types:kl", per="iso4217:INR"),
                  "t/INR": unit("Non-SI:t", per="iso4217:INR")}


def test_conversion_is_idempotent_on_its_own_output():
    first = convert("2.5", unit("Non-SI:TJ"), "energy", ENERGY)
    second = convert(first["normalised_value"],
                     TARGET_MEASURE[first["normalised_unit"]], "energy",
                     ENERGY)
    assert second["conversion_status"] == "NO_CONVERSION_NEEDED"
    assert second["normalised_value"] == first["normalised_value"]


@pytest.fixture(scope="module")
def production_cy():
    return production_values("CY")


def test_real_conversions_applied_exactly_once(production_cy):
    """Every applied rule conversion in production: multiplier equals the
    converter's factor for the filed unit (not a product of two factors),
    final = original x multiplier, and re-converting the final value from
    the target unit is a no-op."""
    cy = production_cy
    rule = cy[(cy["multiplier_basis"] == "RULE_UNIT_CONVERSION")
              & (cy["final_unit"].isin(TARGET_MEASURE))]
    assert len(rule) > 9000
    assert (rule["rule_status"] == "CONVERTED").sum() > 0
    checked = 0
    for r in rule.to_dict("records"):
        assert r["actual_numeric_multiplier_on_original"] == r["rule_factor"]
        assert Decimal(r["original_value"]) * Decimal(
            r["actual_numeric_multiplier_on_original"]) == \
            Decimal(r["final_value"])
        again = convert(r["final_value"], TARGET_MEASURE[r["final_unit"]],
                        r["expected_unit_family"], r["data_type"])
        assert again["conversion_factor"] == "1", r["final_unit"]
        checked += 1
    assert checked == len(rule)


def test_real_unconverted_rows_keep_original_and_no_final(production_cy):
    cy = production_cy
    none = cy[cy["multiplier_basis"] == "NONE"]
    assert len(none) > 0
    assert (none["final_value"] == "").all()
    assert (none["actual_numeric_multiplier_on_original"] == "").all()
    # a filed value is always kept as the original value
    filed = cy[cy["value_status"] == "REPORTED"]
    assert (filed["original_value"] != "").all()
    # pending unit assumptions keep a proposal only, never a final value
    pend = cy[cy["final_status"] == "PENDING_APPROVAL_UNIT_ASSUMPTION"]
    assert len(pend) == 529 and (pend["final_value"] == "").all()


def test_unit_text_round_trip():
    assert unit_text(unit("Non-SI:GJ", per="iso4217:INR")) == \
        "Non-SI:GJ / iso4217:INR"
    assert unit_text(None) == ""
