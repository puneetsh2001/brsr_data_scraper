"""
Validation rules engine (Phase 9).

Rule categories (roadmap): STRUCTURAL, ENVIRONMENTAL, SOCIAL, GOVERNANCE,
INTENSITY, RESTATEMENT.

Input: the production normalisation in normalised/ (written by the
factor-model engine since the 2026-11-12 switch). Everything is read only.

Two kinds of rules:
  - legacy checks C01-C08, C10, C11 of app.validators.
    brsr_normalisation_validator, run unchanged through an adapter so their
    meaning is identical; their issues are reconciled with the existing
    issues file
  - new rules, which record PASS / FAIL / NOT_EVALUATED (with the reason)
    per company, FY and breakdown. Missing values are never treated as
    zero and never count as a pass.

Deferred owner decisions are not acted on: values linked to the deferred
issues register carry their issue ids; restatement notes on
OtherStakeHolderAxis rows keep the legacy row-position result and are
tagged DQ-039 (identity unreliable) instead of being re-matched.

Nothing is corrected. Outputs: data/processed/nifty100/validation/

Run:
    python -m app.validation.rules_engine
"""

from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation

import pandas as pd

from app.config import settings
from app.core import deferred_issues
from app.core.logging_setup import get_logger
from app.normalisation.factor_model import read_production
from app.validators import brsr_normalisation_validator as legacy

log = get_logger(__name__, "rules_engine")

N = settings.processed_dir / "nifty100"
# production normalisation (factor-model engine since the 2026-11-12 switch)
PRODUCTION_DIR = N / "normalised"
CY_FILE = PRODUCTION_DIR / "brsr_requirement_values_normalised.csv"
PY_FILE = PRODUCTION_DIR / "brsr_requirement_values_py_normalised.csv"
LEGACY_ISSUES = N / "normalised" / "brsr_normalisation_validation_issues.csv"
MANIFEST = settings.processed_dir / "registry" / "filing_manifest.csv"
OUTPUT_DIR = N / "validation"

SUM_TOL = Decimal("0.01")         # relative; no absolute floors
RATIO_TOL = Decimal("0.01")
PCT_POINT_TOL = Decimal("0.5")    # percentage points for recomputed shares
OTHER_AXIS = "OtherStakeHolderAxis"

# ==========================================================
# rule registry
# ==========================================================

