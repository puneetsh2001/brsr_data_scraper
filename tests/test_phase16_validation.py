"""
Phase 16: validation rules and block dependencies on controlled fixtures
(master prompt section 18): total/component reconciliation, intensity
calculations, restatement differences, zero and null values, inconsistent
units, missing data and block dependencies.

The rule functions of app.validation.rules_engine, the legacy checks they
reproduce, and app.validation.block_validation.build() run on small
synthetic frames; no project file is read or written by the rules here
(block validation reads its inputs from tmp_path).
"""

from decimal import Decimal

import pandas as pd
import pytest

from app.validation import block_validation as bv
from app.validation import rules_engine as re_
from app.validators import brsr_normalisation_validator as legacy

ISIN, SLOT = "INE000A01001", "2024-25"


def value(req, final, unit="GJ", breakdown="", status=None, **extra):
    row = {"company_name": "Alpha", "master_isin": ISIN,
           "master_nse_symbol": "ALPHA", "fy_slot": SLOT,
           "requirement_id": req, "breakdown": breakdown,
           "metric_name": f"M_{req}", "original_value": final,
           "original_unit": unit, "final_value": final,
           "final_unit": unit if final != "" else "",
           "final_status": status or ("NO_CONVERSION_NEEDED" if final != ""
                                      else "NOT_CONVERTED_UNIT_MISSING"),
           "source_file": "A_2024.xml", "context_reference": "CY",
           "source_url": "https://x/A_2024.xml", "deferred_issue_ids": "",
           "pending_review": "", "period_start": "2024-04-01",
           "period_end": "2025-03-31", "instant_date": "",
           "actual_numeric_multiplier_on_original": "1" if final != ""
           else "", "factor_formula": f"{final} x 1" if final != "" else "",
           "unit_assumption_decision": "",
           "proposed_value_if_assumption_approved": ""}
    row.update(extra)
    return row


def run(rows, fn):
    cy = pd.DataFrame(rows)
    results = re_.Results({})
    if fn is re_.intensity_units:
        fn(cy, results)
    elif fn is re_.governance_and_pending:
        fn(re_.index(cy), {}, results, {})
    else:
        fn(re_.index(cy), results)
    return pd.DataFrame(results.rows)


def outcome(res, rule):
    r = res[res["rule_id"] == rule]
    assert len(r) == 1, r
    return r.iloc[0]


# ---- total / component reconciliation ----------------------------------------

def test_component_inequality_pass_and_fail():
    ok = run([value("E05", "100"), value("E01", "60"), value("E03", "40")],
             re_.environmental)
    assert outcome(ok, "ENV02")["status"] == "PASS"
    bad = run([value("E05", "90"), value("E01", "60"), value("E03", "40")],
              re_.environmental)
    row = outcome(bad, "ENV02")
    assert row["status"] == "FAIL" and row["severity"] == "WARNING"
    assert row["observed"] == "90" and row["expected"] == ">= 100"


def test_component_tolerance_is_relative_one_percent():
    near = run([value("E05", "99.5"), value("E01", "60"), value("E03", "40")],
               re_.environmental)
    assert outcome(near, "ENV02")["status"] == "PASS"     # 0.5 % below
    far = run([value("E05", "98"), value("E01", "60"), value("E03", "40")],
              re_.environmental)
    assert outcome(far, "ENV02")["status"] == "FAIL"      # 2 % below


def test_legacy_sum_check_c07():
    cy = re_.legacy_frame(pd.DataFrame([
        value("E07", "100"), value("E05", "30"), value("E06", "50")]))
    issues = legacy.Issues()
    legacy.intensity_and_sum_checks(cy, issues)
    c07 = [i for i in issues.rows if i["check"] == "C07_COMPONENT_SUMS"]
    assert len(c07) == 1 and "total 100 vs sum" in c07[0]["detail"]


def test_gender_total_reconciliation():
    rows = [value("S01", v, unit="pure", breakdown=f"GenderAxis={m}")
            for m, v in (("GenderMember", "10"), ("MaleMember", "6"),
                         ("FemaleMember", "3"), ("OtherGenderMember", "1"))]
    assert outcome(run(rows, re_.social), "SOC01")["status"] == "PASS"
    rows[0]["final_value"] = "12"
    assert outcome(run(rows, re_.social), "SOC01")["status"] == "FAIL"


