"""
Single command-line entry point for the BRSR pipeline (Phase 15).

    python -m app.pipeline status
    python -m app.pipeline discover          [--execute]   network
    python -m app.pipeline download          [--execute]   network
    python -m app.pipeline refresh-taxonomy  [--execute]   network
    python -m app.pipeline process           [--execute] [--force]
    python -m app.pipeline reprocess-full-corpus
                         [--check-company <ISIN|symbol|company_id>]
                         [--check-fy 2023-24] [--execute]
    python -m app.pipeline validate          [--execute] [--force]
    python -m app.pipeline report [--output-dir D] [--backup-dir B]
                                  [--dictionary-path F]
    python -m app.pipeline load-db --database <name> [--execute] [--promote]

Safety
- Every command that uses the network or rewrites processed outputs is a
  DRY RUN unless --execute is given. The dry run prints the plan only.
- Incremental: each stage stores a fingerprint (SHA-256 of its inputs and
  of its own code) in data/processed/pipeline_state.json. A stage runs only
  when that fingerprint changed or an output is missing; --force overrides.
  Downloads use the filing manifest flags needs_download / needs_processing
  and the downloader skips files that already exist.
- There is NO selective (per company / per FY) processing. The stage
  modules write corpus-wide files and several rules compare neighbouring
  years (YoY, RST01/RST02, CY/PY), so reprocessing one filing alone would
  leave downstream outputs inconsistent. reprocess-full-corpus therefore
  always re-runs every process and validate stage for ALL in-scope filings;
  --check-company / --check-fy only verify that a filing is part of that
  run. The old name "reprocess" is refused.
- Before an executed process / validate / reprocess-full-corpus run, the
  processed outputs are copied to backups/<timestamp>_pre_<command>/ and
  every copy is verified by SHA-256.
- report generates into a staging folder, verifies it, backs up the
  current outputs (SHA-256 verified) and only then replaces them; a failed
  build leaves the current outputs untouched.
- load-db never writes to the production database: app.db.connection
  refuses it (PRODUCTION_WRITES_ENABLED = False) and only test databases
  with the prefix brsr_phase14_ are accepted. A production import would
  also have to pass app.core.deferred_issues.assert_none_open(); that gate
  is checked and reported here.
- Supplementary PDFs are never downloaded by this CLI (owner decision).
"""

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pandas as pd

from app.config import settings

ROOT = settings.project_root
P = settings.processed_dir
N = P / "nifty100"
STATE_FILE = P / "pipeline_state.json"
MANIFEST = P / "registry" / "filing_manifest.csv"
MASTER = P / "registry" / "company_master.csv"
REF = settings.reference_dir


@dataclass
class Stage:
    name: str
    module: str
    args: list = field(default_factory=list)
    inputs: list = field(default_factory=list)
    outputs: list = field(default_factory=list)
    network: bool = False

    def code_path(self):
        return ROOT / (self.module.replace(".", "/") + ".py")


# ----------------------------------------------------------------------
# stage definitions
# ----------------------------------------------------------------------
RAW_XML = settings.raw_dir / "nse_brsr_xml"

