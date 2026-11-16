"""
Anomaly engine (Phase 11).

Detects anomalies from the production normalisation (factor-model engine,
normalised/), the Phase 9 validation results, the Phase 10 blocks, the
parser, the crosswalk and the existing review files. Read only.

Every anomaly carries its evidence and source references and a
deterministic anomaly_id (a fingerprint of rule, source fact and original
value), so a reviewer decision stays attached to exactly that fact and a
changed value is detected afresh.

Detection never corrects anything and never approves anything; statuses
are assigned by app.review.workflow.

Categories
  YOY_CHANGE            > 10x change between consecutive FY slots
  UNIT_SCALE            scale / unit-tag errors, ambiguous unit symbols
  CONVERSION_FACTOR     factor-model invariant violations, legacy factors
  IMPLAUSIBLE_VALUE     outside documented bands, negative, non-integer
                        counts, consumption > withdrawal, emission factor
  CY_PY_INCONSISTENCY   restated comparatives, pending > filed + opening,
                        conflicting duplicate CY/PY facts
  SCOPE_EMISSIONS       Scope 1 / 2 / 3 consistency
  ENV_INTENSITY         per-INR intensity anomalies
  STAKEHOLDER           deferred stakeholder issues, missing groups
  DUPLICATE_FACT        duplicate / overlapping facts
  CROSSWALK_TAXONOMY    candidate mappings, concepts outside the taxonomy
  ASSURANCE             Phase 12 assurance disclosure issues (assessment vs
                        assurance, levels, providers, signing dates)
  SUPPLEMENTARY_EVIDENCE  Phase 13 candidate evidence from annual / CSR /
                        BRSR PDF reports (never applied), unreadable
                        documents, XBRL values not printed verbatim
"""

import hashlib
from decimal import Decimal, InvalidOperation

import pandas as pd

from app.config import settings
from app.core import deferred_issues
from app.normalisation import factor_model as fm

N = settings.processed_dir / "nifty100"
REF = settings.reference_dir
SOURCES = {
    "production_cy": fm.PRODUCTION["CY"],
    "validation": N / "validation" / "validation_results.csv",
    "fse": fm.SCALE_DECISIONS,
    "fse_evidence": N / "filer_scale_error_review" /
    "filer_scale_error_evidence.csv",
    "unit_factor_issues": fm.UNIT_FACTOR_ISSUES,
    "mtco2e": fm.MTCO2E_DECISIONS,
    "invariants": N / "normalised_factor_model" /
    "factor_model_invariants.csv",
    "parse_issues": N / "parsed" / "xbrl_parse_issues.csv",
    "parsed_facts": N / "parsed" / "xbrl_facts.csv",
    "company_master": settings.processed_dir / "registry" /
    "company_master.csv",
    "crosswalk": settings.processed_dir / "taxonomy_versions" /
    "nse_brsr_concept_crosswalk.csv",
    "requirement_values": N / "brsr_requirement_values.csv",
    "stakeholder_recon": N / "review" /
    "g16_g17_stakeholder_reconciliation" /
    "09_company_year_reconciliation.csv",
    "stakeholder_duplicates": N / "review" /
    "g16_g17_stakeholder_reconciliation" /
    "03_duplicates_of_standard_groups.csv",
    "deferred": deferred_issues.REGISTER,
    "assurance_issues": N / "assurance" / "assurance_issues.csv",
    "assurance_mapping": N / "assurance" / "assurance_mapping_issues.csv",
    "supp_coverage": N / "supplementary" /
    "external_requirement_coverage.csv",
    "supp_crosscheck": N / "supplementary" / "xbrl_pdf_crosscheck.csv",
    "supp_inventory": N / "supplementary" / "document_inventory.csv",
}

