"""Phase 11: anomaly engine and reviewer approval workflow."""

import hashlib
from decimal import Decimal

import pandas as pd
import pytest

from app.core import deferred_issues
from app.normalisation import factor_model as fm
from app.review import anomaly_engine as ae
from app.review import workflow as wf
from tests.conftest import (PROJECT_ROOT, changed_since, factor_model_build,
                            production_values, review_workflow_build)

SNAPSHOT = (PROJECT_ROOT / "data/processed/nifty100/backups/"
            "20261112_pre_phase11/all_files_before.sha")


@pytest.fixture(scope="module")
def built():
    # wf.build(write=False), computed once per session (conftest)
    return review_workflow_build()


def queue_of(built, **match):
    q = built["queue"]
    for k, v in match.items():
        q = q[q[k] == v]
    return q


def log(*entries):
    rows = [dict(zip(wf.LOG_COLUMNS, [str(i + 1), *e]))
            for i, e in enumerate(entries)]
    return pd.DataFrame(rows, columns=wf.LOG_COLUMNS)


GOOD = ("reviewer-a", "2026-11-12T10:00:00", "AUD-1", "ok")


# ---- detection accuracy ------------------------------------------------

def test_categories_present(built):
    expected = {"YOY_CHANGE", "UNIT_SCALE", "CONVERSION_FACTOR",
                "IMPLAUSIBLE_VALUE", "CY_PY_INCONSISTENCY", "SCOPE_EMISSIONS",
                "ENV_INTENSITY", "STAKEHOLDER", "DUPLICATE_FACT",
                "CROSSWALK_TAXONOMY"}
    assert expected <= set(built["anomalies"]["category"])
    assert built["anomalies"]["anomaly_id"].is_unique


@pytest.mark.parametrize("rule,symbol,req", [
    ("A10_STR08", "TATACONSUM", "S12"),       # 10.56 injuries
    ("A10_GOV03", "ADANIENSOL", "G17"),       # pending > filed + opening
    ("A40_FILER_SCALE_ERROR_REVIEW", "POLYCAB", "E01"),
    ("A40_FILER_SCALE_ERROR_REVIEW", "ADANIPORTS", "E08"),
])
def test_known_cases_detected(built, rule, symbol, req):
    a = built["anomalies"]
    assert ((a["detection_rule"] == rule) & (a["nse_symbol"] == symbol)
            & (a["requirement_id"] == req)).any()


def test_known_source_issues_detected(built):
    a = built["anomalies"]
    inconsistent = a[(a["detection_rule"] == "A50_PARSER_DUPLICATE")
                     & (a["severity"] == "ERROR")]
    assert len(inconsistent) == 3                  # BHARTIARTL PY/PPY
    assert (a["detection_rule"] == "A51_CONCEPT_NOT_IN_TAXONOMY").sum() == 4
    xw = a[a["detection_rule"] == "A52_CROSSWALK_CANDIDATE"]
    assert xw["reason"].str.contains("103 requirement values").any()  # E19


def make_rows(values):
    return pd.DataFrame([{
        "master_isin": "INE1", "master_nse_symbol": "X", "company_name": "X",
        "fy_slot": slot, "requirement_id": "E07", "breakdown": "",
        "final_value": v, "final_unit": "GJ", "original_value": v,
        "original_unit": "Non-SI:GJ", "source_file": f"{slot}.xml",
        "context_reference": "C", "source_url": "u", "metric_name": "m"}
        for slot, v in values])


@pytest.mark.parametrize("values,rules", [
    ([("2022-23", "100"), ("2023-24", "100000")],
     {"A01_YOY_10X", "A02_YOY_POWER_OF_TEN"}),
    ([("2022-23", "100"), ("2023-24", "1500")], {"A01_YOY_10X"}),
    ([("2022-23", "100"), ("2023-24", "500")], set()),     # 5x: not flagged
    ([("2022-23", "0"), ("2023-24", "500")], set()),       # undefined ratio
])
def test_yoy_detector(values, rules):
    df = make_rows(values)
    a = ae.Anomalies(df)
    ae.yoy(df, a)
    assert {r["detection_rule"] for r in a.rows} == rules


