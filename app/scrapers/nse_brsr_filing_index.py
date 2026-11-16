"""
NSE BRSR filing discovery for the target company list (Nifty 100).

Builds a filing index from the NSE BRSR JSON API, maps each filing to a
master company using current + historical NSE symbols, and selects the
latest N reporting periods per company for download.

No database access. Outputs are CSV files under data/processed/nifty100/.

Run:
    python -m app.scrapers.nse_brsr_filing_index --years 3
"""

import argparse
import csv
import io
import json

from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from app.config import settings
from app.core.http_client import nse_api_client
from app.core.logging_setup import get_logger


log = get_logger(__name__, run_name="discovery")


# ==========================================================
# CONFIGURATION
# ==========================================================

BASE_DIR = settings.project_root

MASTER_FILE = settings.company_universe_file
BSE_LOOKUP_FILE = (
    BASE_DIR / "data" / "processed" / "bse_company_lookup_results.csv"
)

RUN_DATE = datetime.now().strftime("%Y%m%d")

REFERENCE_DIR = BASE_DIR / "data" / "raw" / "reference" / "nse" / RUN_DATE
API_SNAPSHOT_DIR = BASE_DIR / "data" / "raw" / "nse_brsr_api" / RUN_DATE
OUTPUT_DIR = BASE_DIR / "data" / "processed" / "nifty100"

IDENTITY_OUTPUT = OUTPUT_DIR / "company_identity_master.csv"
INDEX_OUTPUT = OUTPUT_DIR / "nse_brsr_filing_index.csv"
SELECTION_OUTPUT = OUTPUT_DIR / "nse_brsr_filing_selection.csv"
COVERAGE_OUTPUT = OUTPUT_DIR / "nse_brsr_discovery_coverage.csv"

LISTING_PAGE = settings.nse_brsr_listing_page
API_URL = settings.nse_brsr_api
SYMBOL_CHANGE_URL = settings.nse_symbol_change_url
NAME_CHANGE_URL = settings.nse_name_change_url

# Filings submitted under an old symbol are accepted up to this many
# days after the symbol-change effective date.
OLD_SYMBOL_GRACE_DAYS = 45


# ==========================================================
# NSE SESSION
# ==========================================================

class NSESession:
    """
    Thin wrapper kept for compatibility: delegates to the shared HTTP client
    (cookie warm-up, retries with backoff, per-host rate limiting).
    """

    def __init__(self):
        self.client = nse_api_client()
        self.client.warm_up()

    def refresh(self):
        self.client.warm_up()

    def get(self, url, params=None):
        return self.client.get(url, params=params)


# ==========================================================
# STEP 1: REFERENCE FILES (SYMBOL / NAME CHANGES)
# ==========================================================

def download_reference(nse, url, filename):
    REFERENCE_DIR.mkdir(parents=True, exist_ok=True)
    path = REFERENCE_DIR / filename

    if not path.exists():
        response = nse.get(url)
        path.write_bytes(response.content)
        log.info("reference file saved", extra={"fields": {
            "file": str(path), "bytes": len(response.content)}})

    return path.read_text(encoding="utf-8", errors="replace")


def parse_date(value, formats=("%d-%b-%Y", "%d-%B-%Y", "%d-%m-%Y")):
    value = (value or "").strip()

    for fmt in formats:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue

    return None


def load_symbol_changes(text):
    """Return {new_symbol: [(old_symbol, effective_date), ...]}."""

    changes = defaultdict(list)
    reader = csv.reader(io.StringIO(text))
    next(reader, None)

    for row in reader:
        if len(row) < 4:
            continue

        old_symbol = row[1].strip().upper()
        new_symbol = row[2].strip().upper()
        effective = parse_date(row[3])

        if old_symbol and new_symbol and old_symbol != new_symbol:
            changes[new_symbol].append((old_symbol, effective))

    return changes


def symbol_history(symbol, changes):
    """
    Current symbol plus all older symbols, each with the date until which
    filings under that symbol are accepted (None = no limit).
    """

    history = [(symbol, None)]
    seen = {symbol}
    queue = [(symbol, None)]

    while queue:
        current, _ = queue.pop(0)

        for old_symbol, effective in changes.get(current, []):
            if old_symbol in seen:
                continue

            seen.add(old_symbol)
            history.append((old_symbol, effective))
            queue.append((old_symbol, effective))

    return history


