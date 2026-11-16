"""
BRSR filing registry.

One row per filing (keyed by its XBRL URL) for every company in the company
master, built from:

  * saved NSE BRSR API snapshots (all NSE filers, FY2021-22 onwards)
  * the historical RSS run manifest (data/processed/nse_brsr_download_manifest.csv)
  * the Nifty 100 download manifest, verification and extraction summary
  * header reads of the historical RSS files (true reporting period)

For each filing it records the company, source(s), filing symbol, API FY
tags, true reporting period / FY slot (from the XBRL where downloaded),
submission and revision dates, version within company + FY slot, the
latest valid version, download / parsing / processing status, identity
verification, taxonomy version, errors, and incremental-processing flags.
Filings are never deleted: superseded versions stay in the registry.

Outputs (data/processed/registry/):
  filing_manifest.csv
  filing_registry_summary.csv

Run:
    python -m app.discovery.filing_registry                 # offline
    python -m app.discovery.filing_registry --refresh-api   # new API snapshot
"""

import argparse
import hashlib
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from app.config import settings
from app.core.http_client import sha256_file
from app.core.logging_setup import get_logger
from app.discovery.company_master import (
    API_SNAPSHOT_ROOT, REGISTRY_DIR, load_api_rows, parse_date)
from app.scrapers.nse_brsr_filing_index import (
    estimated_period_end, latest_completed_slot, slot_for_date, slot_label)
from app.validators.brsr_report_verifier import read_report_header


log = get_logger(__name__, run_name="registry")

NIFTY_DIR = settings.processed_dir / "nifty100"
MANIFEST_OUTPUT = REGISTRY_DIR / "filing_manifest.csv"
SUMMARY_OUTPUT = REGISTRY_DIR / "filing_registry_summary.csv"

RSS_MANIFEST = settings.processed_dir / "nse_brsr_download_manifest.csv"
RSS_SUMMARY = settings.processed_dir / "multi_company_extraction_summary.csv"
OLD_SYMBOL_GRACE_DAYS = 45


def fy_slot_for_period(start, end):
    """
    April-March year (slot start year) holding the most days of the
    reporting period; ties go to the year containing the period end.

      Jul 2025 - Jun 2026  -> 2025 (FY2025-26: 9 of 12 months)
      Jan 2025 - Dec 2025  -> 2025 (FY2025-26: Apr-Dec)
      Oct 2023 - Sep 2024  -> 2024 (6/6 tie -> period-end year)
      Oct 2024 - Mar 2026  -> 2025 (12 of 18 months)
    Identical to the period-end rule for every April-March filer.
    """
    if not start or not end or start > end:
        return slot_for_date(end) if end else None
    days = defaultdict(int)
    current = start
    while current <= end:
        slot = slot_for_date(current)
        slot_end = datetime(slot + 1, 3, 31).date()
        chunk_end = min(slot_end, end)
        days[slot] += (chunk_end - current).days + 1
        current = chunk_end + timedelta(days=1)
    best = max(days.values())
    winners = [s for s, d in days.items() if d == best]
    return slot_for_date(end) if len(winners) > 1 else winners[0]


BSE_FILINGS = REGISTRY_DIR / "bse_filings.csv"


