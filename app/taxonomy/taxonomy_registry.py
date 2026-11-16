"""
Taxonomy registry (Phase 5).

Builds a register of every SEBI BRSR taxonomy version used by the
platform from the LOCAL taxonomy packages (nothing is downloaded here; the
downloader is app/downloaders/sebi_brsr_taxonomy_downloader.py).

Inputs
  <taxonomy_dir>/Taxonomy_BRSR.zip                    current package
  <taxonomy_dir>/extracted/                           its extracted files
  <taxonomy_dir>/archives/Taxonomy_Archives_*.zip     NSE archive download
  <taxonomy_dir>/archives/BRSR_archives.zip           BRSR.zip from it
  <taxonomy_dir>/versions/<version>/                  extracted old versions
  data/processed/registry/filing_manifest.csv         observed usage
  data/processed/taxonomy_versions/nse_brsr_concept_crosswalk.csv
                                                      (read only)

Outputs  data/processed/taxonomy/
  taxonomy_registry.csv                 one row per version
  taxonomy_files.csv                    every file, sha256, archive check
  taxonomy_calculation_relationships.csv
  taxonomy_label_completeness.csv       per version x label role
  taxonomy_label_gaps.csv               missing / duplicate / dangling
  taxonomy_references.csv               SEBI circular references
  concept_version_applicability.csv     concept x version presence
  taxonomy_integrity_issues.csv

Rules
  - Labels are reported exactly as published. SEBI publishes no
    documentation labels; that is recorded as NOT_PROVIDED_BY_SEBI, never
    filled in.
  - FY applicability is what is OBSERVED in NSE filings, not a legal
    statement of which version must be used.
  - Crosswalk decisions are read, never changed.

Run:
    python -m app.taxonomy.taxonomy_registry
"""

import hashlib
import json
import re
import zipfile
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import pandas as pd
from lxml import etree

from app.config import settings
from app.core.http_client import sha256_file
from app.core.logging_setup import get_logger

log = get_logger(__name__, "taxonomy_registry")

TAXONOMY_DIR = settings.taxonomy_dir
OUTPUT_DIR = settings.processed_dir / "taxonomy"
MANIFEST = settings.processed_dir / "registry" / "filing_manifest.csv"
VERSIONS_PROCESSED = settings.processed_dir / "taxonomy_versions"
CROSSWALK = VERSIONS_PROCESSED / "nse_brsr_concept_crosswalk.csv"

XLINK = "http://www.w3.org/1999/xlink"
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"
ROLE = "http://www.xbrl.org/2003/role/"
STANDARD_LABEL = ROLE + "label"
DOCUMENTATION = ROLE + "documentation"
# roles every version is checked for, even when absent
CHECKED_LABEL_ROLES = [STANDARD_LABEL, ROLE + "terseLabel",
                       ROLE + "verboseLabel", DOCUMENTATION,
                       ROLE + "totalLabel", ROLE + "periodStartLabel",
                       ROLE + "periodEndLabel", ROLE + "negatedLabel"]

# safe parser for downloaded files: no entities, no network
PARSER = etree.XMLParser(resolve_entities=False, no_network=True,
                         remove_comments=True)


# ==========================================================
# helpers
# ==========================================================

def xl(element, attribute):
    return element.get(f"{{{XLINK}}}{attribute}")


def lname(element):
    return etree.QName(element).localname


def concept_from_href(href):
    if not href or "#" not in href:
        return None
    fragment = href.split("#", 1)[1]
    return fragment.split("_", 1)[1] if "_" in fragment else fragment


def parse(path):
    return etree.parse(str(path), PARSER).getroot()


