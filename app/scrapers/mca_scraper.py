"""
MCA (Ministry of Corporate Affairs) identity source adapter.

Access status (probed 2026-11-11):
  * mca.gov.in master-data pages return HTTP 403 (CDN bot protection) - not
    bypassed.
  * data.gov.in publishes MCA "Company Master Data" (CIN, company_name,
    company_status, class_of_company, date_of_incorporation,
    registered_office_address, roc_code, ...). Its API requires a free key
    (api.data.gov.in answers "Authorization field missing" without one).

The adapter supports three legitimate paths:
  1. decode    - the CIN itself encodes listing status, industry (NIC), state
                 / ROC, year of incorporation and company type. Used to
                 cross-check every company in the company master against
                 its XBRL disclosures. No network access needed.
  2. api       - with DATA_GOV_IN_API_KEY and BRSR_DATAGOV_MCA_RESOURCE_IDS
                 set, fetches master data per CIN from data.gov.in.
  3. bulk      - parses ROC-wise "Company Master Data" CSV files downloaded
                 from data.gov.in into settings.mca_bulk_dir.

Outputs (data/processed/registry/):
  mca_identity_validation.csv   per company: decoded CIN + cross-checks
                                (+ data.gov.in fields when available)
  source_access_log.csv         (appended)

Run:
    python -m app.scrapers.mca_scraper validate
    python -m app.scrapers.mca_scraper probe
"""

import argparse
import re
from pathlib import Path

import pandas as pd

from app.config import settings
from app.core.http_client import AccessDenied, HttpClient
from app.core.logging_setup import get_logger
from app.scrapers.bse_scraper import log_access
from app.validators.brsr_report_verifier import (   # canonical CIN rules
    CIN_PATTERN, is_real_cin, placeholder_reasons)


log = get_logger(__name__, run_name="mca")

REGISTRY_DIR = settings.processed_dir / "registry"
VALIDATION_OUTPUT = REGISTRY_DIR / "mca_identity_validation.csv"

# CIN state / ROC codes -> state name (older ROC codes PN, TZ included)
STATE_CODES = {
    "AN": "Andaman and Nicobar", "AP": "Andhra Pradesh",
    "AR": "Arunachal Pradesh", "AS": "Assam", "BR": "Bihar",
    "CH": "Chandigarh", "CT": "Chhattisgarh", "CG": "Chhattisgarh",
    "DL": "Delhi", "DN": "Dadra and Nagar Haveli", "DD": "Daman and Diu",
    "GA": "Goa", "GJ": "Gujarat", "HP": "Himachal Pradesh",
    "HR": "Haryana", "JH": "Jharkhand", "JK": "Jammu and Kashmir",
    "KA": "Karnataka", "KL": "Kerala", "LA": "Ladakh",
    "LD": "Lakshadweep", "MH": "Maharashtra", "ML": "Meghalaya",
    "MN": "Manipur", "MP": "Madhya Pradesh", "MZ": "Mizoram",
    "NL": "Nagaland", "OR": "Odisha", "PB": "Punjab", "PY": "Puducherry",
    "RJ": "Rajasthan", "SK": "Sikkim", "TG": "Telangana",
    "TS": "Telangana", "TN": "Tamil Nadu", "TR": "Tripura",
    "UP": "Uttar Pradesh", "UK": "Uttarakhand", "UR": "Uttarakhand",
    "WB": "West Bengal", "PN": "Maharashtra (ROC Pune)",
    "TZ": "Tamil Nadu (ROC Coimbatore)",
}
# Words (state names, abbreviations, major cities) that identify the state
# in a registered office address
STATE_WORDS = {
    "Maharashtra": ["maharashtra", "(m.s.)", "mumbai", "pune", "nagpur",
                    "nashik", "thane", "aurangabad", "sambhajinagar"],
    "Maharashtra (ROC Pune)": ["maharashtra", "(m.s.)", "pune",
                               "pimpri", "chinchwad"],
    "Tamil Nadu": ["tamil nadu", "chennai", "coimbatore", "hosur"],
    "Tamil Nadu (ROC Coimbatore)": ["tamil nadu", "coimbatore", "hosur"],
    "Delhi": ["delhi"],
    "Gujarat": ["gujarat", "ahmedabad", "vadodara", "gandhinagar", "surat"],
    "Karnataka": ["karnataka", "bengaluru", "bangalore", "bangaluru",
                  "mysuru", "mangaluru"],
    "Kerala": ["kerala", "kochi", "ernakulam", "thiruvananthapuram",
               "thrissur"],
    "Telangana": ["telangana", "hyderabad", "secunderabad"],
    "West Bengal": ["west bengal", "kolkata"],
    "Haryana": ["haryana", "gurugram", "gurgaon", "faridabad"],
    "Uttar Pradesh": ["uttar pradesh", "noida", "lucknow"],
    "Dadra and Nagar Haveli": ["dadra", "nagar haveli", "silvassa"],
    "Odisha": ["odisha", "orissa", "bhubaneswar"],
    "Rajasthan": ["rajasthan", "jaipur", "udaipur"],
    "Punjab": ["punjab", "ludhiana"],
}
COMPANY_TYPES = {
    "PLC": "Public limited company", "PTC": "Private limited company",
    "GOI": "Government of India company", "SGC": "State government company",
    "FLC": "Financial lease company", "GAP": "Public company limited by "
    "guarantee", "GAT": "Private company limited by guarantee",
    "NPL": "Not-for-profit (Section 8) company", "ULL": "Unlimited public "
    "company", "ULT": "Unlimited private company",
    "OPC": "One person company",
}




