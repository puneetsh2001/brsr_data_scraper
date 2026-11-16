-- 0004 decisions_and_review down (reverse dependency order).
DROP TABLE IF EXISTS brsr_staging.review_decision_log;
DROP TABLE IF EXISTS brsr.review_decision_log;
DROP TABLE IF EXISTS brsr_staging.review_recommendation;
DROP TABLE IF EXISTS brsr.review_recommendation;
DROP TABLE IF EXISTS brsr_staging.review_anomaly;
DROP TABLE IF EXISTS brsr.review_anomaly;
DROP TABLE IF EXISTS brsr_staging.deferred_issue;
DROP TABLE IF EXISTS brsr.deferred_issue;
DROP TABLE IF EXISTS brsr_staging.scale_review_record;
DROP TABLE IF EXISTS brsr.scale_review_record;
DROP TABLE IF EXISTS brsr_staging.unit_decision;
DROP TABLE IF EXISTS brsr.unit_decision;
