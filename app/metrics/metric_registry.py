"""
Metric registry (Phase 7).

Turns data/reference/brsr_requirement_concept_map.csv (read only) into a
checked metric registry and measures, from the Phase 6 parsed facts, how
each metric is actually reported.

Inputs (all read only)
  data/reference/brsr_requirement_concept_map.csv
  data/processed/taxonomy/                      Phase 5 registry
  data/processed/taxonomy_versions/             crosswalk, definition arcs
  data/processed/nse_brsr_definition_relationships.csv   (current version)
  data/processed/nifty100/parsed/xbrl_facts.csv Phase 6 facts
  data/processed/registry/filing_manifest.csv
  data/processed/nifty100/brsr_requirement_coverage.csv  current pipeline

Outputs  data/processed/metrics/
  metric_registry.csv               one row per metric
  metric_concepts.csv               metric x concept, taxonomy attributes
  metric_version_availability.csv   metric x concept x taxonomy version
  metric_dimension_checks.csv       map axes / members vs definition linkbase
  metric_observed_dimensions.csv    axes seen in filings vs axes the map allows
  metric_fact_coverage.csv          metric x FY slot coverage from facts
  metric_coverage_reconciliation.csv  facts vs current requirement coverage
  metric_registry_issues.csv

Rules
  - The map, crosswalk and decisions are never modified.
  - A concept counts as available in an older version only through an
    exact / approved crosswalk entry; CANDIDATE_REVIEW mappings are shown as
    PENDING_REVIEW, never as available.
  - Expected units are checked against the taxonomy type; nothing is
    converted here (unit normalisation is Phase 8).

Run:
    python -m app.metrics.metric_registry
"""

from collections import Counter, defaultdict

import pandas as pd

from app.config import settings
from app.core.logging_setup import get_logger
from app.parsers.xbrl_instance import concept_types

log = get_logger(__name__, "metric_registry")

PROCESSED = settings.processed_dir
MAP_FILE = settings.reference_dir / "brsr_requirement_concept_map.csv"
TAXONOMY_REGISTRY = PROCESSED / "taxonomy" / "taxonomy_registry.csv"
VERSIONS_DIR = PROCESSED / "taxonomy_versions"
CROSSWALK = VERSIONS_DIR / "nse_brsr_concept_crosswalk.csv"
FACTS = PROCESSED / "nifty100" / "parsed" / "xbrl_facts.csv"
MANIFEST = PROCESSED / "registry" / "filing_manifest.csv"
REQ_COVERAGE = PROCESSED / "nifty100" / "brsr_requirement_coverage.csv"
OUTPUT_DIR = PROCESSED / "metrics"

DIM = "http://xbrl.org/int/dim/arcrole/"

# expected_unit -> (unit family, taxonomy type families it may carry)
UNIT_FAMILIES = {
    "INR": ("currency", {"monetary"}),
    "pure": ("pure", {"pure", "numeric"}),
    "energy": ("energy", {"numeric"}),
    "energy_per_inr": ("energy_per_currency", {"numeric"}),
    "kilolitre": ("volume", {"numeric"}),
    "kilolitre_per_inr": ("volume_per_currency", {"numeric"}),
    "tonne": ("mass", {"numeric"}),
    "tco2e": ("ghg_emissions", {"numeric"}),
    "tco2e_per_inr": ("ghg_emissions_per_currency", {"numeric"}),
    "": ("none", {"non_numeric"}),
}
AVAILABLE_STATUSES = {"UNCHANGED", "RENAMED", "MAPPED", "MAPPED_APPROVED"}
CY_ROLE_STATUSES = {"REPORTED", "REPORTED_ZERO"}


def issue(metric_id, check, severity, detail):
    return {"metric_id": metric_id, "check": check, "severity": severity,
            "detail": detail}


def strip_prefix(name):
    return name.split(":", 1)[-1]


# ==========================================================
# taxonomy helpers
# ==========================================================

def versions():
    reg = pd.read_csv(TAXONOMY_REGISTRY, dtype=str, keep_default_na=False)
    return list(reg["taxonomy_version"]), reg.iloc[-1]["taxonomy_version"]