def test_board_rule_has_zero_tolerance():
    res = run([value("G01", "10", "pure"), value("G02", "11", "pure")],
              re_.governance_and_pending)
    gov01 = outcome(res, "GOV01")
    assert gov01["status"] == "FAIL" and gov01["severity"] == "ERROR"


# ---- intensity calculations ---------------------------------------------------

def test_share_recalculation():
    rows = [value("G01", "10", "pure"), value("G02", "3", "pure"),
            value("G03", "30", "%")]
    assert outcome(run(rows, re_.governance_and_pending),
                   "GOV02")["status"] == "PASS"
    rows[2]["final_value"] = "31"                    # 1 pp off (> 0.5)
    assert outcome(run(rows, re_.governance_and_pending),
                   "GOV02")["status"] == "FAIL"


def test_share_with_zero_denominator_is_not_evaluated():
    rows = [value("G01", "0", "pure"), value("G02", "0", "pure"),
            value("G03", "0", "%")]
    assert outcome(run(rows, re_.governance_and_pending),
                   "GOV02")["status"] == "NOT_EVALUATED"


def test_legacy_intensity_recompute_c06():
    good = [value("A06", "1000", "INR"), value("E07", "50"),
            value("E08", "0.05", "GJ/INR")]
    issues = legacy.Issues()
    legacy.intensity_and_sum_checks(re_.legacy_frame(pd.DataFrame(good)),
                                    issues)
    assert not [i for i in issues.rows if i["check"].startswith("C06")]
    bad = [dict(r) for r in good]
    bad[2]["final_value"] = "50"                     # 1000x off
    issues = legacy.Issues()
    legacy.intensity_and_sum_checks(re_.legacy_frame(pd.DataFrame(bad)),
                                    issues)
    c06 = [i for i in issues.rows if i["check"].startswith("C06")]
    assert len(c06) == 1 and "ratio 1.0E+3" in c06[0]["detail"]
    assert "looks like factor 1e3" in c06[0]["detail"]


def test_implied_emission_factor_range():
    rows = [value("E17", "40", "tCO2e"), value("E18", "10", "tCO2e"),
            value("E07", "1000")]
    assert outcome(run(rows, re_.environmental), "ENV07")["status"] == "PASS"
    rows[2]["final_value"] = "1"                     # 50 tCO2e/GJ
    assert outcome(run(rows, re_.environmental), "ENV07")["status"] == "FAIL"


# ---- restatement differences --------------------------------------------------

def restatement(mine, theirs, unit="GJ"):
    cy = re_.legacy_frame(pd.DataFrame([value("E07", mine, unit,
                                              status="CONVERTED")]))
    py = re_.legacy_frame(pd.DataFrame([value(
        "E07", theirs, unit, status="CONVERTED", source_file="A_2025.xml",
        context_reference="PY")]))
    issues = legacy.Issues()
    legacy.py_crosscheck(cy, py, issues)
    return [i for i in issues.rows if i["check"] == "C08_NEXT_REPORT_PY"]


def test_restatement_within_one_percent_is_not_reported():
    assert restatement("100", "100.5") == []


def test_genuine_restatement_is_info():
    found = restatement("100", "120")
    assert len(found) == 1 and found[0]["severity"] == "INFO"
    assert "restated in the following report" in found[0]["detail"]


def test_scale_like_restatement_is_error_with_hint():
    found = restatement("100000", "100")
    assert len(found) == 1 and found[0]["severity"] == "ERROR"
    assert "scale mismatch" in found[0]["detail"]


# ---- zero and null values, missing data --------------------------------------

def test_missing_component_is_not_evaluated_never_zero():
    res = run([value("E05", "100"), value("E01", "60")], re_.environmental)
    row = outcome(res, "ENV02")
    assert row["status"] == "NOT_EVALUATED" and "E03" in row["detail"]


def test_blank_value_is_missing_not_zero():
    res = run([value("E05", "100"), value("E01", "60"), value("E03", "")],
              re_.environmental)
    assert outcome(res, "ENV02")["status"] == "NOT_EVALUATED"


def test_reported_zero_is_a_real_value():
    res = run([value("E05", "0"), value("E01", "0"), value("E03", "0")],
              re_.environmental)
    assert outcome(res, "ENV02")["status"] == "PASS"
    res = run([value("E05", "0"), value("E01", "5"), value("E03", "0")],
              re_.environmental)
    assert outcome(res, "ENV02")["status"] == "FAIL"


