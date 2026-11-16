"""
Database connections with a production guard (Phase 14).

The configured database (DB_NAME, i.e. brsr_db) is the production database.
In Phase 14 no code path may write to it: write connections to it are
refused here, before any connection is opened. The guard cannot be
switched off by configuration; lifting it is a code change that needs the
owner's approval (Phase 14 sign-off, then the production import).

Test databases must carry the prefix TEST_DB_PREFIX, so a mistyped name
can never point the tools at another database.
"""

import os

import psycopg2

from app.config import settings  # noqa: F401  (loads .env)

TEST_DB_PREFIX = "brsr_phase14_"
PRODUCTION_WRITES_ENABLED = False      # Phase 14: no production writes


class ProductionWriteBlocked(RuntimeError):
    pass


class NotATestDatabase(RuntimeError):
    pass


def production_database():
    return os.getenv("DB_NAME", "")


def check_target(dbname, write=True):
    if not dbname:
        raise ValueError("a target database name is required")
    if dbname == production_database() and write and \
            not PRODUCTION_WRITES_ENABLED:
        raise ProductionWriteBlocked(
            f"writes to the production database '{dbname}' are blocked in "
            "Phase 14 (owner approval required)")
    if write and not dbname.startswith(TEST_DB_PREFIX):
        raise NotATestDatabase(
            f"'{dbname}' is not a Phase 14 test database "
            f"(expected prefix {TEST_DB_PREFIX})")


def connect(dbname, write=True, autocommit=False):
    check_target(dbname, write)
    conn = psycopg2.connect(host=os.getenv("DB_HOST"),
                            port=os.getenv("DB_PORT"), dbname=dbname,
                            user=os.getenv("DB_USER"),
                            password=os.getenv("DB_PASSWORD"))
    conn.autocommit = autocommit
    if not write:
        conn.set_session(readonly=True)
    return conn


def _maintenance():
    conn = psycopg2.connect(host=os.getenv("DB_HOST"),
                            port=os.getenv("DB_PORT"), dbname="postgres",
                            user=os.getenv("DB_USER"),
                            password=os.getenv("DB_PASSWORD"))
    conn.autocommit = True
    return conn


def create_test_database(dbname):
    """CREATE DATABASE for a prefixed test database (no-op if present)."""
    check_target(dbname, write=True)
    conn = _maintenance()
    try:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,))
        if cur.fetchone() is None:
            cur.execute(f'CREATE DATABASE "{dbname}"')
            return True
        return False
    finally:
        conn.close()


def drop_test_database(dbname):
    """DROP DATABASE - only prefixed test databases, never production."""
    check_target(dbname, write=True)
    conn = _maintenance()
    try:
        conn.cursor().execute(f'DROP DATABASE IF EXISTS "{dbname}" '
                              "WITH (FORCE)")
    finally:
        conn.close()


def production_fingerprint():
    """Read-only fingerprint of the production database (tables, counts,
    content hashes) to prove it was not modified."""
    conn = connect(production_database(), write=False)
    try:
        cur = conn.cursor()
        cur.execute("""SELECT table_schema, table_name FROM
                       information_schema.tables WHERE table_schema NOT IN
                       ('pg_catalog', 'information_schema') ORDER BY 1, 2""")
        out = {}
        for schema, table in cur.fetchall():
            cur.execute(f'SELECT count(*), md5(string_agg(md5(t::text), '
                        f"'' ORDER BY md5(t::text))) FROM "
                        f'"{schema}"."{table}" t')
            out[f"{schema}.{table}"] = cur.fetchone()
        cur.execute("SELECT nspname FROM pg_namespace WHERE nspname NOT LIKE "
                    "'pg_%' AND nspname <> 'information_schema' ORDER BY 1")
        out["__schemas__"] = tuple(r[0] for r in cur.fetchall())
        return out
    finally:
        conn.close()