RULES = [
    # id, category, pillar, severity, description, requirements, legacy
    ("STR01", "STRUCTURAL", "ALL", "ERROR",
     "final value = original value x multiplier (exact Decimal)", "", "C01"),
    ("STR02", "STRUCTURAL", "ALL", "ERROR",
     "converted rows carry value, unit, factor and formula; unconverted "
     "rows carry no value", "", "C02"),
    ("STR03", "STRUCTURAL", "ALL", "ERROR",
     "one normalised unit per requirement", "", "C03"),
    ("STR04", "STRUCTURAL", "ALL", "WARNING",
     "no duplicate company / FY / requirement / breakdown values", "", "C04"),
    ("STR05", "STRUCTURAL", "ALL", "WARNING",
     "no negative quantities; percentages not above 100", "", "C05"),
    ("STR06", "STRUCTURAL", "ALL", "ERROR",
     "the same ambiguous unit symbol is read the same way within a report",
     "", "C11"),
    ("STR07", "STRUCTURAL", "ALL", "ERROR",
     "every value is traceable: source file, context, URL, and the filing "
     "is in the registry processing scope", "", ""),
    ("STR08", "STRUCTURAL", "ALL", "ERROR",
     "count metrics are non-negative integers",
     "S01 S02 S12 S13 S14 S15 S17 S18 S19 S20 S22 S26 G01 G02 G10 G11 G12 "
     "G13 G14 G15 G16 G17 G18 G19", ""),
    ("STR09", "STRUCTURAL", "ALL", "INFO",
     "value is linked to an open deferred data-quality issue", "", ""),
    ("ENV01", "ENVIRONMENTAL", "Environmental", "WARNING",
     "component sums: energy E07 = E05 + E06; water E14 = E09..E13; waste "
     "E29 = E21..E28", "E07 E14 E29", "C07"),
    ("ENV02", "ENVIRONMENTAL", "Environmental", "WARNING",
     "renewable energy E05 >= electricity E01 + fuel E03 (other sources may "
     "add)", "E05 E01 E03", ""),
    ("ENV03", "ENVIRONMENTAL", "Environmental", "WARNING",
     "non-renewable energy E06 >= electricity E02 + fuel E04", "E06 E02 E04",
     ""),
    ("ENV04", "ENVIRONMENTAL", "Environmental", "WARNING",
     "water consumption E15 <= water withdrawal E14", "E15 E14", ""),
    ("ENV05", "ENVIRONMENTAL", "Environmental", "WARNING",
     "waste recovered E32 >= recycled E30 + re-used E31", "E32 E30 E31", ""),
    ("ENV06", "ENVIRONMENTAL", "Environmental", "WARNING",
     "waste disposed E35 >= incinerated E33 + landfilled E34", "E35 E33 E34",
     ""),
    ("ENV07", "ENVIRONMENTAL", "Environmental", "WARNING",
     "implied Scope 1+2 emission factor (E17+E18)/E07 within 0.005-1.0 "
     "tCO2e/GJ", "E17 E18 E07", ""),
    ("SOC01", "SOCIAL", "Social", "WARNING",
     "gender total = male + female + other gender (headcount, differently "
     "abled, safety training)", "S01 S02 S15", ""),
    ("SOC02", "SOCIAL", "Social", "WARNING",
     "female wage share S09 = S07 / S08 x 100 (+/- 0.5 percentage points)",
     "S09 S07 S08", ""),
    ("SOC03", "SOCIAL", "Social", "WARNING",
     "POSH complaints upheld S18 <= reported S17", "S18 S17", ""),
    ("SOC04", "SOCIAL", "Social", "WARNING",
     "POSH pending at year end S20 <= filed S19 + pending at start (same "
     "report PY)", "S20 S19", ""),
    ("GOV01", "GOVERNANCE", "Governance", "ERROR",
     "female directors G02 <= total directors G01", "G02 G01", ""),
    ("GOV02", "GOVERNANCE", "Governance", "WARNING",
     "female board share G03 = G02 / G01 x 100 (+/- 0.5 percentage points)",
     "G03 G02 G01", ""),
    ("GOV03", "GOVERNANCE", "Governance", "WARNING",
     "stakeholder complaints pending G17 <= filed G16 + pending at start "
     "(same report PY), per stakeholder group", "G17 G16", ""),
    ("GOV04", "GOVERNANCE", "Governance", "WARNING",
     "consumer complaints pending G19 <= received G18 + pending at start "
     "(same report PY), per category", "G19 G18", ""),
    ("INT01", "INTENSITY", "Environmental", "WARNING",
     "reported per-INR intensity vs total / turnover (E08, E16, E19)",
     "E08 E16 E19 A06", "C06"),
    ("INT02", "INTENSITY", "Environmental", "ERROR",
     "converted intensity values carry a per-INR unit", "E08 E16 E19", ""),
    ("RST01", "RESTATEMENT", "ALL", "INFO",
     "value vs the next report's restated comparative (relative 1 %)", "",
     "C08"),
    ("RST02", "RESTATEMENT", "ALL", "WARNING",
     "year-on-year change above 10x", "A06 E07 E14 E17 E18 E29", "C10"),
]
RULE_COLUMNS = ["rule_id", "category", "pillar", "severity_on_fail",
                "description", "requirements", "legacy_check"]
LEGACY_TO_RULE = {r[6]: r[0] for r in RULES if r[6]}
SEVERITY = {r[0]: r[3] for r in RULES}

COUNT_METRICS = set(dict((r[0], r[5]) for r in RULES)["STR08"].split())
INTENSITIES = {"E08", "E16", "E19"}


def dec(v):
    try:
        return None if v in ("", None) else Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None


