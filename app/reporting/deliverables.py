"""
Phase 15 deliverables: the 20 output files of the master prompt (section
17), the company x FY coverage summary (section 16) and a versioned
manifest, written to data/outputs/.

Source of truth: the approved outputs of Phases 3-13. Nothing is
recalculated, reconverted, corrected or reinterpreted:
  - exact copies are byte-identical to their source;
  - assembled files only select, join and label existing columns;
  - every pending / deferred / candidate / unresolved record is kept, with
    its status columns (pillar files contain ALL applicable rows).

Derived labels (documented in DATA_DICTIONARY.md):
  record_status    summary of existing statuses with a fixed precedence;
                   never upgrades a pending value to verified
  coverage_status  pipeline coverage status, refined only by evidence:
                   EXTRACTION_ERROR when the filing exists but download or
                   processing failed, or when the concept is present with a
                   CY value in the parsed facts but no requirement value was
                   selected
Supplementary PDF evidence (Reliance only) is never merged into values;
it appears only as candidate counts in the missing-metrics report.

Run:
    python -m app.reporting.deliverables
    (or: python -m app.pipeline report)
"""

import hashlib
import json
import os
import shutil
from datetime import datetime
from pathlib import Path

import pandas as pd

from app.config import settings
from app.core.logging_setup import get_logger

log = get_logger(__name__, "deliverables")

P = settings.processed_dir
N = P / "nifty100"
OUT = settings.data_dir / "outputs"
SRC = {
    "company_master": P / "registry" / "company_master.csv",
    "filing_manifest": P / "registry" / "filing_manifest.csv",
    "taxonomy_current": P / "nse_brsr_taxonomy_concepts.csv",
    "taxonomy_registry": P / "taxonomy" / "taxonomy_registry.csv",
    "metric_availability": P / "metrics" / "metric_version_availability.csv",
    "metric_concepts": P / "metrics" / "metric_concepts.csv",
    "req_map": settings.reference_dir / "brsr_requirement_concept_map.csv",
    "facts": N / "parsed" / "xbrl_facts.csv",
    "prod_cy": N / "normalised" / "brsr_requirement_values_normalised.csv",
    "prod_py": N / "normalised" / "brsr_requirement_values_py_normalised.csv",
    "coverage": N / "brsr_requirement_coverage.csv",
    "crosswalk": P / "taxonomy_versions" / "nse_brsr_concept_crosswalk.csv",
    "parse_issues": N / "parsed" / "xbrl_parse_issues.csv",
    "validation": N / "validation" / "validation_results.csv",
    "queue": N / "review_workflow" / "review_queue.csv",
    "blocks": N / "blocks" / "block_status.csv",
    "block_packages": N / "blocks" / "block_review_packages.csv",
    "fse": N / "filer_scale_error_review" / "approval" / "final_review" /
    "strong_recommendations_final_review.csv",
    "c08_reeval": N / "review" / "g16_g17_stakeholder_reconciliation" /
    "08_restatement_note_reevaluation.csv",
    "assurance_eng": N / "assurance" / "assurance_engagements.csv",
    "supp_coverage": N / "supplementary" /
    "external_requirement_coverage.csv",
    "deferred": settings.reference_dir / "deferred_issues_register.csv",
    "mtco2e": settings.reference_dir / "brsr_mtco2e_unit_decisions.csv",
    "assurance_issues": N / "assurance" / "assurance_issues.csv",
}
PILLAR_FILES = {
    "Environmental": "environmental_metrics.csv",
    "Social": "social_metrics.csv",
    "Governance": "governance_metrics.csv",
    "Corporate": "corporate_identity_metrics.csv",
    "Assurance": "assurance_metrics.csv",
}
REPORTED = {"REPORTED", "REPORTED_ZERO"}


def read(path, **kw):
    return pd.read_csv(path, dtype=str, keep_default_na=False, **kw)


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def write(df, name, dest):
    dest.mkdir(parents=True, exist_ok=True)
    df.to_csv(dest / name, index=False, encoding="utf-8-sig")
    return dest / name


def copy(src, name, dest):
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest / name)
    return dest / name


# ==========================================================
# helpers
# ==========================================================

def filing_index():
    m = read(SRC["filing_manifest"])
    m["source_file"] = m["local_file_path"].str.replace(
        "\\", "/", regex=False).str.split("/").str[-1]
    return m


