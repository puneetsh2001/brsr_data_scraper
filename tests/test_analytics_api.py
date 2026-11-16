"""
Analytics API tests on an isolated database.

The module fixture creates brsr_phase14_pytest_api, migrates it and stages
+ promotes the approved outputs of a handful of companies chosen by data
properties (a failed filing, a not-applicable disclosure, an ambiguous
unit, filed zeros, a missing FY, a candidate crosswalk value, a unit
conversion, an approved unit decision). Expected values are read from the
same source CSVs, independently of the API. The database is dropped at
the end; the production database fingerprint is compared before/after.
"""

from decimal import Decimal

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.analytics import db as adb
from app.analytics import service
from app.api import analytics_routes
from app.api.main import app
from app.db import connection as dbc
from app.db import migrate
from app.db import staging_loader as sl
from tests.conftest import PROJECT_ROOT
from tests.test_db_phase14 import pg_available, write_csv

TEST_DB = "brsr_phase14_pytest_api"
N = "data/processed/nifty100"
CY = f"{N}/normalised/brsr_requirement_values_normalised.csv"
PY = f"{N}/normalised/brsr_requirement_values_py_normalised.csv"
MASTER = "data/processed/registry/company_master.csv"
MANIFEST = "data/processed/registry/filing_manifest.csv"
FACTS = f"{N}/parsed/xbrl_facts.csv"
TABLES = ["company", "company_identifier", "taxonomy_version", "filing",
          "requirement_concept_map", "requirement_value", "conversion_audit",
          "xbrl_fact", "review_recommendation", "deferred_issue"]

pytestmark = pytest.mark.skipif(not pg_available(),
                                reason="PostgreSQL not reachable")


def read(rel, **kw):
    return pd.read_csv(PROJECT_ROOT / rel, dtype=str, keep_default_na=False,
                       **kw)


def first(df, mask):
    hit = df[mask]
    assert len(hit), "fixture property not found in the data"
    return hit.iloc[0]


@pytest.fixture(scope="module")
def src():
    cy, master, man = read(CY), read(MASTER), read(MANIFEST)
    rmap = read("data/reference/brsr_requirement_concept_map.csv")
    universe = master[master["in_processing_universe"] == "True"]
    scope = man[man["in_processing_scope"] == "True"]
    cy = cy[cy["master_isin"].isin(universe["isin"])]
    pick = {}
    failed = first(scope, scope["processing_status"] == "DOWNLOAD_FAILED")
    pick["failed"] = master.loc[master["company_id"] == failed["company_id"],
                                "isin"].item()
    pick["ambiguous"] = first(cy, (cy["requirement_id"] == "E07") & (
        cy["conversion_status"] == "NOT_CONVERTED_UNIT_AMBIGUOUS"))[
        "master_isin"]
    pick["zero"] = first(cy, (cy["requirement_id"] == "S19")
                         & (cy["original_value"] == "0"))["master_isin"]
    pick["converted"] = first(cy, (cy["conversion_status"] == "CONVERTED")
                              & cy["requirement_id"].str.startswith("E"))[
        "master_isin"]
    pick["assumption"] = first(
        cy, cy["conversion_status"] == "CONVERTED_APPROVED_UNIT_ASSUMPTION")[
        "master_isin"]
    pick["candidate"] = first(cy, cy["crosswalk_status"] ==
                              "CANDIDATE_REVIEW")["master_isin"]
    # Stage 2D: a pending filer scale-error record and a deferred issue
    pick["scale"] = first(cy, (cy["scale_decision_id"] != "") & (
        cy["scale_decision_outcome"] != "APPROVED_APPLIED"))["master_isin"]
    pick["deferred"] = first(cy, cy["deferred_issue_ids"] != "")["master_isin"]
    per_co = scope.groupby("company_id")["fy_slot"].nunique()
    short = per_co[per_co < per_co.max()].index[0]
    pick["short"] = master.loc[master["company_id"] == short, "isin"].item()
    # a company whose filing says GHG details are not applicable
    app_concepts = sorted(set(rmap["applicability_concept"]) - {""})
    facts = []
    filings_all = set(scope["filing_id"])
    for chunk in pd.read_csv(PROJECT_ROOT / FACTS, dtype=str,
                             keep_default_na=False, chunksize=250_000):
        facts.append(chunk[chunk["concept"].isin(app_concepts)
                           & chunk["filing_id"].isin(filings_all)])
    facts = pd.concat(facts)
    na = first(facts, facts["value"].str.lower() == "false")
    pick["not_applicable"] = master.loc[master["company_id"] ==
                                        na["company_id"], "isin"].item()
    isins = set(pick.values())
    ids = set(master.loc[master["isin"].isin(isins), "company_id"])
    return {"pick": pick, "isins": isins, "ids": ids, "facts": facts,
            "na_fact": na, "app_concepts": app_concepts}