def rel_diff(a, b):
    base = max(abs(a), abs(b))
    return Decimal(0) if base == 0 else abs(a - b) / base


# ==========================================================
# results
# ==========================================================

class Results:
    def __init__(self, issue_index):
        self.rows = []
        self.issue_index = issue_index

    def add(self, rule, status, row, detail="", observed="", expected="",
            requirement=None, breakdown=None):
        req = requirement or row.get("requirement_id", "")
        self.rows.append({
            "rule_id": rule, "category": RULE_CATEGORY[rule],
            "status": status,
            "severity": SEVERITY[rule] if status == "FAIL" else "",
            "company_name": row.get("company_name", ""),
            "master_isin": row.get("master_isin", ""),
            "master_nse_symbol": row.get("master_nse_symbol", ""),
            "fy_slot": row.get("fy_slot", ""),
            "requirement_id": req,
            "breakdown": row.get("breakdown", "") if breakdown is None
            else breakdown,
            "observed": observed, "expected": expected, "detail": detail,
            "source_file": row.get("source_file", ""),
            "deferred_issue_ids": " | ".join(self.issue_index.get(
                (row.get("master_isin", ""), row.get("fy_slot", ""),
                 req.split(" ")[0]), []))})


RULE_CATEGORY = {r[0]: r[1] for r in RULES}


# ==========================================================
# legacy checks through an adapter (meaning unchanged)
# ==========================================================

def legacy_frame(df):
    """Factor-model columns -> the column names the legacy checks read."""
    return df.assign(
        normalised_value=df["final_value"],
        normalised_unit=df["final_unit"],
        conversion_factor=df["actual_numeric_multiplier_on_original"],
        conversion_status=df["final_status"],
        conversion_formula=df["factor_formula"],
        proposed_normalised_value=df["proposed_value_if_assumption_approved"],
        unit_decision=df["unit_assumption_decision"])


def run_legacy(cy, py):
    issues = legacy.Issues()
    lc, lp = legacy_frame(cy), legacy_frame(py)
    legacy.row_checks(lc, issues)
    legacy.intensity_and_sum_checks(lc, issues)
    legacy.py_crosscheck(lc, lp, issues)
    legacy.yoy_outliers(lc, issues)
    legacy.unit_decision_consistency(lc, issues)
    return pd.DataFrame(issues.rows)


def legacy_results(legacy_issues, results):
    for r in legacy_issues.to_dict("records"):
        rule = LEGACY_TO_RULE[r["check"].split("_")[0]]
        detail = r["detail"]
        if rule == "RST01" and OTHER_AXIS in r.get("breakdown", ""):
            detail += (" | IDENTITY_UNRELIABLE: OtherStakeHolderAxis row "
                       "matched by row position; name-based matching "
                       "deferred (DQ-039)")
        results.add(rule, "FAIL", r, detail=f"[{r['check']} "
                    f"{r['severity']}] {detail}")
        results.rows[-1]["severity"] = r["severity"]


def reconcile_legacy(legacy_issues):
    """Same issues as the existing validator output (C09 not ported)?"""
    old = pd.read_csv(LEGACY_ISSUES, dtype=str, keep_default_na=False)
    old = old[~old["check"].str.startswith("C09")]
    cols = ["check", "severity", "master_isin", "fy_slot", "requirement_id",
            "breakdown", "source_file", "detail"]
    new = legacy_issues.astype(str)
    a = Counter(map(tuple, old[cols].values.tolist()))
    b = Counter(map(tuple, new[cols].values.tolist()))
    rows = []
    for check in sorted(set(old["check"]) | set(new["check"])):
        oa = sum(v for k, v in a.items() if k[0] == check)
        nb = sum(v for k, v in b.items() if k[0] == check)
        same = sum(v for k, v in (a & b).items() if k[0] == check)
        rows.append({"legacy_check": check,
                     "rule_id": LEGACY_TO_RULE[check.split("_")[0]],
                     "existing_issues": oa, "engine_issues": nb,
                     "identical": same, "only_existing": oa - same,
                     "only_engine": nb - same})
    return pd.DataFrame(rows)


