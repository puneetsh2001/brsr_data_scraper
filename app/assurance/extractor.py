"""
Assurance extraction (Phase 12).

Source: the Phase 6 parsed facts (data/processed/nifty100/parsed/
xbrl_facts.csv) of the in-scope filings, read only. Every record keeps its
fact_id, XML line, context, dimensions, period, original concept and
value as filed.

Families (kept apart - never treated as equivalent)
  BRSR_CORE_ENGAGEMENT     2025+: WhetherTheCompanyHasUndertakenAssessment-
                           OrAssuranceOfTheBRSRCore ("Yes assessment" /
                           "Yes assurance" / "No")
  BRSR_CORE_REASONABLE_ASSURANCE_2024
                           2024 taxonomy: WhetherTheCompanyHasUndertaken-
                           ReasonableAssuranceOfTheBRSRCore (true / false).
                           A different question: false does not say whether
                           limited assurance, assessment or nothing was done.
  BRSR_CORE_COVERAGE       TypeOf(Assessment)OrAssuranceObtain: All / Partial
                           (scope of indicators, not the assurance level)
  PROVIDER                 provider firm, firm / LLP ID, signatory,
                           designation, signing date (per provider member)
  INDICATOR_ASSURED        Whether<indicator>IsAssuredByAssurer,
                           AssurerHasAssured<indicator> (true / false)
  INDICATOR_LEVEL          AssuranceSubTypeFor<indicator>: Reasonable /
                           Limited (the only explicit assurance level)
  INDICATOR_REMARKS        RemarksFor<indicator>
  SECTION_COVERAGE         TypeOfAssuranceFor<section>: All / Partial
  LEGACY_EXTERNAL_AGENCY   all versions: "independent assessment OR
                           evaluation OR assurance by an external agency"
                           for environmental topics, and the agency name.
                           Engagement type is NOT distinguishable.
  POLICY_EVALUATION        Section B: independent assessment / evaluation of
                           the working of policies - not an assurance.

Rules
  - Assessment, assurance, limited and reasonable assurance stay distinct.
  - An assurance level is recorded only where the filing states it
    (indicator level fields, or the 2024 reasonable-assurance flag = true).
  - Values outside a field's allowed values are AMBIGUOUS, never coerced.
  - Disclosure status: REPORTED, NOT_REPORTED (concept in the filing's
    taxonomy, no fact), NOT_IN_TAXONOMY_VERSION (unavailable), AMBIGUOUS,
    NIL / EMPTY.
  - Crosswalk files are read, never changed; 2024 concepts are kept under
    their original names (the V01 candidate mapping is not applied).

Outputs: data/processed/nifty100/assurance/

Run:
    python -m app.assurance.extractor
"""

import re
from collections import defaultdict
from datetime import date, timedelta

import pandas as pd

from app.config import settings
from app.core.logging_setup import get_logger
from app.parsers.xbrl_instance import concept_types

log = get_logger(__name__, "assurance")

N = settings.processed_dir / "nifty100"
FACTS = N / "parsed" / "xbrl_facts.csv"
MANIFEST = settings.processed_dir / "registry" / "filing_manifest.csv"
MASTER = settings.processed_dir / "registry" / "company_master.csv"
CROSSWALK = settings.processed_dir / "taxonomy_versions" / \
    "nse_brsr_concept_crosswalk.csv"
REQ_VALUES = N / "brsr_requirement_values.csv"
REQ_MAP = settings.reference_dir / "brsr_requirement_concept_map.csv"
CONCEPTS_CURRENT = settings.processed_dir / "nse_brsr_taxonomy_concepts.csv"
CONCEPTS_DIR = settings.processed_dir / "taxonomy_versions"
OUTPUT_DIR = N / "assurance"

