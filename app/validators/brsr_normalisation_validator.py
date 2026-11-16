"""
Validation of normalised BRSR requirement values.

Reads the normaliser outputs and writes (data/processed/nifty100/normalised/):
  brsr_normalisation_validation_issues.csv   one row per issue
  brsr_normalisation_summary.csv             requirement x conversion status
  brsr_mtco2e_assumption_evidence.csv        evidence for the MtCO2e unit
  brsr_unresolved_review_items.csv           rows that need a manual decision

Checks
  C01 RECOMPUTE            normalised == original x factor (Decimal)
  C02 STATUS_CONSISTENCY   converted rows carry value/unit/factor/formula;
                           unconverted rows carry no normalised value
  C03 UNIT_UNIFORMITY      one normalised unit per requirement
  C04 DUPLICATES           same company/slot/requirement/breakdown twice
  C05 RANGE                negative values, percentages above 100
  C06 INTENSITY_RECOMPUTE  reported per-INR intensity vs total / turnover
  C07 COMPONENT_SUMS       energy, water and waste components vs totals
  C08 NEXT_REPORT_PY       value vs the next report's restated comparative
  C09 MTCO2E_EVIDENCE      plausibility of reading MtCO2e as metric tonnes
  C10 YOY_OUTLIER          > 10x change between consecutive slots
  C11 UNIT_DECISION_CONSISTENCY  conflicting approved readings of the
                           same ambiguous unit within one report

Nothing is corrected here; issues are reported for review.

Run:
    python -m app.validators.brsr_normalisation_validator
"""

from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pandas as pd


BASE_DIR = Path(__file__).resolve().parents[2]
NORM_DIR = BASE_DIR / "data" / "processed" / "nifty100" / "normalised"

CY_FILE = NORM_DIR / "brsr_requirement_values_normalised.csv"
PY_FILE = NORM_DIR / "brsr_requirement_values_py_normalised.csv"

ISSUES_OUTPUT = NORM_DIR / "brsr_normalisation_validation_issues.csv"
SUMMARY_OUTPUT = NORM_DIR / "brsr_normalisation_summary.csv"
MTCO2E_OUTPUT = NORM_DIR / "brsr_mtco2e_assumption_evidence.csv"
UNRESOLVED_OUTPUT = NORM_DIR / "brsr_unresolved_review_items.csv"
MTCO2E_SUMMARY_OUTPUT = NORM_DIR / "brsr_mtco2e_decision_summary.csv"

UNIT_DECISIONS_FILE = (
    BASE_DIR / "data" / "reference" / "brsr_mtco2e_unit_decisions.csv"
)

CONVERTED_STATUSES = {
    "CONVERTED", "NO_CONVERSION_NEEDED", "CONVERTED_FRACTION_TO_PERCENT",
    "CONVERTED_APPROVED_UNIT_ASSUMPTION",
}

SCALE_HINTS = {
    Decimal("1e7"): "per crore (1e7) instead of per INR",
    Decimal("1e5"): "per lakh (1e5) instead of per INR",
    Decimal("1e6"): "per million (1e6) instead of per INR",
    Decimal("1e3"): "factor 1e3 (e.g. thousand / kilo)",
    Decimal("1e9"): "factor 1e9 (e.g. J vs GJ)",
}

# Implied (Scope 1 + Scope 2) tCO2e per GJ of energy. Grid electricity in
# India is about 0.2 tCO2e/GJ, coal about 0.095, natural gas about 0.056.
EMISSION_FACTOR_RANGE = (Decimal("0.005"), Decimal("1.0"))

TOLERANCE = Decimal("0.01")           # 1 % for sums and restatements
INTENSITY_TOLERANCE = Decimal("0.10")


def dec(value):
    try:
        if value in ("", None):
            return None
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


class Issues:
    def __init__(self):
        self.rows = []

    def add(self, check, severity, row_or_key, detail, **extra):
        if isinstance(row_or_key, dict):
            key = {
                "company_name": row_or_key.get("company_name", ""),
                "master_isin": row_or_key.get("master_isin", ""),
                "master_nse_symbol": row_or_key.get("master_nse_symbol", ""),
                "fy_slot": row_or_key.get("fy_slot", ""),
                "requirement_id": row_or_key.get("requirement_id", ""),
                "breakdown": row_or_key.get("breakdown", ""),
                "source_file": row_or_key.get("source_file", ""),
            }
        else:
            key = row_or_key
        self.rows.append({
            "check": check, "severity": severity, **key,
            "detail": detail, **extra,
        })


