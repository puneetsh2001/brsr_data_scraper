"""
Data dictionary for the Phase 15 deliverables (data/outputs/).

COLUMNS documents every column that appears in any output file:
    name -> (type, unit, meaning, allowed values / statuses)
FILES documents every file: grain, source and how it is produced.

A test fails if an output column is not documented here or if
docs/DATA_DICTIONARY.md is out of date.

Run:
    python -m app.reporting.data_dictionary      (writes the markdown)
"""

from pathlib import Path

import pandas as pd

from app.config import settings

OUT = settings.data_dir / "outputs"
DOC = settings.project_root / "docs" / "DATA_DICTIONARY.md"

T, N, I, B, D, J = "text", "decimal text", "integer", "boolean", "date", \
    "list"

# ----------------------------------------------------------------------
# Files
# ----------------------------------------------------------------------
FILES = {
    "company_master.csv": (
        "one row per company (unique company_id and nse_symbol) for the "
        "whole discovered registry, not only the Nifty 100: every NSE BRSR "
        "filer (1,340) plus the universe companies (1,342 rows). Filter "
        "in_processing_universe = True for the 100 Nifty 100 companies; 29 "
        "merged / delisted filers have company_id C-SYM-<symbol> and no "
        "ISIN", "registry/company_master.csv", "exact copy (Phase 3)"),
    "filing_manifest.csv": (
        "one row per filing version of every registered company (4,923); "
        "filter in_processing_scope = True for the 417 latest valid "
        "in-scope filings (98 companies x 5 FY slots, minus gaps)",
        "registry/filing_manifest.csv", "exact copy (Phase 3)"),
    "taxonomy_concepts.csv": (
        "one row per taxonomy version x concept",
        "nse_brsr_taxonomy_concepts.csv (current) and taxonomy_versions/"
        "<v>/ (older)", "union of the 5 versions with taxonomy_version "
        "added (Phase 5)"),
    "concept_metric_mapping.csv": (
        "one row per metric x taxonomy version (+ one row per non-XBRL "
        "metric)", "metrics/metric_version_availability.csv, "
        "metric_concepts.csv, reference/brsr_requirement_concept_map.csv",
        "join; crosswalk status as approved/pending (Phase 7)"),
    "all_brsr_facts.csv": (
        "one row per XBRL fact", "parsed/xbrl_facts.csv",
        "exact copy of the raw parsed facts (Phase 6)"),
    "normalised_brsr_facts.csv": (
        "one row per requirement value (CY then PY)",
        "normalised/brsr_requirement_values_normalised.csv + "
        "_py_normalised.csv", "concatenation of production values "
        "(factor model, Phase 8); nothing recalculated"),
    "environmental_metrics.csv": (
        "one row per CY requirement value, pillar Environmental",
        "production CY + validation + review queue",
        "ALL rows incl. pending / deferred / candidate / unresolved, with "
        "status columns"),
    "social_metrics.csv": (
        "one row per CY requirement value, pillar Social", "as above",
        "as above"),
    "governance_metrics.csv": (
        "one row per CY requirement value, pillar Governance", "as above",
        "as above"),
    "corporate_identity_metrics.csv": (
        "one row per CY requirement value, pillar Corporate", "as above",
        "as above"),
    "assurance_metrics.csv": (
        "one row per CY requirement value, pillar Assurance",
        "as above + assurance/assurance_engagements.csv",
        "as above, plus filing-level assurance fields from Phase 12 kept in "
        "separate columns"),
    "metric_coverage_report.csv": (
        "one row per company x FY slot x requirement (100 x 5 x 102)",
        "brsr_requirement_coverage.csv + filing manifest + production CY",
        "pipeline status kept; coverage_status refined only by evidence"),
    "missing_metrics_report.csv": (
        "rows of metric_coverage_report whose coverage_status is not "
        "REPORTED / REPORTED_ZERO",
        "metric_coverage_report + supplementary/external_requirement_"
        "coverage.csv", "supplementary candidates shown as unverified"),
    "unmapped_concepts_report.csv": (
        "one row per taxonomy version x parsed concept that is unmapped, "
        "not in the crosswalk, without a current equivalent or pending",
        "parsed facts, crosswalk, metric availability, parse issues",
        "counts only"),
    "unit_validation_report.csv": (
        "one row per numeric CY requirement value",
        "production CY (factor-model columns)", "all unit / factor columns"),
    "data_quality_report.csv": (
        "one row per review recommendation", "review_workflow/review_queue."
        "csv", "exact copy (Phase 11)"),
    "block_validation_report.csv": (
        "one row per company x FY x block", "blocks/block_status.csv",
        "exact copy (Phase 10)"),
    "scale_error_recommendations.csv": (
        "one row per filer scale-error record (39)",
        "final_review/strong_recommendations_final_review.csv + production "
        "+ block packages", "recommendations only; none approved"),
    "restatement_validation_report.csv": (
        "one row per RST01 / RST02 result", "validation/validation_results."
        "csv + stakeholder reconciliation 08",
        "name-based re-evaluation shown alongside; not applied (DQ-039)"),
    "company_year_coverage_summary.csv": (
        "one row per company x FY slot", "metric_coverage_report, parsed "
        "facts, production, validation, review queue",
        "section 16 coverage summary; counts of metrics, not facts"),
    "final_processing_summary.csv": (
        "one row per measure", "all of the above", "pipeline totals"),
}

