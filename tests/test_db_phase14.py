"""
Phase 14: schema, migrations and staging loader.

Database tests run in a dedicated database (brsr_phase14_pytest) that the
fixture creates and drops. The production database is only ever read
(fingerprint). Tests are skipped when PostgreSQL is not reachable.
"""

import csv
import hashlib
import json
import shutil
from pathlib import Path

import pandas as pd
import psycopg2
import pytest

from app.db import connection as dbc
from app.db import migrate, sqlgen
from app.db import staging_loader as sl
from app.db.schema_spec import TABLES
from tests.conftest import PROJECT_ROOT, changed_since

TEST_DB = "brsr_phase14_pytest"
SNAPSHOT = PROJECT_ROOT / "data/processed/nifty100/backups/20261112_pre_phase14"


def pg_available():
    try:
        dbc.connect(dbc.production_database(), write=False).close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not pg_available(),
                                reason="PostgreSQL not reachable")


@pytest.fixture()
def db():
    dbc.drop_test_database(TEST_DB)
    dbc.create_test_database(TEST_DB)
    conn = dbc.connect(TEST_DB, write=True)
    yield conn
    conn.close()
    dbc.drop_test_database(TEST_DB)


@pytest.fixture()
def migrated(db):
    migrate.up(db)
    return db


def scalar(conn, sql, params=None):
    cur = conn.cursor()
    cur.execute(sql, params)
    return cur.fetchone()[0]


# ---- guards ----------------------------------------------------------------

@pytest.mark.parametrize("name,error", [
    (None, dbc.ProductionWriteBlocked), ("postgres", dbc.NotATestDatabase),
    ("brsr", dbc.NotATestDatabase), ("", ValueError)])
def test_write_guard(name, error, monkeypatch):
    name = dbc.production_database() if name is None else name
    monkeypatch.setattr(psycopg2, "connect",
                        lambda **k: pytest.fail("must not connect"))
    with pytest.raises(error):
        dbc.connect(name, write=True)
    with pytest.raises(error):
        dbc.drop_test_database(name)


def test_promote_refuses_production(monkeypatch):
    monkeypatch.setattr(psycopg2, "connect",
                        lambda **k: pytest.fail("must not connect"))
    with pytest.raises((dbc.ProductionWriteBlocked, RuntimeError)):
        sl.promote(dbc.production_database(), 1)


def test_production_database_unchanged():
    before = json.loads((SNAPSHOT / "production_db_fingerprint.json")
                        .read_text())
    now = {k: list(v) for k, v in dbc.production_fingerprint().items()}
    assert now == before


# ---- migrations --------------------------------------------------------------

def test_committed_migrations_match_spec():
    for version, name, up, down in sqlgen.migrations():
        base = sqlgen.MIGRATIONS_DIR / f"{version}_{name}"
        assert Path(f"{base}.up.sql").read_text(encoding="utf-8") == up
        assert Path(f"{base}.down.sql").read_text(encoding="utf-8") == down


def test_up_creates_schema_and_is_idempotent(db):
    assert migrate.up(db) == ["0001", "0002", "0003", "0004", "0005"]
    assert migrate.up(db) == []
    assert migrate.verify(db) == []
    n_core = scalar(db, "SELECT count(*) FROM information_schema.tables "
                        "WHERE table_schema = 'brsr'")
    n_stg = scalar(db, "SELECT count(*) FROM information_schema.tables "
                       "WHERE table_schema = 'brsr_staging'")
    assert n_core == len(TABLES) + 4 and n_stg == len(TABLES)
    fks = scalar(db, "SELECT count(*) FROM pg_constraint c JOIN pg_namespace "
                     "n ON n.oid = c.connamespace WHERE n.nspname = 'brsr' "
                     "AND c.contype = 'f'")
    expected_fks = sum(len(t["fks"]) for t in TABLES) + len(TABLES) + 3
    assert fks == expected_fks
    checks = scalar(db, "SELECT count(*) FROM pg_constraint c JOIN "
                        "pg_namespace n ON n.oid = c.connamespace WHERE "
                        "n.nspname = 'brsr' AND c.conname LIKE '%_allowed'")
    assert checks == sum(len(t["checks"]) for t in TABLES)
    # the existing public schema is untouched by migrations
    assert scalar(db, "SELECT count(*) FROM information_schema.tables WHERE "
                      "table_schema = 'public'") == 0