# ==========================================================
# STEP 2: COMPANY IDENTITY MASTER
# ==========================================================

def build_identity_master(changes):
    master = pd.read_csv(MASTER_FILE, dtype=str).fillna("")

    bse_codes = {}
    if BSE_LOOKUP_FILE.exists():
        bse = pd.read_csv(BSE_LOOKUP_FILE, dtype=str).fillna("")
        bse_codes = dict(zip(bse["isin"], bse["bse_scrip_code"]))

    records = []

    for _, row in master.iterrows():
        symbol = row["Symbol"].strip().upper()
        history = symbol_history(symbol, changes)

        records.append({
            "company_name": row["Company Name"].strip(),
            "industry": row["Industry"].strip(),
            "isin": row["ISIN Code"].strip().upper(),
            "nse_symbol": symbol,
            "bse_code": bse_codes.get(row["ISIN Code"].strip().upper(), ""),
            "symbol_history": " | ".join(
                f"{s}<={d.date().isoformat()}" if d else s
                for s, d in history
            ),
            "_history": history,
        })

    return records


# ==========================================================
# STEP 3: FILING INDEX FROM NSE API
# ==========================================================

def save_snapshot(name, response):
    API_SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    (API_SNAPSHOT_DIR / f"{name}.json").write_bytes(response.content)


def fetch_rows(nse, params, snapshot_name):
    # rate limiting between calls is handled by the shared HTTP client
    response = nse.get(API_URL, params=params)
    save_snapshot(snapshot_name, response)
    data = response.json()

    if isinstance(data, dict):
        data = data.get("data", [])

    return data or []


def fy_windows(from_date, to_date):
    """Split a date range into April-March submission windows."""

    windows = []
    start = from_date

    while start <= to_date:
        year = start.year if start.month >= 4 else start.year - 1
        end = min(datetime(year + 1, 3, 31), to_date)
        windows.append((start, end))
        start = end + timedelta(days=1)

    return windows


def fetch_filing_index(nse, companies, from_date, to_date):
    rows_by_url = {}

    def add(rows, source):
        for row in rows:
            url = (row.get("xbrlFile") or "").strip()
            key = url or (
                f"{row.get('symbol')}|{row.get('fyFrom')}|"
                f"{row.get('fyTo')}|{row.get('submissionDate')}"
            )
            if key not in rows_by_url:
                rows_by_url[key] = {**row, "discovered_via": source}

    # Bulk windows: cheap and include filings under retired symbols.
    for start, end in fy_windows(from_date, to_date):
        params = {
            "index": "equities",
            "from_date": start.strftime("%d-%m-%Y"),
            "to_date": end.strftime("%d-%m-%Y"),
        }
        print("Bulk window:", params["from_date"], "to", params["to_date"])
        rows = fetch_rows(
            nse, params,
            f"bulk_{start:%Y%m%d}_{end:%Y%m%d}"
        )
        print("  rows:", len(rows))
        add(rows, "bulk_window")

    # Per-symbol cross-check for every symbol in each company's history.
    symbols = sorted({s for c in companies for s, _ in c["_history"]})

    for index, symbol in enumerate(symbols, start=1):
        params = {
            "index": "equities",
            "symbol": symbol,
            "from_date": from_date.strftime("%d-%m-%Y"),
            "to_date": to_date.strftime("%d-%m-%Y"),
        }
        print(f"Symbol {index}/{len(symbols)}: {symbol}")
        try:
            rows = fetch_rows(nse, params, f"symbol_{symbol}")
        except Exception as error:
            print("  failed:", error)
            continue
        print("  rows:", len(rows))
        add(rows, "symbol_query")

    return list(rows_by_url.values())


# ==========================================================
# STEP 4: MATCH FILINGS TO COMPANIES
# ==========================================================

