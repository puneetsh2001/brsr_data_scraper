"""Phase 7: metric registry outputs and dimension model."""

import pandas as pd
import pytest

from app.core.http_client import sha256_file
from app.metrics import metric_registry as mr


def load(name):
    return pd.read_csv(mr.OUTPUT_DIR / name, dtype=str, keep_default_na=False)


@pytest.fixture(scope="module")
def built():
    before = {p: sha256_file(p) for p in (mr.MAP_FILE, mr.CROSSWALK)}
    mr.build()
    after = {p: sha256_file(p) for p in before}
    return before, after


def test_reference_files_not_modified(built):
    before, after = built
    assert before == after


def test_registry_one_row_per_metric(built):
    reg = load("metric_registry.csv")
    src = pd.read_csv(mr.MAP_FILE, dtype=str, keep_default_na=False)
    assert reg["metric_id"].is_unique
    assert set(reg["metric_id"]) == set(src["requirement_id"])


def test_no_registry_errors(built):
    issues = load("metric_registry_issues.csv")
    assert issues[issues["severity"] == "ERROR"].empty, issues


def test_concepts_exist_and_units_match_types(built):
    c = load("metric_concepts.csv")
    assert (c["in_current_taxonomy"] == "True").all()
    assert (c["abstract"] == "False").all()
    assert (c["unit_type_consistent"] == "True").all()


def test_map_dimensions_valid_in_current_taxonomy(built):
    d = load("metric_dimension_checks.csv")
    current = mr.versions()[1]
    cur = d[(d["taxonomy_version"] == current)
            & (d["kind"] != "NO_AXES_IN_MAP")]
    assert len(cur) > 0 and (cur["result"] == "OK").all()


def test_candidate_mappings_never_available(built):
    a = load("metric_version_availability.csv")
    pending = a[a["crosswalk_status"].str.contains("CANDIDATE_REVIEW")]
    assert (pending["availability"] == "PENDING_REVIEW").all()
    available = a[a["availability"] == "AVAILABLE"]
    assert not available["crosswalk_status"].str.contains(
        "CANDIDATE_REVIEW").any()


def test_every_pipeline_disagreement_is_explained(built):
    r = load("metric_coverage_reconciliation.csv")
    assert (r["disagree_unexplained"].astype(int) == 0).all()


def test_coverage_counts_are_bounded(built):
    cov = load("metric_fact_coverage.csv")
    for col in ("filings", "filings_where_available", "filings_reporting_cy"):
        cov[col] = cov[col].astype(int)
    assert (cov["filings_where_available"] <= cov["filings"]).all()
    assert (cov["filings_reporting_cy"] <= cov["filings_where_available"]
            ).all()


def test_dimension_model(tmp_path, monkeypatch):
    rows = [
        ("r", "HeadAbstract", "Table", "all"),
        ("r", "Table", "GenderAxis", "hypercube-dimension"),
        ("r", "GenderAxis", "GenderMember", "dimension-domain"),
        ("r", "GenderMember", "MaleMember", "domain-member"),
        ("r", "HeadAbstract", "LineItems", "domain-member"),
        ("r", "LineItems", "Headcount", "domain-member"),
        ("other", "Unrelated", "Headcount2", "domain-member"),
    ]
    folder = tmp_path / "2099-01-01"
    folder.mkdir()
    pd.DataFrame([{"role_uri": r, "from_concept": f, "to_concept": t,
                   "arcrole": mr.DIM + a, "order": 1}
                  for r, f, t, a in rows]).to_csv(
        folder / "nse_brsr_definition_relationships.csv", index=False)
    monkeypatch.setattr(mr, "VERSIONS_DIR", tmp_path)
    axes_of, members_of = mr.dimension_model("2099-01-01", "current")
    assert axes_of["Headcount"] == {"GenderAxis"}
    assert "Headcount2" not in axes_of
    assert members_of["GenderAxis"] == {"GenderMember", "MaleMember"}
