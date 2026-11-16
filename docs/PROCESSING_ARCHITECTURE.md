# Processing architecture

This document describes the BRSR data platform end to end, from source
discovery to the deliverables in `data/outputs/`.

- **Primary source:** NSE BRSR XBRL. The scope is the Nifty 100 companies for
  the FY slots 2021-22 to 2025-26.
- **Single entry point:** `python -m app.pipeline`.
- **Field meanings:** see [DATA_DICTIONARY.md](DATA_DICTIONARY.md).
- **Rules:** see [VALIDATION_RULES.md](VALIDATION_RULES.md).
- **Database:** see [DATABASE_SCHEMA.md](DATABASE_SCHEMA.md).
- **Sources and access limits:** see [SOURCE_REGISTER.md](SOURCE_REGISTER.md).

## Principles

- **Raw data is immutable.** Raw XBRL is stored under
  `data/raw/nse_brsr_xml/<ISIN>/`. A malformed file is sanitised into a copy,
  and the original is kept.
- **Every value is traceable.** Each value carries its filing, source file,
  context, concept and source URL.
- **Missing is not zero.**
  - A blank means not reported.
  - An explicit filed zero stays `0` and is labelled REPORTED_ZERO.
  - Units are never guessed from the magnitude of a value.
- **Corrections need owner approval.**
  - Decisions are made through the reviewer workflow and its append-only log
    (`data/reference/review_decisions.csv`).
  - Recommendations are never applied by themselves.
- **Pending work stays visible.** Pending, deferred, candidate and unresolved
  records are kept in every layer and labelled.
- **Supplementary evidence is a separate layer.** It never replaces XBRL
  values.
- **Re-runs are deterministic.** A re-run on the same inputs gives identical
  outputs. A SHA-256 audit is taken before and after each phase.

## Pipeline

```
discover ──> download ──> process ───────────────────────────> validate ──> report ──> load-db
 NSE API      XBRL by      verify → extract → parse → metrics    rules        data/      test DB only
 registry     ISIN         → requirements → normalise            blocks       outputs/   (production
                           → assurance → supplementary           review                  blocked)
```

| Layer | Module | Output (`data/processed/…`) |
|---|---|---|
| Company master | `app.discovery.company_master` | `registry/company_master.csv`, identifiers, listings |
| Filing discovery | `app.scrapers.nse_brsr_filing_index` | `nifty100/nse_brsr_filing_index*.csv` |
| Filing registry | `app.discovery.filing_registry` | `registry/filing_manifest.csv` (versions, latest valid, status, `needs_download` / `needs_processing`) |
| Download | `app.downloaders.xbrl_downloader --source index` | `data/raw/nse_brsr_xml/<ISIN>/` (existing files are skipped) |
| Identity / FY verification | `app.validators.brsr_report_verifier` | `nifty100/nse_brsr_extraction_manifest.csv` |
| Taxonomy | `app.extractors.nse_brsr_taxonomy_mapping_builder`, `app.taxonomy.taxonomy_registry` | `taxonomy/`, `taxonomy_versions/<v>/` (5 versions) |
| Fact extraction | `app.extractors.nse_brsr_multi_company_preview` | `nifty100/multi_company_facts.csv` |
| XBRL parser (raw facts) | `app.parsers.xbrl_fact_store` | `nifty100/parsed/xbrl_facts.csv` (857,776 facts) |
| Metric registry | `app.metrics.metric_registry` | `metrics/` |
| Requirement values and coverage | `app.extractors.brsr_requirement_extractor` | `nifty100/brsr_requirement_values*.csv`, `brsr_requirement_coverage.csv` |
| Normalisation (factor model) | `app.normalisation.factor_model` | `nifty100/normalised/` (production), `normalised_factor_model/` |
| Assurance | `app.assurance.extractor` | `nifty100/assurance/` |
| Supplementary evidence | `app.supplementary.evidence` | `nifty100/supplementary/` |
| Validation rules | `app.validation.rules_engine` | `nifty100/validation/` |
| Block validation | `app.validation.block_validation` | `nifty100/blocks/` |
| Anomalies and review workflow | `app.review.workflow` | `nifty100/review_workflow/` |
| Deliverables | `app.reporting.deliverables`, `app.reporting.data_dictionary` | `data/outputs/` and `docs/DATA_DICTIONARY.md` |
| Database | `app.db.migrate`, `app.db.staging_loader` | PostgreSQL test database (`brsr_phase14_*`) |

### Normalisation factor model

```
final_value = original_value × actual_numeric_multiplier_on_original
```

The multiplier has one of four bases:

- `NONE`
- `RULE_UNIT_CONVERSION`
- `APPROVED_UNIT_ASSUMPTION`
- `APPROVED_SCALE_CORRECTION`

Informational unit reinterpretations and legacy factors are recorded but never
applied.

The engine refuses to apply a record in any of these cases:

- the decision is stale;
- the approval is incomplete;
- the block is partial;
- the record is inconsistent.

Pending scale and unit decisions keep their original values.

### Coverage statuses