def record_status(r):
    """Summary of existing statuses (fixed precedence; never upgrades)."""
    if r["deferred_issue_ids"]:
        return "DEFERRED_DATA_QUALITY_ISSUE"
    if r["crosswalk_status"] == "CANDIDATE_REVIEW":
        return "PENDING_CROSSWALK_REVIEW"
    if r["scale_decision_id"] and r["multiplier_basis"] != \
            "APPROVED_SCALE_CORRECTION":
        return "PENDING_SCALE_REVIEW"
    if r["conversion_status"] == "PENDING_APPROVAL_UNIT_ASSUMPTION":
        return "PENDING_UNIT_DECISION"
    if r["conversion_status"].startswith("NOT_CONVERTED"):
        return "NOT_NORMALISED_" + r["conversion_status"][
            len("NOT_CONVERTED_"):]
    if r["conversion_status"] == "NOT_NUMERIC":
        return "TEXT_VALUE"
    if r["multiplier_basis"] == "APPROVED_UNIT_ASSUMPTION":
        return "NORMALISED_APPROVED_UNIT_DECISION"
    if r["multiplier_basis"] == "APPROVED_SCALE_CORRECTION":
        return "NORMALISED_APPROVED_SCALE_CORRECTION"
    return "NORMALISED_FROM_XBRL"


def validation_status(prod, validation, queue):
    """Per value: Phase 9 failures and open review items for that cell."""
    fails = validation[validation["status"] == "FAIL"].copy()
    fails["req"] = fails["requirement_id"].str.split(" ").str[0]
    f_key = fails.groupby(["master_isin", "fy_slot", "req", "breakdown"])[
        "rule_id"].agg(lambda s: " | ".join(sorted(set(s))))
    open_q = queue[~queue["status"].isin(["APPLIED", "REJECTED"])]
    q_key = open_q.groupby(["master_isin", "fy_slot", "requirement_id",
                            "breakdown"])["status"].agg(
        lambda s: " | ".join(f"{k}:{n}" for k, n in s.value_counts()
                             .sort_index().items()))
    keys = list(zip(prod["master_isin"], prod["fy_slot"],
                    prod["requirement_id"], prod["breakdown"]))
    prod["validation_failures"] = [f_key.get(k, "") for k in keys]
    prod["open_review_items"] = [q_key.get(k, "") for k in keys]
    prod["validation_status"] = [
        "FAILED_RULES" if f else ("OPEN_REVIEW_ITEMS" if q else
                                  "NO_FAILURE_RECORDED")
        for f, q in zip(prod["validation_failures"],
                        prod["open_review_items"])]
    return prod


# ==========================================================
# builders
# ==========================================================

def taxonomy_concepts():
    reg = read(SRC["taxonomy_registry"])
    frames = []
    for v in reg["taxonomy_version"]:
        path = (SRC["taxonomy_current"] if v == reg["taxonomy_version"]
                .iloc[-1] else P / "taxonomy_versions" / v /
                "nse_brsr_taxonomy_concepts.csv")
        df = read(path)
        df.insert(0, "taxonomy_version", v)
        frames.append(df)
    return pd.concat(frames, ignore_index=True)


def concept_metric_mapping():
    av = read(SRC["metric_availability"])
    mc = read(SRC["metric_concepts"])
    m = read(SRC["req_map"])
    req = m.drop_duplicates("requirement_id")[[
        "requirement_id", "pillar", "requirement", "metric_key", "source",
        "expected_unit", "breakdown_axes", "dimension_filter",
        "applicability_concept"]].rename(columns={"requirement_id":
                                                  "metric_id"})
    out = av.merge(req, on="metric_id", how="left").merge(
        mc[["metric_id", "concept", "data_type", "type_family",
            "period_type", "unit_family", "unit_type_consistent"]],
        on=["metric_id", "concept"], how="left")
    ext = req[req["source"] != "BRSR_XBRL"].assign(
        concept="", taxonomy_version="", availability="NOT_IN_BRSR_XBRL",
        concept_in_version="", crosswalk_status="", crosswalk_approval="")
    out = pd.concat([out, ext], ignore_index=True).fillna("")
    return out[["metric_id", "pillar", "requirement", "metric_key",
                "source", "concept", "taxonomy_version", "availability",
                "concept_in_version", "crosswalk_status",
                "crosswalk_approval", "data_type", "type_family",
                "period_type", "expected_unit", "unit_family",
                "unit_type_consistent", "breakdown_axes",
                "dimension_filter", "applicability_concept"]]


def normalised_facts():
    cy = read(SRC["prod_cy"])
    py = read(SRC["prod_py"])
    return pd.concat([cy, py], ignore_index=True)


