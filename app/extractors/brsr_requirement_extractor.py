"""
Select requirement-level BRSR values from extracted facts.

Uses data/reference/brsr_requirement_concept_map.csv, which maps each
required metric to a current-taxonomy concept plus dimension filters.
Facts from older taxonomy versions are matched through their
current_concept column (set by the extractor from the crosswalk).

Only facts for the report's own current year (period_role == CY) are
selected, so each FY slot is taken from the report that covers it.
Raw values, units and source references are kept unchanged; no unit
conversion is done here.

Outputs (data/processed/nifty100/):
  brsr_requirement_values.csv    one row per selected fact
  brsr_requirement_coverage.csv  requirement x company x FY slot status
  brsr_requirement_summary.csv   requirement x FY slot status counts

Status per requirement x company x FY slot:
  REPORTED                  at least one non-zero / non-empty value
  REPORTED_ZERO             only numeric zero values reported
  REPORTED_PENDING_CROSSWALK_REVIEW
                            values exist only through a crosswalk mapping
                            still in CANDIDATE_REVIEW; kept as evidence in
                            the values file, not counted as reported
  NOT_APPLICABLE            the report's applicability flag is false
  NOT_IN_TAXONOMY_VERSION   the report's taxonomy version had no concept
                            for this requirement (added later by SEBI)
  NOT_DISCLOSED             report exists, concept existed, no fact
  NO_REPORT                 no verified report for the FY slot (see
                            nse_brsr_coverage_matrix.csv for why)
  EXTERNAL_SOURCE_REQUIRED  requirement not covered by BRSR XBRL

No database access.

Run:
    python -m app.extractors.brsr_requirement_extractor
"""

from pathlib import Path

import pandas as pd


BASE_DIR = Path(__file__).resolve().parents[2]
NIFTY_DIR = BASE_DIR / "data" / "processed" / "nifty100"

MAP_FILE = BASE_DIR / "data" / "reference" / "brsr_requirement_concept_map.csv"
FACTS_FILE = NIFTY_DIR / "multi_company_facts.csv"
EXTRACTION_MANIFEST = NIFTY_DIR / "nse_brsr_extraction_manifest.csv"
COVERAGE_MATRIX = NIFTY_DIR / "nse_brsr_coverage_matrix.csv"

VALUES_OUTPUT = NIFTY_DIR / "brsr_requirement_values.csv"
# Previous-year comparatives (period_role PY) - used by the normaliser to
# cross-check each slot against the next report's restated figure.
PY_VALUES_OUTPUT = NIFTY_DIR / "brsr_requirement_values_py.csv"
COVERAGE_OUTPUT = NIFTY_DIR / "brsr_requirement_coverage.csv"
SUMMARY_OUTPUT = NIFTY_DIR / "brsr_requirement_summary.csv"

FACT_COLUMNS = [
    "company_name", "master_isin", "master_nse_symbol", "fy_slot",
    "reporting_fy", "period_role", "metric_name", "current_concept",
    "crosswalk_status", "taxonomy_version", "data_type", "value",
    "value_status",
    "unit_reference", "decimals", "dimensions", "period_start",
    "period_end", "instant_date", "context_reference", "source_file",
    "source_url",
]

FALSE_VALUES = {"false", "no", "not applicable", "na"}
CANDIDATE = "CANDIDATE_REVIEW"

CURRENT_CONCEPTS_FILE = (
    BASE_DIR / "data" / "processed" / "nse_brsr_taxonomy_concepts.csv"
)
CROSSWALK_FILE = (
    BASE_DIR / "data" / "processed" / "taxonomy_versions"
    / "nse_brsr_concept_crosswalk.csv"
)


def concepts_available_by_version():
    """
    {taxonomy_version: set of current-taxonomy concepts that a report
    filed in that version could contain}. Lets NOT_IN_TAXONOMY_VERSION
    (concept did not exist yet) be separated from NOT_DISCLOSED.
    """

    available = {}

    current = pd.read_csv(CURRENT_CONCEPTS_FILE, dtype=str).fillna("")
    current_set = set(current["concept_name"])

    if CROSSWALK_FILE.exists():
        crosswalk = pd.read_csv(CROSSWALK_FILE, dtype=str).fillna("")
        crosswalk = crosswalk[crosswalk["current_concept"] != ""]
        for version, group in crosswalk.groupby("source_version"):
            available[version] = set(group["current_concept"])

    return available, current_set


