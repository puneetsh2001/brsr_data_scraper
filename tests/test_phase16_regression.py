"""
Phase 16: regression baseline of the accepted (Phase 15) state.

tests/fixtures/phase16_regression_baseline.json freezes, for the accepted
outputs and decisions:

  * the SHA-256 of every approved production value file, the raw fact
    store, every decision / review file and the derived evidence layers;
  * the status distributions those files carry (unit decisions, the 39
    filer scale-error records, the 39 deferred issues, crosswalk approval
    states, review queue, MtCO2e outcomes, assurance classifications and
    findings, supplementary evidence classifications, block statuses);
  * the schema (column order) and row count of every deliverable in
    data/outputs/.

A difference means an approved value, a pending decision or an output
schema changed. The baseline is the owner-accepted Phase 15 state; it may
only be rewritten after an owner-approved change, with

    python -m tests.test_phase16_regression --write-baseline

The test also checks every documented status vocabulary of
docs/DATA_DICTIONARY.md against the values actually present in
data/outputs/.
"""

import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

BASELINE = PROJECT_ROOT / "tests" / "fixtures" / \
    "phase16_regression_baseline.json"
N = "data/processed/nifty100"
FSE = (f"{N}/filer_scale_error_review/approval/final_review/"
       "strong_recommendations_final_review.csv")
PROTECTED_FILES = [
    # approved production values and the raw facts they come from
    f"{N}/normalised/brsr_requirement_values_normalised.csv",
    f"{N}/normalised/brsr_requirement_values_py_normalised.csv",
    f"{N}/brsr_requirement_values.csv",
    f"{N}/brsr_requirement_values_py.csv",
    f"{N}/parsed/xbrl_facts.csv",
    # decisions (unit, scale, crosswalk, review log, deferred issues)
    "data/reference/brsr_mtco2e_unit_decisions.csv",
    "data/reference/brsr_crosswalk_review_decisions.csv",
    "data/reference/brsr_requirement_concept_map.csv",
    "data/reference/deferred_issues_register.csv",
    "data/reference/review_decisions.csv",
    "data/processed/taxonomy_versions/nse_brsr_concept_crosswalk.csv",
    FSE,
    # derived layers that carry review / classification statuses
    f"{N}/review_workflow/review_queue.csv",
    f"{N}/validation/validation_results.csv",
    f"{N}/blocks/block_status.csv",
    f"{N}/assurance/assurance_engagements.csv",
    f"{N}/assurance/assurance_issues.csv",
    f"{N}/assurance/assurance_providers.csv",
    f"{N}/supplementary/external_requirement_candidates.csv",
    f"{N}/supplementary/external_requirement_coverage.csv",
    f"{N}/supplementary/assurance_statement_evidence.csv",
    f"{N}/supplementary/xbrl_pdf_crosscheck.csv",
    f"{N}/supplementary/document_inventory.csv",
]
DISTRIBUTIONS = {
    # name: (file, [columns])
    "production_cy": (PROTECTED_FILES[0], ["final_status_or_conversion",
                                           "multiplier_basis"]),
    "production_py": (PROTECTED_FILES[1], ["final_status_or_conversion",
                                           "multiplier_basis"]),
    "mtco2e_decisions": ("data/reference/brsr_mtco2e_unit_decisions.csv",
                         ["decision"]),
    "scale_error_records": (FSE, ["approval_decision",
                                  "recommended_decision"]),
    "deferred_issues": ("data/reference/deferred_issues_register.csv",
                        ["status"]),
    "crosswalk": ("data/processed/taxonomy_versions/"
                  "nse_brsr_concept_crosswalk.csv",
                  ["status", "approval_status"]),
    "crosswalk_decisions": ("data/reference/"
                            "brsr_crosswalk_review_decisions.csv",
                            ["decision"]),
    "review_queue": (f"{N}/review_workflow/review_queue.csv",
                     ["category", "status"]),
    "review_queue_mtco2e": (f"{N}/review_workflow/review_queue.csv",
                            ["detection_rules", "source_decision",
                             "status"]),
    "assurance_engagements": (f"{N}/assurance/assurance_engagements.csv",
                              ["engagement_type",
                               "filing_level_assurance_level"]),
    "assurance_issues": (f"{N}/assurance/assurance_issues.csv",
                         ["issue_code", "severity"]),
    "supplementary_candidates": (f"{N}/supplementary/"
                                 "external_requirement_candidates.csv",
                                 ["requirement_id", "status"]),
    "supplementary_coverage": (f"{N}/supplementary/"
                               "external_requirement_coverage.csv",
                               ["status"]),
    "assurance_phrases": (f"{N}/supplementary/"
                          "assurance_statement_evidence.csv",
                          ["evidence", "phrase_type", "found"]),
    "pdf_crosscheck": (f"{N}/supplementary/xbrl_pdf_crosscheck.csv",
                       ["result"]),
    "blocks": (f"{N}/blocks/block_status.csv", ["block_status"]),
}


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def read(rel, **kw):
    return pd.read_csv(PROJECT_ROOT / rel, dtype=str, keep_default_na=False,
                       **kw)


def distribution(rel, columns):
    head = pd.read_csv(PROJECT_ROOT / rel, nrows=0).columns
    real = []
    for c in columns:
        if c == "final_status_or_conversion":
            c = "final_status" if "final_status" in head else \
                "conversion_status"
        real.append(c)
    df = read(rel, usecols=real)
    if "detection_rules" in real:
        df = df[df["detection_rules"].str.contains("A43_AMBIGUOUS_GHG_UNIT")]
    counts = Counter(" || ".join(r) for r in df[real].values.tolist())
    return {"columns": real, "rows": len(df),
            "counts": dict(sorted(counts.items()))}


