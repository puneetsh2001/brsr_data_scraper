"""
Owner decisions of 2026-11-12:
  - CANDIDATE_REVIEW crosswalk rows are not counted as REPORTED
  - OtherStakeHolderAxis added to G16/G17
"""

import hashlib
from pathlib import Path

import pandas as pd
import pytest

from tests.conftest import PROJECT_ROOT, read_csv

N = PROJECT_ROOT / "data" / "processed" / "nifty100"
BACKUP = N / "backups" / "20261112_pre_g16_g17_candidate_coverage"
REVIEW = N / "review" / "g16_g17_other_stakeholder"


@pytest.fixture(scope="module")
def coverage():
    return read_csv(N / "brsr_requirement_coverage.csv")


@pytest.fixture(scope="module")
def values():
    return read_csv(N / "brsr_requirement_values.csv")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def test_candidate_only_cells_never_reported(coverage):
    found = coverage["values_found"].astype(int)
    cand = coverage["values_from_candidate_mapping"].astype(int)
    only_candidate = (found > 0) & (found == cand)
    assert (coverage.loc[only_candidate, "status"]
            == "REPORTED_PENDING_CROSSWALK_REVIEW").all()
    assert not coverage.loc[coverage["status"].isin(
        ["REPORTED", "REPORTED_ZERO"]) & only_candidate].any().any()


def test_candidate_rows_kept_as_evidence(values, coverage):
    cand = values[values["crosswalk_status"] == "CANDIDATE_REVIEW"]
    assert len(cand) == 230
    assert set(cand["requirement_id"]) == {"E19", "V01"}
    assert (coverage["status"]
            == "REPORTED_PENDING_CROSSWALK_REVIEW").sum() == 230


def test_e19_v01_still_pending_in_crosswalk():
    cw = read_csv(PROJECT_ROOT / "data/processed/taxonomy_versions/"
                  "nse_brsr_concept_crosswalk.csv")
    rows = cw[cw["source_concept"].isin([
        "TotalScope1AndScope2EmissionsPerRupeeOfTurnover",
        "WhetherTheCompanyHasUndertakenReasonableAssuranceOfTheBRSRCore"])]
    assert len(rows) == 3
    assert (rows["approval_status"] == "CANDIDATE_REVIEW").all()


def test_other_stakeholder_rows_extracted(values):
    other = values[values["dimensions"].str.contains("OtherStakeHolderAxis")]
    assert other.groupby("requirement_id").size().to_dict() == {
        "G16": 219, "G17": 219}


def test_new_facts_match_source_xbrl():
    ev = read_csv(REVIEW / "g16_g17_new_facts_with_evidence.csv")
    assert len(ev) == 876
    assert (ev["all_checks_pass"] == "True").all()
    assert (ev["stakeholder_group_name_in_xbrl"] != "").all()


@pytest.mark.parametrize("relative", [
    "data/reference/brsr_mtco2e_unit_decisions.csv",
    "data/reference/brsr_crosswalk_review_decisions.csv",
    "data/processed/taxonomy_versions/nse_brsr_concept_crosswalk.csv",
])
def test_protected_files_unchanged(relative):
    lines = (BACKUP / "all_files_before.sha").read_text().splitlines()
    before = {l.split(None, 1)[1].lstrip("*").strip(): l.split()[0]
              for l in lines}
    assert sha(PROJECT_ROOT / relative) == before[relative]


def test_only_expected_files_changed():
    audit = read_csv(REVIEW / "file_change_audit.csv")
    changed = set(audit.loc[audit["unchanged"] == "False", "file"])
    assert changed <= {
        "data/processed/nifty100/brsr_requirement_coverage.csv",
        "data/processed/nifty100/brsr_requirement_summary.csv",
        "data/processed/nifty100/brsr_requirement_values.csv",
        "data/processed/nifty100/brsr_requirement_values_py.csv",
        "data/processed/nifty100/normalised/"
        "brsr_normalisation_before_after.csv",
        "data/processed/nifty100/normalised/brsr_normalisation_summary.csv",
        "data/processed/nifty100/normalised/"
        "brsr_normalisation_validation_issues.csv",
        "data/processed/nifty100/normalised/"
        "brsr_requirement_values_normalised.csv",
        "data/processed/nifty100/normalised/"
        "brsr_requirement_values_py_normalised.csv",
        "data/reference/brsr_requirement_concept_map.csv",
    }