def version_names(current_version):
    """
    {(version, current concept): [(name in that version, status, approval)]}
    from the crosswalk (read only).
    """
    out = defaultdict(list)
    cw = pd.read_csv(CROSSWALK, dtype=str, keep_default_na=False)
    for r in cw.to_dict("records"):
        if r["current_concept"]:
            out[(r["source_version"], r["current_concept"])].append(
                (r["source_concept"], r["status"], r["approval_status"]))
    return out


def dimension_model(version, current_version):
    """
    {primary concept: set(axes)}, {axis: set(members)} from the
    definition linkbase of one version (XBRL Dimensions 'all' arcs).
    """
    path = (PROCESSED if version == current_version else
            VERSIONS_DIR / version) / "nse_brsr_definition_relationships.csv"
    d = pd.read_csv(path, dtype=str, keep_default_na=False)
    by_role = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for r in d.to_dict("records"):
        by_role[r["role_uri"]][r["arcrole"].replace(DIM, "")][
            r["from_concept"]].append(r["to_concept"])

    def descendants(tree, start):
        seen, stack = set(), [start]
        while stack:
            node = stack.pop()
            for child in tree.get(node, []):
                if child not in seen:
                    seen.add(child)
                    stack.append(child)
        return seen

    axes_of, members_of = defaultdict(set), defaultdict(set)
    for role, arcs in by_role.items():
        dm = arcs["domain-member"]
        for axis, domains in arcs["dimension-domain"].items():
            for domain in domains:
                members_of[axis] |= {domain} | descendants(dm, domain)
        for head, tables in arcs["all"].items():
            primaries = {head} | descendants(dm, head)
            for table in tables:
                axes = set(arcs["hypercube-dimension"].get(table, []))
                for p in primaries:
                    axes_of[p] |= axes
    return axes_of, members_of


# ==========================================================
# 1. registry from the map
# ==========================================================

def parse_filter(text):
    out = {}
    for part in (text or "").split(";"):
        if "=" in part:
            axis, member = part.split("=", 1)
            out[axis.strip()] = member.strip()
    return out


def build_registry(issues):
    all_versions, current = versions()
    types = concept_types(current)
    names = version_names(current)
    m = pd.read_csv(MAP_FILE, dtype=str, keep_default_na=False)

    # map integrity
    for key, group in m.groupby("requirement_id"):
        for col in ("pillar", "requirement", "metric_key", "expected_unit",
                    "breakdown_axes", "dimension_filter", "source"):
            if group[col].nunique() > 1:
                issues.append(issue(key, "MAP_ROWS_DISAGREE", "ERROR",
                                    f"{col}: {sorted(group[col].unique())}"))
    dup_keys = m.drop_duplicates("requirement_id").groupby(
        "metric_key")["requirement_id"].apply(list)
    for metric_key, ids in dup_keys.items():
        if len(ids) > 1 and metric_key:
            issues.append(issue(" ".join(ids), "METRIC_KEY_REUSED",
                                "WARNING", metric_key))

    concepts, availability = [], []
    for r in m.to_dict("records"):
        mid, concept = r["requirement_id"], r["current_concept"]
        unit = r["expected_unit"]
        family, allowed = UNIT_FAMILIES.get(unit, ("UNKNOWN", set()))
        if family == "UNKNOWN":
            issues.append(issue(mid, "UNKNOWN_EXPECTED_UNIT", "ERROR", unit))
        if not concept:
            if r["source"] == "BRSR_XBRL":
                issues.append(issue(mid, "XBRL_METRIC_WITHOUT_CONCEPT",
                                    "ERROR", r["requirement"]))
            continue
        ctype = types.get(concept)
        row = {"metric_id": mid, "metric_key": r["metric_key"],
               "concept": concept,
               "in_current_taxonomy": ctype is not None,
               "data_type": ctype[0] if ctype else "",
               "type_family": ctype[1] if ctype else "",
               "period_type": ctype[2] if ctype else "",
               "abstract": ctype[3] if ctype else "",
               "expected_unit": unit, "unit_family": family,
               "unit_type_consistent": bool(ctype) and ctype[1] in allowed}
        if ctype is None:
            issues.append(issue(mid, "CONCEPT_NOT_IN_CURRENT_TAXONOMY",
                                "ERROR", concept))
        elif ctype[3]:
            issues.append(issue(mid, "ABSTRACT_CONCEPT", "ERROR", concept))
        elif not row["unit_type_consistent"]:
            issues.append(issue(mid, "EXPECTED_UNIT_VS_TYPE", "ERROR",
                                f"{concept}: expected '{unit}' but type "
                                f"{ctype[0]} ({ctype[1]})"))
        concepts.append(row)

        for v in all_versions:
            if v == current:
                entries = [(concept, "CURRENT", "")] if ctype else []
            else:
                entries = names.get((v, concept), [])
            ok = [e for e in entries
                  if e[1] in AVAILABLE_STATUSES | {"CURRENT"}]
            pending = [e for e in entries if e[1] == "CANDIDATE_REVIEW"]
            status = ("AVAILABLE" if ok else
                      "PENDING_REVIEW" if pending else "NOT_IN_VERSION")
            used = ok or pending
            availability.append({
                "metric_id": mid, "concept": concept,
                "taxonomy_version": v, "availability": status,
                "concept_in_version": " | ".join(e[0] for e in used),
                "crosswalk_status": " | ".join(sorted({e[1] for e in used})),
                "crosswalk_approval": " | ".join(sorted(
                    {e[2] for e in used} - {""}))})
            if len(ok) > 1:
                issues.append(issue(mid, "SEVERAL_SOURCE_CONCEPTS", "INFO",
                                    f"{v}: {[e[0] for e in ok]}"))
    return m, pd.DataFrame(concepts), pd.DataFrame(availability)