def test_emission_factor_with_zero_energy_is_not_evaluated():
    res = run([value("E17", "1", "tCO2e"), value("E18", "1", "tCO2e"),
               value("E07", "0")], re_.environmental)
    assert outcome(res, "ENV07")["status"] == "NOT_EVALUATED"


def test_pending_rule_needs_same_report_opening_balance():
    cy = pd.DataFrame([value("G16", "5", "pure", breakdown="B=X"),
                       value("G17", "3", "pure", breakdown="B=X")])
    results = re_.Results({})
    re_.governance_and_pending(re_.index(cy), {}, results, {})
    res = pd.DataFrame(results.rows)
    assert outcome(res, "GOV03")["status"] == "NOT_EVALUATED"
    py = {"A_2024.xml": {("G17", "B=X"): value("G17", "1", "pure",
                                               breakdown="B=X")}}
    results = re_.Results({})
    re_.governance_and_pending(re_.index(cy), py, results, {})
    assert outcome(pd.DataFrame(results.rows), "GOV03")["status"] == "PASS"


def test_count_metric_must_be_non_negative_integer():
    cy = pd.DataFrame([value("S01", "10.5", "pure"),
                       value("G01", "-1", "pure"), value("G02", "", "pure"),
                       value("S02", "4", "pure")])
    results = re_.Results({})
    re_.structural(cy, results, {"A_2024.xml"})
    res = pd.DataFrame(results.rows)
    s = res[res["rule_id"] == "STR08"].set_index("requirement_id")["status"]
    assert s.to_dict() == {"S01": "FAIL", "G01": "FAIL",
                           "G02": "NOT_EVALUATED", "S02": "PASS"}


def test_untraceable_value_fails_structural_rule():
    cy = pd.DataFrame([value("E01", "1", source_url=""),
                       value("E02", "1", source_file="other.xml")])
    results = re_.Results({})
    re_.structural(cy, results, {"A_2024.xml"})
    s = [r["status"] for r in results.rows if r["rule_id"] == "STR07"]
    assert s == ["FAIL", "FAIL"]


# ---- inconsistent units -------------------------------------------------------

def test_intensity_without_per_inr_unit_fails():
    res = run([value("E08", "0.1", "GJ/INR"), value("E16", "0.1", "kL"),
               value("E19", "")], re_.intensity_units)
    s = res.set_index("requirement_id")["status"].to_dict()
    assert s == {"E08": "PASS", "E16": "FAIL", "E19": "NOT_EVALUATED"}


def test_mixed_normalised_units_per_requirement_c03():
    cy = re_.legacy_frame(pd.DataFrame([
        value("E07", "1", "GJ"),
        value("E07", "1", "MJ", master_isin="INE000B01002")]))
    issues = legacy.Issues()
    legacy.row_checks(cy, issues)
    assert [i["check"] for i in issues.rows] == ["C03_UNIT_UNIFORMITY"]


def test_recompute_mismatch_c01():
    cy = re_.legacy_frame(pd.DataFrame([value(
        "E07", "2500", status="CONVERTED", original_value="2.5",
        actual_numeric_multiplier_on_original="100",
        factor_formula="2.5 x 100")]))
    issues = legacy.Issues()
    legacy.row_checks(cy, issues)
    assert [i["check"] for i in issues.rows] == ["C01_RECOMPUTE"]


# ---- block dependencies --------------------------------------------------------

def coverage(req, status="REPORTED"):
    return {"isin": ISIN, "fy_slot": SLOT, "requirement_id": req,
            "status": status, "nse_symbol": "ALPHA", "company_name": "Alpha"}


def rule(rule_id, req, status):
    return {"rule_id": rule_id, "master_isin": ISIN, "fy_slot": SLOT,
            "requirement_id": req, "status": status, "severity":
            "WARNING" if status == "FAIL" else "", "breakdown": ""}