# plausibility bands in normalised units, from the filer scale-error
# investigation (scale_review/investigate_scale_errors.py BANDS)
BANDS = {
    "A06": (1e9, 1e15), "A07": (1e9, 1e15),
    "E08": (1e-10, 1e-3), "E16": (1e-11, 1e-3), "E19": (1e-13, 1e-4),
    "E07": (1e2, 1e10), "E05": (0.5, 1e10), "E06": (0.5, 1e10),
    "E01": (0.5, 1e10), "E02": (0.5, 1e10), "E03": (0.5, 1e10),
    "E04": (0.5, 1e10), "E14": (1e2, 1e10), "E15": (1e1, 1e10),
    "E09": (0.5, 1e10), "E10": (0.5, 1e10), "E11": (0.5, 1e10),
    "E12": (0.5, 1e10), "E13": (0.5, 1e10), "S10": (0.0, 100.0),
}
INTENSITIES = {"E08", "E16", "E19"}
SCOPES = {"E17", "E18", "E20"}
YOY_METRICS = (["A06", "A07", "A08", "S07", "S08"]
               + [f"E{i:02d}" for i in range(1, 36)])
SCALE_POWERS = [3, 5, 6, 7, 9, 12]


def dec(v):
    try:
        return None if v in ("", None) else Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None


def read(path):
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def fingerprint(*parts):
    return hashlib.sha1("\x1f".join(str(p) for p in parts)
                        .encode("utf-8")).hexdigest()[:12]


def scale_hint(ratio):
    if ratio is None or ratio <= 0:
        return None
    for k in SCALE_POWERS:
        for sign in (1, -1):
            target = Decimal(10) ** (sign * k)
            if abs(ratio / target - 1) <= Decimal("0.1"):
                return sign * k
    return None


class Anomalies:
    def __init__(self, values):
        self.rows = []
        self.by_fact = {}
        self.by_slot = {}
        for r in values.to_dict("records"):
            self.by_fact[(r["source_file"], r["context_reference"],
                          r["requirement_id"], r["breakdown"])] = r
            self.by_slot.setdefault((r["master_isin"], r["fy_slot"],
                                     r["requirement_id"], r["breakdown"]), r)

    def add(self, category, rule, reason, severity, confidence, fact=None,
            key=None, proposed=None, evidence="", evidence_files="",
            linked_ref="", source_decision="", recommended_decision=""):
        f = fact or {}
        k = key or {}
        get = lambda name: f.get(name, k.get(name, ""))
        p = proposed or {}
        # identity of the detection: rule + entity + period + source fact +
        # value as filed (+ evidence for detections without a single fact)
        aid = "ANM-" + fingerprint(rule, get("master_isin"), get("fy_slot"),
                                   get("source_file"),
                                   get("context_reference"),
                                   get("requirement_id"), get("breakdown"),
                                   get("original_value"), linked_ref,
                                   "" if f else f"{reason}|{evidence}")
        self.rows.append({
            "anomaly_id": aid, "category": category, "detection_rule": rule,
            "reason": reason, "severity": severity, "confidence": confidence,
            "company_name": get("company_name"),
            "master_isin": get("master_isin"),
            "nse_symbol": get("master_nse_symbol") or get("nse_symbol"),
            "fy_slot": get("fy_slot"),
            "requirement_id": get("requirement_id"),
            "metric_name": get("metric_name"),
            "breakdown": get("breakdown"),
            "original_value": get("original_value"),
            "original_unit": get("original_unit"),
            "normalised_value": get("final_value"),
            "normalised_unit": get("final_unit"),
            "proposed_value": p.get("value", ""),
            "proposed_unit": p.get("unit", ""),
            "proposed_multiplier_on_original": p.get("multiplier", ""),
            "proposal": p.get("text", ""),
            "evidence": evidence, "evidence_files": evidence_files,
            "source_file": get("source_file"),
            "context_reference": get("context_reference"),
            "source_url": get("source_url"),
            "linked_ref": linked_ref, "source_decision": source_decision,
            "recommended_decision": recommended_decision})

    def fact(self, source_file, context, req, breakdown=""):
        return self.by_fact.get((source_file, context, req, breakdown))

    def slot(self, isin, slot, req, breakdown=""):
        hit = self.by_slot.get((isin, slot, req, breakdown))
        if hit is None and breakdown:
            # rule evaluated on a breakdown without its gender member:
            # point at the gender-total fact of that breakdown
            for b in (f"{breakdown} | GenderAxis=GenderMember",
                      f"GenderAxis=GenderMember | {breakdown}"):
                hit = self.by_slot.get((isin, slot, req, b))
                if hit:
                    break
        return hit


