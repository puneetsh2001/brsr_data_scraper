
import re
import xml.etree.ElementTree as ET
import pandas as pd

from pathlib import Path
from datetime import datetime


# ==========================================================
# CONFIGURATION
# ==========================================================

BASE_DIR = Path(__file__).resolve().parents[2]

XML_DIR = BASE_DIR / "data" / "raw" / "nse_brsr_xml"

PROCESSED_DIR = BASE_DIR / "data" / "processed"

MANIFEST_FILE = (
    PROCESSED_DIR / "nse_brsr_download_manifest.csv"
)

TAXONOMY_FILE = (
    PROCESSED_DIR / "nse_brsr_taxonomy_concepts.csv"
)

FACTS_OUTPUT = PROCESSED_DIR / "multi_company_facts.csv"

COMPANY_OUTPUT = PROCESSED_DIR / "multi_company_details.csv"

SUMMARY_OUTPUT = (
    PROCESSED_DIR / "multi_company_extraction_summary.csv"
)

ERROR_OUTPUT = PROCESSED_DIR / "multi_company_errors.csv"

# Manifest column -> fact column, copied when present (verifier manifest).
MANIFEST_PASSTHROUGH = {
    "isin": "master_isin",
    "nse_symbol": "master_nse_symbol",
    "true_fy_slot": "fy_slot",
    "identity_status": "identity_status",
    "taxonomy_version": "taxonomy_version",
}


# ==========================================================
# HELPER FUNCTIONS
# ==========================================================

def local_name(tag):
    return tag.split("}")[-1]


def get_namespace(tag):

    if tag.startswith("{"):
        return tag.split("}")[0][1:]

    return ""


def get_financial_year(start_date, end_date, instant_date):

    """
    Identify Indian financial year from XBRL context dates.

    Standard Indian financial year:
    1 April to 31 March.
    """

    if start_date and end_date:

        try:
            start = datetime.strptime(start_date, "%Y-%m-%d")
            end = datetime.strptime(end_date, "%Y-%m-%d")

            if (
                start.month == 4
                and start.day == 1
                and end.month == 3
                and end.day == 31
                and end.year == start.year + 1
            ):
                return f"{start.year}-{str(end.year)[-2:]}"

            return f"Period ending {end_date}"

        except ValueError:
            return ""

    if instant_date:

        try:
            instant = datetime.strptime(
                instant_date,
                "%Y-%m-%d"
            )

            if instant.month == 3 and instant.day == 31:
                return (
                    f"{instant.year - 1}-"
                    f"{str(instant.year)[-2:]}"
                )

            return f"As of {instant_date}"

        except ValueError:
            return ""

    return ""


# ==========================================================
# STEP 1: EXTRACT XBRL CONTEXTS
# ==========================================================

def get_contexts(root):

    contexts = {}

    for element in root.iter():

        if local_name(element.tag) != "context":
            continue

        context_id = element.attrib.get("id")

        if not context_id:
            continue

        data = {
            "identifier": "",
            "start_date": "",
            "end_date": "",
            "instant": "",
            "dimensions": []
        }

        for child in element.iter():

            name = local_name(child.tag)

            if name == "identifier":

                data["identifier"] = (
                    child.text or ""
                ).strip()

            elif name == "startDate":

                data["start_date"] = (
                    child.text or ""
                ).strip()

            elif name == "endDate":

                data["end_date"] = (
                    child.text or ""
                ).strip()

            elif name == "instant":

                data["instant"] = (
                    child.text or ""
                ).strip()

            elif name == "explicitMember":

                dimension = child.attrib.get(
                    "dimension", ""
                )

                member = (child.text or "").strip()

                data["dimensions"].append(
                    f"{dimension}={member}"
                )

            elif name == "typedMember":

                dimension = child.attrib.get(
                    "dimension", ""
                )

                typed_value = " ".join(
                    (node.text or "").strip()
                    for node in child.iter()
                    if (node.text or "").strip()
                )

                data["dimensions"].append(
                    f"{dimension}={typed_value}"
                )

        contexts[context_id] = data

    return contexts


# ==========================================================
# STEP 2: LOAD TAXONOMY LOOKUP
# ==========================================================

