"""Read-only analytics layer over the PostgreSQL `brsr` schema (Phase 14).

The API serves only what the database holds: filed values (with their
normalised form where the factor-model engine produced one), their source
filing, and statuses derived from the database. Nothing is invented and
a missing value is never returned as zero.
"""
