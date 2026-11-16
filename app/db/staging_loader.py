"""
Staging loader (Phase 14).

Flow per batch
  1. read the approved output CSVs listed in app.db.schema_spec (read only)
  2. validate every row: required columns (schema drift fails the table),
     key present, duplicate keys, typed values (numeric / date / integer /
     timestamp), allowed values (CHECK lists), foreign keys, derived keys
     (requirement_value.filing_id from the filing registry). Invalid rows
     are rejected with a reason - never repaired, never dropped silently.
  3. stage the valid rows into brsr_staging.<table> with COPY, exactly as
     text, with their source path, source row number and a SHA-256 row hash
  4. reconcile: rows read = staged + rejected, and every staged row's text
     re-hashed inside PostgreSQL equals the hash of the source row
  All of 1-4 runs in ONE transaction: any error rolls the whole batch back
  and the batch is recorded as FAILED.

  5. promote (separate command, test databases only in Phase 14): upsert
     staging -> brsr.<table> in one transaction. Re-running is idempotent
     (unchanged rows are not rewritten; nothing is deleted); key columns
     keep '' as a valid key, other empty strings become NULL (missing,
     never 0); typed copies are validated casts. Append-only tables never
     change existing rows.

Nothing here interprets or applies review decisions: statuses (pending,
deferred, candidate) are stored exactly as in the source files.

Usage
    python -m app.db.staging_loader --database brsr_phase14_test stage
    python -m app.db.staging_loader --database brsr_phase14_test stage \
        --tables company,filing
    python -m app.db.staging_loader --database brsr_phase14_test promote \
        --batch 3
    python -m app.db.staging_loader --database brsr_phase14_test report \
        --batch 3
"""

import argparse
import csv
import getpass
import hashlib
import io
import json
import re
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from app.config import settings
from app.core import deferred_issues
from app.db import connection as dbc
from app.db.schema_spec import TABLES
from app.db.sqlgen import CORE, STAGING, TYPED_SUFFIX, all_columns, q

