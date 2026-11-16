"""
Normalisation engine with the explicit factor model (Phase 8).

For every requirement value:

    final_value = original_value x actual_numeric_multiplier_on_original

The multiplier has exactly one basis:

  RULE_UNIT_CONVERSION       unit conversion rule (app.validators.unit_converter)
                             from the unit defined in the source instance
  APPROVED_UNIT_ASSUMPTION   owner-approved interpretation of an ambiguous
                             unit (data/reference/brsr_mtco2e_unit_decisions.csv)
  APPROVED_SCALE_CORRECTION  owner-approved filer scale / unit-tag correction
                             (filer_scale_error_review/approval/final_review/
                             strong_recommendations_final_review.csv)

Rules enforced here (final review report, owner instructions):
  - the multiplier is applied to the ORIGINAL value only; the legacy factor
    on the normalised value is never used
  - a unit reinterpretation (e.g. Non-SI:J -> GJ) changes the unit, not the
    number: its informational factor is never applied (multiplier 1)
  - a decision is applied only if it is approved (decision / approval
    fields complete) and still matches the value and unit as filed
  - BLOCK_PARTIAL scale records are never applied on their own
  - pending, flagged, undetermined and recommended-only decisions are never
    applied; they are carried as pending_review references
  - no conversion when the unit is missing or ambiguous

Production engine since the owner-approved switch of 2026-11-12: writes
the production files in normalised/ (established columns unchanged, factor
columns appended) and its reports in normalised_factor_model/. Every run
is reconciled against the frozen output of the retired normaliser
(backups/20261112_pre_production_switch/normalised/); differences appear
only when a newly approved decision is applied.

Run:
    python -m app.normalisation.factor_model
"""

from decimal import Decimal, InvalidOperation
from pathlib import Path

import pandas as pd

from app.config import settings
from app.core import deferred_issues
from app.core.logging_setup import get_logger
from app.validators.unit_converter import (convert, parse_unit_definitions,
                                           unit_text)

log = get_logger(__name__, "factor_model")

N = settings.processed_dir / "nifty100"
MAP_FILE = settings.reference_dir / "brsr_requirement_concept_map.csv"
MANIFEST = N / "nse_brsr_extraction_manifest.csv"
VALUES = {"CY": N / "brsr_requirement_values.csv",
          "PY": N / "brsr_requirement_values_py.csv"}
PRODUCTION_DIR = N / "normalised"
PRODUCTION = {"CY": PRODUCTION_DIR / "brsr_requirement_values_normalised.csv",
              "PY": PRODUCTION_DIR /
              "brsr_requirement_values_py_normalised.csv"}
BEFORE_AFTER = PRODUCTION_DIR / "brsr_normalisation_before_after.csv"
UNIT_DEFINITIONS = PRODUCTION_DIR / "brsr_unit_definitions.csv"
# frozen output of the retired normaliser (owner-approved switch
# 2026-11-12); every run is reconciled against it
BASELINE_DIR = N / "backups" / "20261112_pre_production_switch" / "normalised"
BASELINE = {"CY": BASELINE_DIR / "brsr_requirement_values_normalised.csv",
            "PY": BASELINE_DIR / "brsr_requirement_values_py_normalised.csv"}

# production columns appended after the identity columns, in the order the
# retired normaliser wrote them
LEGACY_VALUE_COLUMNS = {
    "original_value": "original_value",
    "original_unit_id": "original_unit_id",
    "original_unit": "original_unit",
    "expected_unit_family": "expected_unit_family",
    "normalised_value": "final_value",
    "normalised_unit": "final_unit",
    "conversion_factor": "actual_numeric_multiplier_on_original",
    "conversion_formula": "conversion_formula",
    "conversion_status": "final_status",
    "conversion_note": "rule_note",
    "proposed_normalised_value": "proposed_value_if_assumption_approved",
    "needs_review": "needs_review",
    "unit_decision": "unit_assumption_decision",
    "unit_decision_evidence": "unit_assumption_evidence",
}
BEFORE_AFTER_COLUMNS = [
    "company_name", "master_isin", "master_nse_symbol", "fy_slot",
    "requirement_id", "metric_key", "breakdown", "metric_name",
    "taxonomy_version", "data_type",
    "original_value", "original_unit_id", "original_unit",
    "normalised_value", "normalised_unit", "conversion_factor",
    "conversion_formula", "conversion_status", "conversion_note",
    "proposed_normalised_value", "unit_decision",
    "unit_decision_evidence", "needs_review",
    "source_file", "context_reference"]