@pytest.fixture(scope="module")
def client(src, tmp_path_factory):
    root = tmp_path_factory.mktemp("api_sources")
    isins, ids = src["isins"], src["ids"]
    man = read(MANIFEST)
    man = man[man["company_id"].isin(ids)]
    filings = set(man["filing_id"])
    tax = set(man["taxonomy_version"]) - {""}
    for name in TABLES:
        for rel in sl.BY_NAME[name]["sources"]:
            if rel == FACTS:
                df = src["facts"][src["facts"]["filing_id"].isin(filings)]
            else:
                df = read(rel)
            if rel == MANIFEST:
                df = man
            elif "company_id" in df.columns and rel != FACTS:
                df = df[df["company_id"].isin(ids)]
            elif "master_isin" in df.columns:
                df = df[df["master_isin"].isin(isins)]
            elif rel.endswith("taxonomy_registry.csv"):
                df = df[df["taxonomy_version"].isin(tax)]
            write_csv(root / rel, df)
    prod_before = dbc.production_fingerprint()
    dbc.drop_test_database(TEST_DB)
    dbc.create_test_database(TEST_DB)
    conn = dbc.connect(TEST_DB, write=True)
    migrate.up(conn)
    conn.close()
    batch, summary = sl.stage(TEST_DB, TABLES, root=root)
    assert all(s["rows_rejected"] == 0 for s in summary), summary
    sl.promote(TEST_DB, batch)

    def cursor():
        with adb.cursor(TEST_DB) as cur:
            yield cur
    app.dependency_overrides[analytics_routes.get_cursor] = cursor
    yield TestClient(app)
    app.dependency_overrides.clear()
    dbc.drop_test_database(TEST_DB)
    assert dbc.production_fingerprint() == prod_before


@pytest.fixture(scope="module")
def cy(src):
    df = read(CY)
    return df[df["master_isin"].isin(src["isins"])]


def cid(isin):
    return f"C-{isin}"


def get(client, url, status=200):
    r = client.get(url)
    assert r.status_code == status, (url, r.status_code, r.text[:300])
    return r.json()


def all_values(payload):
    return [v for g in payload["metrics"] for v in g["values"]]


# ---- company search and selection --------------------------------------------

def test_health_is_read_only(client):
    h = get(client, "/api/health")
    assert h == {"database": TEST_DB, "read_only": "on"}


def test_company_search(client, src):
    j = get(client, "/api/companies")
    assert {c["isin"] for c in j["companies"]} == src["isins"]
    master = read(MASTER).set_index("isin")
    isin = src["pick"]["zero"]
    for q in (master.loc[isin, "nse_symbol"].lower(), isin,
              master.loc[isin, "legal_name"][:6]):
        hits = get(client, f"/api/companies?q={q}")["companies"]
        assert isin in {c["isin"] for c in hits}, q
    assert get(client, "/api/companies?q=zzzz-no-such")["total"] == 0
    get(client, "/api/companies?limit=0", 422)


