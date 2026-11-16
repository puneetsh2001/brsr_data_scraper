"""
BRSR taxonomy concept crosswalk across SEBI taxonomy versions.

Maps every concept of each older taxonomy (2021-09-30, 2023-06-30,
2024-04-30, 2025-05-31) to its equivalent in the current taxonomy
(2026-02-28). Adjacent versions are matched first and the chain is then
composed to the current version.

Match methods, strongest first:
  EXACT_NAME        same concept name
  CASE_INSENSITIVE  same name ignoring case (e.g. ...forPerformanceand...)
  SAME_LABEL        identical standard label (normalised) - renamed concept
  VALUE_EVIDENCE    same company + period + dimensions + value reported
                    under the old concept in the older report and under
                    the new concept (as previous-year comparative) in the
                    next report
  NAME_SIMILARITY   close name/label similarity - CANDIDATE ONLY, needs
                    manual review, not used for automatic mapping
  NO_EQUIVALENT     no match found (concept removed or restructured)

Inputs : concept tables built by nse_brsr_taxonomy_mapping_builder.py
         (data/processed/taxonomy_versions/<version>/ and, for the current
         version, data/processed/) and, optionally, extracted facts.
Outputs: data/processed/taxonomy_versions/
           nse_brsr_concept_crosswalk_adjacent.csv
           nse_brsr_concept_crosswalk.csv          (old concept -> 2026)

No database access.

Run:
    python -m app.extractors.nse_brsr_taxonomy_crosswalk
"""

import argparse
import difflib
import re

from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd


BASE_DIR = Path(__file__).resolve().parents[2]
VERSIONS_DIR = BASE_DIR / "data" / "processed" / "taxonomy_versions"

VERSIONS = ["2021-09-30", "2023-06-30", "2024-04-30", "2025-05-31",
            "2026-02-28"]
CURRENT = VERSIONS[-1]

DEFAULT_FACTS = [
    BASE_DIR / "data" / "processed" / "nifty100" / "multi_company_facts.csv",
]

ADJACENT_OUTPUT = VERSIONS_DIR / "nse_brsr_concept_crosswalk_adjacent.csv"
CROSSWALK_OUTPUT = VERSIONS_DIR / "nse_brsr_concept_crosswalk.csv"

# Value evidence thresholds.
MIN_AGREEMENTS = 3
MIN_SHARE = 0.8

# Values too common to be evidence of identity.
TRIVIAL_VALUES = {
    "", "0", "0.0", "0.00", "1", "true", "false", "yes", "no", "na", "n.a.",
    "nil", "none", "-", "not applicable", "not applicable.", "nan",
}

SIMILARITY_THRESHOLD = 0.88

# Systematic renames published by SEBI between taxonomy versions.
# 2025-05-31: the BRSR Core assurance block was widened from "reasonable
# assurance" to "assessment or assurance".
RENAME_RULES = [
    (re.compile(r"ReasonableAssurance"), "AssessmentOrAssurance"),
    (re.compile(r"(?<!Or)Assurance(?=Provider|Obtain|Abstract)"),
     "AssessmentOrAssurance"),
    (re.compile(r"(?<!Or)Assurer$"), "AssessorOrAssurer"),
    (re.compile(r"OfAssurer$"), "OfAssessorOrAssurer"),
    (re.compile(r"ByAssurer$"), "ByAssessorOrAssurer"),
    (re.compile(r"TheAssurer$"), "TheAssessorOrAssurer"),
]

# Mappings whose meaning or value domain changed; values must be
# translated, so these are always flagged for review.
VALUE_TRANSLATIONS = {
    "WhetherTheCompanyHasUndertakenReasonableAssuranceOfTheBRSRCore": (
        "Boolean -> enumeration. true -> 'Yes assurance'; "
        "false -> 'No' (old flag covered reasonable assurance only, so "
        "an assessment may still have been obtained)"
    ),
}

STRUCTURAL_SUFFIXES = ("abstract", "table", "axis", "domain", "member",
                       "lineitems")


def structural_suffix(name):
    lower = name.lower()
    for suffix in STRUCTURAL_SUFFIXES:
        if lower.endswith(suffix):
            return suffix
    return ""


def apply_rename_rules(name):
    renamed = name
    for pattern, replacement in RENAME_RULES:
        renamed = pattern.sub(replacement, renamed)
    return renamed if renamed != name else None


# ==========================================================
# LOAD CONCEPT TABLES
# ==========================================================