MTCO2E_DECISIONS = settings.reference_dir / "brsr_mtco2e_unit_decisions.csv"
FINAL_REVIEW = (N / "filer_scale_error_review" / "approval" / "final_review")
SCALE_DECISIONS = FINAL_REVIEW / "strong_recommendations_final_review.csv"
UNIT_FACTOR_ISSUES = FINAL_REVIEW / "unit_factor_issues.csv"
OUTPUT_DIR = N / "normalised_factor_model"

KEY3 = ("source_file", "context_reference", "requirement_id")
KEY4 = KEY3 + ("breakdown",)


def dec(value):
    try:
        return Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None


def fmt(d):
    return "" if d is None else format(d, "f")


def same_unit(a, b):
    return "".join((a or "").split()) == "".join((b or "").split())


# ==========================================================
# decisions
# ==========================================================

def load_unit_assumptions():
    t = pd.read_csv(MTCO2E_DECISIONS, dtype=str, keep_default_na=False)
    return {tuple(r[k] for k in KEY3): r for r in t.to_dict("records")}


def load_scale_decisions(path=SCALE_DECISIONS, review_log=None):
    """
    Scale-error records with their approval state. A record decided in the
    source file keeps that decision; otherwise a validated reviewer
    decision from data/reference/review_decisions.csv (Phase 11 workflow)
    fills the approval fields. The engine guards still apply.
    """
    from app.review import workflow
    t = pd.read_csv(path, dtype=str, keep_default_na=False)
    overrides = workflow.fse_overrides(
        review_log or workflow.DECISIONS_LOG, fse=t)
    out = {}
    for r in t.to_dict("records"):
        if not r["approval_decision"].strip() and \
                r["approval_id"] in overrides:
            r = {**r, **overrides[r["approval_id"]]}
        out[tuple(r[k] for k in KEY4)] = r
    return out


def scale_decision_outcome(d, original_value, original_unit):
    """
    Decide whether an owner scale decision can be applied.
    Returns (outcome, multiplier or None, target unit).
    """
    decision = d["approval_decision"].strip().upper()
    if not decision:
        return "PENDING_NOT_APPLIED", None, ""
    if decision in ("REJECT", "REJECTED"):
        return "REJECTED_BY_OWNER", None, ""
    if decision in ("DEFER", "DEFERRED"):
        return "DEFERRED_BY_OWNER", None, ""
    if decision not in ("APPROVE", "APPROVED"):
        return f"UNKNOWN_DECISION_{decision}", None, ""
    if not d["approved_by"].strip() or not d["approval_date"].strip():
        return "REFUSED_INCOMPLETE_APPROVAL", None, ""
    if d["block_dependency"].startswith("BLOCK_PARTIAL"):
        return "REFUSED_BLOCK_PARTIAL_REQUIRES_BLOCK_REVIEW", None, ""
    if dec(d["original_value"]) != dec(original_value) or not same_unit(
            d["original_unit"], original_unit):
        return "REFUSED_STALE_DECISION", None, ""
    multiplier = dec(d["actual_numeric_multiplier_on_original_value"])
    if multiplier is None:
        return "REFUSED_NO_MULTIPLIER", None, ""
    if dec(d["proposed_corrected_value"]) != dec(original_value) * multiplier:
        return "REFUSED_RECORD_INCONSISTENT", None, ""
    return "APPROVED_APPLIED", multiplier, d["proposed_corrected_unit"]