def load_taxonomy():

    if not TAXONOMY_FILE.exists():

        raise FileNotFoundError(
            f"Taxonomy file not found: {TAXONOMY_FILE}"
        )

    taxonomy = pd.read_csv(
        TAXONOMY_FILE,
        dtype=str
    ).fillna("")

    taxonomy["concept_name"] = (
        taxonomy["concept_name"]
        .astype(str)
        .str.strip()
    )

    taxonomy = taxonomy.drop_duplicates(
        subset=["concept_name"]
    )

    lookup = taxonomy.set_index(
        "concept_name"
    ).to_dict("index")

    print("Taxonomy concepts loaded:", len(lookup))

    return lookup


# ==========================================================
# STEP 3: EXTRACT FACTS FROM ONE REPORT
# ==========================================================

def extract_report(xml_path, company_name, report_url):

    print("\nReading:", company_name)
    print("File:", xml_path.name)

    tree = ET.parse(xml_path)
    root = tree.getroot()

    contexts = get_contexts(root)

    print("Contexts found:", len(contexts))

    rows = []

    excluded_tags = {
        "context",
        "unit",
        "schemaRef",
        "roleRef",
        "arcroleRef",
        "footnoteLink"
    }

    for element in root.iter():

        metric_name = local_name(element.tag)

        if metric_name in excluded_tags:
            continue

        context_ref = element.attrib.get("contextRef")

        if not context_ref:
            continue

        # XBRL facts should be simple elements.
        # Skip elements containing nested child elements.
        if len(element) > 0:
            continue

        context = contexts.get(context_ref)

        if not context:
            continue

        value = (element.text or "").strip()

        # Keep nil / empty facts so "not disclosed" stays distinguishable
        # from "reported zero"; value_status records which case it is.
        is_nil = element.attrib.get(XSI_NIL, "").lower() == "true"

        if is_nil:
            value_status = "NIL"
        elif not value:
            value_status = "EMPTY"
        else:
            value_status = "REPORTED"

        financial_year = get_financial_year(
            context["start_date"],
            context["end_date"],
            context["instant"]
        )

        rows.append({

            "company_name": company_name,

            "isin": context["identifier"],

            "metric_name": metric_name,

            "namespace": get_namespace(element.tag),

            "value": value,

            "value_status": value_status,

            "financial_year": financial_year,

            "period_start": context["start_date"],

            "period_end": context["end_date"],

            "instant_date": context["instant"],

            "context_reference": context_ref,

            "dimensions": " | ".join(
                context["dimensions"]
            ),

            "unit_reference": element.attrib.get(
                "unitRef", ""
            ),

            "decimals": element.attrib.get(
                "decimals", ""
            ),

            "source_file": xml_path.name,

            "source_url": report_url

        })

    add_report_periods(rows)

    print("Facts extracted:", len(rows))

    return rows


# ==========================================================
# STEP 3B: REPORT-DERIVED FINANCIAL PERIODS
# ==========================================================
#
# The original financial_year column assumes April-March and is kept for
# compatibility. These columns use the periods each report declares about
# itself, so July-June, calendar-year and transition-period filers are
# labelled correctly.

XSI_NIL = "{http://www.w3.org/2001/XMLSchema-instance}nil"

PERIOD_FACTS = {
    "CY": ("DateOfStartOfFinancialYear", "DateOfEndOfFinancialYear"),
    "PY": ("DateOfStartOfPreviousYear", "DateOfEndOfPreviousYear"),
    "PPY": (
        "DateOfStartOfPriorToPreviousYear",
        "DateOfEndOfPriorToPreviousYear",
    ),
}

# Tolerance for filer date slips (e.g. a period ending 1 April instead
# of 31 March).
PERIOD_TOLERANCE_DAYS = 7


def parse_iso_date(value):
    try:
        return datetime.strptime((value or "").strip()[:10], "%Y-%m-%d")
    except ValueError:
        return None


def fy_label(start, end):
    """2025-26 for a period spanning two years, 2025 for calendar year."""

    if start.year == end.year:
        return str(end.year)

    return f"{start.year}-{str(end.year)[-2:]}"


def close(a, b):
    return a and b and abs((a - b).days) <= PERIOD_TOLERANCE_DAYS