ENGAGEMENT = {
    "WhetherTheCompanyHasUndertakenAssessmentOrAssuranceOfTheBRSRCore":
        ("BRSR_CORE_ENGAGEMENT", "ASSESSMENT_OR_ASSURANCE"),
    "WhetherTheCompanyHasUndertakenReasonableAssuranceOfTheBRSRCore":
        ("BRSR_CORE_REASONABLE_ASSURANCE_2024", "BOOLEAN"),
    "TypeOfAssessmentOrAssuranceObtain": ("BRSR_CORE_COVERAGE", "ALL_PARTIAL"),
    "TypeOfAssuranceObtain": ("BRSR_CORE_COVERAGE", "ALL_PARTIAL"),
}
PROVIDER = {
    "NameOfTheCompanyOrLLPOrFirmOfAssessmentOrAssuranceProvider":
        "provider_name",
    "NameOfTheCompanyOrLLPOrFirmOfAssuranceProvider": "provider_name",
    "CompanyIDOrLLPIDOrFirmIDOfAssessmentOrAssuranceProvider":
        "provider_registration_id",
    "CompanyIDOrLLPIDOrFirmIDOfAssuranceProvider":
        "provider_registration_id",
    "NameOfTheAssessorOrAssurer": "signatory_name",
    "NameOfTheAssurer": "signatory_name",
    "DesignationOfAssessorOrAssurer": "signatory_designation",
    "DesignationOfAssurer": "signatory_designation",
    "DateOfSigningByAssessorOrAssurer": "signing_date",
    "DateOfSigningByAssurer": "signing_date",
}
LEGACY_FLAG = re.compile(
    r"^(Whether)?(Any)?(IndicateIf)?(Any)?IndependentAssessmentOrEvaluation"
    r"OrAssuranceHasBeenCarriedOutByAnExternalAgency|^WhetherAnyIndicateIf"
    r"AnyIndependentAssessment")
LEGACY_AGENCY = re.compile(r"^NameOfTheExternalAgency.*(Independent"
                           r"AssessmentOrEvaluationOrAssurance)")
POLICY_FLAG = "HasTheEntityCarriedOutIndependentAssessmentEvaluationOf" \
              "TheWorkingOfItsPoliciesByAnExternalAgency"
POLICY_AGENCY = "NameOfTheAgencyIfTheEntityCarriedOutIndependentAssessment" \
                "EvaluationOfTheWorkingOfItsPoliciesByAnExternalAgency" \
                "ExplanatoryTextBlock"
TOPICS = [("WASTE", r"Waste"), ("WATER", r"Water"),
          ("EMISSIONS", r"Emission|GreenHouse|Scope|GHG"),
          ("ENERGY", r"Energy"), ("AIR", r"Air|NOx|SOx")]
PLACEHOLDER_TEXT = {"", "na", "n.a.", "nil", "-", "--", "none",
                    "not applicable"}


def read(path):
    return pd.read_csv(path, dtype=str, keep_default_na=False)


# ==========================================================
# classification of concepts and values
# ==========================================================

def indicator_key(concept):
    k = concept
    for prefix in ("AssuranceSubTypeFor", "RemarksFor", "AssurerHasAssured",
                   "Whether"):
        if k.startswith(prefix):
            k = k[len(prefix):]
            break
    k = k.replace("IsAssuredByAssurer", "")
    if k.startswith("AssuranceOf"):          # RemarksForAssuranceOf<X>
        k = k[len("AssuranceOf"):]
    return k[len("Whether"):] if k.startswith("Whether") else k


def legacy_key(concept):
    """Question-specific part of a legacy external-agency concept, so the
    flag and agency name of the same question share a key (topics such as
    EMISSIONS hold several separate questions)."""
    m = re.search(r"(?:ExternalAgency|OrAssurance)(?!.*(?:ExternalAgency|"
                  r"OrAssurance))(.*)$", concept)
    tail = (m.group(1) if m else concept).replace("ExplanatoryTextBlock", "")
    if tail.startswith("RelatedTo"):          # agency-name wording
        tail = "For" + tail[len("RelatedTo"):]
    return tail or "GENERAL"


def classify_concept(concept):
    """(family, value_kind, key) or None when not assurance-related."""
    if concept in ENGAGEMENT:
        fam, kind = ENGAGEMENT[concept]
        return fam, kind, ""
    if concept in PROVIDER:
        return "PROVIDER", "DATE" if PROVIDER[concept] == "signing_date" \
            else "TEXT", PROVIDER[concept]
    if concept.startswith("AssuranceSubTypeFor"):
        return "INDICATOR_LEVEL", "LEVEL", indicator_key(concept)
    if concept.startswith("TypeOfAssuranceFor"):
        return "SECTION_COVERAGE", "ALL_PARTIAL", \
            concept[len("TypeOfAssuranceFor"):]
    if re.search(r"IsAssuredByAssurer(AsPerP\d)?$", concept) and \
            concept.startswith("Whether") or \
            concept.startswith("AssurerHasAssured"):
        return "INDICATOR_ASSURED", "BOOLEAN", indicator_key(concept)
    if concept.startswith("RemarksFor") and concept not in (
            "RemarksForTheDisclosure",):
        return "INDICATOR_REMARKS", "TEXT", indicator_key(concept)
    if LEGACY_FLAG.search(concept):
        return "LEGACY_EXTERNAL_AGENCY", "BOOLEAN", legacy_key(concept)
    if LEGACY_AGENCY.search(concept):
        return "LEGACY_EXTERNAL_AGENCY", "TEXT", legacy_key(concept)
    if concept == POLICY_FLAG:
        return "POLICY_EVALUATION", "BOOLEAN", ""
    if concept == POLICY_AGENCY:
        return "POLICY_EVALUATION", "TEXT", ""
    return None