def unit_assumption_outcome(d, original_value):
    decision = d["decision"]
    if not decision.startswith("APPROVED"):
        return f"NOT_APPLIED_{decision or 'NO_DECISION'}", None
    if dec(d["original_value"]) != dec(original_value):
        return "REFUSED_STALE_DECISION", None
    factor = dec(d["approved_factor"])
    if factor is None:
        return "REFUSED_NO_FACTOR", None
    return "APPROVED_APPLIED", factor


# ==========================================================
# engine
# ==========================================================

def factor_row(row, definition, family, assumption=None, scale=None):
    """Factor-model record for one requirement value (pure function)."""
    original = row["value"]
    orig_unit = unit_text(definition)
    rule = convert(original, definition, family, row.get("data_type", ""))
    if row.get("unit_reference") and definition is None:
        rule.update({"conversion_status": "NOT_CONVERTED_UNIT_UNDEFINED",
                     "conversion_note": f"unit id {row['unit_reference']} "
                                        "not defined in the source instance",
                     "normalised_value": "", "normalised_unit": "",
                     "conversion_factor": "", "needs_review": True})

    out = {
        "original_value": original, "original_unit_id":
            row.get("unit_reference", ""), "original_unit": orig_unit,
        "expected_unit_family": family,
        "rule_status": rule["conversion_status"],
        "rule_factor": rule["conversion_factor"],
        "rule_target_unit": rule["normalised_unit"],
        "rule_note": rule["conversion_note"],
        "proposed_value_if_assumption_approved":
            rule["proposed_normalised_value"],
        "unit_assumption_decision": "", "unit_assumption_outcome": "",
        "unit_assumption_factor": "", "unit_assumption_evidence": "",
        "scale_decision_id": "", "scale_recommended_decision": "",
        "scale_decision_outcome": "", "scale_proposed_value": "",
        "unit_reinterpretation_informational": "",
        "unit_reinterpretation_factor_not_applied": "",
        "legacy_factor_not_applied": "",
    }
    multiplier, basis, final_unit = None, "NONE", ""
    status = rule["conversion_status"]

    if rule["conversion_factor"] != "":
        multiplier = Decimal(rule["conversion_factor"])
        basis, final_unit = "RULE_UNIT_CONVERSION", rule["normalised_unit"]

    if assumption is not None:
        outcome, factor = unit_assumption_outcome(assumption, original)
        out.update({"unit_assumption_decision": assumption["decision"],
                    "unit_assumption_outcome": outcome,
                    "unit_assumption_evidence":
                        assumption["approval_evidence"]})
        if outcome == "APPROVED_APPLIED" and \
                status == "PENDING_APPROVAL_UNIT_ASSUMPTION":
            multiplier, basis = factor, "APPROVED_UNIT_ASSUMPTION"
            per_inr = family.endswith("_per_inr")
            final_unit = "tCO2e/INR" if per_inr else "tCO2e"
            status = "CONVERTED_APPROVED_UNIT_ASSUMPTION"
            out["unit_assumption_factor"] = fmt(factor)

    if scale is not None:
        outcome, factor, unit = scale_decision_outcome(scale, original,
                                                       orig_unit)
        out.update({
            "scale_decision_id": scale["approval_id"],
            "scale_recommended_decision": scale["recommended_decision"],
            "scale_decision_outcome": outcome,
            "scale_proposed_value": scale["proposed_corrected_value"],
            "unit_reinterpretation_informational":
                scale["unit_reinterpretation"],
            "unit_reinterpretation_factor_not_applied":
                scale["unit_reinterpretation_factor_informational"],
            "legacy_factor_not_applied":
                scale["legacy_factor_on_current_normalised_value_only"]})
        if outcome == "APPROVED_APPLIED":
            multiplier, basis, final_unit = factor, \
                "APPROVED_SCALE_CORRECTION", unit
            status = "CONVERTED_APPROVED_SCALE_CORRECTION"

    number = dec(original)
    final = number * multiplier if (multiplier is not None
                                    and number is not None) else None
    if basis == "RULE_UNIT_CONVERSION" and final is not None:
        # keep the converter's exact Decimal text (same number; the
        # converter's factor exponent avoids trailing zeros such as
        # 590100.0); the invariant check proves final = original x factor
        final = Decimal(rule["normalised_value"])
    pending = []
    if out["unit_assumption_outcome"].startswith("NOT_APPLIED"):
        pending.append(f"UNIT_ASSUMPTION:{assumption['decision']}")
    if out["scale_decision_outcome"] and \
            out["scale_decision_outcome"] != "APPROVED_APPLIED":
        pending.append(f"{scale['approval_id']}:"
                       f"{out['scale_decision_outcome']}"
                       f"(recommended {scale['recommended_decision']})")
    out.update({
        "actual_numeric_multiplier_on_original": fmt(multiplier),
        "multiplier_basis": basis,
        "final_value": fmt(final), "final_unit": final_unit if final is not
        None else "",
        "final_status": status,
        "factor_formula": (f"{original} [{orig_unit or 'no unit'}] x "
                           f"{fmt(multiplier)} = {fmt(final)} [{final_unit}] "
                           f"({basis})") if final is not None else "",
        "needs_review": bool(rule["needs_review"]) and final is None,
        "pending_review": " | ".join(pending)})
    # production column conversion_formula keeps the established wording
    if basis == "APPROVED_UNIT_ASSUMPTION":
        out["conversion_formula"] = (
            f"{original} [{orig_unit}] x {multiplier} "
            f"({assumption['decision']}) = {fmt(final)} [{final_unit}]")
    elif basis == "APPROVED_SCALE_CORRECTION":
        out["conversion_formula"] = out["factor_formula"]
    else:
        out["conversion_formula"] = rule["conversion_formula"]
    return out