DISCOVER = [
    Stage("filing_index", "app.scrapers.nse_brsr_filing_index",
          ["--years", "5"], network=True,
          outputs=[N / "nse_brsr_filing_index.csv"]),
    Stage("company_master", "app.discovery.company_master",
          outputs=[MASTER]),
    Stage("filing_registry", "app.discovery.filing_registry",
          ["--refresh-api"], network=True, outputs=[MANIFEST]),
]
DOWNLOAD = [
    Stage("download_xbrl", "app.downloaders.xbrl_downloader",
          ["--source", "index"], network=True,
          outputs=[N / "nse_brsr_report_manifest.csv"]),
    Stage("filing_registry", "app.discovery.filing_registry",
          outputs=[MANIFEST]),
]
REFRESH_TAXONOMY = [
    Stage("download_taxonomy",
          "app.downloaders.sebi_brsr_taxonomy_downloader", network=True),
    Stage("taxonomy_tables",
          "app.extractors.nse_brsr_taxonomy_mapping_builder"),
    Stage("taxonomy_registry", "app.taxonomy.taxonomy_registry",
          outputs=[P / "taxonomy" / "taxonomy_registry.csv"]),
]
PROCESS = [
    Stage("verify_reports", "app.validators.brsr_report_verifier",
          ["--years", "5"],
          inputs=[N / "nse_brsr_report_manifest.csv", RAW_XML],
          outputs=[N / "nse_brsr_extraction_manifest.csv"]),
    Stage("extract_facts", "app.extractors.nse_brsr_multi_company_preview",
          ["--manifest", "data/processed/nifty100/"
           "nse_brsr_extraction_manifest.csv",
           "--output-dir", "data/processed/nifty100"],
          inputs=[N / "nse_brsr_extraction_manifest.csv", RAW_XML],
          outputs=[N / "multi_company_facts.csv"]),
    Stage("parse_facts", "app.parsers.xbrl_fact_store",
          inputs=[MANIFEST, RAW_XML, N / "multi_company_facts.csv"],
          outputs=[N / "parsed" / "xbrl_facts.csv"]),
    Stage("metric_registry", "app.metrics.metric_registry",
          inputs=[REF / "brsr_requirement_concept_map.csv",
                  P / "taxonomy" / "taxonomy_registry.csv",
                  P / "taxonomy_versions" /
                  "nse_brsr_concept_crosswalk.csv"],
          outputs=[P / "metrics" / "metric_version_availability.csv"]),
    Stage("requirement_values", "app.extractors.brsr_requirement_extractor",
          inputs=[N / "multi_company_facts.csv",
                  REF / "brsr_requirement_concept_map.csv",
                  P / "taxonomy_versions" /
                  "nse_brsr_concept_crosswalk.csv"],
          outputs=[N / "brsr_requirement_values.csv",
                   N / "brsr_requirement_coverage.csv"]),
    Stage("normalise", "app.normalisation.factor_model",
          inputs=[N / "brsr_requirement_values.csv",
                  N / "brsr_requirement_values_py.csv",
                  REF / "brsr_mtco2e_unit_decisions.csv",
                  REF / "review_decisions.csv",
                  N / "filer_scale_error_review" / "approval" /
                  "final_review" / "strong_recommendations_final_review.csv"],
          outputs=[N / "normalised" /
                   "brsr_requirement_values_normalised.csv"]),
    Stage("assurance", "app.assurance.extractor",
          inputs=[N / "parsed" / "xbrl_facts.csv", MANIFEST],
          outputs=[N / "assurance" / "assurance_engagements.csv"]),
    Stage("supplementary", "app.supplementary.evidence",
          inputs=[settings.raw_dir / "supplementary",
                  N / "normalised" /
                  "brsr_requirement_values_normalised.csv"],
          outputs=[N / "supplementary" / "document_inventory.csv"]),
]
VALIDATE = [
    Stage("rules", "app.validation.rules_engine",
          inputs=[N / "normalised"], outputs=[N / "validation" /
                                              "validation_results.csv"]),
    Stage("blocks", "app.validation.block_validation",
          inputs=[N / "normalised", N / "validation" /
                  "validation_results.csv"],
          outputs=[N / "blocks" / "block_status.csv"]),
    Stage("review_workflow", "app.review.workflow",
          inputs=[N / "normalised", N / "validation",
                  N / "assurance", N / "supplementary" /
                  "external_requirement_candidates.csv",
                  REF / "review_decisions.csv",
                  REF / "deferred_issues_register.csv"],
          outputs=[N / "review_workflow" / "review_queue.csv"]),
]
COMMAND_STAGES = {"discover": DISCOVER, "download": DOWNLOAD,
                  "refresh-taxonomy": REFRESH_TAXONOMY, "process": PROCESS,
                  "validate": VALIDATE}


# ----------------------------------------------------------------------
# incremental state
# ----------------------------------------------------------------------
def _hash_file(h, path):
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)


