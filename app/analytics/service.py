"""
Analytics service: turns database rows into API records.

Value record (one filed fact for one metric / breakdown):

  disclosure_status   REPORTED | REPORTED_ZERO (the filer reported 0) |
                      REPORTED_TEXT | REPORTED_EMPTY |
                      REPORTED_PENDING_CROSSWALK_REVIEW (value reached the
                      metric only through a candidate concept mapping that
                      is still awaiting review)
  value_basis         REPORTED_AS_FILED | CALCULATED_UNIT_CONVERSION |
                      CALCULATED_APPROVED_UNIT_DECISION |
                      REPORTED_NOT_NORMALISED (unit ambiguous or decision
                      pending: `value` is null, the filed value is kept in
                      `reported_value`)

Missing record (no filed fact for a metric in a company-year):

  NO_FILING_FOR_YEAR   no in-scope BRSR filing for the FY
  EXTRACTION_ERROR     a filing exists but could not be downloaded/parsed
  NOT_IN_BRSR_XBRL     the metric is not part of BRSR XBRL (external source)
  NOT_APPLICABLE       the filing's applicability concept says it does not
                       apply
  NO_VALUE_IN_FILING   the filing was processed and has no CY fact for the
                       metric (the database does not record whether the
                       concept existed in the filing's taxonomy version)

`value` is never 0 unless the filer reported 0; missing values are null.
"""

import re
from collections import OrderedDict
from decimal import Decimal, InvalidOperation

from app.analytics import repository as repo

FY_PATTERN = re.compile(r"^\d{4}-\d{2}$")

# grouping rules over the metric registry (no metric ids hard-coded):
# a theme is selected by the expected unit or the metric-key prefix
THEMES = OrderedDict([
    ("energy", lambda m: m["expected_unit"].startswith("energy")),
    ("emissions", lambda m: m["expected_unit"].startswith("tco2e")),
    ("water", lambda m: m["expected_unit"].startswith("kilolitre")),
    ("waste", lambda m: m["expected_unit"] == "tonne"),
    ("workforce", lambda m: m["metric_key"].startswith(("headcount",
                                                         "turnover_rate"))),
    ("remuneration", lambda m: "remuneration" in m["metric_key"]
     or "wage" in m["metric_key"]),
    ("safety", lambda m: m["metric_key"].startswith(
        ("ltifr", "recordable_injuries", "fatalities",
         "high_consequence", "safety_"))),
    ("posh", lambda m: m["metric_key"].startswith("posh_")),
    ("csr", lambda m: m["metric_key"].startswith("csr_")),
    ("board", lambda m: m["metric_key"].startswith("board_")),
    ("complaints", lambda m: "complaints" in m["metric_key"]
     and not m["metric_key"].startswith("posh_")),
    ("ethics", lambda m: m["metric_key"].startswith(
        ("corruption_", "anti_corruption", "coi_", "ngrbc_"))),
    ("assurance", lambda m: m["pillar"] == "Assurance"),
    ("corporate", lambda m: m["pillar"] == "Corporate"),
])
PILLARS = {"environmental": "Environmental", "social": "Social",
           "governance": "Governance", "corporate": "Corporate",
           "assurance": "Assurance"}
CONVERTED = {"CONVERTED", "CONVERTED_FRACTION_TO_PERCENT"}


class NotFound(LookupError):
    pass


class BadRequest(ValueError):
    pass


# ---------------------------------------------------------------- helpers
def dec(v):
    try:
        return Decimal(str(v)) if v not in (None, "") else None
    except (InvalidOperation, ValueError):
        return None


def num(v):
    d = dec(v)
    return float(d) if d is not None else None


def words(name):
    """CamelCase XBRL member/axis name -> words ('OtherGenderMember' ->
    'Other Gender')."""
    name = re.sub(r"(Member|Axis|Domain)$", "", name)
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ",
                  name).strip()


def parse_breakdown(breakdown):
    """'A=XMember | GenderAxis=MaleMember' -> [{axis, member, label}]."""
    out = []
    for part in [p for p in (breakdown or "").split(" | ") if p]:
        axis, _, member = part.partition("=")
        axis, member = axis.split(":")[-1], member.split(":")[-1]
        # XBRL convention: the member named after the axis (GenderAxis ->
        # GenderMember) is the axis default, i.e. the total
        is_total = member == re.sub(r"Axis$", "Member", axis)
        out.append({"axis": axis, "member": member,
                    "label": "Total" if is_total else words(member),
                    "is_total": is_total})
    return out