def run(role, values, definitions, families, assumptions, scales, issues):
    rows = []
    for r in values.to_dict("records"):
        definition = definitions.get(r["source_file"], {}).get(
            r.get("unit_reference", "")) if r.get("unit_reference") else None
        key3 = tuple(r[k] for k in KEY3)
        key4 = tuple(r[k] for k in KEY4)
        rec = factor_row(r, definition, families.get(r["requirement_id"], ""),
                         assumptions.get(key3) if role == "CY" else None,
                         scales.get(key4) if role == "CY" else None)
        ids = issues.get((r["master_isin"], r["fy_slot"],
                          r["requirement_id"]), [])
        identity = {k: v for k, v in r.items() if k != "value"}
        rows.append({**identity, **rec,
                     "deferred_issue_ids": " | ".join(ids)})
    return pd.DataFrame(rows)


# ==========================================================
# invariants and reconciliation
# ==========================================================

def invariants(df):
    checks = []

    def check(name, bad):
        checks.append({"invariant": name, "violations": int(bad.sum()),
                       "examples": " | ".join(
                           df.loc[bad, "source_file"].head(3) + ":"
                           + df.loc[bad, "requirement_id"].head(3))})

    has = df["final_value"] != ""
    exact = df.apply(lambda r: r["final_value"] == "" or dec(r["final_value"])
                     == dec(r["original_value"]) * dec(
                         r["actual_numeric_multiplier_on_original"]), axis=1)
    check("final = original x multiplier (exact)", ~exact)
    check("final value only with a multiplier",
          has & (df["actual_numeric_multiplier_on_original"] == ""))
    check("unit assumptions applied only when approved",
          (df["multiplier_basis"] == "APPROVED_UNIT_ASSUMPTION")
          & (df["unit_assumption_outcome"] != "APPROVED_APPLIED"))
    check("scale corrections applied only when approved",
          (df["multiplier_basis"] == "APPROVED_SCALE_CORRECTION")
          & (df["scale_decision_outcome"] != "APPROVED_APPLIED"))
    check("pending / unapproved decisions never produce a value",
          has & (df["pending_review"].str.contains("UNIT_ASSUMPTION:")))
    reint = df["unit_reinterpretation_informational"] != ""
    check("unit reinterpretation factor never applied",
          reint & has & (df["actual_numeric_multiplier_on_original"]
                         == df["unit_reinterpretation_factor_not_applied"])
          & (df["multiplier_basis"] == "APPROVED_SCALE_CORRECTION"))
    check("ambiguous / missing units never converted",
          has & df["final_status"].str.startswith("NOT_CONVERTED"))
    return pd.DataFrame(checks)