def test_company_lookup_by_id_isin_and_symbol(client, src):
    isin = src["pick"]["zero"]
    sym = read(MASTER).set_index("isin").loc[isin, "nse_symbol"]
    for key in (cid(isin), isin, sym, sym.lower()):
        assert get(client, f"/api/companies/{key}")["company"]["isin"] == isin
    ids = get(client, f"/api/companies/{cid(isin)}")["identifiers"]
    assert {"ISIN", "NSE_SYMBOL"} <= {i["identifier_type"] for i in ids}


def test_years_match_the_filing_manifest(client, src):
    man = read(MANIFEST)
    for isin in src["isins"]:
        m = man[(man["company_id"] == cid(isin))
                & (man["in_processing_scope"] == "True")]
        years = get(client, f"/api/companies/{cid(isin)}/years")["years"]
        assert [y["fiscal_year"] for y in years] == sorted(set(m["fy_slot"]))
        for y in years:
            latest = m[(m["fy_slot"] == y["fiscal_year"])
                       & (m["is_latest_valid"] == "True")]
            if len(latest):
                assert y["filing"]["filing_id"] == latest.iloc[-1][
                    "filing_id"]


def test_extraction_error_year(client, src):
    isin = src["pick"]["failed"]
    years = get(client, f"/api/companies/{cid(isin)}/years")["years"]
    bad = [y for y in years if y["status"] == "EXTRACTION_ERROR"]
    assert bad and bad[0]["filing"] is None and bad[0]["failed_filings"]
    j = get(client, f"/api/companies/{cid(isin)}/years/"
                    f"{bad[0]['fiscal_year']}/metrics")
    assert j["metrics"] == []
    assert {m["disclosure_status"] for m in j["missing"]} <= {
        "EXTRACTION_ERROR", "NOT_IN_BRSR_XBRL"}
    assert all(m["value"] is None for m in j["missing"])


# ---- invalid input ----------------------------------------------------------------

@pytest.mark.parametrize("url,status", [
    ("/api/companies/C-XX0000000000/years", 404),
    ("/api/companies/NO_SUCH_SYMBOL", 404),
    ("/api/companies/{c}/years/2024-26/metrics", 400),
    ("/api/companies/{c}/years/24-25/metrics", 400),
    ("/api/companies/{c}/years/1999-00/metrics", 404),
    ("/api/companies/{c}/years/{fy}/pillars/finance", 400),
    ("/api/companies/{c}/years/{fy}/themes/unknown", 400),
    ("/api/companies/{c}/metrics/X99/history", 404),
    ("/api/compare?metric=E07&companies=C-XX0000000000", 404),
    ("/api/compare?metric=E07&companies={c}&fy=2024-2025", 400),
    ("/api/compare?metric=E07", 422),
])
def test_invalid_requests(client, src, url, status):
    isin = src["pick"]["zero"]
    fy = get(client, f"/api/companies/{cid(isin)}/years")["years"][-1][
        "fiscal_year"]
    get(client, url.format(c=cid(isin), fy=fy), status)


def test_company_outside_universe_is_not_served(client):
    master = read(MASTER)
    outside = master[master["in_processing_universe"] != "True"].iloc[0]
    get(client, f"/api/companies/{outside['company_id']}", 404)


# ---- values: energy, workforce, POSH ------------------------------------------------

def expected(cy, isin, fy, reqs):
    return cy[(cy["master_isin"] == isin) & (cy["fy_slot"] == fy)
              & cy["requirement_id"].isin(reqs)]


