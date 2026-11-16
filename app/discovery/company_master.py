"""
Unified company master.

Builds one record per company from every source already in the project,
without a hardcoded company list:

  * NSE BRSR filing API snapshots  -> every company that filed BRSR on NSE
  * processing universe file       -> settings.company_universe_file
                                      (currently the Nifty 100 list)
  * historical RSS run             -> data/processed/multi_company_details.csv
  * NSE equity master              -> current symbol, ISIN, series, listing
  * NSE symbol / name change files -> symbol and name history
  * BSE scrip master               -> BSE scrip code by ISIN
  * verified XBRL headers / facts  -> legal name, CIN, address, contacts

Outputs (data/processed/registry/):
  company_master.csv           one row per company_id
  company_identifiers.csv      ISIN / CIN / symbol / name history
  company_exchange_listings.csv NSE and BSE listings

company_id = "C-<ISIN>" for companies resolved to a current NSE ISIN, else
"C-SYM-<symbol>" (unresolved symbols are kept and flagged, never dropped).

Run:
    python -m app.discovery.company_master
"""

import csv
import glob
import io
import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import pandas as pd

from app.config import settings
from app.core.logging_setup import get_logger
from app.validators.brsr_report_verifier import (   # canonical CIN rules
    is_real_cin, normalise_name, placeholder_reasons)


log = get_logger(__name__, run_name="registry")

REGISTRY_DIR = settings.processed_dir / "registry"
NIFTY_DIR = settings.processed_dir / "nifty100"

NSE_EQUITY_FILE = settings.raw_dir / "reference" / "NSE_EQUITY_L.csv"
BSE_SCRIP_GLOB = str(settings.raw_dir / "reference" / "bse" / "extracted"
                     / "SCRIP" / "BSE_EQ_SCRIP_*.csv")
NSE_REFERENCE_ROOT = settings.raw_dir / "reference" / "nse"
API_SNAPSHOT_ROOT = settings.raw_dir / "nse_brsr_api"
RSS_DETAILS_FILE = settings.processed_dir / "multi_company_details.csv"
BSE_LOOKUP_FILE = settings.processed_dir / "bse_company_lookup_results.csv"

MASTER_OUTPUT = REGISTRY_DIR / "company_master.csv"
IDENTIFIERS_OUTPUT = REGISTRY_DIR / "company_identifiers.csv"
LISTINGS_OUTPUT = REGISTRY_DIR / "company_exchange_listings.csv"


# ==========================================================
# helpers
# ==========================================================

