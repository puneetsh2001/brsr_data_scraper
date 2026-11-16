"""Phase 6: XBRL instance parser on synthetic instances + real-data checks."""

from collections import Counter

import pandas as pd
import pytest

from app.parsers import xbrl_fact_store as fact_store
from app.parsers.xbrl_instance import parse_instance, type_family

HEAD = ('<?xml version="1.0" encoding="UTF-8"?>'
        '<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance" '
        'xmlns:link="http://www.xbrl.org/2003/linkbase" '
        'xmlns:xlink="http://www.w3.org/1999/xlink" '
        'xmlns:xbrldi="http://xbrl.org/2006/xbrldi" '
        'xmlns:iso4217="http://www.xbrl.org/2003/iso4217" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'xmlns:in-capmkt="https://www.sebi.gov.in/xbrl/2026-02-28/in-capmkt">'
        '<link:schemaRef xlink:type="simple" '
        'xlink:href="in-capmkt-ent-2026-02-28.xsd"/>')
CTX = ('<xbrli:context id="{id}"><xbrli:entity><xbrli:identifier '
       'scheme="http://www.sebi.gov.in">INE000A00000</xbrli:identifier>'
       '{seg}</xbrli:entity><xbrli:period><xbrli:startDate>{s}'
       '</xbrli:startDate><xbrli:endDate>{e}</xbrli:endDate></xbrli:period>'
       '</xbrli:context>')
UNITS = ('<xbrli:unit id="INR"><xbrli:measure>iso4217:INR</xbrli:measure>'
         '</xbrli:unit><xbrli:unit id="pure"><xbrli:measure>xbrli:pure'
         '</xbrli:measure></xbrli:unit><xbrli:unit id="GJperINR">'
         '<xbrli:divide><xbrli:unitNumerator><xbrli:measure>Non-SI:GJ'
         '</xbrli:measure></xbrli:unitNumerator><xbrli:unitDenominator>'
         '<xbrli:measure>iso4217:INR</xbrli:measure></xbrli:unitDenominator>'
         '</xbrli:divide></xbrli:unit>')


def ctx(cid, s="2025-04-01", e="2026-03-31", seg=""):
    return CTX.format(id=cid, s=s, e=e, seg=seg)


def instance(tmp_path, body, contexts=None, units=UNITS):
    contexts = contexts if contexts is not None else ctx("CY")
    path = tmp_path / "x.xml"
    path.write_text(HEAD + contexts + units + body + "</xbrli:xbrl>",
                    encoding="utf-8")
    return parse_instance(path, "F-TEST")


def codes(out, severity=None):
    return Counter(i["code"] for i in out["issues"]
                   if severity is None or i["severity"] == severity)


def test_clean_instance(tmp_path):
    out = instance(tmp_path,
                   '<in-capmkt:NameOfTheCompany contextRef="CY">Acme'
                   '</in-capmkt:NameOfTheCompany>'
                   '<in-capmkt:Turnover contextRef="CY" unitRef="INR" '
                   'decimals="-5">-1234500000</in-capmkt:Turnover>')
    assert out["document"]["taxonomy_version"] == "2026-02-28"
    assert not codes(out, "ERROR") and not codes(out, "WARNING")
    f = {x["concept"]: x for x in out["facts"]}
    assert f["Turnover"]["type_family"] == "monetary"
    assert f["Turnover"]["numeric_value"] == "-1234500000"
    assert f["Turnover"]["sign"] == "NEGATIVE"
    assert f["Turnover"]["unit_measure"] == "iso4217:INR"
    assert f["NameOfTheCompany"]["type_family"] == "non_numeric"
    assert out["facts"][0]["fact_id"] == "F-TEST#000001"


def test_value_text_is_preserved_exactly(tmp_path):
    out = instance(tmp_path, '<in-capmkt:Turnover contextRef="CY" '
                   'unitRef="INR" decimals="2"> 001234.50 '
                   '</in-capmkt:Turnover>')
    f = out["facts"][0]
    assert f["value"] == "001234.50"          # only outer whitespace removed
    assert f["numeric_value"] == "1234.50"   # exact Decimal, no float
    assert f["value_decimal_places"] == 2