def fingerprint(stage):
    """SHA-256 over the stage's code and the content of its inputs."""
    h = hashlib.sha256()
    for path in [stage.code_path(), *stage.inputs]:
        path = Path(path)
        h.update(str(path.relative_to(ROOT) if path.is_relative_to(ROOT)
                     else path).encode())
        if path.is_dir():
            for f in sorted(p for p in path.rglob("*") if p.is_file()):
                h.update(str(f.relative_to(path)).encode())
                _hash_file(h, f)
        elif path.is_file():
            _hash_file(h, path)
        else:
            h.update(b"<missing>")
    h.update(json.dumps(stage.args).encode())
    return h.hexdigest()


def load_state(path=None):
    path = Path(path or STATE_FILE)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def save_state(state, path=None):
    Path(path or STATE_FILE).write_text(json.dumps(state, indent=1,
                                                   sort_keys=True),
                                        encoding="utf-8")


def plan(stages, state, force=False):
    """[(stage, reason)] for stages that would run; others are skipped."""
    out, upstream_ran = [], False
    for s in stages:
        reasons = []
        if force:
            reasons.append("forced")
        if s.network:
            reasons.append("network stage (always runs when executed)")
        missing = [str(o) for o in s.outputs if not Path(o).exists()]
        if missing:
            reasons.append("output missing")
        fp = fingerprint(s)
        recorded = state.get(s.name, {}).get("fingerprint")
        if recorded is None:
            reasons.append("no recorded state")
        elif recorded != fp:
            reasons.append("inputs or code changed")
        if upstream_ran and not reasons:
            reasons.append("upstream stage runs")
        out.append((s, reasons, fp))
        upstream_ran = upstream_ran or bool(reasons)
    return out


def default_runner(stage):
    cmd = [sys.executable, "-m", stage.module, *stage.args]
    return subprocess.run(cmd, cwd=ROOT).returncode


def run_stages(stages, execute=False, force=False, state_path=None,
               runner=default_runner, echo=print, before_execute=None):
    state = load_state(state_path)
    steps = plan(stages, state, force)
    for s, reasons, _ in steps:
        verb = "RUN " if reasons else "SKIP"
        echo(f"  {verb} {s.name:<20} {s.module} {' '.join(s.args)}"
             f"{'  [' + '; '.join(reasons) + ']' if reasons else '  [up to date]'}")
    if not execute:
        echo("DRY RUN: nothing executed (add --execute to run).")
        return steps
    if before_execute is not None and any(r for _, r, _ in steps):
        before_execute()
    for s, reasons, _ in steps:
        if not reasons:
            continue
        code = runner(s)
        if code != 0:
            raise SystemExit(f"stage {s.name} failed with exit code {code}")
        state[s.name] = {"fingerprint": fingerprint(s),
                         "ran_at": datetime.now().isoformat(
                             timespec="seconds")}
        save_state(state, state_path)
    return steps


# ----------------------------------------------------------------------
# commands
# ----------------------------------------------------------------------
def filings_for(company=None, fy=None):
    m = pd.read_csv(MANIFEST, dtype=str, keep_default_na=False)
    c = pd.read_csv(MASTER, dtype=str, keep_default_na=False)
    if company:
        key = company.upper()
        ids = c[(c["company_id"].str.upper() == key) | (c["isin"] == key) |
                (c["nse_symbol"].str.upper() == key)]["company_id"]
        if ids.empty:
            raise SystemExit(f"unknown company: {company}")
        m = m[m["company_id"].isin(ids)]
    if fy:
        m = m[m["fy_slot"] == fy]
    return m[m["in_processing_scope"] == "True"]


def cmd_status(args):
    m = pd.read_csv(MANIFEST, dtype=str, keep_default_na=False)
    scope = m[m["in_processing_scope"] == "True"]
    print("Filings registered:", len(m), " in scope:", len(scope))
    print("needs_download:", int((m["needs_download"] == "True").sum()),
          " needs_processing:", int((m["needs_processing"] == "True").sum()))
    print("in-scope processing status:",
          scope["processing_status"].value_counts().to_dict())
    from app.core.deferred_issues import open_issues
    print("deferred issues open:", len(open_issues()))
    print("pipeline state:", STATE_FILE if STATE_FILE.exists() else
          "not recorded")
    for name, stages in (("process", PROCESS), ("validate", VALIDATE)):
        print(f"\n{name}:")
        run_stages(stages, execute=False, echo=print)
    if args.record_state:
        state = load_state()
        for s in PROCESS + VALIDATE:
            if all(Path(o).exists() for o in s.outputs):
                state[s.name] = {"fingerprint": fingerprint(s),
                                 "recorded_at": datetime.now().isoformat(
                                     timespec="seconds"),
                                 "note": "baseline of the approved outputs"}
        save_state(state)
        print("\nbaseline state recorded:", STATE_FILE)