def test_energy_values_match_source(client, src, cy):
    isin = src["pick"]["converted"]
    years = get(client, f"/api/companies/{cid(isin)}/years")["years"]
    for y in [y for y in years if y["status"] == "PROCESSED"]:
        j = get(client, f"/api/companies/{cid(isin)}/years/"
                        f"{y['fiscal_year']}/energy")
        reqs = {g["requirement_id"] for g in j["metrics"]} | {
            m["requirement_id"] for m in j["missing"]}
        assert reqs == {f"E0{i}" for i in range(1, 9)}
        exp = expected(cy, isin, y["fiscal_year"], reqs)
        got = all_values(j)
        assert len(got) == len(exp)
        by = {(r["requirement_id"], r["metric_name"], r["breakdown"]): r
              for r in exp.to_dict("records")}
        for v in got:
            r = by[(v["requirement_id"], v["concept"], v["breakdown"])]
            assert v["reported_value"] == r["original_value"]
            assert v["reported_unit"] == (r["original_unit"] or None)
            if v["value"] is not None:
                assert Decimal(v["value"]) == Decimal(r["normalised_value"])
                assert v["unit"] == r["normalised_unit"]
            else:
                assert r["normalised_value"] == ""


def test_unit_conversion_is_explained(client, src, cy):
    isin = src["pick"]["converted"]
    row = first(cy, (cy["master_isin"] == isin)
                & (cy["conversion_status"] == "CONVERTED")
                & cy["requirement_id"].str.startswith("E"))
    j = get(client, f"/api/companies/{cid(isin)}/years/{row['fy_slot']}/"
                    f"metrics?metric={row['requirement_id']}")
    v = next(v for v in all_values(j) if v["concept"] == row["metric_name"]
             and v["breakdown"] == row["breakdown"])
    assert v["value_basis"] == "CALCULATED_UNIT_CONVERSION"
    calc = v["calculation"]
    assert calc["formula"] == row["conversion_formula"]
    assert Decimal(row["original_value"]) * Decimal(calc["factor"]) == \
        Decimal(v["value"])
    assert calc["inputs"][0]["value"] == row["original_value"]
    assert calc["inputs"][0]["unit"] == row["original_unit"]


def test_approved_unit_decision_is_labelled(client, src, cy):
    isin = src["pick"]["assumption"]
    row = first(cy, (cy["master_isin"] == isin) & (
        cy["conversion_status"] == "CONVERTED_APPROVED_UNIT_ASSUMPTION"))
    j = get(client, f"/api/companies/{cid(isin)}/years/{row['fy_slot']}/"
                    f"metrics?metric={row['requirement_id']}")
    v = next(v for v in all_values(j) if v["concept"] == row["metric_name"]
             and v["breakdown"] == row["breakdown"])
    assert v["value_basis"] == "CALCULATED_APPROVED_UNIT_DECISION"
    assert v["calculation"]["decision"] == row["unit_decision"]


def test_ambiguous_unit_is_never_given_a_value(client, src, cy):
    isin = src["pick"]["ambiguous"]
    row = first(cy, (cy["master_isin"] == isin) & (cy["requirement_id"] ==
                                                   "E07") & (
        cy["conversion_status"] == "NOT_CONVERTED_UNIT_AMBIGUOUS"))
    j = get(client, f"/api/companies/{cid(isin)}/years/{row['fy_slot']}/"
                    "energy")
    v = next(v for v in all_values(j) if v["concept"] == row["metric_name"])
    assert v["value"] is None and v["unit"] is None
    assert v["value_numeric"] is None
    assert v["value_basis"] == "REPORTED_NOT_NORMALISED"
    assert v["reported_value"] == row["original_value"]
    assert v["reported_unit"] == row["original_unit"]


