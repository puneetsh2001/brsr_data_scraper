-- 0001 down: only removes what 0001 created; fails if anything else
-- still lives in these schemas (RESTRICT), so no data is dropped silently.
DROP FUNCTION IF EXISTS brsr.forbid_change();
DROP TABLE IF EXISTS brsr.import_reconciliation;
DROP TABLE IF EXISTS brsr.import_rejection;
DROP TABLE IF EXISTS brsr.import_file_log;
DROP TABLE IF EXISTS brsr.import_batch;
DROP SCHEMA IF EXISTS brsr_staging RESTRICT;
DROP SCHEMA IF EXISTS brsr RESTRICT;