def rel(path):
    try:
        return path.relative_to(settings.project_root).as_posix()
    except ValueError:
        return str(path)


# ==========================================================
# detectors
# ==========================================================

def yoy(cy, a):
    totals = cy[(cy["breakdown"] == "") & cy["requirement_id"].isin(
        YOY_METRICS + sorted(INTENSITIES))]
    for (isin, req), g in totals.groupby(["master_isin", "requirement_id"]):
        g = g.sort_values("fy_slot")
        rows = g.to_dict("records")
        for prev, cur in zip(rows, rows[1:]):
            x, y = dec(prev["final_value"]), dec(cur["final_value"])
            if not x or not y:
                continue
            ratio = y / x
            if Decimal("0.1") <= abs(ratio) <= 10:
                continue
            k = scale_hint(abs(ratio))
            category = "ENV_INTENSITY" if req in INTENSITIES else "YOY_CHANGE"
            a.add(category, "A01_YOY_10X",
                  f"{prev['fy_slot']} {x} -> {cur['fy_slot']} {y} "
                  f"(x{ratio:.4G})", "WARNING",
                  "MEDIUM" if k else "LOW", fact=cur,
                  evidence=f"previous slot fact {prev['source_file']} "
                           f"{prev['context_reference']}",
                  evidence_files=rel(SOURCES["production_cy"]))
            if k:
                a.add("UNIT_SCALE", "A02_YOY_POWER_OF_TEN",
                      f"year-on-year ratio x{ratio:.4G} is close to 10^{k}; "
                      "which year is wrong is not established", "WARNING",
                      "MEDIUM", fact=cur,
                      evidence=f"{prev['fy_slot']}={x}; {cur['fy_slot']}={y}",
                      evidence_files=rel(SOURCES["production_cy"]))


def from_validation(res, a):
    mapping = {
        "STR05": ("IMPLAUSIBLE_VALUE", "WARNING", "HIGH"),
        "STR08": ("IMPLAUSIBLE_VALUE", "ERROR", "HIGH"),
        "ENV04": ("IMPLAUSIBLE_VALUE", "WARNING", "MEDIUM"),
        "ENV07": ("SCOPE_EMISSIONS", "WARNING", "MEDIUM"),
        "SOC01": ("IMPLAUSIBLE_VALUE", "WARNING", "HIGH"),
        "SOC03": ("IMPLAUSIBLE_VALUE", "WARNING", "HIGH"),
        "GOV01": ("IMPLAUSIBLE_VALUE", "ERROR", "HIGH"),
        "GOV02": ("IMPLAUSIBLE_VALUE", "WARNING", "HIGH"),
        "SOC04": ("CY_PY_INCONSISTENCY", "WARNING", "HIGH"),
        "GOV03": ("CY_PY_INCONSISTENCY", "WARNING", "HIGH"),
        "GOV04": ("CY_PY_INCONSISTENCY", "WARNING", "HIGH"),
        "INT02": ("ENV_INTENSITY", "ERROR", "HIGH"),
        "STR01": ("CONVERSION_FACTOR", "CRITICAL", "HIGH"),
        "STR02": ("CONVERSION_FACTOR", "CRITICAL", "HIGH"),
        "STR03": ("CONVERSION_FACTOR", "ERROR", "HIGH"),
        "STR04": ("DUPLICATE_FACT", "WARNING", "HIGH"),
    }
    fails = res[res["status"] == "FAIL"]
    for r in fails.to_dict("records"):
        rule, req = r["rule_id"], r["requirement_id"].split(" ")[0]
        fact = a.slot(r["master_isin"], r["fy_slot"], req, r["breakdown"]) \
            or {**r, "requirement_id": req}
        files = rel(SOURCES["validation"])
        if rule in mapping:
            cat, sev, conf = mapping[rule]
            a.add(cat, f"A10_{rule}", r["detail"], sev, conf, fact=fact,
                  evidence=f"validation {rule}: observed {r['observed']} "
                           f"expected {r['expected']}", evidence_files=files,
                  linked_ref=r["deferred_issue_ids"])
        elif rule == "INT01":
            hint = "looks like" in r["detail"]
            a.add("UNIT_SCALE" if hint else "ENV_INTENSITY", "A11_INT01",
                  r["detail"], "WARNING", "MEDIUM" if hint else "LOW",
                  fact=fact, evidence="Phase 9 INT01 / legacy C06",
                  evidence_files=files)
        elif rule == "RST01":
            if r["severity"] == "ERROR":
                a.add("UNIT_SCALE", "A12_RST01_SCALE", r["detail"], "ERROR",
                      "HIGH", fact=fact,
                      evidence="next report restates with a power-of-ten "
                               "ratio", evidence_files=files)
            else:
                a.add("CY_PY_INCONSISTENCY", "A13_RST01_RESTATED",
                      r["detail"], "INFO", "LOW", fact=fact,
                      evidence="next report PY differs by more than 1 %",
                      evidence_files=files,
                      linked_ref=r["deferred_issue_ids"])
        elif rule == "RST02":
            pass                      # covered by A01 on production values


