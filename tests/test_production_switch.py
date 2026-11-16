"""
Owner-approved production switch (2026-11-12): normalised/ is written by
the factor-model engine. The established columns must stay byte-identical
to the frozen output of the retired normaliser until an approved decision
is applied; factor-model columns are appended.
"""

import hashlib
from pathlib import Path

import pandas as pd
import pytest

from app.normalisation import factor_model as fm
from tests.conftest import (APPROVED_SWITCH_CHANGES, PROJECT_ROOT,
                            SWITCH_BACKUP)

ARCHIVE = Path("C:/brsr_data_scraper_archive/production_switch_20261112")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@pytest.mark.parametrize("name", [
    "brsr_requirement_values_normalised.csv",
    "brsr_requirement_values_py_normalised.csv",
    "brsr_unresolved_review_items.csv",
])
def test_established_columns_byte_identical(name, tmp_path):
    base_file = SWITCH_BACKUP / "normalised" / name
    base_cols = list(pd.read_csv(base_file, dtype=str, nrows=0).columns)
    now = pd.read_csv(fm.PRODUCTION_DIR / name, dtype=str,
                      keep_default_na=False)
    assert list(now.columns[:len(base_cols)]) == base_cols
    out = tmp_path / name
    now[base_cols].to_csv(out, index=False, encoding="utf-8-sig")
    assert sha(out) == sha(base_file)
    for col in ("multiplier_basis", "factor_formula", "pending_review",
                "deferred_issue_ids"):
        assert col in now.columns


@pytest.mark.parametrize("name", [
    "brsr_normalisation_before_after.csv", "brsr_unit_definitions.csv",
    "brsr_normalisation_validation_issues.csv",
    "brsr_normalisation_summary.csv", "brsr_mtco2e_assumption_evidence.csv",
    "brsr_mtco2e_decision_summary.csv"])
def test_other_production_files_unchanged(name):
    assert sha(fm.PRODUCTION_DIR / name) == \
        sha(SWITCH_BACKUP / "normalised" / name)


@pytest.mark.parametrize("folder", ["validation", "blocks"])
def test_downstream_outputs_unchanged(folder):
    for f in (SWITCH_BACKUP / folder).iterdir():
        assert sha(PROJECT_ROOT / "data/processed/nifty100" / folder / f.name) \
            == sha(f), f.name


def test_removed_copies_are_backed_up():
    for name in ("brsr_factor_model_values.csv",
                 "brsr_factor_model_values_py.csv"):
        rel = f"data/processed/nifty100/normalised_factor_model/{name}"
        assert rel in APPROVED_SWITCH_CHANGES
        assert not (PROJECT_ROOT / rel).exists()
        assert (SWITCH_BACKUP / "normalised_factor_model" / name).exists()


def test_read_production_restores_factor_names():
    df = fm.read_production(fm.PRODUCTION["CY"])
    assert (df["final_value"] == df["normalised_value"]).all()
    assert (df["actual_numeric_multiplier_on_original"]
            == df["conversion_factor"]).all()


def test_retired_normaliser_archived_and_wrapped():
    code = (PROJECT_ROOT / "app/extractors/brsr_unit_normaliser.py").read_text()
    assert "from app.normalisation.factor_model import main" in code
    backup = SWITCH_BACKUP / "code" / "brsr_unit_normaliser.py"
    assert backup.exists()
    if ARCHIVE.exists():
        manifest = pd.read_csv(ARCHIVE / "RESTORE_MANIFEST.csv", dtype=str)
        assert sha(ARCHIVE / "app/extractors/brsr_unit_normaliser.py") == \
            manifest["sha256"].iloc[0] == sha(backup)