def topic_of(concept):
    tail = re.split(r"ExternalAgency|Assurance", concept)[-1]
    for name, pattern in TOPICS:
        if re.search(pattern, tail):
            return name
    return "OTHER"


def interpret(kind, value, status):
    """(interpretation, disclosure status)."""
    if status == "NIL":
        return "", "NIL"
    v = value.strip()
    if not v:
        return "", "EMPTY"
    low = v.lower()
    if kind == "BOOLEAN":
        if low in ("true", "1"):
            return "TRUE", "REPORTED"
        if low in ("false", "0"):
            return "FALSE", "REPORTED"
        return "", "AMBIGUOUS"
    if kind == "ASSESSMENT_OR_ASSURANCE":
        return {"yes assessment": ("ASSESSMENT", "REPORTED"),
                "yes assurance": ("ASSURANCE", "REPORTED"),
                "no": ("NONE", "REPORTED")}.get(low, ("", "AMBIGUOUS"))
    if kind == "LEVEL":
        return {"reasonable": ("REASONABLE", "REPORTED"),
                "limited": ("LIMITED", "REPORTED")}.get(low,
                                                       ("", "AMBIGUOUS"))
    if kind == "ALL_PARTIAL":
        return {"all": ("ALL", "REPORTED"),
                "partial": ("PARTIAL", "REPORTED")}.get(low,
                                                       ("", "AMBIGUOUS"))
    if kind == "DATE":
        try:
            date.fromisoformat(v[:10])
            return v[:10], "REPORTED"
        except ValueError:
            return "", "AMBIGUOUS"
    if low in PLACEHOLDER_TEXT:
        return "", "PLACEHOLDER_TEXT"
    return v, "REPORTED"


def period_role(f, filing):
    start, end = filing["reporting_period_start"], \
        filing["reporting_period_end"]
    if (f["period_start"], f["period_end"]) == (start, end) or \
            f["instant"] == end:
        return "CY"
    try:
        prev_end = (date.fromisoformat(start) - timedelta(days=1)).isoformat()
    except ValueError:
        prev_end = ""
    if f["period_end"] == prev_end or f["instant"] == prev_end or \
            f["instant"] == start:
        return "PY"
    return "OTHER"


def labels():
    out = {}
    paths = [CONCEPTS_CURRENT] + sorted(
        CONCEPTS_DIR.glob("*/nse_brsr_taxonomy_concepts.csv"))
    for p in paths:
        for r in read(p)[["concept_name", "standard_label"]].to_dict(
                "records"):
            out.setdefault(r["concept_name"], r["standard_label"])
    return out


# ==========================================================
# extraction
# ==========================================================

def scope():
    m = read(MANIFEST)
    m = m[(m["in_processing_scope"] == "True") & (m["local_file_path"] != "")]
    master = read(MASTER)[["company_id", "legal_name", "isin", "nse_symbol",
                           "bse_scrip_code"]]
    return m.merge(master, on="company_id", how="left")


def extract_facts(filings):
    by_id = {r["filing_id"]: r for r in filings.to_dict("records")}
    lab = labels()
    rows = []
    cols = ["filing_id", "fact_id", "line", "concept", "context_id",
            "period_type", "period_start", "period_end", "instant",
            "dimension_key", "value", "value_status", "data_type",
            "duplicate_group", "duplicate_class"]
    for chunk in pd.read_csv(FACTS, dtype=str, keep_default_na=False,
                             usecols=cols, chunksize=250_000):
        chunk = chunk[chunk["filing_id"].isin(by_id)]
        for f in chunk.to_dict("records"):
            c = classify_concept(f["concept"])
            if c is None:
                continue
            family, kind, key = c
            filing = by_id[f["filing_id"]]
            interp, status = interpret(kind, f["value"], f["value_status"])
            provider = ""
            if "AssuranceProviderAxis" in f["dimension_key"]:
                provider = f["dimension_key"].split("=", 1)[1]
            rows.append({
                "filing_id": f["filing_id"],
                "company_name": filing["legal_name"],
                "master_isin": filing["isin"],
                "nse_symbol": filing["nse_symbol"],
                "bse_scrip_code": filing["bse_scrip_code"],
                "fy_slot": filing["fy_slot"],
                "taxonomy_version": filing["taxonomy_version"],
                "reporting_period_start": filing["reporting_period_start"],
                "reporting_period_end": filing["reporting_period_end"],
                "family": family, "value_kind": kind, "key": key,
                "concept": f["concept"],
                "standard_label": lab.get(f["concept"], ""),
                "value": f["value"], "interpretation": interp,
                "disclosure_status": status,
                "period_role": period_role(f, filing),
                "context_id": f["context_id"],
                "period_start": f["period_start"],
                "period_end": f["period_end"], "instant": f["instant"],
                "dimension_key": f["dimension_key"],
                "provider_member": provider,
                "duplicate_group": f["duplicate_group"],
                "duplicate_class": f["duplicate_class"],
                "fact_id": f["fact_id"], "xml_line": f["line"],
                "source_file": filing["local_file_path"].replace(
                    "\\", "/").split("/")[-1],
                "source_url": filing["xbrl_url"]})
    return pd.DataFrame(rows)