def bands(cy, a):
    totals = cy[(cy["breakdown"] == "") & cy["requirement_id"].isin(BANDS)]
    for r in totals.to_dict("records"):
        v = dec(r["final_value"])
        if v is None:
            continue
        lo, hi = BANDS[r["requirement_id"]]
        if Decimal(str(lo)) <= v <= Decimal(str(hi)) or v == 0:
            continue
        a.add("ENV_INTENSITY" if r["requirement_id"] in INTENSITIES
              else "IMPLAUSIBLE_VALUE", "A20_OUTSIDE_BAND",
              f"{v} {r['final_unit']} outside plausibility band {lo}-{hi}",
              "WARNING", "MEDIUM", fact=r,
              evidence="bands from the filer scale-error investigation",
              evidence_files=rel(SOURCES["production_cy"]))


def scope(cy, a):
    totals = cy[cy["breakdown"] == ""]
    for (isin, slot), g in totals.groupby(["master_isin", "fy_slot"]):
        rows = {r["requirement_id"]: r for r in g.to_dict("records")}
        v = {k: dec(r["final_value"]) for k, r in rows.items()}
        if v.get("E18") == 0 and (v.get("E02") or 0) > 0:
            a.add("SCOPE_EMISSIONS", "A30_SCOPE2_ZERO_WITH_GRID_POWER",
                  f"Scope 2 = 0 while non-renewable electricity E02 = "
                  f"{v['E02']} GJ", "WARNING", "MEDIUM", fact=rows["E18"],
                  evidence=f"E02 fact {rows['E02']['context_reference']}",
                  evidence_files=rel(SOURCES["production_cy"]))
        tags = {k: rows[k]["original_unit"].split(" / ")[0]
                for k in sorted(SCOPES)        # deterministic text order
                if k in rows and rows[k]["original_unit"]}
        if len(set(tags.values())) > 1:
            first = rows[sorted(tags)[0]]
            a.add("SCOPE_EMISSIONS", "A31_SCOPE_UNIT_TAGS_DIFFER",
                  f"scope unit tags differ in one report: {tags}", "WARNING",
                  "MEDIUM", fact=first, evidence="original unit tags",
                  evidence_files=rel(SOURCES["production_cy"]))
        if "E20" in rows and v.get("E20") is not None and (
                v.get("E17") is None or v.get("E18") is None):
            a.add("SCOPE_EMISSIONS", "A32_SCOPE3_WITHOUT_SCOPE12",
                  "Scope 3 reported but Scope 1 or Scope 2 has no "
                  "normalised value", "INFO", "MEDIUM", fact=rows["E20"],
                  evidence_files=rel(SOURCES["production_cy"]))


