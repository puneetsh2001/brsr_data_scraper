"""G16/G17 stakeholder reconciliation review (owner request 2026-11-12)."""

import hashlib
from decimal import Decimal

import pandas as pd
import pytest

from app.validators import stakeholder_complaints_review as sr
from tests.conftest import PROJECT_ROOT, changed_since, read_csv

OUT = sr.OUTPUT_DIR
SNAPSHOT = (PROJECT_ROOT / "data/processed/nifty100/backups/"
            "20261112_pre_stakeholder_reconciliation/all_files_before.sha")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot():
    out = {}
    for line in SNAPSHOT.read_text().splitlines():
        digest, path = line.split(None, 1)
        out[path.lstrip("*").strip()] = digest
    return out


@pytest.fixture(scope="module")
def built():
    return sr.build()


# ---- name normalisation ---------------------------------------------

@pytest.mark.parametrize("a,b", [
    ("Others (Ex-Employees)", "Ex-employees"),
    ("Other (Vigilance)", "Vigilance"),
    ("Value Chain\nPartners", "value chain partners"),
    ("Investors & Shareholders", "Investors and Shareholders"),
])
def test_core_names_match(a, b):
    assert sr.core_key(a) == sr.core_key(b)


def test_generic_and_placeholder_names():
    assert sr.name_quality("Others") == "GENERIC_OTHER_NAME"
    assert sr.name_quality("Other (please specify)") == "GENERIC_OTHER_NAME"
    for name in ("0", "NA", "nil", "...", "NOT RECEIVED"):
        assert sr.name_quality(name) == "PLACEHOLDER_NAME"
    assert sr.name_quality("Vigilance") == "NAMED"
    # different groups stay different
    assert sr.core_key("VIG") != sr.core_key("Vigilance")
    assert sr.core_key("Contract Employees") != sr.core_key("Employees")


# ---- name-based matching --------------------------------------------

def row(name, domain, value):
    return {"stakeholder_group_name_in_xbrl": name, "domain": domain,
            "value": value, "name_exact_key": sr.exact_key(name),
            "name_core_key": sr.core_key(name),
            "name_quality": sr.name_quality(name)}


def test_row_order_change_is_not_a_restatement():
    cur = [row("Ex-Employees", "D1", "72"), row("Anonymous", "D2", "214")]
    nxt = [row("Anonymous", "D1", "214"), row("Ex-Employees", "D2", "72")]
    pairs = sr.match_rows(cur, nxt)
    assert {(c["value"], n["value"], m) for c, n, m in pairs} == {
        ("72", "72", "EXACT_NAME"), ("214", "214", "EXACT_NAME")}
    assert all(c["domain"] != n["domain"] for c, n, _ in pairs)


def test_core_name_match_and_genuine_restatement():
    pairs = sr.match_rows([row("Vigilance", "D1", "71")],
                          [row("Other (Vigilance)", "D6", "70")])
    (c, n, m), = pairs
    assert m == "CORE_NAME" and c["value"] != n["value"]


def test_duplicate_names_are_ambiguous():
    cur = [row("Others", "D1", "5"), row("Others", "D2", "9")]
    nxt = [row("Others", "D1", "9"), row("Others", "D2", "5")]
    methods = {m for _, _, m in sr.match_rows(cur, nxt)}
    assert "EXACT_NAME" not in methods and "GENERIC_NAME_ONLY" not in methods
    assert "AMBIGUOUS_NAME" in methods


def test_single_generic_pair_is_low_confidence_only():
    (_, _, m), = sr.match_rows([row("Others", "D1", "2")],
                               [row("Other", "D1", "0")])
    assert m == "GENERIC_NAME_ONLY"


def test_renamed_group_not_forced():
    pairs = sr.match_rows([row("Vigilance", "D1", "62")],
                          [row("VIG", "D1", "62")])
    assert {m for _, _, m in pairs} == {"ONLY_IN_CURRENT_REPORT",
                                        "ONLY_IN_NEXT_REPORT"}


# ---- outputs ----------------------------------------------------------

def test_raw_source_facts_unchanged(built):
    before = snapshot()
    rel = ("data/processed/nifty100/review/g16_g17_other_stakeholder/"
           "g16_g17_new_facts_with_evidence.csv")
    assert sha(PROJECT_ROOT / rel) == before[rel]
    raw = read_csv(OUT / "01_raw_source_facts.csv")
    assert raw.equals(read_csv(PROJECT_ROOT / rel))
    assert len(raw) == 876


def test_classification_counts(built):
    c = built["classified"]
    assert c["resolution_class"].value_counts().to_dict() == {
        "GENUINE_OTHER_GROUP": 660, "DUPLICATE_OF_STANDARD_GROUP": 196,
        "MANUAL_REVIEW_REQUIRED": 12, "AMBIGUOUS_STAKEHOLDER_MAPPING": 8}
    dup = c[c["resolution_class"] == "DUPLICATE_OF_STANDARD_GROUP"]
    assert (dup["matched_standard_groups"] != "").all()
    amb = c[c["resolution_class"] == "AMBIGUOUS_STAKEHOLDER_MAPPING"]
    assert amb["matched_standard_groups"].str.contains(r"\|").all()


def test_reliable_totals_exclude_overlaps(built):
    r = built["recon"]
    ok = r[r["total_status"] == "RELIABLE_TOTAL"]
    assert (ok["manual_review_rows"] == 0).all()
    assert (ok["ambiguous_rows"] == 0).all()
    assert not ok["under_filer_error_investigation"].any()
    assert (ok["standard_groups_missing"] == "").all()
    for x in ok.to_dict("records"):
        assert Decimal(x["reliable_total"]) == Decimal(x["standard_total"]) \
            + Decimal(x["genuine_other_total"])
    bad = r[r["total_status"] != "RELIABLE_TOTAL"]
    assert (bad["reliable_total"] == "").all() and (bad["reasons"] != "").all()


def test_known_cases(built):
    r = built["recon"].set_index(["nse_symbol", "fy_slot", "requirement_id"])
    sbin = r.loc[("SBIN", "2021-22", "G16")]
    assert sbin["total_status"] == "RELIABLE_TOTAL"
    assert sbin["reliable_total"] == sbin["standard_total"]   # dups excluded
    for key in (("POWERGRID", "2023-24", "G16"),
                ("ADANIENSOL", "2024-25", "G17"),
                ("HINDZINC", "2025-26", "G16")):
        assert r.loc[key, "total_status"] == "NO_RELIABLE_TOTAL"
    assert "POSSIBLE_DOUBLE_COUNT_IN_STANDARD_MEMBERS" in r.loc[
        ("HINDZINC", "2025-26", "G16"), "reasons"]


def test_investigations_supported_by_evidence(built):
    for inv in built["investigations"]:
        assert inv["evidence_supports_conclusion"] is True, inv["company"]


def test_all_28_notes_reevaluated(built):
    n = built["notes"]
    assert len(n) == 28
    assert not (n["re_evaluation"] == "NOT_EVALUATED").any()
    wipro = n[(n["nse_symbol"] == "WIPRO") & (n["fy_slot"] == "2023-24")]
    assert (wipro["re_evaluation"]
            == "LIKELY_FALSE_POSITIVE_ROW_ORDER_VALUES_PRESERVED").all()


def test_no_existing_file_changed(built):
    # only the owner-approved production switch may differ
    assert changed_since(SNAPSHOT) == []