def check_fy(fy):
    if not FY_PATTERN.match(fy or ""):
        raise BadRequest(f"invalid financial year '{fy}' (expected YYYY-YY)")
    start, end = int(fy[:4]), int(fy[5:])
    if (start + 1) % 100 != end:
        raise BadRequest(f"invalid financial year '{fy}'")
    return fy


# ---------------------------------------------------------------- catalog
def catalog(cur):
    """One entry per requirement (several concepts may map to one)."""
    out = OrderedDict()
    for m in repo.requirement_map(cur):
        e = out.setdefault(m["requirement_id"], {
            "requirement_id": m["requirement_id"], "pillar": m["pillar"],
            "metric_name": m["requirement"], "metric_key": m["metric_key"],
            "expected_unit": m["expected_unit"],
            "breakdown_axes": [a for a in m["breakdown_axes"].split(";")
                               if a],
            "dimension_filter": m["dimension_filter"],
            "applicability_concept": m["applicability_concept"],
            "source": m["source"], "concepts": [], "notes": m["notes"]})
        if m["current_concept"]:
            e["concepts"].append(m["current_concept"])
    for e in out.values():
        e["themes"] = [t for t, rule in THEMES.items() if rule(e)]
        e["in_brsr_xbrl"] = e["source"] == "BRSR_XBRL" and bool(e["concepts"])
    return out


def select(cat, pillar=None, theme=None, requirement_ids=None):
    if pillar is not None and pillar not in PILLARS:
        raise BadRequest(f"unknown pillar '{pillar}'; use one of "
                         f"{sorted(PILLARS)}")
    if theme is not None and theme not in THEMES:
        raise BadRequest(f"unknown theme '{theme}'; use one of "
                         f"{list(THEMES)}")
    unknown = sorted(set(requirement_ids or []) - set(cat))
    if unknown:
        raise NotFound(f"unknown metric(s): {unknown}")
    return [m for m in cat.values()
            if (pillar is None or m["pillar"] == PILLARS[pillar])
            and (theme is None or theme in m["themes"])
            and (requirement_ids is None
                 or m["requirement_id"] in requirement_ids)]


# ---------------------------------------------------------------- companies
def company_or_404(cur, key):
    c = repo.get_company(cur, key)
    if c is None:
        raise NotFound(f"company '{key}' is not in the processing universe")
    return c


def years(cur, company):
    by_fy = OrderedDict()
    for f in repo.filings(cur, company["company_id"]):
        by_fy.setdefault(f["fy_slot"], []).append(f)
    out = []
    for fy, fl in by_fy.items():
        latest = [f for f in fl if f["is_latest_valid"] == "True"]
        failed = [f for f in fl if f["processing_status"] ==
                  "DOWNLOAD_FAILED"]
        main = latest[-1] if latest else None
        out.append({
            "fiscal_year": fy,
            "status": ("PROCESSED" if main and main["processing_status"]
                       .startswith("EXTRACTED") else
                       "EXTRACTION_ERROR" if failed and not latest else
                       main["processing_status"] if main else "NO_FILING"),
            "filing": filing_ref(main) if main else None,
            "failed_filings": [{"filing_id": f["filing_id"],
                                "error": f["error_details"]} for f in failed],
            "versions": len(fl)})
    return out


def filing_ref(f):
    return {"filing_id": f["filing_id"],
            "reporting_period_start": f["reporting_period_start"],
            "reporting_period_end": f["reporting_period_end"],
            "submission_date": f["submission_date"],
            "taxonomy_version": f.get("taxonomy_version"),
            "filing_version_status": f["filing_version_status"],
            "xbrl_url": f["xbrl_url"], "pdf_url": f["pdf_url"] or None}


def year_or_404(cur, company, fy):
    check_fy(fy)
    for y in years(cur, company):
        if y["fiscal_year"] == fy:
            return y
    raise NotFound(f"no in-scope BRSR filing record for "
                   f"{company['company_id']} in FY {fy}")


# ---------------------------------------------------------------- values
def disclosure_status(r):
    if r["crosswalk_status"] == "CANDIDATE_REVIEW":
        return "REPORTED_PENDING_CROSSWALK_REVIEW"
    if r["value_status"] != "REPORTED" or r["original_value"] == "":
        return "REPORTED_EMPTY"
    if r["conversion_status"] in ("NOT_NUMERIC",):
        return "REPORTED_TEXT"
    d = dec(r["original_value"])
    if d is None:
        return "REPORTED_TEXT"
    return "REPORTED_ZERO" if d == 0 else "REPORTED"