def pillar_frame(cy, filings, validation, queue, m):
    names = m.drop_duplicates("requirement_id").set_index(
        "requirement_id")["requirement"].to_dict()
    fid = dict(zip(filings["source_file"], filings["filing_id"]))
    df = cy.copy()
    df.insert(0, "filing_id", df["source_file"].map(fid).fillna(""))
    df["requirement"] = df["requirement_id"].map(names).fillna("")
    df["record_status"] = df.apply(record_status, axis=1)
    df = validation_status(df, validation, queue)
    return df


PILLAR_COLUMNS = [
    "filing_id", "company_name", "master_isin", "master_nse_symbol",
    "fy_slot", "reporting_fy", "period_role", "pillar", "requirement_id",
    "requirement", "metric_key", "breakdown", "metric_name",
    "current_concept", "crosswalk_status", "taxonomy_version", "data_type",
    "value_status", "original_value", "original_unit", "original_unit_id",
    "normalised_value", "normalised_unit", "conversion_factor",
    "multiplier_basis", "conversion_status", "conversion_formula",
    "needs_review", "unit_decision", "unit_assumption_outcome",
    "scale_decision_id", "scale_recommended_decision",
    "scale_decision_outcome", "pending_review", "deferred_issue_ids",
    "record_status", "validation_status", "validation_failures",
    "open_review_items", "dimensions", "period_start", "period_end",
    "instant_date", "context_reference", "source_file", "source_url"]
ASSURANCE_EXTRA = ["assurance_engagement_type",
                   "assurance_filing_level_level",
                   "assurance_indicator_scope",
                   "assurance_reasonable_flag_2024",
                   "assurance_core_engagement_2025"]


def pillar_files(df):
    eng = read(SRC["assurance_eng"]).set_index("filing_id")
    out = {}
    for pillar, name in PILLAR_FILES.items():
        part = df[df["pillar"] == pillar][PILLAR_COLUMNS].copy()
        if pillar == "Assurance":
            for col, src in zip(ASSURANCE_EXTRA, [
                    "engagement_type", "filing_level_assurance_level",
                    "indicator_scope", "reasonable_assurance_flag_2024",
                    "brsr_core_engagement_2025"]):
                part[col] = part["filing_id"].map(eng[src]).fillna("")
        out[name] = part
    return out


def present_concepts(cov_need):
    """(filing_id, concept) pairs with a REPORTED CY fact in parsed facts."""
    filings = filing_index().set_index("filing_id")
    wanted = set(cov_need["concept"])
    hits = set()
    for chunk in pd.read_csv(SRC["facts"], dtype=str, keep_default_na=False,
                             usecols=["filing_id", "concept", "value_status",
                                      "period_start", "period_end",
                                      "instant"], chunksize=250_000):
        chunk = chunk[chunk["concept"].isin(wanted)
                      & (chunk["value_status"] == "REPORTED")]
        for r in chunk.to_dict("records"):
            f = filings.loc[r["filing_id"]] if r["filing_id"] in \
                filings.index else None
            if f is None:
                continue
            cy = ((r["period_start"], r["period_end"]) ==
                  (f["reporting_period_start"], f["reporting_period_end"])
                  or (r["instant"] and r["instant"] ==
                      f["reporting_period_end"]))
            if cy:
                hits.add((r["filing_id"], r["concept"]))
    return hits


