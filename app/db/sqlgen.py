"""
Generate the versioned SQL migrations from app.db.schema_spec.

The generated files in app/db/migrations/ are committed and are the
source of truth for the database; tests regenerate them and fail on any
drift. Regenerate only together with a new migration version:

    python -m app.db.sqlgen
"""

from pathlib import Path

from app.db.schema_spec import TABLES

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
CORE, STAGING = "brsr", "brsr_staging"

TYPED_SUFFIX = {"numeric": ("_num", "numeric"),
                "numeric_lenient": ("_num", "numeric"),
                "date": ("_date", "date"), "integer": ("_int", "integer"),
                "timestamptz": ("_ts", "timestamptz")}

GROUPS = [
    ("0002", "registry", ["company", "company_identifier", "taxonomy_version",
                          "filing", "concept_crosswalk",
                          "requirement_concept_map"]),
    ("0003", "facts_and_normalisation", ["xbrl_fact", "requirement_value",
                                         "conversion_audit"]),
    ("0004", "decisions_and_review", ["unit_decision", "scale_review_record",
                                      "deferred_issue", "review_anomaly",
                                      "review_recommendation",
                                      "review_decision_log"]),
    ("0005", "assurance_and_supplementary", [
        "assurance_engagement", "assurance_fact", "assurance_provider",
        "assurance_indicator", "assurance_issue", "supplementary_document",
        "supplementary_candidate", "supplementary_coverage",
        "xbrl_pdf_crosscheck"]),
]
BY_NAME = {t["name"]: t for t in TABLES}


def q(name):
    return '"' + name.replace('"', '""') + '"'


def all_columns(t):
    return list(t["columns"]) + list(t["derived"])


def typed_name(col, kind):
    return col + TYPED_SUFFIX[kind][0]


def core_table(t):
    key = set(t["key"])
    lines = []
    for c in all_columns(t):
        lines.append(f"    {q(c)} text{' NOT NULL' if c in key else ''}")
    for c, kind in t["typed"].items():
        lines.append(f"    {q(typed_name(c, kind))} {TYPED_SUFFIX[kind][1]}")
    lines += [
        "    load_batch_id bigint NOT NULL REFERENCES "
        f"{CORE}.import_batch (batch_id)",
        "    source_path text NOT NULL",
        "    source_row_number integer NOT NULL",
        "    row_hash text NOT NULL",
        "    loaded_at timestamptz NOT NULL DEFAULT now()",
        f"    PRIMARY KEY ({', '.join(q(c) for c in t['key'])})"]
    for u in t["unique"]:
        lines.append(f"    UNIQUE ({', '.join(q(c) for c in u)})")
    for c, allowed in t["checks"].items():
        values = ", ".join("'" + v + "'" for v in allowed)
        lines.append(f"    CONSTRAINT {q(t['name'] + '_' + c + '_allowed')} "
                     f"CHECK ({q(c)} IS NULL OR {q(c)} IN ({values}))")
    for cols, ref, ref_cols in t["fks"]:
        lines.append(f"    FOREIGN KEY ({', '.join(q(c) for c in cols)}) "
                     f"REFERENCES {CORE}.{ref} "
                     f"({', '.join(q(c) for c in ref_cols)})")
    sql = (f"CREATE TABLE IF NOT EXISTS {CORE}.{t['name']} (\n"
           + ",\n".join(lines) + "\n);\n")
    for cols, ref, _ in t["fks"]:
        if cols != t["key"][:len(cols)]:
            sql += (f"CREATE INDEX IF NOT EXISTS "
                    f"{t['name']}_{'_'.join(cols)[:40]}_fk_idx ON "
                    f"{CORE}.{t['name']} "
                    f"({', '.join(q(c) for c in cols)});\n")
    if t["append_only"]:
        sql += (f"DROP TRIGGER IF EXISTS {t['name']}_append_only ON "
                f"{CORE}.{t['name']};\n"
                f"CREATE TRIGGER {t['name']}_append_only BEFORE UPDATE OR "
                f"DELETE ON {CORE}.{t['name']} FOR EACH ROW EXECUTE "
                f"FUNCTION {CORE}.forbid_change();\n"
                f"DROP TRIGGER IF EXISTS {t['name']}_no_truncate ON "
                f"{CORE}.{t['name']};\n"
                f"CREATE TRIGGER {t['name']}_no_truncate BEFORE TRUNCATE ON "
                f"{CORE}.{t['name']} FOR EACH STATEMENT EXECUTE FUNCTION "
                f"{CORE}.forbid_change();\n")
    return sql


def staging_table(t):
    lines = [f"    {q(c)} text NOT NULL" for c in all_columns(t)]
    lines += ["    batch_id bigint NOT NULL REFERENCES "
              f"{CORE}.import_batch (batch_id)",
              "    source_path text NOT NULL",
              "    source_row_number integer NOT NULL",
              "    row_hash text NOT NULL",
              "    PRIMARY KEY (batch_id, "
              f"{', '.join(q(c) for c in t['key'])})"]
    return (f"CREATE TABLE IF NOT EXISTS {STAGING}.{t['name']} (\n"
            + ",\n".join(lines) + "\n);\n")