def load_dimension_name_map():
    """
    {version: {old axis/member name: current name}} from the crosswalk, so
    older dimensions (e.g. CompliantsAxis) line up with current ones
    (ComplaintsAxis).
    """

    names = {}
    if not CROSSWALK_FILE.exists():
        return names

    crosswalk = pd.read_csv(CROSSWALK_FILE, dtype=str).fillna("")
    crosswalk = crosswalk[
        (crosswalk["current_concept"] != "")
        & (crosswalk["source_concept"] != crosswalk["current_concept"])
    ]
    for row in crosswalk.itertuples():
        names.setdefault(row.source_version, {})[row.source_concept] = (
            row.current_concept
        )
    return names


def parse_dimensions(text, name_map=None):
    """'in-capmkt:GenderAxis=in-capmkt:MaleMember | ...' -> dict,
    with axis/member names translated to the current taxonomy."""

    name_map = name_map or {}
    dims = {}
    for part in (text or "").split(" | "):
        if "=" not in part:
            continue
        axis, member = part.split("=", 1)
        axis = axis.split(":")[-1]
        member = member.split(":")[-1]
        dims[name_map.get(axis, axis)] = name_map.get(member, member)
    return dims


def parse_filter(text):
    out = {}
    for part in (text or "").split(";"):
        if "=" in part:
            axis, member = part.split("=", 1)
            out[axis.strip()] = member.strip()
    return out


def dims_match(dims, required, breakdown_axes):
    for axis, member in required.items():
        if dims.get(axis) != member:
            return False
    allowed = set(required) | breakdown_axes
    return set(dims) <= allowed


def is_zero(value):
    try:
        return float(str(value).replace(",", "")) == 0.0
    except ValueError:
        return False