# ==========================================================
# 2. dimension checks against the definition linkbase
# ==========================================================

def check_dimensions(m, availability, issues):
    all_versions, current = versions()
    models = {v: dimension_model(v, current) for v in all_versions}
    names = version_names(current)

    def in_version(v, current_name):
        if v == current:
            return current_name
        hits = [e[0] for e in names.get((v, current_name), [])
                if e[1] in AVAILABLE_STATUSES]
        return hits[0] if hits else ""

    rows = []
    avail = {(a["metric_id"], a["concept"], a["taxonomy_version"]):
             a for a in availability.to_dict("records")}
    for r in m.to_dict("records"):
        concept = r["current_concept"]
        if not concept:
            continue
        mid = r["requirement_id"]
        axes = [a for a in r["breakdown_axes"].split(";") if a]
        filt = parse_filter(r["dimension_filter"])
        for v in all_versions:
            a = avail.get((mid, concept, v))
            if not a or a["availability"] != "AVAILABLE":
                continue
            axes_of, members_of = models[v]
            vconcept = a["concept_in_version"].split(" | ")[0]
            valid = axes_of.get(vconcept, set())
            checks = [(ax, "", "BREAKDOWN_AXIS") for ax in axes] + \
                [(ax, mem, "FILTER") for ax, mem in filt.items()]
            for axis, member, kind in checks:
                vaxis = in_version(v, axis)
                vmember = in_version(v, member) if member else ""
                if not vaxis:
                    result = "AXIS_NOT_IN_VERSION"
                elif vaxis not in valid:
                    result = "AXIS_NOT_VALID_FOR_CONCEPT"
                elif member and vmember not in members_of.get(vaxis, set()):
                    result = "MEMBER_NOT_ON_AXIS"
                else:
                    result = "OK"
                rows.append({"metric_id": mid, "concept": concept,
                             "taxonomy_version": v, "kind": kind,
                             "axis": axis, "member": member,
                             "axis_in_version": vaxis,
                             "member_in_version": vmember,
                             "valid_axes_for_concept": " | ".join(
                                 sorted(valid)),
                             "result": result})
                if result != "OK":
                    issues.append(issue(mid, result, "ERROR"
                                        if v == current else "WARNING",
                                        f"{v} {concept}: {axis}"
                                        f"{'=' + member if member else ''}"))
            if not axes and not filt and valid:
                rows.append({"metric_id": mid, "concept": concept,
                             "taxonomy_version": v, "kind": "NO_AXES_IN_MAP",
                             "axis": "", "member": "",
                             "axis_in_version": "", "member_in_version": "",
                             "valid_axes_for_concept": " | ".join(
                                 sorted(valid)),
                             "result": "CONCEPT_IS_DIMENSIONAL"})
    return pd.DataFrame(rows)


