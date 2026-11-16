"""
BSE source adapter for BRSR filings.

Access status (probed 2026-11-11): every BSE API endpoint behind the
"Get Quote > BRSR" tab (api.bseindia.com/BseIndiaAPI/api/XbrlBrsrDetails/w,
SHP_BRSR_ng/w, even ComHeadernew/w) returns HTTP 403 "Access Denied" from the
CDN bot protection, also after a normal browser-style session warm-up. This
is an access restriction and is NOT bypassed (no browser automation, no
header spoofing beyond a standard client). The adapter therefore:

  1. probe        - one request to the BSE BRSR API; records the result in
                    the source access log and stops at the first 403 instead
                    of repeating blocked requests.
  2. manual import- registers BRSR XBRL files that a person downloaded from
                    the BSE website into settings.bse_manual_dir. Each file
                    is identified from its own XBRL header (ISIN / CIN /
                    scrip code / reporting period) and linked to the company
                    master; filings that duplicate an NSE filing of the same
                    company and period are flagged, never double counted.
  3. requests     - writes the list of company-years missing on NSE with the
                    BSE BRSR page to download them from manually.

Outputs (data/processed/registry/):
  bse_filings.csv                 manually imported BSE filings
  bse_manual_download_requests.csv
  source_access_log.csv           (appended)

Run:
    python -m app.scrapers.bse_scraper probe
    python -m app.scrapers.bse_scraper import-manual
    python -m app.scrapers.bse_scraper requests
"""

import argparse
import csv
import hashlib
from datetime import datetime
from pathlib import Path

import pandas as pd

from app.config import settings
from app.core.http_client import AccessDenied, HttpClient, sha256_file
from app.core.logging_setup import get_logger
from app.validators.brsr_report_verifier import (
    cin_registration, is_real_cin, read_report_header)


log = get_logger(__name__, run_name="bse")

REGISTRY_DIR = settings.processed_dir / "registry"
ACCESS_LOG = REGISTRY_DIR / "source_access_log.csv"
BSE_FILINGS = REGISTRY_DIR / "bse_filings.csv"
BSE_REQUESTS = REGISTRY_DIR / "bse_manual_download_requests.csv"

ACCESS_LOG_FIELDS = ["checked_at", "source", "endpoint", "http_status",
                     "access_status", "detail"]