def test_workforce_gender_grid(client, src, cy):
    isin = src["pick"]["zero"]
    fy = first(cy, (cy["master_isin"] == isin)
               & (cy["requirement_id"] == "S01"))["fy_slot"]
    j = get(client, f"/api/companies/{cid(isin)}/years/{fy}/workforce")
    s01 = next(g for g in j["metrics"] if g["requirement_id"] == "S01")
    exp = expected(cy, isin, fy, ["S01"])
    assert len(s01["values"]) == len(exp)
    grid = {row["category"]: row["cells"] for row in s01["gender_grid"]}
    for r in exp.to_dict("records"):
        parts = dict(p.split("=") for p in r["breakdown"].split(" | "))
        gender = parts.pop("GenderAxis")
        category = " | ".join(f"{a}={m}" for a, m in parts.items())
        label = {"GenderMember": "Total", "MaleMember": "Male",
                 "FemaleMember": "Female",
                 "OtherGenderMember": "Other Gender"}[gender]
        cell = grid[category][label]
        assert Decimal(cell["value"]) == Decimal(r["normalised_value"])
        assert cell["unit"] == r["normalised_unit"]


def test_posh_values_and_reported_zero(client, src, cy):
    isin = src["pick"]["zero"]
    row = first(cy, (cy["master_isin"] == isin) & (cy["requirement_id"] ==
                                                   "S19")
                & (cy["original_value"] == "0"))
    j = get(client, f"/api/companies/{cid(isin)}/years/{row['fy_slot']}/posh")
    reqs = {g["requirement_id"] for g in j["metrics"]} | {
        m["requirement_id"] for m in j["missing"]}
    assert reqs == {"S17", "S18", "S19", "S20"}
    v = next(v for v in all_values(j) if v["requirement_id"] == "S19")
    assert v["disclosure_status"] == "REPORTED_ZERO"
    assert v["value"] is not None and Decimal(v["value"]) == 0


def test_missing_metric_is_null_never_zero(client, src, cy):
    for isin in src["isins"]:
        years = get(client, f"/api/companies/{cid(isin)}/years")["years"]
        for y in years:
            j = get(client, f"/api/companies/{cid(isin)}/years/"
                            f"{y['fiscal_year']}/metrics")
            have = set(expected(cy, isin, y["fiscal_year"],
                                set(cy["requirement_id"]))["requirement_id"])
            assert {g["requirement_id"] for g in j["metrics"]} == have
            for m in j["missing"]:
                assert m["value"] is None and m["value_numeric"] is None
                assert m["requirement_id"] not in have
                assert m["disclosure_status"] in {
                    "NO_FILING_FOR_YEAR", "EXTRACTION_ERROR",
                    "NOT_IN_BRSR_XBRL", "NOT_APPLICABLE",
                    "NO_VALUE_IN_FILING"}
            assert j["summary"]["metrics_with_values"] + \
                j["summary"]["metrics_missing"] == \
                j["summary"]["metrics_requested"]


def test_not_applicable_from_the_filing(client, src):
    na = src["na_fact"]
    isin = src["pick"]["not_applicable"]
    j = get(client, f"/api/companies/{cid(isin)}/years/{na['fy_slot']}/"
                    "metrics")
    rmap = read("data/reference/brsr_requirement_concept_map.csv")
    governed = set(rmap.loc[(rmap["applicability_concept"] == na["concept"])
                            & (rmap["source"] == "BRSR_XBRL"),
                            "requirement_id"])
    missing = {m["requirement_id"]: m for m in j["missing"]}
    expected_na = governed & set(missing)
    assert expected_na, "no governed metric without a value"
    for rid in expected_na:
        assert missing[rid]["disclosure_status"] == "NOT_APPLICABLE", rid
        assert na["concept"] in missing[rid]["missing_reason"]
    # metrics not governed by that concept are never marked not applicable
    assert {r for r, m in missing.items()
            if m["disclosure_status"] == "NOT_APPLICABLE"} == expected_na