# ==========================================================
# per-filing tables
# ==========================================================

def cy_values(facts, family, key=None):
    x = facts[(facts["family"] == family) & (facts["period_role"] == "CY")]
    if key is not None:
        x = x[x["key"] == key]
    return x


def single(rows, issues, filing, label):
    """One interpretation from CY facts; conflicting values -> AMBIGUOUS."""
    if not len(rows):
        return None, "", ""
    interps = set(rows["interpretation"]) - {""}
    statuses = set(rows["disclosure_status"])
    raw = " | ".join(sorted(set(rows["value"])))
    if len(interps) > 1:
        issues.append(issue(filing, "AS06_CONFLICTING_DUPLICATE", "ERROR",
                            f"{label}: {raw}", rows))
        return "AMBIGUOUS", raw, rows["fact_id"].iloc[0]
    if "AMBIGUOUS" in statuses and not interps:
        return "AMBIGUOUS", raw, rows["fact_id"].iloc[0]
    if interps:
        return interps.pop(), raw, rows["fact_id"].iloc[0]
    return statuses.pop() if len(statuses) == 1 else "EMPTY", raw, \
        rows["fact_id"].iloc[0]


def issue(filing, code, severity, detail, rows=None):
    first = rows.iloc[0].to_dict() if rows is not None and len(rows) else {}
    return {"filing_id": filing["filing_id"],
            "company_name": filing["legal_name"],
            "master_isin": filing["isin"], "nse_symbol": filing["nse_symbol"],
            "fy_slot": filing["fy_slot"],
            "taxonomy_version": filing["taxonomy_version"],
            "issue_code": code, "severity": severity, "detail": detail,
            "concept": first.get("concept", ""),
            "context_id": first.get("context_id", ""),
            "fact_id": first.get("fact_id", ""),
            "xml_line": first.get("xml_line", ""),
            "source_file": first.get("source_file", ""),
            "source_url": filing["xbrl_url"]}


def availability(version, concepts):
    types = concept_types(version) if version else {}
    return {c: c in types for c in concepts}