def coverage_reports(cy_pillar):
    cov = read(SRC["coverage"])
    m = read(SRC["req_map"])
    filings = filing_index()
    master = read(SRC["company_master"])[["company_id", "isin"]]
    scope = filings[filings["in_processing_scope"] == "True"].merge(
        master, on="company_id", how="left")
    by_cell = {(r["isin"], r["fy_slot"]): r for r in scope.to_dict("records")}
    av = read(SRC["metric_availability"])
    av = av[av["availability"] != "NOT_IN_VERSION"]
    names_by = {}
    for r in av.to_dict("records"):
        for name in r["concept_in_version"].split(" | "):
            if name:
                names_by.setdefault((r["metric_id"], r["taxonomy_version"]),
                                    set()).add(name)
    # cells needing an extraction check
    need = []
    for r in cov[cov["status"] == "NOT_DISCLOSED"].to_dict("records"):
        f = by_cell.get((r["isin"], r["fy_slot"]))
        if f is None:
            continue
        for c in names_by.get((r["requirement_id"], f["taxonomy_version"]),
                              ()):
            need.append({"filing_id": f["filing_id"], "concept": c,
                         "isin": r["isin"], "fy_slot": r["fy_slot"],
                         "requirement_id": r["requirement_id"]})
    need = pd.DataFrame(need, columns=["filing_id", "concept", "isin",
                                       "fy_slot", "requirement_id"])
    hits = present_concepts(need) if len(need) else set()
    present_cells = {(r["isin"], r["fy_slot"], r["requirement_id"])
                     for r in need.to_dict("records")
                     if (r["filing_id"], r["concept"]) in hits}

    val = cy_pillar.groupby(["master_isin", "fy_slot", "requirement_id"])
    summary_vals = val.agg(
        value_rows=("original_value", "size"),
        extracted_value=("original_value",
                         lambda s: s.iloc[0] if len(s) == 1 else
                         f"{len(s)} values (see pillar file)"),
        unit=("normalised_unit", lambda s: " | ".join(sorted(set(s) - {""}))),
        original_unit=("original_unit",
                       lambda s: " | ".join(sorted(set(s) - {""}))),
        record_status=("record_status",
                       lambda s: " | ".join(sorted(set(s)))),
        validation_status=("validation_status",
                           lambda s: "FAILED_RULES" if (s == "FAILED_RULES")
                           .any() else ("OPEN_REVIEW_ITEMS" if (
                               s == "OPEN_REVIEW_ITEMS").any() else
                               "NO_FAILURE_RECORDED")),
        source_file=("source_file", "first"),
        source_url=("source_url", "first"))
    rows = []
    for r in cov.to_dict("records"):
        key = (r["isin"], r["fy_slot"], r["requirement_id"])
        f = by_cell.get((r["isin"], r["fy_slot"]))
        status = r["status"]
        basis = "PIPELINE_STATUS"
        if status == "NO_REPORT" and f is not None and \
                f["processing_status"] != "EXTRACTED_CURRENT_PIPELINE":
            status, basis = "EXTRACTION_ERROR", \
                f"filing {f['filing_id']} {f['download_status']}: " \
                f"{f['error_details'][:120]}"
        elif status == "NOT_DISCLOSED" and key in present_cells:
            status, basis = "EXTRACTION_ERROR", \
                "concept has a CY value in the parsed facts but no " \
                "requirement value was selected"
        elif status == "NOT_DISCLOSED":
            basis = "concept available in the filing's taxonomy; no CY " \
                    "fact in the parsed facts"
        v = summary_vals.loc[key] if key in summary_vals.index else None
        rows.append({
            "company_name": r["company_name"], "master_isin": r["isin"],
            "nse_symbol": r["nse_symbol"], "fy_slot": r["fy_slot"],
            "report_status": r["report_status"],
            "filing_id": f["filing_id"] if f is not None else "",
            "pillar": r["pillar"], "requirement_id": r["requirement_id"],
            "metric": r["requirement"], "metric_key": r["metric_key"],
            "source_type": r["source"], "pipeline_status": r["status"],
            "coverage_status": status, "coverage_basis": basis,
            "values_found": r["values_found"],
            "values_from_candidate_mapping":
                r["values_from_candidate_mapping"],
            "extracted_value": v["extracted_value"] if v is not None else "",
            "original_unit": v["original_unit"] if v is not None else "",
            "unit": v["unit"] if v is not None else "",
            "record_status": v["record_status"] if v is not None else "",
            "validation_status": v["validation_status"] if v is not None
            else "",
            "source_file": v["source_file"] if v is not None else "",
            "source_url": (v["source_url"] if v is not None else
                           (f["xbrl_url"] if f is not None else ""))})
    coverage = pd.DataFrame(rows)

    supp = read(SRC["supp_coverage"])
    supp_by = {(r["master_isin"], r["fy_slot"], r["requirement_id"]):
               r for r in supp.to_dict("records")}
    missing = coverage[~coverage["coverage_status"].isin(REPORTED)].copy()
    missing["supplementary_evidence_status"] = [
        supp_by.get((a, b, c), {}).get("status", "")
        for a, b, c in zip(missing["master_isin"], missing["fy_slot"],
                           missing["requirement_id"])]
    missing["supplementary_candidates"] = [
        supp_by.get((a, b, c), {}).get("candidates", "")
        for a, b, c in zip(missing["master_isin"], missing["fy_slot"],
                           missing["requirement_id"])]
    missing["supplementary_note"] = missing["supplementary_evidence_status"]\
        .map(lambda s: "" if not s else
             "candidate evidence only (unverified; local PDFs cover "
             "Reliance only)")
    return coverage, missing