def test_external_metrics_are_labelled_not_in_brsr_xbrl(client, src):
    rmap = read("data/reference/brsr_requirement_concept_map.csv")
    external = set(rmap.loc[rmap["source"] != "BRSR_XBRL", "requirement_id"])
    isin = src["pick"]["zero"]
    fy = get(client, f"/api/companies/{cid(isin)}/years")["years"][-1][
        "fiscal_year"]
    j = get(client, f"/api/companies/{cid(isin)}/years/{fy}/metrics")
    labelled = {m["requirement_id"] for m in j["missing"]
                if m["disclosure_status"] == "NOT_IN_BRSR_XBRL"}
    assert labelled == external


def test_candidate_crosswalk_values_are_flagged(client, src, cy):
    isin = src["pick"]["candidate"]
    row = first(cy, (cy["master_isin"] == isin)
                & (cy["crosswalk_status"] == "CANDIDATE_REVIEW"))
    j = get(client, f"/api/companies/{cid(isin)}/years/{row['fy_slot']}/"
                    f"metrics?metric={row['requirement_id']}")
    v = next(v for v in all_values(j) if v["concept"] == row["metric_name"])
    assert v["disclosure_status"] == "REPORTED_PENDING_CROSSWALK_REVIEW"


@pytest.mark.parametrize("pillar", ["environmental", "social",
                                    "governance"])
def test_pillar_endpoints(client, src, pillar):
    isin = src["pick"]["zero"]
    fy = get(client, f"/api/companies/{cid(isin)}/years")["years"][-1][
        "fiscal_year"]
    j = get(client, f"/api/companies/{cid(isin)}/years/{fy}/pillars/"
                    f"{pillar}")
    pillars = {g["pillar"] for g in j["metrics"]} | {
        m["pillar"] for m in j["missing"]}
    assert pillars == {service.PILLARS[pillar]}


# ---- traceability -------------------------------------------------------------------

def test_every_value_is_traceable(client, src, cy):
    man = read(MANIFEST).set_index("filing_id")
    isin = src["pick"]["converted"]
    years = get(client, f"/api/companies/{cid(isin)}/years")["years"]
    fy = [y for y in years if y["status"] == "PROCESSED"][-1]["fiscal_year"]
    j = get(client, f"/api/companies/{cid(isin)}/years/{fy}/metrics")
    assert all_values(j)
    for v in all_values(j):
        s = v["source"]
        assert s["filing_id"] and s["xbrl_url"] and s["context_reference"]
        assert s["source_file"].endswith(".xml")
        assert man.loc[s["filing_id"], "xbrl_url"] == s["xbrl_url"]
        assert man.loc[s["filing_id"], "fy_slot"] == fy


# ---- history and comparison ---------------------------------------------------------

def test_history_across_years(client, src, cy):
    isin = src["pick"]["converted"]
    j = get(client, f"/api/companies/{cid(isin)}/metrics/E07/history")
    years = [y["fiscal_year"] for y in get(
        client, f"/api/companies/{cid(isin)}/years")["years"]]
    assert [s["fiscal_year"] for s in j["series"]] == years
    exp = cy[(cy["master_isin"] == isin) & (cy["requirement_id"] == "E07")
             & (cy["breakdown"] == "")]
    for s in j["series"]:
        e = exp[exp["fy_slot"] == s["fiscal_year"]]
        assert len(s["values"]) == len(e)
        if not len(e):
            assert s["missing"]["value"] is None
    assert j["comparable"] == (len(j["units"]) <= 1)


def test_history_with_breakdown(client, src, cy):
    isin = src["pick"]["zero"]
    row = first(cy, (cy["master_isin"] == isin)
                & (cy["requirement_id"] == "S01"))
    r = client.get(f"/api/companies/{cid(isin)}/metrics/S01/history",
                   params={"breakdown": row["breakdown"]})
    assert r.status_code == 200
    j = r.json()
    vals = [v for s in j["series"] for v in s["values"]]
    assert vals and all(v["breakdown"] == row["breakdown"] for v in vals)