# ==========================================================
# new rules
# ==========================================================

def index(df):
    """{(isin, slot): {(req, breakdown): row}}"""
    out = defaultdict(dict)
    for r in df.to_dict("records"):
        out[(r["master_isin"], r["fy_slot"])][(r["requirement_id"],
                                               r["breakdown"])] = r
    return out


def structural(cy, results, scope_files):
    for r in cy.to_dict("records"):
        missing = [c for c in ("source_file", "context_reference",
                               "source_url") if not r[c]]
        if missing:
            results.add("STR07", "FAIL", r, f"missing {missing}")
        elif r["source_file"] not in scope_files:
            results.add("STR07", "FAIL", r,
                        "source file not in the registry processing scope")
        else:
            results.add("STR07", "PASS", r)

        if r["requirement_id"] in COUNT_METRICS:
            v = dec(r["final_value"])
            if v is None:
                results.add("STR08", "NOT_EVALUATED", r,
                            f"no numeric value ({r['final_status']})")
            elif v < 0 or v != v.to_integral_value():
                results.add("STR08", "FAIL", r, "count is negative or not "
                            "an integer", observed=r["final_value"])
            else:
                results.add("STR08", "PASS", r)
        if r["deferred_issue_ids"]:
            results.add("STR09", "FAIL", r,
                        f"open deferred issues {r['deferred_issue_ids']}")
            results.rows[-1]["deferred_issue_ids"] = r["deferred_issue_ids"]


def totals(slot):
    return {req: dec(row["final_value"]) for (req, b), row in slot.items()
            if b == ""}


def check_inequality(results, rule, key_row, lhs, rhs, values, label,
                     op="ge", tol=SUM_TOL):
    """lhs (op) sum(rhs); missing parts -> NOT_EVALUATED, never zero."""
    a = values.get(lhs)
    parts = [values.get(p) for p in rhs]
    req = " ".join([lhs] + rhs)
    if a is None or any(p is None for p in parts):
        absent = [x for x, v in zip([lhs] + rhs, [a] + parts) if v is None]
        results.add(rule, "NOT_EVALUATED", key_row,
                    f"no numeric value for {absent}", requirement=req,
                    breakdown="")
        return
    total = sum(parts)
    if op == "ge":
        ok = a >= total or rel_diff(a, total) <= tol
    else:
        ok = a <= total or rel_diff(a, total) <= tol
    results.add(rule, "PASS" if ok else "FAIL", key_row,
                "" if ok else f"{label}: {lhs}={a} vs {'+'.join(rhs)}="
                f"{total}", observed=str(a), expected=f"{'>=' if op == 'ge' else '<='} {total}",
                requirement=req, breakdown="")


def environmental(cy_idx, results):
    for (isin, slot), rows in cy_idx.items():
        key = next(iter(rows.values()))
        v = totals(rows)
        if not any(r.startswith("E") for r in v):
            continue
        check_inequality(results, "ENV02", key, "E05", ["E01", "E03"], v,
                         "renewable energy below its components")
        check_inequality(results, "ENV03", key, "E06", ["E02", "E04"], v,
                         "non-renewable energy below its components")
        check_inequality(results, "ENV04", key, "E15", ["E14"], v,
                         "water consumption above withdrawal", op="le")
        check_inequality(results, "ENV05", key, "E32", ["E30", "E31"], v,
                         "waste recovered below recycled + re-used")
        check_inequality(results, "ENV06", key, "E35", ["E33", "E34"], v,
                         "waste disposed below incinerated + landfilled")
        e17, e18, e07 = v.get("E17"), v.get("E18"), v.get("E07")
        if None in (e17, e18, e07) or e07 == 0:
            results.add("ENV07", "NOT_EVALUATED", key,
                        "E17, E18 or E07 missing / zero",
                        requirement="E17 E18 E07", breakdown="")
        else:
            factor = (e17 + e18) / e07
            lo, hi = legacy.EMISSION_FACTOR_RANGE
            results.add("ENV07", "PASS" if lo <= factor <= hi else "FAIL",
                        key, "" if lo <= factor <= hi else
                        f"implied {factor:.4G} tCO2e/GJ outside {lo}-{hi}",
                        observed=f"{factor:.6G}", expected=f"{lo}-{hi}",
                        requirement="E17 E18 E07", breakdown="")