def nearest_scale(ratio):
    if ratio is None or ratio <= 0:
        return None
    for scale, hint in SCALE_HINTS.items():
        for candidate in (scale, 1 / scale):
            if abs(ratio / candidate - 1) <= INTENSITY_TOLERANCE:
                return hint + ("" if candidate == scale else " (inverse)")
    return None


# ==========================================================
# C01 - C05: row-level checks
# ==========================================================

def row_checks(cy, issues):
    for row in cy.to_dict("records"):
        status = row["conversion_status"]
        value = dec(row["normalised_value"])
        original = dec(row["original_value"])
        factor = dec(row["conversion_factor"])

        if status in CONVERTED_STATUSES:
            missing = [
                c for c in ("normalised_value", "normalised_unit",
                            "conversion_factor", "conversion_formula")
                if not row[c]
            ]
            if missing:
                issues.add("C02_STATUS_CONSISTENCY", "ERROR", row,
                           f"{status} row missing {missing}")
            elif original * factor != value:
                issues.add("C01_RECOMPUTE", "ERROR", row,
                           f"{original} x {factor} = {original * factor} "
                           f"but normalised_value = {value}")
        elif row["normalised_value"]:
            issues.add("C02_STATUS_CONSISTENCY", "ERROR", row,
                       f"{status} row has a normalised value")

        if value is not None and value < 0:
            issues.add("C05_RANGE", "WARNING", row,
                       f"negative value {value} {row['normalised_unit']}")
        if (
            row["normalised_unit"] == "%"
            and value is not None and value > 100
        ):
            issues.add("C05_RANGE", "WARNING", row,
                       f"percentage above 100: {value}")

    units = (
        cy[cy["normalised_unit"] != ""]
        .groupby("requirement_id")["normalised_unit"].agg(set)
    )
    for requirement_id, unit_set in units.items():
        if len(unit_set) > 1:
            issues.add("C03_UNIT_UNIFORMITY", "ERROR",
                       {"requirement_id": requirement_id},
                       f"multiple normalised units: {sorted(unit_set)}")

    keys = ["master_isin", "fy_slot", "requirement_id", "breakdown"]
    for _, group in cy.groupby(keys):
        if len(group) < 2:
            continue
        values = set(
            group["normalised_value"].where(
                group["normalised_value"] != "", group["original_value"]
            ).map(lambda v: dec(v) if dec(v) is not None else v)
        )
        first = group.iloc[0].to_dict()
        concepts = ", ".join(sorted(set(group["metric_name"])))
        if len(values) == 1:
            issues.add("C04_DUPLICATES", "INFO", first,
                       f"{len(group)} facts with the same value "
                       f"({concepts})")
        else:
            issues.add("C04_DUPLICATES", "WARNING", first,
                       f"{len(group)} facts with different values "
                       f"{sorted(map(str, values))} ({concepts})")


# ==========================================================
# Slot-level lookups
# ==========================================================

def slot_values(cy, use_proposed=False):
    """{(isin, slot): {requirement_id: Decimal}} for totals (no breakdown)."""

    table = defaultdict(dict)
    totals = cy[cy["breakdown"] == ""]
    for row in totals.to_dict("records"):
        value = dec(row["normalised_value"])
        if value is None and use_proposed:
            value = dec(row["proposed_normalised_value"])
        if value is not None:
            table[(row["master_isin"], row["fy_slot"])].setdefault(
                row["requirement_id"], value
            )
    return table


def slot_key(isin, slot, cy_index):
    row = cy_index.get((isin, slot), {})
    return {
        "company_name": row.get("company_name", ""),
        "master_isin": isin,
        "master_nse_symbol": row.get("master_nse_symbol", ""),
        "fy_slot": slot,
        "requirement_id": "",
        "breakdown": "",
        "source_file": row.get("source_file", ""),
    }


# ==========================================================
# C06 intensity recompute, C07 component sums
# ==========================================================