# ==========================================================
# 3. coverage from parsed facts
# ==========================================================

def fact_coverage(m, availability, issues):
    all_versions, current = versions()
    names = version_names(current)
    manifest = pd.read_csv(MANIFEST, dtype=str, keep_default_na=False)
    scope = manifest[(manifest["in_processing_scope"] == "True")
                     & (manifest["local_file_path"] != "")]
    filing = {r["filing_id"]: r for r in scope.to_dict("records")}

    # version-specific name -> [(metric, current concept, allowed axes)]
    lookup = defaultdict(list)
    to_current = {}
    for r in m.to_dict("records"):
        if not r["current_concept"]:
            continue
        allowed = {a for a in r["breakdown_axes"].split(";") if a} | set(
            parse_filter(r["dimension_filter"]))
        for v in all_versions:
            vnames = [r["current_concept"]] if v == current else [
                e[0] for e in names.get((v, r["current_concept"]), [])
                if e[1] in AVAILABLE_STATUSES]
            for name in vnames:
                lookup[(v, name)].append((r["requirement_id"],
                                          r["current_concept"], allowed))
    for (v, cur), entries in names.items():
        for name, status, _ in entries:
            if status in AVAILABLE_STATUSES:
                to_current[(v, name)] = cur

    reported = defaultdict(set)        # (metric, filing) -> roles seen
    measures = defaultdict(Counter)
    families = defaultdict(Counter)
    observed_axes = defaultdict(Counter)
    outside = defaultdict(Counter)
    cols = ["filing_id", "concept", "period_start", "period_end", "instant",
            "dimension_key", "unit_measure", "type_family", "value_status"]
    for chunk in pd.read_csv(FACTS, dtype=str, keep_default_na=False,
                             usecols=cols, chunksize=250_000):
        for f in chunk.to_dict("records"):
            info = filing.get(f["filing_id"])
            if info is None:
                continue
            v = info["taxonomy_version"]
            hits = lookup.get((v, f["concept"]))
            if not hits:
                continue
            axes = set()
            for part in f["dimension_key"].split(" | "):
                if "=" in part:
                    ax = strip_prefix(part.split("=", 1)[0])
                    axes.add(ax if v == current else
                             to_current.get((v, ax), ax))
            cy = ((f["period_start"] == info["reporting_period_start"]
                   and f["period_end"] == info["reporting_period_end"])
                  or (f["instant"] and f["instant"]
                      == info["reporting_period_end"]))
            for mid, concept, allowed in hits:
                key = (mid, concept)
                for ax in axes:
                    observed_axes[key][ax] += 1
                if axes - allowed:
                    outside[key][" + ".join(sorted(axes - allowed))] += 1
                    continue          # the current pipeline drops these
                if f["value_status"] == "REPORTED" and cy:
                    reported[(mid, f["filing_id"])].add("CY")
                    measures[key][f["unit_measure"] or "(none)"] += 1
                    families[key][f["type_family"]] += 1

    # coverage per metric x slot
    avail = {(a["metric_id"], a["taxonomy_version"])
             for a in availability.to_dict("records")
             if a["availability"] == "AVAILABLE"}
    rows = []
    for mid in m["requirement_id"].unique():
        for slot, group in scope.groupby("fy_slot"):
            files = list(group["filing_id"])
            can = [fid for fid in files
                   if (mid, filing[fid]["taxonomy_version"]) in avail]
            got = [fid for fid in files if (mid, fid) in reported]
            rows.append({"metric_id": mid, "fy_slot": slot,
                         "filings": len(files),
                         "filings_where_available": len(can),
                         "filings_reporting_cy": len(got),
                         "coverage_pct_of_available": round(
                             100 * len(got) / len(can), 1) if can else ""})
    coverage = pd.DataFrame(rows)

    obs_rows = []
    for (mid, concept), axes in sorted(observed_axes.items()):
        allowed = {a for a in m.loc[m["requirement_id"] == mid,
                                    "breakdown_axes"].iloc[0].split(";")
                   if a} | set(parse_filter(m.loc[
                       m["requirement_id"] == mid,
                       "dimension_filter"].iloc[0]))
        dropped = sum(outside[(mid, concept)].values())
        obs_rows.append({
            "metric_id": mid, "concept": concept,
            "axes_allowed_by_map": " | ".join(sorted(allowed)),
            "axes_observed": " | ".join(f"{a}:{n}" for a, n in
                                        sorted(axes.items())),
            "facts_outside_allowed_axes": dropped,
            "outside_axes_combinations": " | ".join(
                f"{a}:{n}" for a, n in outside[(mid, concept)].most_common()),
            "cy_unit_measures": " | ".join(
                f"{u}:{n}" for u, n in measures[(mid, concept)].most_common()),
            "cy_type_families": " | ".join(
                f"{u}:{n}" for u, n in families[(mid, concept)].most_common())})
        if dropped:
            issues.append(issue(mid, "FACTS_WITH_AXES_OUTSIDE_MAP", "WARNING",
                                f"{concept}: {dropped} facts excluded by the "
                                f"map's axes ({' | '.join(outside[(mid, concept)])})"))
    for (mid, concept), ms in measures.items():
        unit = m.loc[m["requirement_id"] == mid, "expected_unit"].iloc[0]
        if unit == "INR" and any(not u.startswith("iso4217:") for u in ms):
            issues.append(issue(mid, "UNEXPECTED_UNIT_MEASURE", "WARNING",
                                f"{concept}: {dict(ms)}"))
        if unit == "pure" and any(u != "xbrli:pure" for u in ms):
            issues.append(issue(mid, "UNEXPECTED_UNIT_MEASURE", "WARNING",
                                f"{concept}: {dict(ms)}"))
        if unit == "" and any(u != "(none)" for u in ms):
            issues.append(issue(mid, "UNEXPECTED_UNIT_MEASURE", "WARNING",
                                f"{concept}: {dict(ms)}"))
    return coverage, pd.DataFrame(obs_rows), reported, scope


