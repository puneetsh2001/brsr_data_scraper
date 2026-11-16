"""Phase 15 verification items: full-corpus reprocess, the report CLI with
staged / verified / backed-up replacement, and the company master grain."""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from tests.conftest import PROJECT_ROOT

OUT = PROJECT_ROOT / "data" / "outputs"


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def snapshot(d):
    return {p.name: sha(p) for p in sorted(Path(d).iterdir()) if p.is_file()}


# ===================================================== 1. reprocess
def test_old_reprocess_name_is_refused(capsys):
    from app import pipeline as pl
    for argv in (["reprocess"], ["reprocess", "--company", "RELIANCE"],
                 ["reprocess", "--company", "RELIANCE", "--fy", "2023-24",
                  "--execute"]):
        with pytest.raises(SystemExit) as e:
            pl.main(argv)
        assert "selective" in str(e.value)
        assert "reprocess-full-corpus" in str(e.value)


def test_reprocess_full_corpus_runs_every_stage_for_all_filings(
        monkeypatch, capsys):
    from app import pipeline as pl
    calls = []
    monkeypatch.setattr(pl, "run_stages", lambda stages, **kw:
                        calls.append((stages, kw)))
    for argv in (["reprocess-full-corpus"],
                 ["reprocess-full-corpus", "--check-company", "RELIANCE",
                  "--check-fy", "2023-24"]):
        pl.main(argv)
    out = capsys.readouterr().out
    corpus = pl.filings_for()
    assert f"all {len(corpus)} in-scope filings" in out
    assert "does not limit it" in out
    assert len(calls) == 2
    for stages, kw in calls:
        # the selection never narrows the stage list or the stage arguments
        assert [s.name for s in stages] == \
            [s.name for s in pl.PROCESS + pl.VALIDATE]
        assert kw["force"] is True and kw["execute"] is False
        for s in stages:
            assert "RELIANCE" not in " ".join(s.args)
            assert "2023-24" not in " ".join(s.args)
    assert calls[0][0] == calls[1][0]


def test_reprocess_check_selection_must_exist():
    from app import pipeline as pl
    with pytest.raises(SystemExit):
        pl.main(["reprocess-full-corpus", "--check-company", "NO_SUCH_CO"])
    with pytest.raises(SystemExit):
        pl.main(["reprocess-full-corpus", "--check-company", "RELIANCE",
                 "--check-fy", "1999-00"])
    corpus, checked = pl.reprocess_plan("RELIANCE", "2023-24")
    assert len(checked) == 1 and checked["filing_id"].isin(
        corpus["filing_id"]).all()


def test_help_text_states_full_corpus():
    from app import pipeline as pl
    text = " ".join(pl.build_parser().format_help().split())
    assert "reprocess-full-corpus" in text
    assert "for ALL in" in text and "no selective processing" in text
    assert "REFUSED" in text
    doc = (PROJECT_ROOT / "docs" / "PROCESSING_ARCHITECTURE.md").read_text(
        encoding="utf-8")
    assert "reprocess-full-corpus" in doc
    assert "not supported" in doc
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    assert "reprocess-full-corpus" in readme
    assert "reprocess --company" not in readme + doc


def _tmp_stages(tmp_path):
    from app import pipeline as pl
    a, b = tmp_path / "a.txt", tmp_path / "b.txt"
    a.write_text("1")
    s1 = pl.Stage("one", "app.pipeline", inputs=[a], outputs=[b])
    s2 = pl.Stage("two", "app.pipeline", inputs=[b],
                  outputs=[tmp_path / "c.txt"])
    return [s1, s2], a, b


def test_forced_full_run_keeps_fingerprints_correct(tmp_path):
    from app import pipeline as pl
    stages, a, b = _tmp_stages(tmp_path)
    state = tmp_path / "state.json"
    ran, backups = [], []

    def runner(s):
        ran.append(s.name)
        Path(s.outputs[0]).write_text(f"{s.name}-{len(ran)}")
        return 0

    quiet = lambda *_: None
    # dry run: nothing runs, no state, no backup
    pl.run_stages(stages, force=True, state_path=state, runner=runner,
                  echo=quiet, before_execute=lambda: backups.append(1))
    assert ran == [] and backups == [] and not state.exists()
    # forced execution: every stage runs once, backup taken first
    pl.run_stages(stages, execute=True, force=True, state_path=state,
                  runner=runner, echo=quiet,
                  before_execute=lambda: backups.append(1))
    assert ran == ["one", "two"] and backups == [1]
    # recorded fingerprints equal the post-run inputs: next plan is clean
    steps = pl.plan(stages, pl.load_state(state))
    assert all(reasons == [] for _, reasons, _ in steps)
    rec = pl.load_state(state)
    assert rec["two"]["fingerprint"] == pl.fingerprint(stages[1])
    # an up-to-date incremental run executes nothing and takes no backup
    pl.run_stages(stages, execute=True, state_path=state, runner=runner,
                  echo=quiet, before_execute=lambda: backups.append(1))
    assert ran == ["one", "two"] and backups == [1]