BY_NAME = {t["name"]: t for t in TABLES}
ROOT = settings.project_root
SEP = "\x1f"
NUMERIC = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)$")
INTEGER = re.compile(r"^-?\d+$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
COPY_CHUNK = 50_000


class SchemaDrift(RuntimeError):
    pass


# ==========================================================
# validation
# ==========================================================

def shared_columns(source):
    """Columns of every table loaded from the same source file."""
    cols = set()
    for t in TABLES:
        if source in t["sources"]:
            cols |= set(t["columns"])
    return cols


def read_sources(t, root=ROOT):
    frames = []
    for src in t["sources"]:
        path = root / src
        df = pd.read_csv(path, dtype=str, keep_default_na=False)
        missing = [c for c in t["columns"] if c not in df.columns]
        if missing:
            raise SchemaDrift(f"{t['name']}: {src} lacks columns {missing}")
        extra = [c for c in df.columns if c not in shared_columns(src)]
        if extra:
            raise SchemaDrift(f"{t['name']}: {src} has columns not in the "
                              f"schema {extra} (a migration is needed)")
        df = df[t["columns"]].copy()
        df["__source_path"] = src
        df["__source_row_number"] = range(2, len(df) + 2)
        frames.append(df)
    return pd.concat(frames, ignore_index=True)


def valid_date(v):
    if not DATE.match(v):
        return False
    try:
        date.fromisoformat(v)
        return True
    except ValueError:
        return False


def valid_ts(v):
    try:
        datetime.fromisoformat(v)
        return True
    except ValueError:
        return False


TYPE_CHECKS = {"numeric": NUMERIC.match, "integer": INTEGER.match,
               "date": valid_date, "timestamptz": valid_ts,
               "numeric_lenient": None}


def validate(t, df, parents):
    """
    Returns (valid df, rejected list). parents: {table: set(key tuples)},
    plus '__filing_by_file' for the derived filing_id.
    """
    reason = pd.Series("", index=df.index)

    def reject(mask, why):
        nonlocal reason
        mask = mask & (reason == "")
        reason = reason.mask(mask, why)

    cols = t["columns"]
    nul = df[cols].apply(lambda s: s.str.contains("\x00", regex=False)).any(
        axis=1)
    reject(nul, "NUL_CHARACTER_NOT_STORABLE")
    reject(df[t["key"][0]] == "", f"REQUIRED_KEY_EMPTY:{t['key'][0]}")
    for c, kind in t["typed"].items():
        check = TYPE_CHECKS[kind]
        if check is None:
            continue
        bad = (df[c] != "") & ~df[c].map(lambda v: bool(check(v))).astype(bool)
        reject(bad, f"INVALID_{kind.upper()}:{c}")
    for c, allowed in t["checks"].items():
        reject((df[c] != "") & ~df[c].isin(allowed), f"CHECK_FAILED:{c}")
    if "filing_id" in t["derived"]:
        by_file = parents["__filing_by_file"]
        resolved = df["source_file"].map(lambda f: by_file.get(f, set()))
        df["filing_id"] = resolved.map(
            lambda s: next(iter(s)) if len(s) == 1 else "")
        reject(resolved.map(len) > 1, "AMBIGUOUS_FILING:source_file")
        reject(df["filing_id"] == "", "UNRESOLVED_FILING:source_file")
    key = set(t["key"])
    for fk_cols, ref, _ in t["fks"]:
        keys = parents.get(ref)
        if keys is None:
            raise RuntimeError(f"{t['name']}: parent {ref} not available")
        tup = list(zip(*[df[c] for c in fk_cols]))
        # an empty non-key FK column is NULL (no reference); key columns
        # are NOT NULL, so '' there is a real value and is checked
        nullable = [c not in key for c in fk_cols]
        present = pd.Series([not any(v == "" and n for v, n in
                                     zip(k, nullable)) for k in tup],
                            index=df.index)
        found = pd.Series([k in keys for k in tup], index=df.index)
        reject(present & ~found, f"ORPHAN_FK:{'+'.join(fk_cols)}->{ref}")
    dup = df.duplicated(t["key"], keep="first") & (reason == "")
    reject(dup, "DUPLICATE_KEY_IN_SOURCE")

    bad = df[reason != ""]
    rejected = [{"source_path": r["__source_path"],
                 "source_row_number": int(r["__source_row_number"]),
                 "reason": reason[i],
                 "record": {c: r[c] for c in cols}}
                for i, r in bad.iterrows()]
    return df[reason == ""].copy(), rejected


def row_hashes(df, columns):
    joined = df[columns].agg(SEP.join, axis=1) if len(df) else pd.Series(
        dtype=str)
    return joined.map(lambda s: hashlib.sha256(s.encode("utf-8"))
                      .hexdigest())


def file_sha(src, root=ROOT):
    return hashlib.sha256((root / src).read_bytes()).hexdigest()


# ==========================================================
# staging
# ==========================================================

def copy_rows(cur, table, df, columns):
    target = f"{STAGING}.{table}"
    col_sql = ", ".join(q(c) for c in columns)
    for start in range(0, len(df), COPY_CHUNK):
        buf = io.StringIO()
        df.iloc[start:start + COPY_CHUNK][columns].to_csv(
            buf, index=False, header=False, quoting=csv.QUOTE_ALL,
            lineterminator="\n")
        buf.seek(0)
        cur.copy_expert(f"COPY {target} ({col_sql}) FROM STDIN WITH "
                        "(FORMAT csv)", buf)


def hash_sql(alias, columns, coalesce=False):
    parts = ", ".join(
        f"coalesce({alias}.{q(c)}, '')" if coalesce else f"{alias}.{q(c)}"
        for c in columns)
    return (f"encode(sha256(convert_to(concat_ws(E'\\x1f', {parts}), "
            f"'UTF8')), 'hex')")


def record(cur, batch, table, stage, check, expected, actual, detail=""):
    passed = expected == actual
    cur.execute(f"INSERT INTO {CORE}.import_reconciliation (batch_id, "
                "table_name, stage, check_name, expected, actual, passed, "
                "detail) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (batch, table, stage, check, expected, actual, passed,
                 detail))
    return passed


