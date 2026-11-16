# BRSR Data Scraper (Nifty 100, NSE XBRL)

Extracts SEBI BRSR disclosures for the Nifty 100 companies from NSE XBRL
filings, maps them to the current SEBI taxonomy, normalises units and
prepares validated CSVs for PostgreSQL. **No data is written to PostgreSQL
until explicitly approved.**

## Configuration, logging and HTTP

- `app/config/settings.py` - all settings with defaults; override any of them
  with `BRSR_*` environment variables or `.env` (see `.env.example`).
  Database credentials use `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`,
  `DB_PASSWORD` and are never logged.
- `app/core/logging_setup.py` - console text + structured JSON lines in
  `logs/<run>_<YYYYMMDD>.jsonl`.
- `app/core/http_client.py` - shared client used by discovery, downloads and
  the taxonomy downloader: retries with exponential backoff (429, 5xx and CDN
  520-530), Retry-After, per-host rate limiting, NSE cookie warm-up, optional
  response cache (`data/cache/http`), atomic checksummed downloads that never
  overwrite existing files. Restricted resources (401/403 after a session
  refresh) raise `AccessDenied` and are not bypassed.
- The taxonomy downloader reuses the local ZIP unless `--refresh` is given and
  falls back to it if the NSE archive is unavailable.

## Company master and filing registry (`data/processed/registry/`)

| Command | Output |
|---|---|
| `python -m app.discovery.company_master` | `company_master.csv` (one row per company, `company_id` = `C-<ISIN>`, or `C-SYM-<symbol>` for merged/delisted filers), `company_identifiers.csv` (ISIN / CIN / symbol / name history), `company_exchange_listings.csv` (NSE and BSE) |
| `python -m app.discovery.filing_registry [--refresh-api]` | `filing_manifest.csv` (one row per filing: versions, latest valid filing, download / parsing / processing status, incremental flags `needs_download` / `needs_processing`), `filing_registry_summary.csv` |

The company universe is not hardcoded: every company that filed BRSR on NSE
(from the saved API snapshots) is registered. Which companies are downloaded
and processed is controlled by `BRSR_COMPANY_UNIVERSE_FILE` and
`BRSR_COMPANY_FILTER`. FY slot = the April-March year holding most days of
the reporting period (ties -> period-end year).

BSE manual imports are registered only when the file is readable XBRL, the
company is identified, and the identifiers agree. A file whose ISIN differs
from the company matched by CIN or BSE code is kept as `IDENTIFIER_CONFLICT`
for manual review.

## Taxonomy registry (`data/processed/taxonomy/`)

`python -m app.taxonomy.taxonomy_registry` reads only the local taxonomy
packages (it downloads nothing) and writes:

| Output | Content |
|---|---|
| `taxonomy_registry.csv` | one row per version: package metadata, source URL, container SHA-256, acquisition time and basis, counts, observed FY usage in filings, previous / superseded-by |
| `taxonomy_files.csv` | every taxonomy file with SHA-256, checked against its source ZIP |
| `taxonomy_calculation_relationships.csv` | calculation linkbase (summation arcs, weights) |
| `taxonomy_label_completeness.csv`, `taxonomy_label_gaps.csv` | label coverage per role; SEBI publishes no documentation labels (`NOT_PROVIDED_BY_SEBI`); missing, duplicate and shared labels are reported as published, never filled in |
| `taxonomy_references.csv` | SEBI circular references (2024-04-30 onwards) |
| `concept_version_applicability.csv` | concept × version presence, data-type changes, removed concepts with their (read-only) crosswalk mapping |
| `taxonomy_integrity_issues.csv` | archive mismatches, unresolved arcs, unknown concepts |

FY applicability is what is observed in NSE filings, not a legal statement.

## XBRL parser (`data/processed/nifty100/parsed/`)

`python -m app.parsers.xbrl_fact_store` parses every in-scope filing with
`app.parsers.xbrl_instance` and writes facts (stable `fact_id` =
`<filing_id>#<sequence>`, source line, unit measure, decimals / precision,
exact `numeric_value`, sign), contexts, a long dimensions table, units,
footnotes, duplicate facts (COMPLETE / CONSISTENT / INCONSISTENT), parse
issues and a per-filing summary.

- Nothing is skipped silently: facts with an undefined context or unit are
  kept and reported. Values are never rounded, converted or guessed
  (`1,234` or `1e5` in a numeric fact is reported as `NOT_A_VALID_NUMBER`).
