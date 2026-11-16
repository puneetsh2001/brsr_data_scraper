# Architecture Assessment and Phase Plan

Assessment date: 2026-11-11. All figures below were recalculated from the
project files; none are carried over from earlier summaries.

## 1. Recalculated figures

| Dataset | Figure |
|---|---|
| Historical 20-company RSS run (`data/processed/multi_company_facts.csv`) | 20 companies, 20 ISINs, 35,449 facts, 929 metrics, 876 context IDs (11,038 file+context pairs), 0 blank values |
| Nifty 100 run (`data/processed/nifty100/multi_company_facts.csv`) | 98 companies, 415 reports, 857,776 facts, 1,096 raw concepts / 1,072 current-taxonomy concepts |
| Facts by FY slot | 2021-22: 67,791 · 2022-23: 166,389 · 2023-24: 200,511 · 2024-25: 210,846 · 2025-26: 212,239 |
| Period roles | CY 680,116 · PY 174,348 · PPY 3,312 |
| Taxonomy match | 857,772 matched to their own taxonomy version; 4 unmatched (TVSMOTOR FY23-24 `UnitOfNox`/`UnitOfSox` - old concept names in a 2024 filing) |
| Crosswalk status | unchanged 839,588 · mapped 11,238 · mapped (approved spelling fix) 6,068 · candidate review 595 · no current equivalent 287 |
| Missing unitRef on numeric facts | 0 of 503,694 |
| Empty / nil facts | 25 (kept, flagged EMPTY) |

## 2. Previously reported issues - current status

| # | Issue | Status |
|---|---|---|
| 1 | Taxonomy download HTTP 530 | NSE archive CDN is intermittent (HEAD returned 503 on 2026-11-11; GET earlier succeeded). SEBI namespace URLs are not download locations (404). Mitigation: local cache of all 5 versions (already present) + retry/backoff (Phase 2). |
| 2 | Empty labels / documentation | Verified: SEBI label linkbases contain **no documentation labels in any version** (roles present: label, terse, total, period start/end). Standard labels are complete except 3 concepts in 2021-09-30. Documentation must be reported as "not provided by SEBI", never fabricated. |
| 3 | RSS discovery / history | Solved for NSE: JSON API with symbol history and submission-date windows covers FY2021-22 to FY2025-26. Universe currently limited to the Nifty 100 list - must become configurable (Phase 3). |
| 4 | Missing unit references | 0 in current data; the Phase 6 parser (`app.parsers.xbrl_instance`) flags `NUMERIC_FACT_WITHOUT_UNIT`, `UNDEFINED_UNIT` and `UNIT_TYPE_MISMATCH` (0 / 0 / 0 across 415 filings). |
| 5 | `Non-SI:J` reinterpretation | Identified (13 strong + 2 moderate records); factor model defined in the final review report; enforced by `app.normalisation.factor_model` (Phase 8): the informational 1e9 factor is never applied (multiplier 1), and nothing is applied until approved. |
| 6 | Scale interpretation | 105 cases investigated (48 confirmed, 19 unit-tag, 35 false positives, 3 possible); 39 strong recommendations pending owner approval. |
| 7 | Legacy factor on original values | Resolved in the final review model (`actual_numeric_multiplier_on_original`); enforced in Phase 8: the multiplier applies to the original value only, and the legacy factor is recorded as `legacy_factor_not_applied`. |
| 8 | Absolute restatement threshold | Fixed: relative tolerance in validator C08 and MtCO2e evidence. |
| 9 | Changing counts | All counts above recalculated. |

## 3. Existing database (read-only check)

`brsr_db` (PostgreSQL 18.6, extensions plpgsql + vector):

| Table | Rows | Origin |
|---|---|---|
| `companies` | 100 | company master (Nifty 100) |
| `brsr_reports` | 4 | Reliance BRSR PDFs (old PDF pipeline) |
| `brsr_chunks` | 1,000 | PDF text chunks with embeddings (old PDF pipeline) |

These tables are preserved. The new platform schema is added alongside them,
never by altering or truncating them.

## 4. Requirement vs existing implementation