def decode_cin(cin):
    cin = (cin or "").strip().upper()
    match = CIN_PATTERN.match(cin)
    reasons = placeholder_reasons(cin)
    if not match:
        return {"cin_format_valid": False,
                "cin_is_placeholder": bool(reasons),
                "cin_placeholder_reasons": "; ".join(reasons)}
    nic, state, year, kind, registration = match.groups()
    return {
        "cin_format_valid": True,
        "cin_is_placeholder": bool(reasons),
        "cin_placeholder_reasons": "; ".join(reasons),
        "cin_listing_flag": "LISTED" if cin[0] == "L" else "UNLISTED",
        "cin_nic_code": nic,
        "cin_state_code": state,
        "cin_state": STATE_CODES.get(state, "UNKNOWN_CODE"),
        "cin_incorporation_year": int(year),
        "cin_company_type_code": kind,
        "cin_company_type": COMPANY_TYPES.get(kind, "OTHER"),
        "cin_registration_number": registration,
    }


# ==========================================================
# data.gov.in (optional)
# ==========================================================

def datagov_lookup(cins):
    """Fetch MCA master data rows per CIN from data.gov.in (needs key)."""
    key, resources = settings.datagov_api_key, \
        settings.datagov_mca_resource_ids
    if not key or not resources:
        log_access("MCA_DATAGOV", settings.datagov_api_base, "",
                   "NOT_CONFIGURED",
                   "set DATA_GOV_IN_API_KEY and "
                   "BRSR_DATAGOV_MCA_RESOURCE_IDS to enable")
        return {}
    client = HttpClient(cache_namespace="datagov")
    found = {}
    for cin in cins:
        for resource in resources:
            url = f"{settings.datagov_api_base}/{resource}"
            params = {"api-key": key, "format": "json", "limit": 1,
                      f"filters[{settings.datagov_cin_field}]": cin}
            try:
                payload = client.get(url, params=params,
                                     use_cache=True).json()
            except AccessDenied as error:
                log_access("MCA_DATAGOV", url, 403, "DENIED", str(error))
                return found
            except Exception as error:
                # invalid key (400/401), unknown resource, non-JSON reply:
                # record and stop - validation continues without MCA data
                response = getattr(error, "response", None)
                log_access("MCA_DATAGOV", url,
                           getattr(response, "status_code", ""),
                           "ERROR_STOPPED",
                           (getattr(response, "text", "") or str(error))
                           [:200])
                return found
            if not isinstance(payload, dict):
                log_access("MCA_DATAGOV", url, 200, "UNEXPECTED_PAYLOAD",
                           str(payload)[:200])
                return found
            records = payload.get("records", [])
            if records:
                found[cin] = {f"mca_{k}": v for k, v in records[0].items()}
                break
    log_access("MCA_DATAGOV", settings.datagov_api_base, 200, "ACCESSIBLE",
               f"matched {len(found)} of {len(cins)} CINs")
    return found


def bulk_lookup(cins):
    """Match CINs in ROC-wise company master CSVs placed in mca_bulk_dir."""
    folder = settings.mca_bulk_dir
    files = sorted(Path(folder).glob("*.csv")) if Path(folder).exists() \
        else []
    if not files:
        return {}
    wanted, found = set(cins), {}
    for path in files:
        for chunk in pd.read_csv(path, dtype=str, chunksize=200_000,
                                 encoding_errors="replace"):
            chunk.columns = [re.sub(r"[^a-z0-9]+", "_", c.strip().lower())
                             .strip("_") for c in chunk.columns]
            cin_col = next((c for c in chunk.columns if c in (
                "cin", "corporate_identification_number",
                "corporate_identity_number")), None)
            if cin_col is None:
                break
            hits = chunk[chunk[cin_col].str.upper().isin(wanted)]
            for r in hits.fillna("").to_dict("records"):
                found[r[cin_col].upper()] = {f"mca_{k}": v
                                             for k, v in r.items()}
                found[r[cin_col].upper()]["mca_source_file"] = path.name
    log_access("MCA_BULK", str(folder), "", "LOCAL_FILES",
               f"{len(files)} file(s); matched {len(found)} CINs")
    return found