def extended_links(path, link_name):
    """
    Yield (role, locators, resources, arcs) for each extended link named
    link_name. Labels are local to an extended link, as XLink requires.
    """
    root = parse(path)
    for link in root.iter():
        if not isinstance(link.tag, str) or lname(link) != link_name:
            continue
        locs, resources, arcs = defaultdict(list), defaultdict(list), []
        for child in link:
            if not isinstance(child.tag, str):
                continue
            kind = xl(child, "type")
            if kind == "locator":
                # two locators may share a label and point to the same
                # concept; that is still one relationship per arc
                concept = concept_from_href(xl(child, "href"))
                if concept not in locs[xl(child, "label")]:
                    locs[xl(child, "label")].append(concept)
            elif kind == "resource":
                resources[xl(child, "label")].append(child)
            elif kind == "arc":
                arcs.append(child)
        yield xl(link, "role") or "", locs, resources, arcs


def version_of(folder):
    entry = sorted((folder / "BRSR").glob("in-capmkt-ent-*.xsd"))
    if not entry:
        return None, None
    match = re.search(r"(\d{4}-\d{2}-\d{2})\.xsd$", entry[0].name)
    return (match.group(1) if match else None), entry[0]


def file_kind(name):
    for key, kind in (("-cal-", "CALCULATION_LINKBASE"),
                      ("-def-", "DEFINITION_LINKBASE"),
                      ("-pre-", "PRESENTATION_LINKBASE"),
                      ("-ref-", "REFERENCE_LINKBASE"),
                      ("-lab", "LABEL_LINKBASE"),
                      ("-ent-", "ENTRY_POINT"),
                      ("-roles-", "ROLE_SCHEMA"),
                      ("-types", "TYPE_SCHEMA"),
                      ("taxonomyPackage", "PACKAGE_METADATA"),
                      ("catalog", "PACKAGE_CATALOG")):
        if key in name:
            return kind
    if name == "in-capmkt.xsd":
        return "CONCEPT_SCHEMA"
    if name.endswith(".xlsx"):
        return "SEBI_SPREADSHEET"
    return "OTHER"


def zip_members(path):
    """{member path: sha256} of a zip file (read in memory, no extract)."""
    out = {}
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            if not info.is_dir():
                out[info.filename] = hashlib.sha256(
                    archive.read(info)).hexdigest()
    return out


# ==========================================================
# 1. discover versions and their sources
# ==========================================================

def discover_versions():
    current_zip = TAXONOMY_DIR / "Taxonomy_BRSR.zip"
    archive_outer = sorted((TAXONOMY_DIR / "archives").glob(
        "Taxonomy_Archives_*.zip"))
    archive_inner = TAXONOMY_DIR / "archives" / "BRSR_archives.zip"

    sources = []
    folder = TAXONOMY_DIR / "extracted"
    version, entry = version_of(folder)
    if version:
        meta = TAXONOMY_DIR / "Taxonomy_BRSR.download.json"
        if meta.exists():
            info = json.loads(meta.read_text(encoding="utf-8"))
            acquired = info.get("downloaded_at", "")
            basis = "DOWNLOAD_METADATA"
            url = info.get("url", settings.brsr_taxonomy_url)
        else:
            acquired = datetime.fromtimestamp(
                current_zip.stat().st_mtime).isoformat(timespec="seconds") \
                if current_zip.exists() else ""
            basis = "FILE_MTIME (download metadata not recorded)"
            url = settings.brsr_taxonomy_url
        sources.append({"version": version, "folder": folder,
                        "entry": entry, "source_url": url,
                        "container": current_zip if current_zip.exists()
                        else None,
                        "inner_container": None,
                        "acquired_at": acquired, "acquired_basis": basis})

    for folder in sorted((TAXONOMY_DIR / "versions").glob("*")):
        version, entry = version_of(folder)
        if not version:
            continue
        outer = archive_outer[-1] if archive_outer else None
        sources.append({
            "version": version, "folder": folder, "entry": entry,
            "source_url": settings.brsr_taxonomy_archive_url,
            "container": outer,
            "inner_container": archive_inner if archive_inner.exists()
            else None,
            "acquired_at": datetime.fromtimestamp(
                outer.stat().st_mtime).isoformat(timespec="seconds")
            if outer else "",
            "acquired_basis": "FILE_MTIME (download metadata not recorded)"})
    return sorted(sources, key=lambda s: s["version"])