INTENSITY_CHECKS = [
    ("E08", ["E07"], "energy intensity per INR"),
    ("E19", ["E17", "E18"], "Scope 1+2 intensity per INR"),
    ("E16", ["E14"], "water intensity per INR (withdrawal basis)"),
]

SUM_CHECKS = [
    ("E07", ["E05", "E06"], "renewable + non-renewable energy"),
    ("E14", ["E09", "E10", "E11", "E12", "E13"], "water withdrawal sources"),
    ("E29", ["E21", "E22", "E23", "E24", "E25", "E26", "E27", "E28"],
     "waste categories"),
    # E32 (total recovered) is not checked: it also includes "other
    # recovery operations", which is not in the requirement map.
]


def intensity_and_sum_checks(cy, issues):
    values = slot_values(cy, use_proposed=True)
    cy_index = {
        (r["master_isin"], r["fy_slot"]): r for r in cy.to_dict("records")
    }

    for (isin, slot), metrics in values.items():
        key = slot_key(isin, slot, cy_index)
        turnover = metrics.get("A06")

        for target, parts, label in INTENSITY_CHECKS:
            reported = metrics.get(target)
            if reported is None or turnover in (None, 0):
                continue
            if any(p not in metrics for p in parts):
                continue
            total = sum(metrics[p] for p in parts)

            if target == "E16" and "E15" in metrics:
                candidates = [total, metrics["E15"]]
            else:
                candidates = [total]

            ratios = [
                reported / (c / turnover) for c in candidates if c
            ]
            if not ratios:
                continue
            if any(abs(r - 1) <= INTENSITY_TOLERANCE for r in ratios):
                continue

            ratio = ratios[0]
            hint = nearest_scale(ratio)
            issues.add(
                "C06_INTENSITY_RECOMPUTE", "WARNING",
                {**key, "requirement_id": target},
                f"{label}: reported {reported} vs recomputed "
                f"{(candidates[0] / turnover):.6E} (ratio {ratio:.4G})"
                + (f" - looks like {hint}" if hint else ""),
            )

        for target, parts, label in SUM_CHECKS:
            if target not in metrics:
                continue
            present = [p for p in parts if p in metrics]
            if len(present) < 2:
                continue
            total = metrics[target]
            parts_sum = sum(metrics[p] for p in present)
            base = max(abs(total), abs(parts_sum), Decimal("1e-30"))
            if abs(total - parts_sum) / base > TOLERANCE:
                issues.add(
                    "C07_COMPONENT_SUMS", "WARNING",
                    {**key, "requirement_id": target},
                    f"{label}: total {total} vs sum of {present} = "
                    f"{parts_sum}",
                )


# ==========================================================
# C08 next report's restated comparative
# ==========================================================

def py_crosscheck(cy, py, issues):
    def period(row):
        return (row["period_start"], row["period_end"], row["instant_date"])

    py_index = {}
    for row in py.to_dict("records"):
        key = (row["master_isin"], row["requirement_id"], row["breakdown"],
               row["metric_name"], period(row))
        py_index.setdefault(key, row)

    for row in cy.to_dict("records"):
        if row["conversion_status"] == "NOT_NUMERIC":
            continue
        key = (row["master_isin"], row["requirement_id"], row["breakdown"],
               row["metric_name"], period(row))
        restated = py_index.get(key)
        if restated is None or restated["source_file"] == row["source_file"]:
            continue

        mine = dec(row["normalised_value"])
        theirs = dec(restated["normalised_value"])
        detail_base = (
            f"next report ({restated['source_file']}) restates as "
            f"{restated['original_value']} [{restated['original_unit']}]"
        )

        if mine is None and theirs is not None:
            issues.add(
                "C08_NEXT_REPORT_PY", "INFO", row,
                f"value not converted ({row['conversion_status']}); "
                f"{detail_base} = {theirs} {restated['normalised_unit']} - "
                "resolution available, not applied",
                suggested_value=str(theirs),
                suggested_unit=restated["normalised_unit"],
            )
            continue

        if mine is None or theirs is None:
            continue

        base = max(abs(mine), abs(theirs), Decimal("1e-30"))
        if abs(mine - theirs) / base <= TOLERANCE:
            continue

        ratio = mine / theirs if theirs else None
        hint = nearest_scale(ratio) if ratio else None
        severity = "ERROR" if hint else "INFO"
        check_detail = (
            f"{mine} {row['normalised_unit']} vs {detail_base} = {theirs} "
            f"{restated['normalised_unit']}"
        )
        if hint:
            check_detail += f" - scale mismatch, {hint}"
        else:
            check_detail += " - restated in the following report"
        issues.add("C08_NEXT_REPORT_PY", severity, row, check_detail)


