"""
Post-download verification of NSE BRSR XBRL reports.

For every downloaded report:
  * reads the identity facts (ISIN context identifier, CIN, company name,
    NSE symbol, BSE scrip code) and compares them with the company master
  * reads the reporting period from DateOfStart/EndOfFinancialYear and
    assigns the true FY slot (April-March year in which the period ends)
  * records the taxonomy version from the schemaRef

Then builds:
  * nse_brsr_report_verification.csv  - one row per downloaded report
  * nse_brsr_coverage_matrix.csv      - final company x FY slot status
  * nse_brsr_extraction_manifest.csv  - primary verified reports for the
                                        target slots (input to extractor)

Validation only: no extraction, no database access.

Run:
    python -m app.validators.brsr_report_verifier --years 3
"""

import argparse
import re
import xml.etree.ElementTree as ET

from datetime import datetime
from pathlib import Path

import pandas as pd

from app.scrapers.nse_brsr_filing_index import (
    latest_completed_slot,
    slot_for_date,
    slot_label,
)


BASE_DIR = Path(__file__).resolve().parents[2]
NIFTY_DIR = BASE_DIR / "data" / "processed" / "nifty100"

MANIFEST_FILE = NIFTY_DIR / "nse_brsr_report_manifest.csv"
DISCOVERY_COVERAGE_FILE = NIFTY_DIR / "nse_brsr_discovery_coverage.csv"

VERIFICATION_OUTPUT = NIFTY_DIR / "nse_brsr_report_verification.csv"
COVERAGE_OUTPUT = NIFTY_DIR / "nse_brsr_coverage_matrix.csv"
EXTRACTION_MANIFEST_OUTPUT = NIFTY_DIR / "nse_brsr_extraction_manifest.csv"

IDENTITY_FACTS = {
    "CorporateIdentityNumber": "xbrl_cin",
    "NameOfTheCompany": "xbrl_company_name",
    "NSESymbol": "xbrl_nse_symbol",
    "ScripCode": "xbrl_bse_code",
    "DateOfStartOfFinancialYear": "fy_start",
    "DateOfEndOfFinancialYear": "fy_end",
    "DateOfStartOfPreviousYear": "py_start",
    "DateOfEndOfPreviousYear": "py_end",
    "ReportingBoundary": "reporting_boundary",
}

VERIFIED_STATUSES = {
    "VERIFIED_BY_ISIN",
    "VERIFIED_BY_CIN",
    "VERIFIED_BY_CIN_REGISTRATION",
    "VERIFIED_BY_NAME_SYMBOL",
}


def local_name(tag):
    return tag.split("}")[-1]


def parse_iso(value):
    try:
        return datetime.strptime((value or "").strip()[:10], "%Y-%m-%d")
    except ValueError:
        return None


def read_report_header(path):
    """Read identity, period and taxonomy facts from one XBRL file."""

    info = {value: "" for value in IDENTITY_FACTS.values()}
    info.update({"xbrl_isin": "", "taxonomy_version": "", "parse_error": ""})

    try:
        root = ET.parse(path).getroot()
    except Exception as error:
        info["parse_error"] = str(error)
        return info

    for element in root.iter():
        name = local_name(element.tag)

        if name == "schemaRef" and not info["taxonomy_version"]:
            href = element.attrib.get(
                "{http://www.w3.org/1999/xlink}href", ""
            )
            match = re.search(r"(\d{4}-\d{2}-\d{2})", href)
            info["taxonomy_version"] = match.group(1) if match else href

        elif name == "identifier" and not info["xbrl_isin"]:
            info["xbrl_isin"] = (element.text or "").strip().upper()

        elif name in IDENTITY_FACTS and element.attrib.get("contextRef"):
            field = IDENTITY_FACTS[name]
            if not info[field]:
                info[field] = (element.text or "").strip()

    return info


CIN_PATTERN = re.compile(
    r"^[LU](\d{5})([A-Z]{2})(\d{4})([A-Z]{3})(\d{6})$"
)

# State/ROC codes seen only in filer placeholders. Real CINs can contain
# NIC code 99999 and older ROC codes such as PN or TZ, so those are valid.
PLACEHOLDER_STATE_CODES = {"AA", "AB", "XX", "ZZ", "NA"}


def is_real_cin(value):
    """
    Format check that rejects filer placeholders such as
    Z99999ZZ9999ZZZ000000, X00000XX0000XXX000000, U12345KA1234KAA123456,
    U99999AB1111ABC000999 (used by statutory bodies such as public-sector
    banks, which have no CIN).
    """

    match = CIN_PATTERN.match((value or "").strip().upper())
    if not match:
        return False
    return not placeholder_reasons(value)