# ----------------------------------------------------------------------
# Status vocabularies
# ----------------------------------------------------------------------
COVERAGE_STATUS = {
    "REPORTED": "a non-zero CY value was extracted from XBRL",
    "REPORTED_ZERO": "the filer reported the value 0 (an explicit zero)",
    "REPORTED_PENDING_CROSSWALK_REVIEW": "values exist only through a "
    "candidate crosswalk mapping awaiting review; not counted as reported",
    "NOT_DISCLOSED": "the concept exists in the filing's taxonomy version "
    "but the filing has no CY fact for it (PY-only facts do not count)",
    "EXTRACTION_ERROR": "evidence of a pipeline failure: the filing exists "
    "but could not be downloaded or parsed, or a CY fact is present but no "
    "value was selected",
    "NOT_IN_TAXONOMY_VERSION": "the filing's taxonomy version has no "
    "concept for this metric",
    "NO_REPORT": "no BRSR filing in this FY slot",
    "EXTERNAL_SOURCE_REQUIRED": "metric is not in BRSR XBRL (annual report, "
    "MCA, etc.)",
    "NOT_APPLICABLE": "applicability concept says the metric does not apply",
}
RECORD_STATUS = {
    "DEFERRED_DATA_QUALITY_ISSUE": "linked to an open deferred issue "
    "(DQ-xxx)",
    "PENDING_CROSSWALK_REVIEW": "value from a candidate crosswalk mapping",
    "PENDING_SCALE_REVIEW": "filer scale-error record pending; original "
    "value kept",
    "PENDING_UNIT_DECISION": "unit assumption awaiting approval; not "
    "converted",
    "NOT_NORMALISED_UNIT_AMBIGUOUS": "unit could not be determined",
    "NOT_NORMALISED_PERCENT_SCALE_AMBIGUOUS": "percent vs fraction unclear",
    "NOT_NORMALISED_TEXT_TYPE": "text in a numeric concept",
    "TEXT_VALUE": "text concept; no normalisation applies",
    "NORMALISED_APPROVED_UNIT_DECISION": "converted under an approved unit "
    "decision",
    "NORMALISED_APPROVED_SCALE_CORRECTION": "converted under an approved "
    "scale correction",
    "NORMALISED_FROM_XBRL": "converted by rule or no conversion needed",
}
VALIDATION_STATUS = {
    "FAILED_RULES": "at least one Phase 9 rule failed for this value",
    "OPEN_REVIEW_ITEMS": "no failure, but open review recommendations",
    "NO_FAILURE_RECORDED": "no failed rule and no open review item "
    "(not a verification)",
}
REVIEW_STATUS = "DETECTED | RECOMMENDED | PENDING_REVIEW | APPROVED | " \
    "REJECTED | DEFERRED | APPLIED"


def _v(d):
    return " | ".join(d)