# ==========================================================
# 2. per-version parsing
# ==========================================================

def parse_concepts(folder):
    rows = {}
    root = parse(folder / "core" / "in-capmkt.xsd")
    for el in root.iter("{http://www.w3.org/2001/XMLSchema}element"):
        name = el.get("name")
        if not name:
            continue
        rows[name] = {
            "data_type": el.get("type", ""),
            "substitution_group": el.get("substitutionGroup", ""),
            "abstract": el.get("abstract", "false").lower() == "true",
            "period_type": el.get(
                "{http://www.xbrl.org/2003/instance}periodType", ""),
        }
    return rows


def linkbase(folder, kind):
    files = sorted((folder / "BRSR").glob(f"in-capmkt-{kind}-*.xml"))
    return files[0] if files else None


def parse_arcs(path, link_name, version, issues, concepts):
    rows = []
    if path is None:
        return rows
    for role, locs, _, arcs in extended_links(path, link_name):
        for arc in arcs:
            for parent in locs.get(xl(arc, "from"), []):
                for child in locs.get(xl(arc, "to"), []):
                    rows.append({
                        "taxonomy_version": version, "role_uri": role,
                        "parent_concept": parent, "child_concept": child,
                        "arcrole": xl(arc, "arcrole") or "",
                        "weight": arc.get("weight", ""),
                        "order": arc.get("order", ""),
                        "priority": arc.get("priority", ""),
                        "use": arc.get("use", "")})
            if not locs.get(xl(arc, "from")) or not locs.get(xl(arc, "to")):
                issues.append(issue(version, f"{link_name}_ARC_UNRESOLVED",
                                    "ERROR", f"{path.name}: arc "
                                    f"{xl(arc, 'from')} -> {xl(arc, 'to')}"))
    for r in rows:
        for key in ("parent_concept", "child_concept"):
            if r[key] not in concepts:
                issues.append(issue(version, f"{link_name}_UNKNOWN_CONCEPT",
                                    "ERROR", f"{r[key]} in {path.name}"))
    return rows


def parse_labels(folder, version, concepts, issues):
    """{concept: {role: [(lang, text)]}}"""
    labels = defaultdict(lambda: defaultdict(list))
    path = folder / "core" / "in-capmkt-lab.xml"
    for _, locs, resources, arcs in extended_links(path, "labelLink"):
        for arc in arcs:
            for concept in locs.get(xl(arc, "from"), []):
                for res in resources.get(xl(arc, "to"), []):
                    labels[concept][xl(res, "role") or STANDARD_LABEL].append(
                        (res.get(XML_LANG, ""),
                         "".join(res.itertext()).strip()))
    for concept in labels:
        if concept not in concepts:
            issues.append(issue(version, "LABEL_FOR_UNKNOWN_CONCEPT",
                                "WARNING", concept))
    return labels


def parse_references(folder, version, concepts, issues):
    path = linkbase(folder, "ref")
    rows = []
    if path is None:
        return rows
    for _, locs, resources, arcs in extended_links(path, "referenceLink"):
        for arc in arcs:
            for concept in locs.get(xl(arc, "from"), []):
                for res in resources.get(xl(arc, "to"), []):
                    parts = {lname(p): (p.text or "").strip()
                             for p in res if isinstance(p.tag, str)}
                    rows.append({
                        "taxonomy_version": version, "concept_name": concept,
                        "reference_role": xl(res, "role") or "",
                        "publisher": parts.pop("Publisher", ""),
                        "name": parts.pop("Name", ""),
                        "section": parts.pop("Section", ""),
                        "paragraph": parts.pop("Paragraph", ""),
                        "other_parts": "; ".join(
                            f"{k}={v}" for k, v in parts.items())})
                    if concept not in concepts:
                        issues.append(issue(version,
                                            "REFERENCE_FOR_UNKNOWN_CONCEPT",
                                            "WARNING", concept))
    return rows


