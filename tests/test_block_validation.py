"""Phase 10: block validation."""

import hashlib

import pytest

from app.validation import block_validation as bv
from tests.conftest import PROJECT_ROOT, changed_since

SNAPSHOT = (PROJECT_ROOT / "data/processed/nifty100/backups/"
            "20261112_pre_phase10/all_files_before.sha")
STATUSES = {"CONSISTENT", "INCOMPLETE", "INCONSISTENT",
            "BLOCKED_PENDING_DECISION", "NOT_REPORTED"}


@pytest.fixture(scope="module")
def built():
    return bv.build(write=False)


def test_every_company_year_has_every_block(built):
    b = built["blocks"]
    per = b.groupby(["master_isin", "fy_slot"])["block"].nunique()
    assert (per == len(bv.BLOCKS)).all()
    assert set(b["block_status"]) <= STATUSES


def test_status_precedence_and_meaning(built):
    b = built["blocks"]
    blocked = b[b["block_status"] == "BLOCKED_PENDING_DECISION"]
    assert (blocked["pending_or_deferred"] != "").all()
    rest = b[b["block_status"] != "BLOCKED_PENDING_DECISION"]
    assert (rest["pending_or_deferred"] == "").all()
    bad = b[b["block_status"] == "INCONSISTENT"]
    assert (bad["rules_failed"] > 0).all()
    ok = b[b["block_status"] == "CONSISTENT"]
    assert (ok["members_present"] > 0).all()
    assert (ok["members_missing"] == "").all()
    assert (ok["members_not_normalised"] == "").all()
    assert (ok["rules_failed"] == 0).all()
    assert (ok["rules_not_evaluated"] == 0).all()


def test_missing_member_is_never_consistent(built):
    b = built["blocks"]
    assert not ((b["members_missing"] != "")
                & (b["block_status"] == "CONSISTENT")).any()


def test_pending_scale_blocks_are_blocked(built):
    b = built["blocks"].set_index(["nse_symbol", "fy_slot", "block"])
    for key in (("POLYCAB", "2024-25", "ENERGY"),
                ("SOLARINDS", "2023-24", "ENERGY"),
                ("TVSMOTOR", "2024-25", "ENERGY"),
                ("VBL", "2024-25", "WATER"),
                ("DLF", "2024-25", "WAGES")):
        assert b.loc[key, "block_status"] == "BLOCKED_PENDING_DECISION", key
        assert "FSE-" in b.loc[key, "pending_or_deferred"]


def test_deferred_stakeholder_blocks_are_blocked(built):
    b = built["blocks"]
    s = b[(b["block"] == "STAKEHOLDER_COMPLAINTS")
          & (b["block_status"] == "BLOCKED_PENDING_DECISION")]
    assert set(s["nse_symbol"]) == {"POWERGRID", "ADANIENSOL", "PNB", "TMPV",
                                    "HINDZINC"}
    assert s["pending_or_deferred"].str.contains("DQ-").all()


def test_partial_block_packages_list_whole_block(built):
    p = built["packages"]
    partial = p[p["package_dependency"] == "BLOCK_PARTIAL"]
    assert partial["package_id"].nunique() == 4
    poly = set(partial.loc[partial["nse_symbol"] == "POLYCAB",
                           "requirement_id"])
    assert {"E01", "E02", "E04", "E05", "E06", "E07", "E08"} <= poly
    # the related E07 / E08 values outside the strong set need a decision
    need = partial[(partial["nse_symbol"] == "POLYCAB")
                   & partial["requirement_id"].isin(["E07", "E08"])]
    assert need["needs_decision"].all()
    assert (need["scale_record"] == "").all()


def test_no_existing_file_changed():
    # only the owner-approved production switch may differ (verified in
    # tests/test_production_switch.py)
    assert changed_since(SNAPSHOT) == []