# ==========================================================
# C09 MtCO2e evidence
# ==========================================================

AMBIGUOUS_GHG_UNITS = ("Non-SI:MtCO2e", "Non-SI:GtCO2e")


def load_unit_decisions():
    if not UNIT_DECISIONS_FILE.exists():
        return {}
    table = pd.read_csv(UNIT_DECISIONS_FILE, dtype=str).fillna("")
    return {
        (r["source_file"], r["context_reference"], r["requirement_id"]): r
        for r in table.to_dict("records")
    }


def mtco2e_evidence(cy, py, issues):
    # Select by ORIGINAL unit so approved (converted) rows stay in the
    # evidence file alongside pending ones.
    pending = cy[
        cy["original_unit"].str.split(" / ").str[0].isin(AMBIGUOUS_GHG_UNITS)
        & cy["requirement_id"].isin(["E17", "E18", "E19", "E20"])
    ]
    decisions = load_unit_decisions()

    # Both interpretations are tested on the ORIGINAL numbers: for rows in
    # an ambiguous unit use the original value (= metric-tonne reading),
    # otherwise the normalised tCO2e value.
    values = defaultdict(dict)
    for row in cy[cy["breakdown"] == ""].to_dict("records"):
        unit = row["original_unit"].split(" / ")[0]
        if unit in AMBIGUOUS_GHG_UNITS:
            value = dec(row["original_value"])
        else:
            value = dec(row["normalised_value"])
        if value is not None:
            values[(row["master_isin"], row["fy_slot"])].setdefault(
                row["requirement_id"], value
            )

    py_index = {}
    for row in py.to_dict("records"):
        py_index.setdefault(
            (row["master_isin"], row["requirement_id"], row["breakdown"],
             row["period_end"]), row,
        )

    rows = []
    for row in pending.to_dict("records"):
        metrics = values.get((row["master_isin"], row["fy_slot"]), {})
        energy = metrics.get("E07")
        scope12 = (
            metrics.get("E17", Decimal(0)) + metrics.get("E18", Decimal(0))
            if "E17" in metrics or "E18" in metrics else None
        )

        low, high = EMISSION_FACTOR_RANGE
        factor = (
            scope12 / energy if scope12 is not None and energy else None
        )
        factor_mega = factor * Decimal("1e6") if factor is not None else None

        if factor is None:
            plausibility = "NO_ENERGY_DATA"
        elif low <= factor <= high:
            plausibility = "PLAUSIBLE_AS_METRIC_TONNE"
        elif low <= factor_mega <= high:
            plausibility = "PLAUSIBLE_AS_MEGATONNE"
        else:
            plausibility = "NEITHER_PLAUSIBLE_CHECK_ENERGY_OR_EMISSIONS"

        # Strongest evidence: the next report restates this value in the
        # unambiguous unit tCO2e.
        restated = py_index.get(
            (row["master_isin"], row["requirement_id"], row["breakdown"],
             row["period_end"])
        )
        restated_note, confirmation = "", ""
        original = dec(row["original_value"])
        if restated is not None and restated["source_file"] != row["source_file"]:
            restated_note = (
                f"{restated['original_value']} [{restated['original_unit']}]"
                f" {restated['conversion_status']}"
            )
            theirs = dec(restated["normalised_value"])
            if (
                restated["conversion_status"] in CONVERTED_STATUSES
                and theirs is not None and original is not None
            ):
                def close(a, b):
                    # Purely relative tolerance: intensities are ~1e-6, so
                    # an absolute floor would make any two values "equal".
                    scale = max(abs(a), abs(b))
                    return scale > 0 and abs(a - b) <= TOLERANCE * scale

                if original == 0 or theirs == 0:
                    confirmation = "ZERO_VALUE_UNINFORMATIVE"
                elif close(theirs, original):
                    confirmation = "CONFIRMED_METRIC_TONNE_BY_RESTATEMENT"
                elif close(theirs, original * Decimal("1e6")):
                    confirmation = "CONFIRMED_MEGATONNE_BY_RESTATEMENT"
                else:
                    confirmation = "RESTATEMENT_DIFFERS"

        if confirmation.startswith("CONFIRMED_METRIC"):
            recommended = "METRIC_TONNE"
        elif confirmation.startswith("CONFIRMED_MEGA"):
            recommended = "MEGATONNE"
        elif plausibility == "PLAUSIBLE_AS_METRIC_TONNE":
            recommended = "METRIC_TONNE"
        elif plausibility == "PLAUSIBLE_AS_MEGATONNE":
            recommended = "MEGATONNE"
        else:
            recommended = "UNDETERMINED"

        decision = decisions.get(
            (row["source_file"], row["context_reference"],
             row["requirement_id"]), {}
        )

        rows.append({
            "company_name": row["company_name"],
            "master_isin": row["master_isin"],
            "master_nse_symbol": row["master_nse_symbol"],
            "fy_slot": row["fy_slot"],
            "requirement_id": row["requirement_id"],
            "breakdown": row["breakdown"],
            "context_reference": row["context_reference"],
            "original_value": row["original_value"],
            "original_unit": row["original_unit"],
            "conversion_status": row["conversion_status"],
            "normalised_value": row["normalised_value"],
            "normalised_unit": row["normalised_unit"],
            "unit_decision": decision.get("decision", "NOT_YET_DECIDED"),
            "approved_factor": decision.get("approved_factor", ""),
            "approval_evidence": decision.get("approval_evidence", ""),
            "decided_by": decision.get("decided_by", ""),
            "value_if_metric_tonne_tco2e": (
                "" if original is None else format(original, "f")
            ),
            "value_if_megatonne_tco2e": (
                "" if original is None
                else format(original * Decimal("1e6"), "f")
            ),
            "implied_tco2e_per_gj_as_tonne": (
                "" if factor is None else f"{factor:.6G}"
            ),
            "implied_tco2e_per_gj_as_megatonne": (
                "" if factor_mega is None else f"{factor_mega:.6G}"
            ),
            "plausibility": plausibility,
            "next_report_restatement": restated_note,
            "restatement_evidence": confirmation,
            "recommended_interpretation": recommended,
            "source_file": row["source_file"],
        })

        if decision.get("decision", "").startswith("APPROVED"):
            continue
        if recommended == "UNDETERMINED":
            issues.add("C09_MTCO2E_EVIDENCE", "WARNING", row,
                       f"MtCO2e meaning undetermined: implied "
                       f"{factor} tCO2e/GJ as tonnes; {plausibility}")
        elif recommended == "MEGATONNE":
            issues.add("C09_MTCO2E_EVIDENCE", "WARNING", row,
                       "MtCO2e appears to mean megatonnes (million "
                       f"tonnes): {plausibility} {confirmation}")

    pd.DataFrame(rows).to_csv(MTCO2E_OUTPUT, index=False,
                              encoding="utf-8-sig")
    return pd.DataFrame(rows)