def add_bse_manual_filings(df):
    """
    Merge manually downloaded BSE filings (app.scrapers.bse_scraper):
      identical file already registered from NSE -> add the BSE source tag
      otherwise                                   -> new registry row
    """
    if not BSE_FILINGS.exists():
        return df
    bse = pd.read_csv(BSE_FILINGS, dtype=str, keep_default_na=False)
    if bse.empty:
        return df
    by_sha = {sha: idx for idx, sha in df["sha256"].items() if sha}
    new_rows = []
    for r in bse.to_dict("records"):
        # unidentified, unreadable or duplicate files are kept in
        # bse_filings.csv for review but never enter the registry
        if str(r.get("registrable", "")).lower() != "true" \
                or not r["company_id"]:
            continue
        if r["sha256"] in by_sha:
            idx = by_sha[r["sha256"]]
            df.at[idx, "filing_sources"] = " | ".join(sorted(set(
                df.at[idx, "filing_sources"].split(" | ")
                + ["BSE_MANUAL_DOWNLOAD"])))
            continue
        start, end = parse_date(r["reporting_period_start"]), \
            parse_date(r["reporting_period_end"])
        slot = fy_slot_for_period(start, end) if end else None
        new_rows.append({
            "filing_id": r["filing_id"], "company_id": r["company_id"],
            "filing_symbol": "", "api_company_name": r["xbrl_company_name"],
            "api_fy_from": "", "api_fy_to": "",
            "fy_slot": slot_label(slot) if slot is not None else "",
            "fy_slot_basis": "XBRL_PERIOD" if slot is not None else "UNKNOWN",
            "reporting_period_start": r["reporting_period_start"],
            "reporting_period_end": r["reporting_period_end"],
            "submission_date": "", "revision_date": "", "xbrl_url": "",
            "original_filename": Path(r["local_file_path"]).name,
            "pdf_url": "", "xbrl_file_size_api": "",
            "local_file_path": r["local_file_path"], "sha256": r["sha256"],
            "filing_sources": "BSE_MANUAL_DOWNLOAD", "api_snapshots": "",
            "download_status": "SUCCESS_MANUAL", "download_note":
            r["relation_to_nse"], "error_details": r["parse_error"],
            "parsing_status": "NOT_PARSED",
            "processing_status": "DOWNLOADED_NOT_PROCESSED",
            "identity_status": f"MATCHED_BY_{r['match_basis']}",
            "xbrl_entity_identifier": r["xbrl_entity_identifier"],
            "taxonomy_version": r["taxonomy_version"],
            "is_voluntary_year": slot == 2021, "_slot": slot,
        })
    if new_rows:
        df = pd.concat([df, pd.DataFrame(new_rows)], ignore_index=True)
    return df


def filing_id(url, fallback):
    key = url or fallback
    return "F-" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def refresh_api_snapshot():
    """Fetch the full NSE BRSR filing index into a new snapshot folder."""
    from app.scrapers import nse_brsr_filing_index as discovery

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    discovery.API_SNAPSHOT_DIR = API_SNAPSHOT_ROOT / stamp
    nse = discovery.NSESession()
    start = datetime(settings.first_fy_slot + 1, 4, 1)
    total = 0
    for window_start, window_end in discovery.fy_windows(start,
                                                         datetime.now()):
        rows = discovery.fetch_rows(
            nse, {"index": "equities",
                  "from_date": window_start.strftime("%d-%m-%Y"),
                  "to_date": window_end.strftime("%d-%m-%Y")},
            f"bulk_{window_start:%Y%m%d}_{window_end:%Y%m%d}")
        total += len(rows)
    log.info("API snapshot refreshed", extra={"fields": {
        "folder": str(discovery.API_SNAPSHOT_DIR), "rows": total}})


