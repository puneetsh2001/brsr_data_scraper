
import xml.etree.ElementTree as ET
import pandas as pd

from pathlib import Path
from urllib.parse import urlparse
from datetime import datetime
import re

from app.config import settings
from app.core.http_client import nse_archive_client
from app.core.logging_setup import get_logger


log = get_logger(__name__, run_name="download")


# ==========================================================
# CONFIGURATION
# ==========================================================

RSS_URL = settings.nse_rss_url

BASE_DIR = settings.project_root

OUTPUT_DIR = settings.raw_dir / "nse_brsr_xml"

MANIFEST_FILE = (
    settings.processed_dir / "nse_brsr_download_manifest.csv"
)

_CLIENT = None


def http_client():
    """Shared nsearchives client (retries, backoff, rate limiting)."""
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = nse_archive_client()
    return _CLIENT


# ==========================================================
# HELPER FUNCTIONS
# ==========================================================

def clean_filename(value):
    """Convert company names into safe folder/file names."""

    value = str(value or "UNKNOWN").strip().upper()
    value = re.sub(r"[^A-Z0-9_-]+", "_", value)
    return value.strip("_")


def get_xml_filename(url):
    """Generate a filename from the original NSE XML URL."""

    original_name = Path(urlparse(url).path).name

    if not original_name:
        original_name = "brsr_report.xml"

    if not original_name.lower().endswith(".xml"):
        original_name += ".xml"

    return original_name