def _is_sequential(digits, length=5):
    """True if the digits contain an ascending/descending run of length."""
    runs = ("0123456789", "9876543210")
    return any(digits[i:i + length] in run for run in runs
               for i in range(len(digits) - length + 1))


def placeholder_reasons(value):
    """
    Reasons a CIN looks like a filer placeholder rather than a real CIN
    (empty list = no placeholder signal). Canonical rule used by the
    verifier, the company master and the MCA adapter.

    Real CINs legitimately contain NIC code 99999 and older ROC codes such
    as PN / TZ, so those are not signals. Examples caught:
      Z99999ZZ9999ZZZ000000, A00000AA0000AAA000000   mostly zeros / format
      U12345KA1234KAA123456                          sequential, bad year
      U99999AB1111ABC000999                          placeholder state code
      L01234KA2023PTC567891                          sequential NIC and
                                                     registration; listed
                                                     flag with private type
    """

    cin = (value or "").strip().upper()
    reasons = []
    match = CIN_PATTERN.match(cin)
    if not match:
        # blank = no CIN reported (not a placeholder); any other value
        # that is not a CIN (e.g. Z99999ZZ9999ZZZ999999) is a placeholder
        if cin:
            reasons.append("not a valid CIN format")
            if cin.count("0") >= 12:
                reasons.append("mostly zeros")
        return reasons

    nic, state, year, kind, registration = match.groups()
    if state in PLACEHOLDER_STATE_CODES:
        reasons.append(f"placeholder state code {state}")
    if not 1850 <= int(year) <= datetime.now().year:
        reasons.append(f"impossible year {year}")
    if registration == "000000" or len(set(registration)) == 1:
        reasons.append("repeated registration digits")
    if nic == "12345" or (_is_sequential(nic, 5)
                          and _is_sequential(registration, 5)):
        reasons.append("sequential NIC and registration number")
    if cin[0] == "L" and kind in ("PTC", "OPC", "ULT", "GAT"):
        reasons.append(f"listed flag with private company type {kind}")
    return reasons


def cin_registration(value):
    """State + year + company type + registration number of a CIN."""

    match = CIN_PATTERN.match((value or "").strip().upper())
    if not match:
        return None
    _, state, year, kind, registration = match.groups()
    return f"{state}{year}{kind}{registration}"