The coverage report keeps the extraction pipeline's status in
`pipeline_status`. It adds `coverage_status`, which differs from the pipeline
status only when there is evidence of an extraction failure. That evidence is
one of two things:

- the filing exists but the download or parse failed. This applies to two
  filings: an HTTP 404 and a malformed XML file.
- a CY fact is present in the parsed facts but no value was selected. In this
  run, 0 cells had this. The 7 candidate cells had prior-year facts only.

No status is ever inferred from the absence of a local PDF. Coverage counts
are counts of metrics, not of XBRL facts.

## Incremental processing

- **Downloads.** `needs_download` / `needs_processing` in the filing manifest
  select the filings to fetch, and the downloader skips files that already
  exist.
- **Stages.** `app.pipeline` stores a fingerprint for each stage in
  `data/processed/pipeline_state.json`. The fingerprint is a SHA-256 of the
  stage's code, its arguments and the content of its inputs.
- **When a stage runs.** A stage runs only in one of these cases:
  - no state is recorded;
  - the fingerprint changed;
  - an output is missing;
  - an upstream stage runs.
- **`--force`.** This runs every stage regardless.
- **Baseline.** `status --record-state` records the approved outputs as the
  baseline.
- **Selective processing is not supported.** Processing one company or one
  FY on its own would leave the outputs inconsistent, for two reasons:
  - The stage modules write corpus-wide files: the requirement values, the
    coverage file, the production normalised files, validation results,
    blocks and the review queue.
  - Several rules compare neighbouring years: YoY anomalies, RST01/RST02
    restatements and CY/PY consistency.
- **`reprocess-full-corpus`.** This command re-runs every process and
  validate stage, forced, for all in-scope filings (417 filings, 98
  companies).
  - `--check-company` and `--check-fy` do not limit the run. They only verify
    that a filing is part of it and list it in the plan.
  - The old name `reprocess` is refused with an explanation.
  - After each successful stage, its fingerprint is recorded from the
    post-run inputs. A following incremental run therefore reports every
    stage as up to date.
- **Backup before execution.** Before any executed `process`, `validate` or
  `reprocess-full-corpus` run, `data/processed/` (without `backups/`) is
  copied to `backups/<timestamp>_pre_<command>/processed/`.
  - Every copy is verified by SHA-256 and listed in `backup_sha256.txt`.
  - No backup is taken when nothing would run.

## Safety gates

| Gate | Where |
|---|---|
| Network and rewrite commands are dry runs unless `--execute` is given | `app/pipeline.py` |
| Writes to the production database are refused, and only `brsr_phase14_*` test databases are accepted | `app/db/connection.py` (`PRODUCTION_WRITES_ENABLED = False`) |
| A production import requires the deferred issues to be resolved | `app.core.deferred_issues.assert_none_open()` (39 issues are open) |
| Decisions only through the append-only log; only FSE records have an apply path | `app/review/workflow.py`, factor model |
| No supplementary PDF downloads | owner decision (no PDF stage in the CLI) |
| No access-control bypass | adapters stop at HTTP 403 (see SOURCE_REGISTER.md) |

## Deliverables (`data/outputs/`)

`python -m app.pipeline report` writes the following:

- the 20 files of the master prompt;
- `company_year_coverage_summary.csv`;
- `manifest.json`, with the SHA-256 and row count of every output and every
  source.

Report generation happens in this order:

1. **Generate.** All files are written into a staging folder,
   `.<outputs>.staging-<timestamp>`, next to the output folder.
2. **Verify.** All expected files must be present, non-empty and match the
   manifest SHA-256. Every column must be documented.
3. **Back up.** The current outputs are copied to
   `data/processed/nifty100/backups/<timestamp>_pre_report_outputs/`. Each
   copy is SHA-256 verified, and the list is written to `backup_sha256.txt`.
4. **Replace.** Only after the backup is verified, the files are moved into
   place.

What happens on failure:

- A failure in steps 1 to 3 leaves the current outputs untouched.
- A failure during step 4 restores every replaced file from the verified
  backup.
- The staging folder is always removed.

`--output-dir`, `--backup-dir` and `--dictionary-path` allow an isolated run.
This was verified by running the real CLI twice in a temporary directory. The
rebuilt files were byte-identical to the accepted outputs.

The files are built only from approved outputs:

- Five files are byte-identical copies: company master, filing manifest, all
  facts, data quality and block validation.
- The others select, join and label existing columns.

## Known limitations

- **39 deferred issues remain open.** They are listed in
  `data/reference/deferred_issues_register.csv`.
- **Some decisions are still pending:**
  - the 39 filer scale-error records;
  - 529 pending unit assumptions;
  - 230 pending crosswalk review cells (V01 is not approved).
- **Supplementary PDFs cover Reliance only.** The 417 NSE BRSR PDF URLs are
  recorded but not downloaded.
- **Two in-scope filings could not be extracted:** a 404, and a malformed XML
  file.
- **Some external sources are blocked.** The BSE API and the MCA portal return
  HTTP 403. MCA validation through data.gov.in needs an API key.
- **The production database import has not been performed.** It needs owner
  approval.