def scale_records(a):
    fse = read(SOURCES["fse"])
    for r in fse.to_dict("records"):
        fact = a.fact(r["source_file"], r["context_reference"],
                      r["requirement_id"], r["breakdown"])
        a.add("UNIT_SCALE", "A40_FILER_SCALE_ERROR_REVIEW",
              f"{r['classification']}: {r['error_mechanism']}", "ERROR",
              "HIGH" if r["evidence_strength"] == "STRONG" else "MEDIUM",
              fact=fact or r,
              proposed={"value": r["proposed_corrected_value"],
                        "unit": r["proposed_corrected_unit"],
                        "multiplier":
                            r["actual_numeric_multiplier_on_original_value"]},
              evidence=f"{r['supporting_checks']}; block "
                       f"{r['block_dependency']}; {r['reviewer_notes'][:300]}",
              evidence_files=rel(SOURCES["fse"]),
              linked_ref=r["approval_id"],
              source_decision=r["approval_decision"] or "NO_DECISION",
              recommended_decision=r["recommended_decision"])
    ev = read(SOURCES["fse_evidence"])
    in_review = {(r["source_file"], r["requirement_id"], r["breakdown"])
                 for r in fse.to_dict("records")}
    for r in ev.to_dict("records"):
        if (r["source_file"], r["requirement_id"], r["breakdown"]) in \
                in_review or r["classification"] == "NO_ERROR_FOUND":
            continue
        fact = a.fact(r["source_file"], r["context_reference"],
                      r["requirement_id"], r["breakdown"])
        a.add("UNIT_SCALE", "A41_SCALE_EVIDENCE_NOT_STRONG",
              f"{r['classification']} ({r['evidence_strength']}): "
              f"{r['classification_reason'][:200]}", "WARNING",
              "MEDIUM" if r["evidence_strength"] == "MODERATE" else "LOW",
              fact=fact or r, evidence=r["notes"][:300],
              evidence_files=rel(SOURCES["fse_evidence"]))
    for r in read(SOURCES["unit_factor_issues"]).to_dict("records"):
        a.add("CONVERSION_FACTOR", "A42_LEGACY_FACTOR_DIFFERS",
              r["issue"], "INFO", "HIGH",
              key={**r, "nse_symbol": r["master_nse_symbol"]},
              evidence=f"legacy factor {r['legacy_factor_on_normalised_value']}"
                       f" vs multiplier on original "
                       f"{r['actual_numeric_multiplier_on_original']} - the "
                       "factor model applies only the latter",
              evidence_files=rel(SOURCES["unit_factor_issues"]),
              linked_ref=r["approval_id"])


def unit_assumptions(a):
    for r in read(SOURCES["mtco2e"]).to_dict("records"):
        fact = a.fact(r["source_file"], r["context_reference"],
                      r["requirement_id"], r["breakdown"])
        proposed = {"value": (fact or {}).get(
            "proposed_value_if_assumption_approved", ""),
            "unit": "tCO2e" if not r["requirement_id"] == "E19"
            else "tCO2e/INR",
            "multiplier": r["approved_factor"] or "1",
            "text": r["recommended_interpretation"]}
        a.add("UNIT_SCALE", "A43_AMBIGUOUS_GHG_UNIT",
              f"ambiguous unit {r['original_unit']}; recommended "
              f"{r['recommended_interpretation']}", "WARNING",
              "HIGH" if r["restatement_evidence"] else "MEDIUM",
              fact=fact or r, proposed=proposed,
              evidence=f"{r['restatement_evidence']}; "
                       f"{r['approval_evidence'][:200]}",
              evidence_files=rel(SOURCES["mtco2e"]),
              linked_ref="MT-" + fingerprint(r["source_file"],
                                             r["context_reference"],
                                             r["requirement_id"]),
              source_decision=r["decision"])