def build_tables(facts, filings):
    engagements, providers, indicators, legacy, issues = [], [], [], [], []
    fac = {k: g for k, g in facts.groupby("filing_id")}
    for filing in filings.to_dict("records"):
        f = fac.get(filing["filing_id"], facts.iloc[0:0])
        version = filing["taxonomy_version"]
        avail = availability(version, list(ENGAGEMENT) + list(PROVIDER))

        def status_for(concepts, family):
            rows = cy_values(f, family)
            rows = rows[rows["concept"].isin(concepts)]
            if not any(avail.get(c) for c in concepts):
                return "NOT_IN_TAXONOMY_VERSION", "", ""
            if not len(rows):
                return "NOT_REPORTED", "", ""
            return single(rows, issues, filing, "/".join(concepts))

        eng, eng_raw, eng_fact = status_for(
            ["WhetherTheCompanyHasUndertakenAssessmentOrAssuranceOfTheBRSRCore"],
            "BRSR_CORE_ENGAGEMENT")
        rflag, rflag_raw, rflag_fact = status_for(
            ["WhetherTheCompanyHasUndertakenReasonableAssuranceOfTheBRSRCore"],
            "BRSR_CORE_REASONABLE_ASSURANCE_2024")
        cov, cov_raw, _ = status_for(["TypeOfAssessmentOrAssuranceObtain",
                                      "TypeOfAssuranceObtain"],
                                     "BRSR_CORE_COVERAGE")
        # engagement type and filing-level level: explicit statements only
        if eng in ("ASSESSMENT", "ASSURANCE", "NONE"):
            engagement_type, level = eng, "NOT_DISCLOSED_AT_FILING_LEVEL"
            if eng == "NONE":
                level = "NOT_APPLICABLE"
        elif rflag == "TRUE":
            # explicit: the 2024 concept asks about reasonable assurance of
            # the BRSR Core (not of every indicator)
            engagement_type, level = "ASSURANCE",                 "REASONABLE_BRSR_CORE_2024_FLAG"
        elif rflag == "FALSE":
            engagement_type, level = "UNKNOWN", "NOT_REASONABLE_OTHERWISE_" \
                                                "UNDISCLOSED"
        elif "AMBIGUOUS" in (eng, rflag):
            engagement_type, level = "AMBIGUOUS", "AMBIGUOUS"
        elif eng == "NOT_IN_TAXONOMY_VERSION" and \
                rflag == "NOT_IN_TAXONOMY_VERSION":
            engagement_type = level = "NOT_IN_TAXONOMY_VERSION"
        else:
            engagement_type = level = "NOT_REPORTED"

        # providers
        prov = cy_values(f, "PROVIDER")
        plist = []
        for member, g in prov.groupby("provider_member"):
            rec = {"filing_id": filing["filing_id"],
                   "company_name": filing["legal_name"],
                   "master_isin": filing["isin"],
                   "nse_symbol": filing["nse_symbol"],
                   "fy_slot": filing["fy_slot"],
                   "taxonomy_version": version, "provider_member": member,
                   "source_url": filing["xbrl_url"]}
            for field in ("provider_name", "provider_registration_id",
                          "signatory_name", "signatory_designation",
                          "signing_date"):
                rows = g[g["key"] == field]
                val, raw, fid = single(rows, issues, filing, field)
                rec[field] = val if val not in ("EMPTY", "NIL",
                                                 "PLACEHOLDER_TEXT", None) \
                    else ""
                rec[f"{field}_status"] = (
                    "REPORTED" if rec[field] and val != "AMBIGUOUS" else
                    "AMBIGUOUS" if val == "AMBIGUOUS" else
                    "NOT_REPORTED" if val is None else val)
                rec[f"{field}_concept"] = rows["concept"].iloc[0] \
                    if len(rows) else ""
                rec[f"{field}_fact_id"] = fid
            rec["original_concepts_version"] = (
                "2024 assurance-provider concepts" if version == "2024-04-30"
                else "assessment-or-assurance-provider concepts")
            if rec["signing_date"] and filing["reporting_period_end"] and \
                    rec["signing_date"] < filing["reporting_period_end"]:
                issues.append(issue(filing, "AS08_SIGNED_BEFORE_PERIOD_END",
                                    "WARNING", f"{member}: signed "
                                    f"{rec['signing_date']} before period end "
                                    f"{filing['reporting_period_end']}", g))
            plist.append(rec)
        providers += plist
        named = [p for p in plist if p["provider_name"]]

        # indicators
        assured = cy_values(f, "INDICATOR_ASSURED")
        levels = cy_values(f, "INDICATOR_LEVEL")
        remarks = cy_values(f, "INDICATOR_REMARKS")
        counts = defaultdict(int)
        for key in sorted(set(assured["key"]) | set(levels["key"])):
            a_val, a_raw, a_fid = single(assured[assured["key"] == key],
                                         issues, filing, f"assured {key}")
            l_val, l_raw, l_fid = single(levels[levels["key"] == key],
                                         issues, filing, f"level {key}")
            r_rows = remarks[remarks["key"] == key]
            a_val = a_val or "NOT_REPORTED"
            l_val = l_val or "NOT_REPORTED"
            if a_val == "AMBIGUOUS":
                issues.append(issue(filing, "AS04_AMBIGUOUS_ASSURED_FLAG",
                                    "WARNING", f"{key}: value {a_raw!r} in a "
                                    "true/false field (not interpreted)",
                                    assured[assured["key"] == key]))
            if l_val in ("REASONABLE", "LIMITED") and a_val == "FALSE":
                issues.append(issue(filing, "AS05_LEVEL_WITHOUT_ASSURANCE",
                                    "WARNING", f"{key}: level {l_raw} but "
                                    "assured = false",
                                    levels[levels["key"] == key]))
            counts[f"assured_{a_val}"] += 1
            if a_val == "TRUE":
                counts[f"level_{l_val}"] += 1
            src = assured[assured["key"] == key]
            indicators.append({
                "filing_id": filing["filing_id"],
                "company_name": filing["legal_name"],
                "master_isin": filing["isin"],
                "nse_symbol": filing["nse_symbol"],
                "fy_slot": filing["fy_slot"], "taxonomy_version": version,
                "indicator_key": key,
                "assured_concept": src["concept"].iloc[0] if len(src) else "",
                "indicator_label": src["standard_label"].iloc[0]
                if len(src) else "",
                "assured": a_val, "assured_raw": a_raw,
                "assured_fact_id": a_fid,
                "assurance_level": l_val, "assurance_level_raw": l_raw,
                "level_fact_id": l_fid,
                "remarks": " | ".join(sorted(set(r_rows["value"]) - {""}))
                [:300],
                "source_url": filing["xbrl_url"]})

        # legacy external agency and policy evaluation (all versions)
        for family in ("LEGACY_EXTERNAL_AGENCY", "POLICY_EVALUATION"):
            fam = cy_values(f, family)
            for (key, dims), g in fam.groupby(["key", "dimension_key"]):
                flags = g[g["value_kind"] == "BOOLEAN"]
                names = g[g["value_kind"] == "TEXT"]
                fv, fraw, ffid = single(flags, issues, filing,
                                        f"{family} {key}")
                legacy.append({
                    "filing_id": filing["filing_id"],
                    "company_name": filing["legal_name"],
                    "master_isin": filing["isin"],
                    "nse_symbol": filing["nse_symbol"],
                    "fy_slot": filing["fy_slot"],
                    "taxonomy_version": version, "family": family,
                    "topic": topic_of(key) if family ==
                    "LEGACY_EXTERNAL_AGENCY" else "POLICIES",
                    "question_key": key, "dimension_key": dims,
                    "engagement_type":
                        "ASSESSMENT_OR_EVALUATION_OR_ASSURANCE_NOT_"
                        "DISTINGUISHED" if family == "LEGACY_EXTERNAL_AGENCY"
                        else "INDEPENDENT_POLICY_EVALUATION_NOT_ASSURANCE",
                    "carried_out": fv or "NOT_REPORTED",
                    "carried_out_raw": fraw,
                    "agency": " | ".join(sorted(set(
                        names.loc[names["disclosure_status"] == "REPORTED",
                                  "interpretation"])))[:500],
                    "concepts": " | ".join(sorted(set(g["concept"]))),
                    "fact_ids": " | ".join(g["fact_id"]),
                    "source_url": filing["xbrl_url"]})

        # filing-level checks
        engaged = engagement_type in ("ASSESSMENT", "ASSURANCE")
        if engaged and not named:
            issues.append(issue(filing, "AS01_ENGAGEMENT_WITHOUT_PROVIDER",
                                "WARNING", f"engagement {engagement_type} "
                                "reported but no provider name"))
        if engagement_type == "ASSESSMENT" and (
                counts["level_REASONABLE"] or counts["level_LIMITED"]):
            issues.append(issue(filing, "AS02_ASSESSMENT_WITH_ASSURANCE_LEVEL",
                                "WARNING", "engagement reported as assessment "
                                f"but {counts['level_REASONABLE']} reasonable / "
                                f"{counts['level_LIMITED']} limited assurance "
                                "indicator levels"))
        if engagement_type == "NONE" and counts["assured_TRUE"]:
            issues.append(issue(filing, "AS03_NONE_BUT_INDICATORS_ASSURED",
                                "WARNING", f"engagement 'No' but "
                                f"{counts['assured_TRUE']} indicators assured"))
        if rflag == "TRUE" and counts["level_LIMITED"]:
            issues.append(issue(filing,
                                "AS07_REASONABLE_FLAG_WITH_LIMITED_INDICATORS",
                                "INFO", "2024 reasonable-assurance flag "
                                f"(BRSR Core) true and {counts['level_LIMITED']}"
                                " indicators at limited assurance - not a "
                                "contradiction by itself (indicators outside "
                                "the BRSR Core may have limited assurance); "
                                "indicator-to-Core mapping not established"))
        engagements.append({
            "filing_id": filing["filing_id"],
            "company_name": filing["legal_name"],
            "master_isin": filing["isin"], "nse_symbol": filing["nse_symbol"],
            "bse_scrip_code": filing["bse_scrip_code"],
            "fy_slot": filing["fy_slot"], "taxonomy_version": version,
            "reporting_period_start": filing["reporting_period_start"],
            "reporting_period_end": filing["reporting_period_end"],
            "brsr_core_engagement_2025": eng or "NOT_REPORTED",
            "brsr_core_engagement_raw": eng_raw,
            "brsr_core_engagement_fact_id": eng_fact,
            "reasonable_assurance_flag_2024": rflag or "NOT_REPORTED",
            "reasonable_assurance_flag_raw": rflag_raw,
            "reasonable_assurance_flag_fact_id": rflag_fact,
            "engagement_type": engagement_type,
            "filing_level_assurance_level": level,
            "indicator_scope": cov or "NOT_REPORTED",
            "indicator_scope_raw": cov_raw,
            "providers_reported": len(named),
            "provider_names": " | ".join(p["provider_name"] for p in named),
            "provider_registration_ids": " | ".join(
                p["provider_registration_id"] for p in named
                if p["provider_registration_id"]),
            "signing_dates": " | ".join(p["signing_date"] for p in named
                                        if p["signing_date"]),
            "indicators_with_assured_flag": counts["assured_TRUE"]
            + counts["assured_FALSE"],
            "indicators_assured_true": counts["assured_TRUE"],
            "indicators_assured_ambiguous": counts["assured_AMBIGUOUS"],
            "assured_indicators_reasonable": counts["level_REASONABLE"],
            "assured_indicators_limited": counts["level_LIMITED"],
            "assured_indicators_level_not_disclosed":
                counts["level_NOT_REPORTED"] + counts["level_EMPTY"],
            "legacy_external_agency_topics_true": " | ".join(sorted({
                r["topic"] for r in legacy
                if r["filing_id"] == filing["filing_id"]
                and r["family"] == "LEGACY_EXTERNAL_AGENCY"
                and r["carried_out"] == "TRUE"})),
            "source_url": filing["xbrl_url"]})
    for r in facts[facts["disclosure_status"] == "AMBIGUOUS"].to_dict(
            "records"):
        if r["family"] in ("INDICATOR_ASSURED",):
            continue                  # reported per indicator (AS04)
        issues.append({**issue({"filing_id": r["filing_id"],
                                "legal_name": r["company_name"],
                                "isin": r["master_isin"],
                                "nse_symbol": r["nse_symbol"],
                                "fy_slot": r["fy_slot"],
                                "taxonomy_version": r["taxonomy_version"],
                                "xbrl_url": r["source_url"]},
                               "AS04_AMBIGUOUS_VALUE", "WARNING",
                               f"{r['concept']}: {r['value']!r} is not an "
                               f"allowed {r['value_kind']} value"),
                       "concept": r["concept"], "context_id": r["context_id"],
                       "fact_id": r["fact_id"], "xml_line": r["xml_line"],
                       "source_file": r["source_file"]})
    return (pd.DataFrame(engagements), pd.DataFrame(providers),
            pd.DataFrame(indicators), pd.DataFrame(legacy),
            pd.DataFrame(issues))


