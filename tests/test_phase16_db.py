"""
Phase 16: database tests from master prompt section 18 that Phase 14 did
not cover explicitly: foreign-key integrity enforced by PostgreSQL itself,
historical (superseded) filing preservation, and reprocessing without
duplicate records.

Everything runs in an isolated test database (brsr_phase14_pytest16),
created and dropped by the fixture. The production database is only read
(fingerprint), and the staging sources are small copies in tmp_path.
"""

import pandas as pd
import psycopg2
import pytest

from app.db import connection as dbc
from app.db import migrate
from app.db import staging_loader as sl
from tests.conftest import PROJECT_ROOT
from tests.test_db_phase14 import pg_available, scalar, write_csv

TEST_DB = "brsr_phase14_pytest16"
TABLES = ["company", "taxonomy_version", "filing"]
MANIFEST = "data/processed/registry/filing_manifest.csv"
MASTER = "data/processed/registry/company_master.csv"

pytestmark = pytest.mark.skipif(not pg_available(),
                                reason="PostgreSQL not reachable")


@pytest.fixture(scope="module")
def production_before():
    return dbc.production_fingerprint()


@pytest.fixture()
def migrated(production_before):
    dbc.check_target(TEST_DB)                 # test prefix only; no raise
    dbc.drop_test_database(TEST_DB)
    dbc.create_test_database(TEST_DB)
    conn = dbc.connect(TEST_DB, write=True)
    migrate.up(conn)
    yield conn
    conn.close()
    dbc.drop_test_database(TEST_DB)
    assert dbc.production_fingerprint() == production_before


@pytest.fixture()
def two_versions(tmp_path):
    """Sources for one company whose FY slot has an original and a later
    filing; the original is superseded (is_latest_valid False)."""
    reg = pd.read_csv(PROJECT_ROOT / MANIFEST, dtype=str,
                      keep_default_na=False)
    pairs = reg[reg["versions_in_slot"] == "2"].groupby(
        ["company_id", "fy_slot"]).filter(lambda g: len(g) == 2)
    company, slot = pairs.iloc[0][["company_id", "fy_slot"]]
    filings = pairs[(pairs["company_id"] == company)
                    & (pairs["fy_slot"] == slot)]
    assert set(filings["is_latest_valid"]) == {"True", "False"}
    master = pd.read_csv(PROJECT_ROOT / MASTER, dtype=str,
                         keep_default_na=False)
    for name in TABLES:
        for src in sl.BY_NAME[name]["sources"]:
            df = pd.read_csv(PROJECT_ROOT / src, dtype=str,
                             keep_default_na=False)
            if src == MANIFEST:
                df = filings
            elif src == MASTER:
                df = master[master["company_id"] == company]
            write_csv(tmp_path / src, df)
    return tmp_path, filings


def run(root):
    batch, summary = sl.stage(TEST_DB, TABLES, root=root)
    assert all(s["rows_rejected"] == 0 for s in summary), summary
    return {r["table"]: r for r in sl.promote(TEST_DB, batch)}


def test_foreign_keys_are_enforced_by_the_database(migrated, two_versions):
    root, _ = two_versions
    run(root)
    cur = migrated.cursor()
    cur.execute("CREATE TEMP TABLE f AS SELECT * FROM brsr.filing LIMIT 1")
    cur.execute("UPDATE f SET filing_id = 'F-ORPHAN', "
                "company_id = 'C-NOT-REGISTERED'")
    with pytest.raises(psycopg2.errors.ForeignKeyViolation):
        cur.execute("INSERT INTO brsr.filing SELECT * FROM f")
    migrated.rollback()
    # a referenced company cannot be removed from under its filings
    with pytest.raises(psycopg2.errors.ForeignKeyViolation):
        cur.execute("DELETE FROM brsr.company")
    migrated.rollback()
    assert scalar(migrated, "SELECT count(*) FROM brsr.filing f LEFT JOIN "
                            "brsr.company c USING (company_id) WHERE "
                            "c.company_id IS NULL") == 0


def test_historical_filing_versions_are_preserved(migrated, two_versions):
    root, filings = two_versions
    first = run(root)
    assert first["filing"]["inserted"] == 2
    cur = migrated.cursor()
    cur.execute("SELECT filing_id, is_latest_valid FROM brsr.filing")
    stored = dict(cur.fetchall())
    expected = dict(zip(filings["filing_id"],
                        filings["is_latest_valid"] == "True"))
    assert {k: str(v).lower() in ("true", "t") for k, v in stored.items()} \
        == expected
    # a later source without the superseded version never deletes it
    latest = filings[filings["is_latest_valid"] == "True"]
    write_csv(root / MANIFEST, latest)
    second = run(root)
    assert second["filing"]["rows_not_in_batch"] == 1
    assert second["filing"]["inserted"] == 0
    old = filings.loc[filings["is_latest_valid"] == "False",
                      "filing_id"].item()
    assert scalar(migrated, "SELECT count(*) FROM brsr.filing WHERE "
                            "filing_id = %s", (old,)) == 1


def test_reprocessing_creates_no_duplicates(migrated, two_versions):
    root, _ = two_versions
    run(root)

    def counts():
        return {t: scalar(migrated, f"SELECT count(*) FROM brsr.{t}")
                for t in TABLES}
    before = counts()
    for _ in range(2):
        again = run(root)
        assert all(r["inserted"] == 0 and r["updated"] == 0
                   for r in again.values())
    assert counts() == before
    assert scalar(migrated, "SELECT count(*) - count(DISTINCT filing_id) "
                            "FROM brsr.filing") == 0
    # every staging batch is recorded; core rows are not multiplied
    assert scalar(migrated, "SELECT count(*) FROM brsr.import_batch") >= 3


def test_production_target_is_refused_for_writes(production_before):
    prod = dbc.production_database()
    assert prod and not prod.startswith(dbc.TEST_DB_PREFIX)
    with pytest.raises(dbc.ProductionWriteBlocked):
        dbc.check_target(prod)
    with pytest.raises(dbc.ProductionWriteBlocked):
        sl.stage(prod, TABLES)            # refused before any statement
    with pytest.raises(dbc.NotATestDatabase):
        dbc.check_target("some_other_db")
    dbc.check_target(prod, write=False)    # read-only access is allowed
    assert dbc.production_fingerprint() == production_before