def invariants(a):
    inv = read(SOURCES["invariants"])
    for r in inv.to_dict("records"):
        if int(r["violations"]):
            a.add("CONVERSION_FACTOR", "A44_FACTOR_MODEL_INVARIANT",
                  f"{r['invariant']}: {r['violations']} violations",
                  "CRITICAL", "HIGH", key={},
                  evidence=r["examples"],
                  evidence_files=rel(SOURCES["invariants"]))


def parsed_facts(fact_ids):
    """Context, concept and value of specific parsed facts (by fact_id)."""
    out = {}
    if not fact_ids:
        return out
    for chunk in pd.read_csv(SOURCES["parsed_facts"], dtype=str,
                             keep_default_na=False, chunksize=250_000,
                             usecols=["fact_id", "context_id", "concept",
                                      "value", "unit_measure",
                                      "dimension_key"]):
        hit = chunk[chunk["fact_id"].isin(fact_ids)]
        out.update({r["fact_id"]: r for r in hit.to_dict("records")})
    return out


def parser_issues(a):
    issues = read(SOURCES["parse_issues"])
    wanted = issues[issues["code"].isin(["DUPLICATE_FACT_INCONSISTENT",
                                         "DUPLICATE_FACT_COMPLETE",
                                         "CONCEPT_NOT_IN_TAXONOMY"])]
    facts = parsed_facts(set(wanted["fact_id"]))
    master = read(SOURCES["company_master"])
    symbol = dict(zip(master["company_id"], master["nse_symbol"]))
    names = dict(zip(master["company_id"], master["legal_name"]))
    for r in wanted.to_dict("records"):
        f = facts.get(r["fact_id"], {})
        key = {"company_name": names.get(r["company_id"], ""),
               "master_isin": r["company_id"].replace("C-", ""),
               "nse_symbol": symbol.get(r["company_id"], ""),
               "fy_slot": r["fy_slot"], "source_file": r["source_file"],
               "context_reference": f.get("context_id", ""),
               "requirement_id": "", "metric_name": f.get("concept", ""),
               "breakdown": f.get("dimension_key", ""),
               "original_value": f.get("value", ""),
               "original_unit": f.get("unit_measure", "")}
        evidence = f"fact {r['fact_id']} line {r['line']}"
        if r["code"] == "CONCEPT_NOT_IN_TAXONOMY":
            a.add("CROSSWALK_TAXONOMY", "A51_CONCEPT_NOT_IN_TAXONOMY",
                  r["detail"], "WARNING", "HIGH", key=key, evidence=evidence,
                  evidence_files=rel(SOURCES["parse_issues"]))
            continue
        inconsistent = r["code"].endswith("INCONSISTENT")
        a.add("CY_PY_INCONSISTENCY" if inconsistent else "DUPLICATE_FACT",
              "A50_PARSER_DUPLICATE", f"{r['code']}: {r['detail']}",
              "ERROR" if inconsistent else "INFO", "HIGH", key=key,
              evidence=evidence,
              evidence_files=rel(SOURCES["parse_issues"]))


def crosswalk(a):
    cw = read(SOURCES["crosswalk"])
    values = read(SOURCES["requirement_values"])
    cand = values[values["crosswalk_status"] == "CANDIDATE_REVIEW"]
    for r in cw[cw["status"] == "CANDIDATE_REVIEW"].to_dict("records"):
        affected = cand[(cand["metric_name"] == r["source_concept"])
                        & (cand["taxonomy_version"] == r["source_version"])]
        a.add("CROSSWALK_TAXONOMY", "A52_CROSSWALK_CANDIDATE",
              f"{r['source_version']} {r['source_concept']} -> "
              f"{r['current_concept']} is CANDIDATE_REVIEW; "
              f"{len(affected)} requirement values reached through it",
              "WARNING", "MEDIUM",
              key={"requirement_id": " ".join(sorted(set(
                  affected["requirement_id"]))), "fy_slot": "",
                  "source_file": "", "context_reference":
                  f"{r['source_version']}:{r['source_concept']}"},
              proposed={"text": f"map {r['source_concept']} to "
                                f"{r['current_concept']} ({r['methods']})"},
              evidence=f"methods {r['methods']}; chain {r['chain']}",
              evidence_files=rel(SOURCES["crosswalk"]),
              linked_ref="XW-" + fingerprint(r["source_version"],
                                             r["source_concept"]),
              source_decision=r["approval_status"])