def main():
    req = pd.read_csv(MAP_FILE, dtype=str).fillna("")
    xbrl_req = req[req["source"] == "BRSR_XBRL"].copy()
    xbrl_req["_filter"] = xbrl_req["dimension_filter"].map(parse_filter)
    xbrl_req["_breakdown"] = xbrl_req["breakdown_axes"].map(
        lambda s: {a.strip() for a in s.split(";") if a.strip()}
    )

    by_concept = {}
    for row in xbrl_req.to_dict("records"):
        by_concept.setdefault(row["current_concept"], []).append(row)

    applicability_concepts = set(xbrl_req["applicability_concept"]) - {""}
    wanted = set(by_concept) | applicability_concepts

    dimension_names = load_dimension_name_map()

    print("Requirements mapped to BRSR XBRL:", len(xbrl_req))
    print("Reading facts...")

    selected, selected_py, applicability = [], [], {}

    for chunk in pd.read_csv(
        FACTS_FILE, dtype=str, keep_default_na=False,
        usecols=FACT_COLUMNS, chunksize=200_000,
    ):
        chunk = chunk[
            chunk["current_concept"].isin(wanted)
            & chunk["period_role"].isin(["CY", "PY"])
        ]

        for fact in chunk.to_dict("records"):
            concept = fact["current_concept"]
            target = selected if fact["period_role"] == "CY" else selected_py

            if concept in applicability_concepts and fact["period_role"] == "CY":
                applicability[
                    (fact["master_isin"], fact["fy_slot"], concept)
                ] = fact["value"].strip().lower()

            dims = parse_dimensions(
                fact["dimensions"],
                dimension_names.get(fact["taxonomy_version"]),
            )

            for rule in by_concept.get(concept, []):
                if not dims_match(dims, rule["_filter"], rule["_breakdown"]):
                    continue
                breakdown = " | ".join(
                    f"{axis}={member}" for axis, member in sorted(dims.items())
                    if axis in rule["_breakdown"]
                )
                target.append({
                    "requirement_id": rule["requirement_id"],
                    "pillar": rule["pillar"],
                    "metric_key": rule["metric_key"],
                    "breakdown": breakdown,
                    **{k: fact[k] for k in FACT_COLUMNS},
                })

    values = pd.DataFrame(selected)
    values.to_csv(VALUES_OUTPUT, index=False, encoding="utf-8-sig")
    print("Requirement values selected:", len(values))

    pd.DataFrame(selected_py).to_csv(
        PY_VALUES_OUTPUT, index=False, encoding="utf-8-sig"
    )
    print("Previous-year comparatives selected:", len(selected_py))

    # ------------------------------------------------------
    # Coverage per requirement x company x FY slot
    # ------------------------------------------------------

    reports = pd.read_csv(EXTRACTION_MANIFEST, dtype=str).fillna("")
    report_keys = set(zip(reports["isin"], reports["true_fy_slot"]))
    report_version = {
        (r.isin, r.true_fy_slot): r.taxonomy_version
        for r in reports.itertuples()
    }
    available, current_set = concepts_available_by_version()

    matrix = pd.read_csv(COVERAGE_MATRIX, dtype=str).fillna("")
    matrix = matrix[matrix["in_download_window"] == "True"]

    grouped = {}
    if len(values):
        for (isin, slot, rid), group in values.groupby(
            ["master_isin", "fy_slot", "requirement_id"]
        ):
            grouped[(isin, slot, rid)] = group

    # A requirement may map to several concepts (e.g. a current concept
    # plus a fallback used by older taxonomies); coverage is per
    # requirement, so collect its concepts and report it once.
    req_concepts = (
        req[req["current_concept"] != ""]
        .groupby("requirement_id")["current_concept"].agg(set).to_dict()
    )
    unique_req = req.drop_duplicates("requirement_id")

    rows = []
    for cell in matrix.to_dict("records"):
        isin, slot = cell["isin"], cell["fy_slot"]
        has_report = (isin, slot) in report_keys
        version_concepts = available.get(
            report_version.get((isin, slot), ""), current_set
        )

        for rule in unique_req.to_dict("records"):
            rid = rule["requirement_id"]
            group = grouped.get((isin, slot, rid))
            n_values = 0 if group is None else len(group)
            # values reached only through a crosswalk mapping that is still
            # CANDIDATE_REVIEW are kept in the values file as evidence but
            # never count as REPORTED (owner decision 2026-11-12)
            approved = None if group is None else group[
                group["crosswalk_status"] != CANDIDATE]
            n_candidate = n_values - (0 if approved is None else len(approved))

            if rule["source"] != "BRSR_XBRL":
                status = "EXTERNAL_SOURCE_REQUIRED"
            elif not has_report:
                status = "NO_REPORT"
            elif group is not None and approved.empty:
                status = "REPORTED_PENDING_CROSSWALK_REVIEW"
            elif group is not None and \
                    not approved["value"].map(is_zero).all():
                status = "REPORTED"
            elif group is not None:
                status = "REPORTED_ZERO"
            elif applicability.get(
                (isin, slot, rule["applicability_concept"])
            ) in FALSE_VALUES:
                status = "NOT_APPLICABLE"
            elif not (req_concepts.get(rid, set()) & version_concepts):
                status = "NOT_IN_TAXONOMY_VERSION"
            else:
                status = "NOT_DISCLOSED"

            rows.append({
                "company_name": cell["company_name"],
                "isin": isin,
                "nse_symbol": cell["nse_symbol"],
                "fy_slot": slot,
                "report_status": cell["status"],
                "requirement_id": rid,
                "pillar": rule["pillar"],
                "requirement": rule["requirement"],
                "metric_key": rule["metric_key"],
                "source": rule["source"],
                "status": status,
                "values_found": n_values,
                "values_from_candidate_mapping": n_candidate,
            })

    coverage = pd.DataFrame(rows)
    coverage.to_csv(COVERAGE_OUTPUT, index=False, encoding="utf-8-sig")

    summary = (
        coverage.pivot_table(
            index=["requirement_id", "pillar", "requirement", "source"],
            columns=["fy_slot", "status"],
            values="isin", aggfunc="count", fill_value=0,
        )
    )
    summary.columns = [f"{slot} {status}" for slot, status in summary.columns]
    summary = summary.reset_index()
    summary.to_csv(SUMMARY_OUTPUT, index=False, encoding="utf-8-sig")

    print("\nStatus by FY slot (all requirements):")
    print(pd.crosstab(coverage["status"], coverage["fy_slot"]).to_string())

    reported = coverage[coverage["source"] == "BRSR_XBRL"].copy()
    reported["has_data"] = reported["status"].isin(
        ["REPORTED", "REPORTED_ZERO", "NOT_APPLICABLE"]
    )
    with_report = reported[
        ~reported["status"].isin(["NO_REPORT", "NOT_IN_TAXONOMY_VERSION"])
    ]
    weakest = (
        with_report.groupby(["requirement_id", "requirement"])["has_data"]
        .mean().sort_values().head(20)
    )
    print("\nLowest coverage requirements (share of company-years with a "
          "value or an explicit not-applicable flag):")
    print(weakest.round(3).to_string())

    print("\nOutputs:")
    for path in (VALUES_OUTPUT, COVERAGE_OUTPUT, SUMMARY_OUTPUT):
        print(" ", path)


if __name__ == "__main__":
    main()
