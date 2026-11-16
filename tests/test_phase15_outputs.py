"""Phase 15: deliverables, data dictionary, coverage statuses and the CLI."""

import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from tests.conftest import PROJECT_ROOT, changed_since

OUT = PROJECT_ROOT / "data" / "outputs"
N = PROJECT_ROOT / "data" / "processed" / "nifty100"
BACKUP = N / "backups" / "20261112_pre_phase15"

EXPECTED_FILES = [
    "company_master.csv", "filing_manifest.csv", "taxonomy_concepts.csv",
    "concept_metric_mapping.csv", "all_brsr_facts.csv",
    "normalised_brsr_facts.csv", "environmental_metrics.csv",
    "social_metrics.csv", "governance_metrics.csv",
    "corporate_identity_metrics.csv", "assurance_metrics.csv",
    "metric_coverage_report.csv", "missing_metrics_report.csv",
    "unmapped_concepts_report.csv", "unit_validation_report.csv",
    "data_quality_report.csv", "block_validation_report.csv",
    "scale_error_recommendations.csv", "restatement_validation_report.csv",
    "final_processing_summary.csv"]
PILLARS = {"environmental_metrics.csv": "Environmental",
           "social_metrics.csv": "Social",
           "governance_metrics.csv": "Governance",
           "corporate_identity_metrics.csv": "Corporate",
           "assurance_metrics.csv": "Assurance"}


def read(name, **kw):
    return pd.read_csv(OUT / name, dtype=str, keep_default_na=False,
                       encoding="utf-8-sig", **kw)


