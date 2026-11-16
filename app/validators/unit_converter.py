"""
Unit definitions and conversions for BRSR XBRL values.

Rules (agreed with the project owner):
  * Energy      -> GJ
  * Emissions   -> tCO2e
  * Mass        -> t        Volume -> kL        Currency -> INR (no FX)
  * Intensities keep their INR denominator; only the numerator is
    converted (e.g. MJ/INR -> GJ/INR). Different currencies, scales or
    denominators are never treated as equivalent.
  * Percentages are converted (fraction x 100) only when the concept's
    data type is num:percentItemType, the unit is xbrli:pure and the value
    lies in [0, 1].
  * No conversion when the unit is missing or ambiguous; the original
    value and unit are always preserved by the caller.

All factors are exact; arithmetic uses Decimal.
"""

import xml.etree.ElementTree as ET
from decimal import Decimal, InvalidOperation


XBRLI = "{http://www.xbrl.org/2003/instance}"

# measure -> (dimension, factor to the dimension's standard unit)
ENERGY_TO_GJ = {
    "Non-SI:J": Decimal("1e-9"),
    "Non-SI:kJ": Decimal("1e-6"),
    "Non-SI:MJ": Decimal("1e-3"),
    "Non-SI:GJ": Decimal("1"),
    "Non-SI:TJ": Decimal("1e3"),
    "Non-SI:PJ": Decimal("1e6"),
    "Non-SI:kWh": Decimal("0.0036"),
    "Non-SI:MWh": Decimal("3.6"),
    "Non-SI:GWh": Decimal("3600"),
}

GHG_TO_TCO2E = {
    "Non-SI:tCO2e": Decimal("1"),
    "Non-SI:ktCO2e": Decimal("1e3"),
}

# Symbols that filers use inconsistently: 'MtCO2e' is used for "metric
# tonnes" in BRSR filings although the unit set also defines ktCO2e and
# GtCO2e, which implies M = mega. Never converted automatically.
AMBIGUOUS_GHG = {
    "Non-SI:MtCO2e": (
        Decimal("1"),
        "symbol MtCO2e is ambiguous (metric tonne vs megatonne); "
        "proposed value assumes metric tonne",
    ),
    "Non-SI:GtCO2e": (
        Decimal("1e9"),
        "symbol GtCO2e (gigatonne) is implausible for a single company; "
        "proposed value assumes gigatonne literally",
    ),
}

MASS_TO_T = {
    "Non-SI:kg": Decimal("1e-3"),
    "Non-SI:t": Decimal("1"),
    "Non-SI:kt": Decimal("1e3"),
}

VOLUME_TO_KL = {
    "in-capmkt-types:kl": Decimal("1"),
    "Non-SI:m3": Decimal("1"),          # 1 m3 = 1 kL exactly
    "Non-SI:l": Decimal("1e-3"),
    "Non-SI:ML": Decimal("1e3"),        # megalitre
}

CURRENCY = {"iso4217:INR"}

PURE = "xbrli:pure"

FAMILIES = {
    "energy": (ENERGY_TO_GJ, "GJ"),
    "tco2e": (GHG_TO_TCO2E, "tCO2e"),
    "tonne": (MASS_TO_T, "t"),
    "kilolitre": (VOLUME_TO_KL, "kL"),
}

PER_INR = {
    "energy_per_inr": "energy",
    "tco2e_per_inr": "tco2e",
    "kilolitre_per_inr": "kilolitre",
    "tonne_per_inr": "tonne",
}


# ==========================================================
# UNIT DEFINITIONS FROM AN XBRL INSTANCE
# ==========================================================

def parse_unit_definitions(xml_path):
    """{unit_id: {'numerator': [...], 'denominator': [...]}}"""

    units = {}

    for _, element in ET.iterparse(xml_path):
        if element.tag != XBRLI + "unit":
            continue

        divide = element.find(XBRLI + "divide")
        if divide is not None:
            numerator = [
                m.text.strip() for m in
                divide.find(XBRLI + "unitNumerator").iter(XBRLI + "measure")
            ]
            denominator = [
                m.text.strip() for m in
                divide.find(XBRLI + "unitDenominator").iter(XBRLI + "measure")
            ]
        else:
            numerator = [
                m.text.strip() for m in element.iter(XBRLI + "measure")
            ]
            denominator = []

        units[element.get("id")] = {
            "numerator": numerator,
            "denominator": denominator,
        }
        element.clear()

    return units