def count_roles(folder):
    files = sorted((folder / "BRSR").glob("in-capmkt-roles-*.xsd"))
    if not files:
        return 0
    return sum(1 for el in parse(files[0]).iter()
               if isinstance(el.tag, str) and lname(el) == "roleType")


def package_metadata(folder):
    path = folder / "META-INF" / "taxonomyPackage.xml"
    if not path.exists():
        return {}
    root = parse(path)
    out = {}
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        name = lname(el)
        if name in ("identifier", "name", "description", "version",
                    "publisher", "publicationDate") and name not in out:
            out[name] = (el.text or "").strip()
        if name == "entryPointDocument":
            out.setdefault("entry_point_href", el.get("href", ""))
    return out


# ==========================================================
# 3. file register with archive check
# ==========================================================

def register_files(source, issues):
    version, folder = source["version"], source["folder"]
    members = {}
    container = source["inner_container"] or source["container"]
    if container is not None:
        members = zip_members(container)
    rows = []
    for path in sorted(p for p in folder.rglob("*") if p.is_file()):
        rel = path.relative_to(folder).as_posix()
        sha = sha256_file(path)
        if source["inner_container"] is not None:
            candidates = [m for m in members
                          if m.endswith(f"BRSR {version}/{rel}")]
        else:
            candidates = [m for m in members
                          if m == rel or m.endswith("/" + rel)]
        if not candidates:
            status = "NOT_IN_SOURCE_ARCHIVE"
        elif len(candidates) > 1:
            status = "AMBIGUOUS_ARCHIVE_MEMBER"
        else:
            status = ("MATCHES_SOURCE_ARCHIVE"
                      if members[candidates[0]] == sha
                      else "DIFFERS_FROM_SOURCE_ARCHIVE")
        if status != "MATCHES_SOURCE_ARCHIVE":
            issues.append(issue(version, status,
                                "ERROR" if "DIFFERS" in status
                                else "WARNING", rel))
        rows.append({"taxonomy_version": version, "relative_path": rel,
                     "file_kind": file_kind(path.name),
                     "bytes": path.stat().st_size, "sha256": sha,
                     "archive_member": candidates[0] if len(
                         candidates) == 1 else "",
                     "archive_check": status,
                     "local_path": str(path)})
    return rows


def check_archive_chain(issues):
    """BRSR_archives.zip must be the BRSR.zip inside the NSE archive."""
    outer = sorted((TAXONOMY_DIR / "archives").glob(
        "Taxonomy_Archives_*.zip"))
    inner = TAXONOMY_DIR / "archives" / "BRSR_archives.zip"
    if not outer or not inner.exists():
        issues.append(issue("", "ARCHIVE_CHAIN_INCOMPLETE", "WARNING",
                            "NSE archive or BRSR_archives.zip missing"))
        return None
    with zipfile.ZipFile(outer[-1]) as archive:
        names = [n for n in archive.namelist()
                 if n.rstrip("/").endswith("/BRSR.zip")]
        if len(names) != 1:
            issues.append(issue("", "ARCHIVE_CHAIN_INCOMPLETE", "ERROR",
                                f"BRSR.zip members: {names}"))
            return None
        same = hashlib.sha256(archive.read(names[0])).hexdigest() == \
            sha256_file(inner)
    if not same:
        issues.append(issue("", "ARCHIVE_CHAIN_MISMATCH", "ERROR",
                            "BRSR_archives.zip differs from BRSR.zip in "
                            f"{outer[-1].name}"))
    return same


def issue(version, check, severity, detail):
    return {"taxonomy_version": version, "check": check,
            "severity": severity, "detail": detail}


# ==========================================================
# 4. label completeness
# ==========================================================

