"""
Phase 16: the test architecture itself.

  * shared session builds are handed out as deep copies, so a test that
    changes its copy cannot affect another test;
  * a mutated shared build is detected by its fingerprint;
  * the session integrity guard detects changed, added and removed files.
"""

import pandas as pd

from tests import conftest


def test_shared_build_returns_independent_copies(monkeypatch):
    monkeypatch.setattr(conftest, "_SHARED", {})
    monkeypatch.setattr(conftest, "_SHARED_FINGERPRINTS", {})
    calls = []

    def factory():
        calls.append(1)
        return {"t": pd.DataFrame({"a": ["1", "2"]}), "n": 3}

    first = conftest.shared_build("demo", factory)
    first["t"].loc[0, "a"] = "changed"
    first["t"]["new"] = "x"
    second = conftest.shared_build("demo", factory)
    assert calls == [1]                               # built once
    assert second["t"]["a"].tolist() == ["1", "2"]    # copy untouched
    assert list(second["t"].columns) == ["a"]
    assert conftest._fingerprint(conftest._SHARED["demo"]) == \
        conftest._SHARED_FINGERPRINTS["demo"]


def test_fingerprint_detects_mutation():
    df = pd.DataFrame({"a": ["1", "2"]})
    before = conftest._fingerprint({"t": df})
    df.loc[1, "a"] = "3"
    assert conftest._fingerprint({"t": df}) != before
    assert conftest._fingerprint({"t": df.rename(columns={"a": "b"})}) != \
        conftest._fingerprint({"t": df})


def test_integrity_guard_detects_changes(monkeypatch, tmp_path):
    (tmp_path / "data/outputs").mkdir(parents=True)
    (tmp_path / "data/processed/backups").mkdir(parents=True)
    keep = tmp_path / "data/outputs/a.csv"
    keep.write_text("x")
    (tmp_path / "data/processed/backups/b.csv").write_text("ignored")
    monkeypatch.setattr(conftest, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(conftest, "GUARDED_DIRS",
                        ("data/outputs", "data/processed"))
    before = conftest.guarded_snapshot()
    assert set(before) == {"data/outputs/a.csv"}      # backups excluded
    keep.write_text("y")
    (tmp_path / "data/outputs/new.csv").write_text("z")
    after = conftest.guarded_snapshot()
    assert after["data/outputs/a.csv"] != before["data/outputs/a.csv"]
    assert "data/outputs/new.csv" in after and \
        "data/outputs/new.csv" not in before
    keep.unlink()
    assert "data/outputs/a.csv" not in conftest.guarded_snapshot()


def test_guard_is_active_for_this_session():
    import os
    if os.environ.get("BRSR_SKIP_INTEGRITY_GUARD") == "1":
        return
    assert conftest._GUARD_BEFORE, "integrity guard snapshot missing"
    assert any(k.startswith("data/outputs/") for k in conftest._GUARD_BEFORE)
    assert any(k.startswith("data/reference/")
               for k in conftest._GUARD_BEFORE)