def concept_table_path(version):
    if version == CURRENT:
        return BASE_DIR / "data" / "processed" / "nse_brsr_taxonomy_concepts.csv"
    return VERSIONS_DIR / version / "nse_brsr_taxonomy_concepts.csv"


def normalise_label(label):
    label = (label or "").lower()
    label = re.sub(r"\[[^\]]*\]", " ", label)       # drop [Abstract] etc.
    label = re.sub(r"[^a-z0-9]+", " ", label)
    return " ".join(label.split())


def load_concepts(version):
    df = pd.read_csv(concept_table_path(version), dtype=str).fillna("")
    df = df.drop_duplicates("concept_name")
    df["label_key"] = df["standard_label"].map(normalise_label)
    df["name_key"] = df["concept_name"].str.lower()
    return df.set_index("concept_name", drop=False)


# ==========================================================
# VALUE EVIDENCE FROM EXTRACTED FACTS
# ==========================================================

def normalise_value(value):
    value = str(value).strip().lower()
    try:
        number = float(value.replace(",", ""))
        return f"{number:.6g}"
    except ValueError:
        return " ".join(value.split())


def load_fact_evidence(fact_files):
    """
    Returns {(company, period_key, dims, value): {version: {concepts}}}
    using the period each fact covers, so a CY fact in an old-version
    report lines up with the PY comparative in the next-version report.
    """

    columns = [
        "metric_name", "value", "dimensions", "period_start",
        "period_end", "instant_date", "namespace",
    ]
    evidence = defaultdict(lambda: defaultdict(set))

    for path in fact_files:
        path = Path(path)
        if not path.exists():
            print("Fact file not found, skipping:", path)
            continue

        header = pd.read_csv(path, nrows=0).columns
        company_col = "master_isin" if "master_isin" in header else "isin"

        for chunk in pd.read_csv(
            path, dtype=str, keep_default_na=False,
            usecols=columns + [company_col], chunksize=200_000,
        ):
            chunk["version"] = chunk["namespace"].str.extract(
                r"(\d{4}-\d{2}-\d{2})"
            )[0]
            chunk["value_key"] = chunk["value"].map(normalise_value)
            chunk = chunk[~chunk["value_key"].isin(TRIVIAL_VALUES)]

            # Long free-text blocks are fine as evidence but trim them.
            chunk["value_key"] = chunk["value_key"].str[:300]
            chunk["period_key"] = (
                chunk["period_start"] + "|" + chunk["period_end"]
                + "|" + chunk["instant_date"]
            )

            for row in chunk.itertuples(index=False):
                key = (
                    getattr(row, company_col), row.period_key,
                    row.dimensions, row.value_key,
                )
                evidence[key][row.version].add(row.metric_name)

    return evidence


def value_evidence_pairs(evidence, old_version, new_version):
    """Count (old_concept, new_concept) co-occurrences of identical facts."""

    pair_counts = Counter()
    old_totals = Counter()

    for versions in evidence.values():
        old = versions.get(old_version)
        new = versions.get(new_version)
        if not old or not new:
            continue
        for o in old:
            old_totals[o] += 1
            for n in new:
                pair_counts[(o, n)] += 1

    best = {}
    for (o, n), count in pair_counts.items():
        share = count / old_totals[o]
        if count >= MIN_AGREEMENTS and share >= MIN_SHARE:
            if o not in best or count > best[o][1]:
                best[o] = (n, count, round(share, 3))

    return best


# ==========================================================
# ADJACENT VERSION MATCHING
# ==========================================================