def test_failed_stage_does_not_record_state(tmp_path):
    from app import pipeline as pl
    stages, a, b = _tmp_stages(tmp_path)
    state = tmp_path / "state.json"
    with pytest.raises(SystemExit):
        pl.run_stages(stages, execute=True, state_path=state,
                      runner=lambda s: 1, echo=lambda *_: None)
    assert pl.load_state(state) == {}


def test_backup_processed_is_verified_and_excludes_backups(tmp_path):
    from app import pipeline as pl
    root = tmp_path / "processed"
    (root / "x").mkdir(parents=True)
    (root / "x" / "f.csv").write_text("a,b\n1,2\n")
    (root / "g.json").write_text("{}")
    (root / "nifty100" / "backups" / "old").mkdir(parents=True)
    (root / "nifty100" / "backups" / "old" / "h.csv").write_text("no")
    dest = pl.backup_processed("test", root=root,
                               backup_root=tmp_path / "bk")
    lines = (dest / "backup_sha256.txt").read_text().splitlines()
    assert sorted(l.split("  ")[1] for l in lines) == ["g.json", "x/f.csv"]
    for l in lines:
        digest, rel = l.split("  ")
        assert sha(dest / "processed" / rel) == digest == sha(root / rel)


# ===================================================== 2. report CLI
def _fake_generate(tag):
    """A fast stand-in for deliverables.generate (documented columns)."""
    from app.reporting import deliverables as d

    def gen(dest):
        dest.mkdir(parents=True, exist_ok=True)
        outputs = {}
        for name in d.EXPECTED_OUTPUTS:
            p = dest / name
            p.write_text(f"company_id\n{tag}-{name}\n", encoding="utf-8")
            outputs[name] = {"sha256": d.sha(p), "rows": 1}
        manifest = {"outputs": outputs}
        (dest / "manifest.json").write_text(json.dumps(manifest))
        return manifest
    return gen


@pytest.fixture
def report_env(tmp_path, monkeypatch):
    from app.reporting import deliverables as d
    out, bak = tmp_path / "outputs", tmp_path / "backups"
    monkeypatch.setattr(d, "generate", _fake_generate("v1"))
    d.build(out, bak)
    return d, out, bak, snapshot(out)


def test_backup_before_replacement(report_env, monkeypatch):
    d, out, bak, before = report_env
    monkeypatch.setattr(d, "generate", _fake_generate("v2"))
    result = d.build(out, bak)
    backup = result["backup_dir"]
    assert backup is not None and backup.parent == bak
    copied = {k: v for k, v in snapshot(backup).items()
              if k != "backup_sha256.txt"}
    assert copied == before                         # previous outputs kept
    listed = dict(reversed(l.split("  ", 1)) for l in
                  (backup / "backup_sha256.txt").read_text().splitlines())
    assert listed == before
    after = snapshot(out)
    assert after != before
    assert all("v2" in (out / n).read_text() for n in d.EXPECTED_OUTPUTS)
    assert not list(out.parent.glob(".outputs.staging-*"))


def test_failed_generation_leaves_outputs_untouched(report_env,
                                                    monkeypatch):
    d, out, bak, before = report_env

    def broken(dest):
        _fake_generate("v2")(dest)
        raise RuntimeError("simulated failure in the middle of a build")
    monkeypatch.setattr(d, "generate", broken)
    with pytest.raises(RuntimeError):
        d.build(out, bak)
    assert snapshot(out) == before
    assert not list(bak.glob("*_pre_report_outputs"))
    assert not list(out.parent.glob(".outputs.staging-*"))


def test_incomplete_generation_is_rejected(report_env, monkeypatch):
    d, out, bak, before = report_env

    def incomplete(dest):
        m = _fake_generate("v2")(dest)
        (dest / "social_metrics.csv").unlink()
        return m
    monkeypatch.setattr(d, "generate", incomplete)
    with pytest.raises(d.ReportBuildError):
        d.build(out, bak)
    assert snapshot(out) == before


def test_undocumented_column_is_rejected_before_replacement(report_env,
                                                            monkeypatch):
    d, out, bak, before = report_env

    def bad_column(dest):
        m = _fake_generate("v2")(dest)
        p = dest / "social_metrics.csv"
        p.write_text("not_documented_col\n1\n")
        m["outputs"]["social_metrics.csv"]["sha256"] = d.sha(p)
        return m
    monkeypatch.setattr(d, "generate", bad_column)
    with pytest.raises(d.ReportBuildError, match="undocumented"):
        d.build(out, bak)
    assert snapshot(out) == before


def test_failed_backup_verification_blocks_replacement(report_env,
                                                       monkeypatch):
    d, out, bak, before = report_env
    monkeypatch.setattr(d, "generate", _fake_generate("v2"))
    real_copy2 = d.shutil.copy2

    def corrupt(src, dst):
        real_copy2(src, dst)
        Path(dst).write_text("corrupted")
    monkeypatch.setattr(d.shutil, "copy2", corrupt)
    with pytest.raises(d.ReportBuildError, match="backup verification"):
        d.build(out, bak)
    monkeypatch.setattr(d.shutil, "copy2", real_copy2)
    assert snapshot(out) == before