def test_fingerprint_changes_with_value():
    a1, a2 = ae.Anomalies(make_rows([])), ae.Anomalies(make_rows([]))
    base = {"source_file": "f", "context_reference": "c",
            "requirement_id": "E07", "breakdown": "", "master_isin": "I",
            "fy_slot": "2024-25"}
    a1.add("X", "R", "r", "INFO", "LOW", fact={**base, "original_value": "1"})
    a2.add("X", "R", "r", "INFO", "LOW", fact={**base, "original_value": "2"})
    assert a1.rows[0]["anomaly_id"] != a2.rows[0]["anomaly_id"]


# ---- false positives -----------------------------------------------------

def test_investigation_false_positives_not_queued(built):
    fp = built["false_positives"]
    cleared = fp[fp["basis"].str.startswith("cleared")]
    assert len(cleared) == 35
    a = built["anomalies"]
    scale = a[a["detection_rule"].isin(["A40_FILER_SCALE_ERROR_REVIEW",
                                        "A41_SCALE_EVIDENCE_NOT_STRONG"])]
    keys = set(zip(scale["source_file"], scale["requirement_id"]))
    assert not (set(zip(cleared["source_file"], cleared["requirement_id"]))
                & keys)


def test_reject_marks_false_positive(built):
    q = built["queue"]
    target = q[q["status"] == "DETECTED"].iloc[0]["recommendation_id"]
    q2, v = wf.build_queue(built["anomalies"], production_values("CY"),
                           log((target, "REJECT", *GOOD)))
    assert v["outcome"].tolist() == ["ACCEPTED"]
    assert q2.loc[q2["recommendation_id"] == target, "status"].item() == \
        "REJECTED"


# ---- evidence traceability -------------------------------------------------

def test_evidence_traceability(built):
    q = built["queue"]
    has_fact = q[q["source_file"] != ""]
    assert (has_fact["context_reference"] != "").all()
    assert (q["evidence_files"] != "").all()
    for f in set(q["evidence_files"]):
        for part in f.split(";"):
            # a reference may name a location inside the file
            path = part.strip().split(" section ")[0]
            assert (PROJECT_ROOT / path).exists(), part
    src = q[q["detection_rules"].str.contains("A40_|A43_")]
    assert (src["source_url"] != "").all()


# ---- approval / rejection workflow ----------------------------------------

def states():
    return {"R1": "PENDING_REVIEW", "R2": "DETECTED", "R3": "APPLIED",
            "R4": "DEFERRED"}


HAS = {"R1": True, "R2": False, "R3": True, "R4": True}


@pytest.mark.parametrize("entry,outcome", [
    (("R1", "APPROVE", *GOOD), "ACCEPTED"),
    (("R2", "APPROVE", *GOOD), "REFUSED_TRANSITION_DETECTED_TO_APPROVE"),
    (("R2", "REJECT", *GOOD), "ACCEPTED"),
    (("R3", "REJECT", *GOOD), "REFUSED_ALREADY_APPLIED"),
    (("R4", "APPROVE", *GOOD), "ACCEPTED"),
    (("R9", "APPROVE", *GOOD), "REFUSED_UNKNOWN_RECOMMENDATION"),
    (("R1", "MAYBE", *GOOD), "REFUSED_INVALID_DECISION"),
    (("R1", "APPROVE", "", *GOOD[1:]), "REFUSED_NO_REVIEWER"),
    (("R1", "APPROVE", "a", "yesterday", "AUD", ""),
     "REFUSED_INVALID_TIMESTAMP"),
    (("R1", "APPROVE", "a", "2026-11-12", "", ""),
     "REFUSED_NO_AUDIT_REFERENCE"),
])
def test_log_validation(entry, outcome):
    s = states()
    v, _ = wf.apply_log(log(entry), s, HAS)
    assert v["outcome"].iloc[0] == outcome
    if outcome != "ACCEPTED":
        assert s == states()                  # refused changes nothing


def test_latest_accepted_decision_wins_and_history_kept():
    s = states()
    v, acc = wf.apply_log(log(("R1", "DEFER", *GOOD),
                              ("R1", "APPROVE", *GOOD),
                              ("R1", "REJECT", *GOOD)), s, HAS)
    assert v["outcome"].tolist() == ["ACCEPTED", "ACCEPTED",
                                     "REFUSED_TRANSITION_APPROVED_TO_REJECT"]
    assert s["R1"] == "APPROVED" and acc["R1"]["decision"] == "APPROVE"


# ---- prevention of unauthorised corrections ----------------------------------

