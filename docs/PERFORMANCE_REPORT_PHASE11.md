# Test-suite performance investigation (before Phase 11)

Date: 2026-11-12

Trigger: one full run after the production switch took 23 min 40 s (177
tests). Earlier full runs took about 3 min.

## Measurements (same code, same 177 tests)

| Run | Result | Runtime |
|---|---|---|
| Immediately after the production switch | 177 passed | 1420 s (23:40) |
| Profiling run (`--durations=40`) | 177 passed | 109 s (1:49) |
| Confirmation run | 177 passed | 125 s (2:05) |

The slowdown does not reproduce. Normal runtime is about 2 minutes, which
is faster than before the switch.

## Slowest tests (profiling run)

| Duration | Test / fixture | Operation |
|---|---|---|
| 56.4 s | `test_validation_rules` module fixture | `rules_engine.build()`. The legacy check C04 (duplicate groupby over 90k rows) inside `brsr_normalisation_validator.row_checks` takes most of this. |
| 11.1 s | `test_factor_model` module fixture | `factor_model.build(write=False)`: parses 415 instances' unit definitions and runs 134k rows |
| 6.8 s | `test_metric_registry` fixture | `metric_registry.build()`: reads the parsed facts (437 MB) in chunks |
| 4.1 s | `test_block_validation` fixture | `block_validation.build()` |
| 3.5 s | `test_stakeholder_review` fixture | `stakeholder_complaints_review.build()` |
| ≤2.5 s each | `test_no_existing_file_changed` (×4) | SHA-256 of 128–152 snapshot files, including the 437 MB parsed facts |
| 1.3 s | `test_production_switch` (CY projection) | Reads the 90k × 55 production file |

## Findings

- **New production reader.** `read_production()` adds 14 alias columns. It
  costs about 0.3 s per read and is not the cause.
- **Production-switch tests.** They take about 3 s in total and are not the
  cause.
- **Cause of the 23-minute run.**
  - The run started right after about 100 MB of freshly written production
    CSVs were created.
  - In the same period, a shell `sha256sum` of a small file
    (`validation_issues.csv`) hung for more than 10 minutes. This means
    file access itself was stalled at that time.
  - Both events point to OS-level file contention: on-access scanning or
    indexing of newly written large files. They do not point to the code.
  - Repeated runs, with no code change, take about 2 minutes.
- **Repeated work.** Each phase's module fixture builds its engine once
  (`scope="module"`). There is no repeated build per test.
- **Snapshot tests.** The four snapshot tests each hash the same large
  files. Together they add about 7 s. That is acceptable, and keeping them
  preserves the integrity guarantees.

## Decisions

- No test was removed or weakened.
- No production code changed for performance.
- Possible future optimisation, not needed now: vectorise legacy C04. This
  must only be done with byte-identical issue reconciliation.
- **No correctness, data-integrity or production issue was found.**
