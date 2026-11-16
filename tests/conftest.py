"""Shared test setup: make the project importable and expose data paths."""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

REGISTRY_DIR = PROJECT_ROOT / "data" / "processed" / "registry"
NIFTY_DIR = PROJECT_ROOT / "data" / "processed" / "nifty100"


# Files changed by the owner-approved production switch to the factor-model
# engine (2026-11-12). Earlier phase snapshots exempt only these; the switch
# itself is verified in tests/test_production_switch.py (established
# columns byte-identical to the frozen baseline, removed copies backed up).
SWITCH_BACKUP = (PROJECT_ROOT / "data/processed/nifty100/backups/"
                 "20261112_pre_production_switch")
APPROVED_SWITCH_CHANGES = {
    "data/processed/nifty100/normalised/brsr_requirement_values_normalised.csv",
    "data/processed/nifty100/normalised/"
    "brsr_requirement_values_py_normalised.csv",
    "data/processed/nifty100/normalised/brsr_unresolved_review_items.csv",
    "data/processed/nifty100/normalised_factor_model/"
    "factor_model_differences.csv",
    "data/processed/nifty100/normalised_factor_model/"
    "brsr_factor_model_values.csv",
    "data/processed/nifty100/normalised_factor_model/"
    "brsr_factor_model_values_py.csv",
}


def changed_since(snapshot):
    """Files in a sha256 snapshot that changed or vanished, minus the
    approved production-switch changes."""
    import hashlib
    changed = []
    for line in Path(snapshot).read_text().splitlines():
        digest, path = line.split(None, 1)
        rel = path.lstrip("*").strip()
        if rel in APPROVED_SWITCH_CHANGES:
            continue
        p = PROJECT_ROOT / rel
        if not p.exists() or hashlib.sha256(p.read_bytes()).hexdigest() \
                != digest:
            changed.append(rel)
    return changed


def read_csv(path):
    import pandas as pd
    return pd.read_csv(path, dtype=str, keep_default_na=False)


# ---------------------------------------------------------------------------
# Shared read-only builds (Phase 16)
#
# Several modules need the same expensive in-memory build of the approved
# corpus (review workflow, factor model, validation). Each is computed once
# per session with write=False and handed out as a deep copy, so a test can
# never change what another test sees. A fingerprint taken when a build is
# created is checked again at the end of the session (see
# pytest_sessionfinish): a shared build that was mutated fails the run.
# ---------------------------------------------------------------------------

_SHARED = {}
_SHARED_FINGERPRINTS = {}


def _fingerprint(obj):
    import hashlib
    import pandas as pd
    h = hashlib.sha256()
    if isinstance(obj, dict):
        for k in sorted(obj):
            h.update(str(k).encode())
            h.update(_fingerprint(obj[k]).encode())
    elif isinstance(obj, pd.DataFrame):
        h.update("|".join(map(str, obj.columns)).encode())
        h.update(pd.util.hash_pandas_object(obj, index=True).values
                 .tobytes())
    else:
        h.update(repr(obj).encode())
    return h.hexdigest()


def _deep_copy(obj):
    import pandas as pd
    if isinstance(obj, dict):
        return {k: _deep_copy(v) for k, v in obj.items()}
    if isinstance(obj, pd.DataFrame):
        return obj.copy(deep=True)
    return obj


def shared_build(name, factory):
    """Deep copy of a session-wide build; `factory` runs at most once."""
    if name not in _SHARED:
        _SHARED[name] = factory()
        _SHARED_FINGERPRINTS[name] = _fingerprint(_SHARED[name])
    return _deep_copy(_SHARED[name])


def review_workflow_build():
    from app.review import workflow as wf
    return shared_build("review_workflow", lambda: wf.build(write=False))


def factor_model_build():
    from app.normalisation import factor_model as fm
    return shared_build("factor_model", lambda: fm.build(write=False))


def production_values(role="CY"):
    from app.normalisation import factor_model as fm
    return shared_build(f"production_{role}",
                        lambda: fm.read_production(fm.PRODUCTION[role]))


# ---------------------------------------------------------------------------
# Session integrity guard (Phase 16)
#
# The accepted outputs, the reference decisions and the processed corpus
# must be byte-identical after the test session. Tests write only to
# tmp_path / isolated databases; the few builds that rewrite processed
# files are deterministic. Any change, addition or removal fails the run.
# Backups (append-only) and the logs folder are excluded.
# ---------------------------------------------------------------------------

GUARDED_DIRS = ("data/outputs", "data/reference", "data/processed",
                "data/raw")
_GUARD_BEFORE = {}


def _guard_excluded(rel):
    return "/backups/" in rel or "__pycache__" in rel


def guarded_snapshot():
    import hashlib
    out = {}
    for top in GUARDED_DIRS:
        for p in sorted((PROJECT_ROOT / top).rglob("*")):
            rel = p.relative_to(PROJECT_ROOT).as_posix()
            if not p.is_file() or _guard_excluded(rel):
                continue
            h = hashlib.sha256()
            with open(p, "rb") as fh:
                for block in iter(lambda: fh.read(1 << 20), b""):
                    h.update(block)
            out[rel] = h.hexdigest()
    return out


def pytest_sessionstart(session):
    import os
    if os.environ.get("BRSR_SKIP_INTEGRITY_GUARD") != "1":
        _GUARD_BEFORE.update(guarded_snapshot())


def pytest_sessionfinish(session, exitstatus):
    problems = []
    for name, digest in _SHARED_FINGERPRINTS.items():
        if _fingerprint(_SHARED[name]) != digest:
            problems.append(f"shared build mutated: {name}")
    if _GUARD_BEFORE:
        after = guarded_snapshot()
        for rel in sorted(set(_GUARD_BEFORE) | set(after)):
            if _GUARD_BEFORE.get(rel) != after.get(rel):
                state = ("added" if rel not in _GUARD_BEFORE else
                         "removed" if rel not in after else "changed")
                problems.append(f"{state}: {rel}")
    if problems:
        tr = session.config.pluginmanager.get_plugin("terminalreporter")
        lines = ["INTEGRITY GUARD FAILED"] + problems[:50]
        if tr is not None:
            tr.write_sep("=", "integrity guard", red=True)
            for line in lines:
                tr.write_line(line)
        else:
            print("\n".join(lines))
        session.exitstatus = 1


@pytest.fixture(scope="session")
def company_master():
    return read_csv(REGISTRY_DIR / "company_master.csv")


@pytest.fixture(scope="session")
def identifiers():
    return read_csv(REGISTRY_DIR / "company_identifiers.csv")


@pytest.fixture(scope="session")
def filing_manifest():
    return read_csv(REGISTRY_DIR / "filing_manifest.csv")


@pytest.fixture(scope="session")
def verification():
    return read_csv(NIFTY_DIR / "nse_brsr_report_verification.csv")
