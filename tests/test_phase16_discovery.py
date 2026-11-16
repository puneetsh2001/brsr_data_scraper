"""
Phase 16: source discovery and filing identification on controlled fixtures.

`app.discovery.filing_registry.build()` runs against a synthetic company
master, identifier table, NSE API snapshot rows and pipeline-state files in
a temporary directory. No network, no real registry file is read or written.

Master prompt section 18 (source discovery): valid filing discovery,
duplicate filing detection, missing company identifier, invalid filing URL,
revised filing handling.
"""

from datetime import date

import pandas as pd
import pytest

from app.discovery import company_master as cm
from app.discovery import filing_registry as fr

URL = "https://nsearchives.nseindia.com/corporate/xbrl/{}.xml"


def api_row(symbol, name, fy_from, fy_to, submitted, xbrl, revised="-",
            snapshot="20990101/bulk_a.json"):
    return {"symbol": symbol, "companyName": name, "fyFrom": fy_from,
            "fyTo": fy_to, "submissionDate": submitted,
            "revisionDate": revised, "xbrlFile": xbrl, "xbrlFileSize": "10",
            "attachmentFile": "https://nsearchives.nseindia.com/x.pdf",
            "_snapshot": snapshot}


def write(path, rows, columns):
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=columns).to_csv(path, index=False,
                                               encoding="utf-8-sig")


@pytest.fixture()
def registry(tmp_path, monkeypatch):
    """Build the filing registry from fixtures; return the manifest."""
    reg, nifty = tmp_path / "registry", tmp_path / "nifty100"
    write(reg / "company_master.csv", [
        ["C-INE000A01001", "INE000A01001", "True"],
        ["C-INE000B01002", "INE000B01002", "True"],
        ["C-INE000C01003", "INE000C01003", "False"]],
        ["company_id", "isin", "in_processing_scope"])
    write(reg / "company_identifiers.csv", [
        ["C-INE000A01001", "NSE_SYMBOL", "ALPHA", ""],
        ["C-INE000B01002", "NSE_SYMBOL", "BETA", ""],
        ["C-INE000C01003", "NSE_SYMBOL", "GAMMA", ""]],
        ["company_id", "identifier_type", "value", "valid_to"])

    a1, a2 = URL.format("ALPHA_2024_v1"), URL.format("ALPHA_2024_v2")
    b1, bf = URL.format("BETA_2024"), URL.format("BETA_2024_failed")
    rows = [
        # valid filing seen in two API snapshots -> one registry row
        api_row("ALPHA", "Alpha Ltd", 2024, 2025, "10-Jul-2025", a1),
        api_row("ALPHA", "Alpha Ltd", 2024, 2025, "10-Jul-2025", a1,
                snapshot="20990102/bulk_a.json"),
        # revised filing for the same company and FY slot
        api_row("ALPHA", "Alpha Ltd", 2024, 2025, "20-Aug-2025", a2,
                revised="25-Aug-2025"),
        # a filing whose download failed and a successful one
        api_row("BETA", "Beta Ltd", 2024, 2025, "01-Jul-2025", b1),
        api_row("BETA", "Beta Ltd", 2024, 2025, "05-Sep-2025", bf),
        # out-of-scope company
        api_row("GAMMA", "Gamma Ltd", 2024, 2025, "01-Jul-2025",
                URL.format("GAMMA_2024")),
        # symbol not in the company master -> missing identifier
        api_row("UNKNOWNCO", "Unknown Ltd", 2024, 2025, "01-Jul-2025",
                URL.format("UNKNOWN_2024")),
        # no XBRL (PDF attachment only / invalid filing URL)
        api_row("BETA", "Beta Ltd", 2023, 2024, "01-Jul-2024",
                "https://nsearchives.nseindia.com/corporate/null"),
    ]
    monkeypatch.setattr(fr, "load_api_rows", lambda: [dict(r) for r in rows])

    period = ("2024-04-01", "2025-03-31")
    write(nifty / "nse_brsr_report_manifest.csv", [
        [a2, "", "NOT_DOWNLOADED", "", "", ""],
        [b1, "", "NOT_DOWNLOADED", "", "", ""],
        [bf, "", "FAILED", "", "HTTP 404", ""]],
        ["report_url", "local_file_path", "download_status", "sha256",
         "error", "note"])
    write(nifty / "nse_brsr_report_verification.csv", [
        [a1, *period, "2024-04-30", "VERIFIED", "INE000A01001"],
        [a2, *period, "2024-04-30", "VERIFIED", "INE000A01001"]],
        ["report_url", "fy_start", "fy_end", "taxonomy_version",
         "identity_status", "xbrl_isin"])
    write(nifty / "multi_company_extraction_summary.csv",
          [["ALPHA_2024_v1.xml"]], ["source_file"])

    monkeypatch.setattr(fr, "REGISTRY_DIR", reg)
    monkeypatch.setattr(fr, "NIFTY_DIR", nifty)
    monkeypatch.setattr(fr, "RSS_MANIFEST", tmp_path / "absent_rss.csv")
    monkeypatch.setattr(fr, "RSS_SUMMARY", tmp_path / "absent_sum.csv")
    monkeypatch.setattr(fr, "BSE_FILINGS", tmp_path / "absent_bse.csv")
    monkeypatch.setattr(fr, "MANIFEST_OUTPUT", reg / "filing_manifest.csv")
    monkeypatch.setattr(fr, "SUMMARY_OUTPUT", reg / "filing_summary.csv")
    assert fr.settings.first_fy_slot <= 2024     # fixture slots in range
    df = fr.build()
    return df.set_index("xbrl_url", drop=False), \
        {"a1": a1, "a2": a2, "b1": b1, "bf": bf}, reg