INVALID_XML_CHARS = re.compile(rb"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def strip_invalid_xml_chars(content):
    """Remove control characters not allowed in XML 1.0."""

    cleaned, removed = INVALID_XML_CHARS.subn(b"", content)
    return cleaned, removed


def load_existing_manifest():
    """Load previous download records."""

    if MANIFEST_FILE.exists():
        try:
            return pd.read_csv(MANIFEST_FILE).to_dict("records")
        except Exception as error:
            print("Could not read previous manifest:", error)

    return []


def save_manifest(records):
    """Save download history."""

    MANIFEST_FILE.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    dataframe = pd.DataFrame(records)

    dataframe.to_csv(
        MANIFEST_FILE,
        index=False,
        encoding="utf-8-sig"
    )


# ==========================================================
# STEP 1: FETCH NSE RSS FEED
# ==========================================================

def fetch_rss_records():

    print("\nConnecting to NSE BRSR RSS feed...")

    response = http_client().get(RSS_URL)

    print("HTTP Status:", response.status_code)

    response.raise_for_status()

    root = ET.fromstring(response.content)

    items = root.findall(".//item")

    records = []

    for item in items:

        company_name = item.findtext(
            "title",
            default=""
        ).strip()

        report_url = item.findtext(
            "link",
            default=""
        ).strip()

        publication_date = item.findtext(
            "pubDate",
            default=""
        ).strip()

        if not report_url:
            continue

        records.append({
            "company_name": company_name,
            "publication_date": publication_date,
            "report_url": report_url
        })

    print("Total RSS records found:", len(records))

    return records


# ==========================================================
# STEP 2: DOWNLOAD INDIVIDUAL XBRL REPORT
# ==========================================================

def download_report(record, target_folder=None):
    """
    Download one XBRL report. target_folder overrides the default
    company-name folder (used by the index-based downloader, which stores
    files under the company ISIN).
    """

    company_name = record["company_name"]
    report_url = record["report_url"]

    safe_company_name = clean_filename(company_name)

    filename = get_xml_filename(report_url)

    company_folder = (
        Path(target_folder) if target_folder
        else OUTPUT_DIR / safe_company_name
    )

    company_folder.mkdir(
        parents=True,
        exist_ok=True
    )

    file_path = company_folder / filename

    # Skip an existing non-empty file.
    if file_path.exists() and file_path.stat().st_size > 0:

        print("Already downloaded:", company_name)

        return {
            **record,
            "local_file_path": str(file_path),
            "download_status": "ALREADY_DOWNLOADED",
            "file_size_bytes": file_path.stat().st_size,
            "downloaded_at": ""
        }

    print("\nDownloading:", company_name)
    print("URL:", report_url)

    try:

        # retries, backoff and rate limiting are handled by the client
        response = http_client().get(report_url)

        response.raise_for_status()

        # Basic validation before saving.
        content = response.content

        if not content:
            raise ValueError("Downloaded file is empty.")

        status = "SUCCESS"
        note = ""

        try:
            root = ET.fromstring(content)
        except ET.ParseError:
            # Some NSE filings contain control characters that XML 1.0
            # forbids (e.g. \x02). Keep the original bytes untouched and
            # parse a copy with only those characters removed.
            cleaned, removed = strip_invalid_xml_chars(content)
            if not removed:
                raise
            root = ET.fromstring(cleaned)
            original_path = file_path.with_suffix(".original.xml")
            original_path.write_bytes(content)
            content = cleaned
            status = "SUCCESS_SANITIZED"
            note = (
                f"{removed} invalid XML control characters removed; "
                f"original saved as {original_path.name}"
            )

        if not root.tag.lower().endswith("xbrl"):
            raise ValueError(
                f"Unexpected XML root element: {root.tag}"
            )

        # Save only after successful XML validation.
        file_path.write_bytes(content)

        print("Download successful:", file_path, note)

        return {
            **record,
            "local_file_path": str(file_path),
            "download_status": status,
            "file_size_bytes": len(content),
            "downloaded_at": datetime.now().isoformat(
                timespec="seconds"
            ),
            "note": note,
        }

    except Exception as error:

        print("Download failed:", error)

        return {
            **record,
            "local_file_path": "",
            "download_status": "FAILED",
            "file_size_bytes": 0,
            "downloaded_at": "",
            "error": str(error)
        }


# ==========================================================
# STEP 3: MAIN MULTI-COMPANY DOWNLOADER
# ==========================================================

def download_multiple_brsr_reports():

    print("\n" + "=" * 70)
    print("MULTI-COMPANY NSE BRSR DOWNLOADER")
    print("=" * 70)

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    existing_records = load_existing_manifest()

    # Keep historical records, indexed by report URL.
    existing_by_url = {
        str(record.get("report_url", "")): record
        for record in existing_records
        if record.get("report_url")
    }

    try:

        rss_records = fetch_rss_records()

    except Exception as error:

        print("\nUnable to fetch RSS feed:", error)
        return

    if not rss_records:

        print("No reports found in RSS feed.")
        return

    results = []

    total = len(rss_records)

    print("\nReports to process:", total)
    print("=" * 70)

    for index, record in enumerate(rss_records, start=1):

        print(f"\nProcessing {index}/{total}")

        old_record = existing_by_url.get(
            record["report_url"]
        )

        # Skip previously successful downloads if file exists.
        if old_record:

            old_path = old_record.get(
                "local_file_path",
                ""
            )

            if (
                old_record.get("download_status") in
                ["SUCCESS", "ALREADY_DOWNLOADED"]
                and old_path
                and Path(old_path).exists()
                and Path(old_path).stat().st_size > 0
            ):

                print(
                    "Skipping previously downloaded report:",
                    record["company_name"]
                )

                results.append(old_record)
                continue

        result = download_report(record)

        results.append(result)

    # Preserve older manifest records that are no longer in RSS.
    current_urls = {
        record["report_url"]
        for record in rss_records
    }

    for old_record in existing_records:

        old_url = str(
            old_record.get("report_url", "")
        )

        if old_url and old_url not in current_urls:
            results.append(old_record)

    save_manifest(results)

    dataframe = pd.DataFrame(results)

    print("\n" + "=" * 70)
    print("DOWNLOAD SUMMARY")
    print("=" * 70)

    print("Total manifest records:", len(dataframe))

    if "download_status" in dataframe.columns:

        print("\nStatus summary:")

        print(
            dataframe["download_status"]
            .value_counts(dropna=False)
        )

    print("\nUnique companies:")

    print(
        dataframe["company_name"]
        .nunique()
    )

    print("\nManifest saved to:")
    print(MANIFEST_FILE)

    print("\nXML reports saved under:")
    print(OUTPUT_DIR)

    print("\nMulti-company download completed.")


# ==========================================================
# STEP 4: INDEX-BASED DOWNLOADER (NSE BRSR API SELECTION)
# ==========================================================

SELECTION_FILE = (
    BASE_DIR / "data" / "processed" / "nifty100"
    / "nse_brsr_filing_selection.csv"
)

INDEX_MANIFEST_FILE = (
    BASE_DIR / "data" / "processed" / "nifty100"
    / "nse_brsr_report_manifest.csv"
)


def file_sha256(path):
    import hashlib

    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_selected_reports(
    selection_file=SELECTION_FILE,
    manifest_file=INDEX_MANIFEST_FILE,
):
    """
    Download every filing in the discovery selection CSV
    (app/scrapers/nse_brsr_filing_index.py). Files are stored under
    data/raw/nse_brsr_xml/<ISIN>/<original NSE filename>.

    The manifest keeps the columns the extractor already reads
    (company_name, report_url, local_file_path, download_status) plus
    identity, period and source columns. PDFs are not downloaded; their
    URLs are recorded.
    """

    print("\n" + "=" * 70)
    print("INDEX-BASED NSE BRSR DOWNLOADER")
    print("=" * 70)

    selection = pd.read_csv(selection_file, dtype=str).fillna("")
    print("Filings in selection:", len(selection))

    results = []
    total = len(selection)

    for index, row in enumerate(selection.to_dict("records"), start=1):

        print(f"\nProcessing {index}/{total}: "
              f"{row['master_symbol']} {row['api_period']}")

        record = {
            "company_name": row["master_company_name"],
            "report_url": row["xbrl_url"],
            "publication_date": row["submission_date"],
        }

        target_folder = OUTPUT_DIR / row["master_isin"]

        result = download_report(record, target_folder=target_folder)

        sha256 = ""
        if result.get("local_file_path"):
            sha256 = file_sha256(result["local_file_path"])
            original = Path(result["local_file_path"]).with_suffix(
                ".original.xml"
            )
            if original.exists() and not result.get("note"):
                result["note"] = (
                    "sanitized copy of " + original.name
                    + " (invalid XML control characters removed)"
                )
                result["download_status"] = "SUCCESS_SANITIZED"

        results.append({
            **result,
            "isin": row["master_isin"],
            "nse_symbol": row["master_symbol"],
            "filing_symbol": row["filing_symbol"],
            "match_basis": row["match_basis"],
            "api_company_name": row["api_company_name"],
            "api_period": row["api_period"],
            "estimated_fy_slot": row["estimated_fy_slot"],
            "voluntary_year": row["voluntary_year"],
            "selection_reason": row.get("selection_reason", ""),
            "submission_date": row["submission_date"],
            "revision_date": row["revision_date"],
            "api_xbrl_file_size": row["xbrl_file_size"],
            "pdf_url": row["pdf_url"],
            "sha256": sha256,
        })
        # pacing between downloads is enforced by the shared client

    manifest = pd.DataFrame(results)
    manifest_file.parent.mkdir(parents=True, exist_ok=True)
    manifest.to_csv(manifest_file, index=False, encoding="utf-8-sig")

    print("\n" + "=" * 70)
    print("DOWNLOAD SUMMARY")
    print("=" * 70)
    print(manifest["download_status"].value_counts(dropna=False).to_string())
    failed = manifest[manifest["download_status"] == "FAILED"]
    if len(failed):
        print("\nFailed downloads:")
        print(failed[["nse_symbol", "api_period", "report_url", "error"]]
              .to_string(index=False))
    print("\nManifest saved to:", manifest_file)


if __name__ == "__main__":

    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source", choices=["rss", "index"], default="rss",
        help="rss: latest filings from the NSE RSS feed (original mode); "
             "index: filings selected by nse_brsr_filing_index.py",
    )
    args = parser.parse_args()

    if args.source == "index":
        download_selected_reports()
    else:
        download_multiple_brsr_reports()