def test_nothing_approved_without_a_decision(built):
    q = built["queue"]
    assert (q["reviewer_decision"] == "").all()
    approved = q[q["status"].isin(["APPROVED", "APPLIED"])]
    # only the 55 MtCO2e records the owner approved before this phase
    assert len(approved) == 55
    assert approved["detection_rules"].eq("A43_AMBIGUOUS_GHG_UNIT").all()
    fse = q[q["recommendation_id"].str.startswith("FSE-")]
    assert len(fse) == 39 and (fse["status"] == "PENDING_REVIEW").all()


def test_engine_unchanged_with_empty_log():
    out = factor_model_build()            # fm.build(write=False)
    assert len(out["differences"]) == 0
    assert int(out["invariants"]["violations"].sum()) == 0


def test_approved_log_entry_is_the_only_way_to_apply(tmp_path, monkeypatch):
    path = tmp_path / "review_decisions.csv"
    log(("FSE-001", "APPROVE", *GOOD),
        ("FSE-019", "APPROVE", *GOOD),          # BLOCK_PARTIAL
        ("FSE-002", "APPROVE", "", *GOOD[1:])   # no reviewer: refused
        ).to_csv(path, index=False)
    monkeypatch.setattr(wf, "DECISIONS_LOG", path)
    out = fm.build(write=False)
    diff = out["differences"]
    assert len(diff) == 1
    row = out["results"]["CY"]
    applied = row[row["final_status"] == "CONVERTED_APPROVED_SCALE_CORRECTION"]
    assert applied["scale_decision_id"].tolist() == ["FSE-001"]
    assert Decimal(applied["final_value"].item()) == Decimal("0.0000122")
    poly = row[row["scale_decision_id"] == "FSE-019"]
    assert poly["scale_decision_outcome"].item() == \
        "REFUSED_BLOCK_PARTIAL_REQUIRES_BLOCK_REVIEW"
    assert row.loc[row["scale_decision_id"] == "FSE-002",
                   "scale_decision_outcome"].item() == "PENDING_NOT_APPLIED"


def test_source_file_decision_cannot_be_overridden():
    fse = pd.read_csv(fm.SCALE_DECISIONS, dtype=str, keep_default_na=False)
    fse.loc[fse["approval_id"] == "FSE-001", "approval_decision"] = "REJECT"
    s = {r: "PENDING_REVIEW" for r in fse["approval_id"]}
    v, _ = wf.apply_log(log(("FSE-001", "APPROVE", *GOOD)), s,
                        {k: True for k in s}, {"FSE-001": "REJECT"})
    assert v["outcome"].iloc[0].startswith("REFUSED_DECIDED_IN_SOURCE_FILE")


def test_approved_but_not_in_production_is_not_applied(built):
    q, _ = wf.build_queue(built["anomalies"], production_values("CY"),
                          log(("FSE-001", "APPROVE", *GOOD)))
    row = q[q["recommendation_id"] == "FSE-001"].iloc[0]
    assert row["status"] == "APPROVED"
    assert "not yet in production" in row["apply_note"]


# ---- deferred issues and pending decisions preserved ----------------------------

def test_deferred_issues_integrated_not_resolved(built):
    q = built["queue"]
    dq = q[q["recommendation_id"].str.startswith("DQ-")]
    assert len(dq) == 39 and (dq["status"] == "DEFERRED").all()
    reg = deferred_issues.load()
    assert (reg["status"] == deferred_issues.OPEN).all()


def test_pending_decisions_preserved(built):
    q = built["queue"]
    mt = q[q["detection_rules"] == "A43_AMBIGUOUS_GHG_UNIT"]
    assert mt["source_decision"].value_counts().to_dict() == {
        "PENDING_REVIEW": 474, "APPROVED_METRIC_TONNE": 53,
        "UNDETERMINED_MANUAL_REVIEW": 35,
        "FLAGGED_RESTATEMENT_DIFFERS": 20, "APPROVED_MEGATONNE": 2}
    xw = q[q["recommendation_id"].str.startswith("XW-")]
    assert len(xw) == 7 and (xw["status"] == "PENDING_REVIEW").all()


# ---- integrity -------------------------------------------------------------------

def test_decision_log_is_empty_template():
    df = wf.read_log()
    assert list(df.columns) == wf.LOG_COLUMNS and len(df) == 0


def test_no_existing_file_changed():
    assert changed_since(SNAPSHOT) == []