FOUNDATION_UP = f"""-- 0001 foundation: schemas, import log tables, append-only guard.
-- The existing public schema (companies, brsr_reports, brsr_chunks) is not
-- touched by any migration.
CREATE SCHEMA IF NOT EXISTS {CORE};
CREATE SCHEMA IF NOT EXISTS {STAGING};

CREATE TABLE IF NOT EXISTS {CORE}.import_batch (
    batch_id bigserial PRIMARY KEY,
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    status text NOT NULL CHECK (status IN ('RUNNING', 'STAGED', 'PROMOTED',
                                           'FAILED')),
    mode text NOT NULL,
    target_database text NOT NULL,
    initiated_by text NOT NULL,
    tables text NOT NULL,
    notes text
);

CREATE TABLE IF NOT EXISTS {CORE}.import_file_log (
    batch_id bigint NOT NULL REFERENCES {CORE}.import_batch (batch_id),
    table_name text NOT NULL,
    source_path text NOT NULL,
    source_sha256 text NOT NULL,
    rows_read integer NOT NULL,
    rows_staged integer NOT NULL,
    rows_rejected integer NOT NULL,
    rows_inserted integer,
    rows_updated integer,
    rows_unchanged integer,
    PRIMARY KEY (batch_id, table_name, source_path)
);

CREATE TABLE IF NOT EXISTS {CORE}.import_rejection (
    rejection_id bigserial PRIMARY KEY,
    batch_id bigint NOT NULL REFERENCES {CORE}.import_batch (batch_id),
    table_name text NOT NULL,
    source_path text NOT NULL,
    source_row_number integer NOT NULL,
    reason text NOT NULL,
    record jsonb NOT NULL
);
CREATE INDEX IF NOT EXISTS import_rejection_batch_idx
    ON {CORE}.import_rejection (batch_id, table_name);

CREATE TABLE IF NOT EXISTS {CORE}.import_reconciliation (
    batch_id bigint NOT NULL REFERENCES {CORE}.import_batch (batch_id),
    table_name text NOT NULL,
    stage text NOT NULL CHECK (stage IN ('STAGING', 'CORE')),
    check_name text NOT NULL,
    expected bigint,
    actual bigint,
    passed boolean NOT NULL,
    detail text,
    PRIMARY KEY (batch_id, table_name, stage, check_name)
);

CREATE OR REPLACE FUNCTION {CORE}.forbid_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'table %.% is append-only (% not allowed)',
        TG_TABLE_SCHEMA, TG_TABLE_NAME, TG_OP;
END;
$$;
"""

FOUNDATION_DOWN = f"""-- 0001 down: only removes what 0001 created; fails if anything else
-- still lives in these schemas (RESTRICT), so no data is dropped silently.
DROP FUNCTION IF EXISTS {CORE}.forbid_change();
DROP TABLE IF EXISTS {CORE}.import_reconciliation;
DROP TABLE IF EXISTS {CORE}.import_rejection;
DROP TABLE IF EXISTS {CORE}.import_file_log;
DROP TABLE IF EXISTS {CORE}.import_batch;
DROP SCHEMA IF EXISTS {STAGING} RESTRICT;
DROP SCHEMA IF EXISTS {CORE} RESTRICT;
"""


def group_sql(version, name, tables):
    up = [f"-- {version} {name}: core tables ({CORE}) and their staging "
          f"tables ({STAGING}).\n-- Generated by app.db.sqlgen from "
          "app.db.schema_spec.\n"]
    for n in tables:
        up.append(core_table(BY_NAME[n]))
        up.append(staging_table(BY_NAME[n]))
    down = [f"-- {version} {name} down (reverse dependency order).\n"]
    for n in reversed(tables):
        down.append(f"DROP TABLE IF EXISTS {STAGING}.{n};\n"
                    f"DROP TABLE IF EXISTS {CORE}.{n};\n")
    return "\n".join(up), "".join(down)


def migrations():
    """[(version, name, up_sql, down_sql)]"""
    out = [("0001", "foundation", FOUNDATION_UP, FOUNDATION_DOWN)]
    for version, name, tables in GROUPS:
        up, down = group_sql(version, name, tables)
        out.append((version, name, up, down))
    covered = [n for _, _, ts in GROUPS for n in ts]
    assert sorted(covered) == sorted(BY_NAME), "spec and groups differ"
    return out


def write():
    MIGRATIONS_DIR.mkdir(parents=True, exist_ok=True)
    for version, name, up, down in migrations():
        (MIGRATIONS_DIR / f"{version}_{name}.up.sql").write_text(
            up, encoding="utf-8", newline="\n")
        (MIGRATIONS_DIR / f"{version}_{name}.down.sql").write_text(
            down, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    write()
    print("migrations written to", MIGRATIONS_DIR)
