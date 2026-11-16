"""Phase 5: taxonomy registry outputs and parser behaviour."""

import pandas as pd
import pytest

from app.taxonomy import taxonomy_registry as tr

OUT = tr.OUTPUT_DIR


def load(name):
    return pd.read_csv(OUT / name, dtype=str, keep_default_na=False)


@pytest.fixture(scope="module")
def built():
    tr.build()
    return {name: load(name) for name in (
        "taxonomy_registry.csv", "taxonomy_files.csv",
        "taxonomy_calculation_relationships.csv",
        "taxonomy_label_completeness.csv", "taxonomy_label_gaps.csv",
        "concept_version_applicability.csv",
        "taxonomy_integrity_issues.csv")}


def test_five_versions_one_current(built):
    reg = built["taxonomy_registry.csv"]
    assert list(reg["taxonomy_version"]) == [
        "2021-09-30", "2023-06-30", "2024-04-30", "2025-05-31", "2026-02-28"]
    assert (reg["status"] == "CURRENT").sum() == 1
    assert reg.iloc[-1]["status"] == "CURRENT"


def test_every_file_matches_its_source_archive(built):
    files = built["taxonomy_files.csv"]
    assert (files["archive_check"] == "MATCHES_SOURCE_ARCHIVE").all()
    assert files["sha256"].str.len().eq(64).all()


def test_no_integrity_errors(built):
    issues = built["taxonomy_integrity_issues.csv"]
    assert issues[issues["severity"] == "ERROR"].empty, issues


def test_every_filing_version_is_registered(built, filing_manifest):
    used = set(filing_manifest["taxonomy_version"]) - {""}
    assert used <= set(built["taxonomy_registry.csv"]["taxonomy_version"])
    observed = built["taxonomy_registry.csv"]["observed_filings"].astype(
        int).sum()
    assert observed == (filing_manifest["taxonomy_version"] != "").sum()


def test_documentation_labels_not_invented(built):
    comp = built["taxonomy_label_completeness.csv"]
    doc = comp[comp["label_role"] == "documentation"]
    assert (doc["status"] == "NOT_PROVIDED_BY_SEBI").all()
    assert (doc["concepts_with_label"] == "0").all()


def test_calculation_arcs_resolve_to_known_concepts(built):
    calc = built["taxonomy_calculation_relationships.csv"]
    app = built["concept_version_applicability.csv"]
    for version, group in calc.groupby("taxonomy_version"):
        known = set(app.loc[app[f"in_{version}"] == "True", "concept_name"])
        assert set(group["parent_concept"]) <= known
        assert set(group["child_concept"]) <= known
    assert calc["arcrole"].str.endswith("summation-item").all()


def test_counts_match_extractor_outputs(built):
    reg = built["taxonomy_registry.csv"].set_index("taxonomy_version")
    for version in reg.index:
        base = (tr.settings.processed_dir if reg.loc[version, "status"]
                == "CURRENT" else tr.VERSIONS_PROCESSED / version)
        for col, name in (("presentation_arcs",
                           "nse_brsr_presentation_relationships.csv"),
                          ("definition_arcs",
                           "nse_brsr_definition_relationships.csv"),
                          ("concepts", "nse_brsr_taxonomy_concepts.csv")):
            assert int(reg.loc[version, col]) == len(
                pd.read_csv(base / name)), (version, col)


def test_removed_concepts_have_crosswalk_entry(built):
    app = built["concept_version_applicability.csv"]
    removed = app[app["applicability_status"] != "IN_CURRENT_TAXONOMY"]
    assert len(removed) > 0
    assert (removed["crosswalk_status"] != "NOT_IN_CROSSWALK").all()
    # mapped concepts must name a concept that exists today
    current = set(app.loc[app["applicability_status"]
                          == "IN_CURRENT_TAXONOMY", "concept_name"])
    mapped = removed[removed["crosswalk_current_concept"] != ""]
    assert set(mapped["crosswalk_current_concept"]) <= current


def test_crosswalk_file_not_modified(built):
    before = tr.sha256_file(tr.CROSSWALK)
    tr.build()
    assert tr.sha256_file(tr.CROSSWALK) == before


def test_build_is_deterministic(built):
    first = {p.name: tr.sha256_file(p) for p in OUT.glob("*.csv")}
    tr.build()
    second = {p.name: tr.sha256_file(p) for p in OUT.glob("*.csv")}
    assert first == second


def test_shared_locator_label_counts_once(tmp_path):
    lb = tmp_path / "x-cal-2099-01-01.xml"
    lb.write_text(
        '<link:linkbase xmlns:link="http://www.xbrl.org/2003/linkbase" '
        'xmlns:xlink="http://www.w3.org/1999/xlink">'
        '<link:calculationLink xlink:type="extended" xlink:role="r">'
        '<link:loc xlink:type="locator" xlink:href="a.xsd#p_Total" '
        'xlink:label="T"/>'
        '<link:loc xlink:type="locator" xlink:href="a.xsd#p_Total" '
        'xlink:label="T"/>'
        '<link:loc xlink:type="locator" xlink:href="a.xsd#p_Part" '
        'xlink:label="P"/>'
        '<link:calculationArc xlink:type="arc" xlink:from="T" xlink:to="P" '
        'xlink:arcrole="summation-item" weight="1" order="1"/>'
        '<link:calculationArc xlink:type="arc" xlink:from="T" '
        'xlink:to="MISSING" weight="1"/>'
        '</link:calculationLink></link:linkbase>', encoding="utf-8")
    issues = []
    rows = tr.parse_arcs(lb, "calculationLink", "v", issues,
                         {"Total": {}, "Part": {}})
    assert len(rows) == 1 and rows[0]["parent_concept"] == "Total"
    assert [i["check"] for i in issues] == ["calculationLink_ARC_UNRESOLVED"]


def test_parser_does_not_expand_entities(tmp_path):
    evil = tmp_path / "evil.xml"
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP-SECRET", encoding="utf-8")
    evil.write_text(
        f'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY s SYSTEM '
        f'"{secret.as_uri()}">]><r>&s;</r>', encoding="utf-8")
    root = tr.parse(evil)
    assert "TOP-SECRET" not in "".join(root.itertext())