def company_year_summary(coverage, facts_counts, prod_cy, validation,
                         queue, master):
    master = master.set_index("isin", drop=False)
    rows = []
    for (isin, slot), g in coverage.groupby(["master_isin", "fy_slot"]):
        row = {"company_name": g["company_name"].iloc[0],
               "master_isin": isin, "nse_symbol": g["nse_symbol"].iloc[0],
               "fy_slot": slot, "report_status": g["report_status"].iloc[0],
               "filing_id": g["filing_id"].iloc[0]}
        m = master.loc[isin] if isin in master.index else None
        ident = ["cin", "isin", "nse_symbol", "bse_scrip_code"]
        row["identity_fields_present"] = sum(
            1 for c in ident if m is not None and str(m[c]) != "")
        row["identity_fields_expected"] = len(ident)
        row["cin_status"] = m["cin_status"] if m is not None else ""
        fid = row["filing_id"]
        fc = facts_counts.get(fid, {})
        row["xbrl_facts_extracted"] = fc.get("facts", 0) if fid else ""
        row["xbrl_unique_concepts"] = fc.get("concepts", 0) if fid else ""
        xbrl = g[g["source_type"] == "BRSR_XBRL"]
        for pillar in ("Corporate", "Environmental", "Social", "Governance",
                       "Assurance"):
            p = xbrl[xbrl["pillar"] == pillar]
            avail = p[~p["coverage_status"].isin(
                ["NOT_IN_TAXONOMY_VERSION", "NO_REPORT", "NOT_APPLICABLE"])]
            row[f"{pillar.lower()}_metrics_reported"] = int(
                p["coverage_status"].isin(REPORTED).sum())
            row[f"{pillar.lower()}_metrics_applicable"] = len(avail)
        for status in ("NOT_DISCLOSED", "EXTRACTION_ERROR",
                       "NOT_IN_TAXONOMY_VERSION", "NO_REPORT",
                       "EXTERNAL_SOURCE_REQUIRED", "NOT_APPLICABLE",
                       "REPORTED_PENDING_CROSSWALK_REVIEW"):
            row[f"metrics_{status.lower()}"] = int(
                (g["coverage_status"] == status).sum())
        p = prod_cy[(prod_cy["master_isin"] == isin)
                    & (prod_cy["fy_slot"] == slot)]
        row["values_unit_not_normalised"] = int(
            p["conversion_status"].str.startswith("NOT_CONVERTED").sum()
            + (p["conversion_status"] == "PENDING_APPROVAL_UNIT_ASSUMPTION")
            .sum())
        v = validation[(validation["master_isin"] == isin)
                       & (validation["fy_slot"] == slot)
                       & (validation["status"] == "FAIL")]
        row["validation_failures"] = len(v)
        qq = queue[(queue["master_isin"] == isin) & (queue["fy_slot"] == slot)
                   & ~queue["status"].isin(["APPLIED", "REJECTED"])]
        row["unresolved_review_items"] = len(qq)
        row["coverage_note"] = ("counts of metrics, not of XBRL facts; "
                                "missing values are not zero")
        rows.append(row)
    return pd.DataFrame(rows)


def unmapped_concepts(facts_by_concept):
    cw = read(SRC["crosswalk"])
    cw_idx = {(r["source_version"], r["source_concept"]): r
              for r in cw.to_dict("records")}
    m = read(SRC["req_map"])
    av = read(SRC["metric_availability"])
    mapped = set()
    for r in av.to_dict("records"):
        for name in r["concept_in_version"].split(" | "):
            mapped.add((r["taxonomy_version"], name))
    issues = read(SRC["parse_issues"])
    not_in_tax = set(zip(issues.loc[issues["code"] ==
                                    "CONCEPT_NOT_IN_TAXONOMY", "fy_slot"],
                         issues.loc[issues["code"] ==
                                    "CONCEPT_NOT_IN_TAXONOMY", "detail"]
                         .str.split(" ").str[0]))
    rows = []
    for (version, concept), d in facts_by_concept.items():
        c = cw_idx.get((version, concept), {})
        in_req = (version, concept) in mapped
        status = c.get("status", "NOT_IN_CROSSWALK")
        reason = []
        if not in_req:
            reason.append("NOT_MAPPED_TO_A_REQUIREMENT")
        if status in ("NO_CURRENT_EQUIVALENT", "NOT_IN_CROSSWALK"):
            reason.append(f"CROSSWALK_{status}")
        if status == "CANDIDATE_REVIEW":
            reason.append("CROSSWALK_CANDIDATE_REVIEW")
        if any(concept == x for _, x in not_in_tax):
            reason.append("CONCEPT_NOT_IN_DECLARED_TAXONOMY")
        if not reason:
            continue
        rows.append({"taxonomy_version": version, "concept": concept,
                     "facts": d["facts"], "filings": d["filings"],
                     "facts_with_value": d["reported"],
                     "mapped_to_requirement": in_req,
                     "crosswalk_status": status,
                     "crosswalk_approval": c.get("approval_status", ""),
                     "current_concept": c.get("current_concept", ""),
                     "unmapped_reason": " | ".join(reason)})
    return pd.DataFrame(rows).sort_values(["taxonomy_version", "concept"])