def test_tampered_migration_is_refused(db, tmp_path):
    copy = tmp_path / "m"
    shutil.copytree(sqlgen.MIGRATIONS_DIR, copy)
    migrate.up(db, directory=copy)
    p = copy / "0002_registry.up.sql"
    p.write_text(p.read_text(encoding="utf-8") + "\n-- edited\n",
                 encoding="utf-8")
    assert migrate.verify(db, copy)
    with pytest.raises(migrate.MigrationError):
        migrate.up(db, directory=copy)


def test_failed_migration_rolls_back(db, tmp_path):
    copy = tmp_path / "m"
    shutil.copytree(sqlgen.MIGRATIONS_DIR, copy)
    (copy / "0006_broken.up.sql").write_text(
        "CREATE TABLE brsr.half_done (x int);\nSELECT * FROM no_such_table;",
        encoding="utf-8")
    (copy / "0006_broken.down.sql").write_text("SELECT 1;", encoding="utf-8")
    with pytest.raises(migrate.MigrationError):
        migrate.up(db, directory=copy)
    assert [s["version"] for s in migrate.status(db, copy)
            if s["applied"]] == ["0001", "0002", "0003", "0004", "0005"]
    assert scalar(db, "SELECT to_regclass('brsr.half_done') IS NULL")


def test_down_and_up_again(migrated):
    assert migrate.down(migrated, steps=1) == ["0005"]
    assert scalar(migrated, "SELECT to_regclass('brsr.assurance_fact') IS "
                            "NULL")
    assert migrate.up(migrated) == ["0005"]
    assert migrate.down(migrated, steps=5) == ["0005", "0004", "0003",
                                               "0002", "0001"]
    assert scalar(migrated, "SELECT count(*) FROM pg_namespace WHERE nspname "
                            "IN ('brsr', 'brsr_staging')") == 0
    assert migrate.up(migrated) == ["0001", "0002", "0003", "0004", "0005"]


# ---- staging loader on synthetic sources -----------------------------------------

SUBSET = ["company", "taxonomy_version", "filing", "requirement_value",
          "conversion_audit", "review_decision_log"]


def write_csv(path, df):
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")


@pytest.fixture()
def sources(tmp_path):
    """Small copies of the real outputs plus deliberately bad rows."""
    by = sl.BY_NAME
    out = {}
    for name in SUBSET:
        for src in by[name]["sources"]:
            df = pd.read_csv(PROJECT_ROOT / src, dtype=str,
                             keep_default_na=False)
            out[src] = df
    reg = out["data/processed/registry/filing_manifest.csv"]
    vals_cy = "data/processed/nifty100/normalised/" \
              "brsr_requirement_values_normalised.csv"
    vals_py = "data/processed/nifty100/normalised/" \
              "brsr_requirement_values_py_normalised.csv"
    filings = reg[reg["local_file_path"] != ""].head(3)
    files = set(filings["local_file_path"].str.replace("\\", "/")
                .str.split("/").str[-1])
    out["data/processed/registry/filing_manifest.csv"] = filings
    master = out["data/processed/registry/company_master.csv"]
    out["data/processed/registry/company_master.csv"] = master[
        master["company_id"].isin(filings["company_id"])]
    cy = out[vals_cy]
    cy = cy[cy["source_file"].isin(files)].head(20).copy()
    bad = cy.head(5).copy()
    bad["metric_name"] = bad["metric_name"] + "_X"          # new keys
    bad.iloc[0, bad.columns.get_loc("period_role")] = "XY"   # CHECK
    bad.iloc[1, bad.columns.get_loc("normalised_value")] = "1,234"  # numeric
    bad.iloc[2, bad.columns.get_loc("source_file")] = "unknown.xml"  # FK
    out[vals_cy] = pd.concat([cy, bad.iloc[:3], cy.head(1)])  # + duplicate
    out[vals_py] = out[vals_py].head(0)
    f = out["data/processed/registry/filing_manifest.csv"].copy()
    extra = f.head(1).copy()
    extra["filing_id"] = "F-BAD-DATE"
    extra["reporting_period_start"] = "31/03/2025"
    orphan = f.head(1).copy()
    orphan["filing_id"] = "F-ORPHAN"
    orphan["company_id"] = "C-NOT-REGISTERED"
    out["data/processed/registry/filing_manifest.csv"] = pd.concat(
        [f, extra, orphan])
    out["data/reference/review_decisions.csv"] = pd.DataFrame(
        [["1", "FSE-001", "DEFER", "tester", "2026-11-12T10:00:00",
          "AUD-T", "test only"]],
        columns=["entry_no", "recommendation_id", "decision", "reviewer",
                 "decided_at", "audit_reference", "comment"])
    for src, df in out.items():
        write_csv(tmp_path / src, df)
    return tmp_path