def output_schemas():
    out = {}
    manifest = json.loads((PROJECT_ROOT / "data/outputs/manifest.json")
                          .read_text(encoding="utf-8"))
    for name, meta in sorted(manifest["outputs"].items()):
        cols = list(pd.read_csv(PROJECT_ROOT / "data/outputs" / name,
                                nrows=0).columns)
        out[name] = {"columns": cols, "rows": meta["rows"]}
    return out


def collect_state():
    return {
        "sha256": {rel: sha(PROJECT_ROOT / rel) for rel in PROTECTED_FILES},
        "distributions": {k: distribution(*v)
                          for k, v in DISTRIBUTIONS.items()},
        "outputs": output_schemas(),
    }


@pytest.fixture(scope="module")
def baseline():
    return json.loads(BASELINE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def state():
    return collect_state()


@pytest.mark.parametrize("rel", PROTECTED_FILES)
def test_protected_file_unchanged(baseline, state, rel):
    assert state["sha256"][rel] == baseline["sha256"][rel], rel


@pytest.mark.parametrize("name", sorted(DISTRIBUTIONS))
def test_status_distribution_unchanged(baseline, state, name):
    assert state["distributions"][name] == baseline["distributions"][name]


def test_output_schemas_and_row_counts_unchanged(baseline, state):
    assert state["outputs"] == baseline["outputs"]


def test_baseline_records_the_accepted_decision_state(baseline):
    """The frozen baseline itself holds the owner-stated invariants, so it
    cannot silently be regenerated from a changed state."""
    d = baseline["distributions"]
    fse = d["scale_error_records"]
    assert fse["rows"] == 39
    assert all(k.startswith(" || ") for k in fse["counts"])   # undecided
    assert d["deferred_issues"]["rows"] == 39
    assert set(d["deferred_issues"]["counts"]) == {"DEFERRED_PENDING_REVIEW"}
    mt = d["mtco2e_decisions"]["counts"]
    assert mt == {"APPROVED_MEGATONNE": 2, "APPROVED_METRIC_TONNE": 53,
                  "FLAGGED_RESTATEMENT_DIFFERS": 20,
                  "PENDING_REVIEW": 474, "UNDETERMINED_MANUAL_REVIEW": 35}
    q = Counter()
    for key, n in d["review_queue"]["counts"].items():
        q[key.split(" || ")[1]] += n
    assert "APPROVED" not in q and q["APPLIED"] == 55
    issues = Counter()
    for key, n in d["assurance_issues"]["counts"].items():
        issues[key.split("_")[0]] += n
    assert issues["AS02"] == 9 and issues["AS08"] == 2
    assert issues["AS07"] == 38
    xw = Counter()
    for key, n in d["crosswalk"]["counts"].items():
        xw[key.split(" || ")[1]] += n
    assert xw["CANDIDATE_REVIEW"] == 7
    assert d["production_cy"]["rows"] == 90517
    assert len(baseline["outputs"]) == 21


# ---- documented status vocabularies ----------------------------------------

TOKEN = re.compile(r"[A-Z0-9_]+")
# columns documented as lists of statuses (format "STATUS:count | ...")
LIST_COLUMNS = {"open_review_items"}
# columns that list the distinct statuses of a multi-value cell
MULTI_VALUE = {("metric_coverage_report.csv", "record_status")}


def documented_vocabularies():
    from app.reporting import data_dictionary as dd
    vocab = {}
    for col, spec in dd.COLUMNS.items():
        allowed = [t.strip() for t in (spec[3] if len(spec) > 3 else "")
                   .split("|")]
        if len(allowed) >= 2 and all(TOKEN.fullmatch(t) for t in allowed):
            vocab[col] = set(allowed)
    return vocab


def test_vocabularies_are_documented():
    vocab = documented_vocabularies()
    for col in ("record_status", "coverage_status", "pipeline_status",
                "block_status", "validation_status", "multiplier_basis",
                "availability", "assurance_engagement_type", "severity",
                "open_review_items"):
        assert col in vocab, col


def test_output_values_stay_within_documented_vocabularies():
    vocab = documented_vocabularies()
    problems = {}
    for path in sorted((PROJECT_ROOT / "data/outputs").glob("*.csv")):
        cols = [c for c in pd.read_csv(path, nrows=0).columns if c in vocab]
        if not cols:
            continue
        df = pd.read_csv(path, dtype=str, keep_default_na=False,
                         usecols=cols)
        for c in cols:
            for value in df[c].unique():
                if value == "":
                    continue
                if c in LIST_COLUMNS:
                    tokens = [t.split(":")[0] for t in value.split(" | ")]
                elif (path.name, c) in MULTI_VALUE:
                    tokens = value.split(" | ")
                else:
                    tokens = [value]
                bad = [t for t in tokens if t not in vocab[c]]
                if bad:
                    problems.setdefault((path.name, c), set()).update(bad)
    assert problems == {}


def test_multi_value_record_status_is_documented():
    from app.reporting import data_dictionary as dd
    assert "multi-value" in dd.COLUMNS["record_status"][2]


if __name__ == "__main__" and "--write-baseline" in sys.argv:
    BASELINE.parent.mkdir(parents=True, exist_ok=True)
    BASELINE.write_text(json.dumps(collect_state(), indent=1,
                                   sort_keys=True) + "\n", encoding="utf-8")
    print("baseline written:", BASELINE)