def match_filings(companies, filings):
    by_symbol = defaultdict(list)
    for filing in filings:
        by_symbol[(filing.get("symbol") or "").strip().upper()].append(filing)

    matched = []

    for company in companies:
        for symbol, valid_until in company["_history"]:
            for filing in by_symbol.get(symbol, []):
                submitted = parse_date(filing.get("submissionDate"))

                if (
                    valid_until
                    and submitted
                    and submitted > valid_until
                    + timedelta(days=OLD_SYMBOL_GRACE_DAYS)
                ):
                    match_basis = "OLD_SYMBOL_AFTER_CHANGE_REJECTED"
                elif symbol == company["nse_symbol"]:
                    match_basis = "CURRENT_SYMBOL"
                else:
                    match_basis = "HISTORICAL_SYMBOL"

                matched.append({
                    "master_company_name": company["company_name"],
                    "master_isin": company["isin"],
                    "master_symbol": company["nse_symbol"],
                    "filing_symbol": symbol,
                    "match_basis": match_basis,
                    "api_company_name": filing.get("companyName", ""),
                    "api_fy_from": filing.get("fyFrom", ""),
                    "api_fy_to": filing.get("fyTo", ""),
                    "submission_date": filing.get("submissionDate", ""),
                    "revision_date": filing.get("revisionDate", ""),
                    "xbrl_url": filing.get("xbrlFile", ""),
                    "xbrl_file_size": filing.get("xbrlFileSize", ""),
                    "pdf_url": filing.get("attachmentFile", ""),
                    "pdf_file_size": filing.get("attFileSize", ""),
                    "discovered_via": filing.get("discovered_via", ""),
                })

    return matched


# ==========================================================
# STEP 5: FY SLOTS, SELECTION AND COVERAGE
# ==========================================================
#
# A "FY slot" is the April-March year in which a reporting period ENDS.
# This lets April-March, calendar-year and transition-period filers sit in
# one company x year matrix. Pre-download, the period end is estimated from
# the API fyFrom/fyTo tags; after download the verifier recomputes the slot
# from the XBRL DateOfStart/EndOfFinancialYear facts.

FIRST_BRSR_SLOT = 2021          # FY2021-22, voluntary year
VOLUNTARY_SLOTS = {2021}

# Days after the end of a reporting period by which the BRSR is expected
# on NSE (filed with the annual report; AGM within 6 months of year end).
FILING_DUE_DAYS = 214


def slot_label(slot_start_year):
    return f"{slot_start_year}-{str(slot_start_year + 1)[-2:]}"


def slot_end(slot_start_year):
    return datetime(slot_start_year + 1, 3, 31)


def slot_for_date(date):
    return date.year if date.month >= 4 else date.year - 1


def estimated_period_end(fy_from, fy_to):
    """Estimate the period end from API tags (calendar-year if equal)."""

    try:
        fy_from, fy_to = int(fy_from), int(fy_to)
    except (TypeError, ValueError):
        return None

    if fy_from == fy_to:
        return datetime(fy_to, 12, 31)

    return datetime(fy_to, 3, 31)


def latest_completed_slot(today):
    """Latest April-March year that has ended."""

    return slot_for_date(today) - 1


def prepare_index(matched):
    df = pd.DataFrame(matched)

    df["api_period"] = (
        df["api_fy_from"].astype(str) + "-" + df["api_fy_to"].astype(str)
    )
    df["has_xbrl"] = df["xbrl_url"].str.lower().str.endswith(".xml")

    ends = [
        estimated_period_end(a, b)
        for a, b in zip(df["api_fy_from"], df["api_fy_to"])
    ]
    df["estimated_period_end"] = [
        e.date().isoformat() if e else "" for e in ends
    ]
    df["estimated_slot"] = [slot_for_date(e) if e else None for e in ends]
    df["estimated_fy_slot"] = [
        slot_label(s) if s is not None else "" for s in df["estimated_slot"]
    ]
    df["voluntary_year"] = df["estimated_slot"].isin(VOLUNTARY_SLOTS)

    df["_submitted"] = df["submission_date"].map(parse_date)
    df = df.sort_values(
        ["master_isin", "estimated_slot", "_submitted"],
        ascending=[True, False, False],
    )

    usable = (
        (df["match_basis"] != "OLD_SYMBOL_AFTER_CHANGE_REJECTED")
        & df["has_xbrl"]
    )

    # Latest usable submission per company + slot is the active filing.
    df["is_active_for_slot"] = False
    active_idx = (
        df[usable]
        .drop_duplicates(["master_isin", "estimated_slot"], keep="first")
        .index
    )
    df.loc[active_idx, "is_active_for_slot"] = True

    counts = df[usable].groupby(["master_isin", "estimated_slot"]).size()
    df["filings_in_slot"] = [
        counts.get((i, s), 0)
        for i, s in zip(df["master_isin"], df["estimated_slot"])
    ]

    return df.drop(columns=["_submitted"])