def rejections(conn, batch):
    cur = conn.cursor()
    cur.execute("SELECT table_name, reason, count(*) FROM "
                "brsr.import_rejection WHERE batch_id = %s GROUP BY 1, 2",
                (batch,))
    return {(t, r.split(":")[0]): n for t, r, n in cur.fetchall()}


def test_stage_validates_and_reconciles(migrated, sources):
    batch, summary = sl.stage(TEST_DB, SUBSET, root=sources)
    rej = rejections(migrated, batch)
    assert rej[("filing", "INVALID_DATE")] == 1
    assert rej[("filing", "ORPHAN_FK")] == 1
    assert rej[("requirement_value", "CHECK_FAILED")] == 1
    assert rej[("requirement_value", "INVALID_NUMERIC")] == 1
    assert rej[("requirement_value", "UNRESOLVED_FILING")] == 1
    assert rej[("requirement_value", "DUPLICATE_KEY_IN_SOURCE")] == 1
    for s in summary:
        assert s["rows_read"] == s["rows_staged"] + s["rows_rejected"]
    assert scalar(migrated, "SELECT bool_and(passed) FROM "
                            "brsr.import_reconciliation WHERE batch_id = %s",
                  (batch,))
    # the rejected record is kept verbatim for review
    rec = scalar(migrated, "SELECT record FROM brsr.import_rejection WHERE "
                           "reason LIKE 'INVALID_NUMERIC%%'")
    assert rec["normalised_value"] == "1,234"


def test_schema_drift_fails_whole_batch(migrated, sources):
    p = sources / "data/processed/registry/company_master.csv"
    df = pd.read_csv(p, dtype=str, keep_default_na=False)
    df["new_column"] = "x"
    write_csv(p, df)
    with pytest.raises(sl.SchemaDrift):
        sl.stage(TEST_DB, SUBSET, root=sources)
    assert scalar(migrated, "SELECT status FROM brsr.import_batch ORDER BY "
                            "batch_id DESC LIMIT 1") == "FAILED"
    assert scalar(migrated, "SELECT count(*) FROM brsr_staging.company") == 0


def test_failure_rolls_back_everything(migrated, sources):
    with pytest.raises(RuntimeError):
        sl.stage(TEST_DB, SUBSET, root=sources, fail_after="filing")
    batch = scalar(migrated, "SELECT max(batch_id) FROM brsr.import_batch")
    assert scalar(migrated, "SELECT status FROM brsr.import_batch WHERE "
                            "batch_id = %s", (batch,)) == "FAILED"
    for table in ("company", "filing"):
        assert scalar(migrated, f"SELECT count(*) FROM brsr_staging.{table}"
                                " WHERE batch_id = %s", (batch,)) == 0
    assert scalar(migrated, "SELECT count(*) FROM brsr.import_rejection "
                            "WHERE batch_id = %s", (batch,)) == 0


def test_promote_idempotent_updates_and_never_deletes(migrated, sources):
    b1, _ = sl.stage(TEST_DB, SUBSET, root=sources)
    first = {r["table"]: r for r in sl.promote(TEST_DB, b1)}
    assert first["requirement_value"]["inserted"] > 0
    b2, _ = sl.stage(TEST_DB, SUBSET, root=sources)
    second = {r["table"]: r for r in sl.promote(TEST_DB, b2)}
    assert all(r["inserted"] == 0 and r["updated"] == 0
               for r in second.values())
    # change one value and drop one row from the source
    src = sources / ("data/processed/nifty100/normalised/"
                     "brsr_requirement_values_normalised.csv")
    df = pd.read_csv(src, dtype=str, keep_default_na=False)
    df.iloc[0, df.columns.get_loc("conversion_note")] = "edited note"
    write_csv(src, df.drop(index=1))            # row 1 is a valid row
    b3, _ = sl.stage(TEST_DB, SUBSET, root=sources)
    third = {r["table"]: r for r in sl.promote(TEST_DB, b3)}
    assert third["requirement_value"]["updated"] == 1
    assert third["requirement_value"]["rows_not_in_batch"] >= 1
    total = scalar(migrated, "SELECT count(*) FROM brsr.requirement_value")
    assert total == first["requirement_value"]["inserted"]