def unit_text(definition):
    if not definition:
        return ""
    numerator = " * ".join(definition["numerator"])
    if definition["denominator"]:
        return f"{numerator} / {' * '.join(definition['denominator'])}"
    return numerator


# ==========================================================
# CONVERSION
# ==========================================================

NUMERIC_TYPE_KEYWORDS = (
    "decimal", "integer", "monetary", "percent", "pure", "float", "double",
    "shares", "energy", "ghg", "mass", "volume", "area",
)


def is_numeric_type(data_type):
    lower = (data_type or "").lower()
    return any(keyword in lower for keyword in NUMERIC_TYPE_KEYWORDS)


def to_decimal(value):
    try:
        return Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None


def result(status, value=None, unit="", factor=None, formula="",
           note="", proposed=None, review=False):
    return {
        "normalised_value": "" if value is None else format(value, "f"),
        "normalised_unit": unit,
        "conversion_factor": "" if factor is None else format(factor, "f"),
        "conversion_formula": formula,
        "conversion_status": status,
        "conversion_note": note,
        "proposed_normalised_value": (
            "" if proposed is None else format(proposed, "f")
        ),
        "needs_review": review,
    }


def _single_measure(definition):
    if (
        definition and len(definition["numerator"]) == 1
        and not definition["denominator"]
    ):
        return definition["numerator"][0]
    return None


def _convert_quantity(number, measure, family, raw_value, raw_unit):
    table, target = FAMILIES[family]

    if family == "tco2e" and measure in AMBIGUOUS_GHG:
        factor, why = AMBIGUOUS_GHG[measure]
        return result(
            "PENDING_APPROVAL_UNIT_ASSUMPTION",
            unit="",
            formula=f"proposed: {raw_value} [{raw_unit}] x {factor} "
                    f"= value [{target}]",
            note=why, proposed=number * factor, review=True,
        )

    if measure not in table:
        return result(
            "NOT_CONVERTED_UNEXPECTED_UNIT",
            note=f"unit {measure} is not a recognised {family} unit",
            review=True,
        )

    factor = table[measure]
    status = "NO_CONVERSION_NEEDED" if factor == 1 else "CONVERTED"
    return result(
        status, number * factor, target, factor,
        f"{raw_value} [{raw_unit}] x {factor} = "
        f"{format(number * factor, 'f')} [{target}]",
    )