def build():
    master = pd.read_csv(REGISTRY_DIR / "company_master.csv",
                         dtype=str, keep_default_na=False)
    idents = pd.read_csv(REGISTRY_DIR / "company_identifiers.csv",
                         dtype=str, keep_default_na=False)
    scope = dict(zip(master["company_id"],
                     master["in_processing_scope"] == "True"))
    by_isin = dict(zip(master["isin"], master["company_id"]))

    # symbol -> [(company_id, valid_to)]
    symbol_owner = defaultdict(list)
    for r in idents[idents["identifier_type"] == "NSE_SYMBOL"].to_dict(
            "records"):
        symbol_owner[r["value"]].append(
            (r["company_id"], parse_date(r["valid_to"])))

    def company_for(symbol, submitted):
        owners = symbol_owner.get((symbol or "").upper(), [])
        if len(owners) <= 1:
            return owners[0][0] if owners else ""
        # symbol reused: the owner whose validity covers the submission
        for company, valid_to in owners:
            if valid_to and submitted and submitted <= valid_to + timedelta(
                    days=OLD_SYMBOL_GRACE_DAYS):
                return company
        current = [c for c, v in owners if v is None]
        return current[0] if current else owners[0][0]

    # ---- filings from API snapshots ----------------------------------------
    filings = {}
    for row in load_api_rows():
        url = (row.get("xbrlFile") or "").strip()
        has_xbrl = url.lower().endswith(".xml")
        key = url if has_xbrl else (f"{row.get('symbol')}|{row.get('fyFrom')}"
                                    f"|{row.get('fyTo')}|"
                                    f"{row.get('submissionDate')}")
        submitted = parse_date(row.get("submissionDate"))
        rec = filings.setdefault(key, {
            "filing_id": filing_id(url if has_xbrl else "", key),
            "company_id": company_for(row.get("symbol"), submitted),
            "filing_symbol": (row.get("symbol") or "").upper(),
            "api_company_name": row.get("companyName", ""),
            "api_fy_from": str(row.get("fyFrom", "")),
            "api_fy_to": str(row.get("fyTo", "")),
            "submission_date": submitted or "",
            "revision_date": "" if row.get("revisionDate") in (None, "-")
            else parse_date(row.get("revisionDate")) or
            row.get("revisionDate"),
            "xbrl_url": url if has_xbrl else "",
            "xbrl_file_size_api": row.get("xbrlFileSize", ""),
            "pdf_url": row.get("attachmentFile", ""),
            "sources": set(),
            "snapshots": set(),
        })
        rec["sources"].add("NSE_BRSR_API")
        rec["snapshots"].add(row["_snapshot"])

    # ---- historical RSS run -------------------------------------------------
    rss_local = {}
    if RSS_MANIFEST.exists():
        for r in pd.read_csv(RSS_MANIFEST, dtype=str).fillna("").to_dict(
                "records"):
            url = r["report_url"].strip()
            rss_local[url] = r
            rec = filings.get(url)
            if rec is None:
                rec = filings.setdefault(url, {
                    "filing_id": filing_id(url, url), "company_id": "",
                    "filing_symbol": "", "api_company_name":
                    r["company_name"], "api_fy_from": "", "api_fy_to": "",
                    "submission_date": "", "revision_date": "",
                    "xbrl_url": url, "xbrl_file_size_api": "",
                    "pdf_url": "", "sources": set(), "snapshots": set()})
            rec["sources"].add("NSE_RSS_HISTORICAL_RUN")

    # ---- Nifty 100 pipeline state ------------------------------------------
    manifest = pd.read_csv(NIFTY_DIR / "nse_brsr_report_manifest.csv",
                           dtype=str).fillna("")
    manifest_by_url = {r["report_url"]: r for r in manifest.to_dict(
        "records")}
    verification = pd.read_csv(NIFTY_DIR / "nse_brsr_report_verification"
                               ".csv", dtype=str).fillna("")
    verif_by_url = {r["report_url"]: r for r in verification.to_dict(
        "records")}
    nifty_parsed = set(pd.read_csv(
        NIFTY_DIR / "multi_company_extraction_summary.csv",
        dtype=str).fillna("")["source_file"])
    rss_parsed = set(pd.read_csv(RSS_SUMMARY, dtype=str).fillna(
        "")["source_file"]) if RSS_SUMMARY.exists() else set()

    last_slot = latest_completed_slot(datetime.now())

    rows = []
    for key, rec in filings.items():
        url = rec["xbrl_url"]
        filename = Path(url).name if url else ""
        verif = verif_by_url.get(url)
        man = manifest_by_url.get(url)
        rss = rss_local.get(url)

        local_path, download_status, sha256, error, note = "", \
            "NOT_DOWNLOADED", "", "", ""
        if man:
            local_path = man["local_file_path"]
            download_status = man["download_status"]
            sha256, error = man.get("sha256", ""), man.get("error", "")
            note = man.get("note", "")
        if (not local_path or not Path(local_path).exists()) and rss:
            if rss["local_file_path"] and Path(rss["local_file_path"]
                                                ).exists():
                local_path = rss["local_file_path"]
                download_status = "SUCCESS_HISTORICAL_RSS_RUN"
                sha256 = ""
        if local_path and Path(local_path).exists() and not sha256:
            sha256 = sha256_file(local_path)
        file_exists = bool(local_path) and Path(local_path).exists()

        # true reporting period: verification, else read the header
        fy_start = fy_end = taxonomy = identity = xbrl_isin = ""
        if verif:
            fy_start, fy_end = verif["fy_start"], verif["fy_end"]
            taxonomy, identity = verif["taxonomy_version"], \
                verif["identity_status"]
            xbrl_isin = verif["xbrl_isin"]
        elif file_exists:
            header = read_report_header(Path(local_path))
            fy_start, fy_end = header["fy_start"], header["fy_end"]
            taxonomy, xbrl_isin = header["taxonomy_version"], \
                header["xbrl_isin"]
            identity = ("HEADER_READ" if not header["parse_error"] else
                        "UNREADABLE")
            if not rec["company_id"] and xbrl_isin in by_isin:
                rec["company_id"] = by_isin[xbrl_isin]

        end = parse_date(fy_end)
        if end:
            slot = fy_slot_for_period(parse_date(fy_start), end)
            slot_basis = "XBRL_PERIOD"
        else:
            est = estimated_period_end(rec["api_fy_from"], rec["api_fy_to"])
            slot, slot_basis = (slot_for_date(est), "API_TAG_ESTIMATE") \
                if est else (None, "UNKNOWN")

        if filename in nifty_parsed:
            processing = "EXTRACTED_CURRENT_PIPELINE"
        elif filename in rss_parsed:
            processing = "EXTRACTED_HISTORICAL_RSS_RUN"
        elif download_status == "FAILED":
            processing = "DOWNLOAD_FAILED"
        elif file_exists:
            processing = "DOWNLOADED_NOT_PROCESSED"
        elif not url:
            processing = "NO_XBRL_ON_NSE"
        else:
            processing = "NOT_DOWNLOADED"
        parsing = ("PARSED" if processing.startswith("EXTRACTED") else
                   "UNREADABLE" if identity == "UNREADABLE" else
                   "NOT_PARSED")

        rows.append({
            "filing_id": rec["filing_id"],
            "company_id": rec["company_id"],
            "filing_symbol": rec["filing_symbol"],
            "api_company_name": rec["api_company_name"],
            "api_fy_from": rec["api_fy_from"],
            "api_fy_to": rec["api_fy_to"],
            "fy_slot": slot_label(slot) if slot is not None else "",
            "fy_slot_basis": slot_basis,
            "reporting_period_start": fy_start,
            "reporting_period_end": fy_end,
            "submission_date": rec["submission_date"],
            "revision_date": rec["revision_date"],
            "xbrl_url": url,
            "original_filename": filename,
            "pdf_url": rec["pdf_url"],
            "xbrl_file_size_api": rec["xbrl_file_size_api"],
            "local_file_path": local_path if file_exists else "",
            "sha256": sha256 if file_exists else "",
            "filing_sources": " | ".join(sorted(rec["sources"])),
            "api_snapshots": " | ".join(sorted(rec["snapshots"])),
            "download_status": download_status,
            "download_note": note,
            "error_details": error,
            "parsing_status": parsing,
            "processing_status": processing,
            "identity_status": identity,
            "xbrl_entity_identifier": xbrl_isin,
            "taxonomy_version": taxonomy,
            "is_voluntary_year": slot == 2021,
            "_slot": slot,
        })

    df = pd.DataFrame(rows)
    df = add_bse_manual_filings(df)

    # ---- versions within company + FY slot ---------------------------------
    df["_sub"] = pd.to_datetime(df["submission_date"], errors="coerce")
    df["_rev"] = pd.to_datetime(df["revision_date"], errors="coerce")
    df = df.sort_values(["company_id", "_slot", "_sub", "_rev", "xbrl_url"])
    df["version_number"] = df.groupby(["company_id", "_slot"],
                                      dropna=False).cumcount() + 1
    df["versions_in_slot"] = df.groupby(["company_id", "_slot"],
                                        dropna=False)["filing_id"].transform(
        "count")
    df["filing_version_status"] = df.apply(
        lambda r: ("ORIGINAL" if r.version_number == 1 else
                   "SUBSEQUENT_FILING") + (
            " (REVISED_ON_NSE)" if r.revision_date else ""), axis=1)

    # latest valid filing per company + slot; NSE filings take precedence
    # over BSE manual downloads of the same period (source priority)
    usable = (df["xbrl_url"].ne("") | df["local_file_path"].ne("")) & \
        ~df["download_status"].eq("FAILED")
    df["_nse"] = df["filing_sources"].str.contains("NSE_").astype(int)
    df["is_latest_valid"] = False
    latest_idx = (df[usable].sort_values(["company_id", "_slot", "_nse",
                                          "_sub", "_rev"])
                  .groupby(["company_id", "_slot"], dropna=False).tail(1)
                  .index)
    df.loc[latest_idx, "is_latest_valid"] = True
    df = df.drop(columns=["_nse"])

    # ---- incremental processing flags ---------------------------------------
    in_scope = df["company_id"].map(scope).fillna(False).astype(bool) & \
        df["_slot"].between(settings.first_fy_slot, last_slot)
    df["in_processing_scope"] = in_scope
    df["needs_download"] = in_scope & df["is_latest_valid"] & \
        df["processing_status"].eq("NOT_DOWNLOADED")
    df["needs_processing"] = in_scope & df["is_latest_valid"] & \
        df["processing_status"].eq("DOWNLOADED_NOT_PROCESSED")

    df = df.drop(columns=["_sub", "_rev", "_slot"])
    df.to_csv(MANIFEST_OUTPUT, index=False, encoding="utf-8-sig")

    summary = []
    for column in ("processing_status", "download_status", "fy_slot",
                   "filing_version_status", "fy_slot_basis",
                   "filing_sources"):
        for value, n in df.groupby(column, dropna=False).size().items():
            summary.append({"dimension": column, "value": value,
                            "filings": n})
    summary.append({"dimension": "in_processing_scope", "value": True,
                    "filings": int(df["in_processing_scope"].sum())})
    summary.append({"dimension": "needs_download", "value": True,
                    "filings": int(df["needs_download"].sum())})
    summary.append({"dimension": "needs_processing", "value": True,
                    "filings": int(df["needs_processing"].sum())})
    pd.DataFrame(summary).to_csv(SUMMARY_OUTPUT, index=False,
                                 encoding="utf-8-sig")
    log.info("filing registry built", extra={"fields": {
        "filings": len(df), "companies": df["company_id"].nunique()}})
    return df


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh-api", action="store_true",
                        help="fetch a fresh NSE filing index snapshot "
                             "before building (network)")
    args = parser.parse_args()
    if args.refresh_api:
        refresh_api_snapshot()

    df = build()
    print("Filings:", len(df), " companies:", df["company_id"].nunique(),
          " unmapped:", int(df["company_id"].eq("").sum()))
    for column in ("processing_status", "download_status", "fy_slot",
                   "filing_version_status", "fy_slot_basis"):
        print(f"\n{column}:")
        print(df[column].value_counts(dropna=False).to_string())
    print("\nin processing scope:", int(df["in_processing_scope"].sum()),
          " needs_download:", int(df["needs_download"].sum()),
          " needs_processing:", int(df["needs_processing"].sum()))
    print("Outputs:", MANIFEST_OUTPUT, SUMMARY_OUTPUT, sep="\n  ")


if __name__ == "__main__":
    main()
