"""
Phase 16: XBRL parser gaps from master prompt section 18 (different
namespaces, multiple units, taxonomy version differences), the sample
instance end to end, and the raw-fact -> requirement-value integration.

Synthetic instances are written to tmp_path; the sample instance and the
parsed fact store are only read.
"""

from collections import Counter

import pandas as pd
import pytest

from app.parsers import xbrl_fact_store as fact_store
from app.parsers.xbrl_instance import parse_instance
from tests.conftest import PROJECT_ROOT, production_values
from tests.test_xbrl_parser import UNITS, ctx

SEBI = "https://www.sebi.gov.in/xbrl/{v}/in-capmkt"
SAMPLE = PROJECT_ROOT / "data/raw/nse_brsr_xml/sample_brsr.xml"
# present in the 2021-09-30 taxonomy, removed before 2026-02-28
OLD_ONLY = "OpeningOutstandingFundsRaisedThroughDebtConsolidated"


def head(version="2026-02-28", prefix="in-capmkt", extra_ns=""):
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            '<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance" '
            'xmlns:link="http://www.xbrl.org/2003/linkbase" '
            'xmlns:xlink="http://www.w3.org/1999/xlink" '
            'xmlns:xbrldi="http://xbrl.org/2006/xbrldi" '
            'xmlns:iso4217="http://www.xbrl.org/2003/iso4217" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
            f'xmlns:{prefix}="{SEBI.format(v=version)}"{extra_ns}>'
            '<link:schemaRef xlink:type="simple" '
            f'xlink:href="in-capmkt-ent-{version}.xsd"/>')


def parse(tmp_path, body, version="2026-02-28", prefix="in-capmkt",
          extra_ns="", units=UNITS, contexts=None, name="x.xml"):
    path = tmp_path / name
    path.write_text(head(version, prefix, extra_ns)
                    + (contexts if contexts is not None else ctx("CY"))
                    + units + body + "</xbrli:xbrl>", encoding="utf-8")
    return parse_instance(path, "F-T")


def codes(out):
    return Counter(i["code"] for i in out["issues"])


# ---- namespaces --------------------------------------------------------------

def test_prefix_does_not_matter_only_the_namespace(tmp_path):
    body = ('<{p}:Turnover contextRef="CY" unitRef="INR" decimals="0">5'
            '</{p}:Turnover>')
    a = parse(tmp_path, body.format(p="in-capmkt"), name="a.xml")
    b = parse(tmp_path, body.format(p="cap"), prefix="cap", name="b.xml")
    fa, fb = a["facts"][0], b["facts"][0]
    for key in ("concept", "namespace", "data_type", "type_family",
                "numeric_value", "unit_measure"):
        assert fa[key] == fb[key], key
    assert fa["namespace"] == SEBI.format(v="2026-02-28")
    assert codes(a) == codes(b)


def test_same_local_name_in_other_namespace_is_not_a_duplicate(tmp_path):
    out = parse(tmp_path,
                '<in-capmkt:Turnover contextRef="CY" unitRef="INR" '
                'decimals="0">5</in-capmkt:Turnover>'
                '<ext:Turnover contextRef="CY" unitRef="INR" decimals="0">9'
                '</ext:Turnover>',
                extra_ns=' xmlns:ext="http://example.com/extension"')
    assert {f["namespace"] for f in out["facts"]} == {
        SEBI.format(v="2026-02-28"), "http://example.com/extension"}
    assert out["duplicates"] == []                  # different namespaces
    assert codes(out)["DUPLICATE_FACT_INCONSISTENT"] == 0


# ---- multiple units ----------------------------------------------------------

def test_multiple_units_each_fact_keeps_its_own_measure(tmp_path):
    units = UNITS + ('<xbrli:unit id="tCO2e"><xbrli:measure>Non-SI:tCO2e'
                     '</xbrli:measure></xbrli:unit>')
    out = parse(tmp_path,
                '<in-capmkt:Turnover contextRef="CY" unitRef="INR" '
                'decimals="0">100</in-capmkt:Turnover>'
                '<in-capmkt:Turnover contextRef="CY" unitRef="GJperINR" '
                'decimals="INF">0.5</in-capmkt:Turnover>'
                '<in-capmkt:Turnover contextRef="CY" unitRef="tCO2e" '
                'decimals="0">7</in-capmkt:Turnover>', units=units)
    by_unit = {f["unit_id"]: f["unit_measure"] for f in out["facts"]}
    assert by_unit == {"INR": "iso4217:INR",
                       "GJperINR": "Non-SI:GJ/iso4217:INR",
                       "tCO2e": "Non-SI:tCO2e"}
    divide = {u["unit_id"]: u for u in out["units"]}["GJperINR"]
    assert (divide["numerator"], divide["denominator"]) == (
        "Non-SI:GJ", "iso4217:INR")
    # unit differences make these different facts, never duplicates
    assert out["duplicates"] == []
    # a monetary concept tagged with a non-currency unit is flagged
    assert codes(out)["UNIT_TYPE_MISMATCH"] == 2