def select_for_download(index_df, years, today, coverage):
    """
    Active filings in the target slots, plus a buffer filing from the slot
    just before the window for companies with an unexplained gap inside
    it. The API tags place non-April-March filers (e.g. October-September)
    one slot early; the verifier assigns the true slot from XBRL dates.
    """

    last = latest_completed_slot(today)
    target = set(range(last - years + 1, last + 1))
    buffer_slot = min(target) - 1

    active = index_df[index_df["is_active_for_slot"]]

    selection = active[active["estimated_slot"].isin(target)].copy()
    selection["selection_reason"] = "TARGET_SLOT"

    target_labels = {slot_label(s) for s in target}
    gap_isins = set(
        coverage[
            coverage["fy_slot"].isin(target_labels)
            & coverage["status"].isin(
                ["NOT_FOUND_ON_NSE", "UNEXPLAINED_MISSING"]
            )
        ]["isin"]
    )

    buffer = active[
        active["master_isin"].isin(gap_isins)
        & (active["estimated_slot"] == buffer_slot)
    ].copy()
    buffer["selection_reason"] = "BUFFER_FOR_PERIOD_CHECK"

    selection = pd.concat([selection, buffer], ignore_index=True)

    return selection, sorted(target)


def coverage_matrix(companies, index_df, today, listing_dates):
    """
    One row per company x FY slot (FY2021-22 .. latest completed FY).

    Status for each cell:
      FOUND                 active NSE filing with XBRL exists
      FOUND_NO_XBRL         filing exists but has no XBRL file
      NOT_LISTED_IN_PERIOD  listed on NSE after the slot ended
      NO_FILING_YET         filing not yet due, or newly listed company
                            whose first BRSR is not yet filed
      VOLUNTARY_NOT_FILED   FY2021-22 (voluntary) with no filing
      NOT_FOUND_ON_NSE      expected (listed, due, mandatory) but absent
                            under every known symbol -> check BSE next
      UNEXPLAINED_MISSING   cannot be classified (e.g. listing date
                            unknown, or only rejected filings exist)
    """

    last = latest_completed_slot(today)
    slots = range(FIRST_BRSR_SLOT, last + 1)
    rows = []

    for company in companies:
        isin = company["isin"]
        listed = listing_dates.get(company["nse_symbol"])
        filings = index_df[index_df["master_isin"] == isin]
        active = filings[filings["is_active_for_slot"]]
        company_has_any_filing = len(active) > 0

        for slot in slots:
            in_slot = filings[filings["estimated_slot"] == slot]
            active_in_slot = active[active["estimated_slot"] == slot]
            due_date = slot_end(slot) + timedelta(days=FILING_DUE_DAYS)

            if len(active_in_slot):
                status = "FOUND"
            elif len(in_slot) and not in_slot["has_xbrl"].any():
                status = "FOUND_NO_XBRL"
            elif len(in_slot):
                status = "UNEXPLAINED_MISSING"   # only rejected filings
            elif listed is None:
                status = "UNEXPLAINED_MISSING"
            elif listed > slot_end(slot):
                status = "NOT_LISTED_IN_PERIOD"
            elif today <= due_date:
                status = "NO_FILING_YET"
            elif not company_has_any_filing and slot_for_date(listed) >= last:
                status = "NO_FILING_YET"         # new listing, first BRSR
            elif slot in VOLUNTARY_SLOTS:
                status = "VOLUNTARY_NOT_FILED"
            else:
                status = "NOT_FOUND_ON_NSE"

            row = active_in_slot.iloc[0] if len(active_in_slot) else None

            rows.append({
                "company_name": company["company_name"],
                "isin": isin,
                "nse_symbol": company["nse_symbol"],
                "listing_date": listed.date().isoformat() if listed else "",
                "fy_slot": slot_label(slot),
                "voluntary_year": slot in VOLUNTARY_SLOTS,
                "status": status,
                "filing_symbol": (
                    row["filing_symbol"] if row is not None else ""
                ),
                "api_period": row["api_period"] if row is not None else "",
                "submission_date": (
                    row["submission_date"] if row is not None else ""
                ),
                "filings_in_slot": len(in_slot),
                "xbrl_url": row["xbrl_url"] if row is not None else "",
            })

    return pd.DataFrame(rows)