# ==========================================================
# mapping issues and registry reconciliation
# ==========================================================

SEMANTIC_NOTES = {
    "WhetherTheCompanyHasUndertakenReasonableAssuranceOfTheBRSRCore":
        "2024 asks only about REASONABLE assurance (true/false); the current "
        "concept asks assessment vs assurance. Not equivalent: false does "
        "not mean 'No'. Kept separate; candidate mapping not applied.",
    "TypeOfAssuranceObtain":
        "All/Partial scope of assured indicators; current concept also "
        "covers assessments. Kept under the original concept.",
}


def mapping_issues():
    cw = read(CROSSWALK)
    x = cw[(cw["source_concept"] != cw["current_concept"])
           & cw["source_concept"].map(lambda c: classify_concept(c)
                                      is not None
                                      or "AssuranceProvider" in c
                                      or "Assurer" in c)]
    rows = []
    for r in x.to_dict("records"):
        note = SEMANTIC_NOTES.get(r["source_concept"], "")
        if not note and ("AssuranceProvider" in r["source_concept"]
                         or r["source_concept"].endswith("Assurer")):
            note = ("2024 'assurance provider / assurer' renamed to "
                    "'assessment or assurance provider / assessor or "
                    "assurer': the current concept is wider. Extraction "
                    "keeps the original 2024 concept name.")
        rows.append({"source_version": r["source_version"],
                     "source_concept": r["source_concept"],
                     "current_concept": r["current_concept"],
                     "crosswalk_status": r["status"],
                     "approval_status": r["approval_status"],
                     "methods": r["methods"],
                     "semantic_difference": bool(note),
                     "semantic_note": note,
                     "extraction_handling": "original concept kept; no "
                     "equivalence applied; crosswalk not modified",
                     "review_needed": r["approval_status"] ==
                     "CANDIDATE_REVIEW" or bool(note)})
    return pd.DataFrame(rows)