| Master-prompt area | Existing (reuse) | Gap |
|---|---|---|
| 3.1 NSE discovery | `scrapers/nse_brsr_filing_index.py` (API, symbol history, coverage), `downloaders/xbrl_downloader.py` (index + RSS modes, sanitising, sha256) | configurable universe beyond Nifty 100; filing registry with versions and processing status |
| 3.2 BSE | - (`bse_scraper.py` empty); BSE scrip master lookup exists | BSE source adapter, cross-exchange de-duplication |
| 3.3 MCA | - (`mca_scraper.py` empty) | adapter within legitimate access; document limits |
| 3.4 Supplementary | `report_discovery.py`, `pdf_link_scraper.py`, `pdf_downloader.py`, `pdf_extractor.py`, `services/report_service.py` | evidence register linking supplementary documents to metrics |
| 5 Taxonomy | `extractors/nse_brsr_taxonomy_mapping_builder.py` (5 versions; labels, presentation, definition), crosswalk + review decisions | taxonomy registry metadata (source URL, hash, timestamp, FY applicability); calculation linkbase |
| 6 XBRL parser | `extractors/nse_brsr_multi_company_preview.py` (contexts, explicit/typed dims, nil, period roles, crosswalk) | fact IDs; precision/scale/sign attributes; unit measures in the fact table; structured dimensions table; duplicate-fact detection at extraction |
| 7 Company / filing mgmt | identity master, ISIN/CIN verification (`validators/brsr_report_verifier.py`), symbol history, demergers by CIN | unified company master incl. the 20 RSS companies; filing registry with original/revised versions |
| 8 Metric registry / units | `data/reference/brsr_requirement_concept_map.csv` (103 rows), `validators/unit_converter.py`, `extractors/brsr_unit_normaliser.py` | full metric registry (accepted units, validation rules, applicability); explicit factor-model fields in the engine |
| 9 Validation | `validators/brsr_normalisation_validator.py` (C01-C11: recompute, consistency, sums, intensity, restatement, YoY) | social and governance rules, structural checks, POSH/CSR balances, board totals |
| 10 Anomalies / approvals | scale-error investigation and approval reports (scripts currently outside the project) | anomaly engine inside the project; approval workflow that applies only approved corrections |
| 11 Block validation | BLOCK_PARTIAL logic in the approval report | general block validator for all metric blocks |
| Pillar 5 Assurance | assurance concepts mapped (V01-V08), MtCO2e decisions | YES / NO / UNKNOWN flag, per-attribute coverage |
| 12 PostgreSQL | `database/connection.py` (env vars), `models.py` (3 tables) | full schema, migrations, staging loader, idempotent upserts |
| 13 API | `api/company_routes.py` | metric / comparison / quality queries |
| 15 CLI | separate module entry points | single CLI with incremental processing |
| 16-17 Reports / docs | coverage matrix, requirement coverage, normalisation reports, README | the 20 named outputs and 6 documentation files |
| 18 Tests | none | unit + fixture tests (`data/raw/nse_brsr_xml/sample_brsr.xml` available) |
| 19 Operations | retries in downloader/discovery, `.env` | settings module, logging, rate limiting, `.env.example` |

## 5. Phase plan

Each phase ends with a short report (done / next) and a pause for review.

| Phase | Scope |
|---|---|
| 1 | Architecture assessment (this document) |
| 2 | Foundation: settings, `.env.example`, logging, HTTP client with retry/backoff/cache |
| 3 | Company master + filing registry (Nifty 100 + 20 RSS companies, versions, statuses, configurable universe) |
| 4 | BSE and MCA source adapters (legitimate access only, limitations documented) |
| 5 | Taxonomy registry (metadata, hashes, FY applicability, calculation linkbase) |
| 6 | XBRL parser upgrade (fact IDs, units, precision/scale/sign, dimensions table, duplicates) |
| 7 | Metric registry + concept mapping for all five pillars |
| 8 | Normalisation engine with the explicit factor model |
| 9 | Validation rules: structural, environmental, social, governance, intensity, restatement |
| 10 | Block validation |
| 11 | Anomaly engine + reviewer approval workflow (integrating the scale-error work) |
| 12 | Assurance extraction (YES/NO/UNKNOWN, attribute coverage) |
| 13 | Supplementary-source evidence (annual / CSR reports) |
| 14 | PostgreSQL schema, migrations, staging loader (no load without approval) |
| 15 | Reports, outputs and documentation |
| 16 | Automated tests |
| 17 | Full pipeline run, independent validation, final completion report |