# ----------------------------------------------------------------------
# Columns
# ----------------------------------------------------------------------
COLUMNS = {
    # identity
    "company_id": (T, "", "registry company id (C-<ISIN>)", ""),
    "company_name": (T, "", "company name", ""),
    "legal_name": (T, "", "legal name (MCA / NSE)", ""),
    "common_name": (T, "", "common / short name", ""),
    "cin": (T, "", "corporate identification number", "blank when unknown"),
    "cin_status": (T, "", "CIN verification status", "registry vocabulary"),
    "isin": (T, "", "ISIN (registry key)", ""),
    "master_isin": (T, "", "ISIN from the company master", ""),
    "nse_symbol": (T, "", "current NSE symbol", ""),
    "master_nse_symbol": (T, "", "current NSE symbol from the company "
                          "master", ""),
    "nse_series": (T, "", "NSE series", ""),
    "nse_listing_date": (D, "", "NSE listing date", ""),
    "bse_scrip_code": (T, "", "BSE scrip code", "blank when unknown"),
    "bse_match_status": (T, "", "BSE match outcome", "incl. "
                         "IDENTIFIER_CONFLICT"),
    "industry": (T, "", "industry", ""),
    "incorporation_date": (D, "", "date of incorporation", ""),
    "registered_office": (T, "", "registered office address", ""),
    "corporate_address": (T, "", "corporate address", ""),
    "email": (T, "", "company contact email (public filing)", ""),
    "telephone": (T, "", "company telephone (public filing)", ""),
    "website": (T, "", "company website", ""),
    "company_status": (T, "", "listing status", ""),
    "in_processing_universe": (B, "", "company is in the Nifty 100 "
                               "universe", "True | False"),
    "in_processing_scope": (B, "", "row is processed (company: in scope; "
                            "filing: latest valid version in scope)",
                            "True | False"),
    "is_nse_brsr_filer": (B, "", "NSE lists BRSR filings for the company",
                          "True | False"),
    "in_rss_historical_run": (B, "", "seen in the historical RSS run",
                              "True | False"),
    "symbol_history": (J, "", "previous symbols", "' | ' separated"),
    "name_history": (J, "", "previous names", "' | ' separated"),
    "latest_verified_report": (T, "", "latest verified BRSR report", ""),
    "latest_verified_fy": (T, "", "FY of the latest verified report", ""),
    "discovery_sources": (J, "", "sources that discovered the company", ""),
    "related_entities": (J, "", "related / merged entities", ""),
    # filing
    "filing_id": (T, "", "registry filing id (F-<hash>)", "blank when no "
                  "filing"),
    "filing_symbol": (T, "", "symbol on the filing", ""),
    "api_company_name": (T, "", "company name in the NSE API", ""),
    "api_fy_from": (T, "", "FY start in the NSE API", ""),
    "api_fy_to": (T, "", "FY end in the NSE API", ""),
    "fy_slot": (T, "", "financial year slot (e.g. 2023-24)",
                "2020-21 .. 2024-25"),
    "fy_slot_basis": (T, "", "how the FY slot was assigned", ""),
    "reporting_period_start": (D, "", "reporting period start", ""),
    "reporting_period_end": (D, "", "reporting period end", ""),
    "reporting_period": (T, "", "reporting period text", ""),
    "reporting_fy": (T, "", "reporting FY of the value", ""),
    "submission_date": (T, "", "NSE submission timestamp", ""),
    "revision_date": (T, "", "NSE revision timestamp", ""),
    "xbrl_url": (T, "", "NSE XBRL URL", ""),
    "original_filename": (T, "", "XBRL file name", ""),
    "pdf_url": (T, "", "NSE BRSR PDF URL (not downloaded)", ""),
    "xbrl_file_size_api": (I, "bytes", "file size reported by the API", ""),
    "local_file_path": (T, "", "local raw file path", ""),
    "sha256": (T, "", "SHA-256 of the raw XBRL file", ""),
    "filing_sources": (J, "", "sources that listed the filing", ""),
    "api_snapshots": (J, "", "API snapshots containing the filing", ""),
    "download_status": (T, "", "download status", "DOWNLOADED | FAILED | "
                        "..."),
    "download_note": (T, "", "download note", ""),
    "error_details": (T, "", "download / parse error", ""),
    "parsing_status": (T, "", "parsing status", ""),
    "processing_status": (T, "", "processing status", "incl. "
                          "EXTRACTED_CURRENT_PIPELINE, DOWNLOAD_FAILED"),
    "identity_status": (T, "", "entity identifier check", ""),
    "xbrl_entity_identifier": (T, "", "entity identifier in the XBRL", ""),
    "taxonomy_version": (T, "", "BRSR taxonomy version", "5 versions"),
    "is_voluntary_year": (B, "", "voluntary BRSR year", "True | False"),
    "version_number": (I, "", "filing version within the slot", ""),
    "versions_in_slot": (I, "", "number of versions in the slot", ""),
    "filing_version_status": (T, "", "version status", ""),
    "is_latest_valid": (B, "", "latest valid version", "True | False"),
    "needs_download": (B, "", "incremental flag: raw file missing / "
                       "changed", "True | False"),
    "needs_processing": (B, "", "incremental flag: not yet processed",
                         "True | False"),
    # taxonomy
    "concept_name": (T, "", "XBRL concept name", ""),
    "data_type": (T, "", "XBRL data type", ""),
    "abstract": (B, "", "abstract concept", "true | false"),
    "nillable": (B, "", "nillable", "true | false"),
    "substitution_group": (T, "", "substitution group", "item | tuple ..."),
    "period_type": (T, "", "duration or instant", "duration | instant"),
    "standard_label": (T, "", "standard label", ""),
    "documentation": (T, "", "documentation label", ""),
    "other_labels": (J, "", "other labels", ""),
    "presentation_roles": (J, "", "presentation roles", ""),
    "presentation_role_descriptions": (J, "", "role descriptions", ""),
    "parent_concepts": (J, "", "presentation parents", ""),
    # mapping
    "metric_id": (T, "", "requirement / metric id (e.g. E01)", ""),
    "pillar": (T, "", "BRSR pillar", "Corporate | Environmental | Social | "
               "Governance | Assurance"),
    "requirement": (T, "", "requirement description", ""),
    "metric": (T, "", "requirement description", ""),
    "metric_key": (T, "", "stable metric key", ""),
    "source": (T, "", "data source of the metric", "BRSR_XBRL | "
               "external sources"),
    "source_type": (T, "", "data source of the metric", "BRSR_XBRL | "
                    "external sources"),
    "concept": (T, "", "current-version concept", ""),
    "availability": (T, "", "metric availability in the version",
                     "AVAILABLE | PENDING_REVIEW | "
                     "NOT_IN_VERSION | NOT_IN_BRSR_XBRL"),
    "concept_in_version": (J, "", "concept name(s) in that version",
                           "' | ' separated"),
    "crosswalk_status": (T, "", "concept crosswalk status", "CURRENT | "
                         "UNCHANGED | MAPPED | MAPPED_APPROVED | "
                         "CANDIDATE_REVIEW | NO_CURRENT_EQUIVALENT | "
                         "NOT_IN_CROSSWALK | blank"),
    "crosswalk_approval": (T, "", "crosswalk approval status", "candidate "
                           "mappings remain unapproved"),
    "type_family": (T, "", "data type family", "numeric | text | ..."),
    "expected_unit": (T, "", "expected unit", ""),
    "expected_unit_family": (T, "", "expected unit family", ""),
    "unit_family": (T, "", "unit family", ""),
    "unit_type_consistent": (B, "", "unit matches the type", ""),
    "breakdown_axes": (J, "", "axes used for breakdowns", ""),
    "dimension_filter": (T, "", "dimension filter", ""),
    "applicability_concept": (T, "", "concept deciding applicability", ""),
    "current_concept": (T, "", "current-version concept", ""),
    "metric_concept": (T, "", "concept of the metric", ""),
    # facts
    "source_file": (T, "", "raw XBRL file name", ""),
    "fact_id": (T, "", "deterministic fact id", ""),
    "line": (I, "", "line in the XBRL file", ""),
    "xml_id": (T, "", "xml id attribute", ""),
    "namespace": (T, "", "concept namespace", ""),
    "context_id": (T, "", "XBRL context id", ""),
    "context_key": (T, "", "normalised context key", ""),
    "context_reference": (T, "", "XBRL context id of the value", ""),
    "entity_identifier": (T, "", "context entity identifier", ""),
    "period_start": (D, "", "period start", "blank for instants"),
    "period_end": (D, "", "period end", "blank for instants"),
    "instant": (D, "", "instant date", "blank for durations"),
    "instant_date": (D, "", "instant date", "blank for durations"),
    "dimension_key": (T, "", "axis=member list", ""),
    "dimensions": (T, "", "axis=member list", ""),
    "breakdown": (T, "", "breakdown (axis=member)", "blank = total"),
    "unit_id": (T, "", "XBRL unit id", ""),
    "unit_measure": (T, "", "XBRL unit measure", ""),
    "unit_reference": (T, "", "XBRL unit reference", ""),
    "value": (T, "", "fact value exactly as filed", "blank for nil"),
    "value_status": (T, "", "value status", "REPORTED | EMPTY (nil)"),
    "numeric_value": (N, "as filed", "numeric parse of value", "blank if "
                      "not numeric; never 0 for missing"),
    "sign": (T, "", "sign attribute", ""),
    "decimals": (T, "", "decimals attribute", ""),
    "precision": (T, "", "precision attribute", ""),
    "value_decimal_places": (I, "", "decimal places in the value", ""),
    "xml_lang": (T, "", "xml:lang", ""),
    "footnotes": (T, "", "linked footnotes", ""),
    "duplicate_group": (T, "", "duplicate fact group", ""),
    "duplicate_class": (T, "", "duplicate classification", ""),
    "issue_codes": (J, "", "parse issue codes", ""),
    # requirement values
    "requirement_id": (T, "", "requirement id", ""),
    "period_role": (T, "", "current year or prior-year comparative",
                    "CY | PY"),
    "metric_name": (T, "", "metric name", ""),
    "original_value": (T, "as filed", "value exactly as filed", "blank = "
                       "missing, never zero"),
    "original_unit_id": (T, "", "unit id as filed", ""),
    "original_unit": (T, "", "unit as filed", ""),
    "interpreted_original_unit": (T, "", "unit interpretation of the "
                                  "record", ""),
    "normalised_value": (N, "normalised_unit", "production value "
                         "(= original_value x conversion_factor)",
                         "equals original when not converted"),
    "normalised_unit": (T, "", "production unit", ""),
    "conversion_factor": (N, "", "multiplier applied to original_value",
                          "1 when not converted"),
    "conversion_formula": (T, "", "conversion formula", ""),
    "conversion_status": (T, "", "conversion status", "CONVERTED | "
                          "CONVERTED_FRACTION_TO_PERCENT | "
                          "CONVERTED_APPROVED_UNIT_ASSUMPTION | "
                          "NO_CONVERSION_NEEDED | NOT_NUMERIC | "
                          "NOT_CONVERTED_* | "
                          "PENDING_APPROVAL_UNIT_ASSUMPTION"),
    "conversion_note": (T, "", "conversion note", ""),
    "proposed_normalised_value": (N, "", "proposed value awaiting "
                                  "approval (not applied)", ""),
    "needs_review": (B, "", "value needs review", "True | False"),
    "unit_decision": (T, "", "MtCO2e unit decision", "APPROVE | REJECT | "
                      "DEFER | blank"),
    "unit_decision_evidence": (T, "", "evidence of the unit decision", ""),
    "rule_status": (T, "", "unit rule outcome", ""),
    "rule_factor": (N, "", "rule unit factor", ""),
    "rule_target_unit": (T, "", "rule target unit", ""),
    "unit_assumption_outcome": (T, "", "unit assumption outcome", ""),
    "unit_assumption_factor": (N, "", "unit assumption factor", ""),
    "scale_decision_id": (T, "", "filer scale-error record (FSE-xxx)",
                          "blank when none"),
    "scale_recommended_decision": (T, "", "recommended decision (not an "
                                   "approval)", "APPROVE | DEFER | REJECT"),
    "scale_decision_outcome": (T, "", "factor-model outcome for the scale "
                               "record", "PENDING_NOT_APPLIED | ..."),
    "scale_proposed_value": (N, "", "proposed corrected value (not "
                             "applied)", ""),
    "unit_reinterpretation_informational": (T, "", "informational unit "
                                            "reinterpretation", "never "
                                            "applied"),
    "unit_reinterpretation_factor_not_applied": (N, "", "informational "
                                                 "factor", "never applied"),
    "legacy_factor_not_applied": (N, "", "legacy factor", "never applied"),
    "multiplier_basis": (T, "", "basis of the applied multiplier",
                         "NONE | RULE_UNIT_CONVERSION | "
                         "APPROVED_UNIT_ASSUMPTION | "
                         "APPROVED_SCALE_CORRECTION"),
    "factor_formula": (T, "", "factor-model formula", ""),
    "pending_review": (T, "", "pending review items for the value",
                       "blank when none"),
    "deferred_issue_ids": (J, "", "open deferred issues (DQ-xxx)",
                           "blank when none"),
    "source_url": (T, "", "source URL", ""),
    # derived status columns
    "record_status": (T, "", "summary status of the value (precedence in "
                      "listed order; never upgrades pending); in "
                      "metric_coverage_report a multi-value cell lists the "
                      "distinct statuses of its values, separated by a "
                      "vertical bar",
                      _v(RECORD_STATUS)),
    "validation_status": (T, "", "Phase 9 / review summary",
                          _v(VALIDATION_STATUS)),
    "validation_failures": (J, "", "failed rule ids", "blank when none"),
    "open_review_items": (J, "", "open review statuses with counts",
                          REVIEW_STATUS),
    # assurance
    "assurance_engagement_type": (T, "", "BRSR Core engagement type "
                                  "(filing level)", "ASSESSMENT | "
                                  "ASSURANCE | NONE | UNKNOWN | AMBIGUOUS "
                                  "| NOT_REPORTED | "
                                  "NOT_IN_TAXONOMY_VERSION"),
    "assurance_filing_level_level": (T, "", "filing-level assurance level",
                                     "REASONABLE_BRSR_CORE_2024_FLAG | "
                                     "NOT_REASONABLE_OTHERWISE_UNDISCLOSED | "
                                     "NOT_DISCLOSED_AT_FILING_LEVEL | "
                                     "NOT_APPLICABLE (indicator-level "
                                     "Reasonable / Limited stays in "
                                     "assurance/)"),
    "assurance_indicator_scope": (T, "", "indicator coverage scope", ""),
    "assurance_reasonable_flag_2024": (T, "", "2024 reasonable-assurance "
                                       "flag, as filed (V01 mapping not "
                                       "applied)", "true | false | blank"),
    "assurance_core_engagement_2025": (T, "", "2025+ BRSR Core engagement "
                                       "value, as filed", ""),
    # coverage
    "report_status": (T, "", "filing status of the company-year", ""),
    "pipeline_status": (T, "", "coverage status from the extraction "
                        "pipeline (unchanged)", _v(COVERAGE_STATUS)),
    "coverage_status": (T, "", "reported coverage status",
                        _v(COVERAGE_STATUS)),
    "coverage_basis": (T, "", "evidence for coverage_status", ""),
    "values_found": (I, "", "values found by the pipeline", ""),
    "values_from_candidate_mapping": (I, "", "values only from a candidate "
                                      "mapping", ""),
    "extracted_value": (T, "as filed", "the CY value, or a count when "
                        "several breakdown values exist", "blank = none"),
    "unit": (T, "", "normalised unit(s)", ""),
    "supplementary_evidence_status": (T, "", "Phase 13 candidate status",
                                      "CANDIDATE_EVIDENCE_FOUND | "
                                      "DOCUMENT_AVAILABLE_NO_CANDIDATE | "
                                      "NO_LOCAL_DOCUMENT | "
                                      "NOT_AVAILABLE_IN_BRSR_NO_"
                                      "SUPPLEMENTARY_EVIDENCE | blank"),
    "supplementary_candidates": (I, "", "number of PDF candidates", ""),
    "supplementary_note": (T, "", "limitation note", "Reliance-only "
                           "candidate evidence"),
    # unmapped
    "facts": (I, "", "parsed facts", ""),
    "filings": (I, "", "filings containing the concept", ""),
    "facts_with_value": (I, "", "facts with value_status REPORTED", ""),
    "mapped_to_requirement": (B, "", "concept is mapped to a requirement",
                              "True | False"),
    "unmapped_reason": (J, "", "reasons", "NOT_MAPPED_TO_A_REQUIREMENT | "
                        "CROSSWALK_* | CONCEPT_NOT_IN_DECLARED_TAXONOMY"),
    # review queue
    "recommendation_id": (T, "", "review recommendation id", ""),
    "category": (T, "", "anomaly / rule category", ""),
    "proposed_value": (N, "", "proposed value (not applied)", ""),
    "proposed_unit": (T, "", "proposed unit", ""),
    "proposed_multiplier_on_original": (N, "", "proposed multiplier", ""),
    "proposal": (T, "", "proposal text", ""),
    "evidence": (T, "", "evidence", ""),
    "evidence_files": (J, "", "evidence files", ""),
    "source_decision": (T, "", "decision from the source register", ""),
    "recommended_decision": (T, "", "recommended decision (not an "
                             "approval)", "APPROVE | REJECT | DEFER | ..."),
    "confidence": (T, "", "confidence", ""),
    "severity": (T, "", "severity", "ERROR | WARNING | INFO"),
    "detection_rules": (J, "", "rules that detected it", ""),
    "reason": (T, "", "reason", ""),
    "anomaly_ids": (J, "", "anomaly ids", ""),
    "linked_refs": (J, "", "linked references", ""),
    "status": (T, "", "status in the source register", REVIEW_STATUS +
               " (review queue); PASS | FAIL | NOT_EVALUATED (rules); "
               "FSE status"),
    "production_final_status": (T, "", "production status of the value",
                                ""),
    "production_scale_outcome": (T, "", "production scale outcome", ""),
    "apply_path": (T, "", "how an approval would be applied", ""),
    "reviewer_decision": (T, "", "reviewer decision", "blank (none taken)"),
    "reviewer": (T, "", "reviewer", ""),
    "decided_at": (T, "", "decision timestamp", ""),
    "audit_reference": (T, "", "audit reference", ""),
    "reviewer_comment": (T, "", "reviewer comment", ""),
    "apply_note": (T, "", "apply note", ""),
    "previously_cleared_by_investigation": (T, "", "earlier investigation "
                                            "result", ""),
    # blocks
    "block": (T, "", "validation block", "11 blocks"),
    "members_expected": (I, "", "expected members", ""),
    "members_present": (I, "", "members present", ""),
    "members_missing": (I, "", "members missing", ""),
    "members_not_expected": (I, "", "members not expected", ""),
    "members_not_normalised": (I, "", "members not normalised", ""),
    "pending_or_deferred": (I, "", "members pending / deferred", ""),
    "source_unit_tags": (J, "", "source unit tags", ""),
    "mixed_unit_tags": (B, "", "mixed unit tags in the block", ""),
    "rules_passed": (I, "", "rules passed", ""),
    "rules_failed": (I, "", "rules failed", ""),
    "rules_not_evaluated": (I, "", "rules not evaluated", ""),
    "block_status": (T, "", "block status", "CONSISTENT | INCONSISTENT | "
                     "INCOMPLETE | BLOCKED_PENDING_DECISION | NOT_REPORTED"),
    # scale records
    "approval_id": (T, "", "FSE record id", "FSE-001 .. FSE-039"),
    "unit_reinterpretation": (T, "", "unit reinterpretation", ""),
    "unit_reinterpretation_factor_informational": (N, "", "informational "
                                                   "factor", "never "
                                                   "applied"),
    "numeric_conversion_factor": (N, "", "numeric conversion factor", ""),
    "actual_numeric_multiplier_on_original_value": (N, "", "multiplier "
                                                    "that would apply on "
                                                    "approval", ""),
    "proposed_corrected_value": (N, "", "proposed corrected value", "not "
                                 "applied"),
    "proposed_corrected_unit": (T, "", "proposed unit", ""),
    "formula_from_original": (T, "", "formula", ""),
    "check_original_x_multiplier_equals_corrected": (B, "", "check", ""),
    "current_normalised_value": (N, "", "normalised value at review time",
                                 ""),
    "legacy_factor_on_current_normalised_value_only": (N, "", "legacy "
                                                       "factor", "never "
                                                       "applied"),
    "check_normalised_x_legacy_equals_corrected": (B, "", "check", ""),
    "legacy_factor_differs_from_original_multiplier": (B, "", "check", ""),
    "classification": (T, "", "error classification", ""),
    "error_mechanism": (T, "", "error mechanism", ""),
    "evidence_strength": (T, "", "evidence strength", ""),
    "supporting_checks": (J, "", "supporting checks", ""),
    "evidence_domain_check": (T, "", "domain check", ""),
    "evidence_intensity_recompute": (T, "", "intensity recomputation", ""),
    "evidence_wages_per_head": (T, "", "wages per head check", ""),
    "evidence_previous_year_report": (T, "", "previous-year check", ""),
    "evidence_same_report_block": (T, "", "same-block check", ""),
    "block_dependency": (T, "", "block dependency", ""),
    "related_values_outside_strong_set": (J, "", "related values", ""),
    "next_report_restated_value": (T, "", "value restated next year", ""),
    "next_report_restated_unit": (T, "", "unit restated next year", ""),
    "corrected_vs_restated_ratio": (N, "", "ratio", ""),
    "restatement_within_5pct": (B, "", "within 5 %", ""),
    "restatement_explanation": (T, "", "explanation", ""),
    "reviewer_notes": (T, "", "reviewer notes", ""),
    "next_report_source_file": (T, "", "next report file", ""),
    "approval_decision": (T, "", "owner decision", "blank (pending)"),
    "approved_by": (T, "", "approver", "blank"),
    "approval_date": (T, "", "approval date", "blank"),
    "reviewer_comments": (T, "", "comments", ""),
    "production_conversion_status": (T, "", "production conversion "
                                     "status", ""),
    "production_normalised_value": (N, "", "production value (unchanged)",
                                    ""),
    "production_normalised_unit": (T, "", "production unit", ""),
    "package_id": (T, "", "block review package", ""),
    "package_dependency": (T, "", "package dependency", ""),
    "engine_status": (T, "", "factor-model engine status", ""),
    # restatement
    "rule_id": (T, "", "validation rule", "RST01 | RST02"),
    "observed": (T, "", "observed value", ""),
    "expected": (T, "", "expected value", ""),
    "detail": (T, "", "detail", ""),
    "name_based_re_evaluation": (T, "", "name-based re-evaluation "
                                 "(not applied)", ""),
    "name_match_method": (T, "", "match method", ""),
    "stakeholder_name_current": (T, "", "stakeholder name this year", ""),
    "stakeholder_name_next": (T, "", "stakeholder name next year", ""),
    "identity_note": (T, "", "row identity note (DQ-039)", ""),
    # company-year summary
    "identity_fields_present": (I, "", "of CIN, ISIN, NSE, BSE present",
                                ""),
    "identity_fields_expected": (I, "", "identity fields expected", "4"),
    "xbrl_facts_extracted": (I, "", "parsed facts of the filing",
                             "blank when no filing"),
    "xbrl_unique_concepts": (I, "", "distinct concepts of the filing", ""),
    **{f"{p}_metrics_reported": (I, "", f"{p} XBRL metrics REPORTED or "
                                 "REPORTED_ZERO", "")
       for p in ("corporate", "environmental", "social", "governance",
                 "assurance")},
    **{f"{p}_metrics_applicable": (I, "", f"{p} XBRL metrics available in "
                                   "the filing's taxonomy and applicable",
                                   "")
       for p in ("corporate", "environmental", "social", "governance",
                 "assurance")},
    **{f"metrics_{s.lower()}": (I, "", f"metrics with coverage_status {s}",
                                "")
       for s in COVERAGE_STATUS if s not in ("REPORTED", "REPORTED_ZERO")},
    "values_unit_not_normalised": (I, "", "CY values not normalised "
                                   "(ambiguous / pending unit)", ""),
    "unresolved_review_items": (I, "", "open review recommendations", ""),
    "coverage_note": (T, "", "methodology note", ""),
    # final summary
    "stage": (T, "", "pipeline stage", ""),
    "measure": (T, "", "measure", ""),
    "source": (T, "", "source of the metric or measure", ""),
}
# 'source' is used both for the metric source (mapping) and the summary
# source; one entry documents both.
COLUMNS["source"] = (T, "", "metric data source (mapping) or source file of "
                     "the measure (summary)", "")