def reconcile(df, existing):
    e = pd.read_csv(existing, dtype=str, keep_default_na=False)
    key = list(KEY4)
    m = df.merge(e[key + ["normalised_value", "normalised_unit",
                          "conversion_factor", "conversion_status"]],
                 on=key, how="outer", indicator=True)
    m["value_same"] = m["final_value"] == m["normalised_value"]
    m["value_numerically_same"] = [
        a == b if a == "" or b == "" else dec(a) == dec(b)
        for a, b in zip(m["final_value"].fillna(""),
                        m["normalised_value"].fillna(""))]
    m["unit_same"] = m["final_unit"] == m["normalised_unit"]
    m["factor_same"] = m["actual_numeric_multiplier_on_original"] == \
        m["conversion_factor"]
    m["status_same"] = m["final_status"] == m["conversion_status"]
    m["identical"] = (m["_merge"] == "both") & m["value_same"] & \
        m["unit_same"] & m["factor_same"] & m["status_same"]
    summary = (m.groupby(["conversion_status", "final_status"], dropna=False)
               .agg(rows=("identical", "size"),
                    identical=("identical", "sum")).reset_index())
    return m, summary


# ==========================================================
# build
# ==========================================================

def load_definitions():
    manifest = pd.read_csv(MANIFEST, dtype=str, keep_default_na=False)
    return {Path(p).name: parse_unit_definitions(p)
            for p in manifest["local_file_path"] if p}


def unit_definition_rows(definitions):
    return pd.DataFrame([
        {"source_file": source_file, "unit_id": unit_id,
         "numerator": " * ".join(d["numerator"]),
         "denominator": " * ".join(d["denominator"]),
         "unit_definition": unit_text(d)}
        for source_file, units in definitions.items()
        for unit_id, d in units.items()])


def read_production(path):
    """
    Read a production file and restore the factor-model field names
    (normalised_value -> final_value, conversion_factor ->
    actual_numeric_multiplier_on_original, ...) so engines can use them.
    """
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    for legacy, factor in LEGACY_VALUE_COLUMNS.items():
        if factor not in df.columns:
            df[factor] = df[legacy]
    return df


def production_frame(df, values_columns):
    """
    Production layout: identity columns, then the established value
    columns (same names, order and meaning), then the factor-model columns.
    """
    identity = [c for c in values_columns if c != "value"]
    legacy = pd.DataFrame({new: df[src] for new, src in
                           LEGACY_VALUE_COLUMNS.items()})
    extra = [c for c in df.columns if c not in identity
             and c not in LEGACY_VALUE_COLUMNS.values()]
    return pd.concat([df[identity], legacy, df[extra]], axis=1)