def cmd_stages(args):
    stages = COMMAND_STAGES[args.command]
    if args.command == "download":
        need = filings_for()
        need = need[need["needs_download"] == "True"]
        print(f"filings flagged needs_download: {len(need)}")
        if need.empty and not args.force:
            print("nothing to download (incremental); use --force to "
                  "re-check the selection")
            return
    run_stages(stages, execute=args.execute, force=args.force)


def backup_processed(label, root=None, backup_root=None):
    """Copy data/processed (without backups/) to a new backup folder and
    verify every copy by SHA-256. Returns the folder."""
    root = Path(root or P)
    backup_root = Path(backup_root or N / "backups")
    dest = backup_root / (datetime.now().strftime("%Y%m%d_%H%M%S_%f") +
                          f"_pre_{label}")
    lines = []
    for f in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = f.relative_to(root)
        if "backups" in rel.parts:
            continue
        target = dest / "processed" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, target)
        h1, h2 = hashlib.sha256(), hashlib.sha256()
        _hash_file(h1, f)
        _hash_file(h2, target)
        if h1.hexdigest() != h2.hexdigest():
            raise SystemExit(f"backup verification failed: {rel}")
        lines.append(f"{h1.hexdigest()}  {rel.as_posix()}")
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "backup_sha256.txt").write_text("\n".join(lines) + "\n",
                                            encoding="utf-8")
    print(f"backup of processed outputs verified: {dest} "
          f"({len(lines)} files)")
    return dest


def cmd_process_like(args):
    """process / validate, with a verified backup before execution."""
    run_stages(COMMAND_STAGES[args.command], execute=args.execute,
               force=args.force,
               before_execute=lambda: backup_processed(args.command))


def reprocess_plan(check_company=None, check_fy=None):
    """All in-scope filings (the run) and the --check subset."""
    corpus = filings_for()
    if not (check_company or check_fy):
        return corpus, corpus.iloc[0:0]
    checked = filings_for(check_company, check_fy)
    if checked.empty:
        raise SystemExit("no in-scope filing matches --check-company / "
                         "--check-fy")
    return corpus, checked


def cmd_reprocess_full_corpus(args):
    corpus, checked = reprocess_plan(args.check_company, args.check_fy)
    print(f"FULL-CORPUS REPROCESS: all {len(corpus)} in-scope filings "
          f"({corpus['company_id'].nunique()} companies) are re-run through "
          "every process and validate stage.")
    print("Selective processing is not supported (corpus-wide outputs and "
          "cross-year rules).")
    if len(checked):
        print(f"--check selection: {len(checked)} filing(s), included in the "
              "full-corpus run (the selection does not limit it):")
        print(checked[["filing_id", "company_id", "fy_slot",
                       "processing_status"]].to_string(index=False))
    run_stages(PROCESS + VALIDATE, execute=args.execute, force=True,
               before_execute=lambda: backup_processed(
                   "reprocess_full_corpus"))


def cmd_reprocess_refused(args):
    raise SystemExit(
        "'reprocess' is not available: selective per-company / per-FY "
        "processing is not supported. Use 'reprocess-full-corpus' (re-runs "
        "ALL in-scope filings; --check-company / --check-fy only verify "
        "inclusion).")


def cmd_report(args):
    from app.reporting import data_dictionary, deliverables
    out_dir = Path(args.output_dir) if args.output_dir else deliverables.OUT
    if args.dictionary_path:
        doc = Path(args.dictionary_path)
    elif out_dir.resolve() == deliverables.OUT.resolve():
        doc = data_dictionary.DOC
    else:
        doc = out_dir.parent / "DATA_DICTIONARY.md"
    result = deliverables.build(out_dir, args.backup_dir)
    for name, info in result["manifest"]["outputs"].items():
        print(f"{info['rows']:>8}  {name}")
    print("outputs:", result["out_dir"])
    print("backup :", result["backup_dir"] or "none (no previous outputs)")
    print("data dictionary:", data_dictionary.write(doc, out_dir))