def match_adjacent(old, new, evidence_pairs):
    rows = []
    used_targets = set()

    new_by_name_key = defaultdict(list)
    for name, key in zip(new["concept_name"], new["name_key"]):
        new_by_name_key[key].append(name)

    new_by_label = defaultdict(list)
    for name, key in zip(new["concept_name"], new["label_key"]):
        if key:
            new_by_label[key].append(name)

    unmatched = []

    for concept in old.itertuples():
        name = concept.concept_name

        if name in new.index:
            target, method, detail = name, "EXACT_NAME", ""
        elif len(new_by_name_key.get(concept.name_key, [])) == 1:
            target = new_by_name_key[concept.name_key][0]
            method, detail = "CASE_INSENSITIVE", ""
        elif (
            concept.label_key
            and len(new_by_label.get(concept.label_key, [])) == 1
            and new_by_label[concept.label_key][0] not in old.index
        ):
            target = new_by_label[concept.label_key][0]
            method, detail = "SAME_LABEL", ""
        elif (
            apply_rename_rules(name)
            and apply_rename_rules(name) in new.index
            and apply_rename_rules(name) not in old.index
        ):
            target = apply_rename_rules(name)
            method, detail = "RENAME_RULE", "SEBI systematic rename"
        elif name in evidence_pairs:
            target, count, share = evidence_pairs[name]
            method = "VALUE_EVIDENCE"
            detail = f"{count} matching facts, share {share}"
        else:
            unmatched.append(concept)
            continue

        used_targets.add(target)
        rows.append(build_row(concept, new.loc[target], method, detail))

    # Similarity candidates only among targets not otherwise claimed and
    # not present in the old version (i.e. genuinely new concepts).
    candidates = new[
        ~new["concept_name"].isin(used_targets)
        & ~new["concept_name"].isin(old.index)
    ]

    for concept in unmatched:
        best, best_score = None, 0.0
        suffix = structural_suffix(concept.concept_name)
        for target in candidates.itertuples():
            # Compare like with like: Table with Table, Axis with Axis,
            # fact concept with fact concept. Data type may legitimately
            # change; build_row flags it.
            if structural_suffix(target.concept_name) != suffix:
                continue
            score = max(
                difflib.SequenceMatcher(
                    None, concept.concept_name, target.concept_name
                ).ratio(),
                difflib.SequenceMatcher(
                    None, concept.label_key, target.label_key
                ).ratio() if concept.label_key and target.label_key else 0,
            )
            if score > best_score:
                best, best_score = target, score

        if best is not None and best_score >= SIMILARITY_THRESHOLD:
            row = build_row(
                concept, new.loc[best.concept_name], "NAME_SIMILARITY",
                f"similarity {best_score:.3f}",
            )
            row["needs_review"] = True
        else:
            row = build_row(concept, None, "NO_EQUIVALENT", "")
        rows.append(row)

    return rows


def build_row(old_concept, new_concept, method, detail):
    has_target = new_concept is not None

    row = {
        "source_concept": old_concept.concept_name,
        "source_label": old_concept.standard_label,
        "source_data_type": old_concept.data_type,
        "source_period_type": old_concept.period_type,
        "source_abstract": old_concept.abstract,
        "target_concept": new_concept["concept_name"] if has_target else "",
        "target_label": new_concept["standard_label"] if has_target else "",
        "target_data_type": new_concept["data_type"] if has_target else "",
        "target_period_type": (
            new_concept["period_type"] if has_target else ""
        ),
        "match_method": method,
        "match_detail": detail,
        "needs_review": False,
    }

    flags = []
    if has_target and row["source_data_type"] != row["target_data_type"]:
        flags.append("DATA_TYPE_CHANGED")
    if has_target and row["source_period_type"] != row["target_period_type"]:
        flags.append("PERIOD_TYPE_CHANGED")
    row["value_translation"] = VALUE_TRANSLATIONS.get(
        old_concept.concept_name, ""
    )
    if row["value_translation"]:
        flags.append("VALUE_TRANSLATION_REQUIRED")
    row["change_flags"] = " | ".join(flags)

    if flags:
        row["needs_review"] = True

    return row


# ==========================================================
# COMPOSE CHAIN TO CURRENT VERSION
# ==========================================================

AUTO_METHODS = {"EXACT_NAME", "CASE_INSENSITIVE", "SAME_LABEL",
                "RENAME_RULE", "VALUE_EVIDENCE"}


DECISIONS_FILE = (
    BASE_DIR / "data" / "reference" / "brsr_crosswalk_review_decisions.csv"
)

# Review decisions that clear the needs_review flag of a link.
CLEARING_DECISIONS = {
    "APPROVED_SPELLING_CORRECTION",
    "IDENTITY_CONFIRMED_TYPE_CHANGED",
}


def load_decisions():
    if not DECISIONS_FILE.exists():
        return {}
    table = pd.read_csv(DECISIONS_FILE, dtype=str).fillna("")
    return {
        (r.source_version, r.source_concept, r.target_concept):
            (r.decision, r.reason)
        for r in table.itertuples()
    }