def value_basis(r):
    s = r["conversion_status"]
    if s == "CONVERTED_APPROVED_UNIT_ASSUMPTION" or \
            r["multiplier_basis"] == "APPROVED_SCALE_CORRECTION":
        return "CALCULATED_APPROVED_UNIT_DECISION"
    if s in CONVERTED:
        return "CALCULATED_UNIT_CONVERSION"
    if s in ("NO_CONVERSION_NEEDED", "NOT_NUMERIC"):
        return "REPORTED_AS_FILED"
    return "REPORTED_NOT_NORMALISED"


def value_record(r, meta):
    basis = value_basis(r)
    normalised = basis != "REPORTED_NOT_NORMALISED"
    text = r["conversion_status"] == "NOT_NUMERIC"
    value = (r["original_value"] if text else r["normalised_value"]) \
        if normalised else None
    rec = {
        "requirement_id": r["requirement_id"],
        "metric_name": meta["metric_name"] if meta else r["requirement_id"],
        "metric_key": r["metric_key"], "pillar": r["pillar"],
        "concept": r["metric_name"], "fiscal_year": r["fy_slot"],
        "period_role": r["period_role"],
        "period": {"start": r["period_start"] or None,
                   "end": r["period_end"] or None,
                   "instant": r["instant_date"] or None},
        "breakdown": r["breakdown"],
        "dimensions": parse_breakdown(r["breakdown"]),
        "value": value if value not in ("", None) else None,
        "value_numeric": None if text or not normalised
        else num(r["normalised_value"]),
        "unit": (r["normalised_unit"] or None) if normalised and not text
        else None,
        "reported_value": r["original_value"],
        "reported_unit": r["original_unit"] or None,
        "disclosure_status": disclosure_status(r),
        "value_basis": basis,
        "normalisation_status": r["conversion_status"],
        "normalisation_note": r["conversion_note"] or None,
        "calculation": None,
        "review": {
            "pending_review": r["pending_review"] or None,
            "deferred_issue_ids": [x for x in (r["deferred_issue_ids"] or "")
                                   .split(" | ") if x],
            "crosswalk_status": r["crosswalk_status"],
            "unit_decision": r["unit_decision"] or None},
        "source": {
            "filing_id": r["filing_id"], "source_file": r["source_file"],
            "xbrl_url": r["source_url"], "pdf_url": r["pdf_url"] or None,
            "context_reference": r["context_reference"],
            "concept": r["metric_name"],
            "taxonomy_version": r["taxonomy_version"],
            "submission_date": r["submission_date"],
            "filing_version_status": r["filing_version_status"]}}
    if basis.startswith("CALCULATED"):
        rec["calculation"] = {
            "formula": r["conversion_formula"],
            "factor": r["conversion_factor"],
            "multiplier_basis": r["multiplier_basis"],
            "inputs": [{"concept": r["metric_name"],
                        "value": r["original_value"],
                        "unit": r["original_unit"],
                        "context_reference": r["context_reference"],
                        "source_file": r["source_file"]}],
            "decision": r["unit_decision"] or r["scale_decision_id"] or None}
    return rec


def missing_record(meta, fy, year, applicable):
    if not meta["in_brsr_xbrl"]:
        status, reason = "NOT_IN_BRSR_XBRL", (
            f"not part of BRSR XBRL; source: {meta['source']}")
    elif year is None or year["status"] == "NO_FILING":
        status, reason = "NO_FILING_FOR_YEAR", "no in-scope BRSR filing"
    elif year["status"] == "EXTRACTION_ERROR":
        status, reason = "EXTRACTION_ERROR", "; ".join(
            f"{f['filing_id']}: {f['error']}" for f in year["failed_filings"])
    elif applicable is False:
        status, reason = "NOT_APPLICABLE", (
            f"{meta['applicability_concept']} = false in the filing")
    else:
        status, reason = "NO_VALUE_IN_FILING", (
            "the filing has no current-year fact for this metric; not "
            "disclosed or not in the filing's taxonomy version")
    return {"requirement_id": meta["requirement_id"],
            "metric_name": meta["metric_name"],
            "metric_key": meta["metric_key"], "pillar": meta["pillar"],
            "fiscal_year": fy, "value": None, "value_numeric": None,
            "unit": None, "disclosure_status": status,
            "missing_reason": reason,
            "source": {"filing_id": year["filing"]["filing_id"]}
            if year and year.get("filing") else None}