def reconcile(m, reported, scope, availability):
    """Facts-based CY reporting vs the current requirement coverage."""
    cov = pd.read_csv(REQ_COVERAGE, dtype=str, keep_default_na=False)
    by_key = {(r["isin"], r["fy_slot"]): r["filing_id"]
              for r in scope.merge(
                  pd.read_csv(PROCESSED / "registry" / "company_master.csv",
                              dtype=str, keep_default_na=False)[
                      ["company_id", "isin"]], on="company_id").to_dict(
                  "records") if r["is_latest_valid"] == "True"}
    pending = {(a["metric_id"], a["taxonomy_version"])
               for a in availability.to_dict("records")
               if a["availability"] == "PENDING_REVIEW"}
    version_of = dict(zip(scope["filing_id"], scope["taxonomy_version"]))
    xbrl = set(m.loc[(m["source"] == "BRSR_XBRL")
                     & (m["current_concept"] != ""), "requirement_id"])
    rows = []
    for (rid, status), group in cov[cov["requirement_id"].isin(xbrl)] \
            .groupby(["requirement_id", "status"]):
        agree = candidate = unexplained = no_filing = 0
        examples = []
        for r in group.to_dict("records"):
            fid = by_key.get((r["isin"], r["fy_slot"]))
            if fid is None:
                no_filing += 1
                continue
            facts_say = (rid, fid) in reported
            pipeline_says = status in CY_ROLE_STATUSES
            if facts_say == pipeline_says:
                agree += 1
            elif pipeline_says and (rid, version_of[fid]) in pending:
                # pipeline used a crosswalk mapping that is still
                # CANDIDATE_REVIEW (not approved)
                candidate += 1
            else:
                unexplained += 1
                if len(examples) < 3:
                    examples.append(f"{r['nse_symbol']} {r['fy_slot']}")
        rows.append({"metric_id": rid, "pipeline_status": status,
                     "rows": len(group), "agree": agree,
                     "pipeline_uses_candidate_mapping": candidate,
                     "disagree_unexplained": unexplained,
                     "no_filing_in_scope": no_filing,
                     "examples": " | ".join(examples)})
    return pd.DataFrame(rows)