def build(scale_decisions=SCALE_DECISIONS, output_dir=OUTPUT_DIR,
          write=True, production_dir=PRODUCTION_DIR):
    families = (pd.read_csv(MAP_FILE, dtype=str, keep_default_na=False)
                .drop_duplicates("requirement_id")
                .set_index("requirement_id")["expected_unit"].to_dict())
    definitions = load_definitions()
    assumptions = load_unit_assumptions()
    scales = load_scale_decisions(scale_decisions)
    issues = deferred_issues.issue_index()

    results, recon, inv, production = {}, {}, [], {}
    for role, path in VALUES.items():
        values = pd.read_csv(path, dtype=str, keep_default_na=False)
        df = run(role, values, definitions, families, assumptions, scales,
                 issues)
        results[role] = df
        production[role] = production_frame(df, list(values.columns))
        baseline = BASELINE[role] if BASELINE[role].exists()             else PRODUCTION[role]
        merged, summary = reconcile(df, baseline)
        recon[role] = (merged, summary)
        inv.append(invariants(df).assign(file=role))

    cy = results["CY"]
    decisions = pd.concat([
        cy[cy["unit_assumption_decision"] != ""][
            list(KEY4) + ["master_nse_symbol", "fy_slot", "original_value",
                          "original_unit", "unit_assumption_decision",
                          "unit_assumption_outcome", "final_value",
                          "final_unit", "final_status"]]
        .assign(decision_source="brsr_mtco2e_unit_decisions.csv"),
        cy[cy["scale_decision_id"] != ""][
            list(KEY4) + ["master_nse_symbol", "fy_slot", "original_value",
                          "original_unit", "scale_decision_id",
                          "scale_recommended_decision",
                          "scale_decision_outcome", "scale_proposed_value",
                          "unit_reinterpretation_informational",
                          "unit_reinterpretation_factor_not_applied",
                          "legacy_factor_not_applied", "final_value",
                          "final_unit", "final_status"]]
        .assign(decision_source="strong_recommendations_final_review.csv"),
    ], ignore_index=True)
    invariants_df = pd.concat(inv, ignore_index=True)
    recon_summary = pd.concat([s.assign(file=role)
                               for role, (_, s) in recon.items()])
    differences = pd.concat([m[~m["identical"]].assign(file=role)
                             for role, (m, _) in recon.items()])

    if write:
        # production outputs (normalised/)
        production_dir.mkdir(parents=True, exist_ok=True)
        cy_prod = production["CY"]
        cy_prod.to_csv(production_dir / PRODUCTION["CY"].name, index=False,
                       encoding="utf-8-sig")
        production["PY"].to_csv(production_dir / PRODUCTION["PY"].name,
                                index=False, encoding="utf-8-sig")
        cy_prod[cy_prod["conversion_status"] != "NOT_NUMERIC"][
            BEFORE_AFTER_COLUMNS].to_csv(
            production_dir / BEFORE_AFTER.name, index=False,
            encoding="utf-8-sig")
        unit_definition_rows(definitions).to_csv(
            production_dir / UNIT_DEFINITIONS.name, index=False,
            encoding="utf-8-sig")
        # engine reports (normalised_factor_model/)
        output_dir.mkdir(parents=True, exist_ok=True)
        decisions.to_csv(output_dir / "factor_model_decisions.csv",
                         index=False, encoding="utf-8-sig")
        invariants_df.to_csv(output_dir / "factor_model_invariants.csv",
                             index=False, encoding="utf-8-sig")
        recon_summary.to_csv(output_dir / "factor_model_reconciliation.csv",
                             index=False, encoding="utf-8-sig")
        differences.to_csv(output_dir / "factor_model_differences.csv",
                           index=False, encoding="utf-8-sig")
    log.info("factor model built", extra={"fields": {
        "cy": len(results["CY"]), "py": len(results["PY"]),
        "differences": len(differences),
        "invariant_violations": int(invariants_df["violations"].sum())}})
    return {"results": results, "production": production,
            "decisions": decisions,
            "invariants": invariants_df, "recon_summary": recon_summary,
            "differences": differences}


def main():
    out = build()
    for role, df in out["results"].items():
        print(f"{role}: {len(df)} values")
        print(df.groupby(["multiplier_basis", "final_status"]).size()
              .to_string())
    print("\ndecisions:")
    print(out["decisions"].groupby(["decision_source",
                                    "unit_assumption_outcome",
                                    "scale_decision_outcome"], dropna=False)
          .size().to_string())
    print("\ninvariant violations:",
          int(out["invariants"]["violations"].sum()))
    print("rows differing from the pre-switch baseline:",
          len(out["differences"]))
    print(f"\nOutputs: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