def test_undefined_context_is_kept_and_reported(tmp_path):
    out = instance(tmp_path, '<in-capmkt:NameOfTheCompany contextRef="NOPE">'
                   'Acme</in-capmkt:NameOfTheCompany>')
    assert len(out["facts"]) == 1
    assert codes(out)["UNDEFINED_CONTEXT"] == 1


def test_unit_problems(tmp_path):
    out = instance(tmp_path,
                   '<in-capmkt:Turnover contextRef="CY" decimals="0">5'
                   '</in-capmkt:Turnover>'
                   '<in-capmkt:Turnover contextRef="CY" unitRef="X" '
                   'decimals="0">5</in-capmkt:Turnover>'
                   '<in-capmkt:Turnover contextRef="CY" unitRef="pure" '
                   'decimals="0">6</in-capmkt:Turnover>'
                   '<in-capmkt:NameOfTheCompany contextRef="CY" unitRef="INR">'
                   'Acme</in-capmkt:NameOfTheCompany>')
    c = codes(out)
    assert c["NUMERIC_FACT_WITHOUT_UNIT"] == 1
    assert c["UNDEFINED_UNIT"] == 1
    assert c["UNIT_TYPE_MISMATCH"] == 1
    assert c["UNIT_ON_NON_NUMERIC_FACT"] == 1


def test_unit_id_disagreeing_with_measure(tmp_path):
    units = ('<xbrli:unit id="Gigajoule"><xbrli:measure>Non-SI:J'
             '</xbrli:measure></xbrli:unit>')
    out = instance(tmp_path, "", units=units)
    assert codes(out)["UNIT_ID_DISAGREES_WITH_MEASURE"] == 1


def test_decimals_and_numbers(tmp_path):
    out = instance(tmp_path,
                   '<in-capmkt:Turnover contextRef="CY" unitRef="INR">1'
                   '</in-capmkt:Turnover>'
                   '<in-capmkt:Turnover contextRef="CY" unitRef="INR" '
                   'decimals="0" precision="3">2</in-capmkt:Turnover>'
                   '<in-capmkt:Turnover contextRef="CY" unitRef="INR" '
                   'decimals="abc">3</in-capmkt:Turnover>'
                   '<in-capmkt:Turnover contextRef="CY" unitRef="INR" '
                   'decimals="0">1,234</in-capmkt:Turnover>'
                   '<in-capmkt:Turnover contextRef="CY" unitRef="INR" '
                   'decimals="0">1e5</in-capmkt:Turnover>'
                   '<in-capmkt:Turnover contextRef="CY" unitRef="INR" '
                   'decimals="0"></in-capmkt:Turnover>')
    c = codes(out)
    assert c["NUMERIC_WITHOUT_DECIMALS_OR_PRECISION"] == 1
    assert c["DECIMALS_AND_PRECISION"] == 1
    assert c["INVALID_DECIMALS"] == 1
    assert c["NOT_A_VALID_NUMBER"] == 2     # "1,234" and "1e5" never guessed
    assert c["EMPTY_NUMERIC_FACT"] == 1
    bad = [f for f in out["facts"] if f["value"] in ("1,234", "1e5")]
    assert all(f["numeric_value"] == "" for f in bad)


def test_nil_is_not_zero(tmp_path):
    out = instance(tmp_path, '<in-capmkt:Turnover contextRef="CY" '
                   'unitRef="INR" xsi:nil="true"/>')
    f = out["facts"][0]
    assert f["value_status"] == "NIL" and f["numeric_value"] == ""
    assert f["sign"] == ""