@pytest.fixture()
def blocks(tmp_path, monkeypatch):
    def build(values, cov, results):
        paths = {}
        for name, rows, cols in (
                ("results", results, ["rule_id", "master_isin", "fy_slot",
                                      "requirement_id", "status", "severity",
                                      "breakdown"]),
                ("coverage", cov, ["isin", "fy_slot", "requirement_id",
                                   "status", "nse_symbol", "company_name"]),
                ("fse", [], ["source_file", "master_nse_symbol", "fy_slot",
                             "requirement_id", "breakdown", "approval_id",
                             "block_dependency", "recommended_decision",
                             "proposed_corrected_value",
                             "proposed_corrected_unit"]),
                ("evidence", [], ["source_file", "requirement_id",
                                  "breakdown", "classification"])):
            p = tmp_path / f"{name}.csv"
            pd.DataFrame(rows, columns=cols).to_csv(p, index=False)
            paths[name] = p
        monkeypatch.setattr(bv, "read_production",
                            lambda _p: pd.DataFrame(
                                values, columns=list(value("G01", "1"))))
        monkeypatch.setattr(bv, "RESULTS", paths["results"])
        monkeypatch.setattr(bv, "COVERAGE", paths["coverage"])
        monkeypatch.setattr(bv, "FSE", paths["fse"])
        monkeypatch.setattr(bv, "FSE_EVIDENCE", paths["evidence"])
        out = bv.build(write=False)
        return out["blocks"].set_index("block")
    return build


BOARD = ["G01", "G02", "G03"]


def board(**overrides):
    rows = {r: value(r, v, "pure") for r, v in
            (("G01", "10"), ("G02", "3"), ("G03", "30"))}
    for req, changes in overrides.items():
        rows[req].update(changes)
    return list(rows.values())


def test_complete_block_is_consistent(blocks):
    b = blocks(board(), [coverage(r) for r in BOARD],
               [rule("GOV01", "G02 G01", "PASS"),
                rule("GOV02", "G03 G02 G01", "PASS")])
    assert b.loc["BOARD", "block_status"] == "CONSISTENT"
    assert b.loc["BOARD", "members_present"] == 3


def test_missing_member_makes_block_incomplete(blocks):
    b = blocks(board(), [coverage("G01"), coverage("G02"),
                         coverage("G03", "NOT_DISCLOSED")],
               [rule("GOV01", "G02 G01", "PASS")])
    assert b.loc["BOARD", "block_status"] == "INCOMPLETE"
    assert b.loc["BOARD", "members_missing"] == "G03"


def test_failed_rule_makes_block_inconsistent(blocks):
    b = blocks(board(), [coverage(r) for r in BOARD],
               [rule("GOV01", "G02 G01", "FAIL")])
    assert b.loc["BOARD", "block_status"] == "INCONSISTENT"


def test_pending_decision_blocks_the_whole_block(blocks):
    b = blocks(board(G02={"pending_review": "FSE-999:PENDING_NOT_APPLIED"}),
               [coverage(r) for r in BOARD],
               [rule("GOV01", "G02 G01", "FAIL")])
    # BLOCKED takes precedence over INCONSISTENT
    assert b.loc["BOARD", "block_status"] == "BLOCKED_PENDING_DECISION"
    assert "FSE-999" in b.loc["BOARD", "pending_or_deferred"]


def test_deferred_issue_and_crosswalk_candidate_block(blocks):
    b = blocks(board(G01={"deferred_issue_ids": "DQ-001"}),
               [coverage("G01"), coverage("G02"),
                coverage("G03", "REPORTED_PENDING_CROSSWALK_REVIEW")], [])
    reason = b.loc["BOARD", "pending_or_deferred"]
    assert b.loc["BOARD", "block_status"] == "BLOCKED_PENDING_DECISION"
    assert "DQ-001" in reason and "CROSSWALK_CANDIDATE_REVIEW" in reason


def test_unnormalised_member_makes_block_incomplete(blocks):
    b = blocks(board(G03={"final_value": "",
                          "final_status": "NOT_CONVERTED_UNIT_AMBIGUOUS"}),
               [coverage(r) for r in BOARD], [])
    assert b.loc["BOARD", "block_status"] == "INCOMPLETE"
    assert "NOT_CONVERTED_UNIT_AMBIGUOUS" in \
        b.loc["BOARD", "members_not_normalised"]


def test_no_member_reported_is_not_reported(blocks):
    b = blocks([], [coverage(r, "NOT_DISCLOSED") for r in BOARD], [])
    assert b.loc["BOARD", "block_status"] == "NOT_REPORTED"


def test_mixed_unit_tags_are_flagged(blocks):
    rows = [value("E01", "1", "Non-SI:GJ"), value("E02", "1", "Non-SI:MWh")]
    b = blocks(rows, [coverage("E01"), coverage("E02")], [])
    assert bool(b.loc["ENERGY", "mixed_unit_tags"])


def test_rule_relative_difference_helper():
    assert re_.rel_diff(Decimal(0), Decimal(0)) == 0
    assert re_.rel_diff(Decimal(100), Decimal(99)) == Decimal("0.01")
