"""Phase 12: assurance extraction."""

import hashlib
from pathlib import Path

import pandas as pd
import pytest
from lxml import etree

from app.assurance import extractor as ax
from tests.conftest import APPROVED_SWITCH_CHANGES, PROJECT_ROOT

SNAPSHOT = (PROJECT_ROOT / "data/processed/nifty100/backups/"
            "20261112_pre_phase12/all_files_before.sha")
REGENERATED = "data/processed/nifty100/review_workflow/"


@pytest.fixture(scope="module")
def built():
    return ax.build(write=False)


# ---- semantic distinctions ------------------------------------------------

@pytest.mark.parametrize("kind,value,expected", [
    ("ASSESSMENT_OR_ASSURANCE", "Yes assessment", ("ASSESSMENT", "REPORTED")),
    ("ASSESSMENT_OR_ASSURANCE", "Yes assurance", ("ASSURANCE", "REPORTED")),
    ("ASSESSMENT_OR_ASSURANCE", "No", ("NONE", "REPORTED")),
    ("ASSESSMENT_OR_ASSURANCE", "Yes", ("", "AMBIGUOUS")),
    ("LEVEL", "Reasonable", ("REASONABLE", "REPORTED")),
    ("LEVEL", "Limited", ("LIMITED", "REPORTED")),
    ("LEVEL", "Reasonable Assurance", ("", "AMBIGUOUS")),   # not coerced
    ("BOOLEAN", "true", ("TRUE", "REPORTED")),
    ("BOOLEAN", "Limited", ("", "AMBIGUOUS")),
    ("ALL_PARTIAL", "Partial", ("PARTIAL", "REPORTED")),
    ("TEXT", "NA", ("", "PLACEHOLDER_TEXT")),
])
def test_value_interpretation(kind, value, expected):
    assert ax.interpret(kind, value, "REPORTED") == expected


def test_nil_and_empty_are_not_reported_values():
    assert ax.interpret("BOOLEAN", "", "NIL") == ("", "NIL")
    assert ax.interpret("BOOLEAN", " ", "REPORTED") == ("", "EMPTY")


def test_assessment_and_assurance_never_merged(built):
    e = built["engagements"]
    assess = e[e["engagement_type"] == "ASSESSMENT"]
    assert len(assess) == 9
    # an assessment never gets a filing-level assurance level
    assert set(assess["filing_level_assurance_level"]) == {
        "NOT_DISCLOSED_AT_FILING_LEVEL"}
    assert (assess["brsr_core_engagement_raw"] == "Yes assessment").all()


def test_levels_only_where_explicit(built):
    e = built["engagements"]
    reasonable = e[e["filing_level_assurance_level"]
                   == "REASONABLE_BRSR_CORE_2024_FLAG"]
    assert (reasonable["taxonomy_version"] == "2024-04-30").all()
    assert (reasonable["reasonable_assurance_flag_2024"] == "TRUE").all()
    # 2024 'false' is not 'No' and not 'limited'
    false = e[e["reasonable_assurance_flag_2024"] == "FALSE"]
    assert (false["engagement_type"] == "UNKNOWN").all()
    ind = built["indicators"]
    lv = ind[ind["assurance_level"].isin(["REASONABLE", "LIMITED"])]
    assert (lv["assurance_level_raw"].isin(["Reasonable", "Limited"])).all()


def test_historical_taxonomy_differences(built):
    e = built["engagements"]
    old = e[e["taxonomy_version"].isin(["2021-09-30", "2023-06-30"])]
    assert (old["engagement_type"] == "NOT_IN_TAXONOMY_VERSION").all()
    v24 = e[e["taxonomy_version"] == "2024-04-30"]
    assert (v24["brsr_core_engagement_2025"] == "NOT_IN_TAXONOMY_VERSION").all()
    later = e[e["taxonomy_version"].isin(["2025-05-31", "2026-02-28"])]
    assert (later["reasonable_assurance_flag_2024"]
            == "NOT_IN_TAXONOMY_VERSION").all()
    p = built["providers"]
    p24 = p[p["taxonomy_version"] == "2024-04-30"]
    assert p24["provider_name_concept"].isin(
        ["NameOfTheCompanyOrLLPOrFirmOfAssuranceProvider"]).all()


# ---- key alignment and concept families ---------------------------------------