def start_batch(dbname, mode, tables, notes=""):
    conn = dbc.connect(dbname, write=True, autocommit=True)
    try:
        cur = conn.cursor()
        cur.execute(f"INSERT INTO {CORE}.import_batch (status, mode, "
                    "target_database, initiated_by, tables, notes) VALUES "
                    "('RUNNING', %s, %s, %s, %s, %s) RETURNING batch_id",
                    (mode, dbname, getpass.getuser(), ",".join(tables),
                     notes))
        return cur.fetchone()[0]
    finally:
        conn.close()


def finish_batch(dbname, batch, status, notes=None):
    conn = dbc.connect(dbname, write=True, autocommit=True)
    try:
        conn.cursor().execute(
            f"UPDATE {CORE}.import_batch SET status = %s, finished_at = "
            "now(), notes = coalesce(%s, notes) WHERE batch_id = %s",
            (status, notes, batch))
    finally:
        conn.close()


def core_keys(cur, table):
    t = BY_NAME[table]
    cur.execute(f"SELECT {', '.join(q(c) for c in t['key'])} FROM "
                f"{CORE}.{table}")
    return {tuple(r) for r in cur.fetchall()}


def stage(dbname, tables=None, root=ROOT, keep_previous=False,
          fail_after=None):
    """Stage tables (default: all) in one transaction. Returns summary."""
    names = [t["name"] for t in TABLES if tables is None
             or t["name"] in tables]
    batch = start_batch(dbname, "STAGE", names)
    conn = dbc.connect(dbname, write=True)
    summary = []
    try:
        cur = conn.cursor()
        parents = {}
        for name in names:
            t = BY_NAME[name]
            df = read_sources(t, root)
            for _, ref, _ in t["fks"]:
                if ref not in parents:
                    parents[ref] = core_keys(cur, ref)
            if "filing_id" in t["derived"] and \
                    "__filing_by_file" not in parents:
                if "filing" not in parents:
                    parents["filing"] = core_keys(cur, "filing")
                parents["__filing_by_file"] = filing_by_file(
                    cur, root, names, parents["filing"])
            valid, rejected = validate(t, df, parents)
            columns = all_columns(t)
            valid["batch_id"] = str(batch)
            valid["source_path"] = valid["__source_path"]
            valid["source_row_number"] = valid["__source_row_number"].astype(
                str)
            valid["row_hash"] = row_hashes(valid, columns)
            if not keep_previous:
                cur.execute(f"DELETE FROM {STAGING}.{name} WHERE batch_id <> "
                            "%s", (batch,))
            copy_rows(cur, name, valid, columns + [
                "batch_id", "source_path", "source_row_number", "row_hash"])
            for r in rejected:
                cur.execute(f"INSERT INTO {CORE}.import_rejection (batch_id, "
                            "table_name, source_path, source_row_number, "
                            "reason, record) VALUES (%s, %s, %s, %s, %s, %s)",
                            (batch, name, r["source_path"],
                             r["source_row_number"], r["reason"],
                             json.dumps(r["record"], ensure_ascii=False)))
            for src in t["sources"]:
                n_read = int((df["__source_path"] == src).sum())
                n_staged = int((valid["__source_path"] == src).sum())
                cur.execute(f"INSERT INTO {CORE}.import_file_log (batch_id, "
                            "table_name, source_path, source_sha256, "
                            "rows_read, rows_staged, rows_rejected) VALUES "
                            "(%s, %s, %s, %s, %s, %s, %s)",
                            (batch, name, src, file_sha(src, root), n_read,
                             n_staged, n_read - n_staged))
            # reconciliation inside the same transaction
            cur.execute(f"SELECT count(*) FROM {STAGING}.{name} WHERE "
                        "batch_id = %s", (batch,))
            staged = cur.fetchone()[0]
            ok = record(cur, batch, name, "STAGING", "rows_read = staged + "
                        "rejected", len(df), staged + len(rejected))
            cur.execute(f"SELECT count(*) FROM {STAGING}.{name} s WHERE "
                        f"s.batch_id = %s AND {hash_sql('s', columns)} <> "
                        "s.row_hash", (batch,))
            mism = cur.fetchone()[0]
            ok &= record(cur, batch, name, "STAGING",
                         "staged text identical to source (row hash)", 0,
                         mism)
            if not ok:
                raise RuntimeError(f"{name}: staging reconciliation failed")
            parents[name] = set(zip(*[valid[c] for c in t["key"]])) | \
                core_keys(cur, name)
            summary.append({"table": name, "rows_read": len(df),
                            "rows_staged": staged,
                            "rows_rejected": len(rejected)})
            if fail_after == name:
                raise RuntimeError(f"forced failure after {name} (test)")
        cur.execute(f"UPDATE {CORE}.import_batch SET status = 'STAGED', "
                    "finished_at = now() WHERE batch_id = %s", (batch,))
        conn.commit()
        return batch, summary
    except Exception as error:
        conn.rollback()
        finish_batch(dbname, batch, "FAILED", f"rolled back: {error}"[:2000])
        raise
    finally:
        conn.close()


