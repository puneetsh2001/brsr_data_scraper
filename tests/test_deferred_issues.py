"""Deferred-issues register: integrity and the load gate."""

import pytest

from app.core import deferred_issues as di


@pytest.fixture(scope="module")
def reg():
    return di.load()


def test_register_complete(reg):
    assert list(reg.columns) == di.REQUIRED_COLUMNS
    assert reg["issue_id"].is_unique and len(reg) == 39
    for col in ("company", "fy_slot", "metric", "original_value",
                "proposed_interpretation_or_correction", "evidence_file",
                "reason_for_deferral", "risk_impact", "revisit_phase",
                "must_resolve_before"):
        assert (reg[col] != "").all(), col


def test_every_issue_still_deferred_or_resolved_with_record(reg):
    resolved = reg[reg["status"] != di.OPEN]
    assert (resolved["resolution"] != "").all()
    assert (resolved["resolved_by"] != "").all()
    assert (reg.loc[reg["status"] == di.OPEN, "resolution"] == "").all()


def test_load_gate_blocks_while_issues_open(reg):
    if (reg["status"] == di.OPEN).any():
        with pytest.raises(di.DeferredIssuesOpen):
            di.assert_none_open("PostgreSQL staging load")


def test_issue_index_links_values(reg):
    idx = di.issue_index()
    pg = reg[reg["company"] == "POWERGRID"]["master_isin"].iloc[0]
    assert idx[(pg, "2023-24", "G16")]