def test_dimensions_and_equivalent_contexts(tmp_path):
    seg = ('<xbrli:segment><xbrldi:explicitMember '
           'dimension="in-capmkt:GenderAxis">in-capmkt:MaleMember'
           '</xbrldi:explicitMember><xbrldi:typedMember '
           'dimension="in-capmkt:PlantAxis"><in-capmkt:PlantDomain>P1'
           '</in-capmkt:PlantDomain></xbrldi:typedMember></xbrli:segment>')
    out = instance(tmp_path, "", contexts=ctx("A", seg=seg)
                   + ctx("B", seg=seg))
    assert len(out["dimensions"]) == 4
    typed = [d for d in out["dimensions"] if d["is_typed"]]
    assert typed[0]["typed_value"] == "P1" and typed[0]["member"] == ""
    a, b = out["contexts"]
    assert a["context_key"] == b["context_key"]
    assert a["dimension_key"] == ("in-capmkt:GenderAxis=in-capmkt:MaleMember"
                                  " | in-capmkt:PlantAxis=[P1]")
    assert codes(out)["EQUIVALENT_CONTEXTS"] == 1


def test_period_problems(tmp_path):
    out = instance(tmp_path, "", contexts=ctx("BAD", s="2026-04-01",
                                              e="2025-03-31")
                   + ctx("DATE", s="01/04/2025"))
    c = codes(out)
    assert c["PERIOD_END_BEFORE_START"] == 1
    assert c["INVALID_PERIOD_DATE"] == 1


def test_duplicate_classification(tmp_path):
    # PY context mis-dated like the real BHARTIARTL FY2022-23 filing
    contexts = ctx("PY", "2024-04-01", "2025-03-31") + \
        ctx("PPY", "2024-04-01", "2025-03-31") + ctx("CY")
    out = instance(tmp_path,
                   '<in-capmkt:Turnover contextRef="PY" unitRef="INR" '
                   'decimals="0">0.3</in-capmkt:Turnover>'
                   '<in-capmkt:Turnover contextRef="PPY" unitRef="INR" '
                   'decimals="0">0.19</in-capmkt:Turnover>'
                   '<in-capmkt:Turnover contextRef="CY" unitRef="INR" '
                   'decimals="2">10.004</in-capmkt:Turnover>'
                   '<in-capmkt:Turnover contextRef="CY" unitRef="INR" '
                   'decimals="0">10</in-capmkt:Turnover>'
                   '<in-capmkt:NameOfTheCompany contextRef="CY">Acme'
                   '</in-capmkt:NameOfTheCompany>'
                   '<in-capmkt:NameOfTheCompany contextRef="CY">Acme'
                   '</in-capmkt:NameOfTheCompany>', contexts=contexts)
    by_class = Counter(d["duplicate_class"] for d in out["duplicates"])
    # 0.3 vs 0.19 at decimals=0 both round to 0 -> CONSISTENT by the
    # XBRL rule; 10.004@2 vs 10@0 -> CONSISTENT; identical text COMPLETE
    assert by_class == {"CONSISTENT": 4, "COMPLETE": 2}
    out2 = instance(tmp_path,
                    '<in-capmkt:Turnover contextRef="PY" unitRef="INR" '
                    'decimals="INF">0.3</in-capmkt:Turnover>'
                    '<in-capmkt:Turnover contextRef="PPY" unitRef="INR" '
                    'decimals="INF">0.19</in-capmkt:Turnover>',
                    contexts=contexts)
    assert {d["duplicate_class"] for d in out2["duplicates"]} == {
        "INCONSISTENT"}
    assert codes(out2, "ERROR")["DUPLICATE_FACT_INCONSISTENT"] == 1


def test_rounding_declaration_recorded_not_applied(tmp_path):
    out = instance(tmp_path,
                   '<in-capmkt:LevelOfRoundingUsedInFinancialStatements '
                   'contextRef="CY">Crores'
                   '</in-capmkt:LevelOfRoundingUsedInFinancialStatements>'
                   '<in-capmkt:Turnover contextRef="CY" unitRef="INR" '
                   'decimals="0">5</in-capmkt:Turnover>')
    assert out["document"]["declared_rounding"] == "Crores"
    turnover = [f for f in out["facts"] if f["concept"] == "Turnover"][0]
    assert turnover["numeric_value"] == "5"