def filing_by_file(cur, root, names, valid_filings):
    """
    source file name -> set of filing_ids, using only filings that passed
    validation (staged in this batch) or already in core. More than one
    filing for a file name is ambiguous and rejected by validate().
    """
    if "filing" in names:
        f = read_sources(BY_NAME["filing"], root)
        pairs = zip(f["local_file_path"], f["filing_id"])
    else:
        cur.execute(f"SELECT local_file_path, filing_id FROM {CORE}.filing")
        pairs = cur.fetchall()
    out = {}
    for path, fid in pairs:
        if path and (fid,) in valid_filings:
            out.setdefault(str(path).replace("\\", "/").split("/")[-1],
                           set()).add(fid)
    return out


# ==========================================================
# promotion (test databases only in Phase 14)
# ==========================================================

def typed_expr(col, kind):
    c = q(col)
    if kind == "numeric_lenient":
        return (f"CASE WHEN s.{c} ~ '^[+-]?([0-9]+([.][0-9]*)?|[.][0-9]+)$' "
                f"THEN s.{c}::numeric END")
    return f"NULLIF(s.{c}, '')::{TYPED_SUFFIX[kind][1]}"


def promote(dbname, batch):
    if dbname == dbc.production_database():
        deferred_issues.assert_none_open("production import")
    dbc.check_target(dbname, write=True)
    conn = dbc.connect(dbname, write=True)
    out = []
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT status, tables FROM {CORE}.import_batch WHERE "
                    "batch_id = %s", (batch,))
        row = cur.fetchone()
        if row is None or row[0] != "STAGED":
            raise RuntimeError(f"batch {batch} is not STAGED ({row})")
        names = [n for n in row[1].split(",") if n]
        for name in names:
            t = BY_NAME[name]
            cols = all_columns(t)
            key = set(t["key"])
            typed = [(typed_expr(c, k), q(c + TYPED_SUFFIX[k][0]))
                     for c, k in t["typed"].items()]
            target_cols = [q(c) for c in cols] + [n for _, n in typed] + [
                "load_batch_id", "source_path", "source_row_number",
                "row_hash"]
            select = ([f"s.{q(c)}" if c in key else f"NULLIF(s.{q(c)}, '')"
                       for c in cols] + [e for e, _ in typed]
                      + ["s.batch_id", "s.source_path", "s.source_row_number",
                         "s.row_hash"])
            conflict = ", ".join(q(c) for c in t["key"])
            if t["append_only"]:
                action = "DO NOTHING"
            else:
                updates = [x for x in target_cols if x.strip('"') not in key]
                action = ("DO UPDATE SET " + ", ".join(
                    f"{x} = EXCLUDED.{x}" for x in updates)
                    + f" WHERE {CORE}.{name}.row_hash IS DISTINCT FROM "
                    "EXCLUDED.row_hash")
            cur.execute(
                f"WITH up AS (INSERT INTO {CORE}.{name} "
                f"({', '.join(target_cols)}) SELECT {', '.join(select)} "
                f"FROM {STAGING}.{name} s WHERE s.batch_id = %s "
                f"ON CONFLICT ({conflict}) {action} RETURNING (xmax = 0) AS "
                "inserted) SELECT count(*) FILTER (WHERE inserted), "
                "count(*) FILTER (WHERE NOT inserted) FROM up", (batch,))
            inserted, updated = cur.fetchone()
            cur.execute(f"SELECT count(*) FROM {STAGING}.{name} WHERE "
                        "batch_id = %s", (batch,))
            staged = cur.fetchone()[0]
            # core rows for this batch's keys must carry the staged text
            join = " AND ".join(f"c.{q(k)} = s.{q(k)}" for k in t["key"])
            cur.execute(f"SELECT count(*) FROM {STAGING}.{name} s JOIN "
                        f"{CORE}.{name} c ON {join} WHERE s.batch_id = %s "
                        f"AND {hash_sql('c', cols, coalesce=True)} = "
                        "s.row_hash", (batch,))
            matching = cur.fetchone()[0]
            ok = record(cur, batch, name, "CORE", "core text identical to "
                        "staged text for every staged key", staged, matching)
            cur.execute(f"SELECT count(*) FROM {CORE}.{name} c WHERE NOT "
                        f"EXISTS (SELECT 1 FROM {STAGING}.{name} s WHERE "
                        f"s.batch_id = %s AND {join})", (batch,))
            extra = cur.fetchone()[0]
            record(cur, batch, name, "CORE", "rows in core not in this batch "
                   "(kept, never deleted)", extra, extra,
                   "informational")
            if not ok:
                raise RuntimeError(f"{name}: core reconciliation failed "
                                   f"({matching} of {staged} match)")
            unchanged = staged - inserted - updated
            cur.execute(f"UPDATE {CORE}.import_file_log SET rows_inserted = "
                        "%s, rows_updated = %s, rows_unchanged = %s WHERE "
                        "batch_id = %s AND table_name = %s",
                        (inserted, updated, unchanged, batch, name))
            out.append({"table": name, "staged": staged,
                        "inserted": inserted, "updated": updated,
                        "unchanged": unchanged, "rows_not_in_batch": extra})
        cur.execute(f"UPDATE {CORE}.import_batch SET status = 'PROMOTED', "
                    "finished_at = now() WHERE batch_id = %s", (batch,))
        conn.commit()
        return out
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def report(dbname, batch):
    conn = dbc.connect(dbname, write=False)
    try:
        frames = {}
        for name, sql in (
                ("files", f"SELECT * FROM {CORE}.import_file_log WHERE "
                          "batch_id = %s ORDER BY table_name"),
                ("reconciliation", f"SELECT * FROM {CORE}."
                                   "import_reconciliation WHERE batch_id = %s"
                                   " ORDER BY table_name, stage"),
                ("rejections", f"SELECT table_name, reason, count(*) AS rows "
                               f"FROM {CORE}.import_rejection WHERE "
                               "batch_id = %s GROUP BY 1, 2 ORDER BY 1, 2")):
            frames[name] = pd.read_sql_query(sql, conn, params=(batch,))
        return frames
    finally:
        conn.close()


def main():
    p = argparse.ArgumentParser(description="BRSR staging loader")
    p.add_argument("--database", required=True)
    p.add_argument("command", choices=["stage", "promote", "report"])
    p.add_argument("--tables")
    p.add_argument("--batch", type=int)
    a = p.parse_args()
    pd.set_option("display.width", 200)
    if a.command == "stage":
        tables = a.tables.split(",") if a.tables else None
        batch, summary = stage(a.database, tables)
        print(f"batch {batch} STAGED")
        print(pd.DataFrame(summary).to_string(index=False))
    elif a.command == "promote":
        print(pd.DataFrame(promote(a.database, a.batch)).to_string(
            index=False))
    else:
        for name, df in report(a.database, a.batch).items():
            print(f"\n== {name}")
            print(df.to_string(index=False))


if __name__ == "__main__":
    main()