def split_gender(breakdown):
    parts = [p for p in breakdown.split(" | ") if p]
    gender = [p for p in parts if p.startswith("GenderAxis=")]
    rest = " | ".join(p for p in parts if not p.startswith("GenderAxis="))
    return rest, gender[0].split("=")[1] if gender else ""


def social(cy_idx, results):
    for (isin, slot), rows in cy_idx.items():
        key = next(iter(rows.values()))
        groups = defaultdict(dict)
        for (req, b), row in rows.items():
            if req in ("S01", "S02", "S15"):
                rest, g = split_gender(b)
                groups[(req, rest)][g] = dec(row["final_value"])
        for (req, rest), g in groups.items():
            parts = [g.get(m) for m in ("MaleMember", "FemaleMember",
                                        "OtherGenderMember")]
            total = g.get("GenderMember")
            k = {**key, "breakdown": rest}
            if total is None or any(p is None for p in parts):
                results.add("SOC01", "NOT_EVALUATED", k,
                            "gender total or a gender member missing",
                            requirement=req, breakdown=rest)
                continue
            s = sum(parts)
            ok = rel_diff(total, s) <= SUM_TOL
            results.add("SOC01", "PASS" if ok else "FAIL", k,
                        "" if ok else f"total {total} vs male+female+other "
                        f"{s}", observed=str(total), expected=str(s),
                        requirement=req, breakdown=rest)

        v = totals(rows)
        share_check(results, "SOC02", key, v, "S09", "S07", "S08")
        if any(r in v for r in ("S17", "S18")):
            check_inequality(results, "SOC03", key, "S18", ["S17"], v,
                             "POSH upheld above reported", op="le")


def share_check(results, rule, key, v, share, num, den):
    s, a, b = v.get(share), v.get(num), v.get(den)
    req = f"{share} {num} {den}"
    if None in (s, a, b) or b == 0:
        results.add(rule, "NOT_EVALUATED", key,
                    f"{share}, {num} or {den} missing (or {den} = 0)",
                    requirement=req, breakdown="")
        return
    expected = a / b * 100
    ok = abs(s - expected) <= PCT_POINT_TOL
    results.add(rule, "PASS" if ok else "FAIL", key,
                "" if ok else f"{share}={s}% vs {num}/{den}x100="
                f"{expected:.4f}%", observed=str(s),
                expected=f"{expected:.4f}", requirement=req, breakdown="")


def pending_rule(results, rule, cy_rows, py_rows, pend, filed, key,
                 issue_index):
    """pending(CY) <= filed(CY) + pending(PY, same report), per breakdown."""
    breakdowns = {b for (req, b) in cy_rows if req == pend}
    for b in sorted(breakdowns):
        p = dec(cy_rows[(pend, b)]["final_value"])
        f = dec(cy_rows.get((filed, b), {}).get("final_value"))
        opening_row = py_rows.get((pend, b))
        o = dec(opening_row["final_value"]) if opening_row else None
        k = {**key, "breakdown": b}
        req = f"{pend} {filed}"
        if None in (p, f, o):
            results.add(rule, "NOT_EVALUATED", k,
                        "pending, filed or opening pending (same report PY) "
                        "missing", requirement=req, breakdown=b)
            continue
        ok = p <= f + o
        results.add(rule, "PASS" if ok else "FAIL", k,
                    "" if ok else f"pending {p} > filed {f} + opening {o}"
                    + (" (OtherStakeHolderAxis row: same-report identity)"
                       if OTHER_AXIS in b else ""),
                    observed=str(p), expected=f"<= {f + o}",
                    requirement=req, breakdown=b)