@pytest.mark.parametrize("concept,family,key", [
    ("WhetherDetailsOfComplaintsMadeByEmployeesAndWorkersIsAssuredByAssurer"
     "AsPerP3", "INDICATOR_ASSURED",
     "DetailsOfComplaintsMadeByEmployeesAndWorkersAsPerP3"),
    ("AssuranceSubTypeForDetailsOfComplaintsMadeByEmployeesAndWorkersAsPerP3",
     "INDICATOR_LEVEL", "DetailsOfComplaintsMadeByEmployeesAndWorkersAsPerP3"),
    ("RemarksForAssuranceOfCorporateIdentityNumber", "INDICATOR_REMARKS",
     "CorporateIdentityNumber"),
    ("WhetherCorporateIdentityNumberIsAssuredByAssurer", "INDICATOR_ASSURED",
     "CorporateIdentityNumber"),
    ("NameOfTheExternalAgencyThatUndertookIndependentAssessmentOrEvaluation"
     "OrAssuranceRelatedToWasteManagementExplanatoryTextBlock",
     "LEGACY_EXTERNAL_AGENCY", "ForWasteManagement"),
    ("WhetherConductedByIndependentExternalAgency", None, None),  # EIA/LCA
])
def test_concept_classification(concept, family, key):
    c = ax.classify_concept(concept)
    if family is None:
        assert c is None
    else:
        assert c[0] == family and c[2] == key


def test_indicator_rows_aligned(built):
    f = built["facts"]
    keys = {fam: set(zip(g["filing_id"], g["key"]))
            for fam, g in f.groupby("family")}
    assert keys["INDICATOR_LEVEL"] <= keys["INDICATOR_ASSURED"]
    assert keys["INDICATOR_REMARKS"] <= keys["INDICATOR_ASSURED"]


# ---- providers, duplicates, periods (synthetic filings) ----------------------

FILING = {"filing_id": "F-T", "legal_name": "Test Ltd", "isin": "INE000T",
          "nse_symbol": "TST", "bse_scrip_code": "1", "fy_slot": "2025-26",
          "taxonomy_version": "2026-02-28",
          "reporting_period_start": "2025-04-01",
          "reporting_period_end": "2026-03-31", "xbrl_url": "u",
          "company_id": "C-INE000T", "local_file_path": "x/t.xml"}


def fact(concept, value, context="DCYMain", dims="", start="2025-04-01",
         end="2026-03-31", instant="", fid="F-T#1"):
    fam, kind, key = ax.classify_concept(concept)
    interp, status = ax.interpret(kind, value, "REPORTED")
    row = {"period_start": start, "period_end": end, "instant": instant}
    return {"filing_id": "F-T", "company_name": "Test Ltd",
            "master_isin": "INE000T", "nse_symbol": "TST",
            "bse_scrip_code": "1", "fy_slot": "2025-26",
            "taxonomy_version": "2026-02-28", "family": fam,
            "value_kind": kind, "key": key, "concept": concept,
            "standard_label": "", "value": value, "interpretation": interp,
            "disclosure_status": status,
            "period_role": ax.period_role(row, FILING), "context_id": context,
            "period_start": start, "period_end": end, "instant": instant,
            "dimension_key": dims,
            "provider_member": dims.split("=", 1)[1] if dims else "",
            "duplicate_group": "", "duplicate_class": "", "fact_id": fid,
            "xml_line": "1", "source_file": "t.xml", "source_url": "u"}


ENG = "WhetherTheCompanyHasUndertakenAssessmentOrAssuranceOfTheBRSRCore"


def tables(*facts):
    return ax.build_tables(pd.DataFrame(list(facts)),
                           pd.DataFrame([FILING]))


def test_missing_provider_flagged():
    eng, prov, _, _, issues = tables(fact(ENG, "Yes assurance"))
    assert eng.iloc[0]["engagement_type"] == "ASSURANCE"
    assert eng.iloc[0]["providers_reported"] == 0
    assert "AS01_ENGAGEMENT_WITHOUT_PROVIDER" in set(issues["issue_code"])


def test_provider_recorded_with_evidence():
    dims = "in-capmkt:AssessmentOrAssuranceProviderAxis=[P1]"
    eng, prov, _, _, issues = tables(
        fact(ENG, "Yes assessment"),
        fact("NameOfTheCompanyOrLLPOrFirmOfAssessmentOrAssuranceProvider",
             "Firm & Co", dims=dims, fid="F-T#2"),
        fact("DateOfSigningByAssessorOrAssurer", "2025-12-01", dims=dims,
             fid="F-T#3"))
    p = prov.iloc[0]
    assert p["provider_name"] == "Firm & Co" and p["provider_name_fact_id"] \
        == "F-T#2"
    assert p["provider_registration_id_status"] == "NOT_REPORTED"
    assert "AS08_SIGNED_BEFORE_PERIOD_END" in set(issues["issue_code"])
    assert "AS01_ENGAGEMENT_WITHOUT_PROVIDER" not in set(issues["issue_code"])


def test_conflicting_duplicates_are_ambiguous():
    eng, _, _, _, issues = tables(fact(ENG, "Yes assurance", "C1"),
                                  fact(ENG, "Yes assessment", "C2",
                                       fid="F-T#2"))
    assert eng.iloc[0]["engagement_type"] == "AMBIGUOUS"
    assert "AS06_CONFLICTING_DUPLICATE" in set(issues["issue_code"])