def test_compare_companies(client, src, cy):
    isins = sorted(src["isins"])
    fy = cy["fy_slot"].max()
    r = client.get("/api/compare", params={
        "metric": "E07", "fy": fy, "companies": [cid(i) for i in isins]})
    assert r.status_code == 200
    j = r.json()
    assert [c["company"]["isin"] for c in j["companies"]] == isins
    for c in j["companies"]:
        e = cy[(cy["master_isin"] == c["company"]["isin"])
               & (cy["fy_slot"] == fy) & (cy["requirement_id"] == "E07")
               & (cy["breakdown"] == "")]
        got = [v for y in c["years"] for v in y["values"]]
        assert len(got) == len(e)
        if not len(e):
            assert c["years"][0]["missing"]["value"] is None
    assert j["comparable"] == (len(j["units"]) <= 1)


def test_compare_accepts_comma_lists(client, src):
    isins = sorted(src["isins"])[:2]
    j = get(client, f"/api/compare?metric=S19&companies="
                    f"{cid(isins[0])},{cid(isins[1])}")
    assert len(j["companies"]) == 2


# ---- data quality ----------------------------------------------------------------------

def test_data_quality_counts_reconcile(client, src):
    isin = src["pick"]["ambiguous"]
    years = get(client, f"/api/companies/{cid(isin)}/years")["years"]
    fy = [y for y in years if y["status"] == "PROCESSED"][0]["fiscal_year"]
    dq = get(client, f"/api/companies/{cid(isin)}/years/{fy}/data-quality")
    full = get(client, f"/api/companies/{cid(isin)}/years/{fy}/metrics")
    assert sum(dq["value_basis"].values()) == len(all_values(full))
    assert sum(dq["missing_metrics"].values()) == len(full["missing"])
    assert len(dq["not_normalised_values"]) == \
        dq["value_basis"].get("REPORTED_NOT_NORMALISED", 0)
    queue = read(f"{N}/review_workflow/review_queue.csv")
    q = queue[(queue["master_isin"] == isin) & (queue["fy_slot"] == fy)]
    assert sum(sum(s.values()) for s in
               dq["review_recommendations"].values()) == len(q)


# ---- safety ------------------------------------------------------------------------------

def test_production_database_is_refused_as_analytics_source():
    with pytest.raises(adb.AnalyticsDatabaseRefused):
        adb.analytics_database(dbc.production_database())


def test_sessions_are_read_only(client):
    import psycopg2
    with adb.cursor(TEST_DB) as cur:
        with pytest.raises(psycopg2.Error):
            cur.execute("DELETE FROM brsr.company")


# ---- publication safeguards (Stage 2D) ----------------------------------------
PUBLISHED = {"PUBLISHED", "PUBLISHED_APPROVED_DECISION"}


def company_values(client, isin, fy):
    return all_values(get(client, f"/api/companies/{cid(isin)}/years/{fy}"
                                  "/metrics?include_missing=false"))


def test_scale_review_value_is_shown_as_filed_and_not_comparable(client, src,
                                                                  cy):
    row = first(cy, (cy["master_isin"] == src["pick"]["scale"])
                & (cy["scale_decision_id"] != "")
                & (cy["scale_decision_outcome"] != "APPROVED_APPLIED"))
    vals = [v for v in company_values(client, row["master_isin"],
                                      row["fy_slot"])
            if v["requirement_id"] == row["requirement_id"]
            and (v.get("scale_review") or {}).get("record_id")
            == row["scale_decision_id"]]
    assert vals, row["scale_decision_id"]
    v = vals[0]
    assert v["publication_status"] == "PENDING_SCALE_REVIEW"
    assert v["comparable"] is False
    assert v["value"] is None and v["value_numeric"] is None
    assert v["reported_value"] == row["original_value"]    # filed, unchanged
    assert v["scale_review"]["note"] == "proposal only; not applied"
    assert v["scale_review"]["proposed_value"] == (
        row["scale_proposed_value"] or None)
    if row["normalised_value"]:
        assert Decimal(v["withheld"]["value"]) == Decimal(
            row["normalised_value"])