def registry_reconciliation(engagements, providers):
    m = read(REQ_MAP)
    v = read(REQ_VALUES)
    v = v[v["requirement_id"].str.startswith("V")]
    rows = []
    for r in m[m["requirement_id"].str.startswith("V")].to_dict("records"):
        vals = v[v["requirement_id"] == r["requirement_id"]]
        rows.append({
            "requirement_id": r["requirement_id"],
            "metric_key": r["metric_key"],
            "current_concept": r["current_concept"],
            "requirement_rows": len(vals),
            "requirement_rows_by_crosswalk_status": "; ".join(
                f"{k}={n}" for k, n in vals["crosswalk_status"]
                .value_counts().items()),
            "assurance_extraction_field": {
                "V01": "brsr_core_engagement_2025 / "
                       "reasonable_assurance_flag_2024 (separate)",
                "V02": "indicator_scope",
                "V03": "providers.provider_name",
                "V04": "providers.provider_registration_id",
                "V05": "providers.signatory_name",
                "V06": "providers.signatory_designation",
                "V07": "providers.signing_date",
                "V08": "indicator / section coverage"}.get(
                    r["requirement_id"], "")})
    return pd.DataFrame(rows)


# ==========================================================
# build
# ==========================================================

def provider_coverage(prov):
    """
    Provider-wise coverage. Names are grouped only after case, punctuation
    and spacing normalisation; spelling variants (e.g. 'Bereau' / 'Bureau')
    are NOT merged - they are listed for review.
    """
    p = prov[prov["provider_name"] != ""].copy()
    if not len(p):
        return pd.DataFrame()
    p["provider_key"] = p["provider_name"].str.upper().str.replace(
        r"[^A-Z0-9 ]", " ", regex=True).str.split().str.join(" ")
    g = p.groupby("provider_key")
    return pd.DataFrame({
        "filings": g["filing_id"].nunique(),
        "companies": g["master_isin"].nunique(),
        "fy_slots": g["fy_slot"].agg(lambda s: " | ".join(sorted(set(s)))),
        "taxonomy_versions": g["taxonomy_version"].agg(
            lambda s: " | ".join(sorted(set(s)))),
        "registration_ids": g["provider_registration_id"].agg(
            lambda s: " | ".join(sorted(set(s) - {""}))[:300]),
        "spellings_as_filed": g["provider_name"].agg(
            lambda s: " | ".join(sorted(set(s)))[:300]),
    }).reset_index().sort_values("filings", ascending=False)