def add_report_periods(rows):
    """
    Adds to every fact:
      report_fy_start / report_fy_end  current year declared by the report
      reporting_fy                     label of that current year
      period_role                      CY / PY / PPY / UNMATCHED
      period_fy_label                  label of the period the fact covers
      period_flag                      DATE_TOLERANCE_APPLIED when a fact's
                                       dates were matched within tolerance
    """

    declared = {}

    for role, (start_name, end_name) in PERIOD_FACTS.items():
        start = next(
            (parse_iso_date(r["value"]) for r in rows
             if r["metric_name"] == start_name and r["value"]), None
        )
        end = next(
            (parse_iso_date(r["value"]) for r in rows
             if r["metric_name"] == end_name and r["value"]), None
        )
        if start and end:
            declared[role] = (start, end)

    current = declared.get("CY")

    for row in rows:
        row["report_fy_start"] = current[0].date().isoformat() if current else ""
        row["report_fy_end"] = current[1].date().isoformat() if current else ""
        row["reporting_fy"] = fy_label(*current) if current else ""

        start = parse_iso_date(row["period_start"])
        end = parse_iso_date(row["period_end"])
        instant = parse_iso_date(row["instant_date"])

        role, label, flag = "UNMATCHED", "", ""

        for candidate, (p_start, p_end) in declared.items():

            if start and end:
                exact = start == p_start and end == p_end
                near = close(start, p_start) and close(end, p_end)
            elif instant:
                exact = instant == p_end
                near = close(instant, p_end)
            else:
                exact = near = False

            if exact or near:
                role = candidate
                label = fy_label(p_start, p_end)
                flag = "" if exact else "DATE_TOLERANCE_APPLIED"
                break

        row["period_role"] = role
        row["period_fy_label"] = label
        row["period_flag"] = flag

    return rows


# ==========================================================
# STEP 4: BUILD COMPANY DETAILS
# ==========================================================


COMPANY_FIELD_MAP = {
    "NameOfTheCompany": "company_name",
    "CorporateIdentityNumber": "cin",
    "ISIN": "isin",
    "NSESymbol": "nse_symbol",
    "ScripCode": "bse_code",
    "DateOfIncorporation": "incorporation_date",

    "AddressOfRegisteredOfficeOfCompany": "registered_office",
    "AddressOfCorporateOfficeOfCompany": "corporate_address",
    "EMailOfTheCompany": "email",
    "TelephoneOfCompany": "telephone",
    "WebsiteOfCompany": "website",

    "NameOfContactPerson": "contact_person",
    "ContactNumberOfContactPerson": "contact_person_number",
    "EMailOfContactPerson": "contact_person_email",
}



def build_company_details(company_name, facts, report_url):

    details = {
        "company_name": company_name,
        "cin": "",
        "isin": "",
        "nse_symbol": "",
        "bse_code": "",
        "incorporation_date": "",
        "incorporation_year": "",
        "registered_office": "",
        "corporate_address": "",
        "email": "",
        "telephone": "",
        "website": "",
        "contact_person": "",
        "contact_person_number": "",
        "contact_person_email": "",
        "investor_url": "",
        "source_url": report_url,
        "source_name": "NSE BRSR XBRL",
    }

    for fact in facts:

        metric_name = fact.get("metric_name", "")
        value = fact.get("value", "")

        if metric_name in COMPANY_FIELD_MAP and value:

            field_name = COMPANY_FIELD_MAP[metric_name]

            # Keep the first non-empty value for each field.
            if not details.get(field_name):
                details[field_name] = value

    # Derive incorporation year from incorporation date.
    incorporation_date = details.get("incorporation_date", "")

    if incorporation_date and len(incorporation_date) >= 4:
        details["incorporation_year"] = incorporation_date[:4]

    return details



# ==========================================================
# STEP 5: ADD TAXONOMY INFORMATION TO FACTS
# ==========================================================

TAXONOMY_VERSIONS_DIR = PROCESSED_DIR / "taxonomy_versions"
CROSSWALK_FILE = TAXONOMY_VERSIONS_DIR / "nse_brsr_concept_crosswalk.csv"


def load_version_lookups():
    """
    Concept tables for older taxonomy versions (built by
    nse_brsr_taxonomy_mapping_builder.py --taxonomy-dir ...) and the
    crosswalk to the current version. Both are optional.
    """

    lookups = {}

    if TAXONOMY_VERSIONS_DIR.exists():
        for path in TAXONOMY_VERSIONS_DIR.glob(
            "*/nse_brsr_taxonomy_concepts.csv"
        ):
            table = pd.read_csv(path, dtype=str).fillna("")
            table = table.drop_duplicates(subset=["concept_name"])
            lookups[path.parent.name] = table.set_index(
                "concept_name"
            ).to_dict("index")

    crosswalk = {}
    if CROSSWALK_FILE.exists():
        table = pd.read_csv(CROSSWALK_FILE, dtype=str).fillna("")
        for row in table.itertuples():
            crosswalk[(row.source_version, row.source_concept)] = (
                row.current_concept, row.status
            )

    if lookups:
        print("Older taxonomy versions loaded:", ", ".join(sorted(lookups)))
    if crosswalk:
        print("Crosswalk entries loaded:", len(crosswalk))

    return lookups, crosswalk