def convert(raw_value, definition, family, data_type=""):
    """
    raw_value   original value string
    definition  unit definition dict (or None when the fact has no unit)
    family      expected_unit from the requirement map
    data_type   XBRL data type of the concept in the fact's own taxonomy
    """

    number = to_decimal(raw_value)
    raw_unit = unit_text(definition)

    if number is None:
        return result(
            "NOT_NUMERIC",
            note="text, boolean or date value - passed through unchanged",
        )

    if data_type and not is_numeric_type(data_type):
        # Codes and identifiers (scrip code, firm ID) and older-taxonomy
        # free-text fields can look numeric; never treat them as numbers.
        if family in FAMILIES or family in PER_INR:
            return result(
                "NOT_CONVERTED_TEXT_TYPE",
                note=f"value {raw_value} reported in a {data_type} field "
                     "(no unit) - quantity meaning unconfirmed",
                review=True,
            )
        return result(
            "NOT_NUMERIC",
            note=f"{data_type} value (code/identifier/text) - passed "
                 "through unchanged",
        )

    if not definition:
        return result(
            "NOT_CONVERTED_UNIT_MISSING",
            note="numeric value without a unit", review=True,
        )

    measure = _single_measure(definition)

    # --- plain quantities -----------------------------------
    if family in FAMILIES:
        if measure == PURE:
            return result(
                "NOT_CONVERTED_UNIT_AMBIGUOUS",
                note=f"{family} value reported with unit xbrli:pure "
                     "(no physical unit)", review=True,
            )
        if measure is None:
            return result(
                "NOT_CONVERTED_UNEXPECTED_UNIT",
                note=f"compound unit {raw_unit} for a {family} value",
                review=True,
            )
        return _convert_quantity(number, measure, family, raw_value,
                                 raw_unit)

    # --- per-INR intensities --------------------------------
    if family in PER_INR:
        base = PER_INR[family]
        table, target = FAMILIES[base]

        if measure == PURE:
            return result(
                "NOT_CONVERTED_UNIT_AMBIGUOUS",
                note="intensity reported with unit xbrli:pure - "
                     "numerator unit and denominator (INR/crore/lakh) "
                     "unknown", review=True,
            )

        numerator = definition["numerator"]
        denominator = definition["denominator"]

        if denominator != ["iso4217:INR"] or len(numerator) != 1:
            return result(
                "NOT_CONVERTED_UNEXPECTED_UNIT",
                note=f"intensity unit {raw_unit} is not <quantity>/INR",
                review=True,
            )

        num = numerator[0]
        if base == "tco2e" and num in AMBIGUOUS_GHG:
            factor, why = AMBIGUOUS_GHG[num]
            return result(
                "PENDING_APPROVAL_UNIT_ASSUMPTION",
                formula=f"proposed: {raw_value} [{raw_unit}] x {factor} "
                        f"= value [{target}/INR]",
                note=why, proposed=number * factor, review=True,
            )
        if num not in table:
            return result(
                "NOT_CONVERTED_UNEXPECTED_UNIT",
                note=f"numerator {num} is not a recognised {base} unit",
                review=True,
            )

        factor = table[num]
        status = "NO_CONVERSION_NEEDED" if factor == 1 else "CONVERTED"
        return result(
            status, number * factor, f"{target}/INR", factor,
            f"{raw_value} [{raw_unit}] x {factor} (numerator only; "
            f"denominator INR unchanged) = "
            f"{format(number * factor, 'f')} [{target}/INR]",
        )

    # --- currency --------------------------------------------
    if family == "INR":
        if measure in CURRENCY:
            return result(
                "NO_CONVERSION_NEEDED", number, "INR", Decimal(1),
                f"{raw_value} [{raw_unit}] x 1 = {raw_value} [INR]",
            )
        return result(
            "NOT_CONVERTED_UNEXPECTED_UNIT",
            note=f"currency {raw_unit} is not INR - no FX conversion",
            review=True,
        )

    # --- dimensionless: percentages, counts, rates -----------
    if family == "pure":
        if measure != PURE:
            return result(
                "NOT_CONVERTED_UNEXPECTED_UNIT",
                note=f"unit {raw_unit} where xbrli:pure was expected",
                review=True,
            )

        if data_type == "num:percentItemType":
            if Decimal(0) <= number <= Decimal(1):
                value = number * 100
                return result(
                    "CONVERTED_FRACTION_TO_PERCENT", value, "%",
                    Decimal(100),
                    f"{raw_value} [fraction, num:percentItemType] x 100 "
                    f"= {format(value, 'f')} [%]",
                )
            return result(
                "NOT_CONVERTED_PERCENT_SCALE_AMBIGUOUS",
                note=f"percentItemType value {raw_value} is outside [0, 1]; "
                     "may be a percentage entered as a fraction or a "
                     "filer error", review=True,
            )

        if data_type in ("xbrli:integerItemType",
                         "xbrli:nonNegativeIntegerItemType"):
            return result(
                "NO_CONVERSION_NEEDED", number, "count", Decimal(1),
                f"{raw_value} [pure, integer] = {raw_value} [count]",
            )

        if "percent" in (data_type or "").lower():
            return result(
                "NOT_CONVERTED_PERCENT_SCALE_AMBIGUOUS",
                note=f"data type {data_type} - percent meaning unconfirmed",
                review=True,
            )

        return result(
            "NO_CONVERSION_NEEDED", number, "pure", Decimal(1),
            f"{raw_value} [pure, {data_type or 'unknown type'}] kept as "
            "dimensionless number",
        )

    # --- no expected unit (identity / text requirements) ------
    return result(
        "NOT_CONVERTED_NO_TARGET_UNIT",
        note=f"numeric value with unit {raw_unit}; requirement has no "
             "target unit", review=False,
    )