def test_deferred_issue_values_are_flagged_never_comparable(client, src, cy):
    rows = cy[(cy["master_isin"] == src["pick"]["deferred"])
              & (cy["deferred_issue_ids"] != "")]
    fy = rows["fy_slot"].iloc[0]
    vals = [v for v in company_values(client, src["pick"]["deferred"], fy)
            if v["review"]["deferred_issue_ids"]]
    assert len(vals) == len(rows[rows["fy_slot"] == fy])
    for v in vals:
        assert v["publication_status"] == "DEFERRED_DATA_QUALITY"
        assert v["comparable"] is False
        assert v["withheld"] is None          # default policy FLAG: shown


def test_deferred_withhold_policy(client, src, cy, monkeypatch):
    monkeypatch.setenv("BRSR_DEFERRED_TREATMENT", "WITHHOLD")
    fy = cy[(cy["master_isin"] == src["pick"]["deferred"])
            & (cy["deferred_issue_ids"] != "")]["fy_slot"].iloc[0]
    vals = [v for v in company_values(client, src["pick"]["deferred"], fy)
            if v["review"]["deferred_issue_ids"]]
    assert vals
    for v in vals:
        assert v["value"] is None and v["reported_value"] is None
        assert "deferred data-quality issue" in v["withheld"]["reason"]
        assert v["comparable"] is False


def test_default_policy_is_reported_as_not_owner_approved(client, src,
                                                          monkeypatch):
    monkeypatch.delenv("BRSR_DEFERRED_TREATMENT", raising=False)
    d = get(client, f"/api/companies/{cid(src['pick']['converted'])}"
                    "/metrics/E07/history")
    assert d["publication_policy"]["deferred_issue_treatment"] == "FLAG"
    assert d["publication_policy"]["owner_approved"] is False


def test_only_published_values_are_comparable(client, src):
    seen = 0
    for isin in src["isins"]:
        years = get(client, f"/api/companies/{cid(isin)}/years")["years"]
        for y in years:
            r = client.get(f"/api/companies/{cid(isin)}/years/"
                           f"{y['fiscal_year']}/metrics?include_missing=false")
            if r.status_code != 200:
                continue
            for v in all_values(r.json()):
                seen += 1
                assert v["publication_status"] in service.publication.STATUSES
                if v["comparable"]:
                    assert v["publication_status"] in PUBLISHED
                    assert v["value_numeric"] is not None
                    assert not v["review"]["deferred_issue_ids"]
                if v["publication_status"] == "PENDING_CROSSWALK_REVIEW":
                    assert not v["comparable"]
    assert seen


def test_compare_counts_only_comparable_values(client, src, cy):
    row = first(cy, (cy["master_isin"] == src["pick"]["scale"])
                & (cy["scale_decision_id"] != "")
                & (cy["scale_decision_outcome"] != "APPROVED_APPLIED"))
    keys = ",".join(cid(i) for i in src["isins"])
    d = get(client, f"/api/compare?metric={row['requirement_id']}"
                    f"&fy={row['fy_slot']}&companies={keys}")
    vals = [v for c in d["companies"] for y in c["years"] for v in y["values"]]
    comparable = [v for v in vals if v["comparable"]]
    assert d["eligible_values"] == len(comparable)
    assert set(d["units"]) == {v["unit"] for v in comparable}
    assert any(v["publication_status"] == "PENDING_SCALE_REVIEW"
               for v in vals)


def test_data_quality_reports_publication_status(client, src):
    isin = src["pick"]["deferred"]
    fy = get(client, f"/api/companies/{cid(isin)}/years")["years"][0][
        "fiscal_year"]
    d = get(client, f"/api/companies/{cid(isin)}/years/{fy}/data-quality")
    assert "publication_status" in d and "publication_policy" in d