- Numeric-ness comes from the concept type in the taxonomy version the
  filing declares, not from the value.
- A declared rounding level (`LevelOfRounding...`) is recorded, never applied.
- `xbrl_reconciliation.csv` compares the new parser with the current
  extraction (`multi_company_facts.csv`); the current extraction stays the
  production input until a later phase switches over.

## Metric registry (`data/processed/metrics/`)

`python -m app.metrics.metric_registry` turns
`data/reference/brsr_requirement_concept_map.csv` (read only) into a checked
registry: concept type / period type per metric, expected unit vs taxonomy
type, availability per taxonomy version (only exact or approved crosswalk
entries count; `CANDIDATE_REVIEW` shows as `PENDING_REVIEW`), map axes and
members validated against each version's definition linkbase, axes observed
in filings, coverage per FY slot from the Phase 6 facts, and a
reconciliation with `brsr_requirement_coverage.csv` that classifies every
disagreement.

## Normalisation engine with the explicit factor model (production since 2026-11-12)

`python -m app.normalisation.factor_model` writes the production
normalisation in `data/processed/nifty100/normalised/`. The old command
`app.extractors.brsr_unit_normaliser` now runs the same engine.

- The established columns keep their names, order and meaning:
  - `normalised_value` = `final_value`
  - `conversion_factor` = `actual_numeric_multiplier_on_original`
  - `conversion_status` = `final_status`
- The factor-model columns are appended after them.
- `app.normalisation.factor_model.read_production()` restores the
  factor-model field names when reading these files.
- The retired normaliser is archived in
  `C:/brsr_data_scraper_archive/production_switch_20261112/`.

The engine computes, for every requirement
value, `final_value = original_value x actual_numeric_multiplier_on_original`.
Each multiplier has exactly one basis:

- `RULE_UNIT_CONVERSION`
- `APPROVED_UNIT_ASSUMPTION`, from `data/reference/brsr_mtco2e_unit_decisions.csv`
- `APPROVED_SCALE_CORRECTION`, from the filer scale-error final review

Decisions are applied only when all of these hold:

- the decision is approved, with a decision, an approver and a date;
- it is not part of a partial block;
- it still matches the value and unit as filed.

Two factors are recorded but never applied:

- the unit-reinterpretation factor (for example J -> GJ keeps the number);
- the legacy factor on the normalised value.

Pending decisions are carried in `pending_review`.

The engine's reports are written to `normalised_factor_model/`:

- `factor_model_decisions.csv`
- `factor_model_invariants.csv`
- `factor_model_differences.csv`
- `factor_model_reconciliation.csv`

Every run is reconciled against the frozen output of the retired
normaliser, in `backups/20261112_pre_production_switch/normalised/`.
`factor_model_differences.csv` stays empty until an approved decision is
applied. All `factor_model_invariants.csv` checks must pass.

## Validation rules (`data/processed/nifty100/validation/`)

`python -m app.validation.rules_engine` runs 28 rules in six categories:
structural, environmental, social, governance, intensity and restatement.
It reads the factor-model values. See `docs/VALIDATION_RULES.md`.

- New rules record PASS / FAIL / NOT_EVALUATED.
- Missing values are never treated as zero.
- The legacy checks C01–C08, C10 and C11 run unchanged and must reconcile
  with the existing issues file.

## Assurance extraction (`data/processed/nifty100/assurance/`)

`python -m app.assurance.extractor` extracts every assurance-related fact
from the parsed facts of the in-scope filings.

Each fact keeps its traceability: `fact_id`, XML line, context,
dimensions, period, original concept and value as filed.

The following are kept apart and never merged:

- BRSR Core assessment vs assurance (2025+)
- the 2024 reasonable-assurance flag. It is a different question, so the
  V01 candidate mapping is not applied.
- indicator scope (All / Partial)
- the per-indicator assured flag and level (Reasonable / Limited)
- provider details (2024 concept names are preserved)
- the legacy "assessment or evaluation or assurance" external-agency
  questions
- the Section B policy evaluation, which is not an assurance

An assurance level is recorded only where the filing states it. A value
outside a field's allowed values is AMBIGUOUS.

Outputs: engagements, providers, provider coverage, indicator coverage,
legacy agency, issues, crosswalk mapping issues and the metric-registry
reconciliation. The anomaly engine reads the issues as the ASSURANCE
category.