def test_identical_duplicates_are_not_conflicts():
    eng, _, _, _, issues = tables(fact(ENG, "Yes assurance", "C1"),
                                  fact(ENG, "Yes assurance", "C2",
                                       fid="F-T#2"))
    assert eng.iloc[0]["engagement_type"] == "ASSURANCE"
    assert "AS06_CONFLICTING_DUPLICATE" not in set(issues["issue_code"])


@pytest.mark.parametrize("start,end,instant,role", [
    ("2025-04-01", "2026-03-31", "", "CY"),
    ("", "", "2026-03-31", "CY"),
    ("2024-04-01", "2025-03-31", "", "PY"),
    ("", "", "2025-03-31", "PY"),
    ("2023-04-01", "2024-03-31", "", "OTHER"),
])
def test_period_roles(start, end, instant, role):
    row = {"period_start": start, "period_end": end, "instant": instant}
    assert ax.period_role(row, FILING) == role


def test_prior_period_facts_never_used():
    eng, *_ = tables(fact(ENG, "Yes assurance", start="2024-04-01",
                          end="2025-03-31"))
    assert eng.iloc[0]["engagement_type"] == "NOT_REPORTED"


# ---- traceability against the source XBRL -------------------------------------

def test_engagement_facts_match_source_xml(built):
    f = built["facts"]
    filings = built["filings"].set_index("filing_id")
    sample = f[f["family"].isin(["BRSR_CORE_ENGAGEMENT",
                                 "BRSR_CORE_REASONABLE_ASSURANCE_2024",
                                 "PROVIDER"])]
    assert (sample[["fact_id", "xml_line", "context_id", "source_url"]]
            != "").all().all()
    parser = etree.XMLParser(resolve_entities=False, no_network=True,
                             huge_tree=True)
    for filing_id, g in list(sample.groupby("filing_id"))[:25]:
        root = etree.parse(filings.loc[filing_id, "local_file_path"],
                           parser).getroot()
        for r in g.to_dict("records"):
            hits = [el for el in root if isinstance(el.tag, str)
                    and etree.QName(el).localname == r["concept"]
                    and el.get("contextRef") == r["context_id"]]
            assert len(hits) == 1
            assert (hits[0].text or "").strip() == r["value"]
            assert str(hits[0].sourceline) == r["xml_line"]


# ---- crosswalk equivalence is not applied ---------------------------------------

def test_v01_candidate_not_applied(built):
    m = built["mapping"]
    v01 = m[m["source_concept"] ==
            "WhetherTheCompanyHasUndertakenReasonableAssuranceOfTheBRSRCore"]
    assert len(v01) == 1
    assert v01.iloc[0]["approval_status"] == "CANDIDATE_REVIEW"
    assert v01.iloc[0]["review_needed"] and v01.iloc[0]["semantic_difference"]
    widened = m[m["source_concept"].str.contains("AssuranceProvider|Assurer$")]
    assert widened["semantic_difference"].all()


def test_reference_files_unchanged():
    lines = SNAPSHOT.read_text().splitlines()
    before = {l.split(None, 1)[1].lstrip("*").strip(): l.split()[0]
              for l in lines}
    for rel in ("data/processed/taxonomy_versions/"
                "nse_brsr_concept_crosswalk.csv",
                "data/reference/brsr_crosswalk_review_decisions.csv",
                "data/reference/brsr_mtco2e_unit_decisions.csv",
                "data/reference/deferred_issues_register.csv",
                "data/processed/nifty100/parsed/xbrl_facts.csv"):
        assert hashlib.sha256((PROJECT_ROOT / rel).read_bytes()).hexdigest() \
            == before[rel], rel


# ---- integration --------------------------------------------------------------

def test_anomalies_and_workflow_integration():
    from tests.conftest import review_workflow_build
    out = review_workflow_build()           # wf.build(write=False)
    q = out["queue"]
    a = q[q["category"] == "ASSURANCE"]
    assert len(a) > 0 and (a["status"] == "DETECTED").all()
    assert (q["reviewer_decision"] == "").all()


def test_only_review_workflow_outputs_regenerated():
    changed = []
    for line in SNAPSHOT.read_text().splitlines():
        digest, path = line.split(None, 1)
        rel = path.lstrip("*").strip()
        if rel in APPROVED_SWITCH_CHANGES:
            continue
        p = PROJECT_ROOT / rel
        if not p.exists() or hashlib.sha256(p.read_bytes()).hexdigest() \
                != digest:
            changed.append(rel)
    assert all(c.startswith(REGENERATED) for c in changed), changed