def test_unknown_taxonomy_and_concept(tmp_path):
    path = tmp_path / "y.xml"
    path.write_text(HEAD.replace("2026-02-28.xsd", "2099-01-01.xsd")
                    + ctx("CY") + UNITS
                    + '<in-capmkt:Foo contextRef="CY">1</in-capmkt:Foo>'
                    '</xbrli:xbrl>', encoding="utf-8")
    assert codes(parse_instance(path, "F"))[
        "TAXONOMY_VERSION_NOT_REGISTERED"] == 1
    out = instance(tmp_path, '<in-capmkt:MadeUpConcept contextRef="CY">x'
                   '</in-capmkt:MadeUpConcept>')
    assert codes(out)["CONCEPT_NOT_IN_TAXONOMY"] == 1


def test_broken_and_non_xbrl_files(tmp_path):
    bad = tmp_path / "bad.xml"
    bad.write_text("<html>login", encoding="utf-8")
    assert codes(parse_instance(bad, "F")) == {"XML_NOT_WELL_FORMED": 1}
    html = tmp_path / "page.xml"
    html.write_text("<html><body/></html>", encoding="utf-8")
    assert codes(parse_instance(html, "F")) == {"NOT_AN_XBRL_INSTANCE": 1}


def test_entities_not_expanded(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP-SECRET", encoding="utf-8")
    path = tmp_path / "evil.xml"
    path.write_text(f'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY s SYSTEM '
                    f'"{secret.as_uri()}">]>' + HEAD[HEAD.index("<xbrli:"):]
                    + ctx("CY") + '<in-capmkt:NameOfTheCompany '
                    'contextRef="CY">&s;</in-capmkt:NameOfTheCompany>'
                    '</xbrli:xbrl>', encoding="utf-8")
    out = parse_instance(path, "F")
    assert all("TOP-SECRET" not in f["value"] for f in out["facts"])


@pytest.mark.parametrize("dtype,family", [
    ("xbrli:monetaryItemType", "monetary"),
    ("num:percentItemType", "pure"),
    ("dtr-types:energyItemType", "numeric"),
    ("num:massItemType", "numeric"),
    ("xbrli:stringItemType", "non_numeric"),
    ("in-capmkt-types:YesNoNA", "non_numeric"),
    ("", "non_numeric"),
])
def test_type_family(dtype, family):
    bases = {"in-capmkt-types:YesNoNA": "xbrli:tokenItemType"}
    assert type_family(dtype, bases) == family


# ---- real data (outputs of app.parsers.xbrl_fact_store) ---------------

PARSED = fact_store.OUTPUT_DIR


@pytest.fixture(scope="module")
def parsed_summary():
    path = PARSED / "xbrl_parse_summary.csv"
    if not path.exists():
        pytest.skip("run python -m app.parsers.xbrl_fact_store first")
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def test_new_parser_reproduces_current_extraction(parsed_summary):
    recon = pd.read_csv(PARSED / "xbrl_reconciliation.csv", dtype=str)
    assert len(recon) == len(parsed_summary)
    assert (recon["identical"] == "True").all()


def test_every_filing_version_agrees(parsed_summary):
    assert (parsed_summary["taxonomy_version_agrees"] == "True").all()


def test_no_missing_units_in_real_data():
    issues = pd.read_csv(PARSED / "xbrl_parse_issues.csv", dtype=str)
    for code in ("NUMERIC_FACT_WITHOUT_UNIT", "UNDEFINED_UNIT",
                 "UNDEFINED_CONTEXT", "NOT_A_VALID_NUMBER"):
        assert not (issues["code"] == code).any(), code


def test_fact_ids_unique():
    ids = pd.read_csv(PARSED / "xbrl_facts.csv", dtype=str,
                      usecols=["fact_id"])["fact_id"]
    assert ids.is_unique
