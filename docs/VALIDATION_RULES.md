# Validation rules

Engine: `python -m app.validation.rules_engine` (Phase 9).

- **Input:** the production normalisation in
  `data/processed/nifty100/normalised/`. Since the switch on 2026-11-12, it
  is written by the factor-model engine.
- **Outputs:** written to `data/processed/nifty100/validation/`.
- **No corrections:** the engine never corrects anything. Every finding is
  reported for review.

## Principles

- **Rule results:** each new rule records `PASS`, `FAIL` or `NOT_EVALUATED`,
  and the reason is always given.
  - A missing value is never treated as zero.
  - A missing value never counts as a pass.
- **Tolerances:** all tolerances are relative, with no absolute floors.
  - Sums and inequalities: 1 %.
  - Recomputed percentage shares: ±0.5 percentage points.
  - Intensities (legacy C06): 10 %.
- **Legacy checks C01–C08, C10 and C11** of
  `app.validators.brsr_normalisation_validator` run unchanged through an
  adapter.
  - Their issues are reconciled with the existing issues file in
    `legacy_check_reconciliation.csv`. Every issue must match.
  - C09 (MtCO2e evidence) produces decision evidence and stays in the
    legacy validator.
- **Deferred owner decisions are linked, not acted on.**
  - Values linked to `data/reference/deferred_issues_register.csv` carry
    `deferred_issue_ids`.
  - Restatement notes on `OtherStakeHolderAxis` rows keep the legacy
    row-position result. They are tagged `IDENTITY_UNRELIABLE … (DQ-039)`.
  - Name-based matching for these rows is deferred to the owner.

## Rules

| Rule | Category | Severity on fail | Legacy | Description |
|---|---|---|---|---|
| STR01 | Structural | ERROR | C01 | final value = original × multiplier (exact Decimal) |
| STR02 | Structural | ERROR | C02 | converted rows carry value, unit, factor and formula; unconverted rows carry no value |
| STR03 | Structural | ERROR | C03 | one normalised unit per requirement |
| STR04 | Structural | WARNING | C04 | no duplicate company / FY / requirement / breakdown values |
| STR05 | Structural | WARNING | C05 | no negative quantities; percentages not above 100 |
| STR06 | Structural | ERROR | C11 | the same ambiguous unit symbol is read the same way within one report |
| STR07 | Structural | ERROR | | every value has a source file, context and URL, and its filing is in the registry processing scope |
| STR08 | Structural | ERROR | | count metrics are non-negative integers |
| STR09 | Structural | INFO | | the value is linked to an open deferred data-quality issue |
| ENV01 | Environmental | WARNING | C07 | E07 = E05 + E06; E14 = E09…E13; E29 = E21…E28 |
| ENV02 | Environmental | WARNING | | renewable energy E05 ≥ E01 + E03 (other renewable sources may add) |
| ENV03 | Environmental | WARNING | | non-renewable energy E06 ≥ E02 + E04 |
| ENV04 | Environmental | WARNING | | water consumption E15 ≤ withdrawal E14 |
| ENV05 | Environmental | WARNING | | waste recovered E32 ≥ E30 + E31 |
| ENV06 | Environmental | WARNING | | waste disposed E35 ≥ E33 + E34 |
| ENV07 | Environmental | WARNING | | implied Scope 1+2 emission factor (E17 + E18) / E07 within 0.005–1.0 tCO2e/GJ |
| SOC01 | Social | WARNING | | gender total = male + female + other gender (S01, S02, S15) |
| SOC02 | Social | WARNING | | female wage share S09 = S07 / S08 × 100 |
| SOC03 | Social | WARNING | | POSH complaints upheld S18 ≤ reported S17 |
| SOC04 | Social | WARNING | | POSH pending at year end S20 ≤ filed S19 + pending at start (same report PY) |
| GOV01 | Governance | ERROR | | female directors G02 ≤ total directors G01 |
| GOV02 | Governance | WARNING | | female board share G03 = G02 / G01 × 100 |
| GOV03 | Governance | WARNING | | stakeholder complaints pending G17 ≤ filed G16 + opening pending, per group |
| GOV04 | Governance | WARNING | | consumer complaints pending G19 ≤ received G18 + opening pending, per category |
| INT01 | Intensity | WARNING | C06 | reported per-INR intensity vs total / turnover (E08, E16, E19) |
| INT02 | Intensity | ERROR | | converted intensities carry a per-INR unit |
| RST01 | Restatement | INFO / ERROR | C08 | value vs the next report's restated comparative (ERROR when the ratio looks like a scale factor) |
| RST02 | Restatement | WARNING | C10 | year-on-year change above 10× |

### About GOV03 / GOV04 / SOC04

These rules compare rows within **one** report: the CY row and the PY row of
the same table row. Typed `OtherStakeHolderAxis` rows are stable inside a
single report, so they are evaluated too. They are never compared across
reports by row position.

## Outputs

| File | Content |
|---|---|
| `validation_rules.csv` | The rule registry |
| `validation_results.csv` | Every evaluation: rule, status, severity, company, FY, requirement, breakdown, observed / expected values, detail, source file, deferred issue ids |
| `validation_issues.csv` | `FAIL` rows only |
| `validation_summary.csv` | PASS / FAIL / NOT_EVALUATED per rule |
| `legacy_check_reconciliation.csv` | Legacy-check issues: existing vs engine |

## Block validation (Phase 10)

Engine: `python -m app.validation.block_validation`. Outputs are written to
`data/processed/nifty100/blocks/`.

A block is a set of related metrics from one report. The metrics share a
unit or scale, or a component / total / intensity relationship. Each block
gets one status per company × FY slot. The statuses are listed below in
order of precedence (highest first):

| Status | Meaning |
|---|---|
| BLOCKED_PENDING_DECISION | A pending scale, unit or crosswalk decision, or an open deferred issue, touches the block |
| INCONSISTENT | A Phase 9 block rule failed. Restatement counts only at ERROR severity |
| INCOMPLETE | A member is not disclosed or not normalised, or a block rule could not be evaluated |
| CONSISTENT | All expected members are present and normalised, and every block rule passed |
| NOT_REPORTED | No report exists for the slot, or no member was reported |

"Not expected" means the member is NOT_APPLICABLE, NOT_IN_TAXONOMY_VERSION or
EXTERNAL_SOURCE_REQUIRED. Such members do not make a block incomplete.

Blocks and their members:

| Block | Members |
|---|---|
| ENERGY | E01–E08 |
| WATER | E09–E16 |
| EMISSIONS | E17–E20 |
| WASTE | E21–E35 |
| WORKFORCE | S01–S02 |
| WAGES | S03–S09 |
| SAFETY_TRAINING | S11–S16 |
| POSH | S17–S20 |
| BOARD | G01–G03 |
| STAKEHOLDER_COMPLAINTS | G16–G17 |
| CONSUMER_COMPLAINTS | G18–G19 |

`block_definitions.csv` lists the rules evaluated for each block.

`block_review_packages.csv` covers every block that contains a filer
scale-error record. Each package lists all members of the block, so a
reviewer can decide the whole block at once. A package marked
`BLOCK_PARTIAL` contains related values that have no record of their own
(`needs_decision = True`). The factor model never applies a partial block.