def governance_and_pending(cy_idx, py_by_file, results, issue_index):
    for (isin, slot), rows in cy_idx.items():
        key = next(iter(rows.values()))
        v = totals(rows)
        if "G01" in v or "G02" in v:
            check_inequality(results, "GOV01", key, "G02", ["G01"], v,
                             "female directors above total", op="le",
                             tol=Decimal(0))
            share_check(results, "GOV02", key, v, "G03", "G02", "G01")
        files = {r["source_file"] for r in rows.values()}
        py_rows = {}
        for f in files:
            py_rows.update(py_by_file.get(f, {}))
        pending_rule(results, "GOV03", rows, py_rows, "G17", "G16", key,
                     issue_index)
        pending_rule(results, "GOV04", rows, py_rows, "G19", "G18", key,
                     issue_index)
        pending_rule(results, "SOC04", rows, py_rows, "S20", "S19", key,
                     issue_index)


def intensity_units(cy, results):
    for r in cy[cy["requirement_id"].isin(INTENSITIES)].to_dict("records"):
        if r["final_value"] == "":
            results.add("INT02", "NOT_EVALUATED", r,
                        f"not converted ({r['final_status']})")
        elif r["final_unit"].endswith("/INR"):
            results.add("INT02", "PASS", r)
        else:
            results.add("INT02", "FAIL", r,
                        f"intensity unit {r['final_unit']}")


# ==========================================================
# build
# ==========================================================

def build(write=True):
    cy = read_production(CY_FILE)
    py = read_production(PY_FILE)
    manifest = pd.read_csv(MANIFEST, dtype=str, keep_default_na=False)
    scope_files = {p.replace("\\", "/").split("/")[-1]
                   for p in manifest.loc[manifest["in_processing_scope"]
                                         == "True", "local_file_path"] if p}
    issue_index = deferred_issues.issue_index()
    results = Results(issue_index)

    legacy_issues = run_legacy(cy, py)
    legacy_results(legacy_issues, results)
    recon = reconcile_legacy(legacy_issues)

    structural(cy, results, scope_files)
    cy_idx = index(cy)
    py_by_file = defaultdict(dict)
    for r in py.to_dict("records"):
        py_by_file[r["source_file"]][(r["requirement_id"], r["breakdown"])] = r
    environmental(cy_idx, results)
    social(cy_idx, results)
    governance_and_pending(cy_idx, py_by_file, results, issue_index)
    intensity_units(cy, results)

    res = pd.DataFrame(results.rows)
    registry = pd.DataFrame(RULES, columns=RULE_COLUMNS)
    summary = (res.groupby(["rule_id", "category", "status"]).size()
               .unstack(fill_value=0).reset_index())
    summary = registry[["rule_id", "category", "pillar", "severity_on_fail",
                        "legacy_check"]].merge(summary, on=["rule_id",
                                                             "category"],
                                               how="left").fillna(0)
    issues = res[res["status"] == "FAIL"]
    if write:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        registry.to_csv(OUTPUT_DIR / "validation_rules.csv", index=False,
                        encoding="utf-8-sig")
        res.to_csv(OUTPUT_DIR / "validation_results.csv", index=False,
                   encoding="utf-8-sig")
        issues.to_csv(OUTPUT_DIR / "validation_issues.csv", index=False,
                      encoding="utf-8-sig")
        summary.to_csv(OUTPUT_DIR / "validation_summary.csv", index=False,
                       encoding="utf-8-sig")
        recon.to_csv(OUTPUT_DIR / "legacy_check_reconciliation.csv",
                     index=False, encoding="utf-8-sig")
    log.info("validation built", extra={"fields": {
        "results": len(res), "fails": len(issues)}})
    return {"results": res, "summary": summary, "recon": recon,
            "issues": issues, "registry": registry}


def main():
    out = build()
    pd.set_option("display.width", 200)
    print(out["summary"].to_string(index=False))
    print("\nlegacy reconciliation:")
    print(out["recon"].to_string(index=False))
    print(f"\nOutputs: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