COLUMNS["value"] = (T, "", "fact value exactly as filed (facts) or the "
                    "measure value (summary)", "blank for nil")


def output_columns(out_dir=None):
    cols = {}
    for path in sorted(Path(out_dir or OUT).glob("*.csv")):
        cols[path.name] = list(pd.read_csv(path, nrows=0,
                                           encoding="utf-8-sig").columns)
    return cols


def undocumented(out_dir=None):
    return sorted({c for cs in output_columns(out_dir).values() for c in cs
                   if c not in COLUMNS})


def render(out_dir=None):
    cols = output_columns(out_dir)
    lines = [
        "# Data dictionary (Phase 15)",
        "",
        "Generated by `python -m app.reporting.data_dictionary` from",
        "`app/reporting/data_dictionary.py`. A test fails if an output",
        "column is undocumented or this file is out of date.",
        "",
        "All files are UTF-8 CSV (with BOM), written to `data/outputs/`, "
        "with",
        "`manifest.json` holding the SHA-256 and row count of every file "
        "and",
        "of every source.",
        "",
        "## General rules",
        "",
        "- Values are copied from approved outputs; nothing is "
        "recalculated.",
        "- A blank cell means missing / not reported. It is never zero.",
        "- Explicit zeros reported by a filer stay `0` (REPORTED_ZERO).",
        "- Pending, deferred, candidate and unresolved records are kept and",
        "  labelled; pillar files contain all applicable rows.",
        "- Recommendations are not approvals; `approval_decision`, ",
        "  `reviewer_decision` and `unit_decision` show what was decided.",
        "- Assessment, Assurance, Limited and Reasonable assurance are",
        "  separate values and never merged.",
        "- Supplementary PDF evidence covers Reliance only and is shown as",
        "  unverified candidates; it never replaces XBRL values.",
        "",
        "## Files",
        "",
        "| File | Grain | Source | Production |",
        "|---|---|---|---|",
    ]
    for name, (grain, src, how) in FILES.items():
        lines.append(f"| `{name}` | {grain} | {src} | {how} |")
    lines += ["", "## Status vocabularies", "", "### coverage_status",
              "", "| Status | Meaning |", "|---|---|"]
    lines += [f"| `{k}` | {v} |" for k, v in COVERAGE_STATUS.items()]
    lines += ["", "Missing information is never inferred from the absence "
              "of a local PDF.", "`EXTRACTION_ERROR` needs evidence (failed "
              "download / parse, or a CY fact", "present but unselected).",
              "", "### record_status (precedence top to bottom)", "",
              "| Status | Meaning |", "|---|---|"]
    lines += [f"| `{k}` | {v} |" for k, v in RECORD_STATUS.items()]
    lines += ["", "### validation_status", "", "| Status | Meaning |",
              "|---|---|"]
    lines += [f"| `{k}` | {v} |" for k, v in VALIDATION_STATUS.items()]
    lines += ["", f"Review workflow statuses: {REVIEW_STATUS}.", "",
              "## Columns by file", ""]
    for name in FILES:
        if name not in cols:
            continue
        lines += [f"### {name}", "", "| Column | Type | Unit | Meaning | "
                  "Allowed values |", "|---|---|---|---|---|"]
        for c in cols[name]:
            t, u, m, a = COLUMNS[c]
            lines.append(f"| `{c}` | {t} | {u} | {m} | "
                         f"{a.replace('|', '/')} |")
        lines.append("")
    return "\n".join(lines) + "\n"


def write(doc=None, out_dir=None):
    missing = undocumented(out_dir)
    if missing:
        raise SystemExit(f"undocumented columns: {missing}")
    doc = Path(doc or DOC)
    doc.write_text(render(out_dir), encoding="utf-8")
    return doc


if __name__ == "__main__":
    print(write())