# ==========================================================
# build
# ==========================================================

def build():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    issues = []
    m, concepts, availability = build_registry(issues)
    dims = check_dimensions(m, availability, issues)
    coverage, observed, reported, scope = fact_coverage(m, availability,
                                                        issues)
    recon = reconcile(m, reported, scope, availability)
    for r in recon.to_dict("records"):
        if r["pipeline_uses_candidate_mapping"]:
            issues.append(issue(
                r["metric_id"], "PIPELINE_USES_UNAPPROVED_CROSSWALK",
                "WARNING", f"{r['pipeline_status']}: "
                f"{r['pipeline_uses_candidate_mapping']} company-years come "
                "from a CANDIDATE_REVIEW mapping (owner decision pending)"))
        if r["disagree_unexplained"]:
            issues.append(issue(
                r["metric_id"], "COVERAGE_DISAGREES_WITH_PIPELINE", "ERROR",
                f"{r['pipeline_status']}: {r['disagree_unexplained']} of "
                f"{r['rows']} ({r['examples']})"))

    first = m.drop_duplicates("requirement_id")
    avail_first = (availability[availability["availability"] == "AVAILABLE"]
                   .groupby("metric_id")["taxonomy_version"].min())
    totals = coverage.groupby("metric_id")[[
        "filings_where_available", "filings_reporting_cy"]].sum()
    sev = Counter((i["metric_id"], i["severity"]) for i in issues)
    registry = pd.DataFrame([{
        "metric_id": r["requirement_id"], "pillar": r["pillar"],
        "metric_key": r["metric_key"], "requirement": r["requirement"],
        "source": r["source"],
        "concepts": " | ".join(m.loc[m["requirement_id"]
                                     == r["requirement_id"],
                                     "current_concept"].replace("", pd.NA)
                               .dropna()),
        "expected_unit": r["expected_unit"],
        "unit_family": UNIT_FAMILIES.get(r["expected_unit"],
                                         ("UNKNOWN",))[0],
        "breakdown_axes": r["breakdown_axes"],
        "dimension_filter": r["dimension_filter"],
        "applicability_concept": r["applicability_concept"],
        "first_available_version": avail_first.get(r["requirement_id"], ""),
        "filings_where_available": int(totals.loc[r["requirement_id"],
                                                  "filings_where_available"]),
        "filings_reporting_cy": int(totals.loc[r["requirement_id"],
                                               "filings_reporting_cy"]),
        "errors": sev.get((r["requirement_id"], "ERROR"), 0),
        "warnings": sev.get((r["requirement_id"], "WARNING"), 0),
        "notes": r["notes"]} for r in first.to_dict("records")])

    outputs = {
        "metric_registry.csv": registry,
        "metric_concepts.csv": concepts,
        "metric_version_availability.csv": availability,
        "metric_dimension_checks.csv": dims,
        "metric_observed_dimensions.csv": observed,
        "metric_fact_coverage.csv": coverage,
        "metric_coverage_reconciliation.csv": recon,
        "metric_registry_issues.csv": pd.DataFrame(
            issues, columns=["metric_id", "check", "severity", "detail"]),
    }
    for name, df in outputs.items():
        df.to_csv(OUTPUT_DIR / name, index=False, encoding="utf-8-sig")
    log.info("metric registry built", extra={"fields": {
        "metrics": len(registry), "issues": len(issues)}})
    return outputs


def main():
    out = build()
    reg = out["metric_registry.csv"]
    print(f"metrics: {len(reg)}  ({reg['pillar'].value_counts().to_dict()})")
    print("source:", reg["source"].value_counts().to_dict())
    issues = out["metric_registry_issues.csv"]
    print(f"\nissues: {len(issues)}")
    if len(issues):
        print(issues.groupby(["severity", "check"]).size().to_string())
    recon = out["metric_coverage_reconciliation.csv"]
    print("\ncoverage reconciliation (facts vs pipeline):",
          int(recon["agree"].sum()), "agree,",
          int(recon["pipeline_uses_candidate_mapping"].sum()),
          "from CANDIDATE_REVIEW mappings,",
          int(recon["disagree_unexplained"].sum()), "unexplained")
    print(f"\nOutputs: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