# ==========================================================
# validation against the company master
# ==========================================================

def validate():
    master = pd.read_csv(REGISTRY_DIR / "company_master.csv", dtype=str,
                         keep_default_na=False)
    rows = []
    for r in master.to_dict("records"):
        if not r["cin"]:
            continue
        d = decode_cin(r["cin"])
        row = {"company_id": r["company_id"], "nse_symbol": r["nse_symbol"],
               "legal_name": r["legal_name"], "cin": r["cin"],
               "cin_status_in_master": r["cin_status"], **d}
        checks = []
        if d.get("cin_is_placeholder"):
            checks.append("PLACEHOLDER_CIN (" + d["cin_placeholder_reasons"]
                          + ")")
        elif not d.get("cin_format_valid"):
            checks.append("CIN_FORMAT_INVALID")
        else:
            inc = r["incorporation_date"][:4]
            if inc.isdigit():
                row["xbrl_incorporation_year"] = int(inc)
                checks.append("INCORPORATION_YEAR_MATCH" if int(inc) ==
                              d["cin_incorporation_year"] else
                              f"INCORPORATION_YEAR_DIFFERS (XBRL {inc} vs "
                              f"CIN {d['cin_incorporation_year']} - CIN "
                              "year changes on conversion / re-registration)")
            if r["company_status"] == "LISTED_NSE":
                checks.append("LISTING_FLAG_MATCH" if d["cin_listing_flag"]
                              == "LISTED" else
                              "CIN_SAYS_UNLISTED_BUT_LISTED_ON_NSE")
            address = r["registered_office"].lower()
            words = STATE_WORDS.get(d["cin_state"],
                                    [d["cin_state"].split(" (")[0].lower()])
            if address:
                checks.append("STATE_MATCHES_REGISTERED_OFFICE" if any(
                    w in address for w in words) else
                    "STATE_NOT_FOUND_IN_REGISTERED_OFFICE (office may have "
                    "moved; CIN keeps the original ROC)")
        row["checks"] = " | ".join(checks)
        rows.append(row)

    df = pd.DataFrame(rows)
    real = df[df["cin_format_valid"].astype(bool)
              & ~df["cin_is_placeholder"].astype(bool)]["cin"].tolist()
    extra = datagov_lookup(real)
    extra.update({k: v for k, v in bulk_lookup(real).items()
                  if k not in extra})
    if extra:
        enrich = pd.DataFrame.from_dict(extra, orient="index")
        df = df.merge(enrich, left_on="cin", right_index=True, how="left")
    df["mca_external_data"] = df["cin"].isin(extra.keys())
    df.to_csv(VALIDATION_OUTPUT, index=False, encoding="utf-8-sig")
    return df


def probe():
    """Record the accessibility of the MCA portal and data.gov.in API."""
    client = HttpClient(cache_namespace="mca")
    for source, url in [
            ("MCA_PORTAL", "https://www.mca.gov.in/content/mca/global/en/"
                           "mca/master-data/MDS.html"),
            ("MCA_DATAGOV", f"{settings.datagov_api_base}/"
                            "probe?format=json&limit=1")]:
        try:
            response = client.get(url)
            log_access(source, url, response.status_code, "ACCESSIBLE", "")
            print(source, "ACCESSIBLE")
        except AccessDenied as error:
            log_access(source, url, 403, "BLOCKED_OR_RESTRICTED",
                       str(error))
            print(source, "BLOCKED_OR_RESTRICTED (403)")
        except Exception as error:
            text = getattr(getattr(error, "response", None), "text", "")
            status = getattr(getattr(error, "response", None),
                             "status_code", "")
            reason = ("API_KEY_REQUIRED" if "Authorization" in (text or "")
                      else "ERROR")
            log_access(source, url, status, reason, (text or str(error))[:200])
            print(source, reason, status)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["validate", "probe"])
    args = parser.parse_args()
    if args.command == "probe":
        probe()
        return
    df = validate()
    print("Companies with a CIN:", len(df))
    flat = df["checks"].str.split(r" \| ").explode().str.split(" ").str[0]
    print(flat.value_counts().to_string())
    print("External MCA data rows:", int(df["mca_external_data"].sum()))
    print("Output:", VALIDATION_OUTPUT)


if __name__ == "__main__":
    main()