def compose(adjacent):
    """old concept -> current concept, following accepted links only."""

    decisions = load_decisions()

    links = {}
    for row in adjacent.itertuples():
        links[(row.source_version, row.source_concept)] = row

    current_concepts = load_concepts(CURRENT)
    rows = []

    for version in VERSIONS[:-1]:
        subset = adjacent[adjacent["source_version"] == version]

        for start in subset.itertuples():
            concept = start.source_concept
            path = [f"{version}:{concept}"]
            methods = []
            review = False
            flags = []
            link_decisions = []
            ok = True
            v_index = VERSIONS.index(version)

            for step_version in VERSIONS[v_index:-1]:
                link = links.get((step_version, concept))
                if link is None or link.match_method not in (
                    AUTO_METHODS | {"NAME_SIMILARITY"}
                ):
                    ok = False
                    methods.append(link.match_method if link else "MISSING")
                    break
                methods.append(link.match_method)
                if link.change_flags:
                    flags.append(link.change_flags)

                link_review = str(link.needs_review) == "True"
                if link_review:
                    decision, reason = decisions.get(
                        (step_version, link.source_concept,
                         link.target_concept),
                        ("CANDIDATE_REVIEW", "no review decision recorded"),
                    )
                    link_decisions.append((decision, reason))
                    if decision not in CLEARING_DECISIONS:
                        review = True
                concept = link.target_concept
                path.append(
                    f"{VERSIONS[VERSIONS.index(step_version) + 1]}:{concept}"
                )

            target = concept if ok else ""
            label = (
                current_concepts.loc[target, "standard_label"]
                if target in current_concepts.index else ""
            )

            approved = any(
                d == "APPROVED_SPELLING_CORRECTION" for d, _ in link_decisions
            )

            if not ok:
                status = "NO_CURRENT_EQUIVALENT"
            elif review:
                status = "CANDIDATE_REVIEW"
            elif all(m == "EXACT_NAME" for m in methods):
                status = "UNCHANGED"
            elif approved:
                status = "MAPPED_APPROVED"
            else:
                status = "MAPPED"

            if not link_decisions:
                approval_status = "NOT_REQUIRED"
            elif review:
                approval_status = "CANDIDATE_REVIEW"
            elif approved:
                approval_status = "APPROVED_SPELLING_CORRECTION"
            else:
                approval_status = "IDENTITY_CONFIRMED_TYPE_CHANGED"

            rows.append({
                "source_version": version,
                "source_concept": start.source_concept,
                "source_label": start.source_label,
                "current_concept": target,
                "current_label": label,
                "status": status,
                "approval_status": approval_status,
                "approval_reason": " || ".join(r for _, r in link_decisions),
                "methods": " > ".join(methods),
                "change_flags": " | ".join(sorted(set(
                    f for flag in flags for f in flag.split(" | ") if f
                ))),
                "chain": " > ".join(path),
            })

    return pd.DataFrame(rows)


# ==========================================================
# MAIN
# ==========================================================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--facts", nargs="*", default=[str(p) for p in DEFAULT_FACTS],
        help="Extracted fact CSVs used for value evidence",
    )
    args = parser.parse_args()

    print("Loading concept tables...")
    tables = {v: load_concepts(v) for v in VERSIONS}
    for v, t in tables.items():
        print(f"  {v}: {len(t)} concepts")

    print("Loading fact evidence...")
    evidence = load_fact_evidence(args.facts)
    print("  evidence keys:", len(evidence))

    adjacent_rows = []
    for old_v, new_v in zip(VERSIONS[:-1], VERSIONS[1:]):
        pairs = value_evidence_pairs(evidence, old_v, new_v)
        rows = match_adjacent(tables[old_v], tables[new_v], pairs)
        for row in rows:
            row["source_version"] = old_v
            row["target_version"] = new_v
        adjacent_rows.extend(rows)

        summary = Counter(r["match_method"] for r in rows)
        print(f"\n{old_v} -> {new_v}: {dict(summary)}")

    adjacent = pd.DataFrame(adjacent_rows)
    first = ["source_version", "target_version"]
    adjacent = adjacent[first + [c for c in adjacent.columns
                                 if c not in first]]
    VERSIONS_DIR.mkdir(parents=True, exist_ok=True)
    adjacent.to_csv(ADJACENT_OUTPUT, index=False, encoding="utf-8-sig")

    crosswalk = compose(adjacent)
    crosswalk.to_csv(CROSSWALK_OUTPUT, index=False, encoding="utf-8-sig")

    print("\nCrosswalk to", CURRENT)
    print(pd.crosstab(crosswalk["status"], crosswalk["source_version"])
          .to_string())

    print("\nOutputs:")
    print(" ", ADJACENT_OUTPUT)
    print(" ", CROSSWALK_OUTPUT)


if __name__ == "__main__":
    main()