## PostgreSQL schema, migrations and staging loader (Phase 14)

See `docs/DATABASE_SCHEMA.md`.

- The schema has two parts: `brsr` (core) and `brsr_staging`. Tables are
  generated from `app/db/schema_spec.py`.
- Migrations are versioned SQL files, recorded with checksums in
  `brsr_meta.schema_migrations`.
- Staging is one transaction: validation, rejections and reconciliation by
  row hash.
- Promote is an idempotent upsert that never deletes.

Writes to the production database (`DB_NAME`) are blocked in code in
Phase 14. Tools only accept databases with the prefix `brsr_phase14_`. The
production import needs owner approval.

## Supplementary-source evidence (`data/processed/nifty100/supplementary/`)

`python -m app.supplementary.evidence` builds an evidence register from
supplementary documents.

Documents come from three places:

- **Local PDFs.** These are hashed and read from the text layer only.
- **Owner-supplied PDFs**, in `data/raw/supplementary/manual/<ISIN>/`.
- **NSE BRSR PDF URLs.** These are recorded but not downloaded (owner
  decision).

The register records:

- candidate evidence for the requirements BRSR XBRL does not cover (S27,
  G04, G05, G06, G20, S28): page, snippet, and value and unit text as
  printed, never converted or applied;
- an XBRL ↔ BRSR PDF verbatim cross-check;
- assurance-statement evidence. Reasonable, limited and assessment wording
  are kept separate.

The anomaly engine reads these results as the SUPPLEMENTARY_EVIDENCE
category (DETECTED / RECOMMENDED; never approved automatically).

## Anomaly engine and reviewer approval workflow (`data/processed/nifty100/review_workflow/`)

`python -m app.review.workflow` does two things:

- runs the anomaly engine (`app.review.anomaly_engine`) over the
  production normalisation, the validation results, the parser, the
  crosswalk and the existing review files;
- builds the review queue.

There are seven statuses:

| Status | Meaning |
|---|---|
| DETECTED | an anomaly with no proposed correction |
| RECOMMENDED | the engine proposes a correction or interpretation |
| PENDING_REVIEW | an existing review record waits for a reviewer decision |
| APPROVED | a reviewer approved it |
| REJECTED | a reviewer rejected it |
| DEFERRED | deferred by the owner or a reviewer |
| APPLIED | approved, and the production normalisation reflects it |

A detection never approves itself.

Reviewer decisions go only into the append-only log
`data/reference/review_decisions.csv`:

- Each entry needs a reviewer, an ISO timestamp and an audit reference.
- Every entry is validated against the allowed transitions.
- An entry that fails validation is refused and changes nothing.

Only filer scale-error records (`FSE-*`) have an apply path. The
factor-model engine reads their approvals from the log and still applies
its guards.

## Deferred data-quality issues

`data/reference/deferred_issues_register.csv` lists the issues the owner
deferred. Each one has the status `DEFERRED_PENDING_REVIEW`, a revisit phase
and the evidence references. `app.core.deferred_issues.assert_none_open()`
must be called by the database loader, so no deferred issue is loaded
silently. The factor-model output links affected values through
`deferred_issue_ids`.

## Pipeline CLI and deliverables (Phase 15, `data/outputs/`)

`python -m app.pipeline <command>` is the single entry point. See
[docs/PROCESSING_ARCHITECTURE.md](docs/PROCESSING_ARCHITECTURE.md) for how
the pipeline fits together.

| Command | What it does |
|---|---|
| `status [--record-state]` | Registry counts, incremental flags, deferred gate and the stage plan. `--record-state` records the current outputs as the baseline. |
| `discover [--execute]` | NSE filing index, company master and filing registry (network) |
| `download [--execute]` | Downloads XBRL for filings flagged `needs_download`, skipping existing files (network) |
| `refresh-taxonomy [--execute]` | Downloads and rebuilds the taxonomy (network) |
| `process [--execute] [--force]` | verify → extract → parse → metrics → requirements → normalise → assurance → supplementary |
| `reprocess-full-corpus [--check-company X] [--check-fy Y] [--execute]` | Re-runs every process and validate stage, forced, for **all** in-scope filings. Selective per-company or per-FY processing is not supported, because the outputs are corpus-wide and some rules compare years. `--check-*` only verifies inclusion. The old `reprocess` name is refused. |
| `validate [--execute] [--force]` | Rules, blocks and the review workflow |
| `report [--output-dir D] [--backup-dir B] [--dictionary-path F]` | Builds the deliverables into a staging folder and verifies them. It then backs up the current outputs, with every copy SHA-256 verified, and only then replaces them. A failed build leaves `data/outputs/` untouched. Also writes `docs/DATA_DICTIONARY.md`. |
| `load-db --database brsr_phase14_<name> [--execute] [--promote]` | Stages into a test database only. Production is refused, and the deferred-issues gate is reported. |

