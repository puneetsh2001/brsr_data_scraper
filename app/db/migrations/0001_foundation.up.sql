-- 0001 foundation: schemas, import log tables, append-only guard.
-- The existing public schema (companies, brsr_reports, brsr_chunks) is not
-- touched by any migration.
CREATE SCHEMA IF NOT EXISTS brsr;
CREATE SCHEMA IF NOT EXISTS brsr_staging;

CREATE TABLE IF NOT EXISTS brsr.import_batch (
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

CREATE TABLE IF NOT EXISTS brsr.import_file_log (
    batch_id bigint NOT NULL REFERENCES brsr.import_batch (batch_id),
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

CREATE TABLE IF NOT EXISTS brsr.import_rejection (
    rejection_id bigserial PRIMARY KEY,
    batch_id bigint NOT NULL REFERENCES brsr.import_batch (batch_id),
    table_name text NOT NULL,
    source_path text NOT NULL,
    source_row_number integer NOT NULL,
    reason text NOT NULL,
    record jsonb NOT NULL
);
CREATE INDEX IF NOT EXISTS import_rejection_batch_idx
    ON brsr.import_rejection (batch_id, table_name);

CREATE TABLE IF NOT EXISTS brsr.import_reconciliation (
    batch_id bigint NOT NULL REFERENCES brsr.import_batch (batch_id),
    table_name text NOT NULL,
    stage text NOT NULL CHECK (stage IN ('STAGING', 'CORE')),
    check_name text NOT NULL,
    expected bigint,
    actual bigint,
    passed boolean NOT NULL,
    detail text,
    PRIMARY KEY (batch_id, table_name, stage, check_name)
);

CREATE OR REPLACE FUNCTION brsr.forbid_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'table %.% is append-only (% not allowed)',
        TG_TABLE_SCHEMA, TG_TABLE_NAME, TG_OP;
END;
$$;