def scan_facts():
    """One pass over the parsed facts: per filing and per concept."""
    filings = dict(zip(filing_index()["filing_id"],
                       filing_index()["taxonomy_version"]))
    per_filing, per_concept = {}, {}
    for chunk in pd.read_csv(SRC["facts"], dtype=str, keep_default_na=False,
                             usecols=["filing_id", "concept",
                                      "value_status"], chunksize=250_000):
        for fid, g in chunk.groupby("filing_id"):
            d = per_filing.setdefault(fid, {"facts": 0, "concepts": set()})
            d["facts"] += len(g)
            d["concepts"] |= set(g["concept"])
        chunk["version"] = chunk["filing_id"].map(filings).fillna("")
        for (v, c), g in chunk.groupby(["version", "concept"]):
            d = per_concept.setdefault((v, c), {"facts": 0, "filings": set(),
                                                "reported": 0})
            d["facts"] += len(g)
            d["filings"] |= set(g["filing_id"])
            d["reported"] += int((g["value_status"] == "REPORTED").sum())
    return ({k: {"facts": v["facts"], "concepts": len(v["concepts"])}
             for k, v in per_filing.items()},
            {k: {"facts": v["facts"], "filings": len(v["filings"]),
                 "reported": v["reported"]} for k, v in per_concept.items()})


def scale_recommendations(cy):
    fse = read(SRC["fse"])
    out = cy[cy["scale_decision_id"] != ""][[
        "scale_decision_id", "multiplier_basis", "scale_decision_outcome",
        "conversion_status", "normalised_value", "normalised_unit"]].rename(
        columns={"scale_decision_id": "approval_id",
                 "normalised_value": "production_normalised_value",
                 "normalised_unit": "production_normalised_unit",
                 "conversion_status": "production_conversion_status"})
    pk = read(SRC["block_packages"])
    pk = pk[pk["scale_record"] != ""][["scale_record", "package_id",
                                       "package_dependency"]].rename(
        columns={"scale_record": "approval_id"})
    df = fse.merge(out, on="approval_id", how="left").merge(
        pk, on="approval_id", how="left").fillna("")
    df["engine_status"] = df["scale_decision_outcome"]
    return df


def restatement_report():
    v = read(SRC["validation"])
    r = v[v["rule_id"].isin(["RST01", "RST02"])].copy()
    re_ = read(SRC["c08_reeval"])
    re_["breakdown"] = "OtherStakeHolderAxis=" + re_["row_position"]
    re_ = re_.rename(columns={"nse_symbol": "master_nse_symbol"})[[
        "master_nse_symbol", "fy_slot", "requirement_id", "breakdown",
        "re_evaluation", "match_method", "current_name",
        "matched_next_name"]]
    r = r.merge(re_, on=["master_nse_symbol", "fy_slot", "requirement_id",
                         "breakdown"], how="left").fillna("")
    r["identity_note"] = r["breakdown"].map(
        lambda b: "OtherStakeHolderAxis row matched by position; "
                  "name-based matching deferred (DQ-039)"
        if "OtherStakeHolderAxis" in b else "")
    r = r.rename(columns={"re_evaluation": "name_based_re_evaluation",
                          "match_method": "name_match_method",
                          "current_name": "stakeholder_name_current",
                          "matched_next_name": "stakeholder_name_next"})
    return r


