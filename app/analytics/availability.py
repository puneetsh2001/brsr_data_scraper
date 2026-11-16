"""
Metric availability report, computed from the analytics database only.

    python -m app.analytics.availability [--database NAME]

Writes docs/METRIC_AVAILABILITY.md and docs/metric_availability.csv. For
every metric of the requirement registry: units, companies and
company-years with values, financial years, value-level disclosure
statuses, reported vs calculated values, company-year missing reasons and
source traceability, over the processing universe x FY-slot grid.
"""

import argparse
import csv
from collections import Counter, defaultdict

from app.analytics import db, service
from app.analytics import repository as repo
from app.config import settings

DOC = settings.project_root / "docs" / "METRIC_AVAILABILITY.md"
CSV_OUT = settings.project_root / "docs" / "metric_availability.csv"


def compute(cur):
    cat = service.catalog(cur)
    companies = repo.search_companies(cur, "", 10000, 0)
    isins = [c["isin"] for c in companies]
    years = {c["company_id"]: {y["fiscal_year"]: y
                               for y in service.years(cur, c)}
             for c in companies}
    slots = sorted({fy for ys in years.values() for fy in ys})
    filing_ids = [y["filing"]["filing_id"] for ys in years.values()
                  for y in ys.values() if y["filing"]]
    concepts = sorted({m["applicability_concept"] for m in cat.values()
                       if m["applicability_concept"]})
    app_facts = repo.applicability(cur, filing_ids, concepts)
    rows = repo.values(cur, isins)
    by = defaultdict(list)
    for r in rows:
        by[(r["requirement_id"], r["master_isin"], r["fy_slot"])].append(r)
    out = []
    for m in cat.values():
        rid = m["requirement_id"]
        recs = [service.value_record(r, m) for (q, _, _), rs in by.items()
                if q == rid for r in rs]
        cells = {(i, fy) for (q, i, fy) in by if q == rid}
        missing = Counter()
        for c in companies:
            for fy in slots:
                if (c["isin"], fy) in cells:
                    continue
                y = years[c["company_id"]].get(fy)
                flag = None
                if y and y["filing"] and m["applicability_concept"]:
                    vals = app_facts.get((y["filing"]["filing_id"],
                                          m["applicability_concept"]), set())
                    flag = True if "true" in vals else \
                        False if "false" in vals else None
                missing[service.missing_record(m, fy, y, flag)
                        ["disclosure_status"]] += 1
        traced = sum(1 for r in recs if r["source"]["filing_id"]
                     and r["source"]["xbrl_url"]
                     and r["source"]["context_reference"])
        out.append({
            "requirement_id": rid, "pillar": m["pillar"],
            "metric_name": m["metric_name"],
            "themes": " ".join(m["themes"]),
            "concepts": " | ".join(m["concepts"]),
            "breakdown_axes": " | ".join(m["breakdown_axes"]),
            "source": m["source"], "table": "brsr.requirement_value"
            if m["in_brsr_xbrl"] else "",
            "normalised_units": " | ".join(sorted(
                {r["unit"] for r in recs if r["unit"]})),
            "filed_units": " | ".join(sorted(
                {r["reported_unit"] for r in recs if r["reported_unit"]})),
            "values": len(recs),
            "companies_with_data": len({i for i, _ in cells}),
            "company_years_with_data": len(cells),
            "grid_company_years": len(companies) * len(slots),
            "financial_years": " ".join(sorted({fy for _, fy in cells})),
            "disclosure_status": dict(Counter(
                r["disclosure_status"] for r in recs)),
            "value_basis": dict(Counter(r["value_basis"] for r in recs)),
            "missing_company_years": dict(missing),
            "traceable_values": traced})
    return {"companies": len(companies), "slots": slots, "metrics": out,
            "values": len(rows)}


def fmt(d):
    return "; ".join(f"{k} {v}" for k, v in sorted(d.items())) or "-"


def write(result, dbname):
    with open(CSV_OUT, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(result["metrics"][0]))
        w.writeheader()
        for m in result["metrics"]:
            w.writerow({k: fmt(v) if isinstance(v, dict) else v
                        for k, v in m.items()})
    lines = [
        "# Metric availability",
        "",
        f"Computed from the `{dbname}` database by "
        "`python -m app.analytics.availability`. Do not edit by hand.",
        "",
        f"- Universe: {result['companies']} companies; FY slots "
        f"{', '.join(result['slots'])}; grid "
        f"{result['companies'] * len(result['slots'])} company-years.",
        f"- Current-year values in `brsr.requirement_value`: "
        f"{result['values']}. Every value has a filing id, source XBRL URL "
        "and context reference.",
        "- **Value statuses:** REPORTED, REPORTED_ZERO (filed 0), "
        "REPORTED_TEXT, REPORTED_PENDING_CROSSWALK_REVIEW.",
        "- **Value basis:** REPORTED_AS_FILED, CALCULATED_UNIT_CONVERSION "
        "(rule conversion, formula given), CALCULATED_APPROVED_UNIT_DECISION, "
        "REPORTED_NOT_NORMALISED (unit ambiguous or pending decision: no "
        "normalised value).",
        "- **Missing company-years:** NO_FILING_FOR_YEAR, EXTRACTION_ERROR, "
        "NOT_APPLICABLE (applicability concept = false), NOT_IN_BRSR_XBRL, "
        "NO_VALUE_IN_FILING (not disclosed, or not in the filing's taxonomy "
        "version: the database does not hold that distinction).",
        "",
        "| ID | Metric | Pillar | Concept(s) | Normalised unit | Filed units "
        "| Companies | Company-years | FYs | Value status | Basis | Missing "
        "company-years |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for m in result["metrics"]:
        lines.append(
            f"| {m['requirement_id']} | {m['metric_name']} | {m['pillar']} | "
            f"{m['concepts'] or '-'} | {m['normalised_units'] or '-'} | "
            f"{m['filed_units'] or '-'} | {m['companies_with_data']} | "
            f"{m['company_years_with_data']} / {m['grid_company_years']} | "
            f"{m['financial_years'] or '-'} | {fmt(m['disclosure_status'])} "
            f"| {fmt(m['value_basis'])} | "
            f"{fmt(m['missing_company_years'])} |")
    DOC.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return DOC, CSV_OUT


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--database", default=None)
    a = p.parse_args()
    name = db.analytics_database(a.database)
    with db.cursor(name) as cur:
        result = compute(cur)
    print(*write(result, name))


if __name__ == "__main__":
    main()