def label_completeness(version, concepts, labels):
    completeness, gaps = [], []
    total = len(concepts)
    roles = list(CHECKED_LABEL_ROLES) + sorted(
        {r for c in labels.values() for r in c} - set(CHECKED_LABEL_ROLES))
    for role in roles:
        with_role = sum(1 for c in concepts if labels.get(c, {}).get(role))
        if role == STANDARD_LABEL:
            status = "COMPLETE" if with_role == total else "INCOMPLETE"
        elif with_role == 0:
            status = ("NOT_PROVIDED_BY_SEBI" if role == DOCUMENTATION
                      else "NOT_USED")
        else:
            status = "PARTIAL_OPTIONAL_ROLE"
        completeness.append({
            "taxonomy_version": version,
            "label_role": role.replace(ROLE, ""),
            "concepts_total": total, "concepts_with_label": with_role,
            "coverage_pct": round(100 * with_role / total, 2) if total else 0,
            "status": status})

    text_owner = defaultdict(list)
    for concept in sorted(concepts):
        std = labels.get(concept, {}).get(STANDARD_LABEL, [])
        if not std:
            gaps.append(gap(version, concept, "MISSING_STANDARD_LABEL", ""))
            continue
        langs = Counter(lang for lang, _ in std)
        if any(n > 1 for n in langs.values()):
            gaps.append(gap(version, concept, "MULTIPLE_STANDARD_LABELS",
                            " | ".join(t for _, t in std)))
        if any(not t for _, t in std):
            gaps.append(gap(version, concept, "EMPTY_STANDARD_LABEL", ""))
        if not concepts[concept]["abstract"]:
            text_owner[std[0][1].casefold()].append(concept)
    for text, owners in sorted(text_owner.items()):
        if len(owners) > 1:
            for concept in owners:
                gaps.append(gap(version, concept,
                                "STANDARD_LABEL_SHARED_BY_CONCEPTS",
                                " | ".join(o for o in owners
                                           if o != concept)))
    return completeness, gaps


def gap(version, concept, kind, detail):
    return {"taxonomy_version": version, "concept_name": concept,
            "gap_type": kind, "detail": detail}


# ==========================================================
# 5. observed usage and concept applicability
# ==========================================================

def observed_usage():
    if not MANIFEST.exists():
        return {}
    manifest = pd.read_csv(MANIFEST, dtype=str, keep_default_na=False)
    used = manifest[manifest["taxonomy_version"] != ""]
    out = {}
    for version, group in used.groupby("taxonomy_version"):
        slots = group["fy_slot"].value_counts().sort_index()
        out[version] = {
            "observed_filings": len(group),
            "observed_fy_slots": "; ".join(f"{s}:{n}"
                                           for s, n in slots.items()),
            "observed_first_submission": min(
                (d for d in group["submission_date"] if d), default=""),
            "observed_last_submission": max(
                (d for d in group["submission_date"] if d), default="")}
    return out


def concept_applicability(per_version):
    versions = sorted(per_version)
    current = versions[-1]
    crosswalk = {}
    if CROSSWALK.exists():
        cw = pd.read_csv(CROSSWALK, dtype=str, keep_default_na=False)
        for r in cw.to_dict("records"):
            crosswalk[(r["source_version"], r["source_concept"])] = r

    names = sorted({c for v in versions for c in per_version[v]})
    rows = []
    for name in names:
        present = [v for v in versions if name in per_version[v]]
        row = {"concept_name": name}
        for v in versions:
            row[f"in_{v}"] = name in per_version[v]
        types = [(v, per_version[v][name]["data_type"]) for v in present]
        periods = [(v, per_version[v][name]["period_type"]) for v in present]
        row.update({
            "first_version": present[0], "last_version": present[-1],
            "versions_present": len(present),
            "gaps_between_versions": len(present) != (
                versions.index(present[-1]) - versions.index(present[0]) + 1),
            "abstract": per_version[present[-1]][name]["abstract"],
            "data_type_current": types[-1][1],
            "data_type_changes": changes(types),
            "period_type_changes": changes(periods),
        })
        if name in per_version[current]:
            row.update({"applicability_status": "IN_CURRENT_TAXONOMY",
                        "crosswalk_current_concept": name,
                        "crosswalk_status": "", "crosswalk_approval": ""})
        else:
            cw = crosswalk.get((present[-1], name), {})
            row.update({
                "applicability_status": f"REMOVED_AFTER_{present[-1]}",
                "crosswalk_current_concept": cw.get("current_concept", ""),
                "crosswalk_status": cw.get("status", "NOT_IN_CROSSWALK"),
                "crosswalk_approval": cw.get("approval_status", "")})
        rows.append(row)
    return pd.DataFrame(rows)


