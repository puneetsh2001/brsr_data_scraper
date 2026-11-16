# Operations and maintenance

This runbook explains how to run, verify and maintain the BRSR data
platform. It complements:

- [PROCESSING_ARCHITECTURE.md](PROCESSING_ARCHITECTURE.md): what each stage
  does;
- [DATA_DICTIONARY.md](DATA_DICTIONARY.md): what each output column means.

All commands run from the project root with the project virtual environment
(`venv/Scripts/python` on Windows).

## 1. Setup

1. Install the dependencies: `pip install -r requirements.txt`.
2. Copy `.env.example` to `.env` and fill in the database connection.
   - `DB_NAME` names the **production** database.
   - Never commit `.env`.
3. Optional settings (see `app/config/settings.py`):
   - `FIRST_FY_SLOT` and `DISCOVERY_YEARS`;
   - the company filter;
   - the data, log and cache folders.

## 2. Everyday commands (`python -m app.pipeline …`)

| Command | Effect | Default |
|---|---|---|
| `status` | registry counts, incremental plan, open deferred issues | read only |
| `discover` / `download` | NSE discovery, XBRL download of filings flagged `needs_download` | dry run; network only with `--execute` |
| `refresh-taxonomy` | download and rebuild the SEBI taxonomy tables | dry run; **needs owner approval** |
| `process` / `validate` | the 8 processing / 3 validation stages, incremental | dry run; backup of `data/processed` before `--execute` |
| `reprocess-full-corpus` | every process and validate stage, forced, for all in-scope filings | dry run; backup before `--execute` |
| `report` | builds `data/outputs/` and `docs/DATA_DICTIONARY.md` | staged, verified, backed up, then replaced |
| `load-db --database brsr_phase14_<name>` | stage (and `--promote`) into a **test** database | dry run; production writes are refused |

How incremental runs work:

- A stage runs only when its code, its arguments or the content of its
  inputs changed, when an output is missing, or when an upstream stage ran.
- `status --record-state` records the current outputs as the baseline.
- Selective per-company or per-FY processing is not supported, because the
  outputs are corpus-wide and several rules compare years. The old
  `reprocess` name is refused.

## 3. Typical workflows

### New filings (for example a new FY)

1. `discover --execute`
2. `download --execute`
3. `process --execute`
4. `validate --execute`
5. `report`
6. Run the tests (section 6).

Always review the dry-run plan before adding `--execute`.

### A reviewer decision

1. Add a row to `data/reference/review_decisions.csv`, the append-only log.
   Each row needs a reviewer, an ISO timestamp and an audit reference.
2. Run `validate --execute`, then `report`.
3. Only filer scale-error (FSE) records have an apply path.
4. The engine refuses a decision in any of these cases:
   - it is stale;
   - the approval is incomplete;
   - it touches a block that is only partly corrected (`BLOCK_PARTIAL`);
   - the record is inconsistent.
5. Recommendations are never applied by themselves.

### Taxonomy refresh

Do this only with owner approval:

1. `refresh-taxonomy --execute`
2. `reprocess-full-corpus --execute`
3. `report`
4. Run the tests.

### Production database import

This is blocked by design and needs all three:

- the owner's approval;
- all 39 deferred issues resolved (`app.core.deferred_issues.assert_none_open()`);
- `PRODUCTION_WRITES_ENABLED` changed in `app/db/connection.py`.

Until then, load only into `brsr_phase14_*` test databases.

## 4. Backups and recovery

| Backup | Location |
|---|---|
| Before executed `process`, `validate` or `reprocess-full-corpus` | `data/processed/nifty100/backups/<timestamp>_pre_<command>/processed/` |
| Before `report` replaces outputs | `data/processed/nifty100/backups/<timestamp>_pre_report_outputs/` |

- Every copy is SHA-256 verified and listed in `backup_sha256.txt`.
- **Recover:** copy the backup folder back over the live folder, then check
  each file against `backup_sha256.txt`.
- **Failed runs:**
  - A failed stage records no state, so the next run retries it.
  - A failed report leaves `data/outputs/` unchanged. If the failure happened
    during replacement, the outputs are restored from the verified backup.
- **Raw files:** raw XBRL under `data/raw/` is never modified or deleted. A
  malformed file is sanitised into a copy.

## 5. Verifying a run

1. **Reproducibility.** The Phase 17 isolated full re-run reproduced all 975
   project data files byte for byte. The only exceptions were the timestamps
   in `data/outputs/manifest.json` and `data/processed/pipeline_state.json`.
   - A re-run on unchanged inputs must give the same result.
   - Any other difference is a defect, or the effect of a new decision.
2. **Manifest.** `data/outputs/manifest.json` lists the SHA-256 and row count
   of every output and every source.
3. **Independent validation.** The Phase 17 checker uses lxml and pandas only,
   with no pipeline code:
   - it re-parses every in-scope XBRL instance and compares every fact;
   - it recomputes `final = original × multiplier` and checks it against an
     independent factor table;
   - it reconciles the pillar, coverage and missing reports;
   - it confirms that no pending decision was applied.

## 6. Tests

`python -m pytest`. The full suite takes about 10 minutes. See the README
section "Tests".

- **Integrity guard.** At the start and end of the session it hashes
  `data/outputs`, `data/reference`, `data/processed` (without backups) and
  `data/raw`. Any change fails the run.
- **Regression baseline.** `tests/fixtures/phase16_regression_baseline.json`
  freezes the approved state. Regenerate it only after an owner-approved
  change: `python -m tests.test_phase16_regression --write-baseline`.
- **Database tests.** These use throw-away `brsr_phase14_pytest*` databases
  only.

## 7. Rules that must not be broken

- Missing information is never zero. Units are never guessed from the size of
  a value, and values are never invented.
- Recommendations are not approvals. Pending, deferred and candidate records
  stay visible and labelled.
- Assessment, assurance, limited assurance and reasonable assurance stay
  distinct. Provider spelling variants are never merged automatically.
- Supplementary PDF evidence is a separate layer and never replaces XBRL
  values. Supplementary PDFs are not downloaded (owner decision).
- Access controls are never bypassed: adapters stop at HTTP 403 and CAPTCHAs.

## 8. Known open items (owner decisions)

- 39 filer scale-error records are pending; 25 are recommended APPROVE and 14
  DEFER.
- There are 39 deferred data-quality issues.
- Unit assumptions:
  - 474 MtCO2e assumptions are pending review;
  - 35 are undetermined;
  - 20 are flagged because the restatement differs.
- 230 crosswalk-candidate cells are pending, from 7 candidate mappings. V01 is
  not approved.
- There is no conversion rule for `kgCO2e`. No filing uses it, and adding one
  needs a decision.
- The production database import has not been performed.