def applicability_flags(cur, metas, filing_id):
    concepts = sorted({m["applicability_concept"] for m in metas
                       if m["applicability_concept"]})
    facts = repo.applicability(cur, [filing_id] if filing_id else [],
                               concepts)
    out = {}
    for c in concepts:
        vals = facts.get((filing_id, c), set())
        out[c] = (True if "true" in vals else
                  False if "false" in vals else None)
    return out


def metric_group(meta, records):
    return {"requirement_id": meta["requirement_id"],
            "metric_name": meta["metric_name"],
            "metric_key": meta["metric_key"], "pillar": meta["pillar"],
            "themes": meta["themes"], "expected_unit": meta["expected_unit"],
            "values": records}


def company_year_metrics(cur, key, fy, pillar=None, theme=None,
                         requirement_ids=None, include_missing=True):
    company = company_or_404(cur, key)
    year = year_or_404(cur, company, fy)
    cat = catalog(cur)
    metas = select(cat, pillar, theme, requirement_ids)
    rows = repo.values(cur, [company["isin"]], fy,
                       [m["requirement_id"] for m in metas])
    by_req = {}
    for r in rows:
        by_req.setdefault(r["requirement_id"], []).append(
            value_record(r, cat.get(r["requirement_id"])))
    filing_id = year["filing"]["filing_id"] if year["filing"] else None
    flags = applicability_flags(cur, metas, filing_id)
    metrics, missing = [], []
    for m in metas:
        if m["requirement_id"] in by_req:
            metrics.append(metric_group(m, by_req[m["requirement_id"]]))
        elif include_missing:
            missing.append(missing_record(
                m, fy, year, flags.get(m["applicability_concept"])))
    return {"company": company_ref(company), "fiscal_year": fy,
            "filing_status": year["status"], "filing": year["filing"],
            "metrics": metrics, "missing": missing,
            "summary": {"metrics_requested": len(metas),
                        "metrics_with_values": len(metrics),
                        "metrics_missing": len(missing),
                        "values": sum(len(m["values"]) for m in metrics)}}


def company_ref(c):
    return {"company_id": c["company_id"], "legal_name": c["legal_name"],
            "nse_symbol": c["nse_symbol"], "isin": c["isin"],
            "bse_scrip_code": c["bse_scrip_code"] or None}


# ---------------------------------------------------------------- grids
def gender_grid(group):
    """Category x gender table for a metric with a GenderAxis breakdown."""
    rows = OrderedDict()
    for v in group["values"]:
        dims = v["dimensions"]
        gender = next((d for d in dims if d["axis"] == "GenderAxis"), None)
        other = [d for d in dims if d["axis"] != "GenderAxis"]
        if gender is None:
            continue
        cat_key = " | ".join(f"{d['axis']}={d['member']}" for d in other)
        row = rows.setdefault(cat_key, {
            "category": cat_key,
            "category_label": " / ".join(d["label"] for d in other)
            or "All", "cells": OrderedDict()})
        row["cells"][gender["label"]] = {
            "value": v["value"], "value_numeric": v["value_numeric"],
            "unit": v["unit"], "disclosure_status": v["disclosure_status"],
            "concept": v["concept"],
            "context_reference": v["source"]["context_reference"]}
    return list(rows.values())


def workforce(cur, key, fy):
    out = company_year_metrics(cur, key, fy, theme="workforce")
    for g in out["metrics"]:
        g["gender_grid"] = gender_grid(g)
    return out