def namespace_version(namespace):
    match = re.search(r"(\d{4}-\d{2}-\d{2})", namespace or "")
    return match.group(1) if match else ""


def add_taxonomy_mapping(facts, taxonomy_lookup, version_lookups=None,
                         crosswalk=None):
    """
    Taxonomy metadata comes from the fact's own taxonomy version when that
    version's concept table is available, else from the current taxonomy.
    current_concept gives the current-taxonomy equivalent via the
    crosswalk (same name for current-version facts).
    """

    version_lookups = version_lookups or {}
    crosswalk = crosswalk or {}

    for row in facts:

        version = namespace_version(row.get("namespace", ""))
        own_lookup = version_lookups.get(version)

        if own_lookup is not None:
            concept = own_lookup.get(row["metric_name"], {})
            row["taxonomy_version_matched"] = version if concept else ""
        else:
            concept = taxonomy_lookup.get(row["metric_name"], {})
            row["taxonomy_version_matched"] = "current" if concept else ""

        if (version, row["metric_name"]) in crosswalk:
            current, status = crosswalk[(version, row["metric_name"])]
        elif row["metric_name"] in taxonomy_lookup:
            current, status = row["metric_name"], "UNCHANGED"
        else:
            current, status = "", "NO_CURRENT_EQUIVALENT"

        row["current_concept"] = current
        row["crosswalk_status"] = status

        row["standard_label"] = concept.get(
            "standard_label", ""
        )

        row["data_type"] = concept.get(
            "data_type", ""
        )

        row["abstract"] = concept.get(
            "abstract", ""
        )

        row["presentation_roles"] = concept.get(
            "presentation_roles", ""
        )

        row["parent_concepts"] = concept.get(
            "parent_concepts", ""
        )

        row["taxonomy_match_status"] = (
            "MATCHED" if concept else "UNMATCHED"
        )

    return facts


# ==========================================================
# STEP 6: PROCESS ALL DOWNLOADED REPORTS
# ==========================================================