# ---- taxonomy version differences --------------------------------------------

def test_concept_is_typed_by_the_filing_s_own_taxonomy(tmp_path):
    body = (f'<in-capmkt:{OLD_ONLY} contextRef="CY" unitRef="INR" '
            f'decimals="0">5</in-capmkt:{OLD_ONLY}>')
    old = parse(tmp_path, body, version="2021-09-30", name="old.xml")
    new = parse(tmp_path, body, version="2026-02-28", name="new.xml")
    assert old["document"]["taxonomy_version"] == "2021-09-30"
    assert codes(old)["CONCEPT_NOT_IN_TAXONOMY"] == 0
    assert old["facts"][0]["type_family"] == "monetary"
    assert codes(new)["CONCEPT_NOT_IN_TAXONOMY"] == 1
    assert new["facts"][0]["data_type"] == ""       # never borrowed
    # the value is kept in both cases
    assert old["facts"][0]["value"] == new["facts"][0]["value"] == "5"


def test_mixed_schema_refs_are_unclear_not_guessed(tmp_path):
    path = tmp_path / "mixed.xml"
    path.write_text(head("2026-02-28") + '<link:schemaRef xlink:type="simple" '
        'xlink:href="in-capmkt-ent-2021-09-30.xsd"/>' + ctx("CY") + UNITS
        + "</xbrli:xbrl>", encoding="utf-8")
    out = parse_instance(path, "F")
    assert codes(out)["TAXONOMY_VERSION_UNCLEAR"] == 1
    assert out["document"]["taxonomy_version"] == ""


def test_instant_and_duration_contexts(tmp_path):
    instant = ('<xbrli:context id="I"><xbrli:entity><xbrli:identifier '
               'scheme="http://www.sebi.gov.in">INE000A00000'
               '</xbrli:identifier></xbrli:entity><xbrli:period>'
               '<xbrli:instant>2026-03-31</xbrli:instant></xbrli:period>'
               '</xbrli:context>')
    out = parse(tmp_path, '<in-capmkt:NameOfTheCompany contextRef="I">A'
                '</in-capmkt:NameOfTheCompany>'
                '<in-capmkt:NameOfTheCompany contextRef="CY">A'
                '</in-capmkt:NameOfTheCompany>', contexts=instant + ctx("CY"))
    kinds = {f["context_id"]: (f["period_type"], f["instant"],
                               f["period_end"]) for f in out["facts"]}
    assert kinds["I"] == ("instant", "2026-03-31", "")
    assert kinds["CY"] == ("duration", "", "2026-03-31")


# ---- the sample instance end to end -------------------------------------------

@pytest.fixture(scope="module")
def sample():
    if not SAMPLE.exists():
        pytest.skip("sample_brsr.xml not present")
    return parse_instance(SAMPLE, "SAMPLE")


def test_sample_instance_parses_cleanly(sample):
    doc = sample["document"]
    assert doc["taxonomy_version"] == "2026-02-28"
    errors = [i for i in sample["issues"] if i["severity"] == "ERROR"]
    assert errors == []
    facts = pd.DataFrame(sample["facts"])
    assert len(facts) > 1000
    assert facts["fact_id"].is_unique
    assert set(facts["entity_identifier"]) == {"INE136B01020"}
    numeric = facts[facts["type_family"].isin(["monetary", "numeric",
                                               "pure", "shares"])
                    & (facts["value_status"] == "REPORTED")]
    assert (numeric["unit_measure"] != "").all()
    assert (numeric["numeric_value"] != "").all()


def test_sample_parse_is_deterministic(sample):
    again = parse_instance(SAMPLE, "SAMPLE")
    assert again["facts"] == sample["facts"]
    assert again["issues"] == sample["issues"]


# ---- raw facts -> requirement values (integration) ---------------------------

@pytest.fixture(scope="module")
def raw_facts():
    path = fact_store.OUTPUT_DIR / "xbrl_facts.csv"
    if not path.exists():
        pytest.skip("run python -m app.parsers.xbrl_fact_store first")
    return pd.read_csv(path, dtype=str, keep_default_na=False,
                       usecols=["source_file", "concept", "context_id",
                                "value", "value_status", "namespace"])


@pytest.mark.parametrize("role", ["CY", "PY"])
def test_every_requirement_value_traces_to_one_raw_fact(raw_facts, role):
    values = production_values(role)
    m = values.merge(raw_facts, how="left", indicator=True,
                     left_on=["source_file", "context_reference",
                              "metric_name"],
                     right_on=["source_file", "context_id", "concept"],
                     suffixes=("", "_raw"))
    assert len(m) == len(values)                    # one fact per value
    assert (m["_merge"] == "both").all()
    assert (m["value"] == m["original_value"]).all()   # text unchanged
    assert (m["namespace"].str.startswith("https://www.sebi.gov.in/")).all()
    # every value names its source URL for traceability
    assert (values["source_url"] != "").all()