def load_listing_dates():
    path = BASE_DIR / "data" / "raw" / "reference" / "NSE_EQUITY_L.csv"
    dates = {}

    if not path.exists():
        return dates

    df = pd.read_csv(path, dtype=str).fillna("")
    df.columns = [c.strip() for c in df.columns]

    for symbol, value in zip(df["SYMBOL"], df["DATE OF LISTING"]):
        parsed = parse_date(value)
        if parsed:
            dates[symbol.strip().upper()] = parsed

    return dates


# ==========================================================
# MAIN
# ==========================================================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--years", type=int, default=3,
        help="Number of latest completed FY slots to select for download",
    )
    parser.add_argument(
        "--from-date", default="01-04-2022",
        help="Earliest submission date to index, DD-MM-YYYY "
             "(default covers FY2021-22 onward)",
    )
    args = parser.parse_args()

    today = datetime.now()
    from_date = datetime.strptime(args.from_date, "%d-%m-%Y")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print("NSE BRSR FILING DISCOVERY")
    print("=" * 70)
    print("Submission range indexed:", from_date.date(), "to", today.date())
    print("FY slots selected for download:", args.years)

    nse = NSESession()

    symbol_text = download_reference(
        nse, SYMBOL_CHANGE_URL, "symbolchange.csv"
    )
    download_reference(nse, NAME_CHANGE_URL, "namechange.csv")

    changes = load_symbol_changes(symbol_text)
    companies = build_identity_master(changes)
    listing_dates = load_listing_dates()

    identity = pd.DataFrame(
        [{k: v for k, v in c.items() if not k.startswith("_")}
         for c in companies]
    )
    identity["listing_date"] = [
        listing_dates[s].date().isoformat() if s in listing_dates else ""
        for s in identity["nse_symbol"]
    ]
    identity.to_csv(IDENTITY_OUTPUT, index=False, encoding="utf-8-sig")
    print("Companies in master:", len(companies))

    filings = fetch_filing_index(nse, companies, from_date, today)
    print("Unique filings fetched (all companies):", len(filings))

    index_df = prepare_index(match_filings(companies, filings))
    coverage = coverage_matrix(companies, index_df, today, listing_dates)
    selection, target_slots = select_for_download(
        index_df, args.years, today, coverage
    )

    index_df.to_csv(INDEX_OUTPUT, index=False, encoding="utf-8-sig")
    selection.to_csv(SELECTION_OUTPUT, index=False, encoding="utf-8-sig")
    coverage.to_csv(COVERAGE_OUTPUT, index=False, encoding="utf-8-sig")

    print("\n" + "=" * 70)
    print("DISCOVERY SUMMARY")
    print("=" * 70)
    print("Filings matched to master companies:", len(index_df))
    print(
        "Target slots for download:",
        ", ".join(slot_label(s) for s in target_slots),
    )
    print("Filings selected for download:", len(selection))
    print(selection["selection_reason"].value_counts().to_string())

    print("\nCoverage matrix (company x FY slot):")
    print(pd.crosstab(coverage["status"], coverage["fy_slot"]).to_string())

    print("\nCells needing attention (not FOUND / NOT_LISTED / voluntary):")
    attention = coverage[
        ~coverage["status"].isin(
            ["FOUND", "NOT_LISTED_IN_PERIOD", "VOLUNTARY_NOT_FILED"]
        )
    ]
    print(attention[
        ["nse_symbol", "listing_date", "fy_slot", "status"]
    ].to_string(index=False))

    multi = index_df[index_df["filings_in_slot"] > 1]
    if len(multi):
        print("\nSlots with more than one filing (latest kept):")
        print(multi[
            ["master_symbol", "filing_symbol", "api_period",
             "estimated_fy_slot", "submission_date", "is_active_for_slot"]
        ].to_string(index=False))

    print("\nOutputs:")
    for path in (IDENTITY_OUTPUT, INDEX_OUTPUT,
                 SELECTION_OUTPUT, COVERAGE_OUTPUT):
        print(" ", path)
    print("API snapshots:", API_SNAPSHOT_DIR)


if __name__ == "__main__":
    main()


