"""
Unit normalisation entry point (kept for the established pipeline command).

Since the owner-approved switch of 2026-11-12 the production normalisation
in data/processed/nifty100/normalised/ is produced by the explicit factor
model engine, app.normalisation.factor_model. The established columns are
unchanged; the factor-model columns are appended.

The retired implementation is archived (not deleted) in
    C:/brsr_data_scraper_archive/production_switch_20261112/
    data/processed/nifty100/backups/20261112_pre_production_switch/code/
Its last output is the frozen reconciliation baseline in
    data/processed/nifty100/backups/20261112_pre_production_switch/normalised/

Run:
    python -m app.extractors.brsr_unit_normaliser
    (same as python -m app.normalisation.factor_model)
"""

from app.normalisation.factor_model import main

if __name__ == "__main__":
    main()