def load_db_gate(database, execute=False, promote=False):
    """Returns the action taken; raises when a guard refuses."""
    from app.core import deferred_issues
    from app.db import connection
    connection.check_target(database, write=True)   # refuses production
    gate = "OPEN" if deferred_issues.open_issues().empty else \
        f"BLOCKED ({len(deferred_issues.open_issues())} deferred issues)"
    print(f"target: {database} (test database)")
    print(f"production import gate: {gate}; production writes enabled: "
          f"{connection.PRODUCTION_WRITES_ENABLED}")
    if not execute:
        print("DRY RUN: nothing loaded (add --execute to stage into the "
              "test database).")
        return "DRY_RUN"
    from app.db import staging_loader
    batch, summary = staging_loader.stage(database, None)
    print(f"batch {batch} STAGED")
    if promote:
        staging_loader.promote(database, batch)
        return "PROMOTED"
    return "STAGED"


def cmd_load_db(args):
    load_db_gate(args.database, args.execute, args.promote)


def build_parser():
    p = argparse.ArgumentParser(prog="python -m app.pipeline",
                                description="BRSR data pipeline")
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("status", help="registry, incremental plan, gates")
    s.add_argument("--record-state", action="store_true",
                   help="record the current outputs as the baseline state")
    s.set_defaults(func=cmd_status)
    for name, help_ in (("discover", "NSE filing discovery and registry "
                         "(network)"),
                        ("download", "download XBRL flagged needs_download "
                         "(network)"),
                        ("refresh-taxonomy", "download and rebuild the "
                         "taxonomy (network)"),
                        ("process", "parse, extract, normalise, assurance, "
                         "supplementary (whole corpus)"),
                        ("validate", "rules, blocks, review workflow "
                         "(whole corpus)")):
        s = sub.add_parser(name, help=help_)
        s.add_argument("--execute", action="store_true")
        s.add_argument("--force", action="store_true")
        s.set_defaults(func=cmd_process_like
                       if name in ("process", "validate") else cmd_stages)
    s = sub.add_parser(
        "reprocess-full-corpus",
        help="re-run EVERY process and validate stage for ALL in-scope "
             "filings (no selective processing)",
        description="Re-runs every process and validate stage for all "
                    "in-scope filings. --check-company / --check-fy do not "
                    "limit the run; they only verify that a filing is part "
                    "of it.")
    s.add_argument("--check-company", help="verify this company is in the "
                   "run (does not limit processing)")
    s.add_argument("--check-fy", help="verify this FY slot is in the run "
                   "(does not limit processing)")
    s.add_argument("--execute", action="store_true")
    s.set_defaults(func=cmd_reprocess_full_corpus)
    s = sub.add_parser("reprocess", help="REFUSED: selective reprocessing "
                       "is not supported; use reprocess-full-corpus")
    s.add_argument("rest", nargs=argparse.REMAINDER)
    s.set_defaults(func=cmd_reprocess_refused)
    s = sub.add_parser("report", help="build data/outputs and the data "
                       "dictionary (staged, verified, backed up)")
    s.add_argument("--output-dir", help="default: data/outputs")
    s.add_argument("--backup-dir", help="default: data/processed/nifty100/"
                   "backups")
    s.add_argument("--dictionary-path", help="default: docs/DATA_DICTIONARY"
                   ".md for the default output dir, else next to the "
                   "output dir")
    s.set_defaults(func=cmd_report)
    s = sub.add_parser("load-db", help="stage outputs into a TEST database")
    s.add_argument("--database", required=True)
    s.add_argument("--execute", action="store_true")
    s.add_argument("--promote", action="store_true")
    s.set_defaults(func=cmd_load_db)
    return p


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv[:1] == ["reprocess"]:
        cmd_reprocess_refused(None)          # whatever options were given
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