def log_access(source, endpoint, status, access_status, detail):
    REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
    new = not ACCESS_LOG.exists()
    with open(ACCESS_LOG, "a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=ACCESS_LOG_FIELDS)
        if new:
            writer.writeheader()
        writer.writerow({"checked_at": datetime.now().isoformat(
            timespec="seconds"), "source": source, "endpoint": endpoint,
            "http_status": status, "access_status": access_status,
            "detail": detail})


def bse_client():
    return HttpClient(warmup_url="https://www.bseindia.com/",
                      referer="https://www.bseindia.com/",
                      cache_namespace="bse",
                      extra_headers={"Origin": "https://www.bseindia.com",
                                     "Accept": "application/json, text/plain,"
                                               " */*"})


# ==========================================================
# 1. probe
# ==========================================================

def probe(scrip_code="500114"):
    """One request to the BSE BRSR API; result goes to the access log."""
    endpoint = (f"{settings.bse_api_base}{settings.bse_brsr_detail_path}"
                f"?scripcode={scrip_code}")
    client = bse_client()
    try:
        response = client.get(endpoint)
        try:
            payload = response.json()
            rows = payload.get("Table", payload) if isinstance(
                payload, dict) else payload
            count = len(rows) if hasattr(rows, "__len__") else ""
        except ValueError:
            count = "non-JSON"
        log_access("BSE", endpoint, response.status_code, "ACCESSIBLE",
                   f"rows={count}")
        return "ACCESSIBLE", response
    except AccessDenied as error:
        log_access("BSE", endpoint, 403, "BLOCKED_BOT_PROTECTION",
                   f"{error}; use manual import")
        log.warning("BSE API blocked - using manual import path",
                    extra={"fields": {"endpoint": endpoint}})
        return "BLOCKED_BOT_PROTECTION", None
    except Exception as error:
        log_access("BSE", endpoint, "", "ERROR", str(error))
        return "ERROR", None


# ==========================================================
# 2. manual import
# ==========================================================

def _slot_for(start, end):
    from app.discovery.filing_registry import fy_slot_for_period
    from app.discovery.company_master import parse_date
    from app.scrapers.nse_brsr_filing_index import slot_label
    slot = fy_slot_for_period(parse_date(start), parse_date(end))
    return slot_label(slot) if slot is not None else ""


def import_manual():
    master = pd.read_csv(REGISTRY_DIR / "company_master.csv", dtype=str,
                         keep_default_na=False)
    registry = pd.read_csv(REGISTRY_DIR / "filing_manifest.csv", dtype=str,
                           keep_default_na=False)
    by_isin = dict(zip(master["isin"], master["company_id"]))
    isin_of = dict(zip(master["company_id"], master["isin"]))
    by_bse = dict(zip(master["bse_scrip_code"], master["company_id"]))
    by_cin_reg = {}
    for r in master.to_dict("records"):
        if is_real_cin(r["cin"]):
            by_cin_reg[cin_registration(r["cin"])] = r["company_id"]

    nse_by_key = {}
    for r in registry.to_dict("records"):
        if r["sha256"]:
            nse_by_key.setdefault(r["sha256"], r)
        nse_by_key.setdefault((r["company_id"], r["fy_slot"]), r)

    folder = settings.bse_manual_dir
    folder.mkdir(parents=True, exist_ok=True)
    rows = []
    seen_sha = {}            # sha256 -> first file name (duplicate copies)
    for path in sorted(folder.rglob("*.xml")):
        header = read_report_header(path)
        sha = sha256_file(path)
        ident = header["xbrl_isin"].strip()
        bse_code = header["xbrl_bse_code"].strip()
        company, basis = "", ""
        # blank identifiers never match (several companies have no ISIN /
        # BSE code, so a blank lookup would hit an arbitrary company)
        if ident and ident in by_isin:
            company, basis = by_isin[ident], "ISIN"
        elif is_real_cin(header["xbrl_cin"]) and cin_registration(
                header["xbrl_cin"]) in by_cin_reg:
            company, basis = by_cin_reg[cin_registration(
                header["xbrl_cin"])], "CIN"
        elif bse_code and bse_code in by_bse:
            company, basis = by_bse[bse_code], "BSE_SCRIP_CODE"
        slot = _slot_for(header["fy_start"], header["fy_end"])

        same_file = nse_by_key.get(sha)
        same_period = nse_by_key.get((company, slot)) if company else None
        # matched by CIN / BSE code while the file carries a different
        # ISIN: never assume, keep for manual review
        conflict = bool(company and basis != "ISIN" and ident
                        and ident.startswith("IN")
                        and ident != isin_of.get(company, ""))
        if sha in seen_sha:
            relation = f"DUPLICATE_OF_MANUAL_FILE {seen_sha[sha]}"
        elif header["parse_error"]:
            relation = "NOT_XBRL_OR_UNREADABLE"
        elif not company:
            relation = "COMPANY_NOT_IDENTIFIED"
        elif conflict:
            relation = (f"IDENTIFIER_CONFLICT file ISIN {ident} vs "
                        f"{isin_of.get(company) or 'no ISIN'} matched by "
                        f"{basis}")
        elif same_file is not None:
            relation = f"IDENTICAL_TO_NSE_FILING {same_file['filing_id']}"
        elif same_period is not None and same_period["xbrl_url"]:
            relation = (f"SAME_COMPANY_PERIOD_AS_NSE_FILING "
                        f"{same_period['filing_id']} (NSE preferred)")
        else:
            relation = "NOT_ON_NSE"

        duplicate = sha in seen_sha
        seen_sha.setdefault(sha, path.name)
        rows.append({
            "filing_id": "F-BSE-" + sha[:16] + (
                f"-DUP{sum(1 for r in rows if r['sha256'] == sha)}"
                if duplicate else ""),
            # only identified, readable, non-duplicate files enter the
            # filing registry; the rest stay here for manual review
            "registrable": relation.startswith((
                "IDENTICAL_TO_NSE", "SAME_COMPANY_PERIOD", "NOT_ON_NSE")),
            "company_id": company,
            "match_basis": basis or "UNMATCHED",
            "fy_slot": slot,
            "reporting_period_start": header["fy_start"],
            "reporting_period_end": header["fy_end"],
            "xbrl_entity_identifier": ident,
            "xbrl_cin": header["xbrl_cin"],
            "xbrl_company_name": header["xbrl_company_name"],
            "xbrl_bse_code": header["xbrl_bse_code"],
            "taxonomy_version": header["taxonomy_version"],
            "parse_error": header["parse_error"],
            "local_file_path": str(path),
            "sha256": sha,
            "filing_source": "BSE_MANUAL_DOWNLOAD",
            "relation_to_nse": relation,
            "imported_at": datetime.now().isoformat(timespec="seconds"),
        })

    df = pd.DataFrame(rows, columns=[
        "filing_id", "registrable", "company_id", "match_basis", "fy_slot",
        "reporting_period_start", "reporting_period_end",
        "xbrl_entity_identifier", "xbrl_cin", "xbrl_company_name",
        "xbrl_bse_code", "taxonomy_version", "parse_error",
        "local_file_path", "sha256", "filing_source", "relation_to_nse",
        "imported_at"])
    df.to_csv(BSE_FILINGS, index=False, encoding="utf-8-sig")
    log.info("BSE manual import", extra={"fields": {
        "files": len(df), "folder": str(folder)}})
    return df


# ==========================================================
# 3. manual download requests for NSE gaps
# ==========================================================

def download_requests():
    """Company-years expected but not available as XBRL on NSE."""
    master = pd.read_csv(REGISTRY_DIR / "company_master.csv", dtype=str,
                         keep_default_na=False).set_index("company_id")
    coverage = pd.read_csv(settings.processed_dir / "nifty100"
                           / "nse_brsr_coverage_matrix.csv", dtype=str,
                           keep_default_na=False)
    wanted = {"NOT_FOUND_ON_NSE", "XBRL_LINK_BROKEN_ON_NSE",
              "XBRL_MALFORMED_ON_NSE", "UNEXPLAINED_MISSING"}
    rows = []
    for r in coverage[coverage["status"].isin(wanted)].to_dict("records"):
        company_id = f"C-{r['isin']}"
        m = master.loc[company_id] if company_id in master.index else {}
        code = m.get("bse_scrip_code", "") if len(m) else ""
        rows.append({
            "company_id": company_id,
            "company_name": r["company_name"],
            "nse_symbol": r["nse_symbol"],
            "bse_scrip_code": code,
            "fy_slot": r["fy_slot"],
            "nse_status": r["status"],
            "bse_brsr_page": settings.bse_brsr_page.format(
                symbol=r["nse_symbol"].lower(), code=code) if code else "",
            "instruction": "Open the BSE BRSR page, download the XBRL for "
                           "this financial year and save it in "
                           f"{settings.bse_manual_dir}; then run "
                           "'python -m app.scrapers.bse_scraper "
                           "import-manual'.",
        })
    df = pd.DataFrame(rows)
    df.to_csv(BSE_REQUESTS, index=False, encoding="utf-8-sig")
    return df


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["probe", "import-manual",
                                            "requests"])
    parser.add_argument("--scrip-code", default="500114")
    args = parser.parse_args()

    if args.command == "probe":
        status, _ = probe(args.scrip_code)
        print("BSE BRSR API access:", status)
        print("Logged in", ACCESS_LOG)
    elif args.command == "import-manual":
        df = import_manual()
        print("Manually downloaded BSE files imported:", len(df))
        if len(df):
            print(df["relation_to_nse"].str.split(" ").str[0]
                  .value_counts().to_string())
        print("Output:", BSE_FILINGS)
    else:
        df = download_requests()
        print("Company-years to fetch manually from BSE:", len(df))
        if len(df):
            print(df[["nse_symbol", "fy_slot", "nse_status",
                      "bse_scrip_code"]].to_string(index=False))
        print("Output:", BSE_REQUESTS)


if __name__ == "__main__":
    main()
