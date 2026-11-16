# Database schema (Phase 14)

PostgreSQL 18.6. Production database: `brsr_db`.

**Phase 14 never writes to `brsr_db`.** The guard lives in
`app/db/connection.py` and refuses write connections to `brsr_db` before any
connection is opened. Testing used separate databases with the prefix
`brsr_phase14_`:

- `brsr_phase14_test` — manual runs
- `brsr_phase14_pytest` — created and dropped by the tests

The existing `public` tables in `brsr_db` are not touched by any migration:
`companies` (100 rows), `brsr_reports` (4 rows) and `brsr_chunks` (1,000
rows).

## Schemas

| Schema | Purpose |
|---|---|
| `brsr_meta` | `schema_migrations`: applied version, name, SHA-256 checksum of the up file, applied_at / applied_by, execution time |
| `brsr` | Core tables, import logs, append-only guard function |
| `brsr_staging` | One staging table per core table: all columns as text, plus batch_id, source path, source row number and row hash |

## Storage rules

- **Source columns.** Every source column is stored as text, exactly as
  written in the approved output file.
- **Key columns.** These are NOT NULL. An empty string is a valid key value,
  for example `breakdown = ''`.
- **Other columns.** An empty string is stored as NULL, which means missing.
  It is never stored as 0.
- **Typed copies.** These are added as `<column>_num` (numeric),
  `_date`, `_int` or `_ts`.
  - They are validated casts.
  - `requirement_value.original_value_num` is lenient: a text value gets
    NULL.
- **Traceability.** Every core row carries `load_batch_id`, `source_path`,
  `source_row_number` and `row_hash`. `row_hash` is the SHA-256 of the
  source row text.
- **CHECK constraints.** These keep the review and assurance vocabularies
  distinct.
  - `engagement_type` allows: ASSESSMENT, ASSURANCE, NONE, UNKNOWN,
    AMBIGUOUS, NOT_REPORTED, NOT_IN_TAXONOMY_VERSION.
  - `assurance_level` allows: REASONABLE, LIMITED, AMBIGUOUS, NOT_REPORTED,
    EMPTY, NIL.
  - Review status allows the seven workflow statuses.
  - Decision allows APPROVE, REJECT and DEFER.
  - `period_role` allows CY and PY.
  - `multiplier_basis` allows the four factor-model bases.
- **Append-only.** `review_decision_log` has an append-only trigger. UPDATE,
  DELETE and TRUNCATE raise an exception.

## Core tables (`brsr`)

| Table | Source | Primary key | Foreign keys |
|---|---|---|---|
| company | registry/company_master.csv | company_id | – (isin unique) |
| company_identifier | registry/company_identifiers.csv | company_id, identifier_type, value, source, valid_from, valid_to, notes | company |
| taxonomy_version | taxonomy/taxonomy_registry.csv | taxonomy_version | – |
| filing | registry/filing_manifest.csv | filing_id | company, taxonomy_version |
| concept_crosswalk | taxonomy_versions/nse_brsr_concept_crosswalk.csv | source_version, source_concept | taxonomy_version |
| requirement_concept_map | reference/brsr_requirement_concept_map.csv | requirement_id, current_concept | – |
| xbrl_fact | parsed/xbrl_facts.csv (raw parsed facts) | fact_id | filing |
| requirement_value | production normalised CY + PY (the 39 established columns, plus derived filing_id) | source_file, context_reference, requirement_id, breakdown, period_role, metric_name | filing |
| conversion_audit | the same files (factor-model columns) | same as requirement_value | requirement_value |
| unit_decision | reference/brsr_mtco2e_unit_decisions.csv | source_file, context_reference, requirement_id, breakdown | – |
| scale_review_record | filer scale-error final review (39 FSE) | approval_id | – |
| deferred_issue | reference/deferred_issues_register.csv | issue_id | – |
| review_anomaly | review_workflow/anomalies.csv | anomaly_id | – |
| review_recommendation | review_workflow/review_queue.csv | recommendation_id | – |
| review_decision_log | reference/review_decisions.csv (append-only) | entry_no | – |
| assurance_engagement | assurance/assurance_engagements.csv | filing_id | filing |
| assurance_fact | assurance/assurance_facts.csv | fact_id | filing, xbrl_fact |
| assurance_provider | assurance/assurance_providers.csv | filing_id, provider_member | filing |
| assurance_indicator | assurance/assurance_indicator_coverage.csv | filing_id, indicator_key | filing |
| assurance_issue | assurance/assurance_issues.csv | filing_id, issue_code, fact_id, detail | filing |
| supplementary_document | supplementary/document_inventory.csv | document_id | – |
| supplementary_candidate | supplementary/external_requirement_candidates.csv | document_id, page, rule, requirement_id, matched_text, snippet | supplementary_document |
| supplementary_coverage | supplementary/external_requirement_coverage.csv | master_isin, fy_slot, requirement_id | – |
| xbrl_pdf_crosscheck | supplementary/xbrl_pdf_crosscheck.csv | document_id, xbrl_source_file, xbrl_context, requirement_id, breakdown | supplementary_document |

