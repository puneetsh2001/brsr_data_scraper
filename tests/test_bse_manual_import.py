"""
BSE manual import: identical, modified, duplicate, junk and unidentified
files. Runs entirely in tmp_path; the real bse_filings.csv is untouched.
"""

import dataclasses
import re
import shutil
from pathlib import Path

import pandas as pd
import pytest

from app.discovery import filing_registry
from app.scrapers import bse_scraper


@pytest.fixture()
def sample_filing(filing_manifest):
    rows = filing_manifest[(filing_manifest["is_latest_valid"] == "True")
                           & (filing_manifest["local_file_path"] != "")
                           & (filing_manifest["xbrl_entity_identifier"]
                              .str.startswith("INE"))]
    for r in rows.to_dict("records"):
        if Path(r["local_file_path"]).exists():
            return r
    pytest.skip("no downloaded filing available")


@pytest.fixture()
def manual_import(tmp_path, monkeypatch, sample_filing):
    manual = tmp_path / "manual"
    manual.mkdir()
    src = Path(sample_filing["local_file_path"])
    text = src.read_text(encoding="utf-8")

    shutil.copy(src, manual / "a_identical.xml")
    shutil.copy(src, manual / "b_identical_copy.xml")
    (manual / "c_modified.xml").write_text(
        text + "\n<!-- re-saved on BSE -->\n", encoding="utf-8")
    (manual / "d_junk.xml").write_text("<html>login page", encoding="utf-8")
    unknown = text.replace(sample_filing["xbrl_entity_identifier"],
                           "INE000X99999")
    unknown = re.sub(r"[LU]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}",
                     "U00000XX0000XXX000000", unknown)
    # ISIN and CIN unknown, BSE scrip code still the real company's
    (manual / "e_conflicting_ids.xml").write_text(unknown, encoding="utf-8")
    unknown = re.sub(r"(<[^>]*ScripCode[^>]*>)\s*\d+\s*(</)",
                     r"\g<1>999999\2", unknown)
    (manual / "f_unknown_company.xml").write_text(unknown, encoding="utf-8")

    out = tmp_path / "bse_filings.csv"
    monkeypatch.setattr(bse_scraper, "settings", dataclasses.replace(
        bse_scraper.settings, bse_manual_dir=manual))
    monkeypatch.setattr(bse_scraper, "BSE_FILINGS", out)
    df = bse_scraper.import_manual()
    return df.set_index(df["local_file_path"].map(lambda p: Path(p).name)), \
        out, sample_filing


def test_relations(manual_import):
    df, _, filing = manual_import
    rel = df["relation_to_nse"]
    assert rel["a_identical.xml"].startswith("IDENTICAL_TO_NSE_FILING")
    assert filing["filing_id"] in rel["a_identical.xml"]
    assert rel["b_identical_copy.xml"].startswith("DUPLICATE_OF_MANUAL_FILE")
    assert rel["c_modified.xml"].startswith("SAME_COMPANY_PERIOD_AS_NSE")
    assert rel["d_junk.xml"] == "NOT_XBRL_OR_UNREADABLE"
    assert rel["e_conflicting_ids.xml"].startswith("IDENTIFIER_CONFLICT")
    assert rel["f_unknown_company.xml"] == "COMPANY_NOT_IDENTIFIED"
    assert df.loc["f_unknown_company.xml", "company_id"] == ""


def test_only_clean_files_are_registrable(manual_import):
    df, _, _ = manual_import
    assert df["registrable"].to_dict() == {
        "a_identical.xml": True, "b_identical_copy.xml": False,
        "c_modified.xml": True, "d_junk.xml": False,
        "e_conflicting_ids.xml": False, "f_unknown_company.xml": False}


def test_filing_ids_unique(manual_import):
    df, _, _ = manual_import
    assert df["filing_id"].is_unique


def test_registry_merge_skips_non_registrable(manual_import, monkeypatch,
                                              filing_manifest):
    _, out, filing = manual_import
    monkeypatch.setattr(filing_registry, "BSE_FILINGS", out)
    before = filing_manifest.copy()
    merged = filing_registry.add_bse_manual_filings(before.copy())
    added = merged[~merged["filing_id"].isin(before["filing_id"])]
    # only the modified copy becomes a new row; the identical file just
    # tags the existing NSE filing
    assert len(added) == 1
    assert added.iloc[0]["company_id"] == filing["company_id"]
    tagged = merged.loc[merged["filing_id"] == filing["filing_id"],
                        "filing_sources"].iloc[0]
    assert "BSE_MANUAL_DOWNLOAD" in tagged and "NSE" in tagged
    assert merged["filing_id"].is_unique