def src(rel, **kw):
    return pd.read_csv(PROJECT_ROOT / rel, dtype=str, keep_default_na=False,
                       **kw)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def manifest():
    return json.loads((OUT / "manifest.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def coverage():
    return read("metric_coverage_report.csv")


@pytest.fixture(scope="module")
def pillar_frames():
    return {n: read(n) for n in PILLARS}


@pytest.fixture(scope="module")
def production_cy():
    return src("data/processed/nifty100/normalised/"
               "brsr_requirement_values_normalised.csv")


# ---------------------------------------------------------------- files
def test_all_20_deliverables_exist_with_rows():
    for name in EXPECTED_FILES:
        assert (OUT / name).exists(), name
        assert len(read(name, usecols=[0])) > 0, name
    assert (OUT / "company_year_coverage_summary.csv").exists()


def test_manifest_hashes_and_row_counts(manifest):
    assert set(EXPECTED_FILES) <= set(manifest["outputs"])
    for name, info in manifest["outputs"].items():
        if name == "all_brsr_facts.csv":
            continue                       # checked through the source sha
        assert sha(OUT / name) == info["sha256"], name
        assert len(read(name, usecols=[0])) == info["rows"], name


def test_exact_copies_are_byte_identical():
    pairs = {"company_master.csv": "data/processed/registry/"
             "company_master.csv",
             "filing_manifest.csv": "data/processed/registry/"
             "filing_manifest.csv",
             "all_brsr_facts.csv": "data/processed/nifty100/parsed/"
             "xbrl_facts.csv",
             "data_quality_report.csv": "data/processed/nifty100/"
             "review_workflow/review_queue.csv",
             "block_validation_report.csv": "data/processed/nifty100/"
             "blocks/block_status.csv"}
    for out, source in pairs.items():
        assert sha(OUT / out) == sha(PROJECT_ROOT / source), out


# ------------------------------------------------------- data dictionary
def test_every_column_is_documented():
    from app.reporting import data_dictionary as dd
    assert dd.undocumented() == []
    for name in EXPECTED_FILES:
        assert name in dd.FILES


def test_dictionary_markdown_is_current():
    from app.reporting import data_dictionary as dd
    doc = (PROJECT_ROOT / "docs" / "DATA_DICTIONARY.md").read_text(
        encoding="utf-8")
    assert doc == dd.render()
    for name, cols in dd.output_columns().items():
        if name in dd.FILES:
            for c in cols:
                assert f"| `{c}` |" in doc, (name, c)


# ------------------------------------------------------- reconciliation
def test_normalised_facts_reconcile_with_production(production_cy):
    py = src("data/processed/nifty100/normalised/"
             "brsr_requirement_values_py_normalised.csv", usecols=[0])
    out = read("normalised_brsr_facts.csv")
    assert len(out) == len(production_cy) + len(py)
    assert out.iloc[:len(production_cy)][list(production_cy.columns)] \
        .equals(production_cy)


def test_pillar_files_contain_all_cy_rows(pillar_frames, production_cy):
    total = sum(len(df) for df in pillar_frames.values())
    assert total == len(production_cy)
    for name, df in pillar_frames.items():
        assert set(df["pillar"]) == {PILLARS[name]}
        assert len(df) == int((production_cy["pillar"] ==
                               PILLARS[name]).sum())


def test_pillar_values_unchanged(pillar_frames, production_cy):
    key = ["source_file", "context_reference", "requirement_id",
           "breakdown", "metric_name"]
    cols = ["original_value", "original_unit", "normalised_value",
            "normalised_unit", "conversion_factor", "conversion_status",
            "multiplier_basis"]
    merged = pd.concat(pillar_frames.values())[key + cols].merge(
        production_cy[key + cols], on=key, suffixes=("", "_prod"))
    assert len(merged) == len(production_cy)
    for c in cols:
        assert (merged[c] == merged[c + "_prod"]).all(), c


def test_coverage_report_reconciles(coverage):
    source = src("data/processed/nifty100/brsr_requirement_coverage.csv")
    assert len(coverage) == len(source) == 100 * 5 * 102
    assert (coverage["pipeline_status"].values == source["status"].values) \
        .all()
    changed = coverage[coverage["pipeline_status"] !=
                       coverage["coverage_status"]]
    assert set(changed["coverage_status"]) <= {"EXTRACTION_ERROR"}


def test_missing_report_is_the_non_reported_part(coverage):
    missing = read("missing_metrics_report.csv")
    assert len(missing) == int((~coverage["coverage_status"].isin(
        ["REPORTED", "REPORTED_ZERO"])).sum())
    assert not missing["coverage_status"].isin(
        ["REPORTED", "REPORTED_ZERO"]).any()


def test_scale_and_restatement_reports_reconcile():
    fse = read("scale_error_recommendations.csv")
    assert len(fse) == 39 and fse["approval_id"].is_unique
    v = src("data/processed/nifty100/validation/validation_results.csv",
            usecols=["rule_id"])
    rst = read("restatement_validation_report.csv")
    assert len(rst) == int(v["rule_id"].isin(["RST01", "RST02"]).sum())


def test_unit_report_is_numeric_cy_rows(production_cy):
    unit = read("unit_validation_report.csv", usecols=["conversion_status"])
    assert len(unit) == int((production_cy["conversion_status"] !=
                             "NOT_NUMERIC").sum())


# --------------------------------------------------------- identifiers
def test_company_isin_fy_identifiers_consistent(coverage, pillar_frames):
    master = read("company_master.csv")
    isins = set(master["isin"])
    filings = read("filing_manifest.csv")
    assert set(coverage["master_isin"]) <= isins
    assert len(set(coverage["fy_slot"])) == 5
    assert set(coverage["fy_slot"]) == set(src(
        "data/processed/nifty100/brsr_requirement_coverage.csv",
        usecols=["fy_slot"])["fy_slot"])
    fid = dict(zip(filings["filing_id"], filings["company_id"]))
    cid = dict(zip(master["company_id"], master["isin"]))
    for df in list(pillar_frames.values()) + [coverage]:
        rows = df[df["filing_id"] != ""]
        assert rows["filing_id"].isin(fid.keys()).all()
        assert (rows["filing_id"].map(fid).map(cid) ==
                rows["master_isin"]).all()
    for df in pillar_frames.values():
        assert (df["filing_id"] != "").all()


def test_company_year_summary_grain(coverage):
    s = read("company_year_coverage_summary.csv")
    assert len(s) == 500
    assert not s.duplicated(["master_isin", "fy_slot"]).any()
    for status in ("not_disclosed", "extraction_error", "no_report"):
        assert s[f"metrics_{status}"].astype(int).sum() == int(
            (coverage["coverage_status"] == status.upper()).sum())


# ----------------------------------------------------- missing != zero
def test_missing_values_are_never_zero(coverage):
    not_reported = coverage[coverage["coverage_status"].isin(
        ["NOT_DISCLOSED", "NO_REPORT", "NOT_IN_TAXONOMY_VERSION",
         "EXTERNAL_SOURCE_REQUIRED", "EXTRACTION_ERROR",
         "NOT_APPLICABLE"])]
    assert (not_reported["extracted_value"] == "").all()
    zero = coverage[coverage["coverage_status"] == "REPORTED_ZERO"]
    assert (zero["extracted_value"] != "").all()


def test_blank_original_stays_blank(pillar_frames):
    for df in pillar_frames.values():
        blank = df[df["original_value"] == ""]
        assert (blank["normalised_value"] == "").all()


def test_coverage_statuses_distinct_and_evidence_based(coverage):
    statuses = set(coverage["coverage_status"])
    for s in ("NOT_DISCLOSED", "NO_REPORT", "NOT_IN_TAXONOMY_VERSION",
              "EXTERNAL_SOURCE_REQUIRED", "EXTRACTION_ERROR"):
        assert s in statuses, s
    err = coverage[coverage["coverage_status"] == "EXTRACTION_ERROR"]
    assert (err["coverage_basis"] != "").all()
    assert (err["filing_id"] != "").all()
    nd = coverage[coverage["coverage_status"] == "NOT_DISCLOSED"]
    assert (nd["filing_id"] != "").all()          # a filing exists
    no_report = coverage[coverage["coverage_status"] == "NO_REPORT"]
    assert (no_report["filing_id"] == "").all()


def test_pending_crosswalk_not_counted_as_reported(coverage):
    pending = coverage[coverage["pipeline_status"] ==
                       "REPORTED_PENDING_CROSSWALK_REVIEW"]
    assert len(pending) == 230
    assert (pending["coverage_status"] ==
            "REPORTED_PENDING_CROSSWALK_REVIEW").all()


# ------------------------------------------- pending / deferred preserved
def test_pending_and_deferred_statuses_preserved(pillar_frames,
                                                 production_cy):
    df = pd.concat(pillar_frames.values())
    fse = df[df["scale_decision_id"] != ""]
    assert len(fse) == 39
    assert (fse["record_status"] == "PENDING_SCALE_REVIEW").all()
    assert (fse["multiplier_basis"] != "APPROVED_SCALE_CORRECTION").all()
    deferred = df[df["deferred_issue_ids"] != ""]
    assert len(deferred) == int((production_cy["deferred_issue_ids"] != "")
                                .sum()) > 0
    assert (deferred["record_status"] ==
            "DEFERRED_DATA_QUALITY_ISSUE").all()
    pend_unit = df[df["conversion_status"] ==
                   "PENDING_APPROVAL_UNIT_ASSUMPTION"]
    assert (pend_unit["record_status"] == "PENDING_UNIT_DECISION").all()
    cand = df[df["crosswalk_status"] == "CANDIDATE_REVIEW"]
    assert len(cand) and (cand["record_status"] ==
                          "PENDING_CROSSWALK_REVIEW").all()
    # nothing pending is labelled as normalised
    pending = df[(df["pending_review"] != "") | (df["needs_review"] ==
                                                 "True")]
    assert not pending["record_status"].str.startswith("NORMALISED").any()


def test_no_decisions_taken_in_outputs():
    fse = read("scale_error_recommendations.csv")
    assert (fse["approval_decision"] == "").all()
    q = read("data_quality_report.csv")
    assert (q["reviewer_decision"] == "").all()
    reg = src("data/reference/deferred_issues_register.csv")
    assert len(reg) == 39 and (reg["status"] ==
                               "DEFERRED_PENDING_REVIEW").all()


# ------------------------------------------------------------ assurance
def test_assurance_classifications_distinct(pillar_frames):
    a = pillar_frames["assurance_metrics.csv"]
    assert {"ASSURANCE", "ASSESSMENT"} <= set(a["assurance_engagement_type"])
    levels = set(a["assurance_filing_level_level"])
    assert not levels & {"ASSURANCE", "ASSESSMENT"}
    assert not set(a["assurance_engagement_type"]) & {
        "REASONABLE", "LIMITED", "REASONABLE_BRSR_CORE_2024_FLAG"}
    # the 2024 flag and the 2025 engagement stay separate columns: a filing
    # has a real value in at most one of them (the other is a placeholder)
    placeholder = {"", "NOT_IN_TAXONOMY_VERSION", "NOT_REPORTED"}
    both = a[~a["assurance_reasonable_flag_2024"].isin(placeholder) &
             ~a["assurance_core_engagement_2025"].isin(placeholder)]
    assert both.empty
    assert set(a["assurance_reasonable_flag_2024"]) - placeholder <= {
        "TRUE", "FALSE"}
    eng = src("data/processed/nifty100/assurance/assurance_engagements.csv")
    m = a.merge(eng[["filing_id", "engagement_type"]], on="filing_id")
    assert (m["assurance_engagement_type"] == m["engagement_type"]).all()


# --------------------------------------------------------- supplementary
def test_supplementary_evidence_not_verified(coverage):
    missing = read("missing_metrics_report.csv")
    with_supp = missing[missing["supplementary_evidence_status"] != ""]
    assert (with_supp["supplementary_note"].str.contains(
        "unverified")).all()
    assert (with_supp["supplementary_note"].str.contains(
        "Reliance only")).all()
    # candidates never change coverage or values
    assert not missing["coverage_status"].isin(["REPORTED",
                                                "REPORTED_ZERO"]).any()
    for name in PILLARS:
        assert "supplementary" not in " ".join(read(name, nrows=0).columns)
    summary = read("final_processing_summary.csv")
    assert summary["value"].str.contains("Reliance only").any()


# ------------------------------------------------------------------ CLI
def test_cli_incremental_skips_unchanged(tmp_path):
    from app import pipeline as pl
    inp = tmp_path / "in.txt"
    out = tmp_path / "out.txt"
    inp.write_text("a")
    calls = []

    def runner(stage):
        calls.append(stage.name)
        out.write_text("done")
        return 0

    stage = pl.Stage("demo", "app.pipeline", inputs=[inp], outputs=[out])
    state = tmp_path / "state.json"
    quiet = lambda *_: None
    pl.run_stages([stage], execute=False, state_path=state, runner=runner,
                  echo=quiet)
    assert calls == [] and not state.exists()          # dry run
    pl.run_stages([stage], execute=True, state_path=state, runner=runner,
                  echo=quiet)
    assert calls == ["demo"]
    pl.run_stages([stage], execute=True, state_path=state, runner=runner,
                  echo=quiet)
    assert calls == ["demo"]                           # up to date: skipped
    inp.write_text("b")
    pl.run_stages([stage], execute=True, state_path=state, runner=runner,
                  echo=quiet)
    assert calls == ["demo", "demo"]                   # input changed
    pl.run_stages([stage], execute=True, force=True, state_path=state,
                  runner=runner, echo=quiet)
    assert calls == ["demo", "demo", "demo"]           # forced
    out.unlink()
    steps = pl.plan([stage], pl.load_state(state))
    assert "output missing" in steps[0][1]


def test_cli_downstream_reruns_when_upstream_runs(tmp_path):
    from app import pipeline as pl
    a_in = tmp_path / "a"
    a_in.write_text("1")
    s1 = pl.Stage("one", "app.pipeline", inputs=[a_in])
    s2 = pl.Stage("two", "app.pipeline")
    state = {"one": {"fingerprint": "old"},
             "two": {"fingerprint": pl.fingerprint(s2)}}
    steps = pl.plan([s1, s2], state)
    assert steps[1][1] == ["upstream stage runs"]


def test_cli_network_commands_are_dry_run_by_default(monkeypatch):
    from app import pipeline as pl
    ran = []
    monkeypatch.setattr(pl, "default_runner", lambda s: ran.append(s) or 0)
    for cmd in ("discover", "refresh-taxonomy", "process", "validate"):
        args = pl.build_parser().parse_args([cmd])
        assert args.execute is False
        monkeypatch.setattr(pl, "run_stages",
                            lambda stages, execute=False, force=False,
                            **kw: ran.append(("plan", execute)))
        args.func(args)
    assert all(r == ("plan", False) for r in ran)
    assert all(s.network for s in pl.DISCOVER if "scrapers" in s.module)
    assert not any("pdf" in s.module for s in
                   sum(pl.COMMAND_STAGES.values(), []))


def test_cli_download_is_incremental(capsys):
    from app import pipeline as pl
    args = pl.build_parser().parse_args(["download"])
    args.func(args)
    out = capsys.readouterr().out
    assert "needs_download: 0" in out and "nothing to download" in out


def test_cli_reprocess_requires_selection():
    from app import pipeline as pl
    with pytest.raises(SystemExit):
        pl.main(["reprocess"])
    with pytest.raises(SystemExit):
        pl.filings_for("NO_SUCH_COMPANY")
    sel = pl.filings_for("RELIANCE", "2023-24")
    assert len(sel) == 1


def test_load_db_blocked_without_approval(monkeypatch):
    from app import pipeline as pl
    from app.db import connection
    assert connection.PRODUCTION_WRITES_ENABLED is False
    monkeypatch.setenv("DB_NAME", "brsr_db")
    with pytest.raises(connection.ProductionWriteBlocked):
        pl.load_db_gate("brsr_db", execute=True, promote=True)
    with pytest.raises(connection.NotATestDatabase):
        pl.load_db_gate("some_other_db", execute=True)
    # test database: dry run unless --execute, gate reported as blocked
    assert pl.load_db_gate("brsr_phase14_test") == "DRY_RUN"
    from app.core import deferred_issues
    with pytest.raises(deferred_issues.DeferredIssuesOpen):
        deferred_issues.assert_none_open("production import")


# ------------------------------------------------------------- audit
def test_phase15_did_not_change_processed_or_reference_files():
    assert changed_since(BACKUP / "all_files_before.sha") == []