def test_missing_values_are_null_never_zero(migrated, sources):
    b, _ = sl.stage(TEST_DB, SUBSET, root=sources)
    sl.promote(TEST_DB, b)
    cur = migrated.cursor()
    cur.execute("SELECT count(*) FROM brsr.requirement_value WHERE "
                "normalised_value IS NULL AND normalised_value_num IS NOT "
                "NULL")
    assert cur.fetchone()[0] == 0
    cur.execute("SELECT count(*) FROM brsr_staging.requirement_value s JOIN "
                "brsr.requirement_value c USING (source_file, "
                "context_reference, requirement_id, breakdown, period_role, "
                "metric_name) WHERE s.batch_id = %s AND s.normalised_value = "
                "'' AND c.normalised_value_num = 0", (b,))
    assert cur.fetchone()[0] == 0
    # key columns keep '' (a valid key), non-key '' becomes NULL
    cur.execute("SELECT count(*) FROM brsr.requirement_value WHERE breakdown "
                "IS NULL")
    assert cur.fetchone()[0] == 0


def test_decision_log_is_append_only(migrated, sources):
    b, _ = sl.stage(TEST_DB, SUBSET, root=sources)
    sl.promote(TEST_DB, b)
    cur = migrated.cursor()
    with pytest.raises(psycopg2.Error):
        cur.execute("UPDATE brsr.review_decision_log SET decision = "
                    "'APPROVE'")
    migrated.rollback()
    with pytest.raises(psycopg2.Error):
        cur.execute("DELETE FROM brsr.review_decision_log")
    migrated.rollback()
    # a changed log entry in the source is not written over the old one
    log = sources / "data/reference/review_decisions.csv"
    df = pd.read_csv(log, dtype=str, keep_default_na=False)
    df.loc[0, "decision"] = "APPROVE"
    write_csv(log, df)
    b2, _ = sl.stage(TEST_DB, SUBSET, root=sources)
    with pytest.raises(RuntimeError):
        sl.promote(TEST_DB, b2)
    assert scalar(migrated, "SELECT decision FROM brsr.review_decision_log") \
        == "DEFER"


# ---- real outputs: decisions preserved exactly ------------------------------------

REAL = ["company", "company_identifier", "taxonomy_version", "filing",
        "concept_crosswalk", "requirement_concept_map", "unit_decision",
        "scale_review_record", "deferred_issue", "review_anomaly",
        "review_recommendation", "review_decision_log",
        "assurance_engagement", "assurance_provider", "assurance_indicator",
        "assurance_issue", "supplementary_document",
        "supplementary_candidate", "supplementary_coverage"]


def test_real_outputs_stage_and_keep_decisions(migrated):
    batch, summary = sl.stage(TEST_DB, REAL)
    assert all(s["rows_rejected"] == 0 for s in summary)
    sl.promote(TEST_DB, batch)
    q = lambda sql: dict(migrated.cursor().execute(sql) or [])
    cur = migrated.cursor()

    def grouped(sql):
        cur.execute(sql)
        return dict(cur.fetchall())

    assert grouped("SELECT decision, count(*) FROM brsr.unit_decision GROUP "
                   "BY 1") == {"PENDING_REVIEW": 474,
                               "APPROVED_METRIC_TONNE": 53,
                               "UNDETERMINED_MANUAL_REVIEW": 35,
                               "FLAGGED_RESTATEMENT_DIFFERS": 20,
                               "APPROVED_MEGATONNE": 2}
    assert scalar(migrated, "SELECT count(*) FROM brsr.scale_review_record "
                            "WHERE approval_decision IS NULL") == 39
    assert grouped("SELECT status, count(*) FROM brsr.deferred_issue GROUP "
                   "BY 1") == {"DEFERRED_PENDING_REVIEW": 39}
    assert scalar(migrated, "SELECT count(*) FROM brsr.concept_crosswalk "
                            "WHERE approval_status = 'CANDIDATE_REVIEW'") == 7
    assert grouped("SELECT engagement_type, count(*) FROM "
                   "brsr.assurance_engagement GROUP BY 1")["ASSESSMENT"] == 9
    rec = grouped("SELECT status, count(*) FROM brsr.review_recommendation "
                  "GROUP BY 1")
    assert "APPROVED" not in rec and rec["APPLIED"] == 55
    assert scalar(migrated, "SELECT count(*) FROM brsr.assurance_issue WHERE "
                            "issue_code LIKE 'AS02%%'") == 9
    providers = scalar(migrated, "SELECT count(DISTINCT provider_name) FROM "
                                 "brsr.assurance_provider")
    src = pd.read_csv(PROJECT_ROOT / "data/processed/nifty100/assurance/"
                      "assurance_providers.csv", dtype=str,
                      keep_default_na=False)
    assert providers == src.loc[src["provider_name"] != "",
                                "provider_name"].nunique()   # not merged


def test_no_output_file_changed():
    assert changed_since(SNAPSHOT / "all_files_before.sha") == []