def processing_summary(outputs, cy, coverage):
    def n(path):
        return len(read(path, usecols=[0]))
    q = read(SRC["queue"])
    mt = read(SRC["mtco2e"])
    rows = [
        ("Registry", "companies registered", n(SRC["company_master"]),
         "registry/company_master.csv"),
        ("Registry", "filings registered", n(SRC["filing_manifest"]),
         "registry/filing_manifest.csv"),
        ("Registry", "filings in processing scope",
         int((read(SRC["filing_manifest"])["in_processing_scope"] == "True")
             .sum()), "registry/filing_manifest.csv"),
        ("Parser", "XBRL facts parsed", n(SRC["facts"]),
         "parsed/xbrl_facts.csv"),
        ("Normalisation", "requirement values CY", len(cy),
         "normalised/brsr_requirement_values_normalised.csv"),
        ("Normalisation", "requirement values PY comparatives",
         n(SRC["prod_py"]),
         "normalised/brsr_requirement_values_py_normalised.csv"),
        *[("Normalisation", f"CY multiplier basis {k}", v,
           "normalised (factor-model columns)")
          for k, v in cy["multiplier_basis"].value_counts().items()],
        *[("Coverage", f"company-year-metric cells {k}", v,
           "metric_coverage_report.csv")
          for k, v in coverage["coverage_status"].value_counts().items()],
        *[("Review", f"review queue {k}", v, "review_workflow/review_queue")
          for k, v in q["status"].value_counts().items()],
        *[("Decisions", f"MtCO2e decision {k}", v,
           "reference/brsr_mtco2e_unit_decisions.csv")
          for k, v in mt["decision"].value_counts().items()],
        ("Decisions", "filer scale-error records (pending)",
         int((read(SRC["fse"])["approval_decision"] == "").sum()),
         "final_review/strong_recommendations_final_review.csv"),
        ("Decisions", "deferred data-quality issues open",
         int((read(SRC["deferred"])["status"] ==
              "DEFERRED_PENDING_REVIEW").sum()),
         "reference/deferred_issues_register.csv"),
        ("Supplementary", "limitation",
         "local supplementary PDFs cover Reliance only; NSE PDFs not "
         "downloaded (owner decision)", "supplementary/"),
        ("Database", "production import", "NOT PERFORMED (owner approval "
         "required)", "Phase 14"),
    ]
    rows += [("Outputs", f"rows in {name}", cnt, f"data/outputs/{name}")
             for name, cnt in outputs]
    return pd.DataFrame(rows, columns=["stage", "measure", "value",
                                       "source"])


# ==========================================================
# build
# ==========================================================

EXPECTED_OUTPUTS = [
    "company_master.csv", "filing_manifest.csv", "taxonomy_concepts.csv",
    "concept_metric_mapping.csv", "all_brsr_facts.csv",
    "normalised_brsr_facts.csv", *PILLAR_FILES.values(),
    "metric_coverage_report.csv", "missing_metrics_report.csv",
    "unmapped_concepts_report.csv", "unit_validation_report.csv",
    "data_quality_report.csv", "block_validation_report.csv",
    "scale_error_recommendations.csv", "restatement_validation_report.csv",
    "company_year_coverage_summary.csv", "final_processing_summary.csv"]
DEFAULT_BACKUP_ROOT = N / "backups"


class ReportBuildError(RuntimeError):
    pass


def generate(dest):
    """Write every deliverable and manifest.json into dest."""
    written = {}
    written["company_master.csv"] = copy(SRC["company_master"],
                                         "company_master.csv", dest)
    written["filing_manifest.csv"] = copy(SRC["filing_manifest"],
                                          "filing_manifest.csv", dest)
    written["taxonomy_concepts.csv"] = write(taxonomy_concepts(),
                                             "taxonomy_concepts.csv", dest)
    written["concept_metric_mapping.csv"] = write(
        concept_metric_mapping(), "concept_metric_mapping.csv", dest)
    written["all_brsr_facts.csv"] = copy(SRC["facts"], "all_brsr_facts.csv",
                                         dest)
    written["normalised_brsr_facts.csv"] = write(
        normalised_facts(), "normalised_brsr_facts.csv", dest)

    cy = read(SRC["prod_cy"])
    filings = filing_index()
    validation = read(SRC["validation"])
    queue = read(SRC["queue"])
    m = read(SRC["req_map"])
    pillar_df = pillar_frame(cy, filings, validation, queue, m)
    for name, df in pillar_files(pillar_df).items():
        written[name] = write(df, name, dest)

    coverage, missing = coverage_reports(pillar_df)
    written["metric_coverage_report.csv"] = write(
        coverage, "metric_coverage_report.csv", dest)
    written["missing_metrics_report.csv"] = write(
        missing, "missing_metrics_report.csv", dest)

    facts_by_filing, facts_by_concept = scan_facts()
    written["unmapped_concepts_report.csv"] = write(
        unmapped_concepts(facts_by_concept), "unmapped_concepts_report.csv",
        dest)
    unit = pillar_df[pillar_df["conversion_status"] != "NOT_NUMERIC"][
        PILLAR_COLUMNS + ["rule_status", "rule_factor", "rule_target_unit",
                          "unit_reinterpretation_informational",
                          "unit_reinterpretation_factor_not_applied",
                          "legacy_factor_not_applied", "factor_formula"]]
    written["unit_validation_report.csv"] = write(
        unit, "unit_validation_report.csv", dest)
    written["data_quality_report.csv"] = copy(SRC["queue"],
                                              "data_quality_report.csv", dest)
    written["block_validation_report.csv"] = copy(
        SRC["blocks"], "block_validation_report.csv", dest)
    written["scale_error_recommendations.csv"] = write(
        scale_recommendations(cy), "scale_error_recommendations.csv", dest)
    written["restatement_validation_report.csv"] = write(
        restatement_report(), "restatement_validation_report.csv", dest)
    summary = company_year_summary(coverage, facts_by_filing, cy, validation,
                                   queue, read(SRC["company_master"]))
    written["company_year_coverage_summary.csv"] = write(
        summary, "company_year_coverage_summary.csv", dest)

    counts = [(name, len(read(path, usecols=[0])))
              for name, path in written.items()]
    final = processing_summary(counts, cy, coverage)
    written["final_processing_summary.csv"] = write(
        final, "final_processing_summary.csv", dest)

    manifest = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "generator": "app.reporting.deliverables",
        "outputs": {name: {"sha256": sha(p),
                           "rows": len(read(p, usecols=[0]))}
                    for name, p in written.items()},
        "sources": {k: {"path": str(v.relative_to(settings.project_root)),
                        "sha256": sha(v)} for k, v in SRC.items()},
        "notes": ["values are not recalculated; see DATA_DICTIONARY.md",
                  "supplementary PDFs: Reliance only; candidates unverified",
                  "production PostgreSQL import not performed"]}
    (dest / "manifest.json").write_text(json.dumps(manifest, indent=1),
                                        encoding="utf-8")
    return manifest