Supplementary evidence is a separate layer. No foreign key or process
writes from it into `requirement_value`.

Import tables:

- `import_batch`: status RUNNING / STAGED / PROMOTED / FAILED
- `import_file_log`: source SHA-256, and rows read / staged / rejected /
  inserted / updated / unchanged
- `import_rejection`: reason, plus the full record as jsonb
- `import_reconciliation`: every check with expected, actual and passed

## Migrations

The migrations are `app/db/migrations/NNNN_name.up.sql` and `.down.sql`,
generated from `app/db/schema_spec.py` by `app/db/sqlgen.py`. A test fails if
the committed files differ from the spec.

| Version | Content |
|---|---|
| 0001 | Schemas, import tables, append-only function |
| 0002 | Registry |
| 0003 | Facts and normalisation |
| 0004 | Decisions and review |
| 0005 | Assurance and supplementary |

How the migrations behave:

- Each migration runs in one transaction together with its
  `schema_migrations` row.
- The DDL is idempotent (`IF NOT EXISTS`).
- `up` skips migrations that are already applied.
- A changed file of an applied version is refused.
- `down` runs in reverse order. The 0001 down drops its schemas only if
  they are empty (`RESTRICT`).

```
python -m app.db.migrate --database brsr_phase14_test create-test-db
python -m app.db.migrate --database brsr_phase14_test up
python -m app.db.migrate --database brsr_phase14_test status|verify
python -m app.db.migrate --database brsr_phase14_test down --steps 1
```

## Staging loader

```
python -m app.db.staging_loader --database brsr_phase14_test stage [--tables a,b]
python -m app.db.staging_loader --database brsr_phase14_test promote --batch N
python -m app.db.staging_loader --database brsr_phase14_test report --batch N
```

### Stage

Stage runs in one transaction. Any error rolls the whole batch back, and the
batch is recorded as FAILED.

1. Read the sources.
2. Validate each row. Any of the following rejects the row with its reason:
   - schema drift (this fails the whole table);
   - an empty key;
   - a duplicate key;
   - an invalid numeric, date, integer or timestamp value;
   - a CHECK value outside the allowed list;
   - a foreign key with no parent;
   - an unresolved or ambiguous derived filing.
3. COPY the valid rows into staging, exactly as text.
4. Reconcile:
   - rows read = rows staged + rows rejected;
   - every staged row, re-hashed inside PostgreSQL, equals its source row
     hash.

### Promote

- Promote upserts staging into core in one transaction.
  - Unchanged rows are not rewritten. A re-run of the same data reports
    0 inserted and 0 updated.
  - Rows missing from the new source are kept and reported. They are never
    deleted.
  - Append-only tables never change existing rows.
- Core reconciliation re-hashes every promoted row and checks that it
  equals the staged hash.
- In Phase 14, promotion is refused for the production database.
- A future production import must also pass
  `app.core.deferred_issues.assert_none_open()`.