def test_failure_during_replacement_restores_from_backup(report_env,
                                                         monkeypatch):
    d, out, bak, before = report_env
    monkeypatch.setattr(d, "generate", _fake_generate("v2"))
    real_replace, n = os.replace, {"i": 0}

    def flaky(src, dst):
        n["i"] += 1
        if n["i"] == 4:
            raise OSError("simulated failure while replacing")
        real_replace(src, dst)
    monkeypatch.setattr(d.os, "replace", flaky)
    with pytest.raises(OSError):
        d.build(out, bak)
    assert snapshot(out) == before                  # all restored
    assert not list(out.parent.glob(".outputs.staging-*"))


def test_cli_report_options():
    from app import pipeline as pl
    a = pl.build_parser().parse_args(["report", "--output-dir", "x",
                                      "--backup-dir", "y",
                                      "--dictionary-path", "z.md"])
    assert (a.output_dir, a.backup_dir, a.dictionary_path) == \
        ("x", "y", "z.md")


@pytest.fixture(scope="module")
def real_cli_runs(tmp_path_factory):
    """The real `python -m app.pipeline report` run twice into an isolated
    directory (reads the approved sources, never writes data/outputs)."""
    iso = tmp_path_factory.mktemp("report_cli")
    out, bak = iso / "outputs", iso / "backups"
    accepted = snapshot(OUT)
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    cmd = [sys.executable, "-m", "app.pipeline", "report", "--output-dir",
           str(out), "--backup-dir", str(bak), "--dictionary-path",
           str(iso / "DATA_DICTIONARY.md")]
    runs = []
    for _ in range(2):
        r = subprocess.run(cmd, cwd=PROJECT_ROOT, env=env,
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stderr[-2000:]
        runs.append(snapshot(out))
    return {"iso": iso, "out": out, "bak": bak, "runs": runs,
            "accepted_before": accepted}


def test_real_cli_generates_reconciled_outputs(real_cli_runs):
    from app.reporting import deliverables as d
    out, first = real_cli_runs["out"], real_cli_runs["runs"][0]
    accepted = real_cli_runs["accepted_before"]
    assert set(d.EXPECTED_OUTPUTS) | {"manifest.json"} == set(first)
    for name in d.EXPECTED_OUTPUTS:                 # deterministic rebuild
        assert first[name] == accepted[name], name
    m = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    acc = json.loads((OUT / "manifest.json").read_text(encoding="utf-8"))
    assert m["sources"] == acc["sources"]
    for name, info in acc["outputs"].items():
        assert m["outputs"][name]["rows"] == info["rows"]
    doc = (real_cli_runs["iso"] / "DATA_DICTIONARY.md").read_text(
        encoding="utf-8")
    assert doc == (PROJECT_ROOT / "docs" / "DATA_DICTIONARY.md").read_text(
        encoding="utf-8")


def test_real_cli_second_run_backs_up_first(real_cli_runs):
    first, second = real_cli_runs["runs"]
    backups = sorted(real_cli_runs["bak"].glob("*_pre_report_outputs"))
    assert len(backups) == 1                        # run 1 had nothing
    copied = {k: v for k, v in snapshot(backups[0]).items()
              if k != "backup_sha256.txt"}
    assert copied == first
    for name in first:
        if name != "manifest.json":
            assert second[name] == first[name]
    assert not list(real_cli_runs["iso"].glob(".outputs.staging-*"))


def test_real_cli_left_accepted_outputs_untouched(real_cli_runs):
    assert snapshot(OUT) == real_cli_runs["accepted_before"]


# ===================================================== 3. company master
def test_company_master_grain():
    m = pd.read_csv(OUT / "company_master.csv", dtype=str,
                    keep_default_na=False, encoding="utf-8-sig")
    assert m["company_id"].is_unique and m["nse_symbol"].is_unique
    isin = m.loc[m["isin"] != "", "isin"]
    assert isin.is_unique
    sym = m[m["company_id"].str.startswith("C-SYM-")]
    assert (sym["isin"] == "").all()
    assert (sym["company_status"] ==
            "NOT_IN_CURRENT_NSE_MASTER_MERGED_OR_DELISTED").all()
    assert (m.loc[m["isin"] != "", "company_id"] ==
            "C-" + m.loc[m["isin"] != "", "isin"]).all()
    universe = m[m["in_processing_universe"] == "True"]
    assert len(universe) == 100
    cov = pd.read_csv(OUT / "metric_coverage_report.csv", dtype=str,
                      keep_default_na=False, encoding="utf-8-sig",
                      usecols=["master_isin"])
    assert set(cov["master_isin"]) == set(universe["isin"])
    # every non-universe row is a discovered NSE BRSR filer
    assert (m.loc[m["in_processing_universe"] == "False",
                  "is_nse_brsr_filer"] == "True").all()


def test_company_master_grain_documented():
    from app.reporting import data_dictionary as dd
    grain = dd.FILES["company_master.csv"][0]
    assert "whole discovered registry" in grain
    assert "in_processing_universe" in grain
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    assert "1,342" in readme and "in_processing_universe" in readme