def verify_generated(dest, manifest):
    """Every expected file exists and matches the manifest."""
    missing = [n for n in EXPECTED_OUTPUTS if not (dest / n).exists()]
    if missing:
        raise ReportBuildError(f"outputs missing: {missing}")
    for name, info in manifest["outputs"].items():
        if sha(dest / name) != info["sha256"]:
            raise ReportBuildError(f"hash mismatch after write: {name}")
        if info["rows"] <= 0:
            raise ReportBuildError(f"empty output: {name}")
    from app.reporting.data_dictionary import undocumented
    gaps = undocumented(dest)
    if gaps:
        raise ReportBuildError(f"undocumented columns: {gaps}")


def backup_existing(out_dir, backup_root):
    """Copy the current outputs to a new backup folder and verify each copy
    by SHA-256. Returns the folder (None when there is nothing to back up).
    """
    files = sorted(p for p in out_dir.glob("*") if p.is_file()) \
        if out_dir.exists() else []
    if not files:
        return None
    dest = Path(backup_root) / (datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                                + "_pre_report_outputs")
    dest.mkdir(parents=True, exist_ok=False)
    lines = []
    for f in files:
        shutil.copy2(f, dest / f.name)
        digest = sha(f)
        if sha(dest / f.name) != digest:
            raise ReportBuildError(f"backup verification failed: {f.name}")
        lines.append(f"{digest}  {f.name}")
    (dest / "backup_sha256.txt").write_text("\n".join(lines) + "\n",
                                            encoding="utf-8")
    return dest


def replace_outputs(staging, out_dir, backup):
    """Move the verified new files into out_dir. If any move fails, every
    file already replaced is restored from the verified backup."""
    out_dir.mkdir(parents=True, exist_ok=True)
    done = []
    try:
        for f in sorted(staging.iterdir()):
            os.replace(f, out_dir / f.name)
            done.append(f.name)
    except Exception:
        for name in done:
            if backup is not None and (backup / name).exists():
                shutil.copy2(backup / name, out_dir / name)
            else:
                (out_dir / name).unlink(missing_ok=True)
        raise


def build(out_dir=None, backup_root=None):
    """
    Generate into a staging folder next to out_dir, verify, back up the
    current outputs (SHA-256 verified) and only then replace them.
    A failure before the replacement leaves out_dir untouched; a failure
    during the replacement restores the files from the backup.
    """
    out_dir = Path(out_dir or OUT)
    backup_root = Path(backup_root or DEFAULT_BACKUP_ROOT)
    staging = out_dir.parent / (f".{out_dir.name}.staging-"
                                + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
    staging.mkdir(parents=True)
    try:
        manifest = generate(staging)
        verify_generated(staging, manifest)
        backup = backup_existing(out_dir, backup_root)
        replace_outputs(staging, out_dir, backup)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    log.info("deliverables built", extra={"fields": {
        "files": len(manifest["outputs"]), "backup": str(backup)}})
    return {"out_dir": out_dir, "backup_dir": backup, "manifest": manifest}


def main():
    result = build()
    for name, info in result["manifest"]["outputs"].items():
        print(f"{info['rows']:>8}  {name}")
    print(f"\nOutputs: {result['out_dir']}")
    print(f"Backup : {result['backup_dir'] or 'none (no previous outputs)'}")


if __name__ == "__main__":
    main()