How the commands behave:

- Every network or rewrite command is a dry run unless `--execute` is given.
- Stages are incremental: each one has a SHA-256 fingerprint of its code and
  inputs, stored in `data/processed/pipeline_state.json`.
- No command downloads supplementary PDFs.
- Executed `process`, `validate` and `reprocess-full-corpus` runs first
  copy `data/processed/` to `backups/<timestamp>_pre_<command>/`, with
  every copy SHA-256 verified.

**Grain of `company_master.csv`.** The file has 1,342 rows and is the whole
discovered registry, not just the Nifty 100. There is one row per company:
`company_id` and `nse_symbol` are unique, and the 1,313 non-blank ISINs are
unique too.

- **Who is in it.** Every company that filed BRSR on NSE (1,340), plus the
  two Nifty 100 companies that have no NSE BRSR filing yet: VAML and ENRIN.
- **Merged or delisted filers.** 29 rows have `company_id` = `C-SYM-<symbol>`
  and no ISIN.
- **The Nifty 100.** Filter `in_processing_universe = True` to get the 100
  companies.
- **Filings.** `filing_manifest.csv` is also registry-wide: 4,923 filing
  versions. Filter `in_processing_scope = True` to get the 417 in-scope
  filings.

`data/outputs/` holds the following:

- the 20 master-prompt files: `company_master`, `filing_manifest`,
  `taxonomy_concepts`, `concept_metric_mapping`, `all_brsr_facts`,
  `normalised_brsr_facts`, five pillar files (`environmental`, `social`,
  `governance`, `corporate_identity`, `assurance_metrics`),
  `metric_coverage_report`, `missing_metrics_report`,
  `unmapped_concepts_report`, `unit_validation_report`,
  `data_quality_report`, `block_validation_report`,
  `scale_error_recommendations`, `restatement_validation_report` and
  `final_processing_summary`;
- `company_year_coverage_summary.csv`;
- `manifest.json`, with the SHA-256 and row count of every file.

What the files contain:

- **Pillar files.** These contain all CY rows, including pending, deferred,
  candidate and unresolved records. Each row carries `record_status`,
  `validation_status`, `pending_review` and `deferred_issue_ids`.
- **Coverage report.** `pipeline_status` is kept unchanged. `coverage_status`
  adds `EXTRACTION_ERROR` only where there is evidence. Every column is
  documented in [docs/DATA_DICTIONARY.md](docs/DATA_DICTIONARY.md).

## BRSR Analytics application (API and web UI)

To start the application:

```
venv/Scripts/python -m uvicorn app.api.main:app --port 8000
```

Then open http://localhost:8000/app/.

- **Backend.** The backend is in `app/analytics/` and `app/api/` (FastAPI).
  - It reads the database named by `BRSR_ANALYTICS_DB_NAME`, which defaults
    to `brsr_phase14_test`.
  - Every session is read-only, and the production database is refused.
  - The endpoints are under `/api/`; the full list is at `/docs`.
  - Metric availability is listed in
    [docs/METRIC_AVAILABILITY.md](docs/METRIC_AVAILABILITY.md).