def normalise_name(value):
    value = (value or "").lower()
    value = re.sub(r"\bltd\b\.?", "limited", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def assign_identity(df):
    """
    Identity evidence, strongest first. The XBRL entity identifier is the
    ISIN in 2026-taxonomy filings but the CIN in older taxonomies, so the
    CIN is anchored from the company's other reports.

    VERIFIED_BY_ISIN           identifier (ISIN scheme) == master ISIN
    VERIFIED_BY_CIN            real CIN == the company's reference CIN;
                               reference CIN comes from an ISIN-verified
                               report, else from a report whose company
                               name matches the master name
    VERIFIED_BY_NAME_SYMBOL    no usable CIN (placeholder) but the XBRL
                               company name matches the master name (or the
                               NSE API name) and NSE listed the filing under
                               the company's symbol history  [weaker]
    IDENTITY_MISMATCH          none of the above -> manual review
    UNREADABLE                 file could not be parsed
    """

    df["cin_is_real"] = df["xbrl_cin"].map(is_real_cin)
    df["identifier_is_isin"] = df["xbrl_isin"].str.match(
        r"^IN[A-Z0-9]{10}$"
    )

    master_name = df["company_name"].map(normalise_name)
    api_name = df["api_company_name"].map(normalise_name)
    xbrl_name = df["xbrl_company_name"].map(normalise_name)
    df["name_matches"] = (xbrl_name != "") & (
        (xbrl_name == master_name) | (xbrl_name == api_name)
    )

    def reference_for(mask):
        return (
            df[mask & df["cin_is_real"]]
            .groupby("isin")["xbrl_cin"]
            .agg(lambda s: s.mode().iloc[0])
            .to_dict()
        )

    by_isin = reference_for(df["xbrl_isin"] == df["isin"])
    by_name = reference_for(df["name_matches"])
    reference_cin = {**by_name, **by_isin}

    statuses = []
    for row in df.itertuples():
        reference = reference_cin.get(row.isin, "")

        if row.parse_error:
            statuses.append("UNREADABLE")
        elif row.identifier_is_isin and row.xbrl_isin == row.isin:
            statuses.append("VERIFIED_BY_ISIN")
        elif row.cin_is_real and row.xbrl_cin == reference:
            statuses.append("VERIFIED_BY_CIN")
        elif (
            row.cin_is_real and reference
            and cin_registration(row.xbrl_cin) == cin_registration(reference)
        ):
            # Same state + year + type + registration number; only the
            # listing flag or NIC activity code changed (e.g. MOTHERSON
            # L34300MH1986PLC284510 -> L35106MH1986PLC284510).
            statuses.append("VERIFIED_BY_CIN_REGISTRATION")
        elif not row.cin_is_real and row.name_matches:
            statuses.append("VERIFIED_BY_NAME_SYMBOL")
        else:
            statuses.append("IDENTITY_MISMATCH")

    df["reference_cin"] = df["isin"].map(reference_cin).fillna("")
    df["identity_status"] = statuses

    df["cin_consistent"] = (
        ~df["cin_is_real"]
        | (df["reference_cin"] == "")
        | (df["xbrl_cin"] == df["reference_cin"])
    )

    return df


def assign_periods(df):
    slots, labels, months, flags = [], [], [], []

    for row in df.itertuples():
        start = parse_iso(row.fy_start)
        end = parse_iso(row.fy_end)

        if not end:
            slots.append(None)
            labels.append("")
            months.append(None)
            flags.append("PERIOD_DATES_MISSING")
            continue

        slot = slot_for_date(end)
        slots.append(slot)
        labels.append(slot_label(slot))

        if start:
            length = round((end - start).days / 30.44)
            months.append(length)
            flags.append("" if 11 <= length <= 13 else "NON_12_MONTH_PERIOD")
        else:
            months.append(None)
            flags.append("PERIOD_START_MISSING")

    df["true_slot"] = slots
    df["true_fy_slot"] = labels
    df["period_months"] = months
    df["period_flag"] = flags

    df["slot_differs_from_api_estimate"] = (
        (df["true_fy_slot"] != df["estimated_fy_slot"])
        & (df["true_fy_slot"] != "")
    )

    return df


def choose_primary(df):
    """One primary report per company x true slot: latest submission."""

    df["_submitted"] = pd.to_datetime(
        df["submission_date"], format="%d-%b-%Y", errors="coerce"
    )
    df = df.sort_values(["isin", "true_slot", "_submitted"],
                        ascending=[True, True, False])

    eligible = df["identity_status"].isin(VERIFIED_STATUSES) & df[
        "true_slot"
    ].notna()

    df["is_primary"] = False
    primary_idx = (
        df[eligible].drop_duplicates(["isin", "true_slot"], keep="first").index
    )
    df.loc[primary_idx, "is_primary"] = True

    counts = df[eligible].groupby(["isin", "true_slot"]).size()
    df["reports_in_true_slot"] = [
        counts.get((i, s), 0) for i, s in zip(df["isin"], df["true_slot"])
    ]

    return df.drop(columns=["_submitted"])


def final_coverage(discovery, verification, target_slots, failed=None):
    """
    Final status per company x FY slot.

    Target slots: FOUND_VERIFIED when a primary verified report exists for
    the true slot; IDENTITY_MISMATCH / UNREADABLE when only such reports
    exist; otherwise the discovery status is kept, except that a discovery
    FOUND without a verified report becomes UNEXPLAINED_MISSING.
    Slots outside the download window keep the discovery status.
    """

    primaries = verification[verification["is_primary"]]
    primary_map = {
        (r.isin, r.true_fy_slot): r for r in primaries.itertuples()
    }

    problem_map = {}
    for r in verification[~verification["identity_status"].isin(
            VERIFIED_STATUSES)].itertuples():
        slot = r.true_fy_slot or r.estimated_fy_slot
        problem_map.setdefault((r.isin, slot), r.identity_status)

    reassigned_from = {
        (r.isin, r.estimated_fy_slot)
        for r in verification.itertuples()
        if r.true_fy_slot and r.true_fy_slot != r.estimated_fy_slot
    }

    # Filings listed by NSE whose XBRL link could not be downloaded.
    if failed is not None:
        for r in failed.to_dict("records"):
            error = str(r.get("error", "")).lower()
            if "404" in error or "not found" in error:
                status = "XBRL_LINK_BROKEN_ON_NSE"
            elif "well-formed" in error or "xml" in error:
                status = "XBRL_MALFORMED_ON_NSE"
            else:
                status = "DOWNLOAD_FAILED"
            problem_map.setdefault(
                (r["isin"], r["estimated_fy_slot"]), status
            )

    rows = []
    for row in discovery.to_dict("records"):
        key = (row["isin"], row["fy_slot"])
        in_window = row["fy_slot"] in target_slots
        status = row["status"]
        primary = primary_map.get(key)

        if in_window:
            if primary is not None:
                status = "FOUND_VERIFIED"
            elif key in problem_map:
                status = problem_map[key]
            elif status == "FOUND" and key in reassigned_from:
                # The API-tagged filing for this slot turned out (from
                # XBRL dates) to belong to another slot, e.g. an
                # October-September filer. Nothing covers this slot.
                status = (
                    "VOLUNTARY_NOT_FILED"
                    if row.get("voluntary_year") in (True, "True")
                    else "NOT_FOUND_ON_NSE"
                )
            elif status == "FOUND":
                status = "UNEXPLAINED_MISSING"

        rows.append({
            **row,
            "in_download_window": in_window,
            "discovery_status": row["status"],
            "status": status,
            "local_file_path": primary.local_file_path if primary else "",
            "reporting_period": (
                f"{primary.fy_start} to {primary.fy_end}" if primary else ""
            ),
            "taxonomy_version": primary.taxonomy_version if primary else "",
        })

    return pd.DataFrame(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", type=int, default=3)
    args = parser.parse_args()

    today = datetime.now()
    last = latest_completed_slot(today)
    target_slots = {
        slot_label(s) for s in range(last - args.years + 1, last + 1)
    }

    manifest = pd.read_csv(MANIFEST_FILE, dtype=str).fillna("")
    failed = manifest[manifest["download_status"] == "FAILED"].copy()
    manifest = manifest[
        manifest["download_status"].isin(
            ["SUCCESS", "SUCCESS_SANITIZED", "ALREADY_DOWNLOADED"]
        )
    ].copy()

    print("Reports to verify:", len(manifest))

    headers = [
        read_report_header(Path(p)) for p in manifest["local_file_path"]
    ]
    df = pd.concat(
        [manifest.reset_index(drop=True), pd.DataFrame(headers)], axis=1
    )

    df = assign_identity(df)
    df = assign_periods(df)
    df = choose_primary(df)
    df["in_download_window"] = df["true_fy_slot"].isin(target_slots)

    df.to_csv(VERIFICATION_OUTPUT, index=False, encoding="utf-8-sig")

    discovery = pd.read_csv(DISCOVERY_COVERAGE_FILE, dtype=str).fillna("")
    coverage = final_coverage(discovery, df, target_slots, failed)
    coverage.to_csv(COVERAGE_OUTPUT, index=False, encoding="utf-8-sig")

    extraction = df[df["is_primary"] & df["in_download_window"]].copy()
    extraction.to_csv(
        EXTRACTION_MANIFEST_OUTPUT, index=False, encoding="utf-8-sig"
    )

    print("\nIdentity status:")
    print(df["identity_status"].value_counts().to_string())
    print("\nTaxonomy versions:")
    print(df["taxonomy_version"].value_counts().to_string())
    print("\nPeriod flags:")
    print(df["period_flag"].replace("", "OK").value_counts().to_string())

    moved = df[df["slot_differs_from_api_estimate"]]
    if len(moved):
        print("\nReports whose true FY slot differs from the API tag:")
        print(moved[["nse_symbol", "api_period", "estimated_fy_slot",
                     "true_fy_slot", "fy_start", "fy_end"]]
              .to_string(index=False))

    odd = df[(df["period_flag"] != "") | (~df["cin_consistent"])
             | (~df["identity_status"].isin(VERIFIED_STATUSES))]
    if len(odd):
        print("\nReports needing review:")
        print(odd[["nse_symbol", "api_period", "identity_status",
                   "xbrl_isin", "xbrl_cin", "reference_cin",
                   "fy_start", "fy_end", "period_flag"]]
              .to_string(index=False))

    print("\nFinal coverage (company x FY slot):")
    print(pd.crosstab(coverage["status"], coverage["fy_slot"]).to_string())

    print("\nReports passed to extraction:", len(extraction))
    print("\nOutputs:")
    for path in (VERIFICATION_OUTPUT, COVERAGE_OUTPUT,
                 EXTRACTION_MANIFEST_OUTPUT):
        print(" ", path)


if __name__ == "__main__":
    main()
