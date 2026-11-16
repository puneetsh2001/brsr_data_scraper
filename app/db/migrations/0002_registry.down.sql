-- 0002 registry down (reverse dependency order).
DROP TABLE IF EXISTS brsr_staging.requirement_concept_map;
DROP TABLE IF EXISTS brsr.requirement_concept_map;
DROP TABLE IF EXISTS brsr_staging.concept_crosswalk;
DROP TABLE IF EXISTS brsr.concept_crosswalk;
DROP TABLE IF EXISTS brsr_staging.filing;
DROP TABLE IF EXISTS brsr.filing;
DROP TABLE IF EXISTS brsr_staging.taxonomy_version;
DROP TABLE IF EXISTS brsr.taxonomy_version;
DROP TABLE IF EXISTS brsr_staging.company_identifier;
DROP TABLE IF EXISTS brsr.company_identifier;
DROP TABLE IF EXISTS brsr_staging.company;
DROP TABLE IF EXISTS brsr.company;