- **Frontend (React).** The frontend is in `frontend/`: React 19 and Vite 8,
  in plain JavaScript (JSX). It has no runtime libraries besides React, and
  its charts are inline SVG. The API serves the production build
  (`frontend/dist`) at `/app/`. The React app talks only to the API, never
  to PostgreSQL.
  - **Node.js:** v26.13.0 LTS (Node 22.22 or later is required).
    Download the zip from https://nodejs.org/dist/v26.13.0/ and check it
    against `SHASUMS256.txt` from the same folder. In development it is
    unpacked in `%LOCALAPPDATA%\Programs\node-v26.13.0-win-x64`.
  - **Commands** (run in `frontend/`):
    - `npm ci` installs the exact versions from `package-lock.json`.
    - `npm run build` builds the bundle served at `/app/`.
    - `npm test` runs the Vitest unit and component tests.
    - `npm run dev` starts the Vite dev server, which proxies `/api` to
      uvicorn on port 8000.
  - **Windows quirk:** run npm from `C:\brsr_data_scraper\frontend` with an
    upper-case drive letter. A working directory spelled `c:\…` makes Vitest
    load two copies of itself, and every test fails with "Cannot read
    properties of undefined (reading 'config')".
  - **Runtime configuration** is in `frontend/public/config.js`, copied to
    `dist/config.js`, so it can change without a rebuild:
    - `apiBase`: empty means the same origin;
    - `summaryMetricKeys`: the metric keys shown as overview cards;
    - `locale`.
  - **No hard-coded data.** No company, value, year or status is hard-coded.
    Everything comes from the API.
  - **Previous UI.** The previous HTML/JS UI (`app/frontend/`) is kept,
    served at `/legacy/`, until the React UI is accepted. If no React build
    exists, `/app/` falls back to it.
- **CORS.** CORS is not needed when the UI is served by the API. To call the
  API from another origin, set `apiBase` and start the API with
  `BRSR_CORS_ORIGINS=http://host:port` (comma-separated). Only GET is
  allowed.
- **Screens:**
  - Overview: identity, filing, key-metric cards and workforce.
  - Environmental, Social (workforce grids and POSH) and Governance.
  - Trends: history of one metric.
  - Compare: several companies on one metric.
  - Data quality.
  - A details panel for every value: reported and normalised value and
    unit, status, basis, formula and factor, source filing, XBRL and PDF
    links, context, and review flags.
- **Display rules:**
  - A missing value is shown as "Not available" with its reason, never as
    0.
  - Values without a normalised unit are shown as filed and are never
    charted.
  - Charts are drawn only when every value is normalised in one unit, and
    the API reports the values as comparable.
- **Tests:**
  - `tests/test_analytics_api.py` runs on an isolated database.
  - `tests/test_frontend_react.py` runs the Vitest suite and checks that the
    React build is current.
  - `tests/test_frontend_ui.py` drives headless Chrome or Edge against the
    running API and compares every displayed value with the API response.
    Every test runs against both UIs, `/app` (React) and `/legacy`; set
    `BRSR_UI_PATHS` to choose. It is skipped when no browser or database is
    available.

## Tests

`venv/Scripts/python -m pytest` runs the whole suite offline. No test
downloads anything, writes to the production database or changes an
accepted file.

- **Controlled fixtures.** Discovery, XBRL parsing, unit conversion,
  validation rules and block validation are tested on synthetic inputs in
  temporary folders (`tests/test_phase16_*.py`, `tests/test_xbrl_parser.py`).
  The sample instance `data/raw/nse_brsr_xml/sample_brsr.xml` is parsed end to
  end.
- **Real-corpus checks.** Other tests rebuild stages in memory
  (`write=False`) and compare the result with the accepted outputs.
- **Regression baseline.** `tests/fixtures/phase16_regression_baseline.json`
  freezes several things from the accepted state:
  - the SHA-256 of the approved values, the raw facts and every decision
    file;
  - the status distributions (the 39 filer scale-error records, the 39
    deferred issues, MtCO2e outcomes, crosswalk candidates, assurance and
    supplementary classifications);
  - the schema and row count of every deliverable.

  Rewrite it only after an owner-approved change:
  `python -m tests.test_phase16_regression --write-baseline`.
- **Database tests.** These run only in throw-away `brsr_phase14_pytest*`
  databases, which are created and dropped by the fixtures. They compare the
  production database fingerprint before and after, and are skipped when
  PostgreSQL is not reachable.
- **Shared builds.** The review workflow, factor model and production values
  are built once per session (`tests/conftest.py`). Each test gets a deep
  copy, and a build that was mutated fails the run.
- **Integrity guard.** At session start and end, `tests/conftest.py` hashes
  `data/outputs`, `data/reference`, `data/processed` (without `backups/`) and
  `data/raw`. Any changed, added or removed file fails the run.
  `BRSR_SKIP_INTEGRITY_GUARD=1` turns the guard off, for quick partial runs
  only.

## Pipeline (run from the project root with `venv/Scripts/python -m ...`)