# ---------------------------------------------------------------- history
def history(cur, key, requirement_id, breakdown=""):
    company = company_or_404(cur, key)
    cat = catalog(cur)
    meta = select(cat, requirement_ids=[requirement_id])[0]
    rows = repo.values(cur, [company["isin"]], None, [requirement_id],
                       breakdown)
    by_fy = {}
    for r in rows:
        by_fy.setdefault(r["fy_slot"], []).append(value_record(r, meta))
    series = []
    for y in years(cur, company):
        fy = y["fiscal_year"]
        if fy in by_fy:
            series.append({"fiscal_year": fy, "values": by_fy[fy]})
        else:
            filing_id = y["filing"]["filing_id"] if y["filing"] else None
            flag = applicability_flags(cur, [meta], filing_id).get(
                meta["applicability_concept"])
            series.append({"fiscal_year": fy, "values": [],
                           "missing": missing_record(meta, fy, y, flag)})
    units = sorted({v["unit"] for s in series for v in s["values"]
                    if v["unit"]})
    return {"company": company_ref(company),
            "metric": {k: meta[k] for k in ("requirement_id", "metric_name",
                                            "pillar", "expected_unit")},
            "breakdown": breakdown, "dimensions": parse_breakdown(breakdown),
            "series": series, "units": units,
            "comparable": len(units) <= 1,
            "note": None if len(units) <= 1 else
            "normalised units differ across years; values are not compared"}


def compare(cur, keys, requirement_id, fy=None, breakdown=""):
    if not keys:
        raise BadRequest("give at least one company")
    if fy is not None:
        check_fy(fy)
    cat = catalog(cur)
    meta = select(cat, requirement_ids=[requirement_id])[0]
    companies = [company_or_404(cur, k) for k in keys]
    rows = repo.values(cur, [c["isin"] for c in companies], fy,
                       [requirement_id], breakdown)
    by = {}
    for r in rows:
        by.setdefault((r["master_isin"], r["fy_slot"]), []).append(
            value_record(r, meta))
    out = []
    for c in companies:
        ys = [y for y in years(cur, c) if fy is None or y["fiscal_year"] == fy]
        if fy is not None and not ys:
            ys = [{"fiscal_year": fy, "status": "NO_FILING", "filing": None,
                   "failed_filings": []}]
        entries = []
        for y in ys:
            vals = by.get((c["isin"], y["fiscal_year"]), [])
            if vals:
                entries.append({"fiscal_year": y["fiscal_year"],
                                "values": vals})
            else:
                filing_id = y["filing"]["filing_id"] if y["filing"] else None
                flag = applicability_flags(cur, [meta], filing_id).get(
                    meta["applicability_concept"])
                entries.append({"fiscal_year": y["fiscal_year"], "values": [],
                                "missing": missing_record(
                                    meta, y["fiscal_year"], y, flag)})
        out.append({"company": company_ref(c), "years": entries})
    units = sorted({v["unit"] for c in out for e in c["years"]
                    for v in e["values"] if v["unit"]})
    return {"metric": {k: meta[k] for k in ("requirement_id", "metric_name",
                                            "pillar", "expected_unit")},
            "breakdown": breakdown, "dimensions": parse_breakdown(breakdown),
            "fiscal_year": fy, "companies": out, "units": units,
            "comparable": len(units) <= 1}


# ---------------------------------------------------------------- quality
def data_quality(cur, key, fy):
    company = company_or_404(cur, key)
    year = year_or_404(cur, company, fy)
    full = company_year_metrics(cur, key, fy)
    statuses, bases = {}, {}
    for g in full["metrics"]:
        for v in g["values"]:
            statuses[v["disclosure_status"]] = \
                statuses.get(v["disclosure_status"], 0) + 1
            bases[v["value_basis"]] = bases.get(v["value_basis"], 0) + 1
    missing = {}
    for m in full["missing"]:
        missing[m["disclosure_status"]] = \
            missing.get(m["disclosure_status"], 0) + 1
    review = repo.review_items(cur, company["isin"], fy)
    by_status = {}
    for r in review:
        by_status.setdefault(r["category"], {}).setdefault(r["status"], 0)
        by_status[r["category"]][r["status"]] += 1
    not_normalised = [
        {"requirement_id": v["requirement_id"], "breakdown": v["breakdown"],
         "reported_value": v["reported_value"],
         "reported_unit": v["reported_unit"],
         "normalisation_status": v["normalisation_status"],
         "note": v["normalisation_note"]}
        for g in full["metrics"] for v in g["values"]
        if v["value_basis"] == "REPORTED_NOT_NORMALISED"]
    return {"company": company_ref(company), "fiscal_year": fy,
            "filing_status": year["status"], "filing": year["filing"],
            "value_disclosure_status": statuses, "value_basis": bases,
            "missing_metrics": missing, "not_normalised_values":
            not_normalised, "review_recommendations": by_status,
            "open_review_items": [r for r in review if r["status"] not in
                                  ("APPLIED", "REJECTED")],
            "deferred_issues": repo.deferred_issues(cur, company["isin"], fy)}