def main():

    import argparse

    global MANIFEST_FILE, FACTS_OUTPUT, COMPANY_OUTPUT
    global SUMMARY_OUTPUT, ERROR_OUTPUT

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest", default=None,
        help="Manifest CSV (default: nse_brsr_download_manifest.csv)",
    )
    parser.add_argument(
        "--output-dir", default=None,
        help="Directory for output CSVs (default: data/processed)",
    )
    args = parser.parse_args()

    if args.manifest:
        MANIFEST_FILE = Path(args.manifest)

    if args.output_dir:
        out = Path(args.output_dir)
        out.mkdir(parents=True, exist_ok=True)
        FACTS_OUTPUT = out / "multi_company_facts.csv"
        COMPANY_OUTPUT = out / "multi_company_details.csv"
        SUMMARY_OUTPUT = out / "multi_company_extraction_summary.csv"
        ERROR_OUTPUT = out / "multi_company_errors.csv"

    print("=" * 75)
    print("MULTI-COMPANY BRSR FACT EXTRACTION")
    print("=" * 75)
    print("Manifest:", MANIFEST_FILE)

    PROCESSED_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    if not MANIFEST_FILE.exists():

        print("Download manifest not found.")
        return

    manifest = pd.read_csv(
        MANIFEST_FILE,
        dtype=str
    ).fillna("")

    manifest = manifest[
        manifest["download_status"].isin(
            ["SUCCESS", "SUCCESS_SANITIZED", "ALREADY_DOWNLOADED"]
        )
    ]

    print("Successful reports in manifest:", len(manifest))

    taxonomy_lookup = load_taxonomy()

    version_lookups, crosswalk = load_version_lookups()

    all_facts = []
    all_companies = []
    summaries = []
    errors = []

    for index, record in manifest.iterrows():

        company_name = record["company_name"]

        report_url = record["report_url"]

        xml_path = Path(record["local_file_path"])

        print("\n" + "=" * 75)
        print(
            f"REPORT {len(summaries) + len(errors) + 1}"
        )
        print("=" * 75)

        try:

            if not xml_path.exists():

                raise FileNotFoundError(
                    f"XML file not found: {xml_path}"
                )

            facts = extract_report(
                xml_path,
                company_name,
                report_url
            )

            if not facts:

                raise ValueError(
                    "No XBRL facts found in report."
                )

            facts = add_taxonomy_mapping(
                facts,
                taxonomy_lookup,
                version_lookups,
                crosswalk
            )

            # When the manifest comes from the report verifier, carry the
            # verified master identity and FY slot onto every fact. The
            # fact "isin" column is the XBRL entity identifier, which is
            # the CIN in pre-2026 taxonomy filings.
            for source, target in MANIFEST_PASSTHROUGH.items():
                if source in record.index:
                    for row in facts:
                        row[target] = record[source]

            company_details = build_company_details(
                company_name,
                facts,
                report_url
            )

            all_facts.extend(facts)

            all_companies.append(company_details)

            dataframe = pd.DataFrame(facts)

            matched_count = (
                dataframe["taxonomy_match_status"]
                == "MATCHED"
            ).sum()

            unique_metrics = dataframe[
                "metric_name"
            ].nunique()

            summaries.append({

                "company_name": company_name,

                "isin": company_details["isin"],

                "total_facts": len(facts),

                "unique_metrics": unique_metrics,

                "taxonomy_matched_facts": int(
                    matched_count
                ),

                "taxonomy_unmatched_facts": (
                    len(facts) - int(matched_count)
                ),

                "financial_years": " | ".join(
                    sorted(
                        dataframe["financial_year"]
                        .dropna()
                        .unique()
                    )
                ),

                "reporting_fy": dataframe["reporting_fy"].iloc[0],

                "report_fy_start": dataframe["report_fy_start"].iloc[0],

                "report_fy_end": dataframe["report_fy_end"].iloc[0],

                "facts_period_unmatched": int(
                    (dataframe["period_role"] == "UNMATCHED").sum()
                ),

                "facts_nil_or_empty": int(
                    (dataframe["value_status"] != "REPORTED").sum()
                ),

                "status": "SUCCESS",

                "source_file": xml_path.name

            })

            print("Taxonomy matches:", matched_count)

        except Exception as error:

            print("ERROR:", error)

            errors.append({

                "company_name": company_name,

                "report_url": report_url,

                "local_file_path": str(xml_path),

                "error": str(error),

                "status": "FAILED"

            })

    # ======================================================
    # STEP 7: SAVE CONSOLIDATED CSV FILES
    # ======================================================

    if all_facts:

        facts_df = pd.DataFrame(all_facts)

        facts_df.to_csv(
            FACTS_OUTPUT,
            index=False,
            encoding="utf-8-sig"
        )

    if all_companies:

        companies_df = pd.DataFrame(all_companies)

        companies_df.to_csv(
            COMPANY_OUTPUT,
            index=False,
            encoding="utf-8-sig"
        )

    if summaries:

        summary_df = pd.DataFrame(summaries)

        summary_df.to_csv(
            SUMMARY_OUTPUT,
            index=False,
            encoding="utf-8-sig"
        )

    # Always rewrite the errors file so results from an earlier run
    # cannot be mistaken for this run's errors.
    errors_df = pd.DataFrame(
        errors,
        columns=[
            "company_name", "report_url", "local_file_path",
            "error", "status",
        ],
    )

    errors_df.to_csv(
        ERROR_OUTPUT,
        index=False,
        encoding="utf-8-sig"
    )

    # ======================================================
    # STEP 8: FINAL SUMMARY
    # ======================================================

    print("\n" + "=" * 75)
    print("MULTI-COMPANY EXTRACTION SUMMARY")
    print("=" * 75)

    print("Reports processed:", len(manifest))

    print("Successful companies:", len(all_companies))

    print("Failed reports:", len(errors))

    print("Total extracted facts:", len(all_facts))

    print(
        "Unique companies:",
        len({
            row["company_name"]
            for row in all_facts
        })
    )

    if all_facts:

        final_df = pd.DataFrame(all_facts)

        print(
            "Unique metrics:",
            final_df["metric_name"].nunique()
        )

        print(
            "Unique ISINs:",
            final_df["isin"].nunique()
        )

        print("\nTaxonomy matching:")

        print(
            final_df["taxonomy_match_status"]
            .value_counts()
        )

        print("\nFinancial years found:")

        print(
            final_df["financial_year"]
            .value_counts(dropna=False)
        )

    print("\nOUTPUT FILES")
    print("-" * 75)

    print("Facts:", FACTS_OUTPUT)

    print("Company details:", COMPANY_OUTPUT)

    print("Extraction summary:", SUMMARY_OUTPUT)

    print("Errors:", ERROR_OUTPUT)

    print("\nExtraction process completed.")


if __name__ == "__main__":
    main()