| Step | Module | Output (`data/processed/...`) |
|---|---|---|
| 1. Filing discovery (NSE BRSR API, symbol history, coverage) | `app.scrapers.nse_brsr_filing_index --years 5` | `nifty100/nse_brsr_filing_index.csv`, `..._selection.csv`, `..._discovery_coverage.csv` |
| 2. Download XBRL (stored by ISIN; malformed files sanitised, originals kept) | `app.downloaders.xbrl_downloader --source index` | `data/raw/nse_brsr_xml/<ISIN>/`, `nifty100/nse_brsr_report_manifest.csv` |
| 3. Verify identity (ISIN/CIN) and true FY slot | `app.validators.brsr_report_verifier --years 5` | `nifty100/nse_brsr_report_verification.csv`, `..._coverage_matrix.csv`, `..._extraction_manifest.csv` |
| 4. Taxonomy tables (current + older versions) | `app.extractors.nse_brsr_taxonomy_mapping_builder [--taxonomy-dir --version --output-dir]` | `nse_brsr_taxonomy_*.csv`, `taxonomy_versions/<version>/` |
| 5. Extract facts | `app.extractors.nse_brsr_multi_company_preview --manifest data/processed/nifty100/nse_brsr_extraction_manifest.csv --output-dir data/processed/nifty100` | `nifty100/multi_company_facts.csv` |
| 6. Taxonomy crosswalk + review decisions | `app.extractors.nse_brsr_taxonomy_crosswalk`, `app.extractors.nse_brsr_crosswalk_review` | `taxonomy_versions/nse_brsr_concept_crosswalk*.csv`, `data/reference/brsr_crosswalk_review_decisions.csv` |
| 7. Requirement values and coverage | `app.extractors.brsr_requirement_extractor` | `nifty100/brsr_requirement_values*.csv`, `..._coverage.csv` |
| 8. Unit normalisation (factor-model engine) | `app.normalisation.factor_model` (or `app.extractors.brsr_unit_normaliser`) | `nifty100/normalised/`, `nifty100/normalised_factor_model/` |
| 9. Normalisation validation | `app.validators.brsr_normalisation_validator` | `nifty100/normalised/brsr_normalisation_validation_issues.csv` etc. |

`app.validators.brsr_mtco2e_decisions` records MtCO2e unit decisions; it never
overwrites existing decisions in `data/reference/brsr_mtco2e_unit_decisions.csv`.

## Reference and decision files (`data/reference/`)

- `brsr_requirement_concept_map.csv` – final-goal requirements -> taxonomy concepts
- `brsr_crosswalk_review_decisions.csv` – approved spelling corrections etc.
- `brsr_mtco2e_unit_decisions.csv` – approved MtCO2e interpretations (audit trail)

## Review folders

- `data/processed/nifty100/filer_scale_error_review/` – investigation of filer
  scale errors; `approval/final_review/` holds the pending approval report.
  Nothing there is applied.

## Company master

`app.scrapers.nse_constituent_collector`, `nse_equity_master_lookup`,
`bse_company_lookup`, `update_bse_codes`; database code in `app/database/`.
`app/scrapers/mca_scraper.py` and `bse_scraper.py` are placeholders for the
MCA (governance) and BSE (fallback filings) sources.

## Supplementary sources (not the primary extraction path)

Annual reports, CSR reports, BRSR PDFs and investor-relations websites are
supplementary evidence only (e.g. governance and total CSR spend, which BRSR
XBRL does not disclose): `app/scrapers/report_discovery.py`,
`app/scrapers/pdf_link_scraper.py`, `app/downloaders/pdf_downloader.py`,
`app/extractors/pdf_extractor.py`, `app/services/report_service.py`,
`app/company/company_mapper.py`, `app/company/company_website_status.py`.
Raw PDFs: `data/raw/Reliance/`, `data/raw/Reliance Industries Limited/`.

API routes for a future dashboard: `app/api/`.

## Historical 20-company RSS run

Raw XBRL in `data/raw/nse_brsr_xml/<COMPANY_NAME>/` and outputs in
`data/processed/multi_company_*.csv`, `nse_brsr_*preview*.csv` etc. are kept
for reference and recalculation of the historical figures.

## Archived

Out-of-scope items (LLM chatbot and semantic search, embeddings, derived PDF
text, exploration scripts, empty stubs, superseded logs) are in
`C:\brsr_data_scraper_archive\cleanup_20261111\`. `RESTORE_MANIFEST.csv` lists
every item with its status (ARCHIVED / RESTORED) and reason.