def stakeholder(a):
    for r in deferred_issues.load().to_dict("records"):
        metric = r["metric"].split(" ")[0]
        a.add("STAKEHOLDER", "A60_DEFERRED_ISSUE", r["item"],
              "WARNING", "MEDIUM",
              key={"company_name": "", "master_isin": r["master_isin"],
                   "nse_symbol": r["company"], "fy_slot": r["fy_slot"],
                   "requirement_id": metric,
                   "original_value": r["original_value"],
                   "source_file": "", "context_reference": r["issue_id"]},
              proposed={"text": r["proposed_interpretation_or_correction"]},
              evidence=f"{r['evidence_reference']}; risk: "
                       f"{r['risk_impact']}",
              evidence_files=r["evidence_file"], linked_ref=r["issue_id"],
              source_decision=r["status"])
    recon = read(SOURCES["stakeholder_recon"])
    only_missing = recon[recon["reasons"] == "STANDARD_GROUPS_MISSING"]
    for r in only_missing.to_dict("records"):
        a.add("STAKEHOLDER", "A61_STANDARD_GROUP_NOT_DISCLOSED",
              f"standard stakeholder groups not disclosed: "
              f"{r['standard_groups_missing']} (not treated as zero)",
              "INFO", "HIGH",
              key={"master_isin": r["master_isin"],
                   "nse_symbol": r["nse_symbol"], "fy_slot": r["fy_slot"],
                   "requirement_id": r["requirement_id"],
                   "source_file": "", "context_reference": "groups"},
              evidence="reconciliation total_status NO_RELIABLE_TOTAL",
              evidence_files=rel(SOURCES["stakeholder_recon"]))
    dup = read(SOURCES["stakeholder_duplicates"])
    for (isin, slot, req, role), g in dup.groupby(
            ["master_isin", "fy_slot", "requirement_id", "period_role"]):
        a.add("DUPLICATE_FACT", "A62_STAKEHOLDER_RELISTED_STANDARD_GROUP",
              f"{len(g)} OtherStakeHolderAxis rows repeat standard groups "
              f"({role}); excluded from totals",
              "INFO", "HIGH",
              key={"master_isin": isin, "nse_symbol": g["nse_symbol"].iloc[0],
                   "fy_slot": slot, "requirement_id": req,
                   "source_file": g["source_file"].iloc[0],
                   "context_reference": role},
              evidence="classification DUPLICATE_OF_STANDARD_GROUP",
              evidence_files=rel(SOURCES["stakeholder_duplicates"]))


def assurance(a):
    """Phase 12 outputs (absent before Phase 12 runs)."""
    path = SOURCES["assurance_issues"]
    if path.exists():
        for r in read(path).to_dict("records"):
            a.add("ASSURANCE", f"A70_{r['issue_code']}", r["detail"],
                  r["severity"], "HIGH",
                  key={"company_name": r["company_name"],
                       "master_isin": r["master_isin"],
                       "nse_symbol": r["nse_symbol"], "fy_slot": r["fy_slot"],
                       "requirement_id": "", "metric_name": r["concept"],
                       "source_file": r["source_file"],
                       "context_reference": r["context_id"] or "filing",
                       "source_url": r["source_url"]},
                  evidence=f"fact {r['fact_id']} line {r['xml_line']}"
                  if r["fact_id"] else f"filing {r['filing_id']}",
                  evidence_files=rel(path))
    path = SOURCES["assurance_mapping"]
    if path.exists():
        m = read(path)
        for r in m[(m["semantic_difference"] == "True")
                   & (m["approval_status"] != "CANDIDATE_REVIEW")].to_dict(
                "records"):
            a.add("CROSSWALK_TAXONOMY", "A71_ASSURANCE_MAPPING_WIDENS_MEANING",
                  f"{r['source_version']} {r['source_concept']} -> "
                  f"{r['current_concept']} ({r['crosswalk_status']}): "
                  f"{r['semantic_note']}", "INFO", "HIGH",
                  key={"requirement_id": "", "fy_slot": "", "source_file": "",
                       "context_reference":
                           f"{r['source_version']}:{r['source_concept']}"},
                  evidence="approved crosswalk unchanged; extraction keeps "
                           "the original concept", evidence_files=rel(path))