def build(write=True):
    filings = scope()
    facts = extract_facts(filings)
    eng, prov, ind, legacy, issues = build_tables(facts, filings)
    mapping = mapping_issues()
    recon = registry_reconciliation(eng, prov)
    issues = issues.drop_duplicates()
    summary = pd.DataFrame([
        {"measure": "filings examined", "value": len(filings)},
        {"measure": "companies examined",
         "value": filings["company_id"].nunique()},
        {"measure": "assurance facts extracted", "value": len(facts)},
        *[{"measure": f"engagement_type={k}", "value": n}
          for k, n in eng["engagement_type"].value_counts().items()],
        *[{"measure": f"filing_level_assurance_level={k}", "value": n}
          for k, n in eng["filing_level_assurance_level"]
          .value_counts().items()],
        {"measure": "assured indicators reasonable",
         "value": int(eng["assured_indicators_reasonable"].sum())},
        {"measure": "assured indicators limited",
         "value": int(eng["assured_indicators_limited"].sum())},
        {"measure": "issues", "value": len(issues)}])
    if write:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        for name, df in (("assurance_facts.csv", facts),
                         ("assurance_engagements.csv", eng),
                         ("assurance_providers.csv", prov),
                         ("assurance_indicator_coverage.csv", ind),
                         ("assurance_external_agency_legacy.csv", legacy),
                         ("assurance_issues.csv", issues),
                         ("assurance_mapping_issues.csv", mapping),
                         ("assurance_metric_registry_reconciliation.csv",
                          recon),
                         ("assurance_summary.csv", summary),
                         ("assurance_provider_coverage.csv",
                          provider_coverage(prov))):
            df.to_csv(OUTPUT_DIR / name, index=False, encoding="utf-8-sig")
    log.info("assurance built", extra={"fields": {
        "facts": len(facts), "filings": len(eng), "issues": len(issues)}})
    return {"facts": facts, "engagements": eng, "providers": prov,
            "indicators": ind, "legacy": legacy, "issues": issues,
            "mapping": mapping, "recon": recon, "summary": summary,
            "filings": filings}


def main():
    out = build()
    pd.set_option("display.width", 200)
    print(out["summary"].to_string(index=False))
    print("\nissues:", out["issues"]["issue_code"].value_counts().to_dict())
    print(f"\nOutputs: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