def test_valid_filing_discovery(registry):
    df, u, reg = registry
    a1 = df.loc[u["a1"]]
    assert a1["company_id"] == "C-INE000A01001"
    assert a1["fy_slot"] == "2024-25" and a1["fy_slot_basis"] == "XBRL_PERIOD"
    assert a1["processing_status"] == "EXTRACTED_CURRENT_PIPELINE"
    assert a1["filing_id"] == fr.filing_id(u["a1"], "")
    # the manifest is written to the fixture directory only
    assert (reg / "filing_manifest.csv").exists()


def test_duplicate_filing_rows_are_merged(registry):
    df, u, _ = registry
    assert df["filing_id"].is_unique
    assert (df["xbrl_url"] == u["a1"]).sum() == 1
    snaps = df.loc[u["a1"], "api_snapshots"].split(" | ")
    assert snaps == ["20990101/bulk_a.json", "20990102/bulk_a.json"]


def test_missing_company_identifier_is_kept_unassigned(registry):
    df, _, _ = registry
    unknown = df[df["filing_symbol"] == "UNKNOWNCO"]
    assert len(unknown) == 1
    row = unknown.iloc[0]
    assert row["company_id"] == ""               # never guessed
    assert not row["in_processing_scope"]
    assert not row["needs_download"]


def test_invalid_filing_url_is_recorded_not_downloaded(registry):
    df, _, _ = registry
    nox = df[(df["filing_symbol"] == "BETA") & (df["api_fy_from"] == "2023")]
    assert len(nox) == 1
    row = nox.iloc[0]
    assert row["xbrl_url"] == "" and row["original_filename"] == ""
    assert row["processing_status"] == "NO_XBRL_ON_NSE"
    assert row["fy_slot_basis"] == "API_TAG_ESTIMATE"
    assert not row["is_latest_valid"] and not row["needs_download"]
    assert row["filing_id"].startswith("F-")      # fallback key, still stable


def test_revised_filing_supersedes_but_original_is_kept(registry):
    df, u, _ = registry
    v1, v2 = df.loc[u["a1"]], df.loc[u["a2"]]
    assert (v1["version_number"], v2["version_number"]) == (1, 2)
    assert v1["versions_in_slot"] == v2["versions_in_slot"] == 2
    assert v1["filing_version_status"] == "ORIGINAL"
    assert v2["filing_version_status"] == "SUBSEQUENT_FILING (REVISED_ON_NSE)"
    assert v2["revision_date"] == date(2025, 8, 25)
    assert bool(v2["is_latest_valid"]) and not bool(v1["is_latest_valid"])
    # the superseded original is never deleted
    assert v1["filing_id"] in set(df["filing_id"])
    # the latest valid version is not downloaded yet -> incremental flag
    assert bool(v2["needs_download"]) and not bool(v1["needs_download"])


def test_failed_download_is_never_latest_valid(registry):
    df, u, _ = registry
    ok, failed = df.loc[u["b1"]], df.loc[u["bf"]]
    assert failed["processing_status"] == "DOWNLOAD_FAILED"
    assert failed["error_details"] == "HTTP 404"
    assert not bool(failed["is_latest_valid"])
    assert bool(ok["is_latest_valid"])


def test_out_of_scope_company_is_registered_not_processed(registry):
    df, _, _ = registry
    g = df[df["filing_symbol"] == "GAMMA"].iloc[0]
    assert g["company_id"] == "C-INE000C01003"
    assert not g["in_processing_scope"] and not g["needs_download"]


# ---- pure helpers -----------------------------------------------------------

@pytest.mark.parametrize("start,end,slot", [
    (date(2024, 4, 1), date(2025, 3, 31), 2024),     # April-March
    (date(2025, 1, 1), date(2025, 12, 31), 2025),    # calendar year
    (date(2023, 10, 1), date(2024, 9, 30), 2024),    # 6/6 tie -> end year
    (date(2024, 10, 1), date(2026, 3, 31), 2025),    # 18-month period
    (None, date(2025, 3, 31), 2024),                 # no start date
])
def test_fy_slot_for_period(start, end, slot):
    assert fr.fy_slot_for_period(start, end) == slot


def test_filing_id_is_stable_and_url_based():
    a = fr.filing_id(URL.format("X"), "fallback")
    assert a == fr.filing_id(URL.format("X"), "other") and a.startswith("F-")
    assert fr.filing_id("", "k1") != fr.filing_id("", "k2")


@pytest.mark.parametrize("text,expected", [
    ("09-Mar-2023", date(2023, 3, 9)), ("2023-03-09", date(2023, 3, 9)),
    ("09/03/2023", date(2023, 3, 9)), ("-", None), ("", None),
    ("garbage", None)])
def test_parse_date_never_guesses(text, expected):
    assert cm.parse_date(text) == expected


def test_symbol_change_chain():
    forward = {"OLD": [("MID", date(2020, 1, 1), "x")],
               "MID": [("NEW", date(2022, 1, 1), "x")]}
    assert cm.current_symbol("OLD", forward, {"NEW"}) == "NEW"
    assert cm.current_symbol("OLD", forward, {"OTHER"}) is None
    assert cm.final_symbol("OLD", forward) == "NEW"
    backward = {"NEW": [("MID", None, "x")], "MID": [("OLD", None, "x")]}
    assert [s for s, _ in cm.symbol_history("NEW", backward)] == [
        "NEW", "MID", "OLD"]
    # a cycle terminates
    loop = {"A": [("B", None, "x")], "B": [("A", None, "x")]}
    assert cm.final_symbol("A", loop) in {"A", "B"}