def supplementary(a):
    """Phase 13 outputs (absent before Phase 13 runs)."""
    path = SOURCES["supp_coverage"]
    if path.exists():
        cov = read(path)
        for r in cov[cov["status"] == "CANDIDATE_EVIDENCE_FOUND"].to_dict(
                "records"):
            values = r["candidate_values"]
            a.add("SUPPLEMENTARY_EVIDENCE", "A80_EXTERNAL_REQUIREMENT_"
                  "CANDIDATE", f"{r['requirement_id']} {r['requirement']}: "
                  f"{r['candidates']} candidate(s) in supplementary documents",
                  "INFO", "LOW",
                  key={"master_isin": r["master_isin"],
                       "nse_symbol": r["nse_symbol"], "fy_slot": r["fy_slot"],
                       "requirement_id": r["requirement_id"],
                       "source_file": "", "context_reference": "supplementary"},
                  proposed={"text": f"candidate value text (unverified): "
                                    f"{values}"} if values else None,
                  evidence=f"documents: {r['documents_checked']}",
                  evidence_files=rel(path))
    path = SOURCES["supp_crosscheck"]
    if path.exists():
        x = read(path)
        for r in x[x["result"] == "NOT_FOUND_VERBATIM"].to_dict("records"):
            a.add("SUPPLEMENTARY_EVIDENCE", "A81_XBRL_VALUE_NOT_IN_BRSR_PDF",
                  f"XBRL value {r['original_value']} not printed verbatim in "
                  f"{r['file_name']} (formatting / rounding may differ)",
                  "INFO", "LOW",
                  key={"master_isin": r["master_isin"],
                       "nse_symbol": r["nse_symbol"], "fy_slot": r["fy_slot"],
                       "requirement_id": r["requirement_id"],
                       "breakdown": r["breakdown"],
                       "original_value": r["original_value"],
                       "original_unit": r["original_unit"],
                       "source_file": r["xbrl_source_file"],
                       "context_reference": r["xbrl_context"]},
                  evidence=f"{r['document_id']}",
                  evidence_files=rel(path))
    path = SOURCES["supp_inventory"]
    if path.exists():
        inv = read(path)
        for r in inv[inv["text_status"].isin(
                ["UNREADABLE", "NO_TEXT_LAYER"])].to_dict("records"):
            a.add("SUPPLEMENTARY_EVIDENCE", "A82_DOCUMENT_NOT_READABLE",
                  f"{r['file_name']}: {r['text_status']} (no OCR attempted)",
                  "INFO", "HIGH",
                  key={"master_isin": r["master_isin"],
                       "nse_symbol": r["nse_symbol"], "fy_slot": r["fy_slot"],
                       "requirement_id": "", "source_file": r["file_name"],
                       "context_reference": r["document_id"]},
                  evidence=r["error"] or r["text_status"],
                  evidence_files=rel(path))


def detect():
    cy = fm.read_production(SOURCES["production_cy"])
    res = read(SOURCES["validation"])
    a = Anomalies(cy)
    yoy(cy, a)
    from_validation(res, a)
    bands(cy, a)
    scope(cy, a)
    scale_records(a)
    unit_assumptions(a)
    invariants(a)
    parser_issues(a)
    crosswalk(a)
    stakeholder(a)
    assurance(a)
    supplementary(a)
    df = pd.DataFrame(a.rows)
    dup = df[df["anomaly_id"].duplicated(keep=False)]
    if len(dup):
        raise ValueError(f"anomaly ids not unique: "
                         f"{dup['detection_rule'].value_counts().to_dict()}")
    return df