# ==========================================================
# C11 unit-decision consistency within a report
# ==========================================================

def unit_decision_consistency(cy, issues):
    """
    The same ambiguous unit symbol in the same report should be read the
    same way. Conflicting approvals (e.g. E17 approved as metric tonne and
    E19 as megatonne in one filing) are reported as errors.
    """

    approved = cy[
        cy["conversion_status"] == "CONVERTED_APPROVED_UNIT_ASSUMPTION"
    ].copy()
    if not len(approved):
        return
    approved["symbol"] = approved["original_unit"].str.split(" / ").str[0]

    for (source_file, symbol), group in approved.groupby(
        ["source_file", "symbol"]
    ):
        readings = set(group["unit_decision"])
        if len(readings) > 1:
            detail = "; ".join(
                f"{r.requirement_id}={r.unit_decision}"
                for r in group.itertuples()
            )
            first = group.iloc[0].to_dict()
            issues.add(
                "C11_UNIT_DECISION_CONSISTENCY", "ERROR",
                {**first, "requirement_id": "/".join(
                    sorted(set(group["requirement_id"])))},
                f"conflicting approvals for {symbol} in one report: "
                f"{detail}",
            )


# ==========================================================
# C10 year-on-year outliers
# ==========================================================

YOY_REQUIREMENTS = ["A06", "E07", "E14", "E17", "E18", "E29"]