def changes(pairs):
    out, last = [], None
    for version, value in pairs:
        if last is not None and value != last[1]:
            out.append(f"{last[0]}:{last[1]} -> {version}:{value}")
        last = (version, value)
    return " | ".join(out)


def compare_with_processed(version, concepts, issues, is_current):
    """
    Cross-check with the concept CSVs used by the extractor: older
    versions in taxonomy_versions/<version>/, the current version in
    data/processed/ (written by nse_brsr_taxonomy_mapping_builder).
    """
    path = (settings.processed_dir if is_current
            else VERSIONS_PROCESSED / version) / \
        "nse_brsr_taxonomy_concepts.csv"
    if not path.exists():
        issues.append(issue(version, "PROCESSED_CONCEPTS_MISSING",
                            "WARNING", str(path)))
        return
    seen = set(pd.read_csv(path, dtype=str, keep_default_na=False)[
        "concept_name"])
    missing, extra = set(concepts) - seen, seen - set(concepts)
    if missing or extra:
        issues.append(issue(version, "PROCESSED_CONCEPTS_DIFFER", "ERROR",
                            f"{path.name}: {len(missing)} missing, "
                            f"{len(extra)} extra"))


# ==========================================================
# 6. build
# ==========================================================

def build():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    issues, files, calcs, completeness, gaps, refs = [], [], [], [], [], []
    registry, per_version = [], {}
    chain_ok = check_archive_chain(issues)
    usage = observed_usage()
    sources = discover_versions()

    for source in sources:
        version, folder = source["version"], source["folder"]
        concepts = parse_concepts(folder)
        per_version[version] = concepts
        compare_with_processed(version, concepts, issues,
                               source is sources[-1])
        file_rows = register_files(source, issues)
        files += file_rows

        calc = parse_arcs(linkbase(folder, "cal"), "calculationLink",
                          version, issues, concepts)
        pre = parse_arcs(linkbase(folder, "pre"), "presentationLink",
                         version, issues, concepts)
        dfn = parse_arcs(linkbase(folder, "def"), "definitionLink",
                         version, issues, concepts)
        for r in calc:
            if r["weight"] not in ("1", "-1", "1.0", "-1.0"):
                issues.append(issue(version, "UNUSUAL_CALCULATION_WEIGHT",
                                    "WARNING", f"{r['parent_concept']} -> "
                                    f"{r['child_concept']} weight "
                                    f"{r['weight']!r}"))
        calcs += calc
        labels = parse_labels(folder, version, concepts, issues)
        comp, gap_rows = label_completeness(version, concepts, labels)
        completeness += comp
        gaps += gap_rows
        ref_rows = parse_references(folder, version, concepts, issues)
        refs += ref_rows

        meta = package_metadata(folder)
        checks = Counter(r["archive_check"] for r in file_rows)
        doc = next(c for c in comp if c["label_role"] == "documentation")
        std = next(c for c in comp if c["label_role"] == "label")
        registry.append({
            "taxonomy_version": version,
            "publication_date": meta.get("publicationDate", version),
            "publication_date_basis": ("PACKAGE_METADATA"
                                       if meta.get("publicationDate")
                                       else "VERSION_IDENTIFIER"),
            "package_identifier": meta.get("identifier", ""),
            "package_name": meta.get("name", ""),
            "package_description": meta.get("description", ""),
            "publisher": meta.get("publisher", ""),
            "entry_point": source["entry"].relative_to(folder).as_posix(),
            "entry_point_href": meta.get("entry_point_href", ""),
            "local_folder": str(folder),
            "source_url": source["source_url"],
            "source_container": source["container"].name
            if source["container"] else "",
            "source_container_sha256": sha256_file(source["container"])
            if source["container"] else "",
            "inner_container": source["inner_container"].name
            if source["inner_container"] else "",
            "inner_container_sha256": sha256_file(source["inner_container"])
            if source["inner_container"] else "",
            "archive_chain_verified": chain_ok
            if source["inner_container"] else "",
            "acquired_at": source["acquired_at"],
            "acquired_at_basis": source["acquired_basis"],
            "file_count": len(file_rows),
            "files_matching_source": checks.get("MATCHES_SOURCE_ARCHIVE", 0),
            "concepts": len(concepts),
            "abstract_concepts": sum(c["abstract"]
                                     for c in concepts.values()),
            "roles": count_roles(folder),
            "presentation_arcs": len(pre),
            "definition_arcs": len(dfn),
            "calculation_arcs": len(calc),
            "standard_label_coverage_pct": std["coverage_pct"],
            "documentation_labels": doc["status"] if doc[
                "concepts_with_label"] == 0 else doc["concepts_with_label"],
            "has_reference_linkbase": linkbase(folder, "ref") is not None,
            "references": len(ref_rows),
            **usage.get(version, {"observed_filings": 0,
                                  "observed_fy_slots": "",
                                  "observed_first_submission": "",
                                  "observed_last_submission": ""}),
        })

    for i, row in enumerate(registry):
        row["previous_version"] = registry[i - 1]["taxonomy_version"] \
            if i else ""
        row["superseded_by"] = registry[i + 1]["taxonomy_version"] \
            if i + 1 < len(registry) else ""
        row["status"] = "SUPERSEDED" if row["superseded_by"] else "CURRENT"
    for version in usage:
        if version not in per_version:
            issues.append(issue(version, "FILINGS_USE_UNREGISTERED_VERSION",
                                "ERROR", f"{usage[version]['observed_filings']}"
                                " filings"))

    applicability = concept_applicability(per_version)
    outputs = {
        "taxonomy_registry.csv": pd.DataFrame(registry),
        "taxonomy_files.csv": pd.DataFrame(files),
        "taxonomy_calculation_relationships.csv": pd.DataFrame(calcs),
        "taxonomy_label_completeness.csv": pd.DataFrame(completeness),
        "taxonomy_label_gaps.csv": pd.DataFrame(
            gaps, columns=["taxonomy_version", "concept_name", "gap_type",
                           "detail"]),
        "taxonomy_references.csv": pd.DataFrame(refs),
        "concept_version_applicability.csv": applicability,
        "taxonomy_integrity_issues.csv": pd.DataFrame(
            issues, columns=["taxonomy_version", "check", "severity",
                             "detail"]).drop_duplicates(),
    }
    for name, df in outputs.items():
        df.to_csv(OUTPUT_DIR / name, index=False, encoding="utf-8-sig")
    log.info("taxonomy registry built", extra={"fields": {
        "versions": len(registry), "files": len(files),
        "calculation_arcs": len(calcs), "issues": len(issues)}})
    return outputs


def main():
    out = build()
    reg = out["taxonomy_registry.csv"]
    print(reg[["taxonomy_version", "status", "concepts", "file_count",
               "files_matching_source", "calculation_arcs", "references",
               "documentation_labels", "observed_filings"]].to_string(
                   index=False))
    print("\nlabel completeness (standard / documentation):")
    comp = out["taxonomy_label_completeness.csv"]
    print(comp[comp["label_role"].isin(["label", "documentation"])]
          .to_string(index=False))
    print("\nlabel gaps:")
    print(out["taxonomy_label_gaps.csv"].groupby(
        ["taxonomy_version", "gap_type"]).size().to_string())
    print("\nconcept applicability:")
    print(out["concept_version_applicability.csv"][
        "applicability_status"].value_counts().to_string())
    issues = out["taxonomy_integrity_issues.csv"]
    print(f"\nintegrity issues: {len(issues)}")
    if len(issues):
        print(issues.groupby(["taxonomy_version", "check", "severity"])
              .size().to_string())
    print(f"\nOutputs: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