def parse_date(value):
    value = (value or "").strip()
    for fmt in ("%d-%b-%Y", "%d-%B-%Y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def latest_dir(root):
    dirs = sorted(p for p in Path(root).glob("*") if p.is_dir())
    return dirs[-1] if dirs else None


def load_api_rows():
    """All BRSR filing rows from every saved NSE API snapshot."""
    rows = []
    for snap_dir in sorted(p for p in API_SNAPSHOT_ROOT.glob("*")
                           if p.is_dir()):
        for path in sorted(snap_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                log.warning("unreadable snapshot", extra={"fields": {
                    "file": str(path)}})
                continue
            items = data.get("data", []) if isinstance(data, dict) else data
            for item in items or []:
                rows.append({**item, "_snapshot": f"{snap_dir.name}/"
                                                  f"{path.name}"})
    return rows


def load_symbol_changes(path):
    """old_symbol -> [(new_symbol, effective_date, company_name)]."""
    forward, backward = defaultdict(list), defaultdict(list)
    if not path or not path.exists():
        return forward, backward
    reader = csv.reader(io.StringIO(path.read_text(encoding="utf-8",
                                                   errors="replace")))
    next(reader, None)
    for row in reader:
        if len(row) < 4:
            continue
        name, old, new, eff = (row[0].strip(), row[1].strip().upper(),
                               row[2].strip().upper(), parse_date(row[3]))
        if old and new and old != new:
            forward[old].append((new, eff, name))
            backward[new].append((old, eff, name))
    return forward, backward


def load_name_changes(path):
    """symbol -> [(old_name, new_name, effective_date)]."""
    changes = defaultdict(list)
    if not path or not path.exists():
        return changes
    reader = csv.reader(io.StringIO(path.read_text(encoding="utf-8",
                                                   errors="replace")))
    next(reader, None)
    for row in reader:
        if len(row) < 4:
            continue
        changes[row[0].strip().upper()].append(
            (row[1].strip(), row[2].strip(), parse_date(row[3])))
    return changes


def current_symbol(symbol, forward, nse_symbols, max_hops=10):
    """Follow old -> new symbol changes until a current NSE symbol."""
    seen, current = set(), symbol
    for _ in range(max_hops):
        if current in nse_symbols:
            return current
        if current in seen or current not in forward:
            break
        seen.add(current)
        # latest change wins
        current = sorted(forward[current], key=lambda x: x[1] or
                         datetime.min.date())[-1][0]
    return current if current in nse_symbols else None


def final_symbol(symbol, forward, max_hops=10):
    """Last symbol in the old -> new chain (even if no longer listed)."""
    seen, current = set(), symbol
    for _ in range(max_hops):
        if current in seen or current not in forward:
            return current
        seen.add(current)
        current = sorted(forward[current], key=lambda x: x[1] or
                         datetime.min.date())[-1][0]
    return current


def symbol_history(symbol, backward):
    history, queue, seen = [(symbol, None)], [symbol], {symbol}
    while queue:
        cur = queue.pop(0)
        for old, eff, _ in backward.get(cur, []):
            if old not in seen:
                seen.add(old)
                history.append((old, eff))
                queue.append(old)
    return history


# ==========================================================
# build
# ==========================================================

def build():
    REGISTRY_DIR.mkdir(parents=True, exist_ok=True)

    ref_dir = latest_dir(NSE_REFERENCE_ROOT)
    forward, backward = load_symbol_changes(
        ref_dir / "symbolchange.csv" if ref_dir else None)
    name_changes = load_name_changes(
        ref_dir / "namechange.csv" if ref_dir else None)

    nse = pd.read_csv(NSE_EQUITY_FILE, dtype=str).fillna("")
    nse.columns = [c.strip() for c in nse.columns]
    nse_by_symbol = {r["SYMBOL"].strip().upper(): r
                     for r in nse.to_dict("records")}
    nse_symbol_by_isin = {r["ISIN NUMBER"].strip(): s
                          for s, r in nse_by_symbol.items()}

    bse_files = sorted(glob.glob(BSE_SCRIP_GLOB))
    bse = pd.read_csv(bse_files[-1], dtype=str,
                      usecols=["FinInstrmId", "TckrSymb", "SctySrs",
                               "FinInstrmNm", "ISIN", "ListgDt"]
                      ).fillna("") if bse_files else pd.DataFrame()
    bse_by_isin = defaultdict(list)
    for r in bse.to_dict("records"):
        bse_by_isin[r["ISIN"].strip()].append(r)
    bse_verified = {}
    if BSE_LOOKUP_FILE.exists():
        lk = pd.read_csv(BSE_LOOKUP_FILE, dtype=str).fillna("")
        bse_verified = {r["isin"]: r["bse_scrip_code"]
                        for r in lk.to_dict("records")
                        if r["match_status"] == "MATCHED"}

    universe = pd.read_csv(settings.company_universe_file,
                           dtype=str).fillna("")
    universe_isins = set(universe["ISIN Code"].str.strip())
    universe_names = dict(zip(universe["ISIN Code"].str.strip(),
                              universe["Company Name"].str.strip()))
    universe_industry = dict(zip(universe["ISIN Code"].str.strip(),
                                 universe["Industry"].str.strip()))
    scope_filter = {s.upper() for s in settings.company_filter}

    api_rows = load_api_rows()
    api_name_by_symbol = {}
    for row in api_rows:
        api_name_by_symbol.setdefault(row.get("symbol", "").upper(),
                                      row.get("companyName", ""))

    rss = pd.read_csv(RSS_DETAILS_FILE, dtype=str).fillna("") \
        if RSS_DETAILS_FILE.exists() else pd.DataFrame()

    # ---- XBRL-derived identity (verified Nifty 100 reports + RSS run) ----
    verification = pd.read_csv(NIFTY_DIR / "nse_brsr_report_verification"
                               ".csv", dtype=str).fillna("")
    verification = verification.sort_values(["isin", "true_slot"])
    xbrl_identity = {}
    for r in verification.to_dict("records"):
        xbrl_identity[r["isin"]] = {        # latest true slot wins
            "legal_name": r["xbrl_company_name"],
            "cin": r["reference_cin"] or r["xbrl_cin"],
            "cin_source": r["source_file"] if "source_file" in r else
            r["local_file_path"],
            "identity_status": r["identity_status"],
            "latest_report": Path(r["local_file_path"]).name,
            "latest_fy": r["true_fy_slot"],
        }
    # Section A details per report. The "isin" column of the details file is
    # the ISIN *fact* (often absent in older filings), so details are keyed
    # to the company through the report URL -> verified master ISIN.
    details = pd.read_csv(NIFTY_DIR / "multi_company_details.csv",
                          dtype=str).fillna("")
    url_to_isin = dict(zip(verification["report_url"], verification["isin"]))
    url_to_slot = dict(zip(verification["report_url"],
                           verification["true_slot"]))
    details["_master_isin"] = details["source_url"].map(url_to_isin)
    details["_slot"] = details["source_url"].map(url_to_slot)
    details = details.dropna(subset=["_master_isin"]).sort_values(
        ["_master_isin", "_slot"])
    details_by_isin = {}
    for r in details.to_dict("records"):
        details_by_isin[r["_master_isin"]] = r   # latest slot wins
    for r in rss.to_dict("records"):
        if r.get("isin"):
            details_by_isin.setdefault(r["isin"].strip().upper(), r)

    # ---- resolve every symbol / ISIN seen anywhere to a company --------
    symbols = defaultdict(set)              # company_id -> symbols seen
    sources = defaultdict(set)
    company_isin, company_symbol = {}, {}

    def register(symbol=None, isin=None, source=""):
        symbol = (symbol or "").strip().upper()
        isin = (isin or "").strip().upper()
        if not isin and symbol:
            cur = current_symbol(symbol, forward, nse_by_symbol)
            if cur:
                isin = nse_by_symbol[cur]["ISIN NUMBER"].strip()
        if isin and isin in nse_symbol_by_isin:
            company_id = f"C-{isin}"
            company_symbol[company_id] = nse_symbol_by_isin[isin]
        elif isin:
            company_id = f"C-{isin}"
            company_symbol.setdefault(company_id, symbol)
        else:
            # not resolvable to a current ISIN: identify the company by the
            # last symbol of its change chain so GATI and ACLGATI (renamed,
            # later delisted) are one company, not two
            last = final_symbol(symbol, forward)
            company_id = f"C-SYM-{last}"
            company_symbol.setdefault(company_id, last)
        company_isin.setdefault(company_id, isin)
        if symbol:
            symbols[company_id].add(symbol)
        sources[company_id].add(source)
        return company_id

    for r in universe.to_dict("records"):
        register(r["Symbol"], r["ISIN Code"], "UNIVERSE_FILE")
    for row in api_rows:
        register(row.get("symbol"), None, "NSE_BRSR_API")
    for r in rss.to_dict("records"):
        register(r.get("nse_symbol"), r.get("isin"), "RSS_HISTORICAL_RUN")

    # ---- records ----------------------------------------------------------
    masters, identifiers, listings = [], [], []

    for company_id in sorted(company_isin):
        isin = company_isin[company_id]
        symbol = company_symbol.get(company_id, "")
        nse_row = nse_by_symbol.get(symbol, {})
        hist = symbol_history(symbol, backward) if symbol else []
        all_symbols = {s for s, _ in hist} | symbols[company_id]

        # never look up identity data with a blank ISIN
        xid = xbrl_identity.get(isin, {}) if isin else {}
        det = details_by_isin.get(isin, {}) if isin else {}
        cin = xid.get("cin") or det.get("cin", "")
        if not cin:
            cin_status = "NOT_YET_EXTRACTED"
        elif placeholder_reasons(cin):
            # stricter than the verifier: also catches sequential dummy
            # CINs such as L01234KA2023PTC567891
            cin_status = "PLACEHOLDER_IN_FILING"
        elif is_real_cin(cin):
            cin_status = ("VERIFIED_XBRL" if xid else
                          "FROM_XBRL_HISTORICAL_RUN")
        else:
            cin_status = "PLACEHOLDER_IN_FILING"

        bse_rows = bse_by_isin.get(isin, []) if isin else []
        bse_code, bse_status = "", "NOT_FOUND"
        if isin in bse_verified:
            bse_code, bse_status = bse_verified[isin], "MATCHED_VERIFIED"
        elif len(bse_rows) == 1:
            bse_code, bse_status = bse_rows[0]["FinInstrmId"], "MATCHED"
        elif bse_rows:
            same = [b for b in bse_rows if b["TckrSymb"].upper() == symbol]
            if len(same) == 1:
                bse_code, bse_status = same[0]["FinInstrmId"], \
                    "MATCHED_BY_SYMBOL"
            else:
                bse_status = f"AMBIGUOUS_{len(bse_rows)}_RECORDS"
        if not bse_code and det.get("bse_code"):
            bse_code, bse_status = det["bse_code"], "FROM_XBRL"

        legal_name = (xid.get("legal_name") or det.get("company_name")
                      or nse_row.get("NAME OF COMPANY", "")
                      or api_name_by_symbol.get(symbol, ""))
        common_name = ((universe_names.get(isin) if isin else "")
                       or nse_row.get("NAME OF COMPANY", "")
                       or api_name_by_symbol.get(symbol, legal_name))

        if not isin:
            # filed BRSR under a symbol that is no longer in the NSE equity
            # master and has no recorded symbol change (merger, delisting,
            # scheme of arrangement). Kept with its filing history.
            status = "NOT_IN_CURRENT_NSE_MASTER_MERGED_OR_DELISTED"
        elif symbol in nse_by_symbol:
            status = "LISTED_NSE"
        else:
            status = "ISIN_KNOWN_NOT_IN_NSE_EQUITY_MASTER"

        in_universe = isin in universe_isins
        in_scope = in_universe and (not scope_filter
                                    or symbol in scope_filter)

        names_hist = []
        for s in all_symbols:
            for old, new, eff in name_changes.get(s, []):
                names_hist.append((old, new, eff))

        masters.append({
            "company_id": company_id,
            "legal_name": legal_name,
            "common_name": common_name,
            "cin": cin,
            "cin_status": cin_status,
            "isin": isin,
            "nse_symbol": symbol,
            "nse_series": nse_row.get("SERIES", ""),
            "nse_listing_date": (parse_date(nse_row.get("DATE OF LISTING"))
                                 or ""),
            "bse_scrip_code": bse_code,
            "bse_match_status": bse_status,
            "industry": universe_industry.get(isin, ""),
            "incorporation_date": det.get("incorporation_date", ""),
            "registered_office": det.get("registered_office", ""),
            "corporate_address": det.get("corporate_address", ""),
            "email": det.get("email", ""),
            "telephone": det.get("telephone", ""),
            "website": det.get("website", ""),
            "company_status": status,
            "in_processing_universe": in_universe,
            "in_processing_scope": in_scope,
            "is_nse_brsr_filer": "NSE_BRSR_API" in sources[company_id],
            "in_rss_historical_run": "RSS_HISTORICAL_RUN"
                                     in sources[company_id],
            "symbol_history": " | ".join(
                f"{s}<={d}" if d else s for s, d in hist),
            "name_history": " | ".join(
                f"{o} -> {n} ({e})" for o, n, e in sorted(
                    names_hist, key=lambda x: str(x[2]))),
            "latest_verified_report": xid.get("latest_report", ""),
            "latest_verified_fy": xid.get("latest_fy", ""),
            "discovery_sources": " | ".join(sorted(sources[company_id])),
            "related_entities": "",
        })

        # identifiers with history
        if isin:
            identifiers.append({"company_id": company_id,
                                "identifier_type": "ISIN", "value": isin,
                                "valid_from": "", "valid_to": "",
                                "is_current": True,
                                "source": "NSE_EQUITY_L / universe",
                                "notes": ""})
        for s, eff in hist:
            identifiers.append({"company_id": company_id,
                                "identifier_type": "NSE_SYMBOL", "value": s,
                                "valid_from": "", "valid_to": eff or "",
                                "is_current": s == symbol,
                                "source": "NSE symbolchange.csv"
                                if s != symbol else "NSE_EQUITY_L",
                                "notes": ""})
        for s in sorted(symbols[company_id] - {s for s, _ in hist}):
            identifiers.append({"company_id": company_id,
                                "identifier_type": "NSE_SYMBOL", "value": s,
                                "valid_from": "", "valid_to": "",
                                "is_current": s == symbol,
                                "source": "NSE BRSR API filing",
                                "notes": "symbol used in a BRSR filing"})
        for old, new, eff in names_hist:
            identifiers.append({"company_id": company_id,
                                "identifier_type": "LEGAL_NAME",
                                "value": old, "valid_from": "",
                                "valid_to": eff or "", "is_current": False,
                                "source": "NSE namechange.csv",
                                "notes": f"renamed to {new}"})
        if legal_name:
            identifiers.append({"company_id": company_id,
                                "identifier_type": "LEGAL_NAME",
                                "value": legal_name, "valid_from": "",
                                "valid_to": "", "is_current": True,
                                "source": "XBRL NameOfTheCompany" if xid
                                else "NSE_EQUITY_L", "notes": ""})
        if bse_code:
            identifiers.append({"company_id": company_id,
                                "identifier_type": "BSE_SCRIP_CODE",
                                "value": bse_code, "valid_from": "",
                                "valid_to": "", "is_current": True,
                                "source": "BSE scrip master",
                                "notes": bse_status})

        # listings
        if symbol and nse_row:
            listings.append({"company_id": company_id, "exchange": "NSE",
                             "code": symbol, "series":
                             nse_row.get("SERIES", ""), "listing_date":
                             parse_date(nse_row.get("DATE OF LISTING"))
                             or "", "status": "LISTED",
                             "source": "NSE_EQUITY_L"})
        if bse_code:
            listings.append({"company_id": company_id, "exchange": "BSE",
                             "code": bse_code, "series": next(
                                 (b["SctySrs"] for b in bse_rows
                                  if b["FinInstrmId"] == bse_code), ""),
                             "listing_date": next(
                                 (b["ListgDt"] for b in bse_rows
                                  if b["FinInstrmId"] == bse_code), ""),
                             "status": "LISTED", "source": bse_status})

    # CINs observed in verified XBRL reports (incl. registration variants)
    for isin, group in verification.groupby("isin"):
        seen = {}
        for r in group.to_dict("records"):
            c = r["xbrl_cin"]
            if c and c not in seen:
                seen[c] = r
        for c, r in seen.items():
            identifiers.append({
                "company_id": f"C-{isin}", "identifier_type": "CIN",
                "value": c, "valid_from": "", "valid_to": "",
                "is_current": c == xbrl_identity.get(isin, {}).get("cin"),
                "source": f"XBRL {Path(r['local_file_path']).name}",
                "notes": ("placeholder CIN in filing: "
                          + "; ".join(placeholder_reasons(c))
                          if placeholder_reasons(c)
                          else r["identity_status"])})

    master = pd.DataFrame(masters)

    # related entities: different companies sharing a legal / common name
    master["_name_key"] = master["legal_name"].map(normalise_name)
    for key, group in master.groupby("_name_key"):
        if key and len(group) > 1:
            for idx in group.index:
                others = [f"{r.nse_symbol} ({r.company_id})" for r in
                          group.drop(idx).itertuples()]
                master.at[idx, "related_entities"] = (
                    "SAME_NAME_AS: " + ", ".join(others)
                    + " - distinct companies (separate ISIN/CIN)")
    master = master.drop(columns=["_name_key"])

    master.to_csv(MASTER_OUTPUT, index=False, encoding="utf-8-sig")
    # deterministic order: identical inputs give a byte-identical file
    ident = (pd.DataFrame(identifiers).drop_duplicates()
             .sort_values(["company_id", "identifier_type", "valid_from",
                           "valid_to", "value", "source"], kind="stable")
             .reset_index(drop=True))
    ident.to_csv(IDENTIFIERS_OUTPUT, index=False, encoding="utf-8-sig")
    pd.DataFrame(listings).to_csv(LISTINGS_OUTPUT, index=False,
                                  encoding="utf-8-sig")

    log.info("company master built", extra={"fields": {
        "companies": len(master), "identifiers": len(ident),
        "listings": len(listings)}})
    return master, ident, pd.DataFrame(listings)


def main():
    master, ident, listings = build()
    print("Companies               :", len(master))
    print("  in processing universe:", int(master.in_processing_universe
                                            .sum()))
    print("  in processing scope   :", int(master.in_processing_scope.sum()))
    print("  NSE BRSR filers       :", int(master.is_nse_brsr_filer.sum()))
    print("  historical RSS run    :", int(master.in_rss_historical_run
                                            .sum()))
    print("Company status:")
    print(master.company_status.value_counts().to_string())
    print("CIN status:")
    print(master.cin_status.value_counts().to_string())
    print("BSE match:")
    print(master.bse_match_status.value_counts().to_string())
    print("Identifiers:", len(ident), " Listings:", len(listings))
    print("Outputs:", MASTER_OUTPUT, IDENTIFIERS_OUTPUT, LISTINGS_OUTPUT,
          sep="\n  ")


if __name__ == "__main__":
    main()