def yoy_outliers(cy, issues):
    values = slot_values(cy)
    cy_index = {
        (r["master_isin"], r["fy_slot"]): r for r in cy.to_dict("records")
    }
    by_company = defaultdict(dict)
    for (isin, slot), metrics in values.items():
        by_company[isin][slot] = metrics

    for isin, slots in by_company.items():
        ordered = sorted(slots)
        for previous, current in zip(ordered, ordered[1:]):
            for requirement_id in YOY_REQUIREMENTS:
                a = slots[previous].get(requirement_id)
                b = slots[current].get(requirement_id)
                if not a or not b:
                    continue
                ratio = b / a
                if ratio > 10 or ratio < Decimal("0.1"):
                    hint = nearest_scale(ratio)
                    issues.add(
                        "C10_YOY_OUTLIER", "WARNING",
                        {**slot_key(isin, current, cy_index),
                         "requirement_id": requirement_id},
                        f"{previous}: {a} -> {current}: {b} "
                        f"(x{ratio:.4G})"
                        + (f" - possible {hint}" if hint else ""),
                    )


# ==========================================================
# MAIN
# ==========================================================

def main():
    cy = pd.read_csv(CY_FILE, dtype=str, keep_default_na=False)
    py = pd.read_csv(PY_FILE, dtype=str, keep_default_na=False)

    issues = Issues()
    row_checks(cy, issues)
    intensity_and_sum_checks(cy, issues)
    py_crosscheck(cy, py, issues)
    evidence = mtco2e_evidence(cy, py, issues)
    yoy_outliers(cy, issues)
    unit_decision_consistency(cy, issues)

    issue_frame = pd.DataFrame(issues.rows)
    issue_frame.to_csv(ISSUES_OUTPUT, index=False, encoding="utf-8-sig")

    summary = (
        cy.pivot_table(
            index=["requirement_id", "metric_key", "expected_unit_family"],
            columns="conversion_status", values="original_value",
            aggfunc="count", fill_value=0,
        ).reset_index()
    )
    summary.to_csv(SUMMARY_OUTPUT, index=False, encoding="utf-8-sig")

    unresolved = cy[cy["needs_review"].astype(str) == "True"]
    unresolved.to_csv(UNRESOLVED_OUTPUT, index=False, encoding="utf-8-sig")

    print("=" * 72)
    print("NORMALISATION VALIDATION")
    print("=" * 72)
    print("CY values:", len(cy))
    print("\nConversion status:")
    print(cy["conversion_status"].value_counts().to_string())

    if len(issue_frame):
        print("\nIssues by check and severity:")
        print(pd.crosstab(issue_frame["check"], issue_frame["severity"])
              .to_string())

    if len(evidence):
        summary_mt = (
            evidence.groupby(
                ["unit_decision", "recommended_interpretation",
                 "restatement_evidence"], dropna=False,
            ).size().reset_index(name="records")
        )
        summary_mt["restatement_evidence"] = summary_mt[
            "restatement_evidence"
        ].replace("", "NO_RESTATEMENT")
        summary_mt.to_csv(MTCO2E_SUMMARY_OUTPUT, index=False,
                          encoding="utf-8-sig")
        print("\nMtCO2e unit decisions:")
        print(evidence["unit_decision"].value_counts().to_string())
        print("\nMtCO2e evidence by decision:")
        print(pd.crosstab(evidence["recommended_interpretation"],
                          evidence["restatement_evidence"].replace(
                              "", "no restatement")).to_string())
        by_company = (
            evidence.groupby("master_nse_symbol")["recommended_interpretation"]
            .agg(lambda s: "/".join(sorted(set(s))))
        )
        print("\nCompanies by recommended interpretation:")
        print(by_company.value_counts().to_string())
        print(by_company[by_company != "METRIC_TONNE"].to_string())

    print("\nRows needing manual review:", len(unresolved))
    print(unresolved.groupby(["conversion_status", "requirement_id"])
          .size().to_string())

    print("\nOutputs:")
    for path in (ISSUES_OUTPUT, SUMMARY_OUTPUT, MTCO2E_OUTPUT,
                 UNRESOLVED_OUTPUT):
        print(" ", path)


if __name__ == "__main__":
    main()
