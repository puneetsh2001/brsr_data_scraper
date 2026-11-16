"""
Versioned SQL migrations (Phase 14).

Migrations are the committed files app/db/migrations/NNNN_name.up.sql and
NNNN_name.down.sql. Applied versions are recorded in
brsr_meta.schema_migrations with the SHA-256 of the up file; a changed
file of an applied version is refused (tamper / drift detection). Each
migration runs in its own transaction together with its bookkeeping row,
so a failure leaves the database at the previous version. Re-running
`up` is a no-op once everything is applied.

Usage (a target database is always explicit; production is refused):
    python -m app.db.migrate --database brsr_phase14_test create-test-db
    python -m app.db.migrate --database brsr_phase14_test up [--to 0003]
    python -m app.db.migrate --database brsr_phase14_test status
    python -m app.db.migrate --database brsr_phase14_test verify
    python -m app.db.migrate --database brsr_phase14_test down --steps 1
"""

import argparse
import getpass
import hashlib
import re
import time
from pathlib import Path

from app.db import connection as dbc

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
META = "brsr_meta"
BOOTSTRAP = f"""
CREATE SCHEMA IF NOT EXISTS {META};
CREATE TABLE IF NOT EXISTS {META}.schema_migrations (
    version text PRIMARY KEY,
    name text NOT NULL,
    checksum text NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now(),
    applied_by text NOT NULL,
    execution_ms integer NOT NULL
);
"""


class MigrationError(RuntimeError):
    pass


def available(directory=MIGRATIONS_DIR):
    """[(version, name, up_path, down_path, checksum)] in version order."""
    out = []
    for up in sorted(directory.glob("*.up.sql")):
        m = re.match(r"^(\d{4})_(.+)\.up\.sql$", up.name)
        if not m:
            raise MigrationError(f"bad migration file name {up.name}")
        down = up.with_name(up.name.replace(".up.sql", ".down.sql"))
        if not down.exists():
            raise MigrationError(f"missing down migration for {up.name}")
        out.append((m.group(1), m.group(2), up, down,
                    hashlib.sha256(up.read_bytes()).hexdigest()))
    versions = [v for v, *_ in out]
    if len(set(versions)) != len(versions):
        raise MigrationError("duplicate migration versions")
    return out


def applied(conn):
    cur = conn.cursor()
    cur.execute(BOOTSTRAP)
    conn.commit()
    cur.execute(f"SELECT version, name, checksum FROM {META}.schema_migrations"
                " ORDER BY version")
    return {v: (n, c) for v, n, c in cur.fetchall()}


def verify(conn, directory=MIGRATIONS_DIR):
    """Applied migrations must match the committed files exactly."""
    files = {v: c for v, _, _, _, c in available(directory)}
    problems = []
    for version, (name, checksum) in applied(conn).items():
        if version not in files:
            problems.append(f"{version} {name}: applied but file missing")
        elif files[version] != checksum:
            problems.append(f"{version} {name}: file changed after it was "
                            "applied")
    return problems


def up(conn, to=None, directory=MIGRATIONS_DIR):
    problems = verify(conn, directory)
    if problems:
        raise MigrationError("; ".join(problems))
    done = applied(conn)
    ran = []
    for version, name, path, _, checksum in available(directory):
        if version in done or (to and version > to):
            continue
        start = time.monotonic()
        cur = conn.cursor()
        try:
            cur.execute(path.read_text(encoding="utf-8"))
            cur.execute(f"INSERT INTO {META}.schema_migrations (version, name,"
                        " checksum, applied_by, execution_ms) VALUES "
                        "(%s, %s, %s, %s, %s)",
                        (version, name, checksum, getpass.getuser(),
                         int((time.monotonic() - start) * 1000)))
            conn.commit()
        except Exception as error:
            conn.rollback()
            raise MigrationError(f"{version} {name} failed and was rolled "
                                 f"back: {error}") from error
        ran.append(version)
    return ran


def down(conn, steps=1, directory=MIGRATIONS_DIR):
    problems = verify(conn, directory)
    if problems:
        raise MigrationError("; ".join(problems))
    files = {v: d for v, _, _, d, _ in available(directory)}
    done = sorted(applied(conn), reverse=True)[:steps]
    reverted = []
    for version in done:
        cur = conn.cursor()
        try:
            cur.execute(files[version].read_text(encoding="utf-8"))
            cur.execute(f"DELETE FROM {META}.schema_migrations WHERE "
                        "version = %s", (version,))
            conn.commit()
        except Exception as error:
            conn.rollback()
            raise MigrationError(f"rollback of {version} failed and was "
                                 f"undone: {error}") from error
        reverted.append(version)
    return reverted


def status(conn, directory=MIGRATIONS_DIR):
    done = applied(conn)
    return [{"version": v, "name": n, "applied": v in done,
             "checksum_ok": v not in done or done[v][1] == c}
            for v, n, _, _, c in available(directory)]


def main():
    p = argparse.ArgumentParser(description="BRSR schema migrations")
    p.add_argument("--database", required=True)
    p.add_argument("command", choices=["create-test-db", "up", "down",
                                       "status", "verify"])
    p.add_argument("--to")
    p.add_argument("--steps", type=int, default=1)
    a = p.parse_args()
    if a.command == "create-test-db":
        created = dbc.create_test_database(a.database)
        print("created" if created else "already exists", a.database)
        return
    conn = dbc.connect(a.database, write=True)
    try:
        if a.command == "up":
            print("applied:", up(conn, a.to) or "nothing (up to date)")
        elif a.command == "down":
            print("rolled back:", down(conn, a.steps))
        elif a.command == "verify":
            problems = verify(conn)
            print("OK" if not problems else "\n".join(problems))
        for s in status(conn):
            print(f"  {s['version']} {s['name']:32s} "
                  f"{'applied' if s['applied'] else 'pending':8s} "
                  f"{'' if s['checksum_ok'] else 'CHECKSUM MISMATCH'}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
