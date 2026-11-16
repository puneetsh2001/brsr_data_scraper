"""Phase 8: normalisation engine with the explicit factor model."""

import hashlib
from decimal import Decimal

import pandas as pd
import pytest

from app.normalisation import factor_model as fm
from tests.conftest import PROJECT_ROOT, changed_since, factor_model_build

SNAPSHOT = (PROJECT_ROOT / "data/processed/nifty100/backups/"
            "20261112_pre_phase8/all_files_before.sha")

TJ_PER_INR = {"numerator": ["Non-SI:TJ"], "denominator": ["iso4217:INR"]}
JOULE = {"numerator": ["Non-SI:J"], "denominator": []}
MTCO2E = {"numerator": ["Non-SI:MtCO2e"], "denominator": []}


@pytest.fixture(scope="module")
def built():
    # fm.build(write=False), computed once per session (conftest)
    return factor_model_build()


@pytest.fixture(scope="module")
def scales():
    return {d["approval_id"]: d for d in fm.load_scale_decisions().values()}


def approve(d, **changes):
    return {**d, "approval_decision": "APPROVE", "approved_by": "test",
            "approval_date": "2026-11-12", **changes}


def row(value, unit_ref="U", data_type="dtr-types:energyItemType"):
    return {"value": value, "unit_reference": unit_ref,
            "data_type": data_type}


# ---- reproduction of the production output ---------------------------

def test_reproduces_production_output_exactly(built):
    assert len(built["differences"]) == 0


def test_no_invariant_violations(built):
    assert int(built["invariants"]["violations"].sum()) == 0


def test_decision_outcomes_unchanged(built):
    d = built["decisions"]
    mt = d[d["decision_source"] == "brsr_mtco2e_unit_decisions.csv"]
    assert mt["unit_assumption_outcome"].value_counts().to_dict() == {
        "NOT_APPLIED_PENDING_REVIEW": 474, "APPROVED_APPLIED": 55,
        "NOT_APPLIED_UNDETERMINED_MANUAL_REVIEW": 35,
        "NOT_APPLIED_FLAGGED_RESTATEMENT_DIFFERS": 20}
    fse = d[d["decision_source"]
            == "strong_recommendations_final_review.csv"]
    assert len(fse) == 39
    assert (fse["scale_decision_outcome"] == "PENDING_NOT_APPLIED").all()


def test_pending_scale_records_keep_filed_value(built):
    cy = built["results"]["CY"]
    pend = cy[cy["scale_decision_id"] != ""]
    assert (pend["multiplier_basis"] != "APPROVED_SCALE_CORRECTION").all()
    assert pend["pending_review"].str.contains("PENDING_NOT_APPLIED").all()


def test_deferred_issues_linked(built):
    cy = built["results"]["CY"]
    linked = cy[cy["deferred_issue_ids"] != ""]
    assert set(linked["requirement_id"]) <= {"G16", "G17"}
    assert len(linked) > 0


# ---- factor model rules (synthetic approvals, real records) -----------

def test_approved_scale_uses_original_multiplier_not_legacy(scales):
    d = approve(scales["FSE-001"])      # ADANIPORTS E08, TJ per crore INR
    out = fm.factor_row(row(d["original_value"]), TJ_PER_INR,
                        "energy_per_inr", scale=d)
    assert out["scale_decision_outcome"] == "APPROVED_APPLIED"
    assert out["multiplier_basis"] == "APPROVED_SCALE_CORRECTION"
    assert Decimal(out["final_value"]) == Decimal("0.0000122")
    legacy = Decimal(d["original_value"]) * Decimal(
        d["legacy_factor_on_current_normalised_value_only"])
    assert Decimal(out["final_value"]) != legacy


def test_joule_tag_reinterpretation_keeps_number(scales):
    # simulate a completed block review so the J -> GJ path is exercised
    d = approve(scales["FSE-019"],
                block_dependency="BLOCK_COMPLETE_WITHIN_STRONG_SET")
    out = fm.factor_row(row(d["original_value"]), JOULE, "energy", scale=d)
    assert out["actual_numeric_multiplier_on_original"] == "1"
    assert out["final_value"] == d["original_value"]
    assert out["final_unit"] == "GJ"
    assert out["unit_reinterpretation_factor_not_applied"] == "1e9"


def test_partial_block_is_never_applied(scales):
    d = approve(scales["FSE-019"])           # BLOCK_PARTIAL
    out = fm.factor_row(row(d["original_value"]), JOULE, "energy", scale=d)
    assert out["scale_decision_outcome"] == \
        "REFUSED_BLOCK_PARTIAL_REQUIRES_BLOCK_REVIEW"
    assert out["multiplier_basis"] == "RULE_UNIT_CONVERSION"
    # rule conversion of the filed J value, not the correction
    assert Decimal(out["final_value"]) == Decimal(d["original_value"]) * \
        Decimal("1e-9")


@pytest.mark.parametrize("changes,outcome", [
    ({"approved_by": ""}, "REFUSED_INCOMPLETE_APPROVAL"),
    ({"approval_date": ""}, "REFUSED_INCOMPLETE_APPROVAL"),
    ({"approval_decision": "REJECT"}, "REJECTED_BY_OWNER"),
    ({"approval_decision": ""}, "PENDING_NOT_APPLIED"),
    ({"original_value": "999"}, "REFUSED_STALE_DECISION"),
    ({"original_unit": "Non-SI:GJ / iso4217:INR"}, "REFUSED_STALE_DECISION"),
    ({"proposed_corrected_value": "1"}, "REFUSED_RECORD_INCONSISTENT"),
])
def test_scale_decision_guards(scales, changes, outcome):
    d = approve(scales["FSE-001"], **changes)
    out = fm.factor_row(row("0.122"), TJ_PER_INR, "energy_per_inr", scale=d)
    assert out["scale_decision_outcome"] == outcome
    assert out["multiplier_basis"] != "APPROVED_SCALE_CORRECTION"


def test_unit_assumption_rules():
    base = {"decision": "APPROVED_METRIC_TONNE", "approved_factor": "1",
            "original_value": "100", "approval_evidence": "x"}
    ok = fm.factor_row(row("100", data_type="dtr-types:ghgEmissionsItemType"),
                       MTCO2E, "tco2e", assumption=base)
    assert ok["final_value"] == "100" and ok["final_unit"] == "tCO2e"
    pending = fm.factor_row(
        row("100", data_type="dtr-types:ghgEmissionsItemType"), MTCO2E,
        "tco2e", assumption={**base, "decision": "PENDING_REVIEW"})
    assert pending["final_value"] == ""
    assert "UNIT_ASSUMPTION:PENDING_REVIEW" in pending["pending_review"]
    stale = fm.factor_row(
        row("101", data_type="dtr-types:ghgEmissionsItemType"), MTCO2E,
        "tco2e", assumption=base)
    assert stale["unit_assumption_outcome"] == "REFUSED_STALE_DECISION"
    assert stale["final_value"] == ""


def test_missing_or_ambiguous_unit_not_converted():
    out = fm.factor_row(row("5", unit_ref=""), None, "energy")
    assert out["final_value"] == ""
    out = fm.factor_row(row("5"), {"numerator": ["xbrli:pure"],
                                   "denominator": []}, "energy")
    assert out["final_status"] == "NOT_CONVERTED_UNIT_AMBIGUOUS"
    assert out["final_value"] == ""


# ---- safety ------------------------------------------------------------

def test_no_existing_file_changed():
    # only the owner-approved production switch may differ (verified in
    # tests/test_production_switch.py)
    assert changed_since(SNAPSHOT) == []
