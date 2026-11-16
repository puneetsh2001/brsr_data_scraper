"""
Phase 16: CLI tests not covered by Phase 15.

  * every command that can change something defaults to a dry run;
  * the recorded baseline in data/processed/pipeline_state.json matches the
    accepted outputs (every process / validate stage is up to date);
  * fingerprint sensitivity: arguments, code, files added to an input
    folder, and missing inputs;
  * production database write protection through the real CLI process.

Only tmp_path files are written; the real state file is only read.
"""

import os
import subprocess
import sys

import pytest

from app import pipeline as pl
from app.db import connection as dbc
from tests.conftest import PROJECT_ROOT


def subcommands():
    parser = pl.build_parser()
    action = next(a for a in parser._actions
                  if a.__class__.__name__ == "_SubParsersAction")
    return action.choices


def test_every_changing_command_defaults_to_dry_run():
    with_execute = {name for name, p in subcommands().items()
                    if any("--execute" in a.option_strings
                           for a in p._actions)}
    assert with_execute == {"discover", "download", "refresh-taxonomy",
                            "process", "validate", "reprocess-full-corpus",
                            "load-db"}
    for name in sorted(with_execute):
        argv = [name] + (["--database", "brsr_phase14_x"]
                         if name == "load-db" else [])
        assert pl.build_parser().parse_args(argv).execute is False, name


def test_dry_runs_execute_nothing(monkeypatch, capsys):
    def refuse(*_a, **_k):
        raise AssertionError("a stage was executed in a dry run")
    monkeypatch.setattr(pl, "default_runner", refuse)
    monkeypatch.setattr(pl, "backup_processed", refuse)
    for argv in (["process"], ["validate"], ["refresh-taxonomy"],
                 ["discover"], ["reprocess-full-corpus"]):
        pl.main(argv)
    out = capsys.readouterr().out
    assert out.count("DRY RUN: nothing executed") == 5


def test_recorded_baseline_matches_the_accepted_outputs():
    """The baseline recorded with `status --record-state` still describes
    the accepted outputs: no process or validate stage would run."""
    assert pl.STATE_FILE.exists()
    state = pl.load_state()
    for stage, reasons, fp in pl.plan(pl.PROCESS + pl.VALIDATE, state):
        assert reasons == [], (stage.name, reasons)
        assert state[stage.name]["fingerprint"] == fp


def test_fingerprint_covers_args_code_folders_and_missing_inputs(tmp_path):
    folder = tmp_path / "in"
    folder.mkdir()
    (folder / "a.csv").write_text("1")
    base = pl.Stage("demo", "app.pipeline", args=["--x"], inputs=[folder])
    fp = pl.fingerprint(base)
    assert fp == pl.fingerprint(pl.Stage("demo", "app.pipeline",
                                         args=["--x"], inputs=[folder]))
    # other arguments
    assert fp != pl.fingerprint(pl.Stage("demo", "app.pipeline",
                                         args=["--y"], inputs=[folder]))
    # other code
    assert fp != pl.fingerprint(pl.Stage("demo", "app.reporting.deliverables",
                                         args=["--x"], inputs=[folder]))
    # a file added to an input folder
    (folder / "b.csv").write_text("2")
    fp2 = pl.fingerprint(base)
    assert fp2 != fp
    # a renamed file with the same content
    (folder / "b.csv").rename(folder / "c.csv")
    assert pl.fingerprint(base) not in (fp, fp2)
    # a missing input is part of the fingerprint, never an error
    gone = pl.Stage("demo", "app.pipeline", inputs=[tmp_path / "absent"])
    assert pl.fingerprint(gone) != pl.fingerprint(
        pl.Stage("demo", "app.pipeline", inputs=[]))


def test_status_without_record_state_writes_nothing(monkeypatch, tmp_path):
    state = tmp_path / "state.json"
    monkeypatch.setattr(pl, "STATE_FILE", state)
    pl.main(["status"])
    assert not state.exists()


def run_cli(*argv):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    return subprocess.run([sys.executable, "-m", "app.pipeline", *argv],
                          cwd=PROJECT_ROOT, env=env, capture_output=True,
                          text=True, timeout=300)


def test_cli_refuses_production_database_writes():
    prod = dbc.production_database()
    assert prod, "DB_NAME must name the production database"
    before = None
    try:
        before = dbc.production_fingerprint()
    except Exception:            # PostgreSQL not reachable: guard still runs
        pass
    for argv in (["load-db", "--database", prod],
                 ["load-db", "--database", prod, "--execute"],
                 ["load-db", "--database", prod, "--execute", "--promote"]):
        r = run_cli(*argv)
        assert r.returncode != 0, argv
        assert "ProductionWriteBlocked" in r.stderr, r.stderr[-500:]
        assert "STAGED" not in r.stdout and "PROMOTED" not in r.stdout
    r = run_cli("load-db", "--database", "not_a_test_db", "--execute")
    assert r.returncode != 0 and "NotATestDatabase" in r.stderr
    if before is not None:
        assert dbc.production_fingerprint() == before


def test_cli_load_db_requires_a_target():
    r = run_cli("load-db")
    assert r.returncode == 2 and "--database" in r.stderr


def test_production_writes_flag_is_off():
    assert dbc.PRODUCTION_WRITES_ENABLED is False
    assert dbc.TEST_DB_PREFIX == "brsr_phase14_"


@pytest.mark.parametrize("argv", [["reprocess"],
                                  ["reprocess", "--company", "RELIANCE"]])
def test_selective_reprocess_stays_refused(argv):
    with pytest.raises(SystemExit) as e:
        pl.main(argv)
    assert "selective" in str(e.value)
