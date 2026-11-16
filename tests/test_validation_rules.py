"""Phase 9: validation rules engine."""

import hashlib

import pandas as pd
import pytest

from app.validation import rules_engine as ve
from tests.conftest import PROJECT_ROOT, changed_since

SNAPSHOT = (PROJECT_ROOT / "data/processed/nifty100/backups/"
            "20261112_pre_phase9/all_files_before.sha")
CATEGORIES = {"STRUCTURAL", "ENVIRONMENTAL", "SOCIAL", "GOVERNANCE",
              "INTENSITY", "RESTATEMENT"}


@pytest.fixture(scope="module")
def built():
    return ve.build(write=False)


def results():
    return ve.Results({})


KEY = {"company_name": "X", "master_isin": "INE000", "master_nse_symbol": "X",
       "fy_slot": "2024-25", "source_file": "f.xml"}


# ---- registry and legacy reconciliation ---------------------------------

def test_registry_covers_all_roadmap_categories(built):
    reg = built["registry"]
    assert reg["rule_id"].is_unique
    assert set(reg["category"]) == CATEGORIES


def test_legacy_checks_reproduced_exactly(built):
    r = built["recon"]
    assert len(r) > 0
    assert (r["only_existing"] == 0).all() and (r["only_engine"] == 0).all()


def test_every_new_rule_was_evaluated(built):
    res = built["results"]
    new_rules = built["registry"].loc[built["registry"]["legacy_check"] == "",
                                      "rule_id"]
    for rule in new_rules:
        assert (res["rule_id"] == rule).any(), rule


def test_fail_rows_carry_severity(built):
    res = built["results"]
    assert (res.loc[res["status"] == "FAIL", "severity"] != "").all()
    assert (res.loc[res["status"] != "FAIL", "severity"] == "").all()


# ---- deferred decisions are not acted on, only linked -------------------

def test_other_stakeholder_restatement_notes_tagged_deferred(built):
    res = built["results"]
    rst = res[(res["rule_id"] == "RST01")
              & res["breakdown"].str.contains("OtherStakeHolderAxis")]
    assert len(rst) == 28
    assert rst["detail"].str.contains("DQ-039").all()


def test_deferred_issue_ids_attached(built):
    res = built["results"]
    adani = res[(res["rule_id"] == "GOV03")
                & (res["master_nse_symbol"] == "ADANIENSOL")
                & (res["status"] == "FAIL")]
    assert adani["deferred_issue_ids"].str.contains("DQ-010").any()


# ---- rule semantics -------------------------------------------------------

def test_missing_values_are_not_evaluated_never_pass():
    from decimal import Decimal as D
    r = results()
    ve.check_inequality(r, "ENV02", KEY, "E05", ["E01", "E03"],
                        {"E05": D(10), "E01": D(4)}, "x")
    assert r.rows[-1]["status"] == "NOT_EVALUATED"
    ve.check_inequality(r, "ENV02", KEY, "E05", ["E01", "E03"],
                        {"E05": D(10), "E01": D(4), "E03": D(5)}, "x")
    assert r.rows[-1]["status"] == "PASS"
    ve.check_inequality(r, "ENV02", KEY, "E05", ["E01", "E03"],
                        {"E05": D(8), "E01": D(4), "E03": D(5)}, "x")
    assert r.rows[-1]["status"] == "FAIL"
    # relative tolerance, no absolute floor
    ve.check_inequality(r, "ENV02", KEY, "E05", ["E01", "E03"],
                        {"E05": D("8.995"), "E01": D(4), "E03": D(5)}, "x")
    assert r.rows[-1]["status"] == "PASS"


def row(req, b, value):
    return {**KEY, "requirement_id": req, "breakdown": b,
            "final_value": value}


def test_pending_rule_same_report():
    cy = {("G17", "g"): row("G17", "g", "74"), ("G16", "g"): row("G16", "g",
                                                                 "14")}
    py = {("G17", "g"): row("G17", "g", "40")}
    r = results()
    ve.pending_rule(r, "GOV03", cy, py, "G17", "G16", KEY, {})
    assert r.rows[-1]["status"] == "FAIL"
    r = results()
    ve.pending_rule(r, "GOV03", cy, {}, "G17", "G16", KEY, {})
    assert r.rows[-1]["status"] == "NOT_EVALUATED"


def test_gender_sum_and_shares():
    from decimal import Decimal as D
    rows = {(
        "S01", f"X=PermanentEmployeesMember | GenderAxis={g}"): row(
            "S01", "", v) for g, v in (("MaleMember", "6"),
                                      ("FemaleMember", "3"),
                                      ("OtherGenderMember", "1"),
                                      ("GenderMember", "10"))}
    r = results()
    ve.social({("INE000", "2024-25"): rows}, r)
    soc01 = [x for x in r.rows if x["rule_id"] == "SOC01"]
    assert soc01 and soc01[0]["status"] == "PASS"
    r = results()
    ve.share_check(r, "GOV02", KEY, {"G03": D("30"), "G02": D(3),
                                     "G01": D(10)}, "G03", "G02", "G01")
    assert r.rows[-1]["status"] == "PASS"
    ve.share_check(r, "GOV02", KEY, {"G03": D("0"), "G02": D(3),
                                     "G01": D(10)}, "G03", "G02", "G01")
    assert r.rows[-1]["status"] == "FAIL"
    ve.share_check(r, "GOV02", KEY, {"G03": D("30"), "G02": D(3),
                                     "G01": D(0)}, "G03", "G02", "G01")
    assert r.rows[-1]["status"] == "NOT_EVALUATED"


# ---- safety ----------------------------------------------------------------

def test_no_existing_file_changed():
    # only the owner-approved production switch may differ (verified in
    # tests/test_production_switch.py)
    assert changed_since(SNAPSHOT) == []
