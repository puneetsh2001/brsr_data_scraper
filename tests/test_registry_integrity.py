"""Integrity checks on the generated company master and filing registry."""

from pathlib import Path

import pytest

from app.core.http_client import sha256_file
from app.validators.brsr_report_verifier import cin_registration, is_real_cin


def test_company_ids_unique(company_master):
    assert company_master["company_id"].is_unique
    assert (company_master["company_id"] != "").all()


def test_no_isin_shared(company_master):
    isins = company_master.loc[company_master["isin"] != "", "isin"]
    assert isins.is_unique


def test_no_current_symbol_owned_twice(company_master):
    syms = company_master.loc[company_master["nse_symbol"] != "",
                              "nse_symbol"]
    assert syms.is_unique, syms[syms.duplicated()].tolist()


def test_no_real_cin_shared(company_master):
    real = company_master[company_master["cin"].map(is_real_cin)]
    regs = real["cin"].map(cin_registration)
    assert regs.is_unique, real[regs.duplicated(keep=False)][
        ["company_id", "cin"]].to_dict("records")


def test_placeholder_cins_never_marked_verified(company_master):
    bad = company_master[~company_master["cin"].map(is_real_cin)
                         & (company_master["cin"] != "")
                         & (company_master["cin_status"] == "VERIFIED_XBRL")]
    assert bad.empty, bad[["company_id", "cin"]].to_dict("records")


def test_filing_ids_unique_and_mapped(filing_manifest, company_master):
    assert filing_manifest["filing_id"].is_unique
    assert filing_manifest["company_id"].isin(
        company_master["company_id"]).all()


def test_one_latest_valid_per_company_slot(filing_manifest):
    latest = filing_manifest[filing_manifest["is_latest_valid"] == "True"]
    dup = latest.duplicated(["company_id", "fy_slot"], keep=False)
    assert not dup.any(), latest[dup][["company_id", "fy_slot"]].values


def test_processed_filings_are_latest_valid(filing_manifest):
    done = filing_manifest[filing_manifest["processing_status"]
                           == "EXTRACTED_CURRENT_PIPELINE"]
    assert len(done) > 0
    assert (done["is_latest_valid"] == "True").all()


def test_no_pending_work_in_scope(filing_manifest):
    scope = filing_manifest[filing_manifest["in_processing_scope"] == "True"]
    assert (scope["needs_download"] == "False").all()
    assert (scope["needs_processing"] == "False").all()


def test_local_files_exist_and_match_sha(filing_manifest):
    rows = filing_manifest[filing_manifest["local_file_path"] != ""]
    missing, mismatch = [], []
    for r in rows.to_dict("records"):
        path = Path(r["local_file_path"])
        if not path.exists():
            missing.append(r["filing_id"])
        elif r["sha256"] and sha256_file(path) != r["sha256"]:
            mismatch.append(r["filing_id"])
    assert not missing and not mismatch, (missing[:5], mismatch[:5])


def test_verified_by_cin_has_real_reference(verification):
    rows = verification[verification["identity_status"].str.startswith(
        "VERIFIED_BY_CIN")]
    bad = rows[~rows["reference_cin"].map(is_real_cin)]
    assert bad.empty, bad[["nse_symbol", "true_fy_slot", "reference_cin"]].values


def test_bankbaroda_not_verified_by_cin(verification):
    rows = verification[verification["nse_symbol"] == "BANKBARODA"]
    assert len(rows) > 0
    assert not rows["identity_status"].str.startswith("VERIFIED_BY_CIN").any()
